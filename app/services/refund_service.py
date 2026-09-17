"""Refund management — the only code path allowed to create a RefundDB row
or call Stripe's refund API. Every amount, payment id, and transaction
status used here comes from the database, never from anything the client
claims. The original PaymentDB/ManualPaymentDB row is never modified: a
payment's refunded total is always derived by summing this table's
succeeded rows for it, and its display status (PAID / PARTIALLY_REFUNDED /
REFUNDED / ...) is derived fresh every time from that sum, never stored
redundantly on the payment itself."""

from datetime import datetime, UTC
from uuid import uuid4

import stripe
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_stripe_secret_key
from app.db_models import AuditEventDB, ManualPaymentDB, PaymentDB, RefundDB
from app.services.id_service import next_entity_id

PAID = "PAID"
PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
REFUNDED = "REFUNDED"
PENDING = "PENDING"
FAILED = "FAILED"
PAST_DUE = "PAST_DUE"
VOID = "VOID"

# The only base (pre-refund) payment statuses this app's webhook handler
# ever writes are "paid" and "failed" — PENDING/PAST_DUE/VOID are included
# in the vocabulary for completeness/forward-compatibility (e.g. a future
# invoice.voided handler) but nothing currently produces them.
_BASE_STATUS_LABELS = {
    "paid": PAID,
    "failed": FAILED,
    "pending": PENDING,
    "past_due": PAST_DUE,
    "void": VOID,
}


class RefundError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _stripe_object_to_dict(value) -> dict:
    """Real stripe-python SDK objects deliberately aren't dict-like (no
    .get()) — convert to a plain dict so the rest of this module can use
    uniform dict access regardless of whether the caller passed a real
    Stripe object or an already-plain dict (as tests do)."""
    to_dict = getattr(value, "to_dict", None)
    return to_dict() if callable(to_dict) else value


def derive_payment_status(
    base_status: str, original_amount_cents: int, refunded_amount_cents: int
) -> str:
    """Pure function: the payment's raw webhook-driven status combined with
    its refund ledger, never a value stored on the payment itself — so a
    redelivered "invoice.paid" webhook can never clobber a REFUNDED payment
    back to PAID (see upsert_payment_from_stripe_invoice, which is
    deliberately refund-blind and only ever writes "paid"/"failed")."""
    if base_status != "paid":
        return _BASE_STATUS_LABELS.get(base_status, base_status.upper())
    if refunded_amount_cents <= 0:
        return PAID
    if refunded_amount_cents >= original_amount_cents:
        return REFUNDED
    return PARTIALLY_REFUNDED


class RefundService:
    def __init__(self, db: Session):
        self.db = db
        stripe.api_key = get_stripe_secret_key()

    def get_refunded_amount_cents(
        self,
        payment_id: str | None = None,
        manual_payment_id: str | None = None,
    ) -> int:
        query = self.db.query(func.coalesce(func.sum(RefundDB.amount_cents), 0)).filter(
            RefundDB.status == "succeeded"
        )
        if payment_id is not None:
            query = query.filter(RefundDB.payment_id == payment_id)
        if manual_payment_id is not None:
            query = query.filter(RefundDB.manual_payment_id == manual_payment_id)
        return int(query.scalar() or 0)

    def list_refunds_for_payment(self, payment_id: str) -> list[RefundDB]:
        return (
            self.db.query(RefundDB)
            .filter(RefundDB.payment_id == payment_id)
            .order_by(RefundDB.created_at.desc())
            .all()
        )

    def list_refunds_for_manual_payment(self, manual_payment_id: str) -> list[RefundDB]:
        return (
            self.db.query(RefundDB)
            .filter(RefundDB.manual_payment_id == manual_payment_id)
            .order_by(RefundDB.created_at.desc())
            .all()
        )

    def _resolve_refund_amount(
        self, refund_type: str, requested_amount_cents: int | None, remaining_cents: int
    ) -> int:
        if remaining_cents <= 0:
            raise RefundError("This payment has already been fully refunded")
        if refund_type == "full":
            return remaining_cents
        if requested_amount_cents is None or requested_amount_cents <= 0:
            raise RefundError("Refund amount must be greater than zero")
        if requested_amount_cents > remaining_cents:
            raise RefundError(
                "Refund amount exceeds the remaining refundable balance of "
                f"{remaining_cents} cents"
            )
        return requested_amount_cents

    def create_stripe_refund(
        self,
        stripe_invoice_id: str,
        actor_user_id: str,
        refund_type: str,
        amount_cents: int | None,
        reason: str,
        internal_note: str | None,
        idempotency_key: str,
    ) -> RefundDB:
        # Idempotent replay: a double-click or client retry with the SAME
        # key returns the already-created refund instead of refunding
        # twice. Stripe also receives this key below as a second layer.
        existing = (
            self.db.query(RefundDB)
            .filter(RefundDB.idempotency_key == idempotency_key)
            .first()
        )
        if existing is not None:
            return existing

        payment = self.db.get(PaymentDB, stripe_invoice_id)
        if payment is None:
            raise RefundError("Payment not found")
        if payment.status != "paid":
            raise RefundError("Only a successfully paid payment can be refunded")

        refunded_so_far = self.get_refunded_amount_cents(payment_id=stripe_invoice_id)
        remaining_cents = payment.amount - refunded_so_far
        resolved_amount = self._resolve_refund_amount(refund_type, amount_cents, remaining_cents)

        try:
            invoice = _stripe_object_to_dict(stripe.Invoice.retrieve(stripe_invoice_id))
            payment_intent_id = invoice.get("payment_intent")
            if not payment_intent_id:
                raise RefundError("This payment has no Stripe charge to refund")
            stripe_refund = _stripe_object_to_dict(stripe.Refund.create(
                payment_intent=payment_intent_id,
                amount=resolved_amount,
                idempotency_key=idempotency_key,
            ))
        except stripe.StripeError as error:
            raise RefundError(f"Stripe rejected this refund: {error.user_message or error}")

        stripe_refund_id = stripe_refund["id"]

        refund = RefundDB(
            refund_id=next_entity_id(self.db, "refund"),
            payment_id=stripe_invoice_id,
            manual_payment_id=None,
            stripe_refund_id=stripe_refund_id,
            idempotency_key=idempotency_key,
            refund_source="stripe",
            refund_type=refund_type,
            amount_cents=resolved_amount,
            currency=payment.currency,
            reason=reason,
            internal_note=internal_note,
            status="succeeded",
            created_by_user_id=actor_user_id,
            created_at=_now(),
        )
        self.db.add(refund)

        previous_status = derive_payment_status(payment.status, payment.amount, refunded_so_far)
        new_status = derive_payment_status(
            payment.status, payment.amount, refunded_so_far + resolved_amount
        )
        self._audit(
            actor_user_id=actor_user_id,
            action="payment_refunded",
            resource_type="payment",
            resource_id=stripe_invoice_id,
            details={
                "refund_id": refund.refund_id,
                "player_id": payment.player_id,
                "original_amount_cents": payment.amount,
                "refund_amount_cents": resolved_amount,
                "reason": reason,
                "stripe_refund_id": stripe_refund_id,
                "previous_status": previous_status,
                "new_status": new_status,
            },
        )
        self.db.commit()
        self.db.refresh(refund)
        return refund

    def create_manual_refund(
        self,
        manual_payment_id: str,
        actor_user_id: str,
        refund_type: str,
        amount_cents: int | None,
        reason: str,
        internal_note: str | None,
        idempotency_key: str,
    ) -> RefundDB:
        existing = (
            self.db.query(RefundDB)
            .filter(RefundDB.idempotency_key == idempotency_key)
            .first()
        )
        if existing is not None:
            return existing

        payment = self.db.get(ManualPaymentDB, manual_payment_id)
        if payment is None:
            raise RefundError("Manual payment not found")

        refunded_so_far = self.get_refunded_amount_cents(manual_payment_id=manual_payment_id)
        remaining_cents = payment.amount_cents - refunded_so_far
        resolved_amount = self._resolve_refund_amount(refund_type, amount_cents, remaining_cents)

        refund = RefundDB(
            refund_id=next_entity_id(self.db, "refund"),
            payment_id=None,
            manual_payment_id=manual_payment_id,
            stripe_refund_id=None,
            idempotency_key=idempotency_key,
            refund_source="manual",
            refund_type=refund_type,
            amount_cents=resolved_amount,
            currency=payment.currency,
            reason=reason,
            internal_note=internal_note,
            status="succeeded",
            created_by_user_id=actor_user_id,
            created_at=_now(),
        )
        self.db.add(refund)

        previous_status = derive_payment_status(
            "paid", payment.amount_cents, refunded_so_far
        )
        new_status = derive_payment_status(
            "paid", payment.amount_cents, refunded_so_far + resolved_amount
        )
        self._audit(
            actor_user_id=actor_user_id,
            action="manual_payment_refunded",
            resource_type="manual_payment",
            resource_id=manual_payment_id,
            details={
                "refund_id": refund.refund_id,
                "player_id": payment.player_id,
                "original_amount_cents": payment.amount_cents,
                "refund_amount_cents": resolved_amount,
                "reason": reason,
                "stripe_refund_id": None,
                "previous_status": previous_status,
                "new_status": new_status,
            },
        )
        self.db.commit()
        self.db.refresh(refund)
        return refund

    def reconcile_external_stripe_refunds(self, charge: dict) -> None:
        """A refund created directly in the Stripe Dashboard (bypassing
        this app entirely) still needs to show up in our records for
        accounting accuracy — this NEVER initiates or executes a refund,
        it only observes charge.refunded webhook events and records what
        Stripe reports, skipping any stripe_refund_id we already know
        about (i.e. one we created ourselves through create_stripe_refund
        above)."""
        stripe_invoice_id = charge.get("invoice")
        if not stripe_invoice_id:
            return
        payment = self.db.get(PaymentDB, stripe_invoice_id)
        if payment is None:
            return

        refunds_data = (charge.get("refunds") or {}).get("data") or []
        wrote_any = False
        for stripe_refund in refunds_data:
            stripe_refund_id = stripe_refund.get("id")
            if not stripe_refund_id:
                continue
            already_known = (
                self.db.query(RefundDB)
                .filter(RefundDB.stripe_refund_id == stripe_refund_id)
                .first()
            )
            if already_known is not None:
                continue

            amount_cents = stripe_refund.get("amount", 0)
            self.db.add(RefundDB(
                refund_id=next_entity_id(self.db, "refund"),
                payment_id=stripe_invoice_id,
                manual_payment_id=None,
                stripe_refund_id=stripe_refund_id,
                idempotency_key=f"external-{stripe_refund_id}",
                refund_source="external",
                refund_type="full" if amount_cents >= payment.amount else "partial",
                amount_cents=amount_cents,
                currency=stripe_refund.get("currency", payment.currency),
                reason="Processed directly with the payment provider, outside this platform",
                internal_note=None,
                status="succeeded" if stripe_refund.get("status") in (None, "succeeded") else stripe_refund["status"],
                created_by_user_id=None,
                created_at=_now(),
            ))
            wrote_any = True

        # No AuditEventDB row here: that table requires a real local
        # actor_user_id (NOT NULL), and by definition no admin in this
        # app took this action — the RefundDB row itself, with
        # refund_source="external", is the permanent record.
        if wrote_any:
            self.db.commit()

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
                occurred_at=_now(),
                actor_user_id=actor_user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                details=details,
            )
        )
