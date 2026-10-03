"""add V1 assessment data foundation (manual timing)

Revision ID: d4b7e9a1c635
Revises: c9e1a4b7d302

Additive only: six new v1_ tables and three seeded metric-definition rows. No legacy
table is read, altered or converted. The seed below is a literal copy on purpose, so
this migration stays deterministic even if app/v1/definitions.py changes later (a test
keeps the two identical for the current version).
"""
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4b7e9a1c635"
down_revision: Union[str, Sequence[str], None] = "c9e1a4b7d302"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SEED_AT = datetime(2026, 10, 3, 0, 0, 0)
_SEED_COMMON = dict(
    definition_version="1.0.0", metric_class="MANUAL_MEASUREMENT", measurement_type="DURATION",
    unit="seconds", measurement_source="HUMAN_MANUAL", protocol_code="KEMET_V1",
    is_active=True, created_at=_SEED_AT,
)
_SEED = [
    dict(metric_id="PHYS-SPR-020M-MAN-001", name="20 m sprint time (manual stopwatch)",
         domain="PHYSICAL", station_id="S1", start_event="SIGNAL",
         finish_event="TORSO_CROSSES_20M_LINE", definition_ref="Standard 4.1, 5.3, 5.4",
         **_SEED_COMMON),
    dict(metric_id="PHYS-COD-180-MAN-001",
         name="180 degree change-of-direction time (manual stopwatch)",
         domain="PHYSICAL", station_id="S2", start_event="TORSO_CROSSES_T_OUTBOUND",
         finish_event="TORSO_CROSSES_T_RETURN", definition_ref="Standard 4.1, 5.4",
         **_SEED_COMMON),
    dict(metric_id="TECH-DRB-SLALOM-TIME-MAN-001",
         name="Slalom dribbling completion time (manual stopwatch)",
         domain="TECHNICAL", station_id="S4", start_event="SIGNAL",
         finish_event="TORSO_CROSSES_FINISH_LINE_BALL_WITHIN_1_5M",
         definition_ref="Standard 4.1, 5.4", **_SEED_COMMON),
]


def upgrade() -> None:
    definitions = op.create_table(
        "v1_metric_definitions",
        sa.Column("metric_id", sa.String(), nullable=False),
        sa.Column("definition_version", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("station_id", sa.String(), nullable=False),
        sa.Column("metric_class", sa.String(), nullable=False),
        sa.Column("measurement_type", sa.String(), nullable=False),
        sa.Column("unit", sa.String(), nullable=False),
        sa.Column("measurement_source", sa.String(), nullable=False),
        sa.Column("protocol_code", sa.String(), nullable=False),
        sa.Column("start_event", sa.String(), nullable=False),
        sa.Column("finish_event", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("definition_ref", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("metric_id", "definition_version"),
        sa.CheckConstraint("station_id IN ('S1','S2','S3','S4','S5','S6')", name="ck_v1_metric_def_station"),
        sa.CheckConstraint("domain IN ('PHYSICAL','TECHNICAL')", name="ck_v1_metric_def_domain"),
        sa.CheckConstraint("metric_class IN ('MANUAL_MEASUREMENT')", name="ck_v1_metric_def_class"),
        sa.CheckConstraint("measurement_type IN ('DURATION')", name="ck_v1_metric_def_type"),
        sa.CheckConstraint("unit IN ('seconds')", name="ck_v1_metric_def_unit"),
        sa.CheckConstraint("measurement_source IN ('HUMAN_MANUAL')", name="ck_v1_metric_def_source"),
    )
    op.bulk_insert(definitions, _SEED)

    op.create_table(
        "v1_assessments",
        sa.Column("assessment_id", sa.String(), primary_key=True),
        sa.Column("player_id", sa.String(), sa.ForeignKey("players.player_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("assessment_date", sa.Date(), nullable=False),
        sa.Column("protocol_code", sa.String(), nullable=False),
        sa.Column("standard_version", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_by_user_id", sa.String(), sa.ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("client_entry_id", sa.String(), nullable=True, unique=True),
        sa.CheckConstraint("status IN ('IN_PROGRESS','COMPLETE','CANCELLED')", name="ck_v1_assessment_status"),
    )
    op.create_index("ix_v1_assessments_player_id", "v1_assessments", ["player_id"])

    op.create_table(
        "v1_station_runs",
        sa.Column("station_run_id", sa.String(), primary_key=True),
        sa.Column("assessment_id", sa.String(), sa.ForeignKey("v1_assessments.assessment_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("station_id", sa.String(), nullable=False),
        sa.Column("created_by_user_id", sa.String(), sa.ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("assessment_id", "station_id", name="uq_v1_station_run_station"),
        sa.CheckConstraint("station_id IN ('S1','S2','S3','S4','S5','S6')", name="ck_v1_station_run_station"),
    )
    op.create_index("ix_v1_station_runs_assessment_id", "v1_station_runs", ["assessment_id"])

    op.create_table(
        "v1_trials",
        sa.Column("trial_id", sa.String(), primary_key=True),
        sa.Column("station_run_id", sa.String(), sa.ForeignKey("v1_station_runs.station_run_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("series_key", sa.String(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("validity", sa.String(), nullable=False),
        sa.Column("invalid_reason", sa.String(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), sa.ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("station_run_id", "series_key", "attempt_number", name="uq_v1_trial_attempt"),
        sa.CheckConstraint("attempt_number >= 1", name="ck_v1_trial_attempt_number"),
        sa.CheckConstraint("validity IN ('VALID','INVALID')", name="ck_v1_trial_validity"),
        sa.CheckConstraint(
            "(validity = 'INVALID' AND invalid_reason IS NOT NULL) "
            "OR (validity = 'VALID' AND invalid_reason IS NULL)",
            name="ck_v1_trial_invalid_reason",
        ),
    )
    op.create_index("ix_v1_trials_station_run_id", "v1_trials", ["station_run_id"])

    op.create_table(
        "v1_manual_measurements",
        sa.Column("manual_measurement_id", sa.String(), primary_key=True),
        sa.Column("trial_id", sa.String(), sa.ForeignKey("v1_trials.trial_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("metric_id", sa.String(), nullable=False),
        sa.Column("metric_definition_version", sa.String(), nullable=False),
        sa.Column("entry_status", sa.String(), nullable=False),
        sa.Column("value_centiseconds", sa.Integer(), nullable=True),
        sa.Column("unit", sa.String(), nullable=False),
        sa.Column("not_recorded_reason", sa.String(), nullable=True),
        sa.Column("measurement_source", sa.String(), nullable=False),
        sa.Column("measurement_method", sa.String(), nullable=False),
        sa.Column("entered_by_user_id", sa.String(), sa.ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("entered_at", sa.DateTime(), nullable=False),
        sa.Column("client_entry_id", sa.String(), nullable=True, unique=True),
        sa.ForeignKeyConstraint(
            ["metric_id", "metric_definition_version"],
            ["v1_metric_definitions.metric_id", "v1_metric_definitions.definition_version"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("trial_id", "metric_id", name="uq_v1_manual_measurement_trial_metric"),
        sa.CheckConstraint("entry_status IN ('RECORDED','NOT_RECORDED')", name="ck_v1_mm_entry_status"),
        sa.CheckConstraint(
            "(entry_status = 'RECORDED' AND value_centiseconds IS NOT NULL "
            "AND value_centiseconds > 0 AND not_recorded_reason IS NULL) "
            "OR (entry_status = 'NOT_RECORDED' AND value_centiseconds IS NULL "
            "AND not_recorded_reason IS NOT NULL)",
            name="ck_v1_mm_value_or_reason",
        ),
        sa.CheckConstraint("unit = 'seconds'", name="ck_v1_mm_unit"),
        sa.CheckConstraint("measurement_source = 'HUMAN_MANUAL'", name="ck_v1_mm_source"),
        sa.CheckConstraint("measurement_method = 'MANUAL_STOPWATCH'", name="ck_v1_mm_method"),
    )
    op.create_index("ix_v1_manual_measurements_trial_id", "v1_manual_measurements", ["trial_id"])

    op.create_table(
        "v1_manual_measurement_corrections",
        sa.Column("correction_id", sa.String(), primary_key=True),
        sa.Column("manual_measurement_id", sa.String(), sa.ForeignKey("v1_manual_measurements.manual_measurement_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("original_centiseconds", sa.Integer(), nullable=False),
        sa.Column("corrected_centiseconds", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(), nullable=False),
        sa.Column("reason_text", sa.String(), nullable=True),
        sa.Column("corrected_by_user_id", sa.String(), sa.ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("corrected_at", sa.DateTime(), nullable=False),
        sa.Column("confirmed_by_user_id", sa.String(), sa.ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=True),
        sa.UniqueConstraint("manual_measurement_id", "sequence_number", name="uq_v1_correction_sequence"),
        sa.CheckConstraint("sequence_number >= 1", name="ck_v1_correction_sequence"),
        sa.CheckConstraint("original_centiseconds > 0 AND corrected_centiseconds > 0", name="ck_v1_correction_values"),
        sa.CheckConstraint("original_centiseconds <> corrected_centiseconds", name="ck_v1_correction_changes"),
        sa.CheckConstraint(
            "reason_code IN ('DECIMAL_SLIP','TYPING_ERROR','MISREAD_DISPLAY','WRONG_TRIAL','OTHER')",
            name="ck_v1_correction_reason",
        ),
        sa.CheckConstraint(
            "confirmed_by_user_id IS NULL OR confirmed_by_user_id <> corrected_by_user_id",
            name="ck_v1_correction_confirmer",
        ),
    )
    op.create_index("ix_v1_corrections_measurement", "v1_manual_measurement_corrections", ["manual_measurement_id"])


def downgrade() -> None:
    op.drop_table("v1_manual_measurement_corrections")
    op.drop_table("v1_manual_measurements")
    op.drop_table("v1_trials")
    op.drop_table("v1_station_runs")
    op.drop_table("v1_assessments")
    op.drop_table("v1_metric_definitions")
