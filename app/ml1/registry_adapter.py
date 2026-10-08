"""Trusted verification of consent against the platform's own records (read-only).

The `consent` block inside an example is a claim written by whoever built the example; it is NOT evidence. For real
(non-synthetic) data the only evidence is the platform's records: the guardian consent, the ML dataset entry a named
reviewer approved, and the registered video. This adapter looks those up fresh, every time it is asked, and answers
"no" whenever it cannot positively confirm: no record, a database error, a withdrawn or expired consent, a purpose that
is not ml_training, a dataset entry that is not approved, or an example whose pseudonyms do not match the records.

It never writes. It does not copy children's data out: it compares one-way pseudonyms (salted HMAC) computed from the
registry's ids with the pseudonyms stored in the example.

Consent of every child VISIBLE in the footage. The platform has no record of who appears in a video or of their consents: a
dataset entry is one video, ONE consent (the linked player's) and a general "approved" with a reviewer, and that general approval
is NOT accepted as proof that other children are covered. Real data therefore needs a separate trusted coverage record
(`CoverageRecord`, supplied by a `CoverageEvidence` source): for ONE video and ONE file version (its SHA-256) it enumerates the
consent of every person visible, with the reviewer who decided it and when. Every consent in it is checked again, now, for
withdrawal, expiry and purpose. No such record store exists in the platform today, so the default adapter has none and answers
`VISIBLE_CHILD_CONSENT_COVERAGE_NOT_VERIFIABLE` for every real example (fail closed). Nothing here accepts a boolean: an example's
own `covers_all_visible_children` field is never read, and a coverage source must return the enumerated consents themselves.
This code cannot see who is in the footage; a coverage record is only as good as the reviewer and the store behind it.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db_models import DataRecordDB, GuardianConsentDB, MLDatasetEntryDB, VideoDB
from app.ml1 import consent as c
from app.ml1.config import CONSENT_PURPOSE

REGISTRY_NOT_CONFIGURED = "REGISTRY_NOT_CONFIGURED_FAIL_CLOSED"
REGISTRY_UNAVAILABLE = "REGISTRY_UNAVAILABLE_FAIL_CLOSED"
COVERAGE_NOT_VERIFIABLE = "VISIBLE_CHILD_CONSENT_COVERAGE_NOT_VERIFIABLE"
COVERAGE_SCOPE = "ALL_VISIBLE_PERSONS_ENUMERATED"


@dataclass(frozen=True)
class CoverageRecord:
    """A trusted, per-video, per-file-version enumeration of the consent of every person visible. Ids are the registry's own."""
    video_id: str
    video_sha256: str
    scope: str                       # must be COVERAGE_SCOPE
    consent_ids: tuple[str, ...]     # the consent of EVERY visible person, including the linked player
    reviewed_by_user_id: str
    reviewed_at: datetime


class CoverageEvidence(Protocol):
    """A trusted store of CoverageRecords. It must read records written by a named reviewer through an audited process, never a value
    typed into an example or a flag. None means 'no record'."""

    def record_for_video(self, video_id: str) -> CoverageRecord | None: ...


class NoCoverageEvidence:
    """The default: the platform has no coverage records, so nothing can be proven."""

    def record_for_video(self, video_id: str) -> CoverageRecord | None:
        return None


class ConsentRegistry(Protocol):
    def verify_example(self, meta: dict, now: datetime) -> list[str]:
        """Problems with this example according to the trusted records; [] only if every fact is confirmed."""

    def verify_consent_refs(self, refs: set[str], now: datetime) -> list[str]:
        """Problems with a set of consent references (used again at model approval)."""

    def verify_coverage_lineage(self, lineage: list[dict], now: datetime) -> list[str]:
        """Problems with the coverage of every video behind a model (used again at model approval and use)."""


def _naive(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=None) if value is not None else None


class SqlAlchemyConsentRegistry:
    """Read-only lookup in the platform database. `session_factory` should be given a read-only connection where the
    deployment can arrange one; this class additionally never calls add/commit/flush and rolls back after each use."""

    def __init__(self, session_factory: Callable[[], Session], salt: bytes, coverage: CoverageEvidence | None = None):
        if not isinstance(salt, (bytes, bytearray)) or len(salt) < 16:
            raise c.ConsentError("the registry adapter needs the same salt (>= 16 bytes) the dataset pseudonyms were made with")
        self._factory = session_factory
        self._salt = bytes(salt)
        self._coverage = coverage if coverage is not None else NoCoverageEvidence()

    def _coverage_problems(self, db: Session, video: VideoDB, entry: MLDatasetEntryDB | None, now: datetime) -> list[str]:
        """Is every person visible in THIS video file covered by a current consent, according to a trusted coverage record?"""
        try:
            record = self._coverage.record_for_video(video.video_id)
        except Exception:
            return [COVERAGE_NOT_VERIFIABLE]
        if not isinstance(record, CoverageRecord):
            return [COVERAGE_NOT_VERIFIABLE]
        problems = []
        if record.video_id != video.video_id:
            problems.append("COVERAGE_RECORD_NOT_FOR_THIS_VIDEO")
        if not video.checksum or not isinstance(record.video_sha256, str) or record.video_sha256.lower() != video.checksum.lower():
            problems.append("COVERAGE_RECORD_NOT_FOR_THIS_VIDEO_VERSION")
        if record.scope != COVERAGE_SCOPE:
            problems.append("COVERAGE_SCOPE_IS_NOT_ALL_VISIBLE_PERSONS")
        ids = record.consent_ids if isinstance(record.consent_ids, tuple) else ()
        if not ids or not all(isinstance(i, str) and i for i in ids):
            problems.append("COVERAGE_LISTS_NO_CONSENTS")
        if entry is not None and entry.consent_id not in ids:
            problems.append("COVERAGE_DOES_NOT_INCLUDE_THE_VIDEO_PLAYER_CONSENT")
        if not record.reviewed_by_user_id or record.reviewed_at is None:
            problems.append("COVERAGE_HAS_NO_NAMED_REVIEWER")
        elif _naive(record.reviewed_at) > now:
            problems.append("COVERAGE_REVIEW_IS_DATED_IN_THE_FUTURE")
        for consent_id in ids:
            consent = db.query(GuardianConsentDB).filter(GuardianConsentDB.consent_id == consent_id).first() if isinstance(consent_id, str) else None
            ref = c.consent_ref(consent_id, self._salt) if isinstance(consent_id, str) else "?"
            if consent is None:
                problems.append(f"COVERAGE_CONSENT_NOT_FOUND_IN_REGISTRY:{ref}")
                continue
            problems += [f"COVERAGE_CONSENT_{p}:{ref}" for p in self._consent_problems(consent, now)]
            if record.reviewed_at is not None and _naive(record.reviewed_at) < _naive(consent.granted_at):
                problems.append(f"COVERAGE_REVIEW_PREDATES_CONSENT:{ref}")
        return problems

    def verify_coverage_lineage(self, lineage: list[dict], now: datetime | None = None) -> list[str]:
        """For every video behind a model: the coverage record must still exist, match that exact file, and every consent in it must
        still be valid. Empty or malformed lineage fails closed."""
        now = (now or datetime.now(timezone.utc)).replace(tzinfo=None)
        if not isinstance(lineage, list) or not lineage:
            return [COVERAGE_NOT_VERIFIABLE]
        try:
            db = self._open()
            try:
                problems: list[str] = []
                videos = db.query(VideoDB).all()
                for item in lineage:
                    ref, sha = (item or {}).get("video_id"), str((item or {}).get("video_sha256", "")).lower()
                    video = next((v for v in videos if c.video_ref(v.video_id, self._salt) == ref), None)
                    if video is None:
                        problems.append(f"COVERAGE_VIDEO_NOT_IN_REGISTRY:{ref}")
                        continue
                    if not video.checksum or video.checksum.lower() != sha:
                        problems.append(f"COVERAGE_RECORD_NOT_FOR_THIS_VIDEO_VERSION:{ref}")
                        continue
                    entry = db.query(MLDatasetEntryDB).filter(MLDatasetEntryDB.video_id == video.video_id).first()
                    problems += [f"{p}:{ref}" if ":" not in p else p for p in self._coverage_problems(db, video, entry, now)]
                return sorted(set(problems))
            finally:
                db.rollback()
                db.close()
        except Exception:
            return [REGISTRY_UNAVAILABLE]

    def _open(self) -> Session:
        # No connection setting is changed here: a pooled connection may be shared with the application, so read-only
        # access is arranged by the factory (see readonly_sqlite_factory), not by altering a shared connection.
        return self._factory()

    def _find_consent(self, db: Session, ref: str) -> GuardianConsentDB | None:
        for consent in db.query(GuardianConsentDB).all():
            if c.consent_ref(consent.consent_id, self._salt) == ref:
                return consent
        return None

    def _consent_problems(self, consent: GuardianConsentDB | None, now: datetime) -> list[str]:
        if consent is None:
            return ["CONSENT_NOT_FOUND_IN_REGISTRY"]
        problems = []
        if consent.withdrawn_at is not None:
            problems.append("CONSENT_WITHDRAWN")
        if consent.expires_at is not None and _naive(consent.expires_at) <= now:
            problems.append("CONSENT_EXPIRED")
        if CONSENT_PURPOSE not in (consent.purposes or []):
            problems.append("CONSENT_PURPOSE_NOT_ML_TRAINING")
        if _naive(consent.granted_at) > now:
            problems.append("CONSENT_NOT_YET_GRANTED")
        return problems

    def verify_example(self, meta: dict, now: datetime | None = None) -> list[str]:
        now = (now or datetime.now(timezone.utc)).replace(tzinfo=None)
        cons = meta.get("consent") or {}
        try:
            db = self._open()
            try:
                consent = self._find_consent(db, str(cons.get("consent_ref")))
                problems = self._consent_problems(consent, now)
                entry = next((e for e in db.query(MLDatasetEntryDB).all()
                              if c.video_ref(e.video_id, self._salt) == meta.get("video_id")), None)
                if entry is None:
                    return sorted(set(problems + ["VIDEO_NOT_IN_ML_DATASET_REGISTRY"]))
                if entry.status != "approved":
                    problems.append("DATASET_ENTRY_NOT_APPROVED")
                if entry.reviewed_by_user_id is None or entry.reviewed_at is None:
                    problems.append("NO_NAMED_CONSENT_REVIEWER")
                elif c.coach_ref(entry.reviewed_by_user_id, self._salt) != cons.get("reviewed_by_ref"):
                    problems.append("REVIEWER_DOES_NOT_MATCH_REGISTRY")
                if consent is not None and entry.consent_id != consent.consent_id:
                    problems.append("CONSENT_IS_NOT_THE_ONE_ON_THE_DATASET_ENTRY")
                if entry.reviewed_at is not None:
                    if _naive(entry.reviewed_at) > now:
                        problems.append("REVIEW_IS_DATED_IN_THE_FUTURE")
                    if entry.flagged_at is not None and _naive(entry.reviewed_at) < _naive(entry.flagged_at):
                        problems.append("REVIEW_PREDATES_THE_DATASET_ENTRY")
                    if consent is not None and consent.granted_at is not None and _naive(entry.reviewed_at) < _naive(consent.granted_at):
                        problems.append("REVIEW_PREDATES_THE_CONSENT")
                video = db.get(VideoDB, entry.video_id)
                record = db.get(DataRecordDB, video.record_id) if video is not None else None
                if video is None or record is None:
                    problems.append("VIDEO_RECORD_NOT_FOUND_IN_REGISTRY")
                else:
                    if consent is not None and consent.player_id != record.player_id:
                        problems.append("CONSENT_PLAYER_IS_NOT_THE_VIDEO_PLAYER")
                    if c.anonymous_player_id(record.player_id, self._salt) != meta.get("anonymous_player_id"):
                        problems.append("PLAYER_PSEUDONYM_DOES_NOT_MATCH_REGISTRY")
                    claimed = str((meta.get("provenance") or {}).get("source_video_sha256", "")).lower()
                    if not video.checksum or video.checksum.lower() != claimed:
                        problems.append("VIDEO_CHECKSUM_DOES_NOT_MATCH_REGISTRY")
                    else:
                        problems += self._coverage_problems(db, video, entry, now)         # a general approval is not coverage
                return sorted(set(problems))
            finally:
                db.rollback()
                db.close()
        except Exception:                                           # any failure to confirm is a refusal
            return [REGISTRY_UNAVAILABLE]

    def verify_consent_refs(self, refs: set[str], now: datetime | None = None) -> list[str]:
        now = (now or datetime.now(timezone.utc)).replace(tzinfo=None)
        try:
            db = self._open()
            try:
                problems: list[str] = []
                for ref in sorted(refs):
                    problems += [f"{p}:{ref}" for p in self._consent_problems(self._find_consent(db, ref), now)]
                return problems
            finally:
                db.rollback()
                db.close()
        except Exception:
            return [REGISTRY_UNAVAILABLE]


def readonly_sqlite_factory(path) -> Callable[[], Session]:
    """A session factory on its OWN connection, opened by SQLite itself in read-only mode (`mode=ro`): even a bug could not
    write. Use a copy or snapshot of the registry database if the live file must not be touched at all."""
    from pathlib import Path
    engine = create_engine(f"sqlite:///file:{Path(path).resolve()}?mode=ro&uri=true")
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def registry_problems(meta: dict, registry: ConsentRegistry | None, now: datetime) -> list[str]:
    """What the TRUSTED records say about a real example. A synthetic example has no registry record and needs none
    (its claim is proven separately). A real example with no registry configured is refused."""
    source = (meta.get("provenance") or {}).get("source")
    if source == "SYNTHETIC_FIXTURE":
        return []
    if registry is None:
        return [REGISTRY_NOT_CONFIGURED]
    return registry.verify_example(meta, now)
