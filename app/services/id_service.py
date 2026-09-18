from sqlalchemy.orm import Session

from app.db_models import (
    AnalysisDB,
    AssessmentRegistrationDB,
    CoachValidationLabelDB,
    ContactMessageDB,
    DataRecordDB,
    DrillDB,
    GeneratedDrillDiagramDB,
    GuardianConsentDB,
    FamilyDiscountRuleDB,
    IdCounterDB,
    ManualPaymentDB,
    MatchDB,
    MembershipPlanDB,
    MLDatasetEntryDB,
    MLModelRegistryDB,
    PlayerAssessmentDB,
    PlayerCheckInTokenDB,
    PlayerDB,
    PrivacyRequestDB,
    PromoCodeDB,
    PromoCodeRedemptionDB,
    RefundDB,
    SeasonDB,
    TeamDB,
    TrackingEventDB,
    TrackingSessionDB,
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
    "generated_drill_diagram": ("GEN", GeneratedDrillDiagramDB),
    "player_assessment": ("ASSESS", PlayerAssessmentDB),
    "membership_plan": ("MPLAN", MembershipPlanDB),
    "manual_payment": ("MPAY", ManualPaymentDB),
    "promo_code": ("PROMO", PromoCodeDB),
    "promo_code_redemption": ("PROMORED", PromoCodeRedemptionDB),
    "family_discount_rule": ("FAMDISC", FamilyDiscountRuleDB),
    "refund": ("REF", RefundDB),
    "tracking_session": ("TRK", TrackingSessionDB),
    "tracking_event": ("TRKE", TrackingEventDB),
    "ml_model": ("MODEL", MLModelRegistryDB),
    "coach_validation_label": ("CVL", CoachValidationLabelDB),
    "player_checkin_token": ("CHKTOK", PlayerCheckInTokenDB),
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
