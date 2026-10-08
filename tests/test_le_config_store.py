"""Learning engine: configuration defaults, strictness, store safety, locking, audit."""
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from learning_engine import config as cfg
from learning_engine.store import AlreadyRunning, ResearchStore, StoreError, validate_state_dir
from tests.le_helpers import CH1, config, config_dict, paid_provider_dict, PAID_BUDGET

ROOT = Path(__file__).resolve().parent.parent


# ---- offline by default -----------------------------------------------------------------------------------------------------
def test_the_shipped_example_config_is_offline_unconfigured_and_free_of_secrets():
    text = (ROOT / "learning_engine/config.example.json").read_text()
    c = cfg.load_config(ROOT / "learning_engine/config.example.json")
    assert c.mode == "offline" and c.network_enabled is False and c.youtube.enabled is False and c.youtube.channels == () and c.providers == {}
    assert c.budget.daily_cost_usd == 0 and c.budget.daily_ai_requests == 0
    for word in ("sk-", "AIza", "Bearer", "api_key\": \"", "secret", "password", "token\":"):
        assert word.lower() not in text.lower(), word
    assert cfg.youtube_blockers(c, {}) and cfg.paid_blockers(c, "openai", {}) == ["PROVIDER_NOT_CONFIGURED"]


def test_an_empty_default_config_object_is_offline_too():
    c = cfg.parse_config({"schema": cfg.SCHEMA})
    assert c.mode == "offline" and not c.network_enabled and not c.youtube.channels


# ---- strictness --------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [
    {"mode": "turbo"}, {"network_enabled": "yes"}, {"timezone": "Mars/Base"}, {"surprise": 1}, {"schema": "other"},
    {"youtube": {"channels": [{"channel_id": "not-a-channel", "label": "x" * 6, "basis_for_allowing": "y" * 6, "reviewed_by": "z" * 6, "reviewed_on": "2026-10-01"}]}},
    {"youtube": {"channels": [{"channel_id": CH1, "label": "x" * 6, "basis_for_allowing": "y" * 6, "reviewed_by": "z" * 6}]}},
    {"youtube": {"channels": [{"channel_id": CH1, "label": "x" * 6, "basis_for_allowing": "", "reviewed_by": "z" * 6, "reviewed_on": "2026-10-01"}]}},
    {"youtube": {"search_calls_per_day": 101}}, {"youtube": {"units_per_day": 10_001}}, {"youtube": {"max_pages_per_channel": 4}},
    {"youtube": {"results_per_page": 51}}, {"youtube": {"query": "football passing"}}, {"youtube": {"api_key_env": "lowercase_name"}},
    {"youtube": {"api_key_transport": "cookie"}}, {"youtube": {"typo_setting": True}}, {"youtube": {"max_retries": 4}},
    {"retention": {"youtube_data_days": 31}}, {"retention": {"youtube_data_days": 5, "refresh_window_days": 5}},
    {"budget": {"daily_cost_usd": 5.01}}, {"budget": {"daily_ai_requests": 21}}, {"providers": {"gemini": {}}},
    {"providers": {"openai": {"max_call_usd": 6}}}, {"providers": {"openai": {"pricing": {"input_usd_per_million": 1, "output_usd_per_million": 1, "source_url": "http://x", "verified_on": "2026-10-01"}}}},
    {"providers": {"openai": {"pricing": {"input_usd_per_million": 1, "output_usd_per_million": 1, "source_url": "https://x.test", "verified_on": "yesterday"}}}},
])
def test_invalid_or_unknown_settings_are_refused(bad):
    with pytest.raises(cfg.ConfigError):
        config(**bad)


def test_duplicate_channels_and_a_symlinked_config_file_are_refused(tmp_path):
    ch = config_dict()["youtube"]["channels"][0]
    with pytest.raises(cfg.ConfigError, match="duplicate"):
        config(youtube={"channels": [ch, ch]})
    real = tmp_path / "c.json"; real.write_text(json.dumps(config_dict()))
    link = tmp_path / "link.json"; link.symlink_to(real)
    with pytest.raises(cfg.ConfigError, match="symbolic link"):
        cfg.load_config(link)
    with pytest.raises(cfg.ConfigError, match="cannot read"):
        cfg.load_config(tmp_path / "missing.json")


def test_every_channel_on_the_allowlist_carries_its_basis_reviewer_and_date():
    c = config()
    ch = c.youtube.channels[0]
    assert ch.basis_for_allowing and ch.reviewed_by and ch.reviewed_on == "2026-10-01"


def test_live_youtube_needs_every_condition_and_names_each_missing_one():
    off = config(mode="offline", network_enabled=False, youtube={"enabled": False, "channels": []})
    assert set(cfg.youtube_blockers(off, {})) >= {"MODE_IS_OFFLINE", "NETWORK_DISABLED", "YOUTUBE_NOT_ENABLED", "NO_CHANNEL_ALLOWLIST", "YOUTUBE_API_KEY_NOT_SET"}
    on = config()
    assert cfg.youtube_blockers(on, {}) == ["YOUTUBE_API_KEY_NOT_SET"]
    assert cfg.youtube_blockers(on, {"KEMET_LE_YOUTUBE_API_KEY": "x"}) == []


def test_a_paid_provider_needs_every_condition_and_names_each_missing_one():
    base = config(providers=paid_provider_dict(), budget=PAID_BUDGET)
    env = {"KEMET_LE_OPENAI_API_KEY": "x"}
    assert cfg.paid_blockers(base, "openai", env) == []
    assert "API_KEY_NOT_SET" in cfg.paid_blockers(base, "openai", {})
    assert "MODE_IS_OFFLINE" in cfg.paid_blockers(config(mode="offline", providers=paid_provider_dict(), budget=PAID_BUDGET), "openai", env)
    assert "NETWORK_DISABLED" in cfg.paid_blockers(config(network_enabled=False, providers=paid_provider_dict(), budget=PAID_BUDGET), "openai", env)
    assert "PROVIDER_NOT_ENABLED" in cfg.paid_blockers(config(providers=paid_provider_dict(enabled=False), budget=PAID_BUDGET), "openai", env)
    assert "MODEL_NOT_CONFIGURED" in cfg.paid_blockers(config(providers=paid_provider_dict(model=""), budget=PAID_BUDGET), "openai", env)
    no_price = paid_provider_dict(); no_price["openai"].pop("pricing")
    assert "PRICING_NOT_CONFIGURED_WITH_SOURCE" in cfg.paid_blockers(config(providers=no_price, budget=PAID_BUDGET), "openai", env)
    assert "PER_CALL_COST_CAP_NOT_SET" in cfg.paid_blockers(config(providers=paid_provider_dict(max_call_usd=0), budget=PAID_BUDGET), "openai", env)
    assert "DAILY_BUDGET_NOT_SET" in cfg.paid_blockers(config(providers=paid_provider_dict(), budget={**PAID_BUDGET, "daily_cost_usd": 0}), "openai", env)
    assert "COST_APPROVAL_NOT_RECORDED" in cfg.paid_blockers(config(providers=paid_provider_dict(), budget={**PAID_BUDGET, "paid_execution_approved_by": None}), "openai", env)
    assert cfg.paid_blockers(base, "anthropic", env) == ["PROVIDER_NOT_CONFIGURED"]


# ---- store safety ---------------------------------------------------------------------------------------------------------------
def test_the_state_is_private_versioned_and_separate(tmp_path):
    s = ResearchStore(tmp_path / "state")
    assert oct((tmp_path / "state").stat().st_mode & 0o777) == "0o700"
    assert oct(s.db_path.stat().st_mode & 0o777) == "0o600"
    assert s.schema_version() == 3 and s.db_path.name == "learning_engine.sqlite3"
    assert s.db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    s.close()
    again = ResearchStore(tmp_path / "state")                          # reopening keeps the version and does not break
    assert again.schema_version() == 3
    again.close()


def test_a_database_from_a_newer_engine_is_refused(tmp_path):
    s = ResearchStore(tmp_path / "state")
    s.db.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
    s.close()
    with pytest.raises(StoreError, match="newer version"):
        ResearchStore(tmp_path / "state")


def test_symlinks_relative_paths_application_data_and_the_repository_are_refused(tmp_path):
    real = tmp_path / "real"; real.mkdir()
    link = tmp_path / "link"; link.symlink_to(real)
    with pytest.raises(StoreError, match="symbolic link"):
        ResearchStore(link)
    with pytest.raises(StoreError, match="symbolic link"):
        ResearchStore(link / "inner")
    with pytest.raises(StoreError, match="absolute"):
        ResearchStore(Path("relative/state"))
    prod = tmp_path / "prod"; prod.mkdir(); (prod / "cloud_platform.db").write_text("x")
    with pytest.raises(StoreError, match="application's data"):
        ResearchStore(prod)
    uploads = tmp_path / "up"; (uploads / "uploads").mkdir(parents=True)
    with pytest.raises(StoreError, match="application's data"):
        ResearchStore(uploads)
    with pytest.raises(StoreError, match="outside the application repository"):
        validate_state_dir(ROOT / "inside", repo_root=ROOT)
    db_link_dir = tmp_path / "dbl"; db_link_dir.mkdir()
    (db_link_dir / "learning_engine.sqlite3").symlink_to(real / "x.sqlite")
    with pytest.raises(StoreError, match="symbolic link"):
        ResearchStore(db_link_dir)


def test_the_database_cannot_mark_anything_published():
    import sqlite3, tempfile
    s = ResearchStore(Path(tempfile.mkdtemp()) / "s")
    s.db.execute("INSERT INTO review_items(item_id,origin,source_video_id,stage,review_status,current_version,created_at,updated_at,published) VALUES ('AI-x','AI_GENERAL_DRAFT',NULL,'ai_draft','pending',1,'t','t',0)")
    with pytest.raises(sqlite3.IntegrityError):
        s.db.execute("UPDATE review_items SET published=1 WHERE item_id='AI-x'")
    with pytest.raises(sqlite3.IntegrityError):                           # an AI draft may not be tied to a source video
        s.db.execute("INSERT INTO review_items(item_id,origin,source_video_id,stage,review_status,current_version,created_at,updated_at,published) VALUES ('AI-y','AI_GENERAL_DRAFT','vid',  'ai_draft','pending',1,'t','t',0)")
    s.close()


def test_the_audit_log_is_hash_chained_and_a_rewrite_is_detected(tmp_path):
    s = ResearchStore(tmp_path / "s")
    s.audit("A", "x", {"n": 1}); s.audit("B", "y", {"n": 2}); s.audit("C", "z", {"n": 3})
    assert s.verify_audit()
    s.db.execute("UPDATE audit SET actor='someone-else' WHERE n=2")
    assert not s.verify_audit()
    s.close()


def test_transactions_roll_back_on_error(tmp_path):
    s = ResearchStore(tmp_path / "s")
    with pytest.raises(RuntimeError):
        with s.transaction() as db:
            db.execute("INSERT INTO meta(key,value) VALUES('probe','1')")
            raise RuntimeError("boom")
    assert s.db.execute("SELECT 1 FROM meta WHERE key='probe'").fetchone() is None
    s.close()


# ---- one run at a time -------------------------------------------------------------------------------------------------------------
def test_a_second_run_cannot_take_the_lock_in_this_process_or_another(tmp_path):
    a = ResearchStore(tmp_path / "s")
    a.acquire_run_lock()
    b = ResearchStore(tmp_path / "s")
    with pytest.raises(AlreadyRunning):
        b.acquire_run_lock()
    code = ("import sys; sys.path.insert(0, %r)\nfrom learning_engine.store import ResearchStore, AlreadyRunning\n"
            "s = ResearchStore(%r)\ntry:\n    s.acquire_run_lock(); print('GOT')\nexcept AlreadyRunning:\n    print('BLOCKED')\n" % (str(ROOT), str(tmp_path / "s")))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "BLOCKED", out.stderr
    a.release_run_lock()
    b.acquire_run_lock()                                                # free again once released
    b.close(); a.close()


def test_the_lock_file_must_not_be_a_symlink(tmp_path):
    s = ResearchStore(tmp_path / "s")
    (tmp_path / "s" / "run.lock").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(StoreError, match="symbolic link"):
        s.acquire_run_lock()
    s.close()
