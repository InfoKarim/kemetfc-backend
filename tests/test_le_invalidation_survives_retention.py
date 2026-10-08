"""Review round 2, fix 1: an approval that was suspended because its source went bad stays suspended when retention deletes the
source. Validity of the approval and retention of the source data are two separate things."""
import json
from datetime import timedelta

import pytest

from learning_engine.daily import run_daily
from learning_engine.review import AUTH_BACKEND, ReviewError, ReviewService, Reviewer
from tests.le_helpers import NOW, config, video_item, vid
from tests.test_privacy_s0 import ctx  # noqa: F401  (a fixture)
from tests.test_le_source_refresh_invalidation import COACH, GOOD, approve, first_run, run, store

OFFLINE = config(mode="offline", network_enabled=False)
CASES = [("private", lambda: [video_item(1, privacy="private")], "NOT_PUBLIC"), ("deleted", lambda: [], "NOT_RETURNED_BY_API"),
         ("made_for_kids", lambda: [video_item(1, kids=True)], "MADE_FOR_KIDS")]


def purge(tmp_path, days=33):
    return run_daily(OFFLINE, tmp_path / "state", env={}, now=NOW + timedelta(days=days))


def exported(tmp_path):
    s = store(tmp_path)
    try:
        return [i["item_id"] for i in ReviewService(s).approved_content()]
    finally:
        s.close()


@pytest.mark.parametrize("name,videos,reason", CASES, ids=[c[0] for c in CASES])
def test_invalidated_then_purged_stays_out_of_the_valid_export_with_reason_content_and_history(tmp_path, name, videos, reason):
    first_run(tmp_path)
    item = approve(tmp_path, 1)
    run(tmp_path, 1, videos())
    assert exported(tmp_path) == []
    report = purge(tmp_path)
    assert report["retention"]["items_kept_with_source_removed"] == 1
    assert exported(tmp_path) == []                                              # the reported defect: this used to be [item]
    s = store(tmp_path)
    svc = ReviewService(s)
    got = svc.get(item)
    assert got["source"] is None and got["source_state"] == "removed" and got["approval_state"] == "review_required"
    assert got["needs_source_review"] is True and got["valid_for_export"] is False
    assert got["source_issue"] == reason and got["source_issue_at"].startswith((NOW + timedelta(days=1)).strftime("%Y-%m-%d"))
    assert got["content"] == GOOD and got["review_status"] == "approved" and len(svc.history(item)) == 1 and svc.history(item)[0]["decision"] == "approved"
    assert vid(1) not in "\n".join(s.db.iterdump())                              # and still no API-derived identifier anywhere
    s.close()


def test_repeated_purges_change_nothing(tmp_path):
    first_run(tmp_path)
    item = approve(tmp_path, 1)
    run(tmp_path, 1, [video_item(1, privacy="private")])
    purge(tmp_path, 33)
    s = store(tmp_path)
    before = ReviewService(s).get(item)
    s.close()
    for days in (34, 40, 90):
        purge(tmp_path, days)
        assert exported(tmp_path) == []
    s = store(tmp_path)
    assert ReviewService(s).get(item) == before
    s.close()


def test_a_source_that_returns_or_content_edits_do_not_restore_the_approval_after_purge(tmp_path):
    first_run(tmp_path)
    item = approve(tmp_path, 1)
    run(tmp_path, 1, [video_item(1, privacy="private")])
    run(tmp_path, 2, [video_item(1)])                                            # public again before the purge
    assert exported(tmp_path) == []
    purge(tmp_path, 33)
    s = store(tmp_path)
    svc = ReviewService(s)
    svc.save_content(item, {**GOOD, "duration_minutes": 11}, COACH)             # editing is not an approval
    assert svc.get(item)["review_status"] == "pending" and svc.get(item)["needs_source_review"] is True and svc.approved_content() == []
    s.close()


def test_re_approval_of_independent_content_needs_an_explicit_authorised_current_version_decision_and_is_labelled(tmp_path):
    first_run(tmp_path)
    item = approve(tmp_path, 1)
    run(tmp_path, 1, [video_item(1, kids=True)])
    purge(tmp_path, 33)
    s = store(tmp_path)
    svc = ReviewService(s)
    with pytest.raises(ReviewError):
        Reviewer("guardian-1", AUTH_BACKEND, "guardian")
    with pytest.raises(ReviewError, match="changed since"):
        svc.decide(item, 1, "approved", COACH, "Deciding on an old version of the item.", [])
    with pytest.raises(ReviewError, match="written reasons"):
        svc.decide(item, 2, "approved", COACH, "short", [])
    assert svc.approved_content() == []
    out = svc.decide(item, 2, "approved", COACH, "My own drill; the original video was removed but I re-read every step.", [])
    assert out["approval_state"] == "current" and out["source_state"] == "removed" and out["valid_for_export"] is True
    last = svc.history(item)[-1]
    assert last["review_basis"] == "INDEPENDENT_CONTENT_SOURCE_REMOVED" and out["last_decision"]["review_basis"] == "INDEPENDENT_CONTENT_SOURCE_REMOVED"
    assert [i["item_id"] for i in svc.approved_content()] == [item] and svc.approved_content()[0]["review_basis"] == "INDEPENDENT_CONTENT_SOURCE_REMOVED"
    assert "independent" in out["note"].lower() and "not a check of the source" in out["note"].lower()
    s.close()


def test_a_source_deleted_by_retention_that_was_never_invalidated_keeps_a_current_approval_flagged_as_sourceless(tmp_path):
    """Documented behaviour: retention alone is not evidence that the content went bad. The coach's own approval stays valid, the export
    flags it `source_removed`, and no source identifier survives."""
    first_run(tmp_path)
    item = approve(tmp_path, 1)
    purge(tmp_path, 31)
    s = store(tmp_path)
    svc = ReviewService(s)
    got = svc.get(item)
    assert got["approval_state"] == "current" and got["source_state"] == "removed" and got["valid_for_export"] is True
    exp = svc.approved_content()
    assert [e["item_id"] for e in exp] == [item] and exp[0]["source_removed"] is True and exp[0]["source_url"] is None
    s.close()


def test_the_cli_and_api_never_present_an_item_needing_review_as_valid_after_purge(tmp_path, capsys, ctx, monkeypatch):
    from learning_engine.cli import main
    from tests.le_helpers import config_dict
    first_run(tmp_path)
    item = approve(tmp_path, 1)
    run(tmp_path, 1, [video_item(1, privacy="private")])
    purge(tmp_path, 33)
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps(config_dict()))
    base = ["--config", str(cfg), "--state", str(tmp_path / "state")]
    capsys.readouterr()
    assert main(base + ["export-approved"]) == 0 and json.loads(capsys.readouterr().out) == []
    assert main(base + ["list", "--needs-source-review"]) == 0
    out = capsys.readouterr().out
    assert item in out and "NEEDS_REVIEW(NOT_PUBLIC)" in out
    assert main(base + ["list", "--status", "approved"]) == 0
    assert "NEEDS_REVIEW" in capsys.readouterr().out                                 # an "approved" row still says it is not valid
    assert main(base + ["status"]) == 0 and json.loads(capsys.readouterr().out)["source_review_required"] == 1
    monkeypatch.setenv("LEARNING_ENGINE_REVIEW_API_ENABLED", "1")
    monkeypatch.setenv("LEARNING_ENGINE_STATE_DIR", str(tmp_path / "state"))
    listed = ctx.coach.get("/learning-review/items", params={"status": "approved"}).json()
    assert [(i["item_id"], i["valid_for_export"], i["needs_source_review"]) for i in listed] == [(item, False, True)]
    assert ctx.coach.get("/learning-review/items", params={"needs_source_review": "true"}).json()[0]["item_id"] == item


# ---- upgrading a version-2 database: never assume a legacy approval is valid when the audit log cannot show it -------------------------------------
V2_REVIEW_ITEMS = """CREATE TABLE review_items (item_id TEXT PRIMARY KEY, origin TEXT NOT NULL CHECK (origin IN ('YOUTUBE_SOURCE','AI_GENERAL_DRAFT')),
  source_video_id TEXT REFERENCES sources(video_id) ON DELETE SET NULL,
  stage TEXT NOT NULL CHECK (stage IN ('discovered','metadata_only','ai_draft','coach_reviewed')),
  review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending','approved','rejected')), current_version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0 CHECK (published = 0),
  source_state TEXT NOT NULL DEFAULT 'valid' CHECK (source_state IN ('valid','review_required','removed')),
  source_issue TEXT, source_issue_at TEXT, source_removed_at TEXT,
  CHECK ((origin = 'YOUTUBE_SOURCE' AND (source_video_id IS NOT NULL OR source_state = 'removed')) OR (origin = 'AI_GENERAL_DRAFT' AND source_video_id IS NULL)))"""


def legacy_v2(tmp_path, rows, audit):
    """A temporary version-2 database: review_items in its old shape, plus audit events. Nothing real."""
    from learning_engine.store import ResearchStore
    st = ResearchStore(tmp_path / "s")
    db = st.db
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute("DROP TABLE review_items")
    db.execute(V2_REVIEW_ITEMS)
    db.execute("ALTER TABLE reviews DROP COLUMN review_basis")
    db.execute("ALTER TABLE sources DROP COLUMN discovery_class")
    db.execute("ALTER TABLE sources DROP COLUMN discovery_query")
    for item_id, state, issue, issue_at in rows:
        video = None
        if state != "removed":
            video = f"fake-{item_id}"[:11].ljust(11, "x")
            db.execute("INSERT INTO sources(video_id,channel_id,url,discovered_at,query_version,stage) VALUES (?,?,?,?,?,?)",
                       (video, "UC" + "a" * 22, "https://example.test/" + video, "t", "q", "discovered"))
        db.execute("INSERT INTO review_items(item_id,origin,source_video_id,stage,review_status,current_version,created_at,updated_at,source_state,source_issue,source_issue_at) "
                   "VALUES (?,?,?,'coach_reviewed','approved',2,'t','t',?,?,?)", (item_id, "YOUTUBE_SOURCE", video, state, issue, issue_at))
    db.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
    for event, data in audit:
        with st.transaction():
            st.audit(event, "test", data, NOW)
    db.commit()
    return st


def states(tmp_path):
    from learning_engine.store import ResearchStore
    st = ResearchStore(tmp_path / "s")
    try:
        assert st.schema_version() == 5 and st.verify_audit()
        return {r["item_id"]: (r["source_state"], r["approval_state"], r["source_issue"]) for r in st.db.execute("select * from review_items")}
    finally:
        st.close()


def test_a_version_2_database_is_upgraded_fail_closed_for_legacy_approvals(tmp_path):
    rows = [("SRC-a", "review_required", "NOT_PUBLIC", "2026-10-09T00:00:00Z"),      # suspended, source still held
            ("SRC-b", "removed", None, None),                                            # the reported defect: suspension erased, audit shows it
            ("SRC-c", "removed", None, None),                                            # nothing in the audit about this one
            ("SRC-d", "removed", None, None),                                            # approved, never invalidated (audit shows both)
            ("SRC-e", "valid", None, None)]
    audit = [("REVIEW_APPROVED", {"item_id": "SRC-b"}), ("SOURCE_INVALIDATED_REVIEW_REQUIRED", {"item_id": "SRC-b", "reason": "MADE_FOR_KIDS"}),
             ("REVIEW_APPROVED", {"item_id": "SRC-d"})]
    st = legacy_v2(tmp_path, rows, audit)
    st.close()
    got = states(tmp_path)
    assert got["SRC-a"] == ("present", "review_required", "NOT_PUBLIC")
    assert got["SRC-b"] == ("removed", "review_required", "MADE_FOR_KIDS")
    assert got["SRC-c"] == ("removed", "review_required", "UNDETERMINABLE_LEGACY_STATE")            # cannot be shown valid, so it is not
    assert got["SRC-d"] == ("removed", "current", None)
    assert got["SRC-e"] == ("present", "current", None)


def test_a_version_2_upgrade_with_a_broken_audit_chain_treats_removed_approvals_as_not_valid(tmp_path):
    st = legacy_v2(tmp_path, [("SRC-d", "removed", None, None)], [("REVIEW_APPROVED", {"item_id": "SRC-d"})])
    st.db.execute("UPDATE audit SET actor='tampered' WHERE n=1")
    st.db.commit()
    st.close()
    from learning_engine.store import ResearchStore
    up = ResearchStore(tmp_path / "s")
    row = up.db.execute("select approval_state, source_issue from review_items").fetchone()
    assert (row["approval_state"], row["source_issue"]) == ("review_required", "UNDETERMINABLE_LEGACY_STATE")
    up.close()


def test_the_version_3_upgrade_is_all_or_nothing(tmp_path):
    st = legacy_v2(tmp_path, [("SRC-a", "valid", None, None)], [])
    st.db.execute("INSERT INTO item_versions VALUES ('GHOST',1,'{}','h','NONE',NULL,'t')")          # a dangling reference: the upgrade must refuse
    st.db.commit()
    st.close()
    from learning_engine.store import ResearchStore, StoreError
    with pytest.raises(StoreError, match="broken references"):
        ResearchStore(tmp_path / "s")
    import sqlite3
    raw = sqlite3.connect(tmp_path / "s" / "learning_engine.sqlite3")
    assert raw.execute("select value from meta where key='schema_version'").fetchone()[0] == "2"      # still version 2, nothing half-applied
    assert raw.execute("select count(*) from review_items").fetchone()[0] == 1
    raw.close()
