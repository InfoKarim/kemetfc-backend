from sqlalchemy.orm import Session

from app.db_models import (
    AnalysisDB,
    AssessmentRegistrationDB,
    ContactMessageDB,
    DataRecordDB,
    DrillDB,
    GuardianConsentDB,
    IdCounterDB,
    MatchDB,
    MLDatasetEntryDB,
    PlayerDB,
    PrivacyRequestDB,
    SeasonDB,
    TeamDB,
    TrainingPlanDB,
    VideoAnalysisJobDB,
    VideoDB,
)


ENTITY_CONFIG = {
    "player": ("P", PlayerDB),
    "team": ("TEAM", TeamDB),
    "match": ("MATCH", MatchDB),
    "record": ("REC", DataRecordDB),
    "video": ("VID", VideoDB),
    "analysis": ("AN", AnalysisDB),
    "drill": ("DRILL", DrillDB),
    "training_plan": ("PLAN", TrainingPlanDB),
    "analysis_job": ("JOB", VideoAnalysisJobDB),
    "consent": ("CONSENT", GuardianConsentDB),
    "privacy_request": ("PRIVACY", PrivacyRequestDB),
    "season": ("SEASON", SeasonDB),
    "registration": ("REG", AssessmentRegistrationDB),
    "ml_dataset_entry": ("MLDS", MLDatasetEntryDB),
    "contact_message": ("MSG", ContactMessageDB),
}


def next_entity_id(db: Session, entity: str) -> str:
    """Return a persistent, collision-safe human-readable entity ID."""
    try:
        prefix, model = ENTITY_CONFIG[entity]
    except KeyError as error:
        raise ValueError(f"Unknown ID entity: {entity}") from error

    counter = (
        db.query(IdCounterDB)
        .filter(IdCounterDB.entity == entity)
        .with_for_update()
        .one_or_none()
    )
    if counter is None:
        counter = IdCounterDB(entity=entity, next_value=1)
        db.add(counter)
        db.flush()

    while True:
        value = counter.next_value
        counter.next_value += 1
        candidate = f"{prefix}{value:06d}"
        if db.get(model, candidate) is None:
            db.flush()
            return candidate
