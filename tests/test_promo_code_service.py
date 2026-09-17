from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.db_models import PlayerDB, UserDB
from app.services.promo_code_service import PromoCodeError, PromoCodeService


engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def make_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    db.add(UserDB(
        user_id="U1", username="guardian1", password_hash="x", role="guardian",
        active=True, email="guardian@example.com",
        created_at=datetime.now(), updated_at=datetime.now(),
    ))
    db.add(UserDB(
        user_id="U2", username="guardian2", password_hash="x", role="guardian",
        active=True, email="guardian2@example.com",
        created_at=datetime.now(), updated_at=datetime.now(),
    ))
    db.add(UserDB(
        user_id="ADMIN1", username="admin1", password_hash="x", role="admin",
        active=True, created_at=datetime.now(), updated_at=datetime.now(),
    ))
    for player_id in ["P1", "P2"]:
        db.add(PlayerDB(
            player_id=player_id, first_name_ar="a", last_name_ar="b",
            first_name_en="Test", last_name_en="Player",
            date_of_birth=date(2015, 1, 1), sex="male",
            physical_profile={}, technical_profile={}, mental_profile={},
            match_performance={}, created_at=datetime.now(),
        ))
    db.commit()
    return db


def test_create_and_validate_percentage_promo_code():
    db = make_db()
    service = PromoCodeService(db)
    promo = service.create_promo_code(
        actor_user_id="ADMIN1", code="welcome10", discount_type="percentage",
        discount_value=10, starts_at=None, expires_at=None, max_uses=None,
        per_family_limit=None, eligible_plan_ids=None,
    )
    assert promo.code == "WELCOME10"  # normalized to uppercase

    validated = service.validate_promo_code("welcome10", "U1", None)
    assert validated.promo_code_id == promo.promo_code_id


def test_duplicate_code_is_rejected():
    db = make_db()
    service = PromoCodeService(db)
    service.create_promo_code(
        actor_user_id="ADMIN1", code="DUPE", discount_type="percentage",
        discount_value=10, starts_at=None, expires_at=None, max_uses=None,
        per_family_limit=None, eligible_plan_ids=None,
    )
    with pytest.raises(PromoCodeError):
        service.create_promo_code(
            actor_user_id="ADMIN1", code="dupe", discount_type="fixed",
            discount_value=500, starts_at=None, expires_at=None, max_uses=None,
            per_family_limit=None, eligible_plan_ids=None,
        )


def test_invalid_code_is_rejected():
    db = make_db()
    service = PromoCodeService(db)
    with pytest.raises(PromoCodeError, match="Invalid promo code"):
        service.validate_promo_code("DOES_NOT_EXIST", "U1", None)


def test_inactive_code_is_rejected():
    db = make_db()
    service = PromoCodeService(db)
    promo = service.create_promo_code(
        actor_user_id="ADMIN1", code="OFF", discount_type="percentage",
        discount_value=10, starts_at=None, expires_at=None, max_uses=None,
        per_family_limit=None, eligible_plan_ids=None,
    )
    service.update_promo_code(promo.promo_code_id, actor_user_id="ADMIN1", active=False)

    with pytest.raises(PromoCodeError, match="no longer active"):
        service.validate_promo_code("OFF", "U1", None)


def test_expired_code_is_rejected():
    db = make_db()
    service = PromoCodeService(db)
    service.create_promo_code(
        actor_user_id="ADMIN1", code="EXPIRED", discount_type="percentage",
        discount_value=10,
        starts_at=None,
        expires_at=datetime.now() - timedelta(days=1),
        max_uses=None, per_family_limit=None, eligible_plan_ids=None,
    )
    with pytest.raises(PromoCodeError, match="expired"):
        service.validate_promo_code("EXPIRED", "U1", None)


def test_not_yet_started_code_is_rejected():
    db = make_db()
    service = PromoCodeService(db)
    service.create_promo_code(
        actor_user_id="ADMIN1", code="FUTURE", discount_type="percentage",
        discount_value=10,
        starts_at=datetime.now() + timedelta(days=1),
        expires_at=None,
        max_uses=None, per_family_limit=None, eligible_plan_ids=None,
    )
    with pytest.raises(PromoCodeError, match="not active yet"):
        service.validate_promo_code("FUTURE", "U1", None)


def test_max_uses_limit_is_enforced_across_families():
    db = make_db()
    service = PromoCodeService(db)
    promo = service.create_promo_code(
        actor_user_id="ADMIN1", code="LIMITED", discount_type="percentage",
        discount_value=10, starts_at=None, expires_at=None, max_uses=1,
        per_family_limit=None, eligible_plan_ids=None,
    )
    service.validate_promo_code("LIMITED", "U1", None)
    service.redeem_promo_code(promo.promo_code_id, "U1", "P1")

    with pytest.raises(PromoCodeError, match="usage limit"):
        service.validate_promo_code("LIMITED", "U2", None)


def test_per_family_limit_is_enforced():
    db = make_db()
    service = PromoCodeService(db)
    promo = service.create_promo_code(
        actor_user_id="ADMIN1", code="ONEPERFAM", discount_type="percentage",
        discount_value=10, starts_at=None, expires_at=None, max_uses=None,
        per_family_limit=1, eligible_plan_ids=None,
    )
    service.validate_promo_code("ONEPERFAM", "U1", None)
    service.redeem_promo_code(promo.promo_code_id, "U1", "P1")

    # Same family (U1) is blocked...
    with pytest.raises(PromoCodeError, match="already been used by your family"):
        service.validate_promo_code("ONEPERFAM", "U1", None)

    # ...but a different family is unaffected.
    service.validate_promo_code("ONEPERFAM", "U2", None)


def test_plan_restricted_code_rejects_other_plans():
    db = make_db()
    service = PromoCodeService(db)
    service.create_promo_code(
        actor_user_id="ADMIN1", code="PLANONLY", discount_type="percentage",
        discount_value=10, starts_at=None, expires_at=None, max_uses=None,
        per_family_limit=None, eligible_plan_ids=["MPLAN_A"],
    )
    with pytest.raises(PromoCodeError, match="not valid for the selected plan"):
        service.validate_promo_code("PLANONLY", "U1", "MPLAN_B")

    # The eligible plan itself is fine.
    service.validate_promo_code("PLANONLY", "U1", "MPLAN_A")
