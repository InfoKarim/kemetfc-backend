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


def test_creating_plan_without_stripe_price_id_creates_one_via_stripe(client, monkeypatch):
    from app.services import billing_service

    monkeypatch.setattr(billing_service, "get_stripe_secret_key", lambda: "sk_test_x")

    captured = {}

    def fake_price_create(**params):
        captured.update(params)
        return {"id": "price_auto_created"}

    monkeypatch.setattr(billing_service.stripe.Price, "create", fake_price_create)

    response = client.post(
        "/billing/membership-plans",
        json={
            "name": "Auto Priced Plan",
            "amount_cents": 9900,
            "currency": "usd",
            "billing_interval": "month",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["stripe_price_id"] == "price_auto_created"
    assert captured["unit_amount"] == 9900
    assert captured["recurring"] == {"interval": "month"}
    assert captured["product_data"] == {"name": "Auto Priced Plan"}


def test_creating_plan_without_stripe_price_id_requires_billing_configured(client, monkeypatch):
    from app.services import billing_service

    monkeypatch.setattr(billing_service, "get_stripe_secret_key", lambda: "")

    response = client.post(
        "/billing/membership-plans",
        json={
            "name": "Should Fail",
            "amount_cents": 9900,
            "currency": "usd",
            "billing_interval": "month",
        },
    )
    assert response.status_code == 400


def test_creating_plan_surfaces_stripe_error_clearly_instead_of_crashing(client, monkeypatch):
    from app.services import billing_service

    monkeypatch.setattr(billing_service, "get_stripe_secret_key", lambda: "sk_test_bad")

    def fake_price_create(**kwargs):
        raise billing_service.stripe.AuthenticationError("Invalid API Key provided")

    monkeypatch.setattr(billing_service.stripe.Price, "create", fake_price_create)

    response = client.post(
        "/billing/membership-plans",
        json={
            "name": "Should Fail Clearly",
            "amount_cents": 9900,
            "currency": "usd",
            "billing_interval": "month",
        },
    )
    assert response.status_code == 400
    assert "Stripe rejected" in response.json()["detail"]


def test_admin_can_change_plan_price_which_mints_a_new_stripe_price(client, monkeypatch):
    from app.services import billing_service

    plan_id = create_membership_plan(client, "reprice")

    monkeypatch.setattr(billing_service, "get_stripe_secret_key", lambda: "sk_test_x")
    monkeypatch.setattr(
        billing_service.stripe.Price,
        "create",
        lambda **kwargs: {"id": "price_reprice_new"},
    )
    monkeypatch.setattr(
        billing_service.stripe.Price,
        "modify",
        lambda price_id, **kwargs: {"id": price_id},
    )

    response = client.patch(
        f"/billing/membership-plans/{plan_id}",
        json={"amount_cents": 15000},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["amount_cents"] == 15000
    assert body["stripe_price_id"] == "price_reprice_new"


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


def test_admin_can_discount_a_player_before_any_payment_exists(client):
    create_test_player(client, "PC_P_PRE_PAYMENT_DISCOUNT")
    plan_id = create_membership_plan(client, "predisc")
    client.post(
        "/billing/subscriptions/PC_P_PRE_PAYMENT_DISCOUNT/membership",
        json={"plan_id": plan_id},
    )

    apply_response = client.post(
        "/billing/subscriptions/PC_P_PRE_PAYMENT_DISCOUNT/discount",
        json={"percent_off": 40},
    )
    assert apply_response.status_code == 200
    assert apply_response.json() == {
        "player_id": "PC_P_PRE_PAYMENT_DISCOUNT",
        "discount_percent_off": 40,
        "applies_to": "pending_membership",
    }

    status_response = client.get("/billing/status/PC_P_PRE_PAYMENT_DISCOUNT")
    assert status_response.json()["membership"]["discount_percent_off"] == 40

    remove_response = client.delete(
        "/billing/subscriptions/PC_P_PRE_PAYMENT_DISCOUNT/discount"
    )
    assert remove_response.status_code == 200
    assert remove_response.json()["discount_percent_off"] is None


def test_discount_without_a_membership_or_subscription_returns_404(client):
    create_test_player(client, "PC_P_NO_DISCOUNT_TARGET")

    response = client.post(
        "/billing/subscriptions/PC_P_NO_DISCOUNT_TARGET/discount",
        json={"percent_off": 20},
    )
    assert response.status_code == 404


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


def test_billing_reports_configured_with_only_secret_key_no_legacy_price(client, monkeypatch):
    from app.services import billing_service

    create_test_player(client, "PC_P_CONFIGURED")
    monkeypatch.setattr(billing_service, "get_stripe_secret_key", lambda: "sk_test_x")
    monkeypatch.setattr(billing_service, "get_stripe_price_id", lambda: "")

    response = client.get("/billing/status/PC_P_CONFIGURED")
    assert response.status_code == 200
    assert response.json()["configured"] is True


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
# Pause / resume — Stripe-backed, mocked
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


# ---------------------------------------------------------------------------
# No-refunds policy — KEMET FC does not offer refunds through this
# platform. A completed payment stays recorded as paid; admins may only
# cancel future renewals, pause a membership, or record a manual payment.
# These tests are a regression guard: refund functionality must never
# reappear in the API surface.
# ---------------------------------------------------------------------------

def test_no_refund_endpoint_exists(client):
    response = client.post("/billing/payments/in_norefund_test/refund", json={})
    assert response.status_code in (404, 405)


def test_billing_service_has_no_refund_method():
    from app.services.billing_service import BillingService

    assert not hasattr(BillingService, "refund_payment")


# ---------------------------------------------------------------------------
# Promo codes
# ---------------------------------------------------------------------------

def test_admin_can_create_list_and_deactivate_a_promo_code(client):
    response = client.post(
        "/billing/promo-codes",
        json={
            "code": "SAVE10",
            "discount_type": "percentage",
            "discount_value": 10,
        },
    )
    assert response.status_code == 201
    promo_code_id = response.json()["promo_code_id"]
    assert response.json()["code"] == "SAVE10"

    list_response = client.get("/billing/promo-codes")
    assert list_response.status_code == 200
    codes = [p["code"] for p in list_response.json()["promo_codes"]]
    assert "SAVE10" in codes

    deactivate_response = client.patch(
        f"/billing/promo-codes/{promo_code_id}", json={"active": False}
    )
    assert deactivate_response.status_code == 200
    assert deactivate_response.json()["active"] is False


def test_non_admin_cannot_manage_promo_codes(client):
    coach_client = _create_role_client("pc.coach.promo", "CoachPassword123!", "coach")
    response = coach_client.post(
        "/billing/promo-codes",
        json={"code": "COACHNO", "discount_type": "percentage", "discount_value": 10},
    )
    assert response.status_code == 403
    assert coach_client.get("/billing/promo-codes").status_code == 403


def test_checkout_session_applies_a_valid_promo_code(client, monkeypatch):
    from app.services import billing_service

    create_test_player(client, "PC_P_PROMO_CHECKOUT")
    plan_id = create_membership_plan(client, "promo_checkout")
    client.post(
        "/billing/subscriptions/PC_P_PROMO_CHECKOUT/membership",
        json={"plan_id": plan_id},
    )
    client.post(
        "/billing/promo-codes",
        json={"code": "PROMO20", "discount_type": "percentage", "discount_value": 20},
    )

    monkeypatch.setattr(billing_service, "get_stripe_secret_key", lambda: "sk_test_x")
    monkeypatch.setattr(billing_service.stripe.Coupon, "retrieve", lambda coupon_id: None)
    monkeypatch.setattr(
        billing_service.stripe.Customer, "create", lambda **kwargs: {"id": "cus_promo_test"}
    )

    captured = {}

    class FakeSession:
        url = "https://checkout.stripe.com/session/promo"

    monkeypatch.setattr(
        billing_service.stripe.checkout.Session,
        "create",
        lambda **kwargs: (captured.update(kwargs), FakeSession())[1],
    )

    guardian_client = _create_role_client("pc.guardian.promo", "GuardianPassword123!", "guardian")
    db = TestingSessionLocal()
    db.add(GuardianPlayerLinkDB(
        guardian_user_id="PC_PC.GUARDIAN.PROMO",
        player_id="PC_P_PROMO_CHECKOUT",
        created_at=utcnow(),
        created_by_user_id="PC_ADMIN",
    ))
    db.query(UserDB).filter(UserDB.user_id == "PC_PC.GUARDIAN.PROMO").update(
        {"email": "promo.guardian@example.com"}
    )
    db.commit()
    db.close()

    response = guardian_client.post(
        "/billing/checkout-session",
        json={"player_id": "PC_P_PROMO_CHECKOUT", "promo_code": "promo20"},
    )
    assert response.status_code == 201
    assert captured["discounts"] == [{"coupon": "promo-" + captured["discounts"][0]["coupon"].split("-", 1)[1]}]

    # The redemption was recorded server-side.
    redemptions = db_query_redemption_count("PROMO20")
    assert redemptions == 1


def test_checkout_session_rejects_an_invalid_promo_code(client, monkeypatch):
    from app.services import billing_service

    create_test_player(client, "PC_P_BAD_PROMO")
    plan_id = create_membership_plan(client, "bad_promo")
    client.post(
        "/billing/subscriptions/PC_P_BAD_PROMO/membership",
        json={"plan_id": plan_id},
    )
    monkeypatch.setattr(billing_service, "get_stripe_secret_key", lambda: "sk_test_x")

    guardian_client = _create_role_client("pc.guardian.badpromo", "GuardianPassword123!", "guardian")
    db = TestingSessionLocal()
    db.add(GuardianPlayerLinkDB(
        guardian_user_id="PC_PC.GUARDIAN.BADPROMO",
        player_id="PC_P_BAD_PROMO",
        created_at=utcnow(),
        created_by_user_id="PC_ADMIN",
    ))
    db.query(UserDB).filter(UserDB.user_id == "PC_PC.GUARDIAN.BADPROMO").update(
        {"email": "badpromo.guardian@example.com"}
    )
    db.commit()
    db.close()

    response = guardian_client.post(
        "/billing/checkout-session",
        json={"player_id": "PC_P_BAD_PROMO", "promo_code": "NOPE_NOT_REAL"},
    )
    assert response.status_code == 404
    assert "Invalid promo code" in response.json()["detail"]


def db_query_redemption_count(code):
    from app.db_models import PromoCodeDB, PromoCodeRedemptionDB

    db = TestingSessionLocal()
    promo = db.query(PromoCodeDB).filter(PromoCodeDB.code == code).one()
    count = (
        db.query(PromoCodeRedemptionDB)
        .filter(PromoCodeRedemptionDB.promo_code_id == promo.promo_code_id)
        .count()
    )
    db.close()
    return count


# ---------------------------------------------------------------------------
# Complimentary membership
# ---------------------------------------------------------------------------

def test_admin_can_grant_and_revoke_complimentary_membership(client):
    create_test_player(client, "PC_P_COMP")

    grant_response = client.post(
        "/billing/subscriptions/PC_P_COMP/complimentary", json={}
    )
    assert grant_response.status_code == 200
    assert grant_response.json()["is_complimentary"] is True

    status_response = client.get("/billing/status/PC_P_COMP")
    assert status_response.json()["membership_status"] == "COMPLIMENTARY"
    assert status_response.json()["eligibility"] == "TRAINING_ELIGIBLE"

    revoke_response = client.delete("/billing/subscriptions/PC_P_COMP/complimentary")
    assert revoke_response.status_code == 200
    assert revoke_response.json()["is_complimentary"] is False


def test_complimentary_membership_blocks_checkout(client, monkeypatch):
    from app.services import billing_service

    create_test_player(client, "PC_P_COMP_CHECKOUT")
    client.post("/billing/subscriptions/PC_P_COMP_CHECKOUT/complimentary", json={})
    monkeypatch.setattr(billing_service, "get_stripe_secret_key", lambda: "sk_test_x")

    guardian_client = _create_role_client("pc.guardian.comp", "GuardianPassword123!", "guardian")
    db = TestingSessionLocal()
    db.add(GuardianPlayerLinkDB(
        guardian_user_id="PC_PC.GUARDIAN.COMP",
        player_id="PC_P_COMP_CHECKOUT",
        created_at=utcnow(),
        created_by_user_id="PC_ADMIN",
    ))
    db.query(UserDB).filter(UserDB.user_id == "PC_PC.GUARDIAN.COMP").update(
        {"email": "comp.guardian@example.com"}
    )
    db.commit()
    db.close()

    response = guardian_client.post(
        "/billing/checkout-session", json={"player_id": "PC_P_COMP_CHECKOUT"}
    )
    assert response.status_code == 404
    assert "complimentary" in response.json()["detail"]


def test_revoking_complimentary_without_one_raises(client):
    create_test_player(client, "PC_P_NO_COMP")
    response = client.delete("/billing/subscriptions/PC_P_NO_COMP/complimentary")
    assert response.status_code == 404


def test_non_admin_cannot_grant_complimentary_membership(client):
    create_test_player(client, "PC_P_COMP_DENY")
    coach_client = _create_role_client("pc.coach.comp", "CoachPassword123!", "coach")
    response = coach_client.post(
        "/billing/subscriptions/PC_P_COMP_DENY/complimentary", json={}
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Eligibility override
# ---------------------------------------------------------------------------

def test_admin_can_set_and_clear_eligibility_override(client):
    create_test_player(client, "PC_P_OVERRIDE")
    plan_id = create_membership_plan(client, "override")
    client.post(
        "/billing/subscriptions/PC_P_OVERRIDE/membership", json={"plan_id": plan_id}
    )

    set_response = client.put(
        "/billing/subscriptions/PC_P_OVERRIDE/eligibility-override",
        json={"reason": "Injured — approved to keep training with the team"},
    )
    assert set_response.status_code == 200
    assert set_response.json()["admin_override_eligibility"]

    status_response = client.get("/billing/status/PC_P_OVERRIDE")
    assert status_response.json()["eligibility"] == "ADMIN_OVERRIDE"

    clear_response = client.put(
        "/billing/subscriptions/PC_P_OVERRIDE/eligibility-override",
        json={"reason": None},
    )
    assert clear_response.status_code == 200
    assert clear_response.json()["admin_override_eligibility"] is None


def test_eligibility_override_requires_existing_membership(client):
    create_test_player(client, "PC_P_NO_MEMBERSHIP_OVERRIDE")
    response = client.put(
        "/billing/subscriptions/PC_P_NO_MEMBERSHIP_OVERRIDE/eligibility-override",
        json={"reason": "test"},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Billing settings
# ---------------------------------------------------------------------------

def test_admin_can_read_and_update_billing_settings(client):
    get_response = client.get("/billing/settings")
    assert get_response.status_code == 200
    assert get_response.json()["grace_period_days"] == 7  # sensible default

    update_response = client.patch(
        "/billing/settings", json={"grace_period_days": 14}
    )
    assert update_response.status_code == 200
    assert update_response.json()["grace_period_days"] == 14

    # Persisted, not just echoed back.
    get_again = client.get("/billing/settings")
    assert get_again.json()["grace_period_days"] == 14


def test_non_admin_cannot_manage_billing_settings(client):
    coach_client = _create_role_client("pc.coach.settings", "CoachPassword123!", "coach")
    assert coach_client.get("/billing/settings").status_code == 403
    assert coach_client.patch("/billing/settings", json={"grace_period_days": 1}).status_code == 403


# ---------------------------------------------------------------------------
# Family / sibling discount rules
# ---------------------------------------------------------------------------

def test_admin_can_create_and_update_family_discount_rule(client):
    response = client.post(
        "/billing/family-discount-rules",
        json={"sibling_position": 2, "discount_percent": 15},
    )
    assert response.status_code == 201
    rule_id = response.json()["rule_id"]

    list_response = client.get("/billing/family-discount-rules")
    assert any(r["rule_id"] == rule_id for r in list_response.json()["rules"])

    update_response = client.patch(
        f"/billing/family-discount-rules/{rule_id}",
        json={"discount_percent": 20},
    )
    assert update_response.status_code == 200
    assert update_response.json()["discount_percent"] == 20


def test_duplicate_sibling_position_rejected(client):
    client.post(
        "/billing/family-discount-rules",
        json={"sibling_position": 3, "discount_percent": 10},
    )
    response = client.post(
        "/billing/family-discount-rules",
        json={"sibling_position": 3, "discount_percent": 25},
    )
    assert response.status_code == 400


def test_sibling_discount_applied_automatically_at_checkout(client, monkeypatch):
    from app.services import billing_service

    # sibling_position=2 may already exist from an earlier test in this
    # shared module-scoped DB — create it, or update it in place to the
    # exact percent this test expects, rather than assuming a fresh row.
    create_response = client.post(
        "/billing/family-discount-rules",
        json={"sibling_position": 2, "discount_percent": 25},
    )
    if create_response.status_code != 201:
        existing_rule = next(
            rule for rule in client.get("/billing/family-discount-rules").json()["rules"]
            if rule["sibling_position"] == 2
        )
        client.patch(
            f"/billing/family-discount-rules/{existing_rule['rule_id']}",
            json={"discount_percent": 25, "active": True},
        )

    create_test_player(client, "PC_P_SIB_FIRST")
    create_test_player(client, "PC_P_SIB_SECOND")
    plan_id = create_membership_plan(client, "sibling")

    guardian_client = _create_role_client("pc.guardian.sibling", "GuardianPassword123!", "guardian")
    db = TestingSessionLocal()
    for player_id in ("PC_P_SIB_FIRST", "PC_P_SIB_SECOND"):
        db.add(GuardianPlayerLinkDB(
            guardian_user_id="PC_PC.GUARDIAN.SIBLING",
            player_id=player_id,
            created_at=utcnow(),
            created_by_user_id="PC_ADMIN",
        ))
    db.query(UserDB).filter(UserDB.user_id == "PC_PC.GUARDIAN.SIBLING").update(
        {"email": "sibling.guardian@example.com"}
    )
    db.commit()
    db.close()

    # First child assigned first -> full price (sibling position 1).
    client.post(
        f"/billing/subscriptions/PC_P_SIB_FIRST/membership", json={"plan_id": plan_id}
    )
    # Second child assigned after -> sibling position 2 -> discounted.
    client.post(
        f"/billing/subscriptions/PC_P_SIB_SECOND/membership", json={"plan_id": plan_id}
    )

    monkeypatch.setattr(billing_service, "get_stripe_secret_key", lambda: "sk_test_x")
    monkeypatch.setattr(billing_service.stripe.Coupon, "retrieve", lambda coupon_id: None)
    monkeypatch.setattr(
        billing_service.stripe.Customer, "create", lambda **kwargs: {"id": "cus_sibling_test"}
    )

    captured = {}

    class FakeSession:
        url = "https://checkout.stripe.com/session/sibling"

    monkeypatch.setattr(
        billing_service.stripe.checkout.Session,
        "create",
        lambda **kwargs: (captured.update(kwargs), FakeSession())[1],
    )

    response = guardian_client.post(
        "/billing/checkout-session", json={"player_id": "PC_P_SIB_SECOND"}
    )
    assert response.status_code == 201
    assert captured["discounts"] == [{"coupon": "kemetfc-25pct-forever"}]


def test_non_admin_cannot_manage_family_discount_rules(client):
    coach_client = _create_role_client("pc.coach.sibling", "CoachPassword123!", "coach")
    response = coach_client.post(
        "/billing/family-discount-rules",
        json={"sibling_position": 5, "discount_percent": 10},
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Billing portal (saved payment methods)
# ---------------------------------------------------------------------------

def test_guardian_can_request_a_billing_portal_session(client, monkeypatch):
    from app.services import billing_service

    monkeypatch.setattr(billing_service, "get_stripe_secret_key", lambda: "sk_test_x")
    monkeypatch.setattr(
        billing_service.stripe.Customer, "create", lambda **kwargs: {"id": "cus_portal_test"}
    )

    class FakePortalSession:
        url = "https://billing.stripe.com/session/portal"

    monkeypatch.setattr(
        billing_service.stripe.billing_portal.Session,
        "create",
        lambda **kwargs: FakePortalSession(),
    )

    guardian_client = _create_role_client("pc.guardian.portal", "GuardianPassword123!", "guardian")
    db = TestingSessionLocal()
    db.query(UserDB).filter(UserDB.user_id == "PC_PC.GUARDIAN.PORTAL").update(
        {"email": "portal.guardian@example.com"}
    )
    db.commit()
    db.close()

    response = guardian_client.post("/billing/portal-session")
    assert response.status_code == 200
    assert response.json()["portal_url"] == "https://billing.stripe.com/session/portal"


def test_coach_cannot_request_a_billing_portal_session(client):
    coach_client = _create_role_client("pc.coach.portal", "CoachPassword123!", "coach")
    response = coach_client.post("/billing/portal-session")
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Financial reporting
# ---------------------------------------------------------------------------

def test_admin_financial_report_reflects_manual_payments_by_plan(client):
    create_test_player(client, "PC_P_REPORT")
    plan_id = create_membership_plan(client, "report")

    assign_response = client.post(
        "/billing/subscriptions/PC_P_REPORT/membership", json={"plan_id": plan_id}
    )
    assert assign_response.status_code == 200

    manual_response = client.post(
        "/billing/manual-payments/PC_P_REPORT",
        json={
            "amount_cents": 12000,
            "currency": "usd",
            "method": "cash",
            "payment_date": "2026-09-01",
        },
    )
    assert manual_response.status_code == 201

    report_response = client.get("/billing/admin/report")
    assert report_response.status_code == 200
    report = report_response.json()

    assert "revenue_by_plan" in report
    assert "revenue_by_team" in report
    assert "manual_vs_online_cents" in report
    assert "upcoming_renewals" in report

    plan_bucket = next(
        (entry for entry in report["revenue_by_plan"] if entry["plan_name"] == "Monthly Plan report"),
        None,
    )
    assert plan_bucket is not None
    assert plan_bucket["total_cents"] >= 12000
    assert report["manual_vs_online_cents"]["manual_cents"] >= 12000


def test_non_admin_cannot_view_financial_report(client):
    guardian_client = _create_role_client("pc.guardian.report", "GuardianPassword123!", "guardian")
    coach_client = _create_role_client("pc.coach.report", "CoachPassword123!", "coach")

    assert guardian_client.get("/billing/admin/report").status_code == 403
    assert coach_client.get("/billing/admin/report").status_code == 403


def test_unauthenticated_cannot_view_financial_report(anonymous_client):
    assert anonymous_client.get("/billing/admin/report").status_code == 401
