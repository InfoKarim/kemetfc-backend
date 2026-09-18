from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, Field, field_validator


class PhysicalProfileSchema(BaseModel):
    height_cm: float
    weight_kg: float
    dominant_foot: str
    speed: float
    acceleration: float
    agility: float
    stamina: float
    strength: float


class TechnicalProfileSchema(BaseModel):
    ball_control: float
    dribbling: float
    passing: float
    shooting: float
    finishing: float


class MentalProfileSchema(BaseModel):
    decision_making: float
    concentration: float
    composure: float
    positioning: float
    vision: float
    awareness: float
    game_reading: float
    coachability: float


class MatchPerformanceSchema(BaseModel):
    minutes_played: int
    goals: int
    assists: int
    shots: int
    shots_on_target: int
    passes_attempted: int
    passes_completed: int
    tackles: int
    interceptions: int
    rating: float


class TacticalProfileSchema(BaseModel):
    positioning_spatial_intelligence: float
    attacking_contribution_in_possession: float
    attacking_contribution_off_ball: float
    defensive_tactical_contribution: float
    transitions: float
    decision_quality: float
    collective_coordination: float
    set_piece_contribution: float


class WeakFootProfileSchema(BaseModel):
    weak_foot_usage_pct: float
    weak_foot_passing: float
    weak_foot_receiving: float
    weak_foot_dribbling: float
    weak_foot_finishing: float


class PlayerSchema(BaseModel):
    player_id: str | None = None
    first_name_ar: str
    last_name_ar: str
    first_name_en: str
    last_name_en: str
    date_of_birth: date
    sex: str
    physical_profile: PhysicalProfileSchema
    technical_profile: TechnicalProfileSchema
    mental_profile: MentalProfileSchema
    match_performance: MatchPerformanceSchema
    tactical_profile: TacticalProfileSchema
    weak_foot_profile: WeakFootProfileSchema
    team_id: str | None = None


class MatchSchema(BaseModel):
    match_id: str | None = None
    competition_id: str
    season_id: str
    home_team_id: str
    away_team_id: str
    match_date: datetime
    venue_id: str | None
    status: str
    home_score: int | None
    away_score: int | None


class AnalysisSchema(BaseModel):
    analysis_id: str | None = None
    video_id: str
    player_id: str
    created_at: datetime
    analysis_type: str
    model_name: str
    model_version: str
    processing_status: str
    processed_at: datetime | None
    confidence_score: float | None
    overall_score: float | None
    strengths: list
    weaknesses: list
    recommendations: list
    raw_output_path: str | None
    requires_human_review: bool
    human_review_status: str
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_notes: str | None
    approved: bool
    approved_by: str | None
    approved_at: datetime | None


class DrillSchema(BaseModel):
    drill_id: str | None = None
    name: str
    category: str
    description: str
    min_age: int
    max_age: int
    difficulty: str
    duration_minutes: int
    equipment: list[str]
    video_url: str
    active: bool


class DrillRecommendationSchema(BaseModel):
    weakness: str
    weakness_score: float
    age: int
    player_difficulty: str | None = None
    target_duration: int | None = None
    available_equipment: list[str] | None = None


class AnalysisDrillRecommendationSchema(BaseModel):
    age: int | None = None
    player_difficulty: str | None = None
    target_duration: int | None = None
    available_equipment: list[str] | None = None


class CreateTrainingPlanSchema(BaseModel):
    plan_id: str | None = None
    player_difficulty: str
    target_duration: int
    available_equipment: list[str]


class UpdateTrainingPlanStatusSchema(BaseModel):
    status: Literal[
        "draft",
        "active",
        "completed",
        "cancelled",
    ]


class UpdateTrainingPlanDetailsSchema(BaseModel):
    player_difficulty: Literal[
        "beginner",
        "intermediate",
        "advanced",
    ] | None = None
    target_duration: int | None = Field(default=None, gt=0)
    available_equipment: list[str] | None = None


class AddPlanVideoSchema(BaseModel):
    weakness: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=2000)
    channel: str | None = Field(default=None, max_length=200)
    thumbnail_url: str | None = Field(default=None, max_length=2000)


class VideoSchema(BaseModel):
    video_id: str | None = None
    record_id: str
    video_type: str
    duration_seconds: float
    recorded_at: datetime
    session_id: str
    location_id: str
    capture_device: str
    resolution: str
    frame_rate_fps: float
    file_size_mb: float
    file_format: str
    file_path: str
    checksum: str
    original_preserved: bool
    ai_processing_status: str
    ai_processed_at: datetime | None
    ai_model_version: str | None
    ai_confidence_score: float | None
    requires_human_review: bool
    review_reason: str
    human_review_status: str
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_notes: str | None
    analysis_approved: bool
    approved_by: str | None
    approved_at: datetime | None


class PlayerVideoUploadMetadataSchema(BaseModel):
    video_id: str | None = None
    record_id: str | None = None
    player_id: str
    video_type: str
    duration_seconds: float
    session_id: str
    location_id: str
    capture_device: str
    resolution: str
    frame_rate_fps: float
    schema_version: str
    created_by: str


class CreateVideoAnalysisJobSchema(BaseModel):
    job_id: str | None = Field(default=None, min_length=1)
    analysis_type: Literal[
        "pose_estimation",
        "squat_jump",
        "agility_ladder",
        "full_match",
    ] = "pose_estimation"
    max_attempts: int = Field(default=3, gt=0)
    target_track_id: int | None = Field(default=None, ge=0)


class UpdateVideoAnalysisJobSchema(BaseModel):
    status: Literal[
        "queued",
        "processing",
        "completed",
        "failed",
        "cancelled",
    ]
    progress_percent: float | None = Field(default=None, ge=0, le=100)
    model_name: str | None = None
    model_version: str | None = None
    result_path: str | None = None
    error_message: str | None = None


class ReviewVideoAnalysisJobSchema(BaseModel):
    review_status: Literal["approved", "rejected"]
    review_notes: str | None = None



class TeamSchema(BaseModel):
    team_id: str | None = None
    name: str
    age_group: str
    coach_name: str
    season_id: str
    active: bool


class LoginSchema(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class RequestPasswordResetSchema(BaseModel):
    username: str = Field(min_length=3, max_length=64)


class ConfirmPasswordResetSchema(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    new_password: str = Field(min_length=12, max_length=256)


class CreateUserSchema(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=256)
    role: Literal["admin", "coach", "reviewer", "guardian"]
    email: str | None = Field(default=None, min_length=3, max_length=320)
    first_name: str | None = Field(default=None, max_length=120)
    last_name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=320)
    national_id: str | None = Field(default=None, max_length=64)
    feature_permissions: list[Literal[
        "dashboard",
        "players",
        "teams",
        "assessments",
        "training",
        "videos",
        "matches",
        "reports",
        "calendar",
        "messaging",
    ]] | None = None


class UpdateUserSchema(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=64)
    role: Literal["admin", "coach", "reviewer", "guardian"] | None = None
    active: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=256)
    email: str | None = Field(default=None, min_length=3, max_length=320)
    first_name: str | None = Field(default=None, max_length=120)
    last_name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=320)
    national_id: str | None = Field(default=None, max_length=64)
    feature_permissions: list[Literal[
        "dashboard",
        "players",
        "teams",
        "assessments",
        "training",
        "videos",
        "matches",
        "reports",
        "calendar",
        "messaging",
    ]] | None = None


class ChangeOwnPasswordSchema(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class GuardianConsentSchema(BaseModel):
    consent_id: str | None = Field(default=None, min_length=1, max_length=128)
    guardian_name: str = Field(min_length=1, max_length=200)
    guardian_email: str = Field(min_length=3, max_length=320)
    verification_method: Literal[
        "signed_form",
        "verified_email",
        "in_person",
    ]
    purposes: list[Literal["video_analysis", "performance_tracking", "ml_training"]]
    expires_at: datetime | None = None


class MLDatasetEntryCreateSchema(BaseModel):
    team_id: str | None = None
    age_band: str = Field(min_length=2, max_length=10)
    sex_cohort: Literal["male", "female", "mixed"]
    camera_id: str = Field(min_length=1, max_length=100)
    lighting: Literal["day", "night", "indoor"]
    notes: str | None = Field(default=None, max_length=2000)


class MLDatasetEntryReviewSchema(BaseModel):
    status: Literal["approved", "excluded"]


class GuardianPlayerLinkSchema(BaseModel):
    guardian_user_id: str = Field(min_length=1)
    player_id: str = Field(min_length=1)


class CreateCheckoutSessionSchema(BaseModel):
    player_id: str = Field(min_length=1)
    promo_code: str | None = Field(default=None, max_length=64)


class ApplyDiscountSchema(BaseModel):
    percent_off: int = Field(ge=1, le=100)


class RefundPaymentSchema(BaseModel):
    refund_type: Literal["full", "partial"]
    amount_cents: int | None = Field(default=None, gt=0)
    reason: str = Field(min_length=1, max_length=500)
    internal_note: str | None = Field(default=None, max_length=1000)
    # Client-generated once per confirmation-modal submission and reused
    # on any automatic retry of that SAME submission — never regenerated
    # on click — so a double-click or network retry is a safe no-op
    # instead of a second refund. See RefundService for the server side.
    idempotency_key: str = Field(min_length=8, max_length=100)

    @field_validator("amount_cents")
    @classmethod
    def _partial_requires_amount(cls, value, info):
        if info.data.get("refund_type") == "partial" and value is None:
            raise ValueError("amount_cents is required for a partial refund")
        return value


class CreateMembershipPlanSchema(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    amount_cents: int = Field(ge=1)
    currency: str = Field(min_length=3, max_length=3, default="usd")
    billing_interval: Literal["month", "year"]
    # Optional escape hatch for a plan that should reuse a Stripe Price the
    # admin already created directly in the Stripe dashboard. Left blank,
    # the service creates a real Stripe Product + Price for the admin —
    # they should never need Stripe dashboard access just to set a price.
    stripe_price_id: str | None = Field(default=None, max_length=120)


class UpdateMembershipPlanSchema(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    active: bool | None = None
    # Stripe Price objects are immutable — setting this creates a brand
    # new Stripe Price behind the scenes and repoints the plan at it.
    # Players already assigned to the old price keep paying it until
    # reassigned; this only changes what NEW checkouts charge.
    amount_cents: int | None = Field(default=None, ge=1)


class AssignMembershipPlanSchema(BaseModel):
    plan_id: str = Field(min_length=1)


class RecordManualPaymentSchema(BaseModel):
    amount_cents: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3, default="usd")
    method: Literal["cash", "check", "bank_transfer", "other"]
    payment_date: date
    note: str | None = Field(default=None, max_length=500)


class CreatePromoCodeSchema(BaseModel):
    code: str = Field(min_length=2, max_length=64)
    discount_type: Literal["percentage", "fixed"]
    discount_value: int = Field(ge=1)
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    max_uses: int | None = Field(default=None, ge=1)
    per_family_limit: int | None = Field(default=None, ge=1)
    eligible_plan_ids: list[str] | None = None


class UpdatePromoCodeSchema(BaseModel):
    active: bool | None = None


class GrantComplimentaryMembershipSchema(BaseModel):
    plan_id: str | None = None


class SetEligibilityOverrideSchema(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class UpdateBillingSettingsSchema(BaseModel):
    grace_period_days: int | None = Field(default=None, ge=0, le=90)
    payment_due_reminder_days_before: int | None = Field(default=None, ge=0, le=30)


class CreateFamilyDiscountRuleSchema(BaseModel):
    sibling_position: int = Field(ge=1, le=10)
    discount_percent: int = Field(ge=1, le=100)


class UpdateFamilyDiscountRuleSchema(BaseModel):
    discount_percent: int | None = Field(default=None, ge=1, le=100)
    active: bool | None = None


class UpdateRegistrationStatusSchema(BaseModel):
    status: Literal["submitted", "waitlisted", "archived"]


class RegistrationConsentSchema(BaseModel):
    parent_consent: bool
    liability_waiver: bool
    emergency_medical: bool
    photo_video: bool
    privacy_policy: bool
    terms: bool
    technology_ai_consent: bool


class PublicRegistrationSchema(BaseModel):
    parent_name: str = Field(min_length=1, max_length=200)
    parent_email: str = Field(min_length=3, max_length=320)
    parent_phone: str = Field(min_length=1, max_length=40)
    emergency_contact: str = Field(min_length=1, max_length=200)
    player_name: str = Field(min_length=1, max_length=200)
    player_date_of_birth: date
    player_age: int = Field(ge=4, le=19)
    preferred_position: str | None = Field(default=None, max_length=60)
    experience_level: str | None = Field(default=None, max_length=60)
    current_team: str | None = Field(default=None, max_length=200)
    consents: RegistrationConsentSchema


class CreatePlayerFromRegistrationSchema(BaseModel):
    """Fields the registration does not already provide — everything the
    registration DOES provide (name, DOB) is read server-side from the
    authoritative registration record itself, never re-submitted by the
    client, so it can't drift from what the parent actually typed."""

    first_name_ar: str = Field(min_length=1, max_length=120)
    last_name_ar: str = Field(min_length=1, max_length=120)
    sex: str = Field(min_length=1, max_length=20)
    team_id: str | None = None
    physical_profile: PhysicalProfileSchema
    technical_profile: TechnicalProfileSchema
    mental_profile: MentalProfileSchema
    match_performance: MatchPerformanceSchema
    tactical_profile: TacticalProfileSchema
    weak_foot_profile: WeakFootProfileSchema
    # Set only after the staff member has already seen a possible-duplicate
    # warning from GET /registrations/{id}/duplicate-check and chosen to
    # proceed anyway — never implied by simply resubmitting the form.
    confirm_duplicate: bool = False


class PublicContactMessageSchema(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    topic: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=5000)


class ChildDeletionRequestSchema(BaseModel):
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=1000)


class ReviewPrivacyRequestSchema(BaseModel):
    status: Literal["in_review", "rejected", "completed"]
    review_notes: str | None = Field(default=None, max_length=2000)


class DevelopmentForecastSchema(BaseModel):
    weeks: int = Field(ge=1, le=52)
    sessions_per_week: int = Field(ge=1, le=14)
    expected_gain_per_session: float = Field(default=0.35, ge=0, le=5)
    session_volatility: float = Field(default=0.5, ge=0, le=10)
    adherence_probability: float = Field(default=0.8, ge=0, le=1)
    minimum_improvement: float = Field(default=5.0, ge=0, le=100)
    simulations: int = Field(default=5000, ge=100, le=50000)
    seed: int = 42


class CreateSeasonSchema(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    start_date: date | None = None
    end_date: date | None = None
    make_active: bool = False


class CreateMessageSchema(BaseModel):
    recipient_id: str = Field(min_length=1, max_length=64)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=5000)


class DrillDiagramStrokePointSchema(BaseModel):
    x: float = Field(ge=0, le=600)
    y: float = Field(ge=0, le=380)


class SaveDrillDiagramAnnotationSchema(BaseModel):
    strokes: list[list[DrillDiagramStrokePointSchema]] = Field(
        default_factory=list, max_length=300
    )

    @field_validator("strokes")
    @classmethod
    def _limit_points_per_stroke(cls, strokes):
        for stroke in strokes:
            if len(stroke) > 2000:
                raise ValueError("A single stroke cannot exceed 2000 points")
        return strokes


class RecordYoYoKidsSchema(BaseModel):
    test_date: date
    level: int = Field(ge=1, le=25)
    shuttle: int = Field(ge=1, le=10)
    total_distance_m: float = Field(gt=0, le=3000)
    notes: str | None = Field(default=None, max_length=1000)

    @field_validator("test_date")
    @classmethod
    def _not_in_future(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("Test date cannot be in the future")
        return value


class RecordBallMasterySchema(BaseModel):
    test_date: date
    sole_rolls: int = Field(ge=1, le=5)
    inside_outside_cuts: int = Field(ge=1, le=5)
    l_turn: int = Field(ge=1, le=5)
    drag_back: int = Field(ge=1, le=5)
    notes: str | None = Field(default=None, max_length=1000)
    ai_assisted: bool = False

    @field_validator("test_date")
    @classmethod
    def _not_in_future(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("Test date cannot be in the future")
        return value


class UpdateCoachMessageSchema(BaseModel):
    message: str | None = Field(default=None, max_length=2000)
    next_focus: list[str] = Field(default_factory=list, max_length=3)

    @field_validator("next_focus")
    @classmethod
    def _limit_item_length(cls, items: list[str]) -> list[str]:
        for item in items:
            if len(item) > 200:
                raise ValueError("Each next-focus item must be 200 characters or fewer")
        return items


# --- Smart Soccer Camera tracking -------------------------------------

class CreateTrackingSessionSchema(BaseModel):
    player_id: str = Field(min_length=1)
    tracking_mode: Literal["player_lock", "ball_track", "smart_soccer"]
    gimbal_model: str | None = Field(default=None, max_length=120)
    calibration_scale_m_per_unit: float | None = Field(default=None, gt=0, le=1000)
    # The iOS app's local recording ID — retrying this call (e.g. after a
    # dropped response while the backend actually created the session
    # fine) with the SAME value returns the existing session rather than
    # creating a duplicate. See TrackingService.create_session.
    client_recording_id: str | None = Field(default=None, max_length=64)
    # Model-version traceability (Phase 2) — the iOS client reports
    # exactly what ran, never left for the backend to guess. Omitting
    # ball_detector_version defaults ball_model_status to "missing"
    # server-side (see TrackingService.create_session) rather than
    # silently implying a ball model was present.
    player_detector_version: str | None = Field(default=None, max_length=60)
    ball_detector_version: str | None = Field(default=None, max_length=60)
    ball_model_status: Literal["missing", "fallback_classical", "installed"] = "missing"
    pose_model_version: str | None = Field(default=None, max_length=60)
    tracker_algorithm_version: str | None = Field(default=None, max_length=60)
    framing_algorithm_version: str | None = Field(default=None, max_length=60)
    ios_app_version: str | None = Field(default=None, max_length=60)


class TrackingSampleSchema(BaseModel):
    t_seconds: float = Field(ge=0)
    player_bbox: list[float] | None = None
    player_center: list[float] | None = None
    player_confidence: float | None = Field(default=None, ge=0, le=1)
    player_track_id: int | None = None
    ball_bbox: list[float] | None = None
    ball_center: list[float] | None = None
    ball_confidence: float | None = Field(default=None, ge=0, le=1)
    ball_track_id: int | None = None
    pose_keypoints: list[dict] | None = None
    gimbal_state: str = Field(default="unknown", max_length=40)
    tracking_mode: str = Field(default="smart_soccer", max_length=40)
    tracking_status: str = Field(default="unknown", max_length=40)

    @field_validator("player_bbox", "ball_bbox")
    @classmethod
    def _bbox_has_four_values(cls, value):
        if value is not None and len(value) != 4:
            raise ValueError("A bounding box must have exactly 4 values: [x, y, w, h]")
        return value

    @field_validator("player_center", "ball_center")
    @classmethod
    def _center_has_two_values(cls, value):
        if value is not None and len(value) != 2:
            raise ValueError("A center point must have exactly 2 values: [x, y]")
        return value


class IngestTrackingSamplesSchema(BaseModel):
    samples: list[TrackingSampleSchema] = Field(min_length=1, max_length=500)


class IngestTrackingEventSchema(BaseModel):
    event_type: str = Field(min_length=1, max_length=60)
    details: dict = Field(default_factory=dict)


class CompleteTrackingSessionSchema(BaseModel):
    video_id: str | None = Field(default=None, max_length=64)


class PublishTrackingAssessmentSchema(BaseModel):
    assessment_date: date | None = None


class RecordCoachValidationLabelSchema(BaseModel):
    label_type: Literal[
        "ball_control_rating",
        "agility_rating",
        "dribbling_rating",
        "assessment_quality",
        "tracking_quality",
        "incorrect_ai_metric_flag",
        # Phase 2: the coach's own YES/NO/UNSURE confirmation that the
        # session tracked the right child — future training-label ground
        # truth for wrong-player-lock detection (see spec section 29).
        "correct_player_tracked",
    ]
    value: dict
    notes: str | None = Field(default=None, max_length=1000)


class ConfirmPlayerTrackedSchema(BaseModel):
    answer: Literal["yes", "no", "unsure"]
    notes: str | None = Field(default=None, max_length=1000)


class ResolveCheckInTokenSchema(BaseModel):
    token: str = Field(min_length=1, max_length=200)


class RegisterMLModelSchema(BaseModel):
    model_name: str = Field(min_length=1, max_length=120)
    model_version: str = Field(min_length=1, max_length=60)
    model_type: Literal["on_device_vision", "on_device_coreml", "on_device_classical_cv", "backend", "rule_based"]
    status: Literal["experimental", "active", "inactive", "deprecated"] = "experimental"
    training_dataset_version: str | None = Field(default=None, max_length=120)
    evaluation_metrics: dict | None = None
    coreml_artifact_version: str | None = Field(default=None, max_length=120)
    backend_artifact_version: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)


class UpdateMLModelStatusSchema(BaseModel):
    status: Literal["experimental", "active", "inactive", "deprecated"]
