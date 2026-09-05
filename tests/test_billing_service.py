from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.db_models import PlayerDB, SubscriptionDB, UserDB
from app.services import billing_service
from app.services.billing_service import BillingError, BillingService

TEST_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
)

TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def make_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    db.add(UserDB(
        user_id="U1",
        username="guardian1",
        password_hash="x",
        role="guardian",
        active=True,
        email="guardian@example.com",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    ))
    db.add(PlayerDB(
        player_id="P1",
        first_name_ar="ط",
        last_name_ar="ط",
        first_name_en="Test",
        last_name_en="Player",
        date_of_birth=date(2015, 1, 1),
        sex="male",
        physical_profile={"height_cm": 120, "weight_kg": 30, "dominant_foot": "right", "speed": 70, "acceleration": 70, "agility": 70, "stamina": 70, "strength": 70},
        technical_profile={"ball_control": 70, "dribbling": 70, "passing": 70, "shooting": 70, "finishing": 70},
        mental_profile={"decision_making": 70, "concentration": 70, "composure": 70, "positioning": 70, "vision": 70, "awareness": 70, "game_reading": 70, "coachability": 70},
        match_performance={"minutes_played": 0, "goals": 0, "assists": 0, "shots": 0, "shots_on_target": 0, "passes_attempted": 0, "passes_completed": 0, "tackles": 0, "interceptions": 0, "rating": 0},
        created_at=datetime.now(),
    ))
    db.commit()
    return db


def test_is_configured_requires_secret_key_and_price(monkeypatch):
    monkeypatch.setattr(billing_service, "get_stripe_secret_key", lambda: "")
    monkeypatch.setattr(billing_service, "get_stripe_price_id", lambda: "")
    assert billing_service.is_configured() is False

    monkeypatch.setattr(billing_service, "get_stripe_secret_key", lambda: "sk_test_x")
    monkeypatch.setattr(billing_service, "get_stripe_price_id", lambda: "price_x")
    assert billing_service.is_configured() is True


def test_create_checkout_session_raises_when_not_configured(monkeypatch):
    monkeypatch.setattr(billing_service, "get_stripe_secret_key", lambda: "")
    monkeypatch.setattr(billing_service, "get_stripe_price_id", lambda: "")

    db = make_db()
    service = BillingService(db=db)

    with pytest.raises(BillingError):
        service.create_checkout_session("P1", "U1", "guardian@example.com")


def test_create_checkout_session_calls_stripe_with_expected_params(monkeypatch):
    monkeypatch.setattr(billing_service, "get_stripe_secret_key", lambda: "sk_test_x")
    monkeypatch.setattr(billing_service, "get_stripe_price_id", lambda: "price_x")
    monkeypatch.setattr(
        billing_service, "get_billing_return_base_url", lambda: "https://app.kemetfc.com"
    )

    captured = {}

    class FakeSession:
        url = "https://checkout.stripe.com/session/abc"

    def fake_create(**params):
        captured.update(params)
        return FakeSession()

    monkeypatch.setattr(billing_service.stripe.checkout.Session, "create", fake_create)

    db = make_db()
    service = BillingService(db=db)
    url = service.create_checkout_session("P1", "U1", "guardian@example.com")

    assert url == "https://checkout.stripe.com/session/abc"
    assert captured["mode"] == "subscription"
    assert captured["line_items"] == [{"price": "price_x", "quantity": 1}]
    assert captured["customer_email"] == "guardian@example.com"
    assert captured["client_reference_id"] == "P1"
    assert captured["metadata"] == {"player_id": "P1", "paying_user_id": "U1"}
    assert captured["success_url"] == "https://app.kemetfc.com/billing?checkout=success"
    assert captured["cancel_url"] == "https://app.kemetfc.com/billing?checkout=cancelled"


def test_upsert_subscription_creates_new_row():
    db = make_db()
    service = BillingService(db=db)

    subscription_object = {
        "id": "sub_123",
        "customer": "cus_123",
        "status": "active",
        "current_period_end": 1893456000,
        "cancel_at_period_end": False,
        "items": {"data": [{"price": {"id": "price_x"}}]},
        "metadata": {"player_id": "P1", "paying_user_id": "U1"},
    }

    row = service.upsert_subscription_from_stripe_object(subscription_object)

    assert row.stripe_subscription_id == "sub_123"
    assert row.player_id == "P1"
    assert row.paying_user_id == "U1"
    assert row.status == "active"
    assert row.stripe_price_id == "price_x"
    assert db.get(SubscriptionDB, "sub_123") is not None


def test_upsert_subscription_updates_existing_row():
    db = make_db()
    service = BillingService(db=db)

    base_object = {
        "id": "sub_123",
        "customer": "cus_123",
        "status": "active",
        "current_period_end": 1893456000,
        "cancel_at_period_end": False,
        "items": {"data": [{"price": {"id": "price_x"}}]},
        "metadata": {"player_id": "P1", "paying_user_id": "U1"},
    }
    service.upsert_subscription_from_stripe_object(base_object)

    updated_object = dict(base_object, status="past_due", cancel_at_period_end=True)
    row = service.upsert_subscription_from_stripe_object(updated_object)

    assert row.status == "past_due"
    assert row.cancel_at_period_end is True
    assert db.query(SubscriptionDB).count() == 1


def test_upsert_subscription_requires_metadata():
    db = make_db()
    service = BillingService(db=db)

    subscription_object = {
        "id": "sub_123",
        "customer": "cus_123",
        "status": "active",
        "current_period_end": None,
        "cancel_at_period_end": False,
        "items": {"data": [{"price": {"id": "price_x"}}]},
        "metadata": {},
    }

    with pytest.raises(BillingError):
        service.upsert_subscription_from_stripe_object(subscription_object)


def test_get_subscription_for_player_returns_latest():
    db = make_db()
    service = BillingService(db=db)

    service.upsert_subscription_from_stripe_object({
        "id": "sub_1",
        "customer": "cus_1",
        "status": "canceled",
        "current_period_end": None,
        "cancel_at_period_end": True,
        "items": {"data": [{"price": {"id": "price_x"}}]},
        "metadata": {"player_id": "P1", "paying_user_id": "U1"},
    })

    result = service.get_subscription_for_player("P1")
    assert result.stripe_subscription_id == "sub_1"

    assert service.get_subscription_for_player("DOES_NOT_EXIST") is None


def test_cancel_subscription_raises_when_missing():
    db = make_db()
    service = BillingService(db=db)

    with pytest.raises(BillingError):
        service.cancel_subscription("sub_missing")


def test_cancel_subscription_calls_stripe_and_updates_row(monkeypatch):
    db = make_db()
    service = BillingService(db=db)

    service.upsert_subscription_from_stripe_object({
        "id": "sub_1",
        "customer": "cus_1",
        "status": "active",
        "current_period_end": None,
        "cancel_at_period_end": False,
        "items": {"data": [{"price": {"id": "price_x"}}]},
        "metadata": {"player_id": "P1", "paying_user_id": "U1"},
    })

    captured = {}

    def fake_modify(subscription_id, **params):
        captured["subscription_id"] = subscription_id
        captured["params"] = params

    monkeypatch.setattr(billing_service.stripe.Subscription, "modify", fake_modify)

    row = service.cancel_subscription("sub_1")

    assert captured["subscription_id"] == "sub_1"
    assert captured["params"] == {"cancel_at_period_end": True}
    assert row.cancel_at_period_end is True


def test_construct_webhook_event_requires_secret(monkeypatch):
    monkeypatch.setattr(billing_service, "get_stripe_webhook_secret", lambda: "")

    db = make_db()
    service = BillingService(db=db)

    with pytest.raises(BillingError):
        service.construct_webhook_event(b"{}", "sig")


def test_construct_webhook_event_wraps_invalid_signature(monkeypatch):
    monkeypatch.setattr(billing_service, "get_stripe_webhook_secret", lambda: "whsec_x")

    def fake_construct_event(payload, sig_header, secret, **kwargs):
        raise billing_service.stripe.SignatureVerificationError("bad sig", sig_header)

    monkeypatch.setattr(
        billing_service.stripe.Webhook, "construct_event", fake_construct_event
    )

    db = make_db()
    service = BillingService(db=db)

    with pytest.raises(BillingError):
        service.construct_webhook_event(b"{}", "bad-signature")


def test_construct_webhook_event_returns_event_on_success(monkeypatch):
    monkeypatch.setattr(billing_service, "get_stripe_webhook_secret", lambda: "whsec_x")

    fake_event = {"type": "customer.subscription.updated"}
    monkeypatch.setattr(
        billing_service.stripe.Webhook,
        "construct_event",
        lambda payload, sig_header, secret, **kwargs: fake_event,
    )

    db = make_db()
    service = BillingService(db=db)

    assert service.construct_webhook_event(b"{}", "good-signature") == fake_event
