"""ML-1 review round 2, fix 2: consent of EVERY child visible in a video.

What is tested: with no trusted coverage record, real data is refused (fail closed); a claim in the example, a general "approved"
dataset entry and a saved `passed: true` never stand in for it; a coverage record must name this video and this file version and
list live consents, and every gate that comes later re-checks it.

What is NOT tested, and cannot be: real coverage. No coverage store exists in the platform. The success paths below use
`MockCoverageEvidenceNotRealConsent`, a stand-in that proves nothing about real consent. No training, no weights, no real person.
"""
import json
from datetime import timedelta

import pytest

pytest.importorskip("torch", reason="the ML-1 modules import PyTorch")

from app.db_models import GuardianConsentDB
from app.ml1 import consent as c
from app.ml1 import dataset as ds
from app.ml1 import registry, registry_adapter as ra, train
from tests.ml1_helpers import HUMAN, NOW, SALT, real_examples, real_meta, real_pairs, video_sha
from tests.ml1_registry_fixture import MockCoverageEvidenceNotRealConsent as MockCoverage, make_registry
from tests.test_ml1_model_use_gate import candidate, lineage_for, refs_for

NOT_VERIFIABLE = "VISIBLE_CHILD_CONSENT_COVERAGE_NOT_VERIFIABLE"
PAIRS = [("player-1", "vid-1"), ("player-2", "vid-2")]


@pytest.fixture(autouse=True)
def no_weights(monkeypatch):
    monkeypatch.setattr(registry, "inspect_candidate", lambda directory: (object(), {"card": json.loads((directory / "card.json").read_text()), "config": {}}))


def two_children():
    """vid-1 shows player-1 (the linked player) AND player-2; the stand-in coverage store lists both consents."""
    return make_registry(PAIRS, coverage=MockCoverage({"vid-1": ["consent-player-1", "consent-player-2"], "vid-2": ["consent-player-2"]}))


# ---- the default adapter cannot prove coverage, so real data is refused ---------------------------------------------------------------------
def test_the_players_own_consent_and_an_approved_entry_are_not_enough_by_default():
    reg, _ = make_registry([("player-1", "vid-1")], coverage=None)          # the real default: no coverage store
    assert reg.verify_example(real_meta(), NOW) == [NOT_VERIFIABLE]


@pytest.mark.parametrize("claim", [True, False, "yes", None])
def test_a_covers_all_visible_children_claim_never_opens_the_gate(claim):
    reg, _ = make_registry([("player-1", "vid-1")], coverage=None)
    meta = real_meta()
    meta["consent"]["covers_all_visible_children"] = claim
    assert reg.verify_example(meta, NOW) == [NOT_VERIFIABLE]


def test_a_missing_unreachable_or_malformed_coverage_store_refuses():
    for evidence in (MockCoverage({}), MockCoverage({"vid-1": ["consent-player-1"]}, fail=True)):
        reg, _ = make_registry([("player-1", "vid-1")], coverage=evidence)
        assert reg.verify_example(real_meta(), NOW) == [NOT_VERIFIABLE]

    class Garbage:
        def record_for_video(self, video_id):
            return {"covers_all": True}                                      # a flag-shaped answer is not a record

    reg, _ = make_registry([("player-1", "vid-1")], coverage=Garbage())
    assert reg.verify_example(real_meta(), NOW) == [NOT_VERIFIABLE]


@pytest.mark.parametrize("override,expected", [
    ({"video_id": "vid-9"}, "COVERAGE_RECORD_NOT_FOR_THIS_VIDEO"),
    ({"video_sha256": "0" * 64}, "COVERAGE_RECORD_NOT_FOR_THIS_VIDEO_VERSION"),
    ({"scope": "THE_LINKED_PLAYER_ONLY"}, "COVERAGE_SCOPE_IS_NOT_ALL_VISIBLE_PERSONS"),
    ({"consent_ids": ()}, "COVERAGE_LISTS_NO_CONSENTS"),
    ({"consent_ids": ("consent-player-2",)}, "COVERAGE_DOES_NOT_INCLUDE_THE_VIDEO_PLAYER_CONSENT"),
    ({"reviewed_by_user_id": ""}, "COVERAGE_HAS_NO_NAMED_REVIEWER"),
    ({"reviewed_at": NOW + timedelta(days=3)}, "COVERAGE_REVIEW_IS_DATED_IN_THE_FUTURE"),
    ({"consent_ids": ("consent-player-1", "consent-ghost")}, "COVERAGE_CONSENT_NOT_FOUND_IN_REGISTRY"),
])
def test_a_coverage_record_that_is_not_for_this_video_version_or_scope_is_refused(override, expected):
    reg, _ = make_registry([("player-1", "vid-1")], coverage=MockCoverage({"vid-1": ["consent-player-1"]}, {"vid-1": override}))
    assert any(p.startswith(expected) for p in reg.verify_example(real_meta(), NOW)), reg.verify_example(real_meta(), NOW)


def test_the_example_checksum_must_still_match_before_coverage_is_even_considered():
    reg, _ = make_registry([("player-1", "vid-1")])
    meta = real_meta()
    meta["provenance"]["source_video_sha256"] = "f" * 64
    assert "VIDEO_CHECKSUM_DOES_NOT_MATCH_REGISTRY" in reg.verify_example(meta, NOW)


# ---- withdrawal or expiry of ANY consent inside a coverage record ---------------------------------------------------------------------------
def test_withdrawal_or_expiry_of_a_second_childs_consent_refuses_the_video():
    reg, factory = two_children()
    assert reg.verify_example(real_meta(), NOW) == []                               # stand-in evidence only: proves the code path, not real consent
    ref2 = c.consent_ref("consent-player-2", SALT)
    with factory() as db:
        db.query(GuardianConsentDB).filter(GuardianConsentDB.consent_id == "consent-player-2").update({"withdrawn_at": NOW})
        db.commit()
    assert f"COVERAGE_CONSENT_CONSENT_WITHDRAWN:{ref2}" in reg.verify_example(real_meta(), NOW)
    with factory() as db:
        db.query(GuardianConsentDB).filter(GuardianConsentDB.consent_id == "consent-player-2").update({"withdrawn_at": None, "expires_at": NOW})
        db.commit()
    assert f"COVERAGE_CONSENT_CONSENT_EXPIRED:{ref2}" in reg.verify_example(real_meta(), NOW)


# ---- success path (stand-in) and the dataset / training gates ---------------------------------------------------------------------------------------
def test_with_a_standin_coverage_store_a_dataset_can_be_built_and_records_its_lineage():
    ex = real_examples(6, 1, 1)
    reg, _ = make_registry(real_pairs(6, 1))
    d = ds.build_dataset(ex, version="x", now=NOW, registry=reg)                    # a dataset is data, not weights
    assert {i["video_id"] for i in d["coverage_lineage"]} == {m["video_id"] for m, _ in ex} and all(len(i["video_sha256"]) == 64 for i in d["coverage_lineage"])


def test_dataset_building_loading_and_training_all_refuse_without_coverage_evidence(tmp_path):
    ex = real_examples(6, 1, 1)
    with_standin, _ = make_registry(real_pairs(6, 1))
    d = ds.build_dataset(ex, version="x", now=NOW, registry=with_standin)
    saved = ds.save_dataset(tmp_path / "ds", d)
    default, _ = make_registry(real_pairs(6, 1), coverage=None)
    with pytest.raises(c.ConsentError, match=NOT_VERIFIABLE):
        ds.build_dataset(ex, version="x", now=NOW, registry=default)
    with pytest.raises(c.ConsentError, match=NOT_VERIFIABLE):
        ds.load_dataset(saved, now=NOW, registry=default)
    ds.load_dataset(saved, now=NOW, registry=with_standin)                           # the same files load once evidence exists
    with pytest.raises(c.ConsentError, match=NOT_VERIFIABLE):                       # the training gate runs the same check before anything else
        train.train_candidate(d, ds.split_dataset(d, seed=1), root=tmp_path / "models", name="m", requested_by_ref=HUMAN, now=NOW, registry=default)
    assert not (tmp_path / "models").exists()                                       # nothing was trained or written


# ---- model gates: approval and use re-check coverage, whatever was saved ------------------------------------------------------------------------------
def test_a_real_model_without_lineage_or_without_a_coverage_store_cannot_be_approved_or_used(tmp_path):
    reg, _ = make_registry([("player-1", "vid-1")])
    legacy = candidate(tmp_path, refs_for("player-1"), approved=True, lineage=None, name="legacy")           # an older artifact: no lineage recorded
    for problems in (registry.approval_problems(legacy, registry=reg, now=NOW), registry.use_problems(legacy, registry=reg, purpose="USE", now=NOW)):
        assert "COVERAGE_LINEAGE_MISSING_FOR_REAL_DATA_MODEL" in problems and NOT_VERIFIABLE in problems
    with pytest.raises(registry.RegistryError, match=NOT_VERIFIABLE):
        registry.load_model_for_use(legacy, registry=reg, purpose="EVALUATION", now=NOW)
    default, _ = make_registry([("player-1", "vid-1")], coverage=None)
    full = candidate(tmp_path, refs_for("player-1"), approved=True, name="full")
    with pytest.raises(registry.RegistryError, match=NOT_VERIFIABLE):
        registry.load_model_for_use(full, registry=default, purpose="USE", now=NOW)          # consents are valid and the report says passed: still refused


def test_a_registry_object_with_no_coverage_method_is_refused_not_trusted(tmp_path):
    class OldStyle:
        def verify_consent_refs(self, refs, now):
            return []

    d = candidate(tmp_path, refs_for("player-1"), approved=True)
    assert NOT_VERIFIABLE in registry.use_problems(d, registry=OldStyle(), purpose="USE", now=NOW)


def test_a_withdrawal_inside_the_coverage_blocks_the_model_although_the_training_players_consent_is_fine(tmp_path):
    reg, factory = two_children()
    d = candidate(tmp_path, refs_for("player-1"), approved=True, lineage=lineage_for(1))        # trained on vid-1 only; its consents list shows player-1 alone
    registry.load_model_for_use(d, registry=reg, purpose="USE", now=NOW)                          # stand-in evidence: path check only
    with factory() as db:
        db.query(GuardianConsentDB).filter(GuardianConsentDB.consent_id == "consent-player-2").update({"withdrawn_at": NOW})
        db.commit()
    with pytest.raises(registry.RegistryError, match="COVERAGE_NO_LONGER_VALID:COVERAGE_CONSENT_CONSENT_WITHDRAWN"):
        registry.load_model_for_use(d, registry=reg, purpose="USE", now=NOW)
    assert any(p.startswith("COVERAGE_NO_LONGER_VALID") for p in registry.approval_problems(d, registry=reg, now=NOW))
    assert not (d / "invalidated.json").exists()


def test_a_lineage_that_names_another_file_version_or_an_unknown_video_is_refused(tmp_path):
    reg, _ = two_children()
    stale = [{"video_id": c.video_ref("vid-1", SALT), "video_sha256": "0" * 64}]
    unknown = [{"video_id": c.video_ref("vid-404", SALT), "video_sha256": video_sha("vid-404")}]
    for name, lineage, expected in (("stale", stale, "COVERAGE_RECORD_NOT_FOR_THIS_VIDEO_VERSION"), ("unknown", unknown, "COVERAGE_VIDEO_NOT_IN_REGISTRY")):
        d = candidate(tmp_path, refs_for("player-1"), approved=True, lineage=lineage, name=name)
        assert any(expected in p for p in registry.use_problems(d, registry=reg, purpose="USE", now=NOW)), name


def test_the_adapter_itself_never_returns_clean_for_lineage_without_evidence():
    default, _ = make_registry([("player-1", "vid-1")], coverage=None)
    assert default.verify_coverage_lineage(lineage_for(1), NOW) == [f"{NOT_VERIFIABLE}:{c.video_ref('vid-1', SALT)}"]
    assert default.verify_coverage_lineage([], NOW) == [NOT_VERIFIABLE]
