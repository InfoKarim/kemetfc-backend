"""The controlled learning loop. Every step that matters needs a named human, and nothing trains by itself.

  new video -> AI analysis -> coach review -> approved training example -> dataset version
        -> (a human requests training) -> candidate model -> evaluation gates -> (a human approves) -> (a human releases)

Uploading or analysing a video only records ANALYSED. It cannot create a dataset, a training request or a model. A
training run needs a single-use request made by a named human for one dataset version. Approving or releasing a
model needs another named human act, and the registry refuses synthetic-trained models. There is no scheduler, no
upload hook and no deployment code in this package; the audit log is append-only and hash-chained so a change to
history is detectable.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from app.ml1 import consent as consent_rules
from app.ml1 import registry as registry_module
from app.ml1 import registry_adapter, train

EXAMPLE_STATES = ("ANALYSED", "COACH_REVIEWED", "APPROVED_EXAMPLE", "IN_DATASET_VERSION", "REJECTED")


class WorkflowError(ValueError):
    pass


def _human(ref: str) -> None:
    if not str(ref).startswith("AC-") or len(str(ref)) != 19:
        raise WorkflowError("this step needs a named human (an AC-... reference); automated actors cannot do it")


class LearningWorkflow:
    def __init__(self, log_path: Path):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.log_path.exists():
            self.log_path.write_text("")
        self._requests: dict[str, dict] = {}
        self._states: dict[str, str] = {}
        self._replay()

    # -- log ------------------------------------------------------------------------------------------------------------
    def _entries(self) -> list[dict]:
        return [json.loads(line) for line in self.log_path.read_text().splitlines() if line.strip()]

    def _append(self, event: str, actor: str, data: dict, now: datetime | None = None) -> dict:
        entries = self._entries()
        previous = entries[-1]["hash"] if entries else "0" * 64
        entry = {"n": len(entries) + 1, "at": (now or datetime.now(timezone.utc).replace(tzinfo=None)).isoformat(), "event": event, "actor": actor, "data": data, "previous": previous}
        entry["hash"] = hashlib.sha256(json.dumps({k: v for k, v in entry.items() if k != "hash"}, sort_keys=True).encode()).hexdigest()
        with self.log_path.open("a") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def verify_log(self) -> bool:
        previous = "0" * 64
        for entry in self._entries():
            body = {k: v for k, v in entry.items() if k != "hash"}
            if entry["previous"] != previous or hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest() != entry["hash"]:
                return False
            previous = entry["hash"]
        return True

    def _replay(self) -> None:
        for e in self._entries():
            d = e["data"]
            if e["event"] in ("ANALYSED", "COACH_REVIEWED", "APPROVED_EXAMPLE", "REJECTED"):
                self._states[d["video_id"]] = e["event"]
            elif e["event"] == "IN_DATASET_VERSION":
                for v in d["video_ids"]:
                    self._states[v] = "IN_DATASET_VERSION"
            elif e["event"] == "TRAINING_REQUESTED":
                self._requests[d["request_id"]] = {**d, "used": False}
            elif e["event"] == "CANDIDATE_TRAINED":
                self._requests[d["request_id"]]["used"] = True

    # -- example lifecycle ------------------------------------------------------------------------------------------------
    def state_of(self, video_id: str) -> str | None:
        return self._states.get(video_id)

    def ingest_analysis(self, video_id: str, analysis_sha256: str, now: datetime | None = None) -> None:
        """A video was analysed. This is ALL an upload or an analysis does: it never creates a request or a model."""
        if self._states.get(video_id):
            raise WorkflowError(f"{video_id} is already in the workflow ({self._states[video_id]})")
        self._append("ANALYSED", "system", {"video_id": video_id, "analysis_sha256": analysis_sha256}, now)
        self._states[video_id] = "ANALYSED"

    def coach_review(self, video_id: str, reviewer_ref: str, *, approved: bool, now: datetime | None = None) -> None:
        _human(reviewer_ref)
        if self._states.get(video_id) != "ANALYSED":
            raise WorkflowError("a video must be ANALYSED before a coach reviews it")
        event = "COACH_REVIEWED" if approved else "REJECTED"
        self._append(event, reviewer_ref, {"video_id": video_id}, now)
        self._states[video_id] = event

    def approve_example(self, video_id: str, approver_ref: str, meta: dict, *, now: datetime | None = None,
                        withdrawn_refs: set[str] | None = None, registry=None) -> None:
        _human(approver_ref)
        if self._states.get(video_id) != "COACH_REVIEWED":
            raise WorkflowError("an example needs a coach review before it can be approved for training")
        when = (now or datetime.now(timezone.utc)).replace(tzinfo=None)
        problems = consent_rules.eligibility_problems(meta, when, withdrawn_refs) + registry_adapter.registry_problems(meta, registry, when)
        if problems:
            raise WorkflowError("the example is not eligible for learning: " + ", ".join(sorted(set(problems))))
        self._append("APPROVED_EXAMPLE", approver_ref, {"video_id": video_id, "example_id": meta["example_id"]}, now)
        self._states[video_id] = "APPROVED_EXAMPLE"

    def add_to_dataset(self, video_ids: list[str], dataset_version: str, dataset_fingerprint: str, actor_ref: str,
                       now: datetime | None = None) -> None:
        _human(actor_ref)
        for v in video_ids:
            if self._states.get(v) != "APPROVED_EXAMPLE":
                raise WorkflowError(f"{v} is not an approved training example")
        self._append("IN_DATASET_VERSION", actor_ref, {"video_ids": sorted(video_ids), "dataset_version": dataset_version,
                                                       "dataset_fingerprint": dataset_fingerprint}, now)
        for v in video_ids:
            self._states[v] = "IN_DATASET_VERSION"

    # -- training, approval, release: each a human act ---------------------------------------------------------------------
    def request_training(self, dataset_version: str, requested_by_ref: str, now: datetime | None = None) -> str:
        _human(requested_by_ref)
        request_id = "TR-" + hashlib.sha256(f"{dataset_version}:{requested_by_ref}:{len(self._entries())}".encode()).hexdigest()[:12]
        data = {"request_id": request_id, "dataset_version": dataset_version, "requested_by_ref": requested_by_ref}
        self._append("TRAINING_REQUESTED", requested_by_ref, data, now)
        self._requests[request_id] = {**data, "used": False}
        return request_id

    def pending_training_requests(self) -> list[str]:
        return sorted(r for r, v in self._requests.items() if not v["used"])

    def train_from_request(self, request_id: str, dataset: dict, splits: dict, *, root: Path, name: str, config: dict | None = None,
                           now: datetime | None = None, withdrawn_refs: set[str] | None = None, registry=None) -> Path:
        req = self._requests.get(request_id)
        if req is None or req["used"]:
            raise WorkflowError("training needs a single-use request made by a named human")
        if req["dataset_version"] != dataset["version"]:
            raise WorkflowError("the request was for a different dataset version")
        directory = train.train_candidate(dataset, splits, root=root, name=name, requested_by_ref=req["requested_by_ref"], config=config,
                                          now=now, withdrawn_refs=withdrawn_refs, registry=registry)
        self._append("CANDIDATE_TRAINED", req["requested_by_ref"], {"request_id": request_id, "model": f"{name}/{directory.name}"}, now)
        req["used"] = True
        return directory

    def approve_model(self, model_dir: Path, approver_ref: str, reason: str, now: datetime | None = None, registry=None) -> dict:
        _human(approver_ref)
        approval = registry_module.approve_candidate(model_dir, approver_ref=approver_ref, reason=reason, now=now, registry=registry)
        self._append("MODEL_APPROVED", approver_ref, {"model": f"{Path(model_dir).parent.name}/{Path(model_dir).name}"}, now)
        return approval

    def release_model(self, model_dir: Path, releaser_ref: str, now: datetime | None = None, registry=None) -> dict:
        _human(releaser_ref)
        marker = registry_module.release_marker(model_dir, releaser_ref=releaser_ref, now=now, registry=registry)
        self._append("MODEL_RELEASE_RECORDED", releaser_ref, {"model": f"{Path(model_dir).parent.name}/{Path(model_dir).name}", "deployed": False}, now)
        return marker
