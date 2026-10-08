"""ML-1 review fix 2: loading or using a model that claims real data re-verifies the CURRENT consents every time.

No training and no weights are created: each "model" is a directory of small JSON records, and the integrity step
(`inspect_candidate`, which would rebuild the network) is replaced by a stand-in so nothing is loaded. What is tested is the
authorisation decision, which is the part this fix changes.
"""
import hashlib
import json

import pytest
from sqlalchemy import text

pytest.importorskip("torch", reason="the ML-1 registry module imports PyTorch")

from app.db_models import GuardianConsentDB
from app.ml1 import consent as c
from app.ml1 import registry, registry_adapter as ra
from tests.ml1_helpers import HUMAN, NOW, SALT, video_sha
from tests.ml1_registry_fixture import make_registry

WEIGHTS = "a" * 64


def refs_for(*players):
    return [c.consent_ref(f"consent-{p}", SALT) for p in players]


def lineage_for(count):
    return [{"video_id": c.video_ref(f"vid-{i + 1}", SALT), "video_sha256": video_sha(f"vid-{i + 1}")} for i in range(count)]


def candidate(tmp_path, refs, *, origin="REAL_CONSENTED", approved=False, passed=True, name="m", lineage="auto"):
    """JSON records only (no weights file). `approved` also writes the human approval records. `lineage` is the per-video coverage
    lineage the report carries (auto: vid-1.. one per consent ref)."""
    if lineage == "auto":
        lineage = lineage_for(len(refs) if isinstance(refs, list) and refs else 1)
    d = tmp_path / name / "v0001"
    d.mkdir(parents=True)
    synthetic = origin != "REAL_CONSENTED"
    report = {"dataset": {"version": "v", "fingerprint": "f" * 64, "synthetic_only": synthetic}, "training": {"seed": 1},
              "consent_audit": {"passed": passed, "consent_refs": refs, "synthetic_only": synthetic, "coverage_lineage": lineage}, "weights_sha256": WEIGHTS}
    (d / "report.json").write_text(json.dumps(report, sort_keys=True))
    card = {"name": "m", "version": "v0001", "status": "APPROVED_CANDIDATE" if approved else "CANDIDATE", "data_origin": origin, "released": False,
            "weights_sha256": WEIGHTS, "report_sha256": hashlib.sha256((d / "report.json").read_bytes()).hexdigest()}
    (d / "card.json").write_text(json.dumps(card, sort_keys=True))
    if approved:
        (d / "approval.json").write_text(json.dumps({"approved_by": HUMAN, "weights_sha256": WEIGHTS}))
    return d


@pytest.fixture(autouse=True)
def no_weights(monkeypatch):
    """The integrity step is not under test and must not load anything."""
    monkeypatch.setattr(registry, "inspect_candidate", lambda directory: (object(), {"card": json.loads((directory / "card.json").read_text()), "config": {}}))


def snapshot(directory):
    return sorted(p.name for p in directory.iterdir())


def test_valid_consents_allow_evaluation_and_use_of_an_approved_real_model(tmp_path):
    reg, _ = make_registry([("player-1", "vid-1"), ("player-2", "vid-2")])
    d = candidate(tmp_path, refs_for("player-1", "player-2"), approved=True)
    for purpose in ("EVALUATION", "USE"):
        _, info = registry.load_model_for_use(d, registry=reg, purpose=purpose, now=NOW)
        assert info["authorised_for"] == purpose and info["scope"] == "REAL_CONSENT_VERIFIED_AT_LOAD"


def test_a_consent_withdrawn_after_approval_blocks_use_without_any_invalidated_file(tmp_path):
    reg, factory = make_registry([("player-1", "vid-1"), ("player-2", "vid-2")])
    d = candidate(tmp_path, refs_for("player-1", "player-2"), approved=True)
    registry.load_model_for_use(d, registry=reg, purpose="USE", now=NOW)                    # fine today
    before = snapshot(d)
    with factory() as db:
        db.query(GuardianConsentDB).filter(GuardianConsentDB.consent_id == "consent-player-2").update({"withdrawn_at": NOW})
        db.commit()
    for purpose in ("EVALUATION", "USE"):
        with pytest.raises(registry.RegistryError, match="CONSENT_NO_LONGER_VALID:CONSENT_WITHDRAWN"):
            registry.load_model_for_use(d, registry=reg, purpose=purpose, now=NOW)
    assert not (d / "invalidated.json").exists() and snapshot(d) == before                  # the refusal came from the registry, not from a marker file


def test_an_expired_consent_blocks_use(tmp_path):
    reg, factory = make_registry([("player-1", "vid-1")])
    d = candidate(tmp_path, refs_for("player-1"), approved=True)
    with factory() as db:
        db.query(GuardianConsentDB).update({"expires_at": NOW})
        db.commit()
    with pytest.raises(registry.RegistryError, match="CONSENT_EXPIRED"):
        registry.load_model_for_use(d, registry=reg, purpose="USE", now=NOW)
    assert not (d / "invalidated.json").exists()


def test_a_missing_registry_fails_closed_for_a_real_data_model(tmp_path):
    d = candidate(tmp_path, refs_for("player-1"), approved=True)
    with pytest.raises(registry.RegistryError, match=ra.REGISTRY_NOT_CONFIGURED):
        registry.load_model_for_use(d, registry=None, purpose="USE", now=NOW)


def test_an_unreachable_registry_fails_closed(tmp_path):
    class Down:
        def verify_consent_refs(self, refs, now):
            raise ConnectionError("database unreachable")

    class Garbage:
        def verify_consent_refs(self, refs, now):
            return None

    d = candidate(tmp_path, refs_for("player-1"), approved=True)
    for reg in (Down(), Garbage()):
        with pytest.raises(registry.RegistryError, match=ra.REGISTRY_UNAVAILABLE):
            registry.load_model_for_use(d, registry=reg, purpose="USE", now=NOW)
    broken, factory = make_registry([("player-1", "vid-1")])
    with factory() as db:
        db.execute(text("DROP TABLE guardian_consents"))
        db.commit()
    with pytest.raises(registry.RegistryError, match=ra.REGISTRY_UNAVAILABLE):                # the real adapter turns a database failure into a refusal
        registry.load_model_for_use(d, registry=broken, purpose="USE", now=NOW)


@pytest.mark.parametrize("refs,expected", [([], "CONSENT_REFS_MISSING_FOR_REAL_DATA_MODEL"), (None, "CONSENT_REFS_MISSING_FOR_REAL_DATA_MODEL"),
                                           (["CR-xyz"], "CONSENT_REF_MALFORMED"), ([HUMAN], "CONSENT_REF_MALFORMED"), ([123], "CONSENT_REF_MALFORMED")])
def test_a_real_data_model_with_no_usable_consent_list_is_refused_even_with_a_working_registry(tmp_path, refs, expected):
    reg, _ = make_registry([("player-1", "vid-1")])
    d = candidate(tmp_path, refs, approved=True)
    with pytest.raises(registry.RegistryError, match=expected):
        registry.load_model_for_use(d, registry=reg, purpose="USE", now=NOW)
    assert expected in registry.approval_problems(d, registry=reg, now=NOW)                   # approval refuses the same way


def test_an_unknown_consent_reference_is_refused(tmp_path):
    reg, _ = make_registry([("player-1", "vid-1")])
    d = candidate(tmp_path, refs_for("player-1", "stranger"), approved=True)
    with pytest.raises(registry.RegistryError, match="CONSENT_NOT_FOUND_IN_REGISTRY"):
        registry.load_model_for_use(d, registry=reg, purpose="EVALUATION", now=NOW)


def test_an_edited_report_cannot_hide_the_consent_list(tmp_path):
    reg, _ = make_registry([("player-1", "vid-1")])
    d = candidate(tmp_path, refs_for("player-1"), approved=True)
    report = json.loads((d / "report.json").read_text())
    report["consent_audit"]["consent_refs"] = []
    (d / "report.json").write_text(json.dumps(report))
    with pytest.raises(registry.RegistryError, match="REPORT_CHANGED_AFTER_TRAINING"):
        registry.load_model_for_use(d, registry=reg, purpose="EVALUATION", now=NOW)


def test_a_card_that_disagrees_with_its_report_about_real_or_synthetic_is_refused(tmp_path):
    reg, _ = make_registry([("player-1", "vid-1")])
    d = candidate(tmp_path, refs_for("player-1"))
    card = json.loads((d / "card.json").read_text())
    card["data_origin"] = "SYNTHETIC"
    (d / "card.json").write_text(json.dumps(card))
    with pytest.raises(registry.RegistryError, match="DATA_ORIGIN_UNCLEAR"):
        registry.load_model_for_use(d, registry=reg, purpose="EVALUATION", now=NOW)


def test_use_needs_a_human_approval_but_evaluation_does_not(tmp_path):
    reg, _ = make_registry([("player-1", "vid-1")])
    d = candidate(tmp_path, refs_for("player-1"), approved=False)
    registry.load_model_for_use(d, registry=reg, purpose="EVALUATION", now=NOW)
    with pytest.raises(registry.RegistryError, match="MODEL_NOT_APPROVED_BY_A_HUMAN"):
        registry.load_model_for_use(d, registry=reg, purpose="USE", now=NOW)
    with pytest.raises(registry.RegistryError, match="UNKNOWN_PURPOSE"):
        registry.load_model_for_use(d, registry=reg, purpose="anything", now=NOW)


def test_a_synthetic_model_may_be_evaluated_with_its_scope_stated_and_is_never_for_use(tmp_path):
    d = candidate(tmp_path, [], origin="SYNTHETIC", approved=True)
    _, info = registry.load_model_for_use(d, registry=None, purpose="EVALUATION", now=NOW)      # no registry needed: it holds no real consent
    assert info["scope"] == "SYNTHETIC_EVALUATION_ONLY"
    with pytest.raises(registry.RegistryError, match="SYNTHETIC_MODEL_NEVER_FOR_USE"):
        registry.load_model_for_use(d, registry=None, purpose="USE", now=NOW)


def test_a_manual_invalidation_file_only_adds_a_refusal(tmp_path):
    reg, _ = make_registry([("player-1", "vid-1")])
    d = candidate(tmp_path, refs_for("player-1"), approved=True)
    (d / "invalidated.json").write_text("{}")
    with pytest.raises(registry.RegistryError, match="MODEL_INVALIDATED"):
        registry.load_model_for_use(d, registry=reg, purpose="EVALUATION", now=NOW)


def test_the_old_ungated_loader_is_gone_and_unreadable_records_fail_closed(tmp_path):
    with pytest.raises(registry.RegistryError, match="removed"):
        registry.load_model(tmp_path)
    assert registry.use_problems(tmp_path, registry=None, purpose="USE") == ["MODEL_RECORDS_UNREADABLE"]


def test_no_caller_in_the_repository_uses_the_removed_loader():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    hits = [str(p.relative_to(root)) for folder in ("app", "scripts", "learning_engine", "tests") for p in (root / folder).rglob("*.py")
            if "registry.load_model(" in p.read_text() and p.name != "test_ml1_model_use_gate.py"]
    assert hits == []
