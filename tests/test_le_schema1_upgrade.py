"""Review round 3: upgrading a schema-1 database must not carry an approval over to a source that is no longer valid.

Temporary SQLite files only; the version-1 files are made by taking a fresh store and putting back the version-1 table definitions."""
import json
import sqlite3

import pytest

from learning_engine.budget import Budget, BudgetExceeded
from learning_engine.review import ReviewService
from learning_engine.store import ResearchStore, StoreError
from tests.le_helpers import CH1, NOW, config
from tests.test_le_budget_ai import paid
from tests.test_le_invalidation_survives_retention import legacy_v2

V1_REVIEW_ITEMS = """CREATE TABLE review_items (item_id TEXT PRIMARY KEY, origin TEXT NOT NULL CHECK (origin IN ('YOUTUBE_SOURCE','AI_GENERAL_DRAFT')),
  source_video_id TEXT REFERENCES sources(video_id) ON DELETE SET NULL,
  stage TEXT NOT NULL CHECK (stage IN ('discovered','metadata_only','ai_draft','coach_reviewed')),
  review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending','approved','rejected')), current_version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0 CHECK (published = 0),
  CHECK ((origin = 'YOUTUBE_SOURCE' AND source_video_id IS NOT NULL) OR (origin = 'AI_GENERAL_DRAFT' AND source_video_id IS NULL)))"""
V1_LEDGER = """CREATE TABLE ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, kind TEXT NOT NULL, provider TEXT NOT NULL,
  requests INTEGER NOT NULL DEFAULT 1, units INTEGER NOT NULL DEFAULT 0, tokens_reserved INTEGER NOT NULL DEFAULT 0, usd_reserved REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK (status IN ('reserved','settled','uncertain','released')), tokens_actual INTEGER, usd_actual REAL,
  created_at TEXT NOT NULL, settled_at TEXT, note TEXT)"""
CONTENT = {"title": "Cone weave dribble", "category": "dribbling", "steps": ["Set five cones in a line.", "Dribble through them."], "equipment": ["cones"],
           "duration_minutes": 10, "age_min": 8, "age_max": 12, "safety_notes": "Flat surface, no studs."}


def make_v1(tmp_path, sources, items, *, ledger=(), dangling=False):
    """sources: [(video_id, availability, exclusion_reason, verified_at)]; items: [(item_id, review_status, video_id)]. Each item gets coach
    content (version 2), one review and a REVIEW_APPROVED audit event when approved. Returns the state directory."""
    st = ResearchStore(tmp_path / "s")
    db = st.db
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute("DROP TABLE ledger")
    db.execute(V1_LEDGER)
    db.execute("DROP TABLE review_items")
    db.execute(V1_REVIEW_ITEMS)
    db.execute("ALTER TABLE sources DROP COLUMN fingerprint_conflict_with")
    db.execute("ALTER TABLE reviews DROP COLUMN review_basis")
    for video, availability, reason, verified in sources:
        db.execute("INSERT INTO sources(video_id,channel_id,url,title,discovered_at,verified_at,query_version,stage,availability,exclusion_reason) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (video, CH1, f"https://example.test/{video}", "A title", "2026-09-01T00:00:00Z", verified, "q", "metadata_only" if availability != "unknown" else "discovered", availability, reason))
    for item_id, status, video in items:
        db.execute("INSERT INTO review_items(item_id,origin,source_video_id,stage,review_status,current_version,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                   (item_id, "YOUTUBE_SOURCE", video, "coach_reviewed", status, 2, "2026-09-02T00:00:00Z", "2026-09-02T00:00:00Z"))
        db.execute("INSERT INTO item_versions VALUES (?,?,?,?,?,?,?)", (item_id, 1, "{}", "h1", "NONE", None, "2026-09-01T00:00:00Z"))
        db.execute("INSERT INTO item_versions VALUES (?,?,?,?,?,?,?)", (item_id, 2, json.dumps(CONTENT, sort_keys=True), "h2", "COACH", "coach-1", "2026-09-02T00:00:00Z"))
        if status != "pending":
            db.execute("INSERT INTO reviews(item_id,version,content_hash,decision,reviewer_id,auth_mode,reasons,sources_json,at) VALUES (?,?,?,?,?,?,?,?,?)",
                       (item_id, 2, "h2", status, "coach-1", "backend_user", "Inspected and wrote it myself.", "[]", "2026-09-03T00:00:00Z"))
            with st.transaction():
                st.audit("REVIEW_APPROVED" if status == "approved" else "REVIEW_REJECTED", "coach-1", {"item_id": item_id, "version": 2}, NOW)
    for status, kind, provider in ledger:
        db.execute("INSERT INTO ledger(day,kind,provider,status,created_at) VALUES ('2026-10-08',?,?,?,'2026-10-08T15:00:00Z')", (kind, provider, status))
    db.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
    db.commit()
    st.close()
    return tmp_path / "s"


def raw(path):
    return sqlite3.connect(path / "learning_engine.sqlite3")


def exported(tmp_path):
    st = ResearchStore(tmp_path / "s")
    try:
        return [i["item_id"] for i in ReviewService(st).approved_content()]
    finally:
        st.close()


def state_of(tmp_path, item):
    st = ResearchStore(tmp_path / "s")
    try:
        r = st.db.execute("select approval_state, source_issue, source_issue_at, review_status, source_state from review_items where item_id=?", (item,)).fetchone()
        return tuple(r)
    finally:
        st.close()


# 1 + 2 ----------------------------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("availability,reason,verified,expected_reason,expected_at", [
    ("excluded", "NOT_PUBLIC", "2026-09-20T00:00:00Z", "NOT_PUBLIC", "2026-09-20T00:00:00Z"),
    ("excluded", "MADE_FOR_KIDS", "2026-09-21T00:00:00Z", "MADE_FOR_KIDS", "2026-09-21T00:00:00Z"),
    ("unavailable", None, "2026-09-22T00:00:00Z", "UNDETERMINABLE_LEGACY_STATE", "2026-09-22T00:00:00Z"),
    ("unavailable", "NOT_RETURNED_BY_API", None, "NOT_RETURNED_BY_API", None),
    ("unknown", None, None, "UNDETERMINABLE_LEGACY_STATE", None),
])
def test_an_approved_item_with_an_excluded_unavailable_or_unknown_source_is_suspended_and_not_exported(tmp_path, availability, reason, verified, expected_reason, expected_at):
    make_v1(tmp_path, [("vidAAAAAAAA", availability, reason, verified)], [("SRC-vidAAAAAAAA", "approved", "vidAAAAAAAA")])
    assert state_of(tmp_path, "SRC-vidAAAAAAAA") == ("review_required", expected_reason, expected_at, "approved", "present")
    assert exported(tmp_path) == []
    st = ResearchStore(tmp_path / "s")
    got = ReviewService(st).get("SRC-vidAAAAAAAA")
    assert got["needs_source_review"] is True and got["valid_for_export"] is False and got["source_issue"] == expected_reason
    st.close()


def test_only_an_approved_item_with_an_available_source_keeps_its_approval_and_other_items_are_untouched(tmp_path):
    make_v1(tmp_path, [("vidAAAAAAAA", "available", None, "2026-10-01T00:00:00Z"), ("vidBBBBBBBB", "excluded", "NOT_PUBLIC", "2026-10-02T00:00:00Z")],
            [("SRC-a", "approved", "vidAAAAAAAA"), ("SRC-b", "rejected", "vidBBBBBBBB"), ("SRC-c", "pending", "vidBBBBBBBB")])
    assert state_of(tmp_path, "SRC-a")[0] == "current" and state_of(tmp_path, "SRC-b")[0] == "current" and state_of(tmp_path, "SRC-c")[0] == "current"
    assert exported(tmp_path) == ["SRC-a"]


# 3 ----------------------------------------------------------------------------------------------------------------------------------------------------------
def test_a_dangling_source_reference_refuses_the_whole_upgrade_without_changing_the_file(tmp_path):
    path = make_v1(tmp_path, [], [])
    con = raw(path)
    con.execute("PRAGMA foreign_keys=OFF")
    con.execute("INSERT INTO review_items(item_id,origin,source_video_id,stage,review_status,current_version,created_at,updated_at) VALUES ('SRC-x','YOUTUBE_SOURCE','vidGONE0000','coach_reviewed','approved',2,'t','t')")
    con.execute("INSERT INTO item_versions VALUES ('SRC-x',2,'{}','h2','COACH','coach-1','t')")
    con.commit()
    con.close()
    with pytest.raises(StoreError, match="broken references"):
        ResearchStore(path)
    con = raw(path)
    assert con.execute("select value from meta where key='schema_version'").fetchone()[0] == "1"
    assert [c[1] for c in con.execute("pragma table_info(review_items)")] == ["item_id", "origin", "source_video_id", "stage", "review_status", "current_version", "created_at", "updated_at", "published"]
    assert con.execute("select count(*) from review_items").fetchone()[0] == 1 and con.execute("select count(*) from audit").fetchone()[0] == 0
    con.close()


# 4 + 5 ----------------------------------------------------------------------------------------------------------------------------------------------------
def test_coach_content_versions_decisions_and_the_audit_chain_survive_and_an_old_approval_event_does_not_restore_validity(tmp_path):
    make_v1(tmp_path, [("vidAAAAAAAA", "excluded", "NOT_PUBLIC", "2026-09-20T00:00:00Z")], [("SRC-a", "approved", "vidAAAAAAAA")])
    st = ResearchStore(tmp_path / "s")
    svc = ReviewService(st)
    got = svc.get("SRC-a")
    assert got["content"] == CONTENT and got["version"] == 2 and got["review_status"] == "approved"
    assert [v["version"] for v in st.db.execute("select version from item_versions where item_id='SRC-a' order by version")] == [1, 2]
    history = svc.history("SRC-a")
    assert len(history) == 1 and history[0]["decision"] == "approved" and history[0]["reasons"] == "Inspected and wrote it myself."
    events = [r["event"] for r in st.db.execute("select event from audit order by n")]
    assert events == ["REVIEW_APPROVED", "LEGACY_APPROVALS_SUSPENDED_AT_UPGRADE"]               # the approval event is there and still changes nothing
    assert got["valid_for_export"] is False and svc.approved_content() == [] and st.verify_audit()
    st.close()


# 7 ----------------------------------------------------------------------------------------------------------------------------------------------------------
def test_reopening_the_database_neither_repeats_the_upgrade_nor_changes_a_decision(tmp_path):
    make_v1(tmp_path, [("vidAAAAAAAA", "excluded", "NOT_PUBLIC", "2026-09-20T00:00:00Z"), ("vidBBBBBBBB", "available", None, "2026-10-01T00:00:00Z")],
            [("SRC-a", "approved", "vidAAAAAAAA"), ("SRC-b", "approved", "vidBBBBBBBB")])
    first = (state_of(tmp_path, "SRC-a"), state_of(tmp_path, "SRC-b"))
    for _ in range(3):
        assert (state_of(tmp_path, "SRC-a"), state_of(tmp_path, "SRC-b")) == first
        assert exported(tmp_path) == ["SRC-b"]
    st = ResearchStore(tmp_path / "s")
    assert st.schema_version() == 3 and st.db.execute("select count(*) from audit where event='LEGACY_APPROVALS_SUSPENDED_AT_UPGRADE'").fetchone()[0] == 1
    st.close()


# 8 ----------------------------------------------------------------------------------------------------------------------------------------------------------
def test_a_failure_in_the_middle_of_the_upgrade_rolls_everything_back(tmp_path, monkeypatch):
    path = make_v1(tmp_path, [("vidAAAAAAAA", "excluded", "NOT_PUBLIC", "2026-09-20T00:00:00Z")], [("SRC-a", "approved", "vidAAAAAAAA")],
                   ledger=[("released", "ai", "openai")])

    def boom(self, *args, **kwargs):
        raise RuntimeError("simulated failure after the ledger and review tables were rebuilt")

    monkeypatch.setattr(ResearchStore, "audit", boom)
    with pytest.raises(RuntimeError):
        ResearchStore(path)
    monkeypatch.undo()
    con = raw(path)
    assert con.execute("select value from meta where key='schema_version'").fetchone()[0] == "1"
    assert con.execute("select status from ledger").fetchone()[0] == "released"                  # the old row, not the converted one
    assert "approval_state" not in [c[1] for c in con.execute("pragma table_info(review_items)")]
    assert not con.execute("select name from sqlite_master where name like '%_v1'").fetchall()
    con.close()
    st = ResearchStore(path)                                                                     # and a later, healthy open still upgrades it
    assert st.schema_version() == 3
    st.close()
    assert exported(tmp_path) == []


# 9 ----------------------------------------------------------------------------------------------------------------------------------------------------------
def test_old_ai_reservations_still_count_after_a_schema_1_upgrade(tmp_path):
    cfg = paid(budget={"daily_ai_requests": 1})
    make_v1(tmp_path, [], [], ledger=[("released", "ai", "openai")])                             # a v1 "released" row may have been sent
    st = ResearchStore(tmp_path / "s")
    assert [r["status"] for r in st.db.execute("select status from ledger")] == ["uncertain"]
    with pytest.raises(BudgetExceeded):
        Budget(st, cfg, lambda: NOW).reserve_ai("openai", 50, 100)
    st.close()


def test_a_schema_2_database_still_upgrades_and_its_sent_ai_rows_still_count(tmp_path):
    cfg = paid(budget={"daily_ai_requests": 1})
    st = legacy_v2(tmp_path, [("SRC-b", "removed", None, None)], [("REVIEW_APPROVED", {"item_id": "SRC-b"})])
    st.db.execute("INSERT INTO ledger(day,kind,provider,status,created_at,sent_at) VALUES ('2026-10-08','ai','openai','sent','2026-10-08T15:00:00Z','2026-10-08T15:00:00Z')")
    st.db.commit()
    st.close()
    up = ResearchStore(tmp_path / "s")
    assert up.schema_version() == 3
    assert up.db.execute("select approval_state from review_items").fetchone()[0] == "current"       # 2 -> 3 behaviour unchanged
    with pytest.raises(BudgetExceeded):
        Budget(up, cfg, lambda: NOW).reserve_ai("openai", 50, 100)
    up.close()
