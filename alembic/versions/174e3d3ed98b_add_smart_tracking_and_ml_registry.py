"""add smart tracking sessions/samples/events and ML model registry

Revision ID: 174e3d3ed98b
Revises: 748f0edfbe88

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "174e3d3ed98b"
down_revision: Union[str, Sequence[str], None] = "748f0edfbe88"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tracking_sessions",
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("player_id", sa.String(), nullable=False),
        sa.Column("coach_user_id", sa.String(), nullable=False),
        sa.Column("video_id", sa.String(), nullable=True),
        sa.Column("tracking_mode", sa.String(), nullable=False),
        sa.Column("gimbal_model", sa.String(), nullable=True),
        sa.Column("calibration_status", sa.String(), nullable=False),
        sa.Column("calibration_scale_m_per_unit", sa.Float(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.player_id"]),
        sa.ForeignKeyConstraint(["coach_user_id"], ["users.user_id"]),
        sa.ForeignKeyConstraint(["video_id"], ["videos.video_id"]),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index("ix_tracking_sessions_player_id", "tracking_sessions", ["player_id"])
    op.create_index("ix_tracking_sessions_coach_user_id", "tracking_sessions", ["coach_user_id"])
    op.create_index("ix_tracking_sessions_video_id", "tracking_sessions", ["video_id"])
    op.create_index("ix_tracking_sessions_status", "tracking_sessions", ["status"])
    op.create_index("ix_tracking_sessions_started_at", "tracking_sessions", ["started_at"])

    op.create_table(
        "tracking_samples",
        sa.Column("sample_id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("t_seconds", sa.Float(), nullable=False),
        sa.Column("player_bbox", sa.JSON(), nullable=True),
        sa.Column("player_center", sa.JSON(), nullable=True),
        sa.Column("player_confidence", sa.Float(), nullable=True),
        sa.Column("player_track_id", sa.Integer(), nullable=True),
        sa.Column("ball_bbox", sa.JSON(), nullable=True),
        sa.Column("ball_center", sa.JSON(), nullable=True),
        sa.Column("ball_confidence", sa.Float(), nullable=True),
        sa.Column("ball_track_id", sa.Integer(), nullable=True),
        sa.Column("pose_keypoints", sa.JSON(), nullable=True),
        sa.Column("gimbal_state", sa.String(), nullable=False),
        sa.Column("tracking_mode", sa.String(), nullable=False),
        sa.Column("tracking_status", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["tracking_sessions.session_id"]),
        sa.PrimaryKeyConstraint("sample_id"),
    )
    op.create_index("ix_tracking_samples_session_id", "tracking_samples", ["session_id"])

    op.create_table(
        "tracking_events",
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["tracking_sessions.session_id"]),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_tracking_events_session_id", "tracking_events", ["session_id"])
    op.create_index("ix_tracking_events_occurred_at", "tracking_events", ["occurred_at"])
    op.create_index("ix_tracking_events_event_type", "tracking_events", ["event_type"])

    op.create_table(
        "tracking_feature_sets",
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("feature_schema_version", sa.String(), nullable=False),
        sa.Column("features", sa.JSON(), nullable=False),
        sa.Column("computed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["tracking_sessions.session_id"]),
        sa.PrimaryKeyConstraint("session_id"),
    )

    op.create_table(
        "ml_model_registry",
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("model_version", sa.String(), nullable=False),
        sa.Column("model_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("deployment_date", sa.DateTime(), nullable=True),
        sa.Column("training_dataset_version", sa.String(), nullable=True),
        sa.Column("evaluation_metrics", sa.JSON(), nullable=True),
        sa.Column("coreml_artifact_version", sa.String(), nullable=True),
        sa.Column("backend_artifact_version", sa.String(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("model_id"),
    )
    op.create_index("ix_ml_model_registry_model_name", "ml_model_registry", ["model_name"])
    op.create_index("ix_ml_model_registry_status", "ml_model_registry", ["status"])

    op.create_table(
        "coach_validation_labels",
        sa.Column("label_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("label_type", sa.String(), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("coach_user_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["tracking_sessions.session_id"]),
        sa.ForeignKeyConstraint(["coach_user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("label_id"),
    )
    op.create_index("ix_coach_validation_labels_session_id", "coach_validation_labels", ["session_id"])
    op.create_index("ix_coach_validation_labels_label_type", "coach_validation_labels", ["label_type"])

    with op.batch_alter_table("player_assessments", schema=None) as batch_op:
        batch_op.add_column(sa.Column("tracking_session_id", sa.String(), nullable=True))
        batch_op.create_foreign_key(
            "fk_player_assessments_tracking_session_id_tracking_sessions",
            "tracking_sessions",
            ["tracking_session_id"],
            ["session_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("player_assessments", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_player_assessments_tracking_session_id_tracking_sessions",
            type_="foreignkey",
        )
        batch_op.drop_column("tracking_session_id")

    op.drop_index("ix_coach_validation_labels_label_type", table_name="coach_validation_labels")
    op.drop_index("ix_coach_validation_labels_session_id", table_name="coach_validation_labels")
    op.drop_table("coach_validation_labels")

    op.drop_index("ix_ml_model_registry_status", table_name="ml_model_registry")
    op.drop_index("ix_ml_model_registry_model_name", table_name="ml_model_registry")
    op.drop_table("ml_model_registry")

    op.drop_table("tracking_feature_sets")

    op.drop_index("ix_tracking_events_event_type", table_name="tracking_events")
    op.drop_index("ix_tracking_events_occurred_at", table_name="tracking_events")
    op.drop_index("ix_tracking_events_session_id", table_name="tracking_events")
    op.drop_table("tracking_events")

    op.drop_index("ix_tracking_samples_session_id", table_name="tracking_samples")
    op.drop_table("tracking_samples")

    op.drop_index("ix_tracking_sessions_started_at", table_name="tracking_sessions")
    op.drop_index("ix_tracking_sessions_status", table_name="tracking_sessions")
    op.drop_index("ix_tracking_sessions_video_id", table_name="tracking_sessions")
    op.drop_index("ix_tracking_sessions_coach_user_id", table_name="tracking_sessions")
    op.drop_index("ix_tracking_sessions_player_id", table_name="tracking_sessions")
    op.drop_table("tracking_sessions")
