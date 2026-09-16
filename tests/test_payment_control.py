from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.db_models import (
    AuditEventDB,
    GuardianPlayerLinkDB,
    MembershipPlanDB,
    PaymentDB,
    PlayerDB,
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
        user_id="PC_ADMIN",
        username="pc.admin",
        password_hash=hash_password("PcAdminPassword123!"),
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
        json={"username": "pc.admin", "password": "PcAdminPassword123!"},
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


@pytest.fixture
def anonymous_client(client):
    return TestClient(app)


def _create_role_client(username, password, role):
    db = TestingSessionLocal()
    now = utcnow()
    db.add(UserDB(
        user_id=f"PC_{username.upper()}",
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
        "first_name_en": "Payment",
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


def create_membership_plan(client, plan_suffix="1"):
    response = client.post(
        "/billing/membership-plans",
        json={
            "name": f"Monthly Plan {plan_suffix}",
            "stripe_price_id": f"price_test_{plan_suffix}",
            "amount_cents": 12000,
            "currency": "usd",
            "billing_interval": "month",
        },
    )
    assert response.status_code == 201
    return response.json()["plan_id"]


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

def test_admin_can_view_payment_control_summary_and_rows(client):
    summary_response = client.get("/billing/admin/summary")
    assert summary_response.status_code == 200
    assert "active_memberships" in summary_response.json()

    rows_response = client.get("/billing/admin/players")
    assert rows_response.status_code == 200
    assert "rows" in rows_response.json()


def test_guardian_cannot_access_payment_control(client):
    guardian_client = _create_role_client("pc.guardian1", "GuardianPassword123!", "guardian")

    assert guardian_client.get("/billing/admin/summary").status_code == 403
    assert guardian_client.get("/billing/admin/players").status_code == 403
    assert guardian_client.get("/payment-control").status_code == 403


def test_coach_cannot_access_payment_control(client):
    coach_client = _create_role_client("pc.coach1", "CoachPassword123!", "coach")

    assert coach_client.get("/billing/admin/summary").status_code == 403
    assert coach_client.get("/billing/admin/players").status_code == 403


def test_unauthenticated_cannot_access_payment_control(anonymous_client):
    assert anonymous_client.get("/billing/admin/summary").status_code == 401
    assert anonymous_client.get("/billing/admin/players").status_code == 401


# ---------------------------------------------------------------------------
# Membership plans
# ---------------------------------------------------------------------------

def test_admin_can_create_list_and_update_membership_plan(client):
    plan_id = create_membership_plan(client, "crud")

    list_response = client.get("/billing/membership-plans")
    assert list_response.status_code == 200
    plan_ids = [plan["plan_id"] for plan in list_response.json()["plans"]]
    assert plan_id in plan_ids

    update_response = client.patch(
        f"/billing/membership-plans/{plan_id}",
        json={"active": False},
    )
    assert update_response.status_code == 200
    assert update_response.json()["active"] is False


def test_creating_plan_for_existing_stripe_price_is_rejected(client):
    plan_id = create_membership_plan(client, "dup")

    duplicate_response = client.post(
        "/billing/membership-plans",
        json={
            "name": "Duplicate",
            "stripe_price_id": "price_test_dup",
            "amount_cents": 5000,
            "currency": "usd",
            "billing_interval": "month",
        },
    )
    assert duplicate_response.status_code == 400
    assert plan_id


def test_non_admin_cannot_manage_membership_plans(client):
    coach_client = _create_role_client("pc.coach2", "CoachPassword123!", "coach")

    create_response = coach_client.post(
        "/billing/membership-plans",
        json={
            "name": "Should Fail",
            "stripe_price_id": "price_should_fail",
            "amount_cents": 5000,
            "currency": "usd",
            "billing_interval": "month",
        },
    )
    assert create_response.status_code == 403


def test_admin_can_assign_membership_plan_to_player(client):
    create_test_player(client, "PC_P_ASSIGN")
    plan_id = create_membership_plan(client, "assign")

    response = client.post(
        "/billing/subscriptions/PC_P_ASSIGN/membership",
        json={"plan_id": plan_id},
    )
    assert response.status_code == 200
    assert response.json()["plan_id"] == plan_id

    rows = client.get("/billing/admin/players").json()["rows"]
    row = next(r for r in rows if r["player_id"] == "PC_P_ASSIGN")
    assert row["has_membership_assigned"] is True
    assert row["plan_name"] == "Monthly Plan assign"


def test_billing_status_reflects_assigned_plan_before_any_payment(client):
    create_test_player(client, "PC_P_PAYMENT_DUE")
    plan_id = create_membership_plan(client, "due")

    client.post(
        "/billing/subscriptions/PC_P_PAYMENT_DUE/membership",
        json={"plan_id": plan_id},
    )

    status_response = client.get("/billing/status/PC_P_PAYMENT_DUE")
    assert status_response.status_code == 200
    body = status_response.json()
    assert body["subscription"] is None
    assert body["membership"]["plan_id"] == plan_id
    assert body["membership"]["amount_cents"] == 12000
    assert body["membership"]["name"] == "Monthly Plan due"


def test_billing_status_has_no_membership_when_none_assigned(client):
    create_test_player(client, "PC_P_NO_MEMBERSHIP")

    status_response = client.get("/billing/status/PC_P_NO_MEMBERSHIP")
    assert status_response.status_code == 200
    assert status_response.json()["membership"] is None


def test_assigning_inactive_or_unknown_plan_is_rejected(client):
    create_test_player(client, "PC_P_BADPLAN")

    response = client.post(
        "/billing/subscriptions/PC_P_BADPLAN/membership",
        json={"plan_id": "MPLAN_DOES_NOT_EXIST"},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Manual payments — auditable, source explicitly MANUAL
# ---------------------------------------------------------------------------

def test_admin_can_record_manual_payment_and_it_is_audited(client):
    create_test_player(client, "PC_P_MANUAL")

    response = client.post(
        "/billing/manual-payments/PC_P_MANUAL",
        json={
            "amount_cents": 12000,
            "currency": "usd",
            "method": "cash",
            "payment_date": "2026-09-01",
            "note": "Paid in person at the office",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["source"] == "manual"
    assert body["amount_cents"] == 12000
    assert body["recorded_by_user_id"] == "PC_ADMIN"

    db = TestingSessionLocal()
    audit_events = (
        db.query(AuditEventDB)
        .filter(AuditEventDB.action == "manual_payment_recorded", AuditEventDB.resource_id == "PC_P_MANUAL")
        .all()
    )
    assert len(audit_events) == 1
    assert audit_events[0].details["amount_cents"] == 12000
    assert audit_events[0].actor_user_id == "PC_ADMIN"
    db.close()


def test_non_admin_cannot_record_manual_payment(client):
    create_test_player(client, "PC_P_MANUAL_DENY")
    coach_client = _create_role_client("pc.coach3", "CoachPassword123!", "coach")

    response = coach_client.post(
        "/billing/manual-payments/PC_P_MANUAL_DENY",
        json={
            "amount_cents": 5000,
            "currency": "usd",
            "method": "cash",
            "payment_date": "2026-09-01",
        },
    )
    assert response.status_code == 403


def test_guardian_sees_own_childs_manual_payments_not_another_familys(client):
    create_test_player(client, "PC_P_FAMILY_A")
    create_test_player(client, "PC_P_FAMILY_B")

    client.post(
        "/billing/manual-payments/PC_P_FAMILY_A",
        json={"amount_cents": 9000, "currency": "usd", "method": "cash", "payment_date": "2026-09-01"},
    )
    client.post(
        "/billing/manual-payments/PC_P_FAMILY_B",
        json={"amount_cents": 9000, "currency": "usd", "method": "cash", "payment_date": "2026-09-01"},
    )

    guardian_a = _create_role_client("pc.guardian.a", "GuardianPassword123!", "guardian")
    db = TestingSessionLocal()
    now = utcnow()
    db.add(GuardianPlayerLinkDB(
        guardian_user_id="PC_PC.GUARDIAN.A",
        player_id="PC_P_FAMILY_A",
        created_at=now,
        created_by_user_id="PC_ADMIN",
    ))
    db.commit()
    db.close()

    own_child_response = guardian_a.get("/billing/manual-payments/PC_P_FAMILY_A")
    assert own_child_response.status_code == 200
    assert len(own_child_response.json()["payments"]) == 1

    other_family_response = guardian_a.get("/billing/manual-payments/PC_P_FAMILY_B")
    assert other_family_response.status_code == 404


# ---------------------------------------------------------------------------
# Pause / resume / refund — Stripe-backed, mocked
# ---------------------------------------------------------------------------

def test_admin_can_pause_and_resume_a_subscription(client, monkeypatch):
    from app.services import billing_service

    create_test_player(client, "PC_P_PAUSE")
    db = TestingSessionLocal()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(SubscriptionDB(
        stripe_subscription_id="sub_pause_test",
        player_id="PC_P_PAUSE",
        paying_user_id="PC_ADMIN",
        stripe_customer_id="cus_pause_test",
        stripe_price_id="price_test_pause",
        status="active",
        current_period_end=now,
        cancel_at_period_end=False,
        created_at=now,
        updated_at=now,
    ))
    db.commit()
    db.close()

    def fake_stripe_object(subscription_id, **kwargs):
        return {
            "id": subscription_id,
            "customer": "cus_pause_test",
            "status": "active",
            "current_period_end": 1893456000,
            "cancel_at_period_end": False,
            "items": {"data": [{"price": {"id": "price_test_pause"}}]},
            "metadata": {"player_id": "PC_P_PAUSE", "paying_user_id": "PC_ADMIN"},
        }

    monkeypatch.setattr(billing_service.stripe.Subscription, "modify", fake_stripe_object)

    pause_response = client.post("/billing/subscriptions/PC_P_PAUSE/pause")
    assert pause_response.status_code == 200

    resume_response = client.post("/billing/subscriptions/PC_P_PAUSE/resume")
    assert resume_response.status_code == 200


def test_non_admin_cannot_pause_a_subscription(client):
    coach_client = _create_role_client("pc.coach4", "CoachPassword123!", "coach")
    response = coach_client.post("/billing/subscriptions/PC_P_PAUSE/pause")
    assert response.status_code == 403


def test_admin_can_refund_a_paid_payment(client, monkeypatch):
    from app.services import billing_service

    create_test_player(client, "PC_P_REFUND")
    db = TestingSessionLocal()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(SubscriptionDB(
        stripe_subscription_id="sub_refund_test",
        player_id="PC_P_REFUND",
        paying_user_id="PC_ADMIN",
        stripe_customer_id="cus_refund_test",
        stripe_price_id="price_test_refund",
        status="active",
        current_period_end=now,
        cancel_at_period_end=False,
        created_at=now,
        updated_at=now,
    ))
    db.commit()
    db.add(PaymentDB(
        stripe_invoice_id="in_refund_test",
        stripe_subscription_id="sub_refund_test",
        player_id="PC_P_REFUND",
        amount=12000,
        currency="usd",
        status="paid",
        created_at=now,
    ))
    db.commit()
    db.close()

    monkeypatch.setattr(
        billing_service.stripe.Invoice,
        "retrieve",
        lambda invoice_id: {"payment_intent": "pi_refund_test"},
    )
    monkeypatch.setattr(
        billing_service.stripe.Refund,
        "create",
        lambda **kwargs: {"id": "re_refund_test", "status": "succeeded"},
    )

    response = client.post(
        "/billing/payments/in_refund_test/refund",
        json={"reason": "Guardian requested a refund"},
    )
    assert response.status_code == 200
    assert response.json()["refund_id"] == "re_refund_test"

    db = TestingSessionLocal()
    audit_events = (
        db.query(AuditEventDB)
        .filter(AuditEventDB.action == "payment_refunded", AuditEventDB.resource_id == "in_refund_test")
        .all()
    )
    assert len(audit_events) == 1
    db.close()


def test_refund_requires_admin(client):
    coach_client = _create_role_client("pc.coach5", "CoachPassword123!", "coach")
    response = coach_client.post(
        "/billing/payments/in_refund_test/refund",
        json={},
    )
    assert response.status_code == 403


def test_refund_rejects_unpaid_invoice(client):
    create_test_player(client, "PC_P_UNPAID")
    db = TestingSessionLocal()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(SubscriptionDB(
        stripe_subscription_id="sub_unpaid_test",
        player_id="PC_P_UNPAID",
        paying_user_id="PC_ADMIN",
        stripe_customer_id="cus_unpaid_test",
        stripe_price_id="price_test_unpaid",
        status="active",
        current_period_end=now,
        cancel_at_period_end=False,
        created_at=now,
        updated_at=now,
    ))
    db.commit()
    db.add(PaymentDB(
        stripe_invoice_id="in_unpaid_test",
        stripe_subscription_id="sub_unpaid_test",
        player_id="PC_P_UNPAID",
        amount=12000,
        currency="usd",
        status="failed",
        created_at=now,
    ))
    db.commit()
    db.close()

    response = client.post("/billing/payments/in_unpaid_test/refund", json={})
    assert response.status_code == 404
