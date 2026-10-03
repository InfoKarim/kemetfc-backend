"""Frozen V1 reference data. The migration seeds ``v1_metric_definitions`` from a
literal copy of ``METRIC_DEFINITIONS``; a test keeps the two identical.

Only manual-measurement metrics are defined in S1A. Observation and derived metrics
(cone contacts, touches, pass outcomes, ...) are added by a later slice as new
definition rows; the table already has the columns they need.
"""
from dataclasses import dataclass

PROTOCOL_CODE = "KEMET_V1"
STANDARD_VERSION = "KEMET-V1-MANUAL-TIMING-R2"
DEFINITION_VERSION = "1.0.0"

MEASUREMENT_SOURCE_MANUAL = "HUMAN_MANUAL"
MEASUREMENT_METHOD_MANUAL = "MANUAL_STOPWATCH"
UNIT_SECONDS = "seconds"

STATIONS = ("S1", "S2", "S3", "S4", "S5", "S6")

# Series inside a station run: S2 is timed per turning foot, S5/S6 are counted per foot.
# "" means the station has no series.
STATION_SERIES = {
    "S1": {""}, "S2": {"L", "R"}, "S3": {""}, "S4": {""}, "S5": {"L", "R"}, "S6": {"L", "R"},
}

ASSESSMENT_STATUSES = ("IN_PROGRESS", "COMPLETE", "CANCELLED")
TRIAL_VALIDITIES = ("VALID", "INVALID")
ENTRY_STATUSES = ("RECORDED", "NOT_RECORDED")

# Standard section 7. INVALID means the athlete broke the protocol.
INVALID_REASONS = {
    "S1": {"FALSE_START", "LANE_VIOLATION", "STOPPED_EARLY", "FALL_OR_TRIP", "INTERFERENCE"},
    "S2": {"FALSE_START", "TURNED_BEFORE_L", "FOOT_NOT_AT_L", "WRONG_TURN_SIDE",
           "FALL_OR_TRIP", "LANE_VIOLATION", "INTERFERENCE"},
    "S3": {"HANDS_USED", "WRONG_PATTERN", "FALSE_START"},
    "S4": {"MISSED_CONE", "HANDS_USED", "FALSE_START", "BALL_NOT_CONTROLLED_AT_START",
           "BALL_NOT_CONTROLLED_AT_FINISH", "INTERFERENCE", "FALL_OR_TRIP"},
    "S5": {"BALL_NOT_STATIONARY", "WRONG_FOOT", "MISHIT_SHORT", "INTERFERENCE"},
    "S6": {"BALL_NOT_STATIONARY", "WRONG_FOOT", "WRONG_MARK", "GOALKEEPER_PRESENT",
           "INTERFERENCE"},
}

# Standard section 5.8. NOT_RECORDED means the timing failed, never the child.
NOT_RECORDED_REASONS = (
    "TIMER_FAILURE", "TIMER_MISSED_STOP", "TIMER_RESET_EARLY", "TIMER_WRONG_EVENT",
)

# Standard section 6.3.
CORRECTION_REASONS = ("DECIMAL_SLIP", "TYPING_ERROR", "MISREAD_DISPLAY", "WRONG_TRIAL", "OTHER")

# Hard storage bound only (99 min 59.99 s, the largest mm:ss.hh stopwatch display).
# It is not a plausibility window and says nothing about how good a time is.
MAX_CENTISECONDS = 599_999


@dataclass(frozen=True)
class MetricDefinition:
    metric_id: str
    definition_version: str
    name: str
    domain: str
    station_id: str
    metric_class: str
    measurement_type: str
    unit: str
    measurement_source: str
    protocol_code: str
    start_event: str
    finish_event: str
    is_active: bool
    definition_ref: str


METRIC_DEFINITIONS = (
    MetricDefinition(
        "PHYS-SPR-020M-MAN-001", DEFINITION_VERSION, "20 m sprint time (manual stopwatch)",
        "PHYSICAL", "S1", "MANUAL_MEASUREMENT", "DURATION", UNIT_SECONDS,
        MEASUREMENT_SOURCE_MANUAL, PROTOCOL_CODE,
        "SIGNAL", "TORSO_CROSSES_20M_LINE", True, "Standard 4.1, 5.3, 5.4"),
    MetricDefinition(
        "PHYS-COD-180-MAN-001", DEFINITION_VERSION,
        "180 degree change-of-direction time (manual stopwatch)",
        "PHYSICAL", "S2", "MANUAL_MEASUREMENT", "DURATION", UNIT_SECONDS,
        MEASUREMENT_SOURCE_MANUAL, PROTOCOL_CODE,
        "TORSO_CROSSES_T_OUTBOUND", "TORSO_CROSSES_T_RETURN", True, "Standard 4.1, 5.4"),
    MetricDefinition(
        "TECH-DRB-SLALOM-TIME-MAN-001", DEFINITION_VERSION,
        "Slalom dribbling completion time (manual stopwatch)",
        "TECHNICAL", "S4", "MANUAL_MEASUREMENT", "DURATION", UNIT_SECONDS,
        MEASUREMENT_SOURCE_MANUAL, PROTOCOL_CODE,
        "SIGNAL", "TORSO_CROSSES_FINISH_LINE_BALL_WITHIN_1_5M", True, "Standard 4.1, 5.4"),
)

METRICS_BY_ID = {m.metric_id: m for m in METRIC_DEFINITIONS}


def ensure_metric_definitions(db) -> None:
    """Insert any missing definition row from the constants (used where the schema was
    built without migrations, such as tests). Production rows come from the migration.
    An existing row that differs from the constants is an error, never overwritten."""
    from app.v1.models import V1MetricDefinitionDB

    for definition in METRIC_DEFINITIONS:
        row = db.get(V1MetricDefinitionDB, (definition.metric_id, definition.definition_version))
        if row is None:
            from app.services.auth_service import utcnow
            db.add(V1MetricDefinitionDB(**definition.__dict__, created_at=utcnow()))
        else:
            stored = {key: getattr(row, key) for key in definition.__dict__}
            if stored != definition.__dict__:
                raise ValueError(f"Stored metric definition {definition.metric_id} differs from code")
    db.commit()
