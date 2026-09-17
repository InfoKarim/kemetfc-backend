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
    BillingSettingsDB,
    FamilyDiscountRuleDB,
    GuardianPlayerLinkDB,
    ManualPaymentDB,
    MembershipPlanDB,
    PaymentDB,
    PlayerDB,
    PlayerMembershipDB,
    PromoCodeDB,
    StripeCustomerMappingDB,
    StripeEventDB,
    SubscriptionDB,
    TeamDB,
    UserDB,
)
from app.services.eligibility_service import derive_eligibility, derive_membership_status
from app.services.id_service import next_entity_id
from app.services.promo_code_service import PromoCodeError, PromoCodeService


class BillingError(ValueError):
    pass


def is_configured() -> bool:
    # STRIPE_PRICE_ID is a legacy single-price fallback, not a hard
    # prerequisite — an academy using only per-plan pricing (MembershipPlanDB)
    # never needs to set it. The secret key is the only real requirement.
    return bool(get_stripe_secret_key())


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
        promo_code: str | None = None,
    ) -> str:
        if not is_configured():
            raise BillingError("Billing is not configured")

        # A staff-assigned plan (Payment Control -> Assign Membership) takes
        # priority over the single legacy global price, so a guardian's
        # checkout always reflects the plan an admin actually chose for
        # their child.
        membership = self.get_membership_for_player(player_id)
        if membership is not None and membership.is_complimentary:
            raise BillingError(
                "This player has a complimentary membership — no payment is needed"
            )

        plan = None
        price_id = get_stripe_price_id()
        if membership is not None and membership.plan_id is not None:
            plan = self.db.get(MembershipPlanDB, membership.plan_id)
            if plan is not None and plan.active:
                price_id = plan.stripe_price_id

        if not price_id:
            raise BillingError(
                "No membership plan is assigned to this player and no "
                "default price is configured"
            )

        # Discount precedence — deliberately only one applies, never
        # stacked, so the price a guardian sees is always unambiguous and
        # fully server-computed: a promo code entered right now outranks a
        # standing admin discount, which outranks an automatic sibling
        # discount.
        checkout_kwargs = {}
        redeemed_promo = None
        if promo_code:
            try:
                redeemed_promo = PromoCodeService(self.db).validate_promo_code(
                    promo_code, paying_user_id, plan.plan_id if plan is not None else None
                )
            except PromoCodeError as error:
                raise BillingError(str(error))
            coupon_id = self._ensure_promo_coupon(redeemed_promo)
            checkout_kwargs["discounts"] = [{"coupon": coupon_id}]
        elif membership is not None and membership.discount_percent_off is not None:
            # A discount an admin approved before this guardian ever paid
            # (see apply_discount) — attach it here so the very first
            # invoice already reflects it, rather than requiring a second
            # manual step after checkout completes.
            coupon_id = self._ensure_discount_coupon(membership.discount_percent_off)
            checkout_kwargs["discounts"] = [{"coupon": coupon_id}]
        else:
            sibling_discount = self.get_family_discount_percent(paying_user_id, player_id)
            if sibling_discount is not None:
                coupon_id = self._ensure_discount_coupon(sibling_discount)
                checkout_kwargs["discounts"] = [{"coupon": coupon_id}]

        line_items = [{"price": price_id, "quantity": 1}]
        if plan is not None and plan.enrollment_fee_cents:
            # A one-time fee charged alongside the first invoice only —
            # price_data (not a catalog Price) because it's a one-off, not
            # a reusable recurring price.
            line_items.append({
                "price_data": {
                    "currency": plan.currency,
                    "product_data": {"name": f"{plan.name} — Enrollment Fee"},
                    "unit_amount": plan.enrollment_fee_cents,
                },
                "quantity": 1,
            })

        subscription_data = {
            "metadata": {"player_id": player_id, "paying_user_id": paying_user_id},
        }
        if plan is not None and plan.trial_period_days:
            subscription_data["trial_period_days"] = plan.trial_period_days

        stripe_customer_id = self.get_or_create_stripe_customer(paying_user_id, guardian_email)

        base_url = get_billing_return_base_url()
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=line_items,
            customer=stripe_customer_id,
            client_reference_id=player_id,
            metadata={"player_id": player_id, "paying_user_id": paying_user_id},
            subscription_data=subscription_data,
            success_url=f"{base_url}/billing?checkout=success",
            cancel_url=f"{base_url}/billing?checkout=cancelled",
            **checkout_kwargs,
        )

        if redeemed_promo is not None:
            # Reserved at checkout creation, not at payment success — an
            # abandoned checkout costs one redemption slot, a deliberate
            # simplicity trade-off an admin can always correct by editing
            # the code's max_uses.
            PromoCodeService(self.db).redeem_promo_code(
                redeemed_promo.promo_code_id, paying_user_id, player_id
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
        is_paused = bool(subscription.get("pause_collection"))
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
                is_paused=is_paused,
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
            existing.is_paused = is_paused
            existing.updated_at = now

        self.db.commit()
        self.db.refresh(existing)
        return existing

    def has_processed_stripe_event(self, stripe_event_id: str) -> bool:
        """Checked BEFORE processing. Deliberately read-only — the event is
        only marked processed (mark_stripe_event_processed) AFTER handling
        it succeeds, so a failure mid-processing leaves it unmarked and a
        genuine Stripe retry still reprocesses it, rather than being
        silently swallowed."""
        return self.db.get(StripeEventDB, stripe_event_id) is not None

    def mark_stripe_event_processed(self, stripe_event_id: str, event_type: str) -> None:
        if self.db.get(StripeEventDB, stripe_event_id) is not None:
            return
        self.db.add(StripeEventDB(
            stripe_event_id=stripe_event_id,
            event_type=event_type,
            received_at=datetime.now(UTC).replace(tzinfo=None),
        ))
        self.db.commit()

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

    def _ensure_discount_coupon(self, percent_off: int) -> str:
        coupon_id = _discount_coupon_id(percent_off)
        try:
            try:
                stripe.Coupon.retrieve(coupon_id)
            except stripe.InvalidRequestError:
                stripe.Coupon.create(
                    id=coupon_id,
                    percent_off=percent_off,
                    duration="forever",
                )
        except stripe.StripeError as error:
            raise BillingError(f"Stripe rejected this discount: {error.user_message or error}")
        return coupon_id

    def _ensure_promo_coupon(self, promo: PromoCodeDB) -> str:
        # duration="once" (applies to the first invoice only) — distinct
        # from admin/sibling discounts, which are ongoing.
        coupon_id = f"promo-{promo.promo_code_id}"
        try:
            try:
                stripe.Coupon.retrieve(coupon_id)
            except stripe.InvalidRequestError:
                if promo.discount_type == "percentage":
                    stripe.Coupon.create(
                        id=coupon_id, percent_off=promo.discount_value, duration="once"
                    )
                else:
                    stripe.Coupon.create(
                        id=coupon_id,
                        amount_off=promo.discount_value,
                        currency="usd",
                        duration="once",
                    )
        except stripe.StripeError as error:
            raise BillingError(f"Stripe rejected this promo code: {error.user_message or error}")
        return coupon_id

    def get_or_create_stripe_customer(self, guardian_user_id: str, guardian_email: str) -> str:
        """One persistent Stripe Customer per guardian, reused across every
        one of their children's checkouts — required for saved payment
        methods and the Billing Portal to manage a family, not just one
        subscription."""
        mapping = self.db.get(StripeCustomerMappingDB, guardian_user_id)
        if mapping is not None:
            return mapping.stripe_customer_id

        try:
            customer = stripe.Customer.create(
                email=guardian_email,
                metadata={"guardian_user_id": guardian_user_id},
            )
        except stripe.StripeError as error:
            raise BillingError(f"Stripe rejected this customer: {error.user_message or error}")

        self.db.add(StripeCustomerMappingDB(
            guardian_user_id=guardian_user_id,
            stripe_customer_id=customer["id"],
            created_at=datetime.now(UTC).replace(tzinfo=None),
        ))
        self.db.commit()
        return customer["id"]

    def create_billing_portal_session(self, guardian_user_id: str, guardian_email: str) -> str:
        """A Stripe-hosted page where a guardian manages saved payment
        methods and views invoices across every one of their children —
        no custom card UI, so no card data ever reaches this app."""
        if not is_configured():
            raise BillingError("Billing is not configured")

        stripe_customer_id = self.get_or_create_stripe_customer(
            guardian_user_id, guardian_email
        )
        base_url = get_billing_return_base_url()
        try:
            session = stripe.billing_portal.Session.create(
                customer=stripe_customer_id,
                return_url=f"{base_url}/billing",
            )
        except stripe.StripeError as error:
            raise BillingError(
                f"Stripe rejected the billing portal request: {error.user_message or error}"
            )
        return session.url

    def calculate_sibling_position(self, guardian_user_id: str, player_id: str) -> int:
        """1-indexed position of this player among the guardian's children
        who have ever been assigned a membership, ordered by when they
        were assigned — the 1st-assigned child is position 1 (full
        price), matching a plain "first child full price, later children
        discounted" sibling policy."""
        linked_player_ids = [
            link.player_id
            for link in self.db.query(GuardianPlayerLinkDB)
            .filter(GuardianPlayerLinkDB.guardian_user_id == guardian_user_id)
            .all()
        ]
        if player_id not in linked_player_ids:
            linked_player_ids = [*linked_player_ids, player_id]

        memberships = (
            self.db.query(PlayerMembershipDB)
            .filter(PlayerMembershipDB.player_id.in_(linked_player_ids))
            .order_by(PlayerMembershipDB.assigned_at.asc())
            .all()
        )
        ordered_ids = [membership.player_id for membership in memberships]
        if player_id not in ordered_ids:
            return len(ordered_ids) + 1
        return ordered_ids.index(player_id) + 1

    def get_family_discount_percent(self, guardian_user_id: str, player_id: str) -> int | None:
        position = self.calculate_sibling_position(guardian_user_id, player_id)
        rule = (
            self.db.query(FamilyDiscountRuleDB)
            .filter(
                FamilyDiscountRuleDB.sibling_position == position,
                FamilyDiscountRuleDB.active.is_(True),
            )
            .first()
        )
        return rule.discount_percent if rule is not None else None

    def grant_complimentary_membership(
        self,
        player_id: str,
        actor_user_id: str,
        plan_id: str | None = None,
    ) -> PlayerMembershipDB:
        """A player who trains free — an explicit admin decision, never
        set by anything payment-related. No Stripe checkout is ever
        created while this is true."""
        now = datetime.now(UTC).replace(tzinfo=None)
        membership = self.get_membership_for_player(player_id)

        if membership is None:
            membership = PlayerMembershipDB(
                player_id=player_id,
                plan_id=plan_id,
                assigned_by_user_id=actor_user_id,
                assigned_at=now,
                is_complimentary=True,
                updated_at=now,
            )
            self.db.add(membership)
        else:
            membership.is_complimentary = True
            if plan_id is not None:
                membership.plan_id = plan_id
            membership.updated_at = now

        self._audit(
            actor_user_id=actor_user_id,
            action="complimentary_membership_granted",
            resource_type="player",
            resource_id=player_id,
            details={"plan_id": plan_id},
        )
        self.db.commit()
        self.db.refresh(membership)
        return membership

    def revoke_complimentary_membership(
        self, player_id: str, actor_user_id: str
    ) -> PlayerMembershipDB:
        membership = self.get_membership_for_player(player_id)
        if membership is None or not membership.is_complimentary:
            raise BillingError("This player does not have a complimentary membership")

        membership.is_complimentary = False
        membership.updated_at = datetime.now(UTC).replace(tzinfo=None)
        self._audit(
            actor_user_id=actor_user_id,
            action="complimentary_membership_revoked",
            resource_type="player",
            resource_id=player_id,
            details={},
        )
        self.db.commit()
        self.db.refresh(membership)
        return membership

    def set_admin_override_eligibility(
        self,
        player_id: str,
        actor_user_id: str,
        reason: str | None,
    ) -> PlayerMembershipDB:
        """`reason` set = override active (player is TRAINING_ELIGIBLE
        regardless of computed status); `reason` None = clear it. Always
        a documented exception — never silent."""
        membership = self.get_membership_for_player(player_id)
        if membership is None:
            raise BillingError(
                "Assign a membership to this player before setting an eligibility override"
            )

        previous = membership.admin_override_eligibility
        membership.admin_override_eligibility = reason
        membership.updated_at = datetime.now(UTC).replace(tzinfo=None)
        self._audit(
            actor_user_id=actor_user_id,
            action="eligibility_override_changed",
            resource_type="player",
            resource_id=player_id,
            details={"previous_reason": previous, "new_reason": reason},
        )
        self.db.commit()
        self.db.refresh(membership)
        return membership

    def get_billing_settings(self) -> BillingSettingsDB:
        settings = self.db.get(BillingSettingsDB, "default")
        if settings is None:
            # Sensible defaults on first read — no migration-time data
            # seeding required, and every academy gets a working config
            # immediately.
            settings = BillingSettingsDB(
                settings_id="default",
                grace_period_days=7,
                payment_due_reminder_days_before=3,
                updated_at=datetime.now(UTC).replace(tzinfo=None),
                updated_by_user_id=None,
            )
            self.db.add(settings)
            self.db.commit()
            self.db.refresh(settings)
        return settings

    def update_billing_settings(
        self,
        actor_user_id: str,
        grace_period_days: int | None = None,
        payment_due_reminder_days_before: int | None = None,
    ) -> BillingSettingsDB:
        settings = self.get_billing_settings()
        previous = {
            "grace_period_days": settings.grace_period_days,
            "payment_due_reminder_days_before": settings.payment_due_reminder_days_before,
        }
        if grace_period_days is not None:
            settings.grace_period_days = grace_period_days
        if payment_due_reminder_days_before is not None:
            settings.payment_due_reminder_days_before = payment_due_reminder_days_before
        settings.updated_at = datetime.now(UTC).replace(tzinfo=None)
        settings.updated_by_user_id = actor_user_id

        self._audit(
            actor_user_id=actor_user_id,
            action="billing_settings_updated",
            resource_type="billing_settings",
            resource_id="default",
            details={
                "previous": previous,
                "new": {
                    "grace_period_days": settings.grace_period_days,
                    "payment_due_reminder_days_before": settings.payment_due_reminder_days_before,
                },
            },
        )
        self.db.commit()
        self.db.refresh(settings)
        return settings

    def apply_discount(
        self,
        player_id: str,
        percent_off: int,
        actor_user_id: str,
    ) -> dict:
        """Apply an ongoing (duration=forever) percentage discount for a
        player — admin-only, reversible via remove_discount().

        If the player already has a real, paid Stripe subscription, the
        discount is attached to it directly (takes effect on the next
        invoice). Otherwise — the common case for a family who hasn't
        checked out yet — it's stored on their PlayerMembershipDB as a
        pending discount and applied automatically the moment their
        Stripe Checkout Session is created, so their very first invoice
        already reflects it. Either way, one Stripe coupon is reused per
        percentage rather than minting a new one every click.
        """
        subscription = self.get_subscription_for_player(player_id)

        if subscription is not None:
            coupon_id = self._ensure_discount_coupon(percent_off)
            updated = stripe.Subscription.modify(
                subscription.stripe_subscription_id,
                coupon=coupon_id,
            )
            result = self.upsert_subscription_from_stripe_object(
                _stripe_object_to_dict(updated)
            )
            self._audit(
                actor_user_id=actor_user_id,
                action="discount_applied",
                resource_type="player",
                resource_id=player_id,
                details={"percent_off": percent_off, "applies_to": "active_subscription"},
            )
            self.db.commit()
            return {
                "player_id": player_id,
                "discount_percent_off": result.discount_percent_off,
                "applies_to": "active_subscription",
            }

        membership = self.get_membership_for_player(player_id)
        if membership is None:
            raise BillingError(
                "Assign a membership plan to this player before applying a discount"
            )

        membership.discount_percent_off = percent_off
        membership.updated_at = datetime.now(UTC).replace(tzinfo=None)
        self._audit(
            actor_user_id=actor_user_id,
            action="discount_applied",
            resource_type="player",
            resource_id=player_id,
            details={"percent_off": percent_off, "applies_to": "pending_membership"},
        )
        self.db.commit()
        return {
            "player_id": player_id,
            "discount_percent_off": percent_off,
            "applies_to": "pending_membership",
        }

    def remove_discount(self, player_id: str, actor_user_id: str) -> dict:
        subscription = self.get_subscription_for_player(player_id)

        if subscription is not None and subscription.discount_percent_off is not None:
            stripe.Subscription.delete_discount(subscription.stripe_subscription_id)
            updated = stripe.Subscription.retrieve(subscription.stripe_subscription_id)
            result = self.upsert_subscription_from_stripe_object(
                _stripe_object_to_dict(updated)
            )
            self._audit(
                actor_user_id=actor_user_id,
                action="discount_removed",
                resource_type="player",
                resource_id=player_id,
                details={"applies_to": "active_subscription"},
            )
            self.db.commit()
            return {
                "player_id": player_id,
                "discount_percent_off": result.discount_percent_off,
                "applies_to": "active_subscription",
            }

        membership = self.get_membership_for_player(player_id)
        if membership is None or membership.discount_percent_off is None:
            raise BillingError("No discount is applied for this player")

        membership.discount_percent_off = None
        membership.updated_at = datetime.now(UTC).replace(tzinfo=None)
        self._audit(
            actor_user_id=actor_user_id,
            action="discount_removed",
            resource_type="player",
            resource_id=player_id,
            details={"applies_to": "pending_membership"},
        )
        self.db.commit()
        return {
            "player_id": player_id,
            "discount_percent_off": None,
            "applies_to": "pending_membership",
        }

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
        amount_cents: int,
        currency: str,
        billing_interval: str,
        stripe_price_id: str | None = None,
    ) -> MembershipPlanDB:
        if stripe_price_id is None:
            if not get_stripe_secret_key():
                raise BillingError(
                    "Billing is not configured — set a Stripe secret key "
                    "before creating a plan"
                )
            # Create a real Stripe Product + Price so the admin never has
            # to touch the Stripe dashboard just to set a price — this IS
            # "controlling the price" from inside Payment Control.
            try:
                price = stripe.Price.create(
                    unit_amount=amount_cents,
                    currency=currency.lower(),
                    recurring={"interval": billing_interval},
                    product_data={"name": name},
                )
            except stripe.StripeError as error:
                raise BillingError(f"Stripe rejected this plan: {error.user_message or error}")
            stripe_price_id = price["id"]
        else:
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
        amount_cents: int | None = None,
    ) -> MembershipPlanDB:
        plan = self.db.get(MembershipPlanDB, plan_id)
        if plan is None:
            raise BillingError("Plan not found")

        previous = {
            "name": plan.name,
            "active": plan.active,
            "amount_cents": plan.amount_cents,
        }
        if name is not None:
            plan.name = name
        if active is not None:
            plan.active = active
        if amount_cents is not None and amount_cents != plan.amount_cents:
            if not get_stripe_secret_key():
                raise BillingError(
                    "Billing is not configured — set a Stripe secret key "
                    "before changing a plan's price"
                )
            # Stripe Prices can't be edited in place — mint a new one and
            # archive the old one so it can't be picked for a new checkout.
            try:
                new_price = stripe.Price.create(
                    unit_amount=amount_cents,
                    currency=plan.currency,
                    recurring={"interval": plan.billing_interval},
                    product_data={"name": plan.name},
                )
            except stripe.StripeError as error:
                raise BillingError(
                    f"Stripe rejected this price change: {error.user_message or error}"
                )
            try:
                stripe.Price.modify(plan.stripe_price_id, active=False)
            except stripe.InvalidRequestError:
                pass
            plan.stripe_price_id = new_price["id"]
            plan.amount_cents = amount_cents
        plan.updated_at = datetime.now(UTC).replace(tzinfo=None)

        self._audit(
            actor_user_id=actor_user_id,
            action="membership_plan_updated",
            resource_type="membership_plan",
            resource_id=plan_id,
            details={
                "previous": previous,
                "new": {
                    "name": plan.name,
                    "active": plan.active,
                    "amount_cents": plan.amount_cents,
                },
            },
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

    def get_membership_plan_summary_for_player(self, player_id: str) -> dict | None:
        """The plan a staff member has assigned this player to, if any —
        shown to both the guardian (as "payment due") and staff (Player
        Profile -> Membership) before any Stripe subscription exists."""
        membership = self.get_membership_for_player(player_id)
        if membership is None or membership.plan_id is None:
            return None

        plan = self.db.get(MembershipPlanDB, membership.plan_id)
        if plan is None:
            return None

        return {
            "plan_id": plan.plan_id,
            "name": plan.name,
            "amount_cents": plan.amount_cents,
            "currency": plan.currency,
            "billing_interval": plan.billing_interval,
            "active": plan.active,
            "assigned_at": membership.assigned_at,
            "discount_percent_off": membership.discount_percent_off,
        }

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

    def get_financial_report(self) -> dict:
        """Revenue broken down by plan and by team, manual-vs-online split,
        and renewals due soon — grouped by each player's CURRENT plan/team
        assignment (not a historical snapshot at payment time), which is
        the right tradeoff for a small academy where reassignment is rare
        and this is a working dashboard, not a point-in-time ledger."""
        now = datetime.now(UTC).replace(tzinfo=None)
        renewal_cutoff = now + timedelta(days=14)

        online_payments = (
            self.db.query(PaymentDB.player_id, PaymentDB.amount, PaymentDB.currency)
            .filter(PaymentDB.status == "paid")
            .all()
        )
        manual_payments = (
            self.db.query(
                ManualPaymentDB.player_id,
                ManualPaymentDB.amount_cents,
                ManualPaymentDB.currency,
            ).all()
        )

        online_total_cents = sum(amount for _, amount, _ in online_payments)
        manual_total_cents = sum(amount for _, amount, _ in manual_payments)

        membership_by_player = {
            m.player_id: m for m in self.db.query(PlayerMembershipDB).all()
        }
        plan_by_id = {p.plan_id: p for p in self.db.query(MembershipPlanDB).all()}
        players_by_id = {p.player_id: p for p in self.db.query(PlayerDB).all()}
        teams_by_id = {t.team_id: t for t in self.db.query(TeamDB).all()}

        plan_totals: dict[str, dict] = {}
        team_totals: dict[str, dict] = {}

        def add_revenue(player_id: str, amount: int, currency: str) -> None:
            membership = membership_by_player.get(player_id)
            plan = (
                plan_by_id.get(membership.plan_id)
                if membership is not None and membership.plan_id is not None
                else None
            )
            plan_key = plan.plan_id if plan is not None else "unassigned"
            plan_bucket = plan_totals.setdefault(
                plan_key,
                {
                    "plan_name": plan.name if plan is not None else "No plan assigned",
                    "total_cents": 0,
                    "currency": currency,
                },
            )
            plan_bucket["total_cents"] += amount

            player = players_by_id.get(player_id)
            team = (
                teams_by_id.get(player.team_id)
                if player is not None and player.team_id is not None
                else None
            )
            team_key = team.team_id if team is not None else "unassigned"
            team_bucket = team_totals.setdefault(
                team_key,
                {
                    "team_name": team.name if team is not None else "No team assigned",
                    "total_cents": 0,
                    "currency": currency,
                },
            )
            team_bucket["total_cents"] += amount

        for player_id, amount, currency in online_payments:
            add_revenue(player_id, amount, currency)
        for player_id, amount_cents, currency in manual_payments:
            add_revenue(player_id, amount_cents, currency)

        upcoming = (
            self.db.query(SubscriptionDB)
            .filter(
                SubscriptionDB.status == "active",
                SubscriptionDB.cancel_at_period_end.is_(False),
                SubscriptionDB.current_period_end.isnot(None),
                SubscriptionDB.current_period_end >= now,
                SubscriptionDB.current_period_end <= renewal_cutoff,
            )
            .order_by(SubscriptionDB.current_period_end.asc())
            .all()
        )
        upcoming_renewals = []
        for subscription in upcoming:
            player = players_by_id.get(subscription.player_id)
            plan = (
                plan_by_id.get(subscription.plan_id)
                if subscription.plan_id is not None
                else None
            )
            upcoming_renewals.append({
                "player_id": subscription.player_id,
                "player_name": (
                    f"{player.first_name_en} {player.last_name_en}"
                    if player is not None
                    else subscription.player_id
                ),
                "plan_name": plan.name if plan is not None else None,
                "amount_cents": plan.amount_cents if plan is not None else None,
                "currency": plan.currency if plan is not None else None,
                "current_period_end": subscription.current_period_end,
            })

        return {
            "revenue_by_plan": list(plan_totals.values()),
            "revenue_by_team": list(team_totals.values()),
            "manual_vs_online_cents": {
                "manual_cents": manual_total_cents,
                "online_cents": online_total_cents,
            },
            "upcoming_renewals": upcoming_renewals,
        }

    def list_admin_billing_rows(self) -> list[dict]:
        players = self.db.query(PlayerDB).order_by(PlayerDB.first_name_en.asc()).all()
        rows = []

        for player in players:
            subscription = self.get_subscription_for_player(player.player_id)
            membership = self.get_membership_for_player(player.player_id)
            plan = (
                self.db.get(MembershipPlanDB, membership.plan_id)
                if membership is not None and membership.plan_id is not None
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
                    else (membership.discount_percent_off if membership is not None else None)
                ),
                "has_membership_assigned": membership is not None,
                "is_complimentary": membership.is_complimentary if membership is not None else False,
                "admin_override_eligibility": (
                    membership.admin_override_eligibility if membership is not None else None
                ),
                "membership_status": derive_membership_status(membership, subscription),
                "eligibility": derive_eligibility(membership, subscription),
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
