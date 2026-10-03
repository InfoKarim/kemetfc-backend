"""V1 tables. All ``v1_``; every foreign key is RESTRICT (history is never cascaded away).

Append-only: a saved trial, manual measurement or correction cannot be updated or
deleted through the ORM (see the listeners at the bottom). The privacy service is the
only code that removes V1 rows, with bulk deletes, during an approved privacy request.

There is deliberately no score, rating, confidence, model or AI column anywhere here,
and no float column: official values are integer centiseconds.
"""
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, object_session

from app.database import Base


class V1ImmutableRecordError(RuntimeError):
    """A saved V1 record cannot be changed or deleted; add a correction instead."""


class V1MetricDefinitionDB(Base):
    """Versioned meaning of a metric ("20 m sprint time" always means one thing)."""

    __tablename__ = "v1_metric_definitions"

    metric_id: Mapped[str] = mapped_column(String, primary_key=True)
    definition_version: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    domain: Mapped[str] = mapped_column(String)
    station_id: Mapped[str] = mapped_column(String)
    metric_class: Mapped[str] = mapped_column(String)
    measurement_type: Mapped[str] = mapped_column(String)
    unit: Mapped[str] = mapped_column(String)
    measurement_source: Mapped[str] = mapped_column(String)
    protocol_code: Mapped[str] = mapped_column(String)
    start_event: Mapped[str] = mapped_column(String)
    finish_event: Mapped[str] = mapped_column(String)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    definition_ref: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime)

    __table_args__ = (
        CheckConstraint("station_id IN ('S1','S2','S3','S4','S5','S6')", name="ck_v1_metric_def_station"),
        CheckConstraint("domain IN ('PHYSICAL','TECHNICAL')", name="ck_v1_metric_def_domain"),
        CheckConstraint("metric_class IN ('MANUAL_MEASUREMENT')", name="ck_v1_metric_def_class"),
        CheckConstraint("measurement_type IN ('DURATION')", name="ck_v1_metric_def_type"),
        CheckConstraint("unit IN ('seconds')", name="ck_v1_metric_def_unit"),
        CheckConstraint("measurement_source IN ('HUMAN_MANUAL')", name="ck_v1_metric_def_source"),
    )


class V1AssessmentDB(Base):
    """One assessment occasion for one player. A player can have many over time."""

    __tablename__ = "v1_assessments"

    assessment_id: Mapped[str] = mapped_column(String, primary_key=True)
    player_id: Mapped[str] = mapped_column(
        String, ForeignKey("players.player_id", ondelete="RESTRICT"), index=True
    )
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    assessment_date: Mapped[date] = mapped_column(Date)
    protocol_code: Mapped[str] = mapped_column(String)
    standard_version: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="IN_PROGRESS")
    created_by_user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.user_id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime)
    client_entry_id: Mapped[str | None] = mapped_column(String, nullable=True, unique=True)

    __table_args__ = (
        CheckConstraint("status IN ('IN_PROGRESS','COMPLETE','CANCELLED')", name="ck_v1_assessment_status"),
    )


class V1StationRunDB(Base):
    """One station inside an assessment. Holds no score."""

    __tablename__ = "v1_station_runs"

    station_run_id: Mapped[str] = mapped_column(String, primary_key=True)
    assessment_id: Mapped[str] = mapped_column(
        String, ForeignKey("v1_assessments.assessment_id", ondelete="RESTRICT"), index=True
    )
    station_id: Mapped[str] = mapped_column(String)
    created_by_user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.user_id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime)

    __table_args__ = (
        UniqueConstraint("assessment_id", "station_id", name="uq_v1_station_run_station"),
        CheckConstraint("station_id IN ('S1','S2','S3','S4','S5','S6')", name="ck_v1_station_run_station"),
    )


class V1TrialDB(Base):
    """One attempt. ``validity`` is about the child's performance under the protocol;
    a timer failure is never recorded here (it is a NOT_RECORDED measurement)."""

    __tablename__ = "v1_trials"

    trial_id: Mapped[str] = mapped_column(String, primary_key=True)
    station_run_id: Mapped[str] = mapped_column(
        String, ForeignKey("v1_station_runs.station_run_id", ondelete="RESTRICT"), index=True
    )
    series_key: Mapped[str] = mapped_column(String, default="")
    attempt_number: Mapped[int] = mapped_column(Integer)
    validity: Mapped[str] = mapped_column(String, default="VALID")
    invalid_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by_user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.user_id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime)

    __table_args__ = (
        UniqueConstraint("station_run_id", "series_key", "attempt_number", name="uq_v1_trial_attempt"),
        CheckConstraint("attempt_number >= 1", name="ck_v1_trial_attempt_number"),
        CheckConstraint("validity IN ('VALID','INVALID')", name="ck_v1_trial_validity"),
        CheckConstraint(
            "(validity = 'INVALID' AND invalid_reason IS NOT NULL) "
            "OR (validity = 'VALID' AND invalid_reason IS NULL)",
            name="ck_v1_trial_invalid_reason",
        ),
    )


class V1ManualMeasurementDB(Base):
    """A human-entered stopwatch time. First-class: no confidence, model or AI fields.

    ``value_centiseconds`` is NULL only when the timing failed (NOT_RECORDED); no
    placeholder number is ever stored."""

    __tablename__ = "v1_manual_measurements"

    manual_measurement_id: Mapped[str] = mapped_column(String, primary_key=True)
    trial_id: Mapped[str] = mapped_column(
        String, ForeignKey("v1_trials.trial_id", ondelete="RESTRICT"), index=True
    )
    metric_id: Mapped[str] = mapped_column(String)
    metric_definition_version: Mapped[str] = mapped_column(String)
    entry_status: Mapped[str] = mapped_column(String)
    value_centiseconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit: Mapped[str] = mapped_column(String, default="seconds")
    not_recorded_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    measurement_source: Mapped[str] = mapped_column(String, default="HUMAN_MANUAL")
    measurement_method: Mapped[str] = mapped_column(String, default="MANUAL_STOPWATCH")
    entered_by_user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.user_id", ondelete="RESTRICT")
    )
    entered_at: Mapped[datetime] = mapped_column(DateTime)
    client_entry_id: Mapped[str | None] = mapped_column(String, nullable=True, unique=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["metric_id", "metric_definition_version"],
            ["v1_metric_definitions.metric_id", "v1_metric_definitions.definition_version"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("trial_id", "metric_id", name="uq_v1_manual_measurement_trial_metric"),
        CheckConstraint("entry_status IN ('RECORDED','NOT_RECORDED')", name="ck_v1_mm_entry_status"),
        CheckConstraint(
            "(entry_status = 'RECORDED' AND value_centiseconds IS NOT NULL "
            "AND value_centiseconds > 0 AND not_recorded_reason IS NULL) "
            "OR (entry_status = 'NOT_RECORDED' AND value_centiseconds IS NULL "
            "AND not_recorded_reason IS NOT NULL)",
            name="ck_v1_mm_value_or_reason",
        ),
        CheckConstraint("unit = 'seconds'", name="ck_v1_mm_unit"),
        CheckConstraint("measurement_source = 'HUMAN_MANUAL'", name="ck_v1_mm_source"),
        CheckConstraint("measurement_method = 'MANUAL_STOPWATCH'", name="ck_v1_mm_method"),
    )


class V1ManualMeasurementCorrectionDB(Base):
    """Append-only correction of a manual measurement. The measurement row keeps the
    original value; the effective value is the latest correction's value."""

    __tablename__ = "v1_manual_measurement_corrections"

    correction_id: Mapped[str] = mapped_column(String, primary_key=True)
    manual_measurement_id: Mapped[str] = mapped_column(
        String, ForeignKey("v1_manual_measurements.manual_measurement_id", ondelete="RESTRICT")
    )
    sequence_number: Mapped[int] = mapped_column(Integer)
    original_centiseconds: Mapped[int] = mapped_column(Integer)
    corrected_centiseconds: Mapped[int] = mapped_column(Integer)
    reason_code: Mapped[str] = mapped_column(String)
    reason_text: Mapped[str | None] = mapped_column(String, nullable=True)
    corrected_by_user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.user_id", ondelete="RESTRICT")
    )
    corrected_at: Mapped[datetime] = mapped_column(DateTime)
    confirmed_by_user_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("manual_measurement_id", "sequence_number", name="uq_v1_correction_sequence"),
        Index("ix_v1_corrections_measurement", "manual_measurement_id"),
        CheckConstraint("sequence_number >= 1", name="ck_v1_correction_sequence"),
        CheckConstraint("original_centiseconds > 0 AND corrected_centiseconds > 0", name="ck_v1_correction_values"),
        CheckConstraint("original_centiseconds <> corrected_centiseconds", name="ck_v1_correction_changes"),
        CheckConstraint(
            "reason_code IN ('DECIMAL_SLIP','TYPING_ERROR','MISREAD_DISPLAY','WRONG_TRIAL','OTHER')",
            name="ck_v1_correction_reason",
        ),
        CheckConstraint(
            "confirmed_by_user_id IS NULL OR confirmed_by_user_id <> corrected_by_user_id",
            name="ck_v1_correction_confirmer",
        ),
    )


def _refuse_update(_mapper, _connection, target):
    session = object_session(target)
    if session is not None and session.is_modified(target, include_collections=False):
        raise V1ImmutableRecordError(
            f"{type(target).__name__} is append-only; record a correction instead of editing it"
        )


def _refuse_delete(_mapper, _connection, target):
    raise V1ImmutableRecordError(
        f"{type(target).__name__} cannot be deleted by normal operations; "
        "only an approved privacy deletion removes V1 data"
    )


for _model in (V1TrialDB, V1ManualMeasurementDB, V1ManualMeasurementCorrectionDB):
    event.listen(_model, "before_update", _refuse_update)
    event.listen(_model, "before_delete", _refuse_delete)
for _model in (V1AssessmentDB, V1StationRunDB, V1MetricDefinitionDB):
    event.listen(_model, "before_delete", _refuse_delete)
