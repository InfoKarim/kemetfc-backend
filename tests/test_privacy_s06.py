"""S0.6 privacy guardrails: check-in token revocation, avatar reference,
export completeness contract, audit content, and scratch-storage safety.

Synthetic data only, in a throwaway in-memory database with foreign keys
enforced (see test_privacy_s0.Ctx). The avatar folder, upload folders and
database are redirected to a scratch directory by tests/conftest.py.
"""
import json
import os
from datetime import timedelta
from pathlib import Path

import pytest

import app.avatar_upload as avatar_upload
import tests.conftest as session_guard
from app.db_models import AuditEventDB, PlayerCheckInTokenDB, PlayerDB
from app.services.auth_service import hash_session_token, utcnow
from app.services.privacy_service import (
    ANONYMISED_DATE_OF_BIRTH,
    is_anonymised_player,
)
from tests.test_privacy_s0 import ctx, run_deletion  # noqa: F401  (ctx is a fixture)


def token_rows(ctx, player_id):
    with ctx.db() as db:
        return db.query(PlayerCheckInTokenDB).filter(
            PlayerCheckInTokenDB.player_id == player_id
        ).all()


def add_live_token(ctx, player_id, raw):
    now = utcnow()
    with ctx.db() as db:
        db.add(PlayerCheckInTokenDB(
            token_id=f"TOK_{raw}", player_id=player_id, token_hash=hash_session_token(raw),
            created_at=now, expires_at=now + timedelta(days=30), revoked_at=None,
            last_used_at=None,
        ))
        db.commit()


def resolve(ctx, raw):
    return ctx.coach.post("/players/checkin-token/resolve", json={"token": raw})


# ---- TESTS 1-4: check-in tokens ------------------------------------------

def test_completed_deletion_revokes_every_active_token_for_that_player_only(ctx):
    add_live_token(ctx, "P_A", "KEMETCHK-A1")
    add_live_token(ctx, "P_A", "KEMETCHK-A2")
    add_live_token(ctx, "P_B", "KEMETCHK-B1")
    already_revoked = utcnow() - timedelta(days=3)
    with ctx.db() as db:
        db.add(PlayerCheckInTokenDB(
            token_id="TOK_OLD", player_id="P_A", token_hash=hash_session_token("KEMETCHK-OLD"),
            created_at=already_revoked, expires_at=already_revoked + timedelta(days=30),
            revoked_at=already_revoked, last_used_at=None,
        ))
        db.commit()
    assert resolve(ctx, "KEMETCHK-A1").status_code == 200
    before_b = {t.token_id: t.revoked_at for t in token_rows(ctx, "P_B")}

    assert run_deletion(ctx, ctx.guard_a, "P_A", "REQ_A").status_code == 200

    rows = token_rows(ctx, "P_A")
    assert len(rows) == 3  # rows are kept, only invalidated
    assert all(row.revoked_at is not None for row in rows)
    # An already-revoked token keeps its original revocation time.
    assert next(r for r in rows if r.token_id == "TOK_OLD").revoked_at == already_revoked
    # Player B is untouched.
    assert {t.token_id: t.revoked_at for t in token_rows(ctx, "P_B")} == before_b
    assert all(t.revoked_at is None for t in token_rows(ctx, "P_B"))
    assert resolve(ctx, "KEMETCHK-B1").status_code == 200


def test_revoked_token_cannot_resolve(ctx):
    add_live_token(ctx, "P_A", "KEMETCHK-A1")
    run_deletion(ctx, ctx.guard_a, "P_A", "REQ_A")
    response = resolve(ctx, "KEMETCHK-A1")
    assert response.status_code == 410
    assert response.json()["reason"] == "revoked"


def test_live_token_for_an_anonymised_player_cannot_resolve(ctx):
    # A token minted after deletion (the mint route does not check) or one
    # missed by an older deletion: the player is anonymised, the token is live.
    run_deletion(ctx, ctx.guard_a, "P_A", "REQ_A")
    add_live_token(ctx, "P_A", "KEMETCHK-LATE")
    response = resolve(ctx, "KEMETCHK-LATE")
    assert response.status_code == 404
    assert response.json()["reason"] == "invalid"
    assert "Deleted" not in response.text


def test_anonymised_player_marker_is_the_existing_one(ctx):
    with ctx.db() as db:
        assert is_anonymised_player(db.get(PlayerDB, "P_B")) is False
    run_deletion(ctx, ctx.guard_a, "P_A", "REQ_A")
    with ctx.db() as db:
        player = db.get(PlayerDB, "P_A")
        assert is_anonymised_player(player) is True
        assert player.date_of_birth == ANONYMISED_DATE_OF_BIRTH
        assert (player.first_name_en, player.last_name_en) == ("Deleted", "Player")


# ---- TESTS 5-6: avatar reference only -------------------------------------

def test_deletion_clears_photo_reference_but_never_deletes_the_file(ctx):
    avatar_dir = Path(avatar_upload.AVATAR_DIR)
    avatar_dir.mkdir(parents=True, exist_ok=True)
    avatar_a = avatar_dir / "P_A.png"
    avatar_b = avatar_dir / "P_B.png"
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    avatar_a.write_bytes(png)
    avatar_b.write_bytes(png)
    with ctx.db() as db:
        db.get(PlayerDB, "P_A").photo_filename = "P_A.png"
        db.get(PlayerDB, "P_B").photo_filename = "P_B.png"
        db.commit()

    run_deletion(ctx, ctx.guard_a, "P_A", "REQ_A")

    with ctx.db() as db:
        assert db.get(PlayerDB, "P_A").photo_filename is None
        assert db.get(PlayerDB, "P_B").photo_filename == "P_B.png"
    # No physical avatar deletion: both files are still there, byte for byte.
    assert avatar_a.read_bytes() == png
    assert avatar_b.read_bytes() == png
    assert ctx.deleted_files == []


# ---- TESTS 7-9: export completeness ----------------------------------------

def test_export_carries_completeness_metadata(ctx):
    body = ctx.guard_a.get("/guardian/children/P_A/data-export").json()
    completeness = body["completeness"]
    assert "not a complete copy" in completeness["statement"]
    categories = completeness["categories"]
    allowed = {"INCLUDED", "SEPARATE_DOWNLOAD", "NOT_YET_AVAILABLE", "NOT_INCLUDED"}
    assert {c["state"] for c in categories.values()} <= allowed
    # Every top-level data section of the export that is a data category is declared.
    for section in ("player", "data_records", "videos", "analyses", "training_plans",
                    "player_assessments", "player_metrics", "ml_predictions",
                    "monte_carlo_simulations", "prediction_outcomes", "coach_message",
                    "tracking_sessions", "consents"):
        assert section in body
        assert categories[section]["state"] == "INCLUDED"
    # What is left out is said so, not implied.
    for left_out in ("video_files", "avatar_file", "assessment_registrations",
                     "billing_records", "dataset_entries", "external_dataset_copies"):
        assert categories[left_out]["state"] == "NOT_INCLUDED"
        assert categories[left_out]["note"]


def test_raw_tracking_samples_are_marked_separate_download_and_not_included(ctx):
    body = ctx.guard_a.get("/guardian/children/P_A/data-export").json()
    samples = body["completeness"]["categories"]["tracking_samples"]
    assert samples["state"] == "SEPARATE_DOWNLOAD"
    # The contract names a separate download; it does not exist yet, and says so.
    assert samples["download_available"] is False
    assert "does not exist yet" in samples["note"]
    # Never presented as included: no rows, only the per-session count.
    assert samples["state"] != "INCLUDED"
    (session,) = body["tracking_sessions"]
    assert "samples" not in session and session["sample_count"] == 2
    assert "t_seconds" not in json.dumps(body)


def test_completeness_is_identical_for_a_player_with_no_data(ctx):
    full = ctx.guard_a.get("/guardian/children/P_A/data-export").json()
    empty = ctx.guard_empty.get("/guardian/children/P_EMPTY/data-export").json()
    assert full["completeness"] == empty["completeness"]


# ---- TESTS 10-11: audit content ----------------------------------------------

FORBIDDEN_IN_AUDIT = (
    "synthetic note", "synthetic coach message", "reason-secret", "reviewnote-secret",
    "g@example.test", "لاعب", "t_seconds", "pose",
)


def test_privacy_audit_events_carry_counts_and_ids_but_no_content(ctx):
    created = ctx.guard_a.post(
        "/guardian/children/P_A/deletion-requests",
        json={"request_id": "REQ_A", "reason": "REASON-SECRET about my child"},
    )
    assert created.status_code == 201
    ctx.guard_a.get("/guardian/children/P_A/data-export")
    ctx.admin.patch("/privacy-requests/REQ_A",
                    json={"status": "in_review", "review_notes": "REVIEWNOTE-SECRET"})
    done = ctx.admin.patch("/privacy-requests/REQ_A",
                           json={"status": "completed", "review_notes": "REVIEWNOTE-SECRET"})
    assert done.status_code == 200

    with ctx.db() as db:
        events = db.query(AuditEventDB).all()
    by_action = {e.action: e for e in events}

    export = by_action["child_data_exported"]
    assert export.actor_user_id == "GUARD_A"
    assert export.details["result"] == "exported"
    assert export.details["counts"]["player_assessments"] == 1

    completed = by_action["child_deletion_completed"]
    assert completed.actor_user_id == "ADMIN"
    assert completed.resource_type == "privacy_request" and completed.resource_id == "REQ_A"
    assert completed.details["result"] == "completed"
    assert completed.details["revoked_counts"] == {"player_checkin_tokens": 0}
    assert completed.details["cleared_references"] == ["photo_filename"]
    assert completed.details["removed_counts"]["tracking_samples"] == 2

    everything = json.dumps(
        [{"action": e.action, "resource_id": e.resource_id, "details": e.details} for e in events],
        ensure_ascii=False, default=str,
    ).lower()
    for forbidden in FORBIDDEN_IN_AUDIT:
        assert forbidden.lower() not in everything, forbidden


# ---- TESTS 17-18: scratch storage ------------------------------------------

def test_tests_run_against_scratch_storage_only():
    repo = session_guard.REPO_ROOT
    for name in ("PLAYER_VIDEO_UPLOAD_DIR", "DRILL_UPLOAD_DIR", "VIDEO_ANALYSIS_OUTPUT_DIR"):
        path = Path(os.environ[name])
        assert path.is_absolute() and not session_guard._inside_repo(path), name
    assert not session_guard._inside_repo(Path(avatar_upload.AVATAR_DIR))
    url = os.environ["DATABASE_URL"]
    database_file = session_guard.sqlite_file(url)
    assert url.startswith("postgresql") or database_file is None or (
        database_file.is_absolute() and not session_guard._inside_repo(database_file)
    )


def test_real_upload_directories_and_database_are_untouched_so_far():
    for path in session_guard._GUARDED_PATHS:
        assert session_guard._fingerprint(path) == session_guard._BEFORE[str(path)], str(path)


def test_the_real_data_guard_detects_a_change(tmp_path):
    """The session-end guard compares fingerprints; prove it notices a touched file,
    using a throwaway folder (never a real one)."""
    folder = tmp_path / "uploads"
    folder.mkdir()
    sample = folder / "VID-X.mov"
    sample.write_bytes(b"x")
    before = session_guard._fingerprint(folder)
    assert session_guard._fingerprint(folder) == before
    os.utime(sample, ns=(1, 1))  # a timestamp-only change, like the baseline incident
    assert session_guard._fingerprint(folder) != before
    (folder / "new.mov").write_bytes(b"y")
    assert session_guard._fingerprint(folder) != before
