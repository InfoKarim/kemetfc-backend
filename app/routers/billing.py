from datetime import datetime, UTC
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api_schemas import (
    ApplyDiscountSchema,
    AssignMembershipPlanSchema,
    CreateCheckoutSessionSchema,
    CreateFamilyDiscountRuleSchema,
    CreateMembershipPlanSchema,
    CreatePromoCodeSchema,
    GrantComplimentaryMembershipSchema,
    RecordManualPaymentSchema,
    RefundPaymentSchema,
    SetEligibilityOverrideSchema,
    UpdateBillingSettingsSchema,
    UpdateFamilyDiscountRuleSchema,
    UpdateMembershipPlanSchema,
    UpdatePromoCodeSchema,
)
from app.database import get_db
from app.db_models import FamilyDiscountRuleDB, PaymentDB, SubscriptionDB, UserDB
from app.dependencies import require_admin, require_guardian_player_access
from app.services.billing_service import BillingError, BillingService, is_configured
from app.services.eligibility_service import derive_eligibility, derive_membership_status
from app.services.id_service import next_entity_id
from app.services.notification_service import NotificationService
from app.services.player_service import PlayerService
from app.services.promo_code_service import PromoCodeError, PromoCodeService

router = APIRouter()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _as_plain_dict(value):
    """Stripe SDK objects deliberately aren't dict-like (no .get()) — convert
    to a real dict so downstream code can use plain dict access uniformly."""
    to_dict = getattr(value, "to_dict", None)
    return to_dict() if callable(to_dict) else value


def _subscription_payload(subscription) -> dict:
    return {
        "stripe_subscription_id": subscription.stripe_subscription_id,
        "player_id": subscription.player_id,
        "status": subscription.status,
        "current_period_end": subscription.current_period_end,
        "cancel_at_period_end": subscription.cancel_at_period_end,
        "discount_percent_off": subscription.discount_percent_off,
        "plan_id": subscription.plan_id,
    }


def _plan_payload(plan) -> dict:
    return {
        "plan_id": plan.plan_id,
        "name": plan.name,
        "stripe_price_id": plan.stripe_price_id,
        "amount_cents": plan.amount_cents,
        "currency": plan.currency,
        "billing_interval": plan.billing_interval,
        "active": plan.active,
    }


def _manual_payment_payload(payment) -> dict:
    return {
        "manual_payment_id": payment.manual_payment_id,
        "player_id": payment.player_id,
        "amount_cents": payment.amount_cents,
        "currency": payment.currency,
        "method": payment.method,
        "payment_date": payment.payment_date,
        "note": payment.note,
        "recorded_by_user_id": payment.recorded_by_user_id,
        "recorded_at": payment.recorded_at,
        "source": "manual",
    }


@router.get("/billing")
def billing_page():
    return FileResponse(STATIC_DIR / "billing.html")


@router.get("/payment-control")
def payment_control_page():
    return FileResponse(STATIC_DIR / "payment_control.html")


@router.get("/payment-settings")
def payment_settings_page():
    return FileResponse(STATIC_DIR / "payment_settings.html")


@router.get("/billing/status/{player_id}")
def get_billing_status(
    player_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    require_guardian_player_access(request, db, player_id)

    if PlayerService(db=db).get_player(player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")

    service = BillingService(db=db)
    subscription = service.get_subscription_for_player(player_id)
    membership = service.get_membership_for_player(player_id)

    return {
        "configured": is_configured(),
        "subscription": (
            _subscription_payload(subscription) if subscription is not None else None
        ),
        "membership": service.get_membership_plan_summary_for_player(player_id),
        "membership_status": derive_membership_status(membership, subscription),
        "eligibility": derive_eligibility(membership, subscription),
    }


@router.post("/billing/checkout-session", status_code=201)
def create_checkout_session(
    checkout_data: CreateCheckoutSessionSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    require_guardian_player_access(request, db, checkout_data.player_id)

    if PlayerService(db=db).get_player(checkout_data.player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")

    user_id = request.state.current_user["user_id"]
    user = db.get(UserDB, user_id)

    if user is None or not user.email:
        raise HTTPException(
            status_code=400,
            detail="Your account needs an email on file before subscribing",
        )

    try:
        checkout_url = BillingService(db=db).create_checkout_session(
            player_id=checkout_data.player_id,
            paying_user_id=user_id,
            guardian_email=user.email,
            promo_code=checkout_data.promo_code,
        )
    except BillingError as error:
        raise HTTPException(status_code=404, detail=str(error))

    return {"checkout_url": checkout_url}


@router.post("/billing/cancel")
def cancel_subscription(
    checkout_data: CreateCheckoutSessionSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    require_guardian_player_access(request, db, checkout_data.player_id)

    service = BillingService(db=db)
    subscription = service.get_subscription_for_player(checkout_data.player_id)

    if subscription is None:
        raise HTTPException(status_code=404, detail="No subscription found")

    try:
        updated = service.cancel_subscription(subscription.stripe_subscription_id)
    except BillingError as error:
        raise HTTPException(status_code=404, detail=str(error))

    return _subscription_payload(updated)


@router.post("/billing/subscriptions/{player_id}/discount")
def apply_subscription_discount(
    player_id: str,
    payload: ApplyDiscountSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    # Discounts are an admin/finance decision, not a coach or guardian
    # action — deliberately require_admin here rather than the guardian
    # ownership check used elsewhere in this file.
    require_admin(request)

    if PlayerService(db=db).get_player(player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")

    try:
        result = BillingService(db=db).apply_discount(
            player_id,
            payload.percent_off,
            actor_user_id=request.state.current_user["user_id"],
        )
    except BillingError as error:
        raise HTTPException(status_code=404, detail=str(error))

    return result


@router.delete("/billing/subscriptions/{player_id}/discount")
def remove_subscription_discount(
    player_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)

    if PlayerService(db=db).get_player(player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")

    try:
        result = BillingService(db=db).remove_discount(
            player_id,
            actor_user_id=request.state.current_user["user_id"],
        )
    except BillingError as error:
        raise HTTPException(status_code=404, detail=str(error))

    return result


_SUBSCRIPTION_NOTIFICATIONS = {
    "past_due": ("payment_past_due", "Payment past due", "A payment on your membership is past due — please update your payment method."),
    "unpaid": ("payment_past_due", "Payment past due", "A payment on your membership is past due — please update your payment method."),
    "canceled": ("membership_cancelled", "Membership cancelled", "Your membership has been cancelled."),
    "active": ("membership_activated", "Membership active", "Your membership is now active."),
}


def notify_membership_status_change(db: Session, subscription: SubscriptionDB, event_type: str) -> None:
    # Only worth telling the guardian about on genuine status transitions —
    # not every "updated" event changes anything they'd care about (e.g. a
    # metadata-only update), and "active" right after checkout.session
    # already gets its own confirmation, so skip the generic one there.
    if event_type == "customer.subscription.created":
        return
    notification = _SUBSCRIPTION_NOTIFICATIONS.get(subscription.status)
    if notification is None:
        return
    notif_type, title, body = notification
    NotificationService(db=db).create_notification(
        user_id=subscription.paying_user_id,
        type=notif_type,
        title=title,
        body=body,
        link="/billing",
    )


def notify_payment_result(db: Session, payment: PaymentDB | None, succeeded: bool) -> None:
    if payment is None:
        return
    subscription = db.get(SubscriptionDB, payment.stripe_subscription_id)
    if subscription is None:
        return
    amount = f"{payment.amount / 100:.2f} {payment.currency.upper()}"
    if succeeded:
        NotificationService(db=db).create_notification(
            user_id=subscription.paying_user_id,
            type="payment_received",
            title="Payment received",
            body=f"We received your payment of {amount}. Your receipt is available in Billing.",
            link="/billing",
        )
    else:
        NotificationService(db=db).create_notification(
            user_id=subscription.paying_user_id,
            type="payment_failed",
            title="Payment failed",
            body=f"Your payment of {amount} could not be processed. Please update your payment method.",
            link="/billing",
        )


@router.post("/billing/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    signature_header = request.headers.get("stripe-signature", "")

    service = BillingService(db=db)

    try:
        event = service.construct_webhook_event(payload, signature_header)
    except BillingError as error:
        raise HTTPException(status_code=400, detail=str(error))

    event_type = event["type"]
    event_id = event["id"]

    # Explicit idempotency ledger — defense in depth on top of the
    # upsert-by-Stripe-ID pattern the handlers below already use, and the
    # single place that guarantees a redelivered event never sends a
    # duplicate notification either. Checked before processing but only
    # marked AFTER it succeeds, so a failure mid-processing still gets
    # genuinely retried instead of being silently swallowed.
    if service.has_processed_stripe_event(event_id):
        return {"received": True, "duplicate": True}

    data_object = _as_plain_dict(event["data"]["object"])

    if event_type in {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }:
        subscription = service.upsert_subscription_from_stripe_object(data_object)
        notify_membership_status_change(db, subscription, event_type)
    elif event_type == "checkout.session.completed" and data_object.get("mode") == "subscription":
        import stripe

        subscription = stripe.Subscription.retrieve(data_object["subscription"])
        service.upsert_subscription_from_stripe_object(_as_plain_dict(subscription))
    elif event_type == "invoice.paid":
        payment = service.upsert_payment_from_stripe_invoice(data_object, status="paid")
        notify_payment_result(db, payment, succeeded=True)
    elif event_type == "invoice.payment_failed":
        payment = service.upsert_payment_from_stripe_invoice(data_object, status="failed")
        notify_payment_result(db, payment, succeeded=False)

    service.mark_stripe_event_processed(event_id, event_type)
    return {"received": True}


def _payment_payload(payment) -> dict:
    return {
        "stripe_invoice_id": payment.stripe_invoice_id,
        "player_id": payment.player_id,
        "amount": payment.amount,
        "currency": payment.currency,
        "status": payment.status,
        "hosted_invoice_url": payment.hosted_invoice_url,
        "invoice_pdf_url": payment.invoice_pdf_url,
        "period_end": payment.period_end,
        "created_at": payment.created_at,
    }


@router.get("/billing/payments/{player_id}")
def list_payments(
    player_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    require_guardian_player_access(request, db, player_id)

    if PlayerService(db=db).get_player(player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")

    payments = BillingService(db=db).list_payments_for_player(player_id)
    return {"payments": [_payment_payload(payment) for payment in payments]}


@router.post("/billing/subscriptions/{player_id}/pause")
def pause_subscription(player_id: str, request: Request, db: Session = Depends(get_db)):
    require_admin(request)

    if PlayerService(db=db).get_player(player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")

    try:
        updated = BillingService(db=db).pause_subscription(
            player_id, request.state.current_user["user_id"]
        )
    except BillingError as error:
        raise HTTPException(status_code=404, detail=str(error))

    return _subscription_payload(updated)


@router.post("/billing/subscriptions/{player_id}/resume")
def resume_subscription(player_id: str, request: Request, db: Session = Depends(get_db)):
    require_admin(request)

    if PlayerService(db=db).get_player(player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")

    try:
        updated = BillingService(db=db).resume_subscription(
            player_id, request.state.current_user["user_id"]
        )
    except BillingError as error:
        raise HTTPException(status_code=404, detail=str(error))

    return _subscription_payload(updated)


@router.post("/billing/payments/{stripe_invoice_id}/refund")
def refund_payment(
    stripe_invoice_id: str,
    payload: RefundPaymentSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)

    try:
        refund = BillingService(db=db).refund_payment(
            stripe_invoice_id,
            actor_user_id=request.state.current_user["user_id"],
            amount_cents=payload.amount_cents,
            reason=payload.reason,
        )
    except BillingError as error:
        raise HTTPException(status_code=404, detail=str(error))

    return {"refund_id": refund.get("id"), "status": refund.get("status")}


@router.post("/billing/manual-payments/{player_id}", status_code=201)
def record_manual_payment(
    player_id: str,
    payload: RecordManualPaymentSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)

    if PlayerService(db=db).get_player(player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")

    payment = BillingService(db=db).record_manual_payment(
        player_id=player_id,
        actor_user_id=request.state.current_user["user_id"],
        amount_cents=payload.amount_cents,
        currency=payload.currency,
        method=payload.method,
        payment_date=payload.payment_date,
        note=payload.note,
    )
    return _manual_payment_payload(payment)


@router.get("/billing/manual-payments/{player_id}")
def list_manual_payments(player_id: str, request: Request, db: Session = Depends(get_db)):
    require_guardian_player_access(request, db, player_id)

    if PlayerService(db=db).get_player(player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")

    payments = BillingService(db=db).list_manual_payments_for_player(player_id)
    return {"payments": [_manual_payment_payload(payment) for payment in payments]}


@router.get("/billing/membership-plans")
def list_membership_plans(request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    plans = BillingService(db=db).list_membership_plans()
    return {"plans": [_plan_payload(plan) for plan in plans]}


@router.post("/billing/membership-plans", status_code=201)
def create_membership_plan(
    payload: CreateMembershipPlanSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)

    try:
        plan = BillingService(db=db).create_membership_plan(
            actor_user_id=request.state.current_user["user_id"],
            name=payload.name,
            stripe_price_id=payload.stripe_price_id,
            amount_cents=payload.amount_cents,
            currency=payload.currency,
            billing_interval=payload.billing_interval,
        )
    except BillingError as error:
        raise HTTPException(status_code=400, detail=str(error))

    return _plan_payload(plan)


@router.patch("/billing/membership-plans/{plan_id}")
def update_membership_plan(
    plan_id: str,
    payload: UpdateMembershipPlanSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)

    try:
        plan = BillingService(db=db).update_membership_plan(
            plan_id,
            actor_user_id=request.state.current_user["user_id"],
            name=payload.name,
            active=payload.active,
            amount_cents=payload.amount_cents,
        )
    except BillingError as error:
        raise HTTPException(status_code=404, detail=str(error))

    return _plan_payload(plan)


@router.post("/billing/subscriptions/{player_id}/membership")
def assign_membership(
    player_id: str,
    payload: AssignMembershipPlanSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)

    if PlayerService(db=db).get_player(player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")

    try:
        membership = BillingService(db=db).assign_membership_plan(
            player_id,
            payload.plan_id,
            actor_user_id=request.state.current_user["user_id"],
        )
    except BillingError as error:
        raise HTTPException(status_code=400, detail=str(error))

    return {
        "player_id": membership.player_id,
        "plan_id": membership.plan_id,
        "assigned_by_user_id": membership.assigned_by_user_id,
        "assigned_at": membership.assigned_at,
    }


@router.get("/billing/admin/summary")
def get_admin_billing_summary(request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    return BillingService(db=db).get_admin_billing_summary()


@router.get("/billing/admin/players")
def list_admin_billing_rows(request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    return {"rows": BillingService(db=db).list_admin_billing_rows()}


@router.get("/billing/admin/report")
def get_financial_report(request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    return BillingService(db=db).get_financial_report()


def _promo_code_payload(promo) -> dict:
    return {
        "promo_code_id": promo.promo_code_id,
        "code": promo.code,
        "discount_type": promo.discount_type,
        "discount_value": promo.discount_value,
        "starts_at": promo.starts_at,
        "expires_at": promo.expires_at,
        "max_uses": promo.max_uses,
        "per_family_limit": promo.per_family_limit,
        "eligible_plan_ids": promo.eligible_plan_ids,
        "active": promo.active,
    }


@router.post("/billing/promo-codes", status_code=201)
def create_promo_code(
    payload: CreatePromoCodeSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)
    try:
        promo = PromoCodeService(db=db).create_promo_code(
            actor_user_id=request.state.current_user["user_id"],
            code=payload.code,
            discount_type=payload.discount_type,
            discount_value=payload.discount_value,
            starts_at=payload.starts_at,
            expires_at=payload.expires_at,
            max_uses=payload.max_uses,
            per_family_limit=payload.per_family_limit,
            eligible_plan_ids=payload.eligible_plan_ids,
        )
    except PromoCodeError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return _promo_code_payload(promo)


@router.get("/billing/promo-codes")
def list_promo_codes(request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    promos = PromoCodeService(db=db).list_promo_codes()
    return {"promo_codes": [_promo_code_payload(promo) for promo in promos]}


@router.patch("/billing/promo-codes/{promo_code_id}")
def update_promo_code(
    promo_code_id: str,
    payload: UpdatePromoCodeSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)
    try:
        promo = PromoCodeService(db=db).update_promo_code(
            promo_code_id,
            actor_user_id=request.state.current_user["user_id"],
            active=payload.active,
        )
    except PromoCodeError as error:
        raise HTTPException(status_code=404, detail=str(error))
    return _promo_code_payload(promo)


@router.post("/billing/subscriptions/{player_id}/complimentary")
def grant_complimentary_membership(
    player_id: str,
    payload: GrantComplimentaryMembershipSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)
    if PlayerService(db=db).get_player(player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")

    membership = BillingService(db=db).grant_complimentary_membership(
        player_id,
        actor_user_id=request.state.current_user["user_id"],
        plan_id=payload.plan_id,
    )
    return {
        "player_id": membership.player_id,
        "plan_id": membership.plan_id,
        "is_complimentary": membership.is_complimentary,
    }


@router.delete("/billing/subscriptions/{player_id}/complimentary")
def revoke_complimentary_membership(
    player_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)
    try:
        membership = BillingService(db=db).revoke_complimentary_membership(
            player_id,
            actor_user_id=request.state.current_user["user_id"],
        )
    except BillingError as error:
        raise HTTPException(status_code=404, detail=str(error))
    return {
        "player_id": membership.player_id,
        "plan_id": membership.plan_id,
        "is_complimentary": membership.is_complimentary,
    }


@router.put("/billing/subscriptions/{player_id}/eligibility-override")
def set_eligibility_override(
    player_id: str,
    payload: SetEligibilityOverrideSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)
    try:
        membership = BillingService(db=db).set_admin_override_eligibility(
            player_id,
            actor_user_id=request.state.current_user["user_id"],
            reason=payload.reason,
        )
    except BillingError as error:
        raise HTTPException(status_code=404, detail=str(error))
    return {
        "player_id": membership.player_id,
        "admin_override_eligibility": membership.admin_override_eligibility,
    }


def _billing_settings_payload(settings) -> dict:
    return {
        "grace_period_days": settings.grace_period_days,
        "payment_due_reminder_days_before": settings.payment_due_reminder_days_before,
        "updated_at": settings.updated_at,
    }


@router.get("/billing/settings")
def get_billing_settings(request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    return _billing_settings_payload(BillingService(db=db).get_billing_settings())


@router.patch("/billing/settings")
def update_billing_settings(
    payload: UpdateBillingSettingsSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)
    settings = BillingService(db=db).update_billing_settings(
        actor_user_id=request.state.current_user["user_id"],
        grace_period_days=payload.grace_period_days,
        payment_due_reminder_days_before=payload.payment_due_reminder_days_before,
    )
    return _billing_settings_payload(settings)


def _family_discount_rule_payload(rule) -> dict:
    return {
        "rule_id": rule.rule_id,
        "sibling_position": rule.sibling_position,
        "discount_percent": rule.discount_percent,
        "active": rule.active,
    }


@router.get("/billing/family-discount-rules")
def list_family_discount_rules(request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    rules = (
        db.query(FamilyDiscountRuleDB)
        .order_by(FamilyDiscountRuleDB.sibling_position.asc())
        .all()
    )
    return {"rules": [_family_discount_rule_payload(rule) for rule in rules]}


@router.post("/billing/family-discount-rules", status_code=201)
def create_family_discount_rule(
    payload: CreateFamilyDiscountRuleSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)

    existing = (
        db.query(FamilyDiscountRuleDB)
        .filter(FamilyDiscountRuleDB.sibling_position == payload.sibling_position)
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=400,
            detail=f"A rule for sibling position {payload.sibling_position} already exists",
        )


    now = datetime.now(UTC).replace(tzinfo=None)
    rule = FamilyDiscountRuleDB(
        rule_id=next_entity_id(db, "family_discount_rule"),
        sibling_position=payload.sibling_position,
        discount_percent=payload.discount_percent,
        active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return _family_discount_rule_payload(rule)


@router.patch("/billing/family-discount-rules/{rule_id}")
def update_family_discount_rule(
    rule_id: str,
    payload: UpdateFamilyDiscountRuleSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request)
    rule = db.get(FamilyDiscountRuleDB, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")


    if payload.discount_percent is not None:
        rule.discount_percent = payload.discount_percent
    if payload.active is not None:
        rule.active = payload.active
    rule.updated_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()
    db.refresh(rule)
    return _family_discount_rule_payload(rule)


@router.post("/billing/portal-session")
def create_billing_portal_session(request: Request, db: Session = Depends(get_db)):
    # Any authenticated guardian may open a portal session for their OWN
    # Stripe customer record — there is no player_id in this request to
    # scope, so this deliberately does not use require_guardian_player_access;
    # the guardian's own user_id is the only identity involved.
    if request.state.current_user["role"] not in {"guardian", "admin"}:
        raise HTTPException(status_code=403, detail="Not permitted")

    user_id = request.state.current_user["user_id"]
    user = db.get(UserDB, user_id)
    if user is None or not user.email:
        raise HTTPException(
            status_code=400,
            detail="Your account needs an email on file first",
        )

    try:
        url = BillingService(db=db).create_billing_portal_session(user_id, user.email)
    except BillingError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return {"portal_url": url}
