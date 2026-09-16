from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api_schemas import (
    ApplyDiscountSchema,
    AssignMembershipPlanSchema,
    CreateCheckoutSessionSchema,
    CreateMembershipPlanSchema,
    RecordManualPaymentSchema,
    RefundPaymentSchema,
    UpdateMembershipPlanSchema,
)
from app.database import get_db
from app.db_models import UserDB
from app.dependencies import require_admin, require_guardian_player_access
from app.services.billing_service import BillingError, BillingService, is_configured
from app.services.player_service import PlayerService

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


@router.get("/billing/status/{player_id}")
def get_billing_status(
    player_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    require_guardian_player_access(request, db, player_id)

    if PlayerService(db=db).get_player(player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")

    subscription = BillingService(db=db).get_subscription_for_player(player_id)

    return {
        "configured": is_configured(),
        "subscription": (
            _subscription_payload(subscription) if subscription is not None else None
        ),
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
        updated = BillingService(db=db).apply_discount(player_id, payload.percent_off)
    except BillingError as error:
        raise HTTPException(status_code=404, detail=str(error))

    return _subscription_payload(updated)


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
        updated = BillingService(db=db).remove_discount(player_id)
    except BillingError as error:
        raise HTTPException(status_code=404, detail=str(error))

    return _subscription_payload(updated)


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
    data_object = _as_plain_dict(event["data"]["object"])

    if event_type in {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }:
        service.upsert_subscription_from_stripe_object(data_object)
    elif event_type == "checkout.session.completed" and data_object.get("mode") == "subscription":
        import stripe

        subscription = stripe.Subscription.retrieve(data_object["subscription"])
        service.upsert_subscription_from_stripe_object(_as_plain_dict(subscription))
    elif event_type == "invoice.paid":
        service.upsert_payment_from_stripe_invoice(data_object, status="paid")
    elif event_type == "invoice.payment_failed":
        service.upsert_payment_from_stripe_invoice(data_object, status="failed")

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
