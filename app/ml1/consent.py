"""Who may be learned from.

An example may enter a dataset only if every check here passes, and the checks run again every time a dataset is
built, split or trained on (a consent can be withdrawn or expire after an example was accepted). Real children's
videos need verified, unexpired, unwithdrawn guardian consent for the purpose `ml_training`, a named reviewer who
confirmed that the consent covers EVERY child visible in the footage, and a dataset entry marked approved. Third-party
content (for example a social-media clip) is never eligible, whatever consent is claimed. Synthetic fixtures are
allowed only to test the pipeline and can never lead to an approved model.
"""
import hashlib
import hmac
import re
from datetime import datetime, timezone

from app.ml1.config import CONSENT_PURPOSE, REAL_SOURCES, SCHEMA_VERSION, SOURCES, SYNTHETIC_SOURCE

SYNTHETIC_CONSENT_KIND = "NOT_REQUIRED_SYNTHETIC"
GUARDIAN_CONSENT_KIND = "GUARDIAN_ML_TRAINING"

# Sources that must never be learned from, by recording id or by the SHA-256 of the video file. Checked before
# anything else, so a fabricated consent record cannot let one through.
BLOCKED_SOURCES = {
    "recording_ids": {
        "7228B688-F84E-486E-80BA-34D826914785": "imported social-media reel: third-party content showing other children; "
                                                "a target-selection stress test only",
        "EA8BCAB9-3A61-4FAC-943F-52889A552D8C": "a phone recording of a TV drama: not football, not a consented player; "
                                                "a pipeline test asset only",
    },
    "video_sha256": {
        "429b45774f22ea21be8f49f1ef4ba5df55c3fd0a45ce6579caace26ed8d609e3": "imported social-media reel (see recording id)",
        "d68d9be3617a5c01148c75ac8f835654d2d9b7e303f4d8582f97d9c1af0f1627": "TV-drama phone recording (see recording id)",
    },
}

_ID = re.compile(r"^AP-[0-9a-f]{16}$")
_REF = re.compile(r"^(CR|AC)-[0-9a-f]{16}$")


class ConsentError(ValueError):
    pass


def anonymize(raw_id: str, salt: bytes, prefix: str) -> str:
    """A stable pseudonym for an id, using a secret salt kept OUTSIDE Git. Without the salt the id cannot be
    recomputed from a roster; with it the same player always maps to the same pseudonym (needed to split by player)."""
    if not isinstance(salt, (bytes, bytearray)) or len(salt) < 16:
        raise ConsentError("a salt of at least 16 bytes is required to anonymize ids")
    if not raw_id:
        raise ConsentError("cannot anonymize an empty id")
    return f"{prefix}-" + hmac.new(bytes(salt), f"{prefix}:{raw_id}".encode(), hashlib.sha256).hexdigest()[:16]


def anonymous_player_id(raw_player_id: str, salt: bytes) -> str:
    return anonymize(raw_player_id, salt, "AP")


def consent_ref(raw_consent_id: str, salt: bytes) -> str:
    return anonymize(raw_consent_id, salt, "CR")


def coach_ref(raw_user_id: str, salt: bytes) -> str:
    return anonymize(raw_user_id, salt, "AC")


def _parse(value) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)


def blocked_reason(meta: dict) -> str | None:
    prov = meta.get("provenance", {})
    for key in ("recording_id", "assessment_id"):
        for value in (prov.get(key), meta.get(key)):
            if value and value in BLOCKED_SOURCES["recording_ids"]:
                return BLOCKED_SOURCES["recording_ids"][value]
    sha = (prov.get("source_video_sha256") or meta.get("source_video_sha256") or "").lower()
    return BLOCKED_SOURCES["video_sha256"].get(sha)


def eligibility_problems(meta: dict, now: datetime | None = None, withdrawn_refs: set[str] | None = None) -> list[str]:
    """Every reason this example may NOT be used for learning. An empty list means it may."""
    now = (now or datetime.now(timezone.utc).replace(tzinfo=None)).replace(tzinfo=None)
    problems: list[str] = []
    if meta.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"SCHEMA_VERSION_MISMATCH:{meta.get('schema_version')}")
    blocked = blocked_reason(meta)
    if blocked:
        problems.append("BLOCKED_SOURCE:" + blocked)
    prov = meta.get("provenance", {})
    source = prov.get("source")
    if source not in SOURCES:
        problems.append(f"UNKNOWN_SOURCE:{source}")
    if prov.get("third_party_content"):
        problems.append("THIRD_PARTY_CONTENT")
    if not _ID.match(str(meta.get("anonymous_player_id", ""))):
        problems.append("PLAYER_ID_NOT_ANONYMIZED")

    consent = meta.get("consent") or {}
    target = meta.get("target") or {}
    if source == SYNTHETIC_SOURCE:
        if consent.get("kind") != SYNTHETIC_CONSENT_KIND:
            problems.append("SYNTHETIC_EXAMPLE_MUST_DECLARE_SYNTHETIC_CONSENT_KIND")
    elif source in REAL_SOURCES:
        if consent.get("kind") != GUARDIAN_CONSENT_KIND:
            problems.append("NO_GUARDIAN_ML_TRAINING_CONSENT")
        else:
            if consent.get("purpose") != CONSENT_PURPOSE:
                problems.append("CONSENT_PURPOSE_NOT_ML_TRAINING")
            if not _REF.match(str(consent.get("consent_ref", ""))):
                problems.append("CONSENT_REFERENCE_MISSING")
            granted = _parse(consent.get("granted_at"))
            if granted is None or granted > now:
                problems.append("CONSENT_NOT_YET_GRANTED")
            if consent.get("withdrawn_at") not in (None, ""):
                problems.append("CONSENT_WITHDRAWN")
            if consent.get("consent_ref") in (withdrawn_refs or set()):
                problems.append("CONSENT_WITHDRAWN")
            expires = _parse(consent.get("expires_at"))
            if expires is not None and expires <= now:
                problems.append("CONSENT_EXPIRED")
            if consent.get("dataset_entry_status") != "approved":
                problems.append("DATASET_ENTRY_NOT_APPROVED")
            if consent.get("covers_all_visible_children") is not True:
                problems.append("CONSENT_NOT_VERIFIED_FOR_EVERY_VISIBLE_CHILD")
            if not _REF.match(str(consent.get("reviewed_by_ref", ""))):
                problems.append("NO_NAMED_CONSENT_REVIEWER")
        if target.get("identity_verified_by_coach") is not True or target.get("source") != "COACH_TAP":
            problems.append("TARGET_PLAYER_NOT_SELECTED_BY_COACH")
    for label in meta.get("labels", []):
        if source == SYNTHETIC_SOURCE and label.get("source") == "SYNTHETIC_GENERATOR" and label.get("status") == "SYNTHETIC_GROUND_TRUTH":
            continue            # a fixture knows what it generated; this is not a review and never counts as one
        if label.get("status") != "COACH_REVIEWED" or label.get("source") != "COACH" \
                or not _REF.match(str(label.get("reviewed_by_ref", ""))):
            problems.append("LABEL_NOT_COACH_REVIEWED:" + str(label.get("label_type")))
    return sorted(set(problems))


def assert_eligible(meta: dict, now: datetime | None = None, withdrawn_refs: set[str] | None = None) -> None:
    problems = eligibility_problems(meta, now, withdrawn_refs)
    if problems:
        raise ConsentError(f"example {meta.get('example_id')} is not eligible for learning: {', '.join(problems)}")
