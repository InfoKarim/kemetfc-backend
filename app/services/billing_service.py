from datetime import datetime, UTC

import stripe
from sqlalchemy.orm import Session

from app.config import (
    get_billing_return_base_url,
    get_stripe_price_id,
    get_stripe_secret_key,
    get_stripe_webhook_secret,
)
from app.db_models import SubscriptionDB


class BillingError(ValueError):
    pass


def is_configured() -> bool:
    return bool(get_stripe_secret_key()) and bool(get_stripe_price_id())


def _stripe_timestamp_to_datetime(value: int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=UTC).replace(tzinfo=None)


class BillingService:
    def __init__(self, db: Session):
        self.db = db
        stripe.api_key = get_stripe_secret_key()

    def create_checkout_session(
        self,
        player_id: str,
        paying_user_id: str,
        guardian_email: str,
    ) -> str:
        if not is_configured():
            raise BillingError("Billing is not configured")

        base_url = get_billing_return_base_url()
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": get_stripe_price_id(), "quantity": 1}],
            customer_email=guardian_email,
            client_reference_id=player_id,
            metadata={"player_id": player_id, "paying_user_id": paying_user_id},
            subscription_data={
                "metadata": {"player_id": player_id, "paying_user_id": paying_user_id},
            },
            success_url=f"{base_url}/billing?checkout=success",
            cancel_url=f"{base_url}/billing?checkout=cancelled",
        )
        return session.url

    def construct_webhook_event(self, payload: bytes, signature_header: str):
        secret = get_stripe_webhook_secret()
        if not secret:
            raise BillingError("Stripe webhook secret is not configured")

        try:
            return stripe.Webhook.construct_event(payload, signature_header, secret)
        except (ValueError, stripe.SignatureVerificationError) as error:
            raise BillingError(f"Invalid webhook signature: {error}") from error

    def upsert_subscription_from_stripe_object(self, subscription: dict) -> SubscriptionDB:
        metadata = subscription.get("metadata") or {}
        player_id = metadata.get("player_id")
        paying_user_id = metadata.get("paying_user_id")

        if not player_id or not paying_user_id:
            raise BillingError(
                "Stripe subscription is missing player_id/paying_user_id metadata"
            )

        stripe_subscription_id = subscription["id"]
        now = datetime.now(UTC).replace(tzinfo=None)

        existing = self.db.get(SubscriptionDB, stripe_subscription_id)
        current_period_end = _stripe_timestamp_to_datetime(
            subscription.get("current_period_end")
        )

        if existing is None:
            existing = SubscriptionDB(
                stripe_subscription_id=stripe_subscription_id,
                player_id=player_id,
                paying_user_id=paying_user_id,
                stripe_customer_id=subscription["customer"],
                stripe_price_id=subscription["items"]["data"][0]["price"]["id"],
                status=subscription["status"],
                current_period_end=current_period_end,
                cancel_at_period_end=bool(subscription.get("cancel_at_period_end")),
                created_at=now,
                updated_at=now,
            )
            self.db.add(existing)
        else:
            existing.status = subscription["status"]
            existing.current_period_end = current_period_end
            existing.cancel_at_period_end = bool(
                subscription.get("cancel_at_period_end")
            )
            existing.updated_at = now

        self.db.commit()
        self.db.refresh(existing)
        return existing

    def get_subscription_for_player(self, player_id: str) -> SubscriptionDB | None:
        return (
            self.db.query(SubscriptionDB)
            .filter(SubscriptionDB.player_id == player_id)
            .order_by(SubscriptionDB.created_at.desc())
            .first()
        )

    def cancel_subscription(self, stripe_subscription_id: str) -> SubscriptionDB:
        existing = self.db.get(SubscriptionDB, stripe_subscription_id)
        if existing is None:
            raise BillingError("Subscription not found")

        stripe.Subscription.modify(stripe_subscription_id, cancel_at_period_end=True)
        existing.cancel_at_period_end = True
        existing.updated_at = datetime.now(UTC).replace(tzinfo=None)
        self.db.commit()
        self.db.refresh(existing)
        return existing
