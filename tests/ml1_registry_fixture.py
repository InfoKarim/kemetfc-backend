"""A real SQLite registry (the platform's own tables, scratch database) seeded to match fake real-style examples, so the
consent gate is tested against the real adapter rather than a stand-in that believes the example's own claims."""
import hashlib
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.db_models import DataRecordDB, GuardianConsentDB, MLDatasetEntryDB, PlayerDB, VideoDB
from app.ml1.registry_adapter import COVERAGE_SCOPE, CoverageRecord, SqlAlchemyConsentRegistry
from tests.ml1_helpers import NOW, SALT, video_sha


class MockCoverageEvidenceNotRealConsent:
    """A STAND-IN for a trusted coverage store, used only to test the success path and the refusals around it. It proves nothing about
    real consent: no such store exists in the platform. Records are built from an explicit map {raw_video: [raw consent ids]}; a
    video missing from the map has no record (like the real default). `overrides` replaces fields of one video's record."""

    def __init__(self, mapping, overrides=None, fail=False):
        self.mapping, self.overrides, self.fail = mapping, overrides or {}, fail

    def record_for_video(self, video_id):
        if self.fail:
            raise ConnectionError("coverage store unreachable")
        if video_id not in self.mapping:
            return None
        fields = dict(video_id=video_id, video_sha256=video_sha(video_id), scope=COVERAGE_SCOPE, consent_ids=tuple(self.mapping[video_id]),
                      reviewed_by_user_id="coach-007", reviewed_at=NOW)
        fields.update(self.overrides.get(video_id, {}))
        return CoverageRecord(**fields)


def make_registry(pairs, *, status="approved", reviewer="coach-007", coverage="mock"):
    """pairs: iterable of (raw_player, raw_video). Returns (registry, session_factory).
    coverage: "mock" = a stand-in coverage store listing each video's own player consent (NOT real evidence); None = the real default
    (no coverage store, so every real example is refused); or a ready-made evidence object."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    seed(factory, pairs, status=status, reviewer=reviewer)
    if coverage == "mock":
        coverage = MockCoverageEvidenceNotRealConsent({video: [f"consent-{player}"] for player, video in set(pairs)})
    return SqlAlchemyConsentRegistry(factory, SALT, coverage), factory


def seed(factory, pairs, *, status="approved", reviewer="coach-007"):
    with factory() as db:
        players, n = set(), 0
        for player, video in sorted(set(pairs)):
            if player not in players:
                players.add(player)
                db.add(PlayerDB(player_id=player, first_name_ar="x", last_name_ar="x", first_name_en="Fake", last_name_en="Child",
                                date_of_birth=NOW.date(), sex="n/a", physical_profile={}, technical_profile={}, mental_profile={},
                                match_performance={}))
                db.add(GuardianConsentDB(consent_id=f"consent-{player}", player_id=player, guardian_name="Fake Guardian",
                                         guardian_email="guardian@example.test", verification_method="in_person",
                                         purposes=["video_analysis", "ml_training"], granted_at=NOW - timedelta(days=30),
                                         expires_at=NOW + timedelta(days=300), withdrawn_at=None, recorded_by_user_id="admin-1"))
            db.add(DataRecordDB(record_id=f"REC-{video}", player_id=player, source_type="video", created_at=NOW, data_type="video",
                                status="active", original_file_path="/nowhere", analysis_id="A", schema_version="1", created_by="x"))
            db.add(VideoDB(video_id=video, record_id=f"REC-{video}", video_type="assessment", duration_seconds=20.0, recorded_at=NOW,
                           session_id="S", location_id="L", capture_device="D", resolution="1x1", frame_rate_fps=30.0, file_size_mb=1.0,
                           file_format="mov", file_path="/nowhere", checksum=video_sha(video), original_preserved=True,
                           ai_processing_status="none", requires_human_review=False, review_reason="", human_review_status="none",
                           analysis_approved=False))
            n += 1
            db.add(MLDatasetEntryDB(entry_id=f"ENTRY-{n:04d}", video_id=video, team_id=None, age_band="U10", sex_cohort="mixed", camera_id="C",
                                    lighting="day", consent_id=f"consent-{player}", status=status, notes=None, flagged_by_user_id="admin-1",
                                    flagged_at=NOW, reviewed_by_user_id=reviewer, reviewed_at=NOW))
        db.commit()
