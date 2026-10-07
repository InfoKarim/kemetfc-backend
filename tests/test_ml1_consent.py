"""ML-1 consent enforcement: who may be learned from, and what happens when consent changes."""
import copy
from datetime import timedelta

import pytest

pytest.importorskip("torch", reason="PyTorch is needed for the ML-1 training tests (requirements-ml.txt)")

from app.ml1 import consent as c
from app.ml1 import dataset as ds
from app.ml1 import registry, train, workflow
from app.ml1.synthetic import make_synthetic_examples
from tests.ml1_helpers import HUMAN, HUMAN2, NOW, SALT, real_examples, real_meta, real_pairs
from tests.ml1_registry_fixture import make_registry


def problems(meta, **kw):
    return c.eligibility_problems(meta, NOW, **kw)


def test_a_fully_consented_real_example_is_eligible():
    assert problems(real_meta()) == []


def test_every_missing_piece_of_consent_makes_a_real_example_ineligible():
    m = real_meta(); m["consent"] = {"kind": "NONE"}
    assert "NO_GUARDIAN_ML_TRAINING_CONSENT" in problems(m)
    m = real_meta(); m["consent"].pop("kind")
    assert "NO_GUARDIAN_ML_TRAINING_CONSENT" in problems(m)
    m = real_meta(); m["consent"]["purpose"] = "video_analysis"
    assert "CONSENT_PURPOSE_NOT_ML_TRAINING" in problems(m)
    m = real_meta(); m["consent"]["withdrawn_at"] = (NOW - timedelta(days=1)).isoformat()
    assert "CONSENT_WITHDRAWN" in problems(m)
    m = real_meta(); m["consent"]["expires_at"] = (NOW - timedelta(seconds=1)).isoformat()
    assert "CONSENT_EXPIRED" in problems(m)
    m = real_meta(); m["consent"]["granted_at"] = (NOW + timedelta(days=1)).isoformat()
    assert "CONSENT_NOT_YET_GRANTED" in problems(m)
    m = real_meta(); m["consent"]["granted_at"] = None
    assert "CONSENT_NOT_YET_GRANTED" in problems(m)
    m = real_meta(); m["consent"]["dataset_entry_status"] = "pending_review"
    assert "DATASET_ENTRY_NOT_APPROVED" in problems(m)
    m = real_meta(); m["consent"]["covers_all_visible_children"] = False
    assert "CONSENT_NOT_VERIFIED_FOR_EVERY_VISIBLE_CHILD" in problems(m)
    m = real_meta(); m["consent"]["covers_all_visible_children"] = "yes"
    assert "CONSENT_NOT_VERIFIED_FOR_EVERY_VISIBLE_CHILD" in problems(m)
    m = real_meta(); m["consent"]["reviewed_by_ref"] = "someone"
    assert "NO_NAMED_CONSENT_REVIEWER" in problems(m)
    m = real_meta(); m["consent"]["consent_ref"] = "guardian-123"
    assert "CONSENT_REFERENCE_MISSING" in problems(m)


def test_withdrawal_after_acceptance_is_honoured_at_every_later_check():
    m = real_meta()
    ref = m["consent"]["consent_ref"]
    assert problems(m) == []
    assert "CONSENT_WITHDRAWN" in problems(m, withdrawn_refs={ref})
    with pytest.raises(c.ConsentError):
        c.assert_eligible(m, NOW, {ref})
    assert problems(m, withdrawn_refs={"CR-0000000000000000"}) == []


def test_third_party_content_is_never_eligible_whatever_consent_is_claimed():
    m = real_meta(); m["provenance"]["third_party_content"] = True
    assert "THIRD_PARTY_CONTENT" in problems(m)


def test_the_instagram_reel_and_the_tv_recording_are_blocked_even_with_perfect_consent():
    for recording in ("7228B688-F84E-486E-80BA-34D826914785", "EA8BCAB9-3A61-4FAC-943F-52889A552D8C"):
        m = real_meta(); m["provenance"]["recording_id"] = recording
        assert any(p.startswith("BLOCKED_SOURCE") for p in problems(m)), recording
        m = real_meta(); m["assessment_id"] = recording
        assert any(p.startswith("BLOCKED_SOURCE") for p in problems(m)), recording
    for sha in ("429b45774f22ea21be8f49f1ef4ba5df55c3fd0a45ce6579caace26ed8d609e3", "D68D9BE3617A5C01148C75AC8F835654D2D9B7E303F4D8582F97D9C1AF0F1627"):
        m = real_meta(); m["provenance"]["source_video_sha256"] = sha
        assert any(p.startswith("BLOCKED_SOURCE") for p in problems(m)), sha


def test_a_real_example_needs_the_target_to_have_been_chosen_by_a_coach():
    m = real_meta(); m["target"]["identity_verified_by_coach"] = False
    assert "TARGET_PLAYER_NOT_SELECTED_BY_COACH" in problems(m)
    m = real_meta(); m["target"]["source"] = "DIAGNOSTIC_MOST_PROMINENT"
    assert "TARGET_PLAYER_NOT_SELECTED_BY_COACH" in problems(m)
    m = real_meta(); m["target"]["source"] = "SYNTHETIC_TEST_TAP"
    assert "TARGET_PLAYER_NOT_SELECTED_BY_COACH" in problems(m)


def test_labels_must_come_from_a_coach_review_never_from_the_ai():
    m = real_meta(); m["labels"][0]["status"] = "AI_PREDICTED"
    assert any(p.startswith("LABEL_NOT_COACH_REVIEWED") for p in problems(m))
    m = real_meta(); m["labels"][0]["source"] = "MODEL"
    assert any(p.startswith("LABEL_NOT_COACH_REVIEWED") for p in problems(m))
    m = real_meta(); m["labels"][0]["reviewed_by_ref"] = None
    assert any(p.startswith("LABEL_NOT_COACH_REVIEWED") for p in problems(m))
    # a synthetic fixture's generator label is not a review, and is accepted only on synthetic examples
    m = real_meta(); m["labels"] = [{"label_type": "drill_type", "value": "x", "status": "SYNTHETIC_GROUND_TRUTH", "source": "SYNTHETIC_GENERATOR"}]
    assert any(p.startswith("LABEL_NOT_COACH_REVIEWED") for p in problems(m))


def test_synthetic_examples_must_say_so_and_real_examples_cannot_pretend_to_be_synthetic():
    syn = make_synthetic_examples(1, 1, 1)[0][0]
    assert problems(syn) == []
    syn2 = copy.deepcopy(syn); syn2["consent"] = {"kind": c.GUARDIAN_CONSENT_KIND}
    assert "SYNTHETIC_EXAMPLE_MUST_DECLARE_SYNTHETIC_CONSENT_KIND" in problems(syn2)
    real_pretending = real_meta(); real_pretending["consent"] = {"kind": c.SYNTHETIC_CONSENT_KIND}
    assert "NO_GUARDIAN_ML_TRAINING_CONSENT" in problems(real_pretending)
    unknown = real_meta(); unknown["provenance"]["source"] = "SCRAPED_FROM_THE_WEB"
    assert any(p.startswith("UNKNOWN_SOURCE") for p in problems(unknown))


def test_player_ids_are_pseudonymous_stable_salted_and_never_the_raw_id():
    a = c.anonymous_player_id("PLAYER-123", SALT)
    assert a.startswith("AP-") and "PLAYER-123" not in a and a == c.anonymous_player_id("PLAYER-123", SALT)
    assert a != c.anonymous_player_id("PLAYER-124", SALT) and a != c.anonymous_player_id("PLAYER-123", b"another-salt-0123456789abcdef")
    assert c.consent_ref("C1", SALT) != c.coach_ref("C1", SALT)                 # different kinds of reference never collide
    for bad in (b"", b"short", None, "text-not-bytes-0123456789"):
        with pytest.raises(c.ConsentError):
            c.anonymous_player_id("P", bad)
    with pytest.raises(c.ConsentError):
        c.anonymous_player_id("", SALT)
    m = real_meta(); m["anonymous_player_id"] = "PLAYER-123"
    assert "PLAYER_ID_NOT_ANONYMIZED" in problems(m)


def test_an_ineligible_example_stops_the_whole_dataset_from_being_built():
    ex = real_examples(6, 1, 1)
    ex[3][0]["consent"]["covers_all_visible_children"] = False
    reg, _ = make_registry(real_pairs(6, 1))
    with pytest.raises(c.ConsentError, match="CONSENT_NOT_VERIFIED_FOR_EVERY_VISIBLE_CHILD"):
        ds.build_dataset(ex, version="x", now=NOW, registry=reg)


def test_a_withdrawal_after_the_dataset_was_saved_blocks_loading_it(tmp_path):
    ex = real_examples(6, 1, 1)
    reg, _ = make_registry(real_pairs(6, 1))
    d = ds.build_dataset(ex, version="x", now=NOW, registry=reg)
    ds.save_dataset(tmp_path / "d", d)
    assert ds.load_dataset(tmp_path / "d", now=NOW, registry=reg)["version"] == "x"
    ref = ex[0][0]["consent"]["consent_ref"]
    with pytest.raises(c.ConsentError, match="CONSENT_WITHDRAWN"):
        ds.load_dataset(tmp_path / "d", now=NOW, withdrawn_refs={ref}, registry=reg)
    with pytest.raises(c.ConsentError, match="CONSENT_EXPIRED"):
        ds.load_dataset(tmp_path / "d", now=NOW + timedelta(days=400), registry=reg)


def test_consent_is_checked_again_at_training_time_not_only_when_the_dataset_was_built(tmp_path):
    ex = real_examples(10, 2, 2)
    reg, _ = make_registry(real_pairs(10, 2))
    d = ds.build_dataset(ex, version="x", now=NOW, registry=reg)
    sp = ds.split_dataset(d, seed=1)
    ref = ex[0][0]["consent"]["consent_ref"]
    with pytest.raises(c.ConsentError, match="CONSENT_WITHDRAWN"):
        train.train_candidate(d, sp, root=tmp_path, name="m", requested_by_ref=HUMAN, now=NOW, withdrawn_refs={ref}, config={"epochs": 1}, registry=reg)
    assert not list(tmp_path.glob("*"))                                       # nothing was trained or written


@pytest.mark.trains_models
def test_a_model_trained_on_data_whose_consent_is_later_withdrawn_is_invalidated_and_cannot_be_loaded(tmp_path, monkeypatch):
    monkeypatch.setenv("KEMET_ML_TRAINING_ENABLED", "1")
    ex = real_examples(10, 2, 2)
    reg, _ = make_registry(real_pairs(10, 2))
    d = ds.build_dataset(ex, version="x", now=NOW, registry=reg)
    model_dir = train.train_candidate(d, ds.split_dataset(d, seed=1), root=tmp_path, name="m", requested_by_ref=HUMAN, now=NOW, config={"epochs": 1}, registry=reg)
    registry.load_model(model_dir)
    hit = registry.invalidate_for_withdrawn_consent(tmp_path, {ex[0][0]["consent"]["consent_ref"]})
    assert hit == [f"m/{model_dir.name}"]
    with pytest.raises(registry.RegistryError, match="invalidated"):
        registry.load_model(model_dir)
    assert "MODEL_INVALIDATED" in registry.approval_problems(model_dir)
    assert registry.invalidate_for_withdrawn_consent(tmp_path, {"CR-ffffffffffffffff"}) == []          # an unrelated consent touches nothing
    assert (model_dir / "weights.pt").exists()                                # nothing is deleted


def test_the_workflow_refuses_to_approve_an_example_without_consent():
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as t:
        wf = workflow.LearningWorkflow(pathlib.Path(t) / "log.jsonl")
        wf.ingest_analysis("v1", "a" * 64)
        wf.coach_review("v1", HUMAN, approved=True)
        bad = real_meta(); bad["consent"]["withdrawn_at"] = NOW.isoformat()
        with pytest.raises(workflow.WorkflowError, match="CONSENT_WITHDRAWN"):
            wf.approve_example("v1", HUMAN2, bad, now=NOW, registry=make_registry([("player-1", "vid-1")])[0])
        assert wf.state_of("v1") == "COACH_REVIEWED"
