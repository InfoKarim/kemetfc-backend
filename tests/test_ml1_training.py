"""ML-1 training pipeline: reproducibility, model loading, versioning, evaluation, approval gates, the controlled workflow."""
import hashlib
import json
import pathlib
import re

import numpy as np
import pytest

pytest.importorskip("torch", reason="PyTorch is needed for the ML-1 training tests (requirements-ml.txt)")
import torch

from app.ml1 import dataset as ds
from app.ml1 import registry, train, workflow
from app.ml1.config import APPROVAL_GATES, LABELS
from app.ml1.model import PoseTemporalCNN, input_channels, to_tensors
from app.ml1.synthetic import make_synthetic_examples
import os

from tests.ml1_helpers import HUMAN, HUMAN2, NOW, real_examples, real_meta, real_pairs
from tests.ml1_registry_fixture import make_registry

CFG = {"epochs": 6}


@pytest.fixture(autouse=True)
def _training_switch(request, monkeypatch):
    """Training stays off for every test except the ones a human has opted into."""
    if request.node.get_closest_marker("trains_models"):
        monkeypatch.setenv("KEMET_ML_TRAINING_ENABLED", "1")
    else:
        monkeypatch.delenv("KEMET_ML_TRAINING_ENABLED", raising=False)


@pytest.fixture(scope="module")
def synth():
    d = ds.build_dataset(make_synthetic_examples(12, 2, 4), version="synth-1")
    return d, ds.split_dataset(d, seed=4)


def fit(synth, root, **kw):
    d, sp = synth
    return train.train_candidate(d, sp, root=root, name="pose_tcn", requested_by_ref=HUMAN, config={**CFG, **kw.pop("config", {})}, now=NOW, **kw)


# ---- the network -----------------------------------------------------------------------------------------------------------------
def test_the_network_returns_embeddings_and_a_classifier_only_when_asked_for():
    ex = make_synthetic_examples(2, 1, 2)
    x, valid, target, observed = to_tensors(ex)
    assert x.shape == (4, input_channels(), 48) and valid.shape == (4, 48)
    plain = PoseTemporalCNN()
    out = plain(x, valid)
    assert out["embedding"].shape == (4, 32) and out["logits"] is None and out["reconstruction"].shape == (4, 26, 48)
    assert PoseTemporalCNN(n_classes=3)(x, valid)["logits"].shape == (4, 3)


def test_hidden_joints_are_removed_from_the_input_and_unobserved_joints_can_never_be_hidden():
    ex = make_synthetic_examples(1, 1, 2)
    kp_mask = torch.from_numpy(np.stack([a["keypoint_mask"] for _, a in ex]))
    seen = np.argwhere(kp_mask.numpy())[0]                                     # (example, frame, joint) of an observed joint
    hide = torch.zeros_like(kp_mask); hide[tuple(seen)] = True
    x, _, target, _ = to_tensors(ex)
    x_hidden, _, target_hidden, _ = to_tensors(ex, hide)
    b, t, k = seen
    assert x[b, k * 3, t] != 0 and x_hidden[b, k * 3, t] == 0 and x_hidden[b, k * 3 + 1, t] == 0 and x_hidden[b, k * 3 + 2, t] == 0
    assert x_hidden[b, 13 * 3 + k, t] == 0                                      # its mask is zero too
    assert torch.equal(target, target_hidden) and target_hidden[b, t, k].abs().sum() > 0       # the target keeps the true value
    ex_missing = np.argwhere(~kp_mask.numpy())[0]
    assert train._hide_mask(kp_mask, 1.0, torch.Generator().manual_seed(0)).numpy()[tuple(ex_missing)] == False


def test_the_embedding_is_pooled_only_over_frames_with_a_usable_pose():
    ex = make_synthetic_examples(1, 1, 1)
    model = PoseTemporalCNN().eval()
    x, valid, _, _ = to_tensors(ex)
    with torch.no_grad():
        h = model.blocks(model.stem(x))
        only = torch.zeros_like(valid); only[0, 10] = True
        assert torch.allclose(model(x, only)["embedding"], model.embed(h[:, :, 10]), atol=1e-6)      # exactly that one frame
        nothing = torch.zeros_like(valid)
        assert torch.allclose(model(x, nothing)["embedding"], model.embed(torch.zeros(1, h.shape[1])), atol=1e-6)


# ---- training reproducibility ---------------------------------------------------------------------------------------------------
@pytest.mark.trains_models
def test_training_twice_with_the_same_data_split_and_seed_gives_identical_weights_and_metrics(synth, tmp_path):
    a = fit(synth, tmp_path / "a")
    b = fit(synth, tmp_path / "b")
    ra, rb = (json.loads((p / "report.json").read_text()) for p in (a, b))
    assert ra["weights_sha256"] == rb["weights_sha256"]
    assert ra["evaluation"] == rb["evaluation"] and ra["training"]["history"] == rb["training"]["history"]
    c = fit(synth, tmp_path / "c", config={"seed": 99})
    assert json.loads((c / "report.json").read_text())["weights_sha256"] != ra["weights_sha256"]


@pytest.mark.trains_models
def test_the_report_records_everything_needed_to_reproduce_and_audit_a_run(synth, tmp_path):
    r = json.loads((fit(synth, tmp_path) / "report.json").read_text())
    assert r["dataset"]["fingerprint"] == synth[0]["fingerprint"] and r["dataset"]["version"] == "synth-1" and r["dataset"]["synthetic_only"] is True
    assert r["training"]["seed"] == 1337 and r["training"]["deterministic"] and r["training"]["requested_by_ref"] == HUMAN
    assert r["dataset"]["split_sha256"] and r["git_revision"] and r["torch"] == torch.__version__ and r["no_player_scores"] is True
    assert r["leakage_check"]["passed"] and r["consent_audit"]["passed"]


def test_training_must_be_requested_by_a_named_human_and_needs_all_three_splits(synth, tmp_path):
    d, sp = synth
    for who in ("system", "scheduler", "AI", ""):
        with pytest.raises(train.TrainingRefused, match="named human"):
            train.train_candidate(d, sp, root=tmp_path, name="m", requested_by_ref=who, config=CFG)
    with pytest.raises(train.TrainingRefused, match="empty"):
        train.train_candidate(d, {"train": sp["train"], "val": sp["val"] + sp["test"], "test": []}, root=tmp_path, name="m", requested_by_ref=HUMAN, config=CFG)
    assert not list(tmp_path.iterdir())


def test_training_refuses_leaky_splits(synth, tmp_path):
    d, sp = synth
    bad = {"train": sp["train"] + sp["test"][:1], "val": sp["val"], "test": sp["test"]}
    with pytest.raises(ds.DatasetError, match="leakage"):
        train.train_candidate(d, bad, root=tmp_path, name="m", requested_by_ref=HUMAN, config=CFG)


# ---- evaluation --------------------------------------------------------------------------------------------------------------------
@pytest.mark.trains_models
def test_evaluation_reports_each_split_against_a_stated_baseline_and_only_hides_observed_joints(synth, tmp_path):
    ev = json.loads((fit(synth, tmp_path, config={"epochs": 25}) / "report.json").read_text())["evaluation"]
    assert set(("train", "val", "test")) <= set(ev) and "mean observed position" in ev["baseline"]
    for split in ("train", "val", "test"):
        assert ev[split]["n_hidden_values"] > 0 and ev[split]["baseline_mse"] > 0
    assert ev["test"]["reconstruction_improvement_over_baseline"] > 0.3          # it really learns structure (synthetic: trivially)


@pytest.mark.trains_models
def test_no_classifier_is_trained_without_enough_reviewed_labels_and_the_report_says_why(tmp_path):
    d = ds.build_dataset(make_synthetic_examples(6, 1, 3), version="few")
    sp = ds.split_dataset(d, seed=1, min_groups=3)
    r = json.loads((train.train_candidate(d, sp, root=tmp_path, name="m", requested_by_ref=HUMAN, config=CFG, now=NOW) / "report.json").read_text())
    assert r["classifier"]["trained"] is False and "not enough reviewed labels" in r["classifier"]["reason"]
    assert "classification" not in r["evaluation"]


@pytest.fixture(scope="module")
def synth_big():
    d = ds.build_dataset(make_synthetic_examples(30, 2, 5), version="synth-big")
    return d, ds.split_dataset(d, seed=4)


@pytest.mark.trains_models
def test_a_classifier_appears_only_when_each_class_has_enough_reviewed_examples_from_enough_players(synth_big, tmp_path):
    ex = synth_big[0]["examples"]
    train_ex = [ex[i] for i in synth_big[1]["train"]]
    classes, why = train.class_plan(train_ex)
    assert classes == ["SYNTH_DRILL_A", "SYNTH_DRILL_B", "SYNTH_DRILL_C"] and why == "enough reviewed labels"
    only_one_class = [e for e in train_ex if e[0]["drill_type"] == "SYNTH_DRILL_A"]
    assert train.class_plan(only_one_class)[0] is None                                    # a classifier needs at least two classes
    thin = [e for e in train_ex if e[0]["drill_type"] != "SYNTH_DRILL_C"][:50]
    assert train.class_plan(thin)[0] is None                                              # too few examples per class
    one_player = [e for e in train_ex if e[0]["anonymous_player_id"] == train_ex[0][0]["anonymous_player_id"]]
    assert train.class_plan(one_player * 10)[0] is None                                   # repeated examples from one player do not count
    r = json.loads((fit(synth_big, tmp_path, config={"epochs": 10}) / "report.json").read_text())
    assert r["classifier"]["trained"] and "test_macro_f1" in r["evaluation"]["classification"]
    conf = np.array(r["evaluation"]["classification"]["test"]["confusion_matrix"])
    assert conf.sum() == r["evaluation"]["classification"]["test"]["n_examples"]


def test_an_example_without_a_reviewed_label_belongs_to_no_class():
    ex = real_examples(10, 2, 3)
    for meta, _ in ex[::2]:
        meta["labels"] = []
    assert [train.label_of(m) is not None for m, _ in ex] == [i % 2 == 1 for i in range(len(ex))]


# ---- versioning and loading ---------------------------------------------------------------------------------------------------------
@pytest.mark.trains_models
def test_each_run_is_a_new_version_that_is_never_overwritten(synth, tmp_path):
    a, b = fit(synth, tmp_path), fit(synth, tmp_path)
    assert a.name != b.name and a.name.startswith("v0001-") and b.name.startswith("v0002-") and synth[0]["fingerprint"][:8] in a.name
    assert registry.list_versions(tmp_path, "pose_tcn") == [a.name, b.name]
    model, _ = registry.inspect_candidate(a)
    with pytest.raises(registry.RegistryError, match="already exists"):
        registry.save_candidate(tmp_path, "pose_tcn", model, report=json.loads((a / "report.json").read_text()), classes=None, version=a.name)


@pytest.mark.trains_models
def test_a_saved_model_loads_and_reproduces_its_own_outputs(synth, tmp_path):
    d = fit(synth, tmp_path)
    model, info = registry.inspect_candidate(d)
    again, _ = registry.inspect_candidate(d)
    x, valid, _, _ = to_tensors(synth[0]["examples"][:5])
    assert torch.equal(model(x, valid)["embedding"], again(x, valid)["embedding"]) and not model.training
    assert info["card"]["status"] == "CANDIDATE" and info["card"]["released"] is False
    e = train.embed(model, synth[0]["examples"][:5])
    assert e.shape == (5, 32) and np.isfinite(e).all()


@pytest.mark.trains_models
def test_a_changed_weights_file_or_an_unknown_architecture_is_refused(synth, tmp_path):
    d = fit(synth, tmp_path)
    model, _ = registry.inspect_candidate(d)
    state = model.state_dict()
    key = next(iter(state)); state[key] = state[key] + 1.0
    torch.save(state, d / "weights.pt")
    with pytest.raises(registry.RegistryError, match="SHA-256"):
        registry.inspect_candidate(d)
    d2 = fit(synth, tmp_path / "x")
    cfg = json.loads((d2 / "config.json").read_text()); cfg["architecture"] = "something-else/9"
    (d2 / "config.json").write_text(json.dumps(cfg))
    with pytest.raises(registry.RegistryError, match="unsupported architecture"):
        registry.inspect_candidate(d2)


@pytest.mark.trains_models
def test_the_registry_entry_is_always_experimental_never_active(synth, tmp_path):
    entry = registry.registry_entry(fit(synth, tmp_path))
    assert entry["status"] == "experimental" and entry["model_type"] == "pose_tcn_embedding" and entry["training_dataset_version"] == "synth-1"
    assert "SYNTHETIC" in entry["notes"]


# ---- approval: synthetic never, gates always, a human always ------------------------------------------------------------------------------
@pytest.mark.trains_models
def test_a_model_trained_on_synthetic_fixtures_can_never_be_approved_or_released(synth, tmp_path):
    d = fit(synth, tmp_path, config={"epochs": 25})
    assert "TRAINED_ON_SYNTHETIC_DATA_NEVER_APPROVABLE" in registry.approval_problems(d)
    with pytest.raises(registry.RegistryError, match="SYNTHETIC"):
        registry.approve_candidate(d, approver_ref=HUMAN, reason="it looks good to me, ship it")
    with pytest.raises(registry.RegistryError, match="approved by a human"):
        registry.release_marker(d, releaser_ref=HUMAN)
    assert json.loads((d / "card.json").read_text())["status"] == "CANDIDATE"


@pytest.fixture(scope="module")
def real_candidate(tmp_path_factory):
    """Trains once on fake real-style examples, with every consent confirmed by a real adapter on a seeded scratch DB.
    Only runs for the opt-in trains_models tests."""
    ex = real_examples(40, 2, 8)
    reg, _ = make_registry(real_pairs(40, 2))
    d = ds.build_dataset(ex, version="fake-real-fixture", now=NOW, registry=reg)
    sp = ds.split_dataset(d, seed=2)
    root = tmp_path_factory.mktemp("real")
    os.environ["KEMET_ML_TRAINING_ENABLED"] = "1"
    try:
        path = train.train_candidate(d, sp, root=root, name="pose_tcn", requested_by_ref=HUMAN, config={"epochs": 20}, now=NOW, registry=reg)
    finally:
        os.environ.pop("KEMET_ML_TRAINING_ENABLED", None)
    return path, ex, reg


@pytest.mark.trains_models
def test_approval_needs_a_named_human_a_written_reason_and_passing_gates(real_candidate):
    path, _, reg = real_candidate
    problems = registry.approval_problems(path, registry=reg, now=NOW)
    assert problems == [], problems                       # (fabricated-consent fixture in a temp dir: tests the code path only)
    for who in ("system", "bot", "AC-short", "", "CR-0123456789abcdef"):
        with pytest.raises(registry.RegistryError, match="named human"):
            registry.approve_candidate(path, approver_ref=who, reason="reviewed the evaluation carefully", registry=reg, now=NOW)
    with pytest.raises(registry.RegistryError, match="written reason"):
        registry.approve_candidate(path, approver_ref=HUMAN2, reason="ok", registry=reg, now=NOW)


@pytest.mark.trains_models
def test_release_is_a_separate_human_act_and_only_writes_a_marker_it_deploys_nothing(real_candidate, tmp_path):
    import shutil
    path = tmp_path / "pose_tcn" / real_candidate[0].name
    shutil.copytree(real_candidate[0], path)
    with pytest.raises(registry.RegistryError, match="approved by a human"):
        registry.release_marker(path, releaser_ref=HUMAN)
    reg = real_candidate[2]
    registry.approve_candidate(path, approver_ref=HUMAN2, reason="evaluation reviewed with the coaches", now=NOW, registry=reg)
    assert json.loads((path / "card.json").read_text())["status"] == "APPROVED_CANDIDATE"
    marker = registry.release_marker(path, releaser_ref=HUMAN, now=NOW, registry=reg)
    assert marker["deployed"] is False and json.loads((path / "card.json").read_text())["released"] is True
    assert "deployed" in marker and not list(path.glob("*.py"))


@pytest.mark.trains_models
def test_a_candidate_that_does_not_beat_the_baseline_or_has_too_few_test_examples_cannot_be_approved(real_candidate, tmp_path):
    import shutil
    path = tmp_path / "pose_tcn" / real_candidate[0].name
    shutil.copytree(real_candidate[0], path)
    reg = real_candidate[2]
    report = json.loads((path / "report.json").read_text())
    report["evaluation"]["test"]["reconstruction_improvement_over_baseline"] = 0.01
    (path / "report.json").write_text(json.dumps(report))
    found = registry.approval_problems(path, registry=reg, now=NOW)
    assert "DOES_NOT_BEAT_THE_BASELINE" in found and "REPORT_CHANGED_AFTER_TRAINING" in found      # an edited report is noticed too
    report["evaluation"]["test"]["n_examples"] = APPROVAL_GATES["min_test_examples"] - 1
    (path / "report.json").write_text(json.dumps(report))
    assert "TOO_FEW_TEST_EXAMPLES" in registry.approval_problems(path, registry=reg, now=NOW)
    report["consent_audit"]["passed"] = False
    (path / "report.json").write_text(json.dumps(report))
    assert "CONSENT_AUDIT_NOT_PASSED" in registry.approval_problems(path, registry=reg, now=NOW)


# ---- the controlled learning loop ------------------------------------------------------------------------------------------------------
def wf(tmp_path):
    return workflow.LearningWorkflow(tmp_path / "audit.jsonl")


def test_uploading_and_analysing_a_video_never_creates_a_dataset_a_request_or_a_model(tmp_path, monkeypatch):
    monkeypatch.setattr(train, "train_candidate", lambda *a, **k: (_ for _ in ()).throw(AssertionError("training must not start")))
    w = wf(tmp_path)
    for n in range(5):
        w.ingest_analysis(f"video-{n}", "a" * 64)
    assert w.pending_training_requests() == [] and w.state_of("video-0") == "ANALYSED"
    assert not list(tmp_path.glob("**/weights.pt")) and not list(tmp_path.glob("**/manifest.json"))
    w.coach_review("video-0", HUMAN, approved=True)
    assert w.pending_training_requests() == []                 # not even a review starts training


def test_the_loop_must_be_followed_in_order_by_named_humans(tmp_path):
    w = wf(tmp_path)
    with pytest.raises(workflow.WorkflowError, match="ANALYSED"):
        w.coach_review("v1", HUMAN, approved=True)
    w.ingest_analysis("v1", "b" * 64)
    with pytest.raises(workflow.WorkflowError, match="already in the workflow"):
        w.ingest_analysis("v1", "b" * 64)
    with pytest.raises(workflow.WorkflowError, match="named human"):
        w.coach_review("v1", "system", approved=True)
    with pytest.raises(workflow.WorkflowError, match="coach review"):
        w.approve_example("v1", HUMAN, real_meta(), now=NOW, registry=make_registry([("player-1", "vid-1")])[0])
    w.coach_review("v1", HUMAN, approved=True)
    with pytest.raises(workflow.WorkflowError, match="not an approved training example"):
        w.add_to_dataset(["v1"], "ds-1", "f" * 64, HUMAN)
    reg, _ = make_registry([("player-1", "vid-1")])
    w.approve_example("v1", HUMAN2, real_meta(), now=NOW, registry=reg)
    w.add_to_dataset(["v1"], "ds-1", "f" * 64, HUMAN)
    assert w.state_of("v1") == "IN_DATASET_VERSION"
    w.ingest_analysis("v2", "c" * 64)
    w.coach_review("v2", HUMAN, approved=False)
    assert w.state_of("v2") == "REJECTED"
    with pytest.raises(workflow.WorkflowError):
        w.approve_example("v2", HUMAN, real_meta(), now=NOW, registry=make_registry([("player-1", "vid-1")])[0])


@pytest.mark.trains_models
def test_training_needs_a_single_use_request_from_a_named_human_for_that_dataset(tmp_path, synth):
    w = wf(tmp_path)
    with pytest.raises(workflow.WorkflowError, match="named human"):
        w.request_training("synth-1", "scheduler")
    with pytest.raises(workflow.WorkflowError, match="single-use"):
        w.train_from_request("TR-nope", synth[0], synth[1], root=tmp_path / "m", name="m")
    request = w.request_training("other-version", HUMAN, now=NOW)
    with pytest.raises(workflow.WorkflowError, match="different dataset version"):
        w.train_from_request(request, synth[0], synth[1], root=tmp_path / "m", name="m")
    request = w.request_training("synth-1", HUMAN, now=NOW)
    assert request in w.pending_training_requests()
    d = w.train_from_request(request, synth[0], synth[1], root=tmp_path / "m", name="pose_tcn", config=CFG, now=NOW)
    assert request not in w.pending_training_requests() and d.exists()
    with pytest.raises(workflow.WorkflowError, match="single-use"):
        w.train_from_request(request, synth[0], synth[1], root=tmp_path / "m", name="pose_tcn", config=CFG, now=NOW)
    with pytest.raises(registry.RegistryError):
        w.approve_model(d, HUMAN2, "looking at the evaluation with the coaches now")      # synthetic: refused, and nothing is logged as approved
    assert not any(e["event"] == "MODEL_APPROVED" for e in w._entries())


def test_the_audit_log_is_hash_chained_so_a_rewritten_history_is_detected(tmp_path):
    w = wf(tmp_path)
    w.ingest_analysis("v1", "a" * 64); w.coach_review("v1", HUMAN, approved=True)
    assert w.verify_log()
    lines = (tmp_path / "audit.jsonl").read_text().splitlines()
    forged = json.loads(lines[0]); forged["actor"] = "someone-else"
    (tmp_path / "audit.jsonl").write_text("\n".join([json.dumps(forged, sort_keys=True)] + lines[1:]) + "\n")
    assert not w.verify_log()
    reopened = workflow.LearningWorkflow(tmp_path / "audit.jsonl")
    assert not reopened.verify_log()


def test_state_survives_a_restart(tmp_path):
    w = wf(tmp_path)
    w.ingest_analysis("v1", "a" * 64); w.coach_review("v1", HUMAN, approved=True)
    r = w.request_training("ds-1", HUMAN)
    again = workflow.LearningWorkflow(tmp_path / "audit.jsonl")
    assert again.state_of("v1") == "COACH_REVIEWED" and again.pending_training_requests() == [r]


def test_there_is_no_scheduler_upload_hook_network_or_deployment_code_in_the_ml_package():
    import ast
    banned_modules = {"apscheduler", "schedule", "celery", "requests", "httpx", "urllib3", "fastapi", "starlette", "sqlalchemy", "docker", "kubernetes"}
    for path in pathlib.Path("app/ml1").glob("*.py"):
        if path.name == "registry_adapter.py":
            continue                                          # the one read-only door to the platform's consent records (checked below)
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            assert not (set(names) & banned_modules), (path.name, names)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert "deploy" not in node.name.lower() and "schedule" not in node.name.lower(), (path.name, node.name)
            if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute)):
                called = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
                assert "deploy" not in called.lower(), (path.name, called)
    # the only external process the package starts is `git rev-parse` for the revision stamp
    calls = re.findall(r"subprocess\.run\((\[[^\]]*\])", "\n".join(p.read_text() for p in pathlib.Path("app/ml1").glob("*.py")))
    assert calls == ['["git", "rev-parse", "HEAD"]']


# ---- regression protection --------------------------------------------------------------------------------------------------------------
@pytest.mark.trains_models
def test_golden_values_catch_an_accidental_change_to_the_architecture_the_fixture_or_the_loss(synth, tmp_path):
    model = PoseTemporalCNN()
    assert sum(p.numel() for p in model.parameters()) == 34954 and input_channels() == 80
    assert model.config == {"architecture": "kemet.ml1.pose_tcn/1", "channels": 48, "embedding_dim": 32, "n_classes": 0, "n_kp": 13, "n_ang": 10, "n_mot": 4}
    assert ds.build_dataset(make_synthetic_examples(6, 1, 2), version="s")["fingerprint"] == GOLDEN_FINGERPRINT
    ev = json.loads((fit(synth, tmp_path, config={"epochs": 25}) / "report.json").read_text())["evaluation"]["test"]
    assert ev["reconstruction_improvement_over_baseline"] >= GOLDEN_MIN_IMPROVEMENT
    assert ev["model_mse"] <= GOLDEN_MAX_TEST_MSE


GOLDEN_FINGERPRINT = "158515d6f63c5c480f2b92ede0fdf9a21c0a46c022cf9f657951f5d614879691"
GOLDEN_MIN_IMPROVEMENT = 0.5
GOLDEN_MAX_TEST_MSE = 0.03
