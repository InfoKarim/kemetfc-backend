
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
)

from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PlayerDB(Base):
    __tablename__ = "players"

    player_id: Mapped[str] = mapped_column(String, primary_key=True)
    first_name_ar: Mapped[str] = mapped_column(String)
    last_name_ar: Mapped[str] = mapped_column(String)
    first_name_en: Mapped[str] = mapped_column(String)
    last_name_en: Mapped[str] = mapped_column(String)
    date_of_birth: Mapped[date] = mapped_column(Date)
    sex: Mapped[str] = mapped_column(String)
    team_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("teams.team_id"),
        nullable=True,
        index=True,
    )

    physical_profile: Mapped[dict] = mapped_column(JSON)
    technical_profile: Mapped[dict] = mapped_column(JSON)
    mental_profile: Mapped[dict] = mapped_column(JSON)
    match_performance: Mapped[dict] = mapped_column(JSON)
    tactical_profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    weak_foot_profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    photo_filename: Mapped[str | None] = mapped_column(String, nullable=True)


class MatchDB(Base):
    __tablename__ = "matches"

    match_id: Mapped[str] = mapped_column(String, primary_key=True)
    competition_id: Mapped[str] = mapped_column(String)
    season_id: Mapped[str] = mapped_column(String)
    home_team_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("teams.team_id"),
        index=True,
    )
    away_team_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("teams.team_id"),
        index=True,
    )
    match_date: Mapped[datetime] = mapped_column(DateTime)
    venue_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String)
    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AnalysisDB(Base):
    __tablename__ = "analyses"

    analysis_id: Mapped[str] = mapped_column(String, primary_key=True)
    video_id: Mapped[str] = mapped_column(
    String,
    ForeignKey("videos.video_id"),
    index=True,
)
    player_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("players.player_id"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime)

    analysis_type: Mapped[str] = mapped_column(String)
    model_name: Mapped[str] = mapped_column(String)
    model_version: Mapped[str] = mapped_column(String)

    processing_status: Mapped[str] = mapped_column(String)
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    confidence_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    overall_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    strengths: Mapped[list] = mapped_column(JSON)
    weaknesses: Mapped[list] = mapped_column(JSON)
    recommendations: Mapped[list] = mapped_column(JSON)

    raw_output_path: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    requires_human_review: Mapped[bool] = mapped_column(Boolean)
    human_review_status: Mapped[str] = mapped_column(String)

    reviewed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    review_notes: Mapped[str | None] = mapped_column(String, nullable=True)
    approved: Mapped[bool] = mapped_column(Boolean)
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )


class VideoDB(Base):
    __tablename__ = "videos"

    video_id: Mapped[str] = mapped_column(String, primary_key=True)
    record_id: Mapped[str] = mapped_column(
    String,
    ForeignKey("data_records.record_id"),
    index=True,
)
    video_type: Mapped[str] = mapped_column(String)

    duration_seconds: Mapped[float] = mapped_column(Float)
    recorded_at: Mapped[datetime] = mapped_column(DateTime)

    session_id: Mapped[str] = mapped_column(String)
    location_id: Mapped[str] = mapped_column(String)
    capture_device: Mapped[str] = mapped_column(String)
    resolution: Mapped[str] = mapped_column(String)

    frame_rate_fps: Mapped[float] = mapped_column(Float)
    file_size_mb: Mapped[float] = mapped_column(Float)

    file_format: Mapped[str] = mapped_column(String)
    file_path: Mapped[str] = mapped_column(String)
    checksum: Mapped[str] = mapped_column(String)

    original_preserved: Mapped[bool] = mapped_column(Boolean)

    ai_processing_status: Mapped[str] = mapped_column(String)
    ai_processed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    ai_model_version: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )
    ai_confidence_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    requires_human_review: Mapped[bool] = mapped_column(Boolean)
    review_reason: Mapped[str] = mapped_column(String)
    human_review_status: Mapped[str] = mapped_column(String)

    reviewed_by: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    review_notes: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    analysis_approved: Mapped[bool] = mapped_column(Boolean)
    approved_by: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )


class VideoAnalysisJobDB(Base):
    __tablename__ = "video_analysis_jobs"

    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    video_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("videos.video_id"),
        index=True,
    )
    analysis_type: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    progress_percent: Mapped[float] = mapped_column(Float)
    attempt_count: Mapped[int] = mapped_column(Integer)
    max_attempts: Mapped[int] = mapped_column(Integer)
    model_name: Mapped[str | None] = mapped_column(String, nullable=True)
    model_version: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )
    result_path: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    review_status: Mapped[str] = mapped_column(
        String,
        default="pending",
        server_default="pending",
    )
    reviewed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    review_notes: Mapped[str | None] = mapped_column(String, nullable=True)
    target_track_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )


class DataRecordDB(Base):
    __tablename__ = "data_records"

    record_id: Mapped[str] = mapped_column(String, primary_key=True)

    player_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("players.player_id"),
        index=True,
    )

    source_type: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    data_type: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)

    original_file_path: Mapped[str] = mapped_column(String)

    analysis_id: Mapped[str] = mapped_column(String)

    schema_version: Mapped[str] = mapped_column(String)
    created_by: Mapped[str] = mapped_column(String)


class UserDB(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(
        String,
        unique=True,
        index=True,
    )
    password_hash: Mapped[str] = mapped_column(String)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    role: Mapped[str] = mapped_column(String, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    feature_permissions: Mapped[list | None] = mapped_column(
        JSON,
        nullable=True,
    )
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    avatar_filename: Mapped[str | None] = mapped_column(String, nullable=True)


class AuthSessionDB(Base):
    __tablename__ = "auth_sessions"

    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.user_id"),
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(
        String,
        unique=True,
        index=True,
    )
    csrf_token: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)


class PasswordResetCodeDB(Base):
    __tablename__ = "password_reset_codes"

    reset_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.user_id"),
        index=True,
    )
    code_hash: Mapped[str] = mapped_column(String)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class GuardianConsentDB(Base):
    __tablename__ = "guardian_consents"

    consent_id: Mapped[str] = mapped_column(String, primary_key=True)
    player_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("players.player_id"),
        index=True,
    )
    guardian_name: Mapped[str] = mapped_column(String)
    guardian_email: Mapped[str] = mapped_column(String)
    verification_method: Mapped[str] = mapped_column(String)
    purposes: Mapped[list] = mapped_column(JSON)
    granted_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        index=True,
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    recorded_by_user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.user_id"),
        index=True,
    )


class MLDatasetEntryDB(Base):
    """A staff-flagged candidate source video for the real football ML
    training dataset (see ml/DATASET_CARD.md). Flagging only registers the
    video as a candidate — it stays "pending_review" until a named reviewer
    confirms consent coverage for every child visible in the footage (not
    just the one player the video's record is officially linked to) and
    marks it "approved". Frame extraction and annotation happen separately,
    outside this app, per ml/ANNOTATION_GUIDE.md.
    """

    __tablename__ = "ml_dataset_entries"

    entry_id: Mapped[str] = mapped_column(String, primary_key=True)
    video_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("videos.video_id"),
        unique=True,
        index=True,
    )
    team_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("teams.team_id"),
        nullable=True,
    )
    age_band: Mapped[str] = mapped_column(String)
    sex_cohort: Mapped[str] = mapped_column(String)
    camera_id: Mapped[str] = mapped_column(String)
    lighting: Mapped[str] = mapped_column(String)
    consent_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("guardian_consents.consent_id"),
    )
    status: Mapped[str] = mapped_column(String, default="pending_review")
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    flagged_by_user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.user_id"),
    )
    flagged_at: Mapped[datetime] = mapped_column(DateTime)
    reviewed_by_user_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("users.user_id"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )


class SubscriptionDB(Base):
    """A recurring Stripe subscription paying for one enrolled player.

    Card details never touch this app — Stripe Checkout (a Stripe-hosted
    page) collects them directly, and this row is created/updated only
    from signature-verified Stripe webhook events.
    """

    __tablename__ = "subscriptions"

    # Stripe's own subscription id (e.g. "sub_...") — reused directly as our
    # primary key rather than minting a separate id, since every row here
    # is created/updated from a Stripe webhook event, not a user action.
    stripe_subscription_id: Mapped[str] = mapped_column(String, primary_key=True)
    player_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("players.player_id"),
        index=True,
    )
    paying_user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.user_id"),
        index=True,
    )
    stripe_customer_id: Mapped[str] = mapped_column(String, index=True)
    stripe_price_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class AuditEventDB(Base):
    __tablename__ = "audit_events"

    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    actor_user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.user_id"),
        index=True,
    )
    action: Mapped[str] = mapped_column(String, index=True)
    resource_type: Mapped[str] = mapped_column(String, index=True)
    resource_id: Mapped[str] = mapped_column(String, index=True)
    details: Mapped[dict] = mapped_column(JSON)


class GuardianPlayerLinkDB(Base):
    __tablename__ = "guardian_player_links"

    guardian_user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.user_id"),
        primary_key=True,
    )
    player_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("players.player_id"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime)
    created_by_user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.user_id"),
        index=True,
    )


class PrivacyRequestDB(Base):
    __tablename__ = "privacy_requests"

    request_id: Mapped[str] = mapped_column(String, primary_key=True)
    guardian_user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.user_id"),
        index=True,
    )
    player_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("players.player_id"),
        index=True,
    )
    request_type: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, index=True)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    reviewed_by_user_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("users.user_id"),
        nullable=True,
        index=True,
    )
    review_notes: Mapped[str | None] = mapped_column(String, nullable=True)


class DrillDB(Base):
    __tablename__ = "drills"

    drill_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String, index=True)
    description: Mapped[str] = mapped_column(String)

    min_age: Mapped[int] = mapped_column(Integer)
    max_age: Mapped[int] = mapped_column(Integer)

    difficulty: Mapped[str] = mapped_column(String)
    duration_minutes: Mapped[int] = mapped_column(Integer)

    equipment: Mapped[list] = mapped_column(JSON)
    video_url: Mapped[str] = mapped_column(String)

    active: Mapped[bool] = mapped_column(Boolean)


class TrainingPlanDB(Base):
    __tablename__ = "training_plans"

    plan_id: Mapped[str] = mapped_column(String, primary_key=True)

    player_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("players.player_id"),
        index=True,
    )
    analysis_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("analyses.analysis_id"),
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String, index=True)
    player_difficulty: Mapped[str] = mapped_column(String)
    target_duration: Mapped[int] = mapped_column(Integer)

    available_equipment: Mapped[list] = mapped_column(JSON)
    recommendations: Mapped[list] = mapped_column(JSON)



class TeamDB(Base):
    __tablename__ = "teams"

    team_id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
    )
    name: Mapped[str] = mapped_column(String)
    age_group: Mapped[str] = mapped_column(
        String,
        index=True,
    )
    coach_name: Mapped[str] = mapped_column(String)
    season_id: Mapped[str] = mapped_column(
        String,
        index=True,
    )
    active: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class IdCounterDB(Base):
    __tablename__ = "id_counters"

    entity: Mapped[str] = mapped_column(String, primary_key=True)
    next_value: Mapped[int] = mapped_column(Integer, nullable=False)


class SeasonDB(Base):
    __tablename__ = "seasons"

    season_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean)


class NotificationDB(Base):
    __tablename__ = "notifications"

    notification_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.user_id"),
        index=True,
    )
    type: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(String)
    link: Mapped[str | None] = mapped_column(String, nullable=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class MessageDB(Base):
    __tablename__ = "messages"

    message_id: Mapped[str] = mapped_column(String, primary_key=True)
    sender_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("users.user_id"),
        nullable=True,
    )
    recipient_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.user_id"),
        index=True,
    )
    subject: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(String)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class AssessmentRegistrationDB(Base):
    __tablename__ = "assessment_registrations"

    registration_id: Mapped[str] = mapped_column(String, primary_key=True)
    parent_name: Mapped[str] = mapped_column(String)
    parent_email: Mapped[str] = mapped_column(String, index=True)
    parent_phone: Mapped[str] = mapped_column(String)
    emergency_contact: Mapped[str] = mapped_column(String)
    player_name: Mapped[str] = mapped_column(String)
    player_date_of_birth: Mapped[date] = mapped_column(Date)
    player_age: Mapped[int] = mapped_column(Integer)
    preferred_position: Mapped[str | None] = mapped_column(String, nullable=True)
    experience_level: Mapped[str | None] = mapped_column(String, nullable=True)
    current_team: Mapped[str | None] = mapped_column(String, nullable=True)
    consents: Mapped[dict] = mapped_column(JSON)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class ContactMessageDB(Base):
    __tablename__ = "contact_messages"

    message_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    email: Mapped[str] = mapped_column(String, index=True)
    topic: Mapped[str] = mapped_column(String)
    message: Mapped[str] = mapped_column(String)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, index=True)
