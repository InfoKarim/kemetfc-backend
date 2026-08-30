from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, Field


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
    role: Literal["admin", "coach", "reviewer", "guardian"] | None = None
    active: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=256)
    email: str | None = Field(default=None, min_length=3, max_length=320)
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
    purposes: list[Literal["video_analysis", "performance_tracking"]]
    expires_at: datetime | None = None


class GuardianPlayerLinkSchema(BaseModel):
    guardian_user_id: str = Field(min_length=1)
    player_id: str = Field(min_length=1)


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
