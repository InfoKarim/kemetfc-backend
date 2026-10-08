"""Request bodies for the V1 manual-measurement API.

Every model forbids unknown fields, so a client cannot smuggle in a score, a confidence,
a model name, an age band, a unit or a source the server does not accept. Times are
decimal STRINGS ("4.25"); a JSON number is rejected so no binary float reaches the store.
"""
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateAssessmentBody(_Body):
    player_id: StrictStr = Field(min_length=1, max_length=128)
    assessment_date: date
    protocol_code: Literal["KEMET_V1"] = "KEMET_V1"
    title: StrictStr | None = Field(default=None, max_length=120)
    client_entry_id: StrictStr = Field(min_length=8, max_length=64)


class CreateStationRunBody(_Body):
    station_id: StrictStr = Field(min_length=1, max_length=8)


class CreateTrialBody(_Body):
    attempt_number: int = Field(ge=1, le=99, strict=True)
    # "" for a station without series; "L" or "R" for the turning foot at S2.
    series_key: Literal["", "L", "R"] = ""
    validity: Literal["VALID", "INVALID"] = "VALID"
    invalid_reason: StrictStr | None = Field(default=None, max_length=64)


class RecordMeasurementBody(_Body):
    metric_id: StrictStr = Field(min_length=1, max_length=64)
    entry_status: Literal["RECORDED", "NOT_RECORDED"] = "RECORDED"
    displayed_value: StrictStr | None = Field(default=None, max_length=16)
    not_recorded_reason: StrictStr | None = Field(default=None, max_length=64)
    # Optional echoes. They are never trusted: each must equal what the server fixes.
    measurement_source: StrictStr | None = Field(default=None, max_length=32)
    unit: StrictStr | None = Field(default=None, max_length=16)
    client_entry_id: StrictStr = Field(min_length=8, max_length=64)


class CorrectMeasurementBody(_Body):
    corrected_value: StrictStr = Field(max_length=16)
    # The value the caller saw as current. A stale caller gets 409 instead of
    # silently stacking a second correction on top of someone else's.
    expected_effective_value: StrictStr = Field(max_length=16)
    reason_code: Literal["DECIMAL_SLIP", "TYPING_ERROR", "MISREAD_DISPLAY", "WRONG_TRIAL", "OTHER"]
    reason_text: StrictStr | None = Field(default=None, max_length=200)
    confirmed_by_user_id: StrictStr = Field(min_length=1, max_length=128)


class SaveAttemptBody(_Body):
    """One attempt in one request: the unit the coach saves, and the unit the browser's offline
    queue replays. ``client_entry_id`` is the idempotency key; sending the same body again can
    never create a second attempt. A VALID attempt carries a time (RECORDED) or a NOT_RECORDED
    reason; a not-valid attempt carries a reason from the station's list and no time."""
    station_id: StrictStr = Field(min_length=1, max_length=8)
    series_key: Literal["", "L", "R"] = ""
    attempt_number: int = Field(ge=1, le=99, strict=True)
    validity: Literal["VALID", "INVALID"] = "VALID"
    invalid_reason: StrictStr | None = Field(default=None, max_length=64)
    entry_status: Literal["RECORDED", "NOT_RECORDED"] | None = None
    displayed_value: StrictStr | None = Field(default=None, max_length=16)
    not_recorded_reason: StrictStr | None = Field(default=None, max_length=64)
    client_entry_id: StrictStr = Field(min_length=8, max_length=64)
