from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

@dataclass
class DataRecord:
    record_id: str
    player_id: str
    source_type: str
    created_at: datetime
    data_type: str
    status: str
    original_file_path: str
    analysis_id: str
    schema_version: str
    created_by: str
    
    

    def __post_init__(self):
        valid_source_types = {
            "video",
            "sensor",
            "manual",
        }

        if self.source_type not in valid_source_types:
            raise ValueError(
                f"Invalid source_type: {self.source_type}"
            )

        valid_statuses = {
            "pending",
            "processing",
            "completed",
            "failed",
        }

        if self.status not in valid_statuses:
            raise ValueError(
                f"Invalid status: {self.status}"
            )

        required_fields = {
            "record_id": self.record_id,
            "player_id": self.player_id,
            "original_file_path": self.original_file_path,
            "schema_version": self.schema_version,
            "created_by": self.created_by,
        }

        for field_name, value in required_fields.items():
            if not value or not value.strip():
                raise ValueError(
                    f"{field_name} cannot be empty"
                )

@dataclass
class VideoData:
    video_id: str
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
    ai_processed_at: Optional[datetime]
    ai_model_version: Optional[str]
    ai_confidence_score: Optional[float]
    requires_human_review: bool
    review_reason: str
    human_review_status: str
    reviewed_by: Optional[str]
    reviewed_at: Optional[datetime]
    review_notes: Optional[str]
    analysis_approved: bool
    approved_by: Optional[str]
    approved_at: Optional[datetime]
    

    def __post_init__(self):
        if self.duration_seconds <= 0:
            raise ValueError(
                "duration_seconds must be greater than 0"
            )

        if self.frame_rate_fps <= 0:
            raise ValueError(
                "frame_rate_fps must be greater than 0"
            )

        if self.file_size_mb <= 0:
            raise ValueError(
                "file_size_mb must be greater than 0"
            )

        if self.ai_confidence_score is not None:
            if not 0.0 <= self.ai_confidence_score <= 1.0:
                raise ValueError(
                    "ai_confidence_score must be between 0.0 and 1.0"
                )

        valid_processing_statuses = {
            "pending",
            "processing",
            "completed",
            "failed",
        }

        if self.ai_processing_status not in valid_processing_statuses:
            raise ValueError(
                f"Invalid ai_processing_status: {self.ai_processing_status}"
            )

        if self.ai_processing_status in {"pending", "processing"}:
            if self.ai_processed_at is not None:
                raise ValueError(
                    "ai_processed_at must be None until processing "
                    "is completed"
                )

        if self.ai_processing_status == "completed":
            if self.ai_processed_at is None:
                raise ValueError(
                    "ai_processed_at is required when processing "
                    "is completed"
                )

        valid_review_statuses = {
            "not_required",
            "pending",
            "in_review",
            "completed",
        }

        if self.human_review_status not in valid_review_statuses:
            raise ValueError(
                f"Invalid human_review_status: {self.human_review_status}"
            )

        if self.requires_human_review:
            if self.human_review_status == "not_required":
                raise ValueError(
                    "human_review_status cannot be 'not_required' "
                    "when human review is required"
                )

        if not self.requires_human_review:
            if self.human_review_status != "not_required":
                raise ValueError(
                    "human_review_status must be 'not_required' "
                    "when human review is not required"
                )

        if self.human_review_status == "completed":
            if self.reviewed_by is None:
                raise ValueError(
                    "reviewed_by is required when human review "
                    "is completed"
                )

            if self.reviewed_at is None:
                raise ValueError(
                    "reviewed_at is required when human review "
                    "is completed"
                )

        if self.analysis_approved and self.approved_by is None:
            raise ValueError(
                "approved_by is required when analysis is approved"
            )

        if self.analysis_approved and self.approved_at is None:
            raise ValueError(
                "approved_at is required when analysis is approved"
            )


@dataclass
class VideoAnalysisJobData:
    job_id: str
    video_id: str
    analysis_type: str
    status: str
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    progress_percent: float
    attempt_count: int
    max_attempts: int
    model_name: Optional[str]
    model_version: Optional[str]
    result_path: Optional[str]
    error_message: Optional[str]
    review_status: str = "pending"
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    review_notes: Optional[str] = None
    target_track_id: Optional[int] = None

    def __post_init__(self):
        required_fields = {
            "job_id": self.job_id,
            "video_id": self.video_id,
            "analysis_type": self.analysis_type,
        }

        for field_name, value in required_fields.items():
            if not value or not value.strip():
                raise ValueError(f"{field_name} cannot be empty")

        valid_statuses = {
            "queued",
            "processing",
            "completed",
            "failed",
            "cancelled",
        }

        if self.status not in valid_statuses:
            raise ValueError(f"Invalid analysis job status: {self.status}")

        valid_review_statuses = {"pending", "approved", "rejected"}

        if self.review_status not in valid_review_statuses:
            raise ValueError(
                f"Invalid analysis review status: {self.review_status}"
            )

        if self.review_status in {"approved", "rejected"}:
            if not self.reviewed_by or not self.reviewed_by.strip():
                raise ValueError("reviewed_by is required after review")
            if self.reviewed_at is None:
                raise ValueError("reviewed_at is required after review")

        if not 0.0 <= self.progress_percent <= 100.0:
            raise ValueError("progress_percent must be between 0 and 100")

        if self.max_attempts <= 0:
            raise ValueError("max_attempts must be greater than 0")

        if not 0 <= self.attempt_count <= self.max_attempts:
            raise ValueError(
                "attempt_count must be between 0 and max_attempts"
            )

        if self.status == "queued":
            if self.started_at is not None or self.completed_at is not None:
                raise ValueError(
                    "queued jobs cannot have processing timestamps"
                )

        if self.status == "processing":
            if self.started_at is None:
                raise ValueError("started_at is required while processing")
            if self.completed_at is not None:
                raise ValueError(
                    "completed_at must be None while processing"
                )

        if self.status == "completed":
            if self.started_at is None or self.completed_at is None:
                raise ValueError(
                    "completed jobs require start and completion timestamps"
                )
            if self.progress_percent != 100.0:
                raise ValueError("completed jobs must have 100% progress")
            if not self.result_path:
                raise ValueError("result_path is required when completed")

        if self.status == "failed":
            if self.started_at is None or self.completed_at is None:
                raise ValueError(
                    "failed jobs require start and completion timestamps"
                )
            if not self.error_message:
                raise ValueError("error_message is required when failed")

        if self.status == "cancelled" and self.completed_at is None:
            raise ValueError("completed_at is required when cancelled")


@dataclass
class AIAnalysisRecord:
    analysis_id: str
    video_id: str
    player_id: str
    created_at: datetime
    analysis_type: str
    model_name: str
    model_version: str
    processing_status: str
    processed_at: Optional[datetime]
    confidence_score: Optional[float]
    overall_score: Optional[float]
    strengths: list
    weaknesses: list
    recommendations: list
    raw_output_path: Optional[str]
    requires_human_review: bool
    human_review_status: str
    reviewed_by: Optional[str]
    reviewed_at: Optional[datetime]
    review_notes: Optional[str]
    approved: bool
    approved_by: Optional[str]
    approved_at: Optional[datetime]
    def __post_init__(self):
        valid_statuses = {"pending", "processing", "completed", "failed"}

        if self.processing_status not in valid_statuses:
            raise ValueError(
                f"Invalid processing_status: {self.processing_status}"
            )

        if self.confidence_score is not None:
            if not 0.0 <= self.confidence_score <= 1.0:
                raise ValueError(
                    "confidence_score must be between 0.0 and 1.0"
                )

        for weakness in self.weaknesses:
            if isinstance(weakness, dict):
                score = weakness.get("score")
            elif isinstance(weakness, (list, tuple)) and len(weakness) == 2:
                score = weakness[1]
            else:
                raise ValueError(
                    "each weakness must contain an attribute and score"
                )

            if not isinstance(score, (int, float)) or not 0 <= score <= 100:
                raise ValueError(
                    "weakness score must be between 0 and 100"
                )

        if self.processing_status in {"pending", "processing"}:
            if self.processed_at is not None:
                raise ValueError(
                    "processed_at must be None until processing is completed"
                )

        if self.processing_status == "completed":
            if self.processed_at is None:
                raise ValueError(
                    "processed_at is required when processing is completed"
                )

        if self.approved and self.approved_at is None:
            raise ValueError(
                "approved_at is required when analysis is approved"
            )

        if self.approved and self.approved_by is None:
            raise ValueError(
                "approved_by is required when analysis is approved"
            )
        
        valid_review_statuses = {
            "not_required",
            "pending",
            "in_review",
            "completed",
        }

        if self.human_review_status not in valid_review_statuses:
            raise ValueError(
                f"Invalid human_review_status: {self.human_review_status}"
            )

        if self.requires_human_review:
            if self.human_review_status == "not_required":
                raise ValueError(
                    "human_review_status cannot be 'not_required' "
                    "when human review is required"
                )

        if not self.requires_human_review:
            if self.human_review_status != "not_required":
                raise ValueError(
                    "human_review_status must be 'not_required' "
                    "when human review is not required"
                )

        if self.human_review_status == "completed":
            if self.reviewed_by is None:
                raise ValueError(
                    "reviewed_by is required when human review is completed"
                )

            if self.reviewed_at is None:
                raise ValueError(
                    "reviewed_at is required when human review is completed"
                )

@dataclass
class MatchData:
    match_id: str
    competition_id: str
    season_id: str
    home_team_id: str
    away_team_id: str
    match_date: datetime
    venue_id: Optional[str]
    status: str
    home_score: Optional[int]
    away_score: Optional[int]

    def __post_init__(self):
        valid_statuses = {
            "scheduled",
            "in_progress",
            "completed",
            "cancelled",
            "postponed",
        }

        if self.status not in valid_statuses:
            raise ValueError(
                f"Invalid match status: {self.status}"
            )

        if self.home_team_id == self.away_team_id:
            raise ValueError(
                "home_team_id and away_team_id must be different"
            )

        if self.home_score is not None and self.home_score < 0:
            raise ValueError(
                "home_score cannot be negative"
            )

        if self.away_score is not None and self.away_score < 0:
            raise ValueError(
                "away_score cannot be negative"
            )

        if self.status == "scheduled":
            if self.home_score is not None or self.away_score is not None:
                raise ValueError(
                    "scheduled match cannot have a score"
                )

        if self.status == "completed":
            if self.home_score is None:
                raise ValueError(
                    "home_score is required for completed match"
                )

            if self.away_score is None:
                raise ValueError(
                    "away_score is required for completed match"
                )

@dataclass
class DrillData:
    drill_id: str
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

    def __post_init__(self):
        if not self.drill_id or not self.drill_id.strip():
            raise ValueError("drill_id cannot be empty")

        if not self.name or not self.name.strip():
            raise ValueError("name cannot be empty")

        if not self.category or not self.category.strip():
            raise ValueError("category cannot be empty")

        if self.min_age <= 0 or self.max_age <= 0:
            raise ValueError(
                "min_age and max_age must be greater than 0"
            )

        if self.min_age > self.max_age:
            raise ValueError(
                "min_age cannot be greater than max_age"
            )

        valid_difficulties = {
            "beginner",
            "intermediate",
            "advanced",
        }

        if self.difficulty not in valid_difficulties:
            raise ValueError(
                f"Invalid difficulty: {self.difficulty}"
            )

        if self.duration_minutes <= 0:
            raise ValueError(
                "duration_minutes must be greater than 0"
            )

        if not self.video_url or not self.video_url.strip():
            raise ValueError("video_url cannot be empty")

        video_url = self.video_url.strip()
        is_local_path = (
            video_url.startswith("/")
            and not video_url.startswith("//")
        )

        if not is_local_path:
            parsed_url = urlparse(video_url)

            if (
                parsed_url.scheme not in {"http", "https"}
                or not parsed_url.netloc
            ):
                raise ValueError(
                    "video_url must be a local path or an HTTP(S) URL"
                )


@dataclass
class TrainingPlanData:
    plan_id: str
    player_id: str
    analysis_id: str
    created_at: datetime
    status: str
    player_difficulty: str
    target_duration: int
    available_equipment: list[str]
    recommendations: list[dict]

    def __post_init__(self):
        required_ids = {
            "plan_id": self.plan_id,
            "player_id": self.player_id,
            "analysis_id": self.analysis_id,
        }

        for field_name, value in required_ids.items():
            if not value or not value.strip():
                raise ValueError(
                    f"{field_name} cannot be empty"
                )

        valid_statuses = {
            "draft",
            "active",
            "completed",
            "cancelled",
        }

        if self.status not in valid_statuses:
            raise ValueError(
                f"Invalid status: {self.status}"
            )

        valid_difficulties = {
            "beginner",
            "intermediate",
            "advanced",
        }

        if self.player_difficulty not in valid_difficulties:
            raise ValueError(
                f"Invalid player_difficulty: {self.player_difficulty}"
            )

        if self.target_duration <= 0:
            raise ValueError(
                "target_duration must be greater than 0"
            )



@dataclass
class TeamData:
    team_id: str
    name: str
    age_group: str
    coach_name: str
    season_id: str
    active: bool
    created_at: datetime | None = None

    def __post_init__(self):
        required_fields = {
            "team_id": self.team_id,
            "name": self.name,
            "age_group": self.age_group,
            "coach_name": self.coach_name,
            "season_id": self.season_id,
        }

        for field_name, value in required_fields.items():
            if not value or not value.strip():
                raise ValueError(
                    f"{field_name} cannot be empty"
                )

        normalized_age_group = self.age_group.strip().upper()

        if (
            not normalized_age_group.startswith("U")
            or not normalized_age_group[1:].isdigit()
            or int(normalized_age_group[1:]) <= 0
        ):
            raise ValueError(
                "age_group must use a format like U10"
            )
