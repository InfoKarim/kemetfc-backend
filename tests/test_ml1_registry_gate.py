"""ML-1 gates against TRUSTED records: the consent block inside an example is a claim, not evidence.

These tests use the real read-only adapter on a scratch SQLite database built from the platform's own tables. No model is
trained; every refusal below happens before training could start (and training is switched off anyway).
"""
import ast
import copy
import pathlib
from datetime import timedelta

import numpy as np
import pytest
from sqlalchemy import text

pytest.importorskip("torch", reason="PyTorch is needed to import the ML-1 training module")

from app.ml1 import consent as c
from app.ml1 import dataset as ds
from app.ml1 import registry, registry_adapter as ra, synthetic, train, workflow
from app.ml1.synthetic import make_synthetic_examples
from app.db_models import GuardianConsentDB, MLDatasetEntryDB
from tests.ml1_helpers import HUMAN, HUMAN2, NOW, SALT, real_examples, real_meta, real_pairs, video_sha
from tests.ml1_registry_fixture import make_registry


def verify(meta, reg, **kw):
    return reg.verify_example(meta, kw.get("now", NOW))


def test_the_registry_confirms_a_real_example_whose_every_fact_matches_the_platform_records():
    reg, _ = make_registry([("player-1", "vid-1")])
    assert verify(real_meta(), reg) == []


def test_a_user_written_consent_block_is_not_evidence_without_a_registry():
    meta = real_meta()
    assert c.eligibility_problems(meta, NOW) == []                       # the claim alone looks perfect ...
    with pytest.raises(c.ConsentError, match="REGISTRY_NOT_CONFIGURED_FAIL_CLOSED"):
        ds.build_dataset(real_examples(6, 1, 1), version="x", now=NOW)   # ... and is refused without a registry
    assert ra.registry_problems(meta, None, NOW) == [ra.REGISTRY_NOT_CONFIGURED]


def test_a_perfect_looking_claim_for_a_child_who_is_not_in_the_registry_is_refused():
    reg, _ = make_registry([("player-1", "vid-1")])
    stranger = real_meta(player="player-99", video="vid-99")
    problems = verify(stranger, reg)
    assert "CONSENT_NOT_FOUND_IN_REGISTRY" in problems and "VIDEO_NOT_IN_ML_DATASET_REGISTRY" in problems


@pytest.mark.parametrize("mutate,expected", [
    (lambda db: db.query(GuardianConsentDB).update({"withdrawn_at": NOW - timedelta(days=1)}), "CONSENT_WITHDRAWN"),
    (lambda db: db.query(GuardianConsentDB).update({"expires_at": NOW - timedelta(seconds=1)}), "CONSENT_EXPIRED"),
    (lambda db: db.query(GuardianConsentDB).update({"purposes": ["video_analysis"]}), "CONSENT_PURPOSE_NOT_ML_TRAINING"),
    (lambda db: db.query(GuardianConsentDB).update({"granted_at": NOW + timedelta(days=1)}), "CONSENT_NOT_YET_GRANTED"),
    (lambda db: db.query(MLDatasetEntryDB).update({"status": "pending_review"}), "DATASET_ENTRY_NOT_APPROVED"),
    (lambda db: db.query(MLDatasetEntryDB).update({"status": "rejected"}), "DATASET_ENTRY_NOT_APPROVED"),
    (lambda db: db.query(MLDatasetEntryDB).update({"reviewed_by_user_id": None}), "NO_NAMED_CONSENT_REVIEWER"),
    (lambda db: db.query(MLDatasetEntryDB).update({"reviewed_by_user_id": "someone-else"}), "REVIEWER_DOES_NOT_MATCH_REGISTRY"),
    (lambda db: db.query(MLDatasetEntryDB).update({"consent_id": "consent-other"}), "CONSENT_IS_NOT_THE_ONE_ON_THE_DATASET_ENTRY"),
])
def test_the_registry_refuses_when_the_platform_records_say_no(mutate, expected):
    reg, factory = make_registry([("player-1", "vid-1")])
    with factory() as db:
        mutate(db)
        db.commit()
    assert expected in verify(real_meta(), reg)


def test_a_changed_registry_is_noticed_every_time_not_cached():
    reg, factory = make_registry([("player-1", "vid-1")])
    assert verify(real_meta(), reg) == []
    with factory() as db:
        db.query(GuardianConsentDB).update({"withdrawn_at": NOW})
        db.commit()
    assert "CONSENT_WITHDRAWN" in verify(real_meta(), reg)


def test_pseudonyms_and_the_video_checksum_must_match_the_records():
    reg, _ = make_registry([("player-1", "vid-1"), ("player-2", "vid-2")])
    other_player = real_meta(); other_player["anonymous_player_id"] = c.anonymous_player_id("player-2", SALT)
    assert "PLAYER_PSEUDONYM_DOES_NOT_MATCH_REGISTRY" in verify(other_player, reg)
    wrong_sha = real_meta(); wrong_sha["provenance"]["source_video_sha256"] = video_sha("some-other-video")
    assert "VIDEO_CHECKSUM_DOES_NOT_MATCH_REGISTRY" in verify(wrong_sha, reg)
    wrong_consent = real_meta(); wrong_consent["consent"]["consent_ref"] = c.consent_ref("consent-player-2", SALT)
    assert "CONSENT_IS_NOT_THE_ONE_ON_THE_DATASET_ENTRY" in verify(wrong_consent, reg) or "CONSENT_PLAYER_IS_NOT_THE_VIDEO_PLAYER" in verify(wrong_consent, reg)


def test_a_different_salt_cannot_confirm_anything():
    reg, factory = make_registry([("player-1", "vid-1")])
    foreign = ra.SqlAlchemyConsentRegistry(factory, b"another-salt-0123456789abcdef")
    problems = foreign.verify_example(real_meta(), NOW)
    assert "CONSENT_NOT_FOUND_IN_REGISTRY" in problems and "VIDEO_NOT_IN_ML_DATASET_REGISTRY" in problems


def test_any_failure_to_reach_the_registry_is_a_refusal_never_a_pass():
    def broken():
        raise RuntimeError("database is down")
    down = ra.SqlAlchemyConsentRegistry(broken, SALT)
    assert down.verify_example(real_meta(), NOW) == [ra.REGISTRY_UNAVAILABLE]
    assert down.verify_consent_refs({"CR-0123456789abcdef"}, NOW) == [ra.REGISTRY_UNAVAILABLE]
    with pytest.raises(c.ConsentError, match="REGISTRY_UNAVAILABLE_FAIL_CLOSED"):
        ds.build_dataset(real_examples(6, 1, 1), version="x", now=NOW, registry=down)


def test_the_adapter_is_read_only_and_leaves_the_records_unchanged():
    reg, factory = make_registry([("player-1", "vid-1")])
    with factory() as db:
        before = [tuple(r) for r in db.execute(text("select * from guardian_consents")).fetchall()] + \
                 [tuple(r) for r in db.execute(text("select * from ml_dataset_entries")).fetchall()]
    reg.verify_example(real_meta(), NOW); reg.verify_consent_refs({real_meta()["consent"]["consent_ref"]}, NOW)
    with factory() as db:
        after = [tuple(r) for r in db.execute(text("select * from guardian_consents")).fetchall()] + \
                [tuple(r) for r in db.execute(text("select * from ml_dataset_entries")).fetchall()]
    assert before == after
    source = pathlib.Path("app/ml1/registry_adapter.py").read_text()
    tree = ast.parse(source)
    calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not (calls & {"add", "add_all", "commit", "flush", "merge", "delete", "bulk_insert_mappings", "executemany"})
    assert "PRAGMA" not in source                              # it never changes a (possibly shared) connection's settings


def test_a_read_only_sqlite_factory_can_verify_but_cannot_write(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import sessionmaker
    from app.database import Base
    from tests.ml1_registry_fixture import seed
    path = tmp_path / "registry.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    seed(sessionmaker(bind=engine, autoflush=False, expire_on_commit=False), [("player-1", "vid-1")])
    engine.dispose()
    factory = ra.readonly_sqlite_factory(path)
    reg = ra.SqlAlchemyConsentRegistry(factory, SALT)
    assert reg.verify_example(real_meta(), NOW) == []
    with factory() as db:
        with pytest.raises(OperationalError):
            db.execute(text("update guardian_consents set withdrawn_at = '2026-01-01'"))


def test_the_dataset_workflow_and_training_each_refuse_without_a_confirming_registry(tmp_path):
    ex = real_examples(10, 2, 2)
    reg, factory = make_registry(real_pairs(10, 2))
    d = ds.build_dataset(ex, version="x", now=NOW, registry=reg)
    sp = ds.split_dataset(d, seed=1)
    with factory() as db:                                                     # a guardian withdraws consent AFTER the dataset exists
        db.query(GuardianConsentDB).filter(GuardianConsentDB.consent_id == "consent-player-0").update({"withdrawn_at": NOW})
        db.commit()
    with pytest.raises(c.ConsentError, match="CONSENT_WITHDRAWN"):
        train.train_candidate(d, sp, root=tmp_path, name="m", requested_by_ref=HUMAN, now=NOW, registry=reg)
    with pytest.raises(c.ConsentError, match="REGISTRY_NOT_CONFIGURED"):
        train.train_candidate(d, sp, root=tmp_path, name="m", requested_by_ref=HUMAN, now=NOW)
    assert list(tmp_path.iterdir()) == []
    w = workflow.LearningWorkflow(tmp_path / "log.jsonl")
    w.ingest_analysis("v", "a" * 64); w.coach_review("v", HUMAN, approved=True)
    with pytest.raises(workflow.WorkflowError, match="REGISTRY_NOT_CONFIGURED"):
        w.approve_example("v", HUMAN2, real_meta(), now=NOW)


def test_with_every_gate_passed_training_is_still_switched_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("KEMET_ML_TRAINING_ENABLED", raising=False)
    ex = real_examples(10, 2, 2)
    reg, _ = make_registry(real_pairs(10, 2))
    d = ds.build_dataset(ex, version="x", now=NOW, registry=reg)
    with pytest.raises(train.TrainingDisabled, match="switched off"):
        train.train_candidate(d, ds.split_dataset(d, seed=1), root=tmp_path, name="m", requested_by_ref=HUMAN, now=NOW, registry=reg)
    assert list(tmp_path.iterdir()) == [] and train.training_enabled() is False
    for value in ("true", "yes", "0", ""):
        monkeypatch.setenv("KEMET_ML_TRAINING_ENABLED", value)
        assert train.training_enabled() is False                              # only the exact value "1"


# ---- real data cannot pass as synthetic ------------------------------------------------------------------------------------
def test_a_real_example_relabelled_synthetic_is_caught_because_it_is_not_the_generators_output():
    real = real_examples(1, 1, 1)[0]
    meta = copy.deepcopy(real[0])
    meta["provenance"].update(source="SYNTHETIC_FIXTURE", synthetic=True, generator="app.ml1.synthetic", seed=7, index=[0, 0, 0], drill="SYNTH_DRILL_A")
    meta["consent"] = {"kind": c.SYNTHETIC_CONSENT_KIND}
    meta["labels"] = [{"label_type": "drill_type", "value": "SYNTH_DRILL_A", "status": "SYNTHETIC_GROUND_TRUTH", "source": "SYNTHETIC_GENERATOR"}]
    meta["example_id"] = "SYN-000-0-00"
    meta["anonymous_player_id"] = c.anonymous_player_id("SYNTHETIC-PLAYER-000", synthetic.SYNTHETIC_SALT)
    problems = synthetic.verify_synthetic(meta, real[1])
    assert problems and problems[0].startswith("SYNTHETIC_CLAIM_NOT_PROVEN")
    with pytest.raises(c.ConsentError, match="claims to be synthetic but is not"):
        ds.build_dataset([(meta, real[1])], version="x")


def test_a_genuine_fixture_is_proven_and_any_tampering_with_its_numbers_breaks_the_proof():
    meta, arrays = make_synthetic_examples(2, 1, 1)[0]
    assert synthetic.verify_synthetic(meta, arrays) == []
    tampered = {k: v.copy() for k, v in arrays.items()}
    r, k = np.argwhere(tampered["keypoint_mask"])[0]
    tampered["keypoints"][r, k, 0] += 0.001
    assert synthetic.verify_synthetic(meta, tampered)
    no_recipe = copy.deepcopy(meta); no_recipe["provenance"].pop("index")
    assert synthetic.verify_synthetic(no_recipe, arrays)[0].startswith("SYNTHETIC_CLAIM_NOT_PROVEN")
    assert ds.build_dataset(make_synthetic_examples(2, 1, 1), version="ok")["synthetic_only"] is True


# ---- approval re-verifies consent today, and detects edited files -----------------------------------------------------------------------
def _fake_candidate(tmp_path, refs, synthetic_only=False):
    """A model directory written WITHOUT training (random-init weights) to test approval gates only."""
    import json
    import torch
    from app.ml1.model import PoseTemporalCNN
    torch.manual_seed(0)
    model = PoseTemporalCNN()
    improvement = 0.5
    report = {"dataset": {"version": "v", "fingerprint": "f" * 64, "synthetic_only": synthetic_only, "n_examples": 300, "split_sizes": {}, "split_sha256": "x"},
              "training": {"seed": 1, "requested_by_ref": HUMAN, "history": []}, "classifier": {"trained": False, "reason": "none"},
              "evaluation": {"test": {"n_examples": 80, "reconstruction_improvement_over_baseline": improvement}, "baseline": "mean pose"},
              "consent_audit": {"passed": True, "n_examples": 300, "consent_refs": sorted(refs), "synthetic_only": synthetic_only},
              "leakage_check": {"passed": True}, "no_player_scores": True}
    return registry.save_candidate(tmp_path, "m", model, report=report, classes=None)


def test_approval_rechecks_every_consent_behind_the_training_data_today(tmp_path):
    refs = {c.consent_ref(f"consent-player-{p}", SALT) for p in range(3)}
    reg, factory = make_registry([(f"player-{p}", f"vid-{p}") for p in range(3)])
    d = _fake_candidate(tmp_path, refs)
    assert registry.approval_problems(d, registry=reg, now=NOW) == []
    assert registry.approval_problems(d, now=NOW) == [ra.REGISTRY_NOT_CONFIGURED]
    with factory() as db:
        db.query(GuardianConsentDB).filter(GuardianConsentDB.consent_id == "consent-player-1").update({"withdrawn_at": NOW})
        db.commit()
    problems = registry.approval_problems(d, registry=reg, now=NOW)
    assert any(p.startswith("CONSENT_NO_LONGER_VALID:CONSENT_WITHDRAWN") for p in problems)
    with pytest.raises(registry.RegistryError, match="CONSENT_NO_LONGER_VALID"):
        registry.approve_candidate(d, approver_ref=HUMAN, reason="reviewed the evaluation with the coaches", registry=reg, now=NOW)
    assert not (d / "approval.json").exists()


def test_an_edited_report_or_edited_weights_are_noticed_at_approval(tmp_path):
    import json
    import torch
    refs = {c.consent_ref("consent-player-0", SALT)}
    reg, _ = make_registry([("player-0", "vid-0")])
    d = _fake_candidate(tmp_path, refs)
    report = json.loads((d / "report.json").read_text())
    report["evaluation"]["test"]["reconstruction_improvement_over_baseline"] = 0.99
    (d / "report.json").write_text(json.dumps(report))
    assert "REPORT_CHANGED_AFTER_TRAINING" in registry.approval_problems(d, registry=reg, now=NOW)
    d2 = _fake_candidate(tmp_path / "other", refs)
    state = torch.load(d2 / "weights.pt", weights_only=True)
    key = next(iter(state)); state[key] = state[key] + 1
    torch.save(state, d2 / "weights.pt")
    assert "WEIGHTS_DO_NOT_MATCH_THE_RECORDED_HASH" in registry.approval_problems(d2, registry=reg, now=NOW)


def test_every_candidate_states_what_it_is_and_is_not_validated_for(tmp_path):
    import json
    card = json.loads((_fake_candidate(tmp_path, set()) / "card.json").read_text())
    assert "mean-pose baseline" in card["claims"][0]
    for word in ("dribbling", "drill quality", "player assessment", "children"):
        assert any(word in item for item in card["not_validated_for"]), word
