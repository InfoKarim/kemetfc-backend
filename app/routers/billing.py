from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api_schemas import CreateCheckoutSessionSchema
from app.database import get_db
from app.db_models import UserDB
from app.dependencies import require_guardian_player_access
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
    }


@router.get("/billing")
def billing_page():
    return FileResponse(STATIC_DIR / "billing.html")


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

    return {"received": True}
