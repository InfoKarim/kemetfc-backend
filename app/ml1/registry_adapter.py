"""Trusted verification of consent against the platform's own records (read-only).

The `consent` block inside an example is a claim written by whoever built the example; it is NOT evidence. For real
(non-synthetic) data the only evidence is the platform's records: the guardian consent, the ML dataset entry a named
reviewer approved, and the registered video. This adapter looks those up fresh, every time it is asked, and answers
"no" whenever it cannot positively confirm: no record, a database error, a withdrawn or expired consent, a purpose that
is not ml_training, a dataset entry that is not approved, or an example whose pseudonyms do not match the records.

It never writes. It does not copy children's data out: it compares one-way pseudonyms (salted HMAC) computed from the
registry's ids with the pseudonyms stored in the example.
"""
from datetime import datetime, timezone
from typing import Callable, Protocol

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db_models import DataRecordDB, GuardianConsentDB, MLDatasetEntryDB, VideoDB
from app.ml1 import consent as c
from app.ml1.config import CONSENT_PURPOSE

REGISTRY_NOT_CONFIGURED = "REGISTRY_NOT_CONFIGURED_FAIL_CLOSED"
REGISTRY_UNAVAILABLE = "REGISTRY_UNAVAILABLE_FAIL_CLOSED"


class ConsentRegistry(Protocol):
    def verify_example(self, meta: dict, now: datetime) -> list[str]:
        """Problems with this example according to the trusted records; [] only if every fact is confirmed."""

    def verify_consent_refs(self, refs: set[str], now: datetime) -> list[str]:
        """Problems with a set of consent references (used again at model approval)."""


def _naive(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=None) if value is not None else None


class SqlAlchemyConsentRegistry:
    """Read-only lookup in the platform database. `session_factory` should be given a read-only connection where the
    deployment can arrange one; this class additionally never calls add/commit/flush and rolls back after each use."""

    def __init__(self, session_factory: Callable[[], Session], salt: bytes):
        if not isinstance(salt, (bytes, bytearray)) or len(salt) < 16:
            raise c.ConsentError("the registry adapter needs the same salt (>= 16 bytes) the dataset pseudonyms were made with")
        self._factory = session_factory
        self._salt = bytes(salt)

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
