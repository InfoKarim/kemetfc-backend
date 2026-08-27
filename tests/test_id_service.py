from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.db_models import PlayerDB
from app.services.id_service import next_entity_id


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_generates_persistent_sequential_ids():
    db = make_session()

    assert next_entity_id(db, "player") == "P000001"
    db.commit()
    assert next_entity_id(db, "player") == "P000002"


def test_skips_an_existing_legacy_id():
    db = make_session()
    db.add(PlayerDB(
        player_id="P000001",
        first_name_ar="كريم",
        last_name_ar="السيد",
        first_name_en="Karim",
        last_name_en="Elsayed",
        date_of_birth=date(2015, 1, 1),
        sex="male",
        team_id=None,
        physical_profile={},
        technical_profile={},
        mental_profile={},
        match_performance={},
    ))
    db.commit()

    assert next_entity_id(db, "player") == "P000002"
