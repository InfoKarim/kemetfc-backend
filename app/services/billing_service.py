from datetime import date, datetime, timedelta, UTC
from uuid import uuid4

import stripe
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import (
    get_billing_return_base_url,
    get_stripe_price_id,
    get_stripe_secret_key,
    get_stripe_webhook_secret,
)
from app.db_models import (
    AssessmentRegistrationDB,
    AuditEventDB,
    GuardianPlayerLinkDB,
    ManualPaymentDB,
    MembershipPlanDB,
    PaymentDB,
    PlayerDB,
    PlayerMembershipDB,
    SubscriptionDB,
    UserDB,
)
from app.services.id_service import next_entity_id


class BillingError(ValueError):
    pass


def is_configured() -> bool:
    return bool(get_stripe_secret_key()) and bool(get_stripe_price_id())


def _stripe_timestamp_to_datetime(value: int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=UTC).replace(tzinfo=None)


def _extract_discount_percent_off(subscription: dict) -> int | None:
    discount = subscription.get("discount")
    if not discount:
        return None
    coupon = discount.get("coupon") or {}
    percent_off = coupon.get("percent_off")
    return int(percent_off) if percent_off is not None else None


def _stripe_object_to_dict(value) -> dict:
    """Real stripe-python SDK objects deliberately aren't dict-like (no
    .get()) — convert to a plain dict so the rest of this module can use
    uniform dict access regardless of whether the caller passed a real
    Stripe object or an already-plain dict (as tests do)."""
    to_dict = getattr(value, "to_dict", None)
    return to_dict() if callable(to_dict) else value


def _discount_coupon_id(percent_off: int) -> str:
    # A fixed, deterministic id per percentage so repeated "give a
    # discount" actions reuse the same Stripe coupon instead of creating
    # a new one every time — Stripe coupon ids are globally unique per
    # account, and this stays stable and human-readable in the dashboard.
    return f"kemetfc-{percent_off}pct-forever"


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

        # A staff-assigned plan (Payment Control -> Assign Membership) takes
        # priority over the single legacy global price, so a guardian's
        # checkout always reflects the plan an admin actually chose for
        # their child.
        membership = self.get_membership_for_player(player_id)
        price_id = get_stripe_price_id()
        if membership is not None:
            plan = self.db.get(MembershipPlanDB, membership.plan_id)
            if plan is not None and plan.active:
                price_id = plan.stripe_price_id

        base_url = get_billing_return_base_url()
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
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
        discount_percent_off = _extract_discount_percent_off(subscription)
        stripe_price_id = subscription["items"]["data"][0]["price"]["id"]
        plan = (
            self.db.query(MembershipPlanDB)
            .filter(MembershipPlanDB.stripe_price_id == stripe_price_id)
            .first()
        )
        plan_id = plan.plan_id if plan is not None else None

        if existing is None:
            existing = SubscriptionDB(
                stripe_subscription_id=stripe_subscription_id,
                player_id=player_id,
                paying_user_id=paying_user_id,
                stripe_customer_id=subscription["customer"],
                stripe_price_id=stripe_price_id,
                status=subscription["status"],
                current_period_end=current_period_end,
                cancel_at_period_end=bool(subscription.get("cancel_at_period_end")),
                discount_percent_off=discount_percent_off,
                plan_id=plan_id,
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
            existing.discount_percent_off = discount_percent_off
            existing.plan_id = plan_id
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

    def upsert_payment_from_stripe_invoice(
        self,
        invoice: dict,
        status: str,
    ) -> PaymentDB | None:
        """Record a completed or failed invoice as a Guardian-visible
        payment. Resolves player_id via our OWN subscription row (never
        trusting invoice metadata alone) — if that subscription isn't
        known yet, the invoice is skipped rather than guessed at; the
        subscription.created/updated webhook that normally precedes or
        accompanies it will let a retried/later invoice event resolve.
        """
        stripe_subscription_id = invoice.get("subscription")
        if not stripe_subscription_id:
            return None

        subscription = self.db.get(SubscriptionDB, stripe_subscription_id)
        if subscription is None:
            return None

        now = datetime.now(UTC).replace(tzinfo=None)
        stripe_invoice_id = invoice["id"]
        existing = self.db.get(PaymentDB, stripe_invoice_id)

        amount = invoice.get("amount_paid") if status == "paid" else invoice.get("amount_due")
        period_end = _stripe_timestamp_to_datetime(invoice.get("period_end"))

        if existing is None:
            existing = PaymentDB(
                stripe_invoice_id=stripe_invoice_id,
                stripe_subscription_id=stripe_subscription_id,
                player_id=subscription.player_id,
                amount=amount or 0,
                currency=invoice.get("currency", "usd"),
                status=status,
                hosted_invoice_url=invoice.get("hosted_invoice_url"),
                invoice_pdf_url=invoice.get("invoice_pdf"),
                period_end=period_end,
                created_at=now,
            )
            self.db.add(existing)
        else:
            existing.status = status
            existing.amount = amount or existing.amount
            existing.hosted_invoice_url = invoice.get("hosted_invoice_url")
            existing.invoice_pdf_url = invoice.get("invoice_pdf")
            existing.period_end = period_end

        self.db.commit()
        self.db.refresh(existing)
        return existing

    def list_payments_for_player(self, player_id: str) -> list[PaymentDB]:
        return (
            self.db.query(PaymentDB)
            .filter(PaymentDB.player_id == player_id)
            .order_by(PaymentDB.created_at.desc())
            .all()
        )

    def apply_discount(self, player_id: str, percent_off: int) -> SubscriptionDB:
        """Apply an ongoing (duration=forever) percentage discount to a
        player's subscription — admin-only, reversible via
        remove_discount(). Reuses one Stripe coupon per percentage rather
        than minting a new one on every click.
        """
        subscription = self.get_subscription_for_player(player_id)
        if subscription is None:
            raise BillingError("No subscription found for this player")

        coupon_id = _discount_coupon_id(percent_off)
        try:
            stripe.Coupon.retrieve(coupon_id)
        except stripe.InvalidRequestError:
            stripe.Coupon.create(
                id=coupon_id,
                percent_off=percent_off,
                duration="forever",
            )

        updated = stripe.Subscription.modify(
            subscription.stripe_subscription_id,
            coupon=coupon_id,
        )
        return self.upsert_subscription_from_stripe_object(_stripe_object_to_dict(updated))

    def remove_discount(self, player_id: str) -> SubscriptionDB:
        subscription = self.get_subscription_for_player(player_id)
        if subscription is None:
            raise BillingError("No subscription found for this player")

        stripe.Subscription.delete_discount(subscription.stripe_subscription_id)
        updated = stripe.Subscription.retrieve(subscription.stripe_subscription_id)
        return self.upsert_subscription_from_stripe_object(_stripe_object_to_dict(updated))

    def pause_subscription(self, player_id: str, actor_user_id: str) -> SubscriptionDB:
        subscription = self.get_subscription_for_player(player_id)
        if subscription is None:
            raise BillingError("No subscription found for this player")

        updated = stripe.Subscription.modify(
            subscription.stripe_subscription_id,
            pause_collection={"behavior": "void"},
        )
        result = self.upsert_subscription_from_stripe_object(
            _stripe_object_to_dict(updated)
        )
        self._audit(
            actor_user_id=actor_user_id,
            action="membership_paused",
            resource_type="player",
            resource_id=player_id,
            details={"stripe_subscription_id": subscription.stripe_subscription_id},
        )
        self.db.commit()
        return result

    def resume_subscription(self, player_id: str, actor_user_id: str) -> SubscriptionDB:
        subscription = self.get_subscription_for_player(player_id)
        if subscription is None:
            raise BillingError("No subscription found for this player")

        updated = stripe.Subscription.modify(
            subscription.stripe_subscription_id,
            pause_collection="",
        )
        result = self.upsert_subscription_from_stripe_object(
            _stripe_object_to_dict(updated)
        )
        self._audit(
            actor_user_id=actor_user_id,
            action="membership_resumed",
            resource_type="player",
            resource_id=player_id,
            details={"stripe_subscription_id": subscription.stripe_subscription_id},
        )
        self.db.commit()
        return result

    def refund_payment(
        self,
        stripe_invoice_id: str,
        actor_user_id: str,
        amount_cents: int | None = None,
        reason: str | None = None,
    ) -> dict:
        payment = self.db.get(PaymentDB, stripe_invoice_id)
        if payment is None:
            raise BillingError("Payment not found")
        if payment.status != "paid":
            raise BillingError("Only a paid invoice can be refunded")

        invoice = _stripe_object_to_dict(stripe.Invoice.retrieve(stripe_invoice_id))
        payment_intent_id = invoice.get("payment_intent")
        if not payment_intent_id:
            raise BillingError("This invoice has no payment to refund")

        refund_kwargs = {"payment_intent": payment_intent_id}
        if amount_cents is not None:
            refund_kwargs["amount"] = amount_cents

        refund = stripe.Refund.create(**refund_kwargs)

        self._audit(
            actor_user_id=actor_user_id,
            action="payment_refunded",
            resource_type="payment",
            resource_id=stripe_invoice_id,
            details={
                "player_id": payment.player_id,
                "amount_cents": amount_cents or payment.amount,
                "reason": reason,
                "stripe_refund_id": refund["id"],
            },
        )
        self.db.commit()
        return _stripe_object_to_dict(refund)

    def record_manual_payment(
        self,
        player_id: str,
        actor_user_id: str,
        amount_cents: int,
        currency: str,
        method: str,
        payment_date: date,
        note: str | None,
    ) -> ManualPaymentDB:
        payment = ManualPaymentDB(
            manual_payment_id=next_entity_id(self.db, "manual_payment"),
            player_id=player_id,
            amount_cents=amount_cents,
            currency=currency.lower(),
            method=method,
            payment_date=payment_date,
            note=note,
            recorded_by_user_id=actor_user_id,
            recorded_at=datetime.now(UTC).replace(tzinfo=None),
        )
        self.db.add(payment)
        self._audit(
            actor_user_id=actor_user_id,
            action="manual_payment_recorded",
            resource_type="player",
            resource_id=player_id,
            details={
                "amount_cents": amount_cents,
                "currency": currency,
                "method": method,
                "payment_date": payment_date.isoformat(),
            },
        )
        self.db.commit()
        self.db.refresh(payment)
        return payment

    def list_manual_payments_for_player(self, player_id: str) -> list[ManualPaymentDB]:
        return (
            self.db.query(ManualPaymentDB)
            .filter(ManualPaymentDB.player_id == player_id)
            .order_by(ManualPaymentDB.payment_date.desc())
            .all()
        )

    def create_membership_plan(
        self,
        actor_user_id: str,
        name: str,
        stripe_price_id: str,
        amount_cents: int,
        currency: str,
        billing_interval: str,
    ) -> MembershipPlanDB:
        existing = (
            self.db.query(MembershipPlanDB)
            .filter(MembershipPlanDB.stripe_price_id == stripe_price_id)
            .first()
        )
        if existing is not None:
            raise BillingError("A plan for this Stripe price already exists")

        now = datetime.now(UTC).replace(tzinfo=None)
        plan = MembershipPlanDB(
            plan_id=next_entity_id(self.db, "membership_plan"),
            name=name,
            stripe_price_id=stripe_price_id,
            amount_cents=amount_cents,
            currency=currency.lower(),
            billing_interval=billing_interval,
            active=True,
            created_at=now,
            updated_at=now,
        )
        self.db.add(plan)
        self._audit(
            actor_user_id=actor_user_id,
            action="membership_plan_created",
            resource_type="membership_plan",
            resource_id=plan.plan_id,
            details={"name": name, "amount_cents": amount_cents},
        )
        self.db.commit()
        self.db.refresh(plan)
        return plan

    def list_membership_plans(self) -> list[MembershipPlanDB]:
        return (
            self.db.query(MembershipPlanDB)
            .order_by(MembershipPlanDB.created_at.asc())
            .all()
        )

    def update_membership_plan(
        self,
        plan_id: str,
        actor_user_id: str,
        name: str | None = None,
        active: bool | None = None,
    ) -> MembershipPlanDB:
        plan = self.db.get(MembershipPlanDB, plan_id)
        if plan is None:
            raise BillingError("Plan not found")

        previous = {"name": plan.name, "active": plan.active}
        if name is not None:
            plan.name = name
        if active is not None:
            plan.active = active
        plan.updated_at = datetime.now(UTC).replace(tzinfo=None)

        self._audit(
            actor_user_id=actor_user_id,
            action="membership_plan_updated",
            resource_type="membership_plan",
            resource_id=plan_id,
            details={"previous": previous, "new": {"name": plan.name, "active": plan.active}},
        )
        self.db.commit()
        self.db.refresh(plan)
        return plan

    def assign_membership_plan(
        self,
        player_id: str,
        plan_id: str,
        actor_user_id: str,
    ) -> PlayerMembershipDB:
        plan = self.db.get(MembershipPlanDB, plan_id)
        if plan is None or not plan.active:
            raise BillingError("Plan not found or inactive")

        now = datetime.now(UTC).replace(tzinfo=None)
        existing = self.db.get(PlayerMembershipDB, player_id)
        previous_plan_id = existing.plan_id if existing is not None else None

        if existing is None:
            existing = PlayerMembershipDB(
                player_id=player_id,
                plan_id=plan_id,
                assigned_by_user_id=actor_user_id,
                assigned_at=now,
                updated_at=now,
            )
            self.db.add(existing)
        else:
            existing.plan_id = plan_id
            existing.assigned_by_user_id = actor_user_id
            existing.updated_at = now

        self._audit(
            actor_user_id=actor_user_id,
            action="membership_plan_assigned",
            resource_type="player",
            resource_id=player_id,
            details={"previous_plan_id": previous_plan_id, "new_plan_id": plan_id},
        )
        self.db.commit()
        self.db.refresh(existing)
        return existing

    def get_membership_for_player(self, player_id: str) -> PlayerMembershipDB | None:
        return self.db.get(PlayerMembershipDB, player_id)

    def get_admin_billing_summary(self) -> dict:
        now = datetime.now(UTC).replace(tzinfo=None)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        due_soon_cutoff = now + timedelta(days=7)

        active_memberships = (
            self.db.query(SubscriptionDB)
            .filter(SubscriptionDB.status == "active")
            .count()
        )
        payment_due = (
            self.db.query(SubscriptionDB)
            .filter(
                SubscriptionDB.status == "active",
                SubscriptionDB.cancel_at_period_end.is_(False),
                SubscriptionDB.current_period_end.isnot(None),
                SubscriptionDB.current_period_end <= due_soon_cutoff,
            )
            .count()
        )
        past_due = (
            self.db.query(SubscriptionDB)
            .filter(SubscriptionDB.status.in_(["past_due", "unpaid"]))
            .count()
        )
        processing = (
            self.db.query(SubscriptionDB)
            .filter(SubscriptionDB.status.in_(["incomplete", "trialing"]))
            .count()
        )
        failed_payments = (
            self.db.query(PaymentDB).filter(PaymentDB.status == "failed").count()
        )
        paid_this_month_cents = (
            self.db.query(func.coalesce(func.sum(PaymentDB.amount), 0))
            .filter(PaymentDB.status == "paid", PaymentDB.created_at >= month_start)
            .scalar() or 0
        )
        paid_this_month_cents += (
            self.db.query(func.coalesce(func.sum(ManualPaymentDB.amount_cents), 0))
            .filter(ManualPaymentDB.recorded_at >= month_start)
            .scalar() or 0
        )

        total_players = self.db.query(PlayerDB).count()
        players_with_membership = self.db.query(PlayerMembershipDB).count()

        registrations_awaiting_billing_setup = (
            self.db.query(AssessmentRegistrationDB)
            .filter(
                AssessmentRegistrationDB.status == "player_created",
                AssessmentRegistrationDB.player_id.isnot(None),
                ~AssessmentRegistrationDB.player_id.in_(
                    self.db.query(PlayerMembershipDB.player_id)
                ),
            )
            .count()
        )

        return {
            "active_memberships": active_memberships,
            "payment_due": payment_due,
            "past_due": past_due,
            "processing": processing,
            "failed_payments": failed_payments,
            "paid_this_month_cents": paid_this_month_cents,
            "players_without_membership": total_players - players_with_membership,
            "registrations_awaiting_billing_setup": registrations_awaiting_billing_setup,
        }

    def list_admin_billing_rows(self) -> list[dict]:
        players = self.db.query(PlayerDB).order_by(PlayerDB.first_name_en.asc()).all()
        rows = []

        for player in players:
            subscription = self.get_subscription_for_player(player.player_id)
            membership = self.get_membership_for_player(player.player_id)
            plan = (
                self.db.get(MembershipPlanDB, membership.plan_id)
                if membership is not None
                else None
            )
            guardian_link = (
                self.db.query(GuardianPlayerLinkDB)
                .filter(GuardianPlayerLinkDB.player_id == player.player_id)
                .first()
            )
            guardian = (
                self.db.get(UserDB, guardian_link.guardian_user_id)
                if guardian_link is not None
                else None
            )

            rows.append({
                "player_id": player.player_id,
                "player_name": f"{player.first_name_en} {player.last_name_en}",
                "guardian_name": (
                    f"{guardian.first_name or ''} {guardian.last_name or ''}".strip()
                    or guardian.username
                    if guardian is not None
                    else None
                ),
                "guardian_user_id": guardian.user_id if guardian is not None else None,
                "plan_name": plan.name if plan is not None else None,
                "plan_id": plan.plan_id if plan is not None else None,
                "amount_cents": plan.amount_cents if plan is not None else None,
                "currency": plan.currency if plan is not None else None,
                "status": subscription.status if subscription is not None else "none",
                "cancel_at_period_end": (
                    subscription.cancel_at_period_end
                    if subscription is not None
                    else False
                ),
                "current_period_end": (
                    subscription.current_period_end.isoformat()
                    if subscription is not None and subscription.current_period_end
                    else None
                ),
                "discount_percent_off": (
                    subscription.discount_percent_off
                    if subscription is not None
                    else None
                ),
                "has_membership_assigned": membership is not None,
            })

        return rows

    def _audit(
        self,
        actor_user_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        details: dict,
    ) -> None:
        self.db.add(
            AuditEventDB(
                event_id=str(uuid4()),
                occurred_at=datetime.now(UTC).replace(tzinfo=None),
                actor_user_id=actor_user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                details=details,
            )
        )
