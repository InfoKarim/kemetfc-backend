"""Model versions on disk: save, load, verify, approve. Nothing here deploys anything.

A trained model is a CANDIDATE. It becomes APPROVED_CANDIDATE only by a named human, only if it was trained on real
(non-synthetic) consented data, passed the evaluation gates on the held-out test split, and its dataset still passes
the consent audit. Released is a further, separate human act and is only a marker file: there is no deployment code.
"""
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import torch

from app.ml1.config import APPROVAL_GATES, MODEL_ARCHITECTURE
from app.ml1.consent import _REF
from app.ml1.registry_adapter import REGISTRY_NOT_CONFIGURED
from app.ml1.model import PoseTemporalCNN


class RegistryError(ValueError):
    pass


def weights_sha256(model: torch.nn.Module) -> str:
    digest = hashlib.sha256()
    for key in sorted(model.state_dict()):
        tensor = model.state_dict()[key].detach().cpu().contiguous()
        digest.update(key.encode() + str(tensor.dtype).encode() + str(tuple(tensor.shape)).encode() + tensor.numpy().tobytes())
    return digest.hexdigest()


def git_revision() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
                              cwd=Path(__file__).resolve().parents[2]).stdout.strip()
    except Exception:
        return None


def next_version(root: Path, name: str, dataset_fingerprint: str, seed: int) -> str:
    existing = [p for p in (Path(root) / name).glob("v*") if p.is_dir()] if (Path(root) / name).exists() else []
    return f"v{len(existing) + 1:04d}-{dataset_fingerprint[:8]}-s{seed}"


def save_candidate(root: Path, name: str, model: PoseTemporalCNN, *, report: dict, classes: list[str] | None, version: str | None = None) -> Path:
    root = Path(root)
    version = version or next_version(root, name, report["dataset"]["fingerprint"], report["training"]["seed"])
    directory = root / name / version
    if directory.exists():
        raise RegistryError(f"{name} {version} already exists; a version is never overwritten")
    directory.mkdir(parents=True)
    torch.save(model.state_dict(), directory / "weights.pt")
    sha = weights_sha256(model)
    (directory / "config.json").write_text(json.dumps({**model.config, "classes": classes or []}, indent=1, sort_keys=True))
    report = {**report, "weights_sha256": sha, "git_revision": git_revision(), "torch": torch.__version__}
    (directory / "report.json").write_text(json.dumps(report, indent=1, sort_keys=True, default=str))
    card = {"name": name, "version": version, "status": "CANDIDATE", "data_origin": "SYNTHETIC" if report["dataset"]["synthetic_only"] else "REAL_CONSENTED",
            "released": False, "weights_sha256": sha, "approvable": not report["dataset"]["synthetic_only"],
            "report_sha256": hashlib.sha256((directory / "report.json").read_bytes()).hexdigest(),
            "claims": ["reconstructs deliberately hidden joints better than a per-joint mean-pose baseline on held-out player/video groups"],
            "not_validated_for": ["dribbling skill or technique", "drill quality or suitability", "player assessment or ranking",
                                  "injury, medical or psychological conclusions", "any use on children without a fresh human review"],
            "note": "a candidate: not approved, not released, not deployed"}
    (directory / "card.json").write_text(json.dumps(card, indent=1, sort_keys=True))
    return directory


def load_model(directory: Path) -> tuple[PoseTemporalCNN, dict]:
    """Rebuild the network from its config, load the weights, and verify they are the ones that were saved."""
    directory = Path(directory)
    config = json.loads((directory / "config.json").read_text())
    card = json.loads((directory / "card.json").read_text())
    if config.get("architecture") != MODEL_ARCHITECTURE:
        raise RegistryError(f"unsupported architecture {config.get('architecture')}")
    if (directory / "invalidated.json").exists():
        raise RegistryError("this model was invalidated (for example a consent was withdrawn) and must not be used")
    model = PoseTemporalCNN(channels=config["channels"], embedding_dim=config["embedding_dim"], n_classes=config["n_classes"],
                            n_kp=config["n_kp"], n_ang=config["n_ang"], n_mot=config["n_mot"])
    model.load_state_dict(torch.load(directory / "weights.pt", map_location="cpu", weights_only=True))
    model.eval()
    if weights_sha256(model) != card["weights_sha256"]:
        raise RegistryError("the weights do not match their recorded SHA-256: the file was changed")
    return model, {"config": config, "card": card}


def list_versions(root: Path, name: str) -> list[str]:
    base = Path(root) / name
    return sorted(p.name for p in base.glob("v*") if p.is_dir()) if base.exists() else []


def registry_entry(directory: Path) -> dict:
    """The arguments the existing ModelRegistryService.register_model takes. Always status 'experimental': activating a
    model there is a separate, human decision this module never makes."""
    card = json.loads((Path(directory) / "card.json").read_text())
    report = json.loads((Path(directory) / "report.json").read_text())
    return {"model_name": f"ml1_{card['name']}", "model_version": card["version"], "model_type": "pose_tcn_embedding",
            "status": "experimental", "training_dataset_version": report["dataset"]["version"],
            "evaluation_metrics": report.get("evaluation"), "backend_artifact_version": card["weights_sha256"][:16],
            "notes": f"{card['data_origin']} - {card['note']}"}


def approval_problems(directory: Path, *, registry=None, now: datetime | None = None) -> list[str]:
    """Why this candidate may not be approved (or released) now. Recomputes what it can instead of trusting files: the
    weights and the report must still match the hashes recorded when they were written, and for real data the trusted
    registry must confirm, today, that every consent behind the training data is still valid."""
    directory = Path(directory)
    report = json.loads((directory / "report.json").read_text())
    card = json.loads((directory / "card.json").read_text())
    problems = []
    if hashlib.sha256((directory / "report.json").read_bytes()).hexdigest() != card.get("report_sha256"):
        problems.append("REPORT_CHANGED_AFTER_TRAINING")
    if report.get("weights_sha256") != card.get("weights_sha256"):
        problems.append("REPORT_AND_CARD_DISAGREE_ON_WEIGHTS")
    else:
        try:
            load_model(directory)
        except RegistryError as exc:
            if "invalidated" not in str(exc):
                problems.append("WEIGHTS_DO_NOT_MATCH_THE_RECORDED_HASH")
    if report["dataset"]["synthetic_only"] or card["data_origin"] != "REAL_CONSENTED":
        problems.append("TRAINED_ON_SYNTHETIC_DATA_NEVER_APPROVABLE")
    else:
        refs = set(report.get("consent_audit", {}).get("consent_refs", []))
        if registry is None:
            problems.append(REGISTRY_NOT_CONFIGURED)
        else:
            current = registry.verify_consent_refs(refs, (now or datetime.now(timezone.utc)).replace(tzinfo=None))
            problems += [f"CONSENT_NO_LONGER_VALID:{p}" for p in current]
    if not report.get("consent_audit", {}).get("passed"):
        problems.append("CONSENT_AUDIT_NOT_PASSED")
    if not report.get("leakage_check", {}).get("passed"):
        problems.append("LEAKAGE_CHECK_NOT_PASSED")
    ev = report.get("evaluation") or {}
    test = ev.get("test") or {}
    if test.get("n_examples", 0) < APPROVAL_GATES["min_test_examples"]:
        problems.append("TOO_FEW_TEST_EXAMPLES")
    improvement = test.get("reconstruction_improvement_over_baseline")
    if improvement is None or improvement < APPROVAL_GATES["min_reconstruction_improvement_over_baseline"]:
        problems.append("DOES_NOT_BEAT_THE_BASELINE")
    if ev.get("classification") and ev["classification"].get("test_macro_f1", 0) < APPROVAL_GATES["min_macro_f1_if_classifier"]:
        problems.append("CLASSIFIER_BELOW_MINIMUM")
    if (directory / "invalidated.json").exists():
        problems.append("MODEL_INVALIDATED")
    return problems


def approve_candidate(directory: Path, *, approver_ref: str, reason: str, now: datetime | None = None, registry=None) -> dict:
    """A named human approves a candidate. Refuses for synthetic training data, a failed gate or an anonymous approver."""
    directory = Path(directory)
    if not _REF.match(str(approver_ref)) or not approver_ref.startswith("AC-"):
        raise RegistryError("approval needs a named human approver reference (AC-...)")
    if not reason or len(reason.strip()) < 10:
        raise RegistryError("approval needs a written reason")
    problems = approval_problems(directory, registry=registry, now=now)
    if problems:
        raise RegistryError("candidate cannot be approved: " + ", ".join(problems))
    card = json.loads((directory / "card.json").read_text())
    approval = {"approved_by": approver_ref, "reason": reason, "approved_at": (now or datetime.now(timezone.utc).replace(tzinfo=None)).isoformat(),
                "weights_sha256": card["weights_sha256"]}
    (directory / "approval.json").write_text(json.dumps(approval, indent=1, sort_keys=True))
    card.update(status="APPROVED_CANDIDATE", note="approved by a human; not released, not deployed")
    (directory / "card.json").write_text(json.dumps(card, indent=1, sort_keys=True))
    return approval


def release_marker(directory: Path, *, releaser_ref: str, now: datetime | None = None, registry=None) -> dict:
    """Records the human decision to release. It deploys nothing; deployment is outside this module."""
    directory = Path(directory)
    if not (directory / "approval.json").exists():
        raise RegistryError("a model must be approved by a human before it can be released")
    if approval_problems(directory, registry=registry, now=now):
        raise RegistryError("the candidate no longer passes its approval gates")
    if not _REF.match(str(releaser_ref)) or not releaser_ref.startswith("AC-"):
        raise RegistryError("release needs a named human reference (AC-...)")
    marker = {"released_by": releaser_ref, "released_at": (now or datetime.now(timezone.utc).replace(tzinfo=None)).isoformat(), "deployed": False}
    (directory / "released.json").write_text(json.dumps(marker, indent=1, sort_keys=True))
    card = json.loads((directory / "card.json").read_text())
    card.update(status="RELEASED_MARKER", released=True, note="release recorded by a human; deployment is a separate, manual act")
    (directory / "card.json").write_text(json.dumps(card, indent=1, sort_keys=True))
    return marker


def invalidate_for_withdrawn_consent(root: Path, withdrawn_refs: set[str]) -> list[str]:
    """When a guardian withdraws consent, every model whose training data used it is marked invalid and cannot be
    loaded or approved. Returns the versions invalidated. Nothing is deleted."""
    hit = []
    for report_file in Path(root).glob("*/*/report.json"):
        report = json.loads(report_file.read_text())
        if withdrawn_refs & set(report.get("consent_audit", {}).get("consent_refs", [])):
            (report_file.parent / "invalidated.json").write_text(json.dumps(
                {"reason": "CONSENT_WITHDRAWN", "refs": sorted(withdrawn_refs & set(report["consent_audit"]["consent_refs"]))}, sort_keys=True))
            card_file = report_file.parent / "card.json"
            card = json.loads(card_file.read_text())
            card.update(status="INVALIDATED_RETRAIN_REQUIRED", released=False)
            card_file.write_text(json.dumps(card, indent=1, sort_keys=True))
            hit.append(f"{report_file.parent.parent.name}/{report_file.parent.name}")
    return sorted(hit)
