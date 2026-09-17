from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.db_models import (
    AuditEventDB,
    GuardianPlayerLinkDB,
    ManualPaymentDB,
    NotificationDB,
    PaymentDB,
    PlayerDB,
    RefundDB,
    SubscriptionDB,
    UserDB,
)
from app.services.auth_service import hash_password, utcnow
from main import CSRF_COOKIE_NAME, app


test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(test_engine, "connect")
def enable_test_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

Base.metadata.create_all(bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(scope="module")
def client():
    db = TestingSessionLocal()
    now = utcnow()
    db.add(UserDB(
        user_id="RF_ADMIN",
        username="rf.admin",
        password_hash=hash_password("RfAdminPassword123!"),
        role="admin",
        active=True,
        created_at=now,
        updated_at=now,
    ))
    db.commit()
    db.close()

    previous_override = app.dependency_overrides.get(get_db)
    previous_session_factory = app.state.auth_session_factory

    app.dependency_overrides[get_db] = override_get_db
    app.state.auth_session_factory = TestingSessionLocal

    test_client = TestClient(app)
    login_response = test_client.post(
        "/auth/login",
        json={"username": "rf.admin", "password": "RfAdminPassword123!"},
    )
    assert login_response.status_code == 200
    test_client.headers.update({
        "X-CSRF-Token": test_client.cookies.get(CSRF_COOKIE_NAME),
    })

    yield test_client

    if previous_override is None:
        app.dependency_overrides.pop(get_db, None)
    else:
        app.dependency_overrides[get_db] = previous_override
    app.state.auth_session_factory = previous_session_factory


def _create_role_client(username, password, role):
    db = TestingSessionLocal()
    now = utcnow()
    db.add(UserDB(
        user_id=f"RF_{username.upper()}",
        username=username,
        password_hash=hash_password(password),
        role=role,
        active=True,
        email=f"{username}@example.com" if role == "guardian" else None,
        created_at=now,
        updated_at=now,
    ))
    db.commit()
    db.close()

    role_client = TestClient(app)
    login_response = role_client.post(
        "/auth/login", json={"username": username, "password": password}
    )
    assert login_response.status_code == 200
    role_client.headers.update({
        "X-CSRF-Token": role_client.cookies.get(CSRF_COOKIE_NAME),
    })
    return role_client


def create_test_player(client, player_id):
    payload = {
        "player_id": player_id,
        "first_name_ar": "لاعب",
        "last_name_ar": "تجربة",
        "first_name_en": "Refund",
        "last_name_en": "Test",
        "date_of_birth": "2015-06-01",
        "sex": "male",
        "physical_profile": {
            "height_cm": 140.0, "weight_kg": 35.0, "dominant_foot": "right",
            "speed": 70.0, "acceleration": 72.0, "agility": 68.0, "stamina": 75.0, "strength": 60.0,
        },
        "technical_profile": {
            "ball_control": 70.0, "dribbling": 72.0, "passing": 68.0, "shooting": 65.0, "finishing": 67.0,
        },
        "mental_profile": {
            "decision_making": 70.0, "concentration": 72.0, "composure": 68.0, "positioning": 71.0,
            "vision": 74.0, "awareness": 70.0, "game_reading": 70.0, "coachability": 70.0,
        },
        "match_performance": {
            "minutes_played": 90, "goals": 1, "assists": 1, "shots": 3, "shots_on_target": 2,
            "passes_attempted": 40, "passes_completed": 34, "tackles": 3, "interceptions": 2, "rating": 8.2,
        },
        "tactical_profile": {
            "positioning_spatial_intelligence": 70.0, "attacking_contribution_in_possession": 68.0,
            "attacking_contribution_off_ball": 72.0, "defensive_tactical_contribution": 69.0,
            "transitions": 71.0, "decision_quality": 70.0, "collective_coordination": 68.0, "set_piece_contribution": 65.0,
        },
        "weak_foot_profile": {
            "weak_foot_usage_pct": 20.0, "weak_foot_passing": 60.0, "weak_foot_receiving": 62.0,
            "weak_foot_dribbling": 58.0, "weak_foot_finishing": 55.0,
        },
    }
    response = client.post("/players", json=payload)
    assert response.status_code == 201


def seed_paid_stripe_payment(player_id, invoice_id, subscription_id, amount=10000):
    db = TestingSessionLocal()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(SubscriptionDB(
        stripe_subscription_id=subscription_id,
        player_id=player_id,
        paying_user_id="RF_ADMIN",
        stripe_customer_id=f"cus_{subscription_id}",
        stripe_price_id=f"price_{subscription_id}",
        status="active",
        current_period_end=now,
        cancel_at_period_end=False,
        created_at=now,
        updated_at=now,
    ))
    db.commit()
    db.add(PaymentDB(
        stripe_invoice_id=invoice_id,
        stripe_subscription_id=subscription_id,
        player_id=player_id,
        amount=amount,
        currency="usd",
        status="paid",
        created_at=now,
    ))
    db.commit()
    db.close()


def link_guardian(guardian_user_id, player_id):
    db = TestingSessionLocal()
    now = utcnow()
    db.add(GuardianPlayerLinkDB(
        guardian_user_id=guardian_user_id,
        player_id=player_id,
        created_at=now,
        created_by_user_id="RF_ADMIN",
    ))
    db.commit()
    db.close()


def mock_stripe_refund(monkeypatch, refund_id="re_test", payment_intent="pi_test"):
    from app.services import refund_service

    monkeypatch.setattr(
        refund_service.stripe.Invoice, "retrieve", lambda invoice_id: {"payment_intent": payment_intent}
    )
    monkeypatch.setattr(
        refund_service.stripe.Refund,
        "create",
        lambda **kwargs: {"id": refund_id, "status": "succeeded", **kwargs},
    )


# ---------------------------------------------------------------------------
# Full / partial Stripe refunds
# ---------------------------------------------------------------------------

def test_admin_can_issue_full_stripe_refund(client, monkeypatch):
    create_test_player(client, "RF_P_FULL")
    seed_paid_stripe_payment("RF_P_FULL", "in_full_test", "sub_full_test", amount=10000)
    mock_stripe_refund(monkeypatch, refund_id="re_full_test")

    response = client.post(
        "/billing/payments/in_full_test/refund",
        json={
            "refund_type": "full",
            "reason": "Guardian requested cancellation",
            "idempotency_key": "idem-full-001",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["amount_cents"] == 10000
    assert body["refund_source"] == "stripe"
    assert body["stripe_refund_id"] == "re_full_test"
    assert body["refund_type"] == "full"

    history = client.get("/billing/payments/RF_P_FULL")
    payment = history.json()["payments"][0]
    assert payment["status"] == "REFUNDED"
    assert payment["refunded_amount_cents"] == 10000
    assert payment["net_amount_cents"] == 0
    assert payment["remaining_refundable_cents"] == 0


def test_admin_can_issue_partial_stripe_refund(client, monkeypatch):
    create_test_player(client, "RF_P_PARTIAL")
    seed_paid_stripe_payment("RF_P_PARTIAL", "in_partial_test", "sub_partial_test", amount=10000)
    mock_stripe_refund(monkeypatch, refund_id="re_partial_test")

    response = client.post(
        "/billing/payments/in_partial_test/refund",
        json={
            "refund_type": "partial",
            "amount_cents": 3000,
            "reason": "Missed two sessions",
            "idempotency_key": "idem-partial-001",
        },
    )
    assert response.status_code == 200
    assert response.json()["amount_cents"] == 3000

    history = client.get("/billing/payments/RF_P_PARTIAL")
    payment = history.json()["payments"][0]
    assert payment["status"] == "PARTIALLY_REFUNDED"
    assert payment["amount"] == 10000
    assert payment["refunded_amount_cents"] == 3000
    assert payment["net_amount_cents"] == 7000
    assert payment["remaining_refundable_cents"] == 7000


def test_multiple_partial_refunds_accumulate_and_cap_at_original_amount(client, monkeypatch):
    create_test_player(client, "RF_P_MULTI")
    seed_paid_stripe_payment("RF_P_MULTI", "in_multi_test", "sub_multi_test", amount=10000)
    mock_stripe_refund(monkeypatch, refund_id="re_multi_1")

    first = client.post(
        "/billing/payments/in_multi_test/refund",
        json={"refund_type": "partial", "amount_cents": 4000, "reason": "First refund", "idempotency_key": "idem-multi-1"},
    )
    assert first.status_code == 200

    mock_stripe_refund(monkeypatch, refund_id="re_multi_2")
    second = client.post(
        "/billing/payments/in_multi_test/refund",
        json={"refund_type": "partial", "amount_cents": 3000, "reason": "Second refund", "idempotency_key": "idem-multi-2"},
    )
    assert second.status_code == 200

    history = client.get("/billing/payments/RF_P_MULTI")
    payment = history.json()["payments"][0]
    assert payment["refunded_amount_cents"] == 7000
    assert payment["remaining_refundable_cents"] == 3000
    assert payment["status"] == "PARTIALLY_REFUNDED"
    assert len(payment["refund_history"]) == 2

    # A third refund for the remaining balance completes it.
    mock_stripe_refund(monkeypatch, refund_id="re_multi_3")
    third = client.post(
        "/billing/payments/in_multi_test/refund",
        json={"refund_type": "full", "reason": "Final refund", "idempotency_key": "idem-multi-3"},
    )
    assert third.status_code == 200
    assert third.json()["amount_cents"] == 3000

    final = client.get("/billing/payments/RF_P_MULTI").json()["payments"][0]
    assert final["status"] == "REFUNDED"
    assert final["refunded_amount_cents"] == 10000


def test_refund_amount_exceeding_remaining_balance_is_rejected(client, monkeypatch):
    create_test_player(client, "RF_P_OVER")
    seed_paid_stripe_payment("RF_P_OVER", "in_over_test", "sub_over_test", amount=10000)
    mock_stripe_refund(monkeypatch)

    response = client.post(
        "/billing/payments/in_over_test/refund",
        json={"refund_type": "partial", "amount_cents": 15000, "reason": "Too much", "idempotency_key": "idem-over-1"},
    )
    assert response.status_code == 400
    assert "exceeds" in response.json()["detail"]

    # Nothing was refunded — the payment is untouched.
    history = client.get("/billing/payments/RF_P_OVER").json()["payments"][0]
    assert history["status"] == "PAID"
    assert history["refunded_amount_cents"] == 0


def test_duplicate_refund_request_with_same_idempotency_key_is_a_no_op(client, monkeypatch):
    create_test_player(client, "RF_P_DUPE")
    seed_paid_stripe_payment("RF_P_DUPE", "in_dupe_test", "sub_dupe_test", amount=10000)

    call_count = {"n": 0}

    from app.services import refund_service

    def counting_refund_create(**kwargs):
        call_count["n"] += 1
        return {"id": "re_dupe_test", "status": "succeeded"}

    monkeypatch.setattr(
        refund_service.stripe.Invoice, "retrieve", lambda invoice_id: {"payment_intent": "pi_dupe"}
    )
    monkeypatch.setattr(refund_service.stripe.Refund, "create", counting_refund_create)

    payload = {
        "refund_type": "full",
        "reason": "Double click test",
        "idempotency_key": "idem-dupe-shared",
    }
    first = client.post("/billing/payments/in_dupe_test/refund", json=payload)
    second = client.post("/billing/payments/in_dupe_test/refund", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["refund_id"] == second.json()["refund_id"]
    # Stripe was only ever actually called once — the second request was
    # answered from our own idempotency ledger, not a second live refund.
    assert call_count["n"] == 1

    db = TestingSessionLocal()
    refund_rows = db.query(RefundDB).filter(RefundDB.payment_id == "in_dupe_test").all()
    assert len(refund_rows) == 1
    db.close()


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------

def test_unauthorized_refund_attempt_is_denied_for_coach(client, monkeypatch):
    create_test_player(client, "RF_P_COACH")
    seed_paid_stripe_payment("RF_P_COACH", "in_coach_test", "sub_coach_test")
    mock_stripe_refund(monkeypatch)

    coach_client = _create_role_client("rf.coach1", "CoachPassword123!", "coach")
    response = coach_client.post(
        "/billing/payments/in_coach_test/refund",
        json={"refund_type": "full", "reason": "Not allowed", "idempotency_key": "idem-coach-1"},
    )
    assert response.status_code == 403


def test_refund_of_another_familys_payment_is_denied_for_guardian(client, monkeypatch):
    create_test_player(client, "RF_P_OTHERFAM")
    seed_paid_stripe_payment("RF_P_OTHERFAM", "in_otherfam_test", "sub_otherfam_test")
    mock_stripe_refund(monkeypatch)

    guardian_client = _create_role_client("rf.guardian.outsider", "GuardianPassword123!", "guardian")
    response = guardian_client.post(
        "/billing/payments/in_otherfam_test/refund",
        json={"refund_type": "full", "reason": "Not my child, not my call", "idempotency_key": "idem-otherfam-1"},
    )
    assert response.status_code == 403


def test_unauthenticated_refund_attempt_is_denied(client, monkeypatch):
    create_test_player(client, "RF_P_ANON")
    seed_paid_stripe_payment("RF_P_ANON", "in_anon_test", "sub_anon_test")
    mock_stripe_refund(monkeypatch)

    anonymous_client = TestClient(app)
    response = anonymous_client.post(
        "/billing/payments/in_anon_test/refund",
        json={"refund_type": "full", "reason": "No session", "idempotency_key": "idem-anon-1"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Manual (cash / check) refunds — never call Stripe
# ---------------------------------------------------------------------------

def test_admin_can_issue_manual_cash_refund(client):
    create_test_player(client, "RF_P_CASH")
    create_response = client.post(
        "/billing/manual-payments/RF_P_CASH",
        json={"amount_cents": 8000, "currency": "usd", "method": "cash", "payment_date": "2026-09-01"},
    )
    manual_payment_id = create_response.json()["manual_payment_id"]

    response = client.post(
        f"/billing/manual-payments/{manual_payment_id}/refund",
        json={"refund_type": "full", "reason": "Refunded in person", "idempotency_key": "idem-cash-1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["refund_source"] == "manual"
    assert body["stripe_refund_id"] is None
    assert body["amount_cents"] == 8000

    history = client.get("/billing/manual-payments/RF_P_CASH").json()["payments"][0]
    assert history["status"] == "REFUNDED"
    assert history["refunded_amount_cents"] == 8000


def test_admin_can_issue_manual_check_refund_partial(client):
    create_test_player(client, "RF_P_CHECK")
    create_response = client.post(
        "/billing/manual-payments/RF_P_CHECK",
        json={"amount_cents": 12000, "currency": "usd", "method": "check", "payment_date": "2026-09-01"},
    )
    manual_payment_id = create_response.json()["manual_payment_id"]

    response = client.post(
        f"/billing/manual-payments/{manual_payment_id}/refund",
        json={
            "refund_type": "partial",
            "amount_cents": 5000,
            "reason": "Partial credit issued by check",
            "idempotency_key": "idem-check-1",
        },
    )
    assert response.status_code == 200
    assert response.json()["refund_source"] == "manual"

    history = client.get("/billing/manual-payments/RF_P_CHECK").json()["payments"][0]
    assert history["status"] == "PARTIALLY_REFUNDED"
    assert history["refunded_amount_cents"] == 5000
    assert history["net_amount_cents"] == 7000


def test_manual_refund_never_calls_stripe(client, monkeypatch):
    from app.services import refund_service

    def fail_if_called(*args, **kwargs):
        raise AssertionError("Manual refunds must never call Stripe")

    monkeypatch.setattr(refund_service.stripe.Refund, "create", fail_if_called)
    monkeypatch.setattr(refund_service.stripe.Invoice, "retrieve", fail_if_called)

    create_test_player(client, "RF_P_NOSTRIPE")
    create_response = client.post(
        "/billing/manual-payments/RF_P_NOSTRIPE",
        json={"amount_cents": 4000, "currency": "usd", "method": "bank_transfer", "payment_date": "2026-09-01"},
    )
    manual_payment_id = create_response.json()["manual_payment_id"]

    response = client.post(
        f"/billing/manual-payments/{manual_payment_id}/refund",
        json={"refund_type": "full", "reason": "No stripe call expected", "idempotency_key": "idem-nostripe-1"},
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Stripe API failure
# ---------------------------------------------------------------------------

def test_refund_api_failure_is_surfaced_clearly_and_nothing_is_recorded(client, monkeypatch):
    from app.services import refund_service

    create_test_player(client, "RF_P_APIFAIL")
    seed_paid_stripe_payment("RF_P_APIFAIL", "in_apifail_test", "sub_apifail_test")

    monkeypatch.setattr(
        refund_service.stripe.Invoice, "retrieve", lambda invoice_id: {"payment_intent": "pi_apifail"}
    )

    def fake_failure(**kwargs):
        raise refund_service.stripe.InvalidRequestError("Charge already refunded", param=None)

    monkeypatch.setattr(refund_service.stripe.Refund, "create", fake_failure)

    response = client.post(
        "/billing/payments/in_apifail_test/refund",
        json={"refund_type": "full", "reason": "Should fail", "idempotency_key": "idem-apifail-1"},
    )
    assert response.status_code == 400
    assert "Stripe rejected" in response.json()["detail"]

    history = client.get("/billing/payments/RF_P_APIFAIL").json()["payments"][0]
    assert history["status"] == "PAID"
    assert history["refunded_amount_cents"] == 0

    db = TestingSessionLocal()
    assert db.query(RefundDB).filter(RefundDB.payment_id == "in_apifail_test").count() == 0
    db.close()


# ---------------------------------------------------------------------------
# Stripe webhook reconciliation — a refund processed directly in the
# Stripe Dashboard, outside this app entirely, must still show up here.
# ---------------------------------------------------------------------------

def test_stripe_webhook_reconciles_a_refund_made_outside_the_app(client, monkeypatch):
    from app.routers import billing as billing_router_module

    create_test_player(client, "RF_P_EXTERNAL")
    seed_paid_stripe_payment("RF_P_EXTERNAL", "in_external_test", "sub_external_test", amount=10000)

    fake_charge_refunded_event = {
        "id": "evt_external_refund_test",
        "type": "charge.refunded",
        "data": {
            "object": {
                "id": "ch_external_test",
                "invoice": "in_external_test",
                "refunds": {
                    "data": [
                        {"id": "re_external_test", "amount": 4000, "currency": "usd", "status": "succeeded"},
                    ]
                },
            }
        },
    }
    monkeypatch.setattr(
        billing_router_module.BillingService,
        "construct_webhook_event",
        lambda self, payload, signature_header: fake_charge_refunded_event,
    )

    response = client.post(
        "/billing/webhook",
        content=b"{}",
        headers={"stripe-signature": "t=1,v1=whatever"},
    )
    assert response.status_code == 200

    history = client.get("/billing/payments/RF_P_EXTERNAL").json()["payments"][0]
    assert history["refunded_amount_cents"] == 4000
    assert history["status"] == "PARTIALLY_REFUNDED"
    refund_entry = history["refund_history"][0]
    assert refund_entry["refund_source"] == "external"
    assert refund_entry["stripe_refund_id"] == "re_external_test"

    # A LATER charge.refunded delivery (its own distinct event id, as a
    # real second Stripe delivery would have) still reports the SAME
    # refund in its cumulative refunds.data list — it must not be
    # double-counted, since our dedup keys on stripe_refund_id, not the
    # webhook event id.
    fake_charge_refunded_event_2 = {
        "id": "evt_external_refund_test_2",
        "type": "charge.refunded",
        "data": {"object": fake_charge_refunded_event["data"]["object"]},
    }
    monkeypatch.setattr(
        billing_router_module.BillingService,
        "construct_webhook_event",
        lambda self, payload, signature_header: fake_charge_refunded_event_2,
    )
    response2 = client.post(
        "/billing/webhook",
        content=b"{}",
        headers={"stripe-signature": "t=1,v1=whatever"},
    )
    assert response2.status_code == 200
    history2 = client.get("/billing/payments/RF_P_EXTERNAL").json()["payments"][0]
    assert history2["refunded_amount_cents"] == 4000
    assert len(history2["refund_history"]) == 1


# ---------------------------------------------------------------------------
# Financial reporting
# ---------------------------------------------------------------------------

def test_financial_report_shows_gross_refunds_and_net_after_a_refund(client, monkeypatch):
    before = client.get("/billing/admin/report").json()

    create_test_player(client, "RF_P_REPORT")
    seed_paid_stripe_payment("RF_P_REPORT", "in_report_test", "sub_report_test", amount=10000)
    mock_stripe_refund(monkeypatch, refund_id="re_report_test")

    refund_response = client.post(
        "/billing/payments/in_report_test/refund",
        json={"refund_type": "partial", "amount_cents": 2500, "reason": "Reporting check", "idempotency_key": "idem-report-1"},
    )
    assert refund_response.status_code == 200

    after = client.get("/billing/admin/report").json()

    assert after["refunds_cents"] == before["refunds_cents"] + 2500
    assert after["gross_payments_cents"] == before["gross_payments_cents"] + 10000
    assert after["net_collected_revenue_cents"] == after["gross_payments_cents"] - after["refunds_cents"]


def test_admin_transactions_endpoint_filters_by_status(client, monkeypatch):
    create_test_player(client, "RF_P_FILTER")
    seed_paid_stripe_payment("RF_P_FILTER", "in_filter_test", "sub_filter_test", amount=10000)
    mock_stripe_refund(monkeypatch, refund_id="re_filter_test")

    client.post(
        "/billing/payments/in_filter_test/refund",
        json={"refund_type": "full", "reason": "Filter check", "idempotency_key": "idem-filter-1"},
    )

    refunded = client.get("/billing/admin/transactions", params={"status": "refunded"})
    assert refunded.status_code == 200
    ids = [t["transaction_id"] for t in refunded.json()["transactions"]]
    assert "in_filter_test" in ids

    paid_only = client.get("/billing/admin/transactions", params={"status": "paid"})
    paid_ids = [t["transaction_id"] for t in paid_only.json()["transactions"]]
    assert "in_filter_test" not in paid_ids


def test_transactions_endpoint_requires_admin(client):
    coach_client = _create_role_client("rf.coach.tx", "CoachPassword123!", "coach")
    response = coach_client.get("/billing/admin/transactions")
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

def test_refund_creates_a_permanent_audit_log_entry(client, monkeypatch):
    create_test_player(client, "RF_P_AUDIT")
    seed_paid_stripe_payment("RF_P_AUDIT", "in_audit_test", "sub_audit_test", amount=10000)
    mock_stripe_refund(monkeypatch, refund_id="re_audit_test")

    response = client.post(
        "/billing/payments/in_audit_test/refund",
        json={
            "refund_type": "partial",
            "amount_cents": 1500,
            "reason": "Audit trail check",
            "internal_note": "Verified with guardian by phone",
            "idempotency_key": "idem-audit-1",
        },
    )
    assert response.status_code == 200
    refund_id = response.json()["refund_id"]

    db = TestingSessionLocal()
    events = (
        db.query(AuditEventDB)
        .filter(AuditEventDB.action == "payment_refunded", AuditEventDB.resource_id == "in_audit_test")
        .all()
    )
    assert len(events) == 1
    details = events[0].details
    assert details["refund_id"] == refund_id
    assert details["player_id"] == "RF_P_AUDIT"
    assert details["original_amount_cents"] == 10000
    assert details["refund_amount_cents"] == 1500
    assert details["reason"] == "Audit trail check"
    assert details["stripe_refund_id"] == "re_audit_test"
    assert details["previous_status"] == "PAID"
    assert details["new_status"] == "PARTIALLY_REFUNDED"
    assert events[0].actor_user_id == "RF_ADMIN"
    db.close()


# ---------------------------------------------------------------------------
# Parent visibility
# ---------------------------------------------------------------------------

def test_parent_can_view_refund_history_for_own_payment_but_not_internal_note(client, monkeypatch):
    create_test_player(client, "RF_P_PARENTVIEW")
    seed_paid_stripe_payment("RF_P_PARENTVIEW", "in_parentview_test", "sub_parentview_test", amount=10000)
    mock_stripe_refund(monkeypatch, refund_id="re_parentview_test")

    client.post(
        "/billing/payments/in_parentview_test/refund",
        json={
            "refund_type": "partial",
            "amount_cents": 2000,
            "reason": "Visible to parent",
            "internal_note": "Should stay internal",
            "idempotency_key": "idem-parentview-1",
        },
    )

    guardian_client = _create_role_client("rf.guardian.parentview", "GuardianPassword123!", "guardian")
    link_guardian("RF_RF.GUARDIAN.PARENTVIEW", "RF_P_PARENTVIEW")

    response = guardian_client.get("/billing/payments/RF_P_PARENTVIEW")
    assert response.status_code == 200
    payment = response.json()["payments"][0]
    assert payment["status"] == "PARTIALLY_REFUNDED"
    assert payment["refunded_amount_cents"] == 2000
    refund_entry = payment["refund_history"][0]
    assert refund_entry["amount_cents"] == 2000
    assert refund_entry["reason"] == "Visible to parent"
    assert "internal_note" not in refund_entry
    assert "created_by_user_id" not in refund_entry


def test_guardian_notified_after_successful_refund(client, monkeypatch):
    create_test_player(client, "RF_P_NOTIFY")
    seed_paid_stripe_payment("RF_P_NOTIFY", "in_notify_test", "sub_notify_test", amount=10000)
    mock_stripe_refund(monkeypatch, refund_id="re_notify_test")

    response = client.post(
        "/billing/payments/in_notify_test/refund",
        json={"refund_type": "full", "reason": "Notify guardian", "idempotency_key": "idem-notify-1"},
    )
    assert response.status_code == 200

    db = TestingSessionLocal()
    notification = (
        db.query(NotificationDB)
        .filter(NotificationDB.user_id == "RF_ADMIN", NotificationDB.type == "refund_issued")
        .order_by(NotificationDB.created_at.desc())
        .first()
    )
    assert notification is not None
    assert "100.00" in notification.body
    assert "in_notify_test" in notification.body
    db.close()


# ---------------------------------------------------------------------------
# Membership behavior — refund and membership status are separate
# ---------------------------------------------------------------------------

def test_membership_status_unchanged_after_refund(client, monkeypatch):
    create_test_player(client, "RF_P_MEMBERSHIP")
    seed_paid_stripe_payment("RF_P_MEMBERSHIP", "in_membership_test", "sub_membership_test", amount=10000)
    mock_stripe_refund(monkeypatch, refund_id="re_membership_test")

    db = TestingSessionLocal()
    subscription = db.get(SubscriptionDB, "sub_membership_test")
    status_before = subscription.status
    cancel_before = subscription.cancel_at_period_end
    db.close()

    response = client.post(
        "/billing/payments/in_membership_test/refund",
        json={"refund_type": "full", "reason": "Membership must stay untouched", "idempotency_key": "idem-membership-1"},
    )
    assert response.status_code == 200

    db = TestingSessionLocal()
    subscription = db.get(SubscriptionDB, "sub_membership_test")
    assert subscription.status == status_before
    assert subscription.cancel_at_period_end == cancel_before
    db.close()

    # The admin can still separately pause/cancel afterward — refunding
    # never does it automatically, but the action remains available.
    from app.services import billing_service

    def fake_subscription_modify(subscription_id, **kwargs):
        return {
            "id": subscription_id,
            "customer": "cus_membership_test",
            "status": "active",
            "current_period_end": 1893456000,
            "cancel_at_period_end": False,
            "items": {"data": [{"price": {"id": "price_sub_membership_test"}}]},
            "metadata": {"player_id": "RF_P_MEMBERSHIP", "paying_user_id": "RF_ADMIN"},
        }

    monkeypatch.setattr(billing_service.stripe.Subscription, "modify", fake_subscription_modify)

    pause_response = client.post("/billing/subscriptions/RF_P_MEMBERSHIP/pause")
    assert pause_response.status_code == 200
