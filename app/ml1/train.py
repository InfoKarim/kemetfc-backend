"""Dataset -> split -> train -> evaluate -> versioned CANDIDATE. Never approves, never releases, never deploys.

Self-supervised first: the network learns movement embeddings by predicting joints that were hidden ON PURPOSE, scored
only where a real observation exists (so it is never trained on a joint that was not seen, and no label is invented).
A drill classifier is added only if enough coach-reviewed labels exist (config.LABELS); otherwise there is none and the
report says why. Every run is deterministic on CPU for a given dataset, split and seed.
"""
import hashlib
import json
import os
import random
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch

from app.ml1 import dataset as ds
from app.ml1 import registry
from app.ml1.config import LABELS, MODEL_ARCHITECTURE, TRAINING
from app.ml1.model import PoseTemporalCNN, to_tensors


class TrainingRefused(ValueError):
    pass


class TrainingDisabled(TrainingRefused):
    pass


TRAINING_ENV = "KEMET_ML_TRAINING_ENABLED"


def training_enabled() -> bool:
    """Training is OFF unless a human switches it on for that process. Every gate runs first; this is the last door."""
    return os.environ.get(TRAINING_ENV) == "1"


def set_determinism(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)


def label_of(meta: dict, label_type: str = "drill_type") -> str | None:
    for label in meta.get("labels", []):
        if label.get("label_type") == label_type:
            return str(label["value"])
    return None


def class_plan(examples: list[tuple[dict, dict]], *, label_type: str = "drill_type") -> tuple[list[str] | None, str]:
    """The classes a classifier may be trained on, or (None, why not). Needs enough reviewed examples and enough
    different players per class; classes that do not qualify are left out rather than padded."""
    by_class: dict[str, list[dict]] = {}
    for meta, _ in examples:
        value = label_of(meta, label_type)
        if value is not None:
            by_class.setdefault(value, []).append(meta)
    ok = sorted(c for c, metas in by_class.items()
                if len(metas) >= LABELS["min_examples_per_class"] and len({m["anonymous_player_id"] for m in metas}) >= LABELS["min_players_per_class"])
    if len(ok) < LABELS["min_classes"]:
        counts = {c: (len(m), len({x['anonymous_player_id'] for x in m})) for c, m in by_class.items()}
        return None, f"not enough reviewed labels for a classifier (need {LABELS['min_classes']} classes each with >= {LABELS['min_examples_per_class']} examples from >= {LABELS['min_players_per_class']} players; have {counts})"
    return ok, "enough reviewed labels"


def mean_pose(examples: list[tuple[dict, dict]]) -> np.ndarray:
    """Baseline: per joint, the average observed position in the TRAINING examples (torso-normalized)."""
    total = None
    for _, a in examples:
        kp, m = a["keypoints"][..., :2], a["keypoint_mask"]
        s = (kp * m[..., None]).sum(axis=(0))
        c = m.sum(axis=0)
        total = (s, c) if total is None else (total[0] + s, total[1] + c)
    s, c = total
    return (s / np.maximum(c[:, None], 1)).astype(np.float32)


def _hide_mask(kp_mask: torch.Tensor, fraction: float, generator: torch.Generator) -> torch.Tensor:
    draw = torch.rand(kp_mask.shape, generator=generator)
    return (draw < fraction) & kp_mask


def reconstruction_metrics(model: PoseTemporalCNN, examples: list[tuple[dict, dict]], baseline: np.ndarray, *, seed: int,
                           fraction: float, batch_size: int = 32) -> dict:
    """Hide a fixed random 25% of the observed joints, ask the model for them, compare with the mean-pose baseline."""
    model.eval()
    gen = torch.Generator().manual_seed(seed)
    base = torch.from_numpy(baseline)
    m_err = b_err = 0.0
    n = 0
    with torch.no_grad():
        for start in range(0, len(examples), batch_size):
            batch = examples[start:start + batch_size]
            kp_mask = torch.from_numpy(np.stack([a["keypoint_mask"] for _, a in batch]))
            hide = _hide_mask(kp_mask, fraction, gen)
            x, valid, target, observed = to_tensors(batch, hide)
            out = model(x, valid)["reconstruction"]                      # B, K*2, T
            B, _, T = out.shape
            pred = out.permute(0, 2, 1).reshape(B, T, -1, 2)
            score = hide.unsqueeze(-1).float()
            m_err += (((pred - target) ** 2) * score).sum().item()
            b_err += (((base.view(1, 1, -1, 2) - target) ** 2) * score).sum().item()
            n += int(hide.sum().item()) * 2
    if n == 0:
        return {"n_hidden_values": 0, "model_mse": None, "baseline_mse": None, "reconstruction_improvement_over_baseline": None}
    model_mse, base_mse = m_err / n, b_err / n
    return {"n_hidden_values": n, "model_mse": round(model_mse, 6), "baseline_mse": round(base_mse, 6),
            "reconstruction_improvement_over_baseline": round(1.0 - model_mse / base_mse, 4) if base_mse > 0 else None}


def classification_metrics(model: PoseTemporalCNN, examples: list[tuple[dict, dict]], classes: list[str]) -> dict:
    model.eval()
    rows = [(i, label_of(m)) for i, (m, _) in enumerate(examples)]
    rows = [(i, c) for i, c in rows if c in classes]
    if not rows:
        return {"n_examples": 0}
    batch = [examples[i] for i, _ in rows]
    truth = [classes.index(c) for _, c in rows]
    with torch.no_grad():
        x, valid, _, _ = to_tensors(batch)
        pred = model(x, valid)["logits"].argmax(dim=1).tolist()
    k = len(classes)
    confusion = [[0] * k for _ in range(k)]
    for t, p in zip(truth, pred):
        confusion[t][p] += 1
    f1s = []
    for c in range(k):
        tp = confusion[c][c]
        fp = sum(confusion[r][c] for r in range(k)) - tp
        fn = sum(confusion[c]) - tp
        f1s.append(0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn))
    return {"n_examples": len(rows), "accuracy": round(sum(int(t == p) for t, p in zip(truth, pred)) / len(rows), 4),
            "macro_f1": round(float(np.mean(f1s)), 4), "confusion_matrix": confusion, "classes": classes,
            "classes_missing_from_this_split": [classes[c] for c in range(k) if sum(confusion[c]) == 0]}


def train_candidate(dataset: dict, splits: dict[str, list[int]], *, root: Path, name: str, requested_by_ref: str,
                    config: dict | None = None, now: datetime | None = None, withdrawn_refs: set[str] | None = None,
                    registry=None) -> Path:
    """Train ONE candidate on the train split. `requested_by_ref` is the named human who asked for this run; training is
    never started by an upload or by a review (see workflow.py)."""
    cfg = {**TRAINING, **(config or {})}
    if not str(requested_by_ref).startswith("AC-"):
        raise TrainingRefused("training must be requested by a named human (AC-... reference)")
    ds.check_no_leakage(dataset, splits)
    # the consent gate again, at training time
    ds.build_dataset(dataset["examples"], version=dataset["version"], now=now, withdrawn_refs=withdrawn_refs, registry=registry)
    for need in ("train", "val", "test"):
        if not splits.get(need):
            raise TrainingRefused(f"the {need} split is empty")
    if not training_enabled():
        raise TrainingDisabled(f"training is switched off; a human must set {TRAINING_ENV}=1 for this process to run it")
    set_determinism(cfg["seed"])
    ex = dataset["examples"]
    train_ex, val_ex, test_ex = ([ex[i] for i in splits[s]] for s in ("train", "val", "test"))
    classes, why = class_plan(train_ex)
    model = PoseTemporalCNN(channels=cfg["channels"], embedding_dim=cfg["embedding_dim"], n_classes=len(classes) if classes else 0)
    opt = torch.optim.AdamW(model.parameters(), lr=cfg["learning_rate"], weight_decay=cfg["weight_decay"])
    baseline = mean_pose(train_ex)
    gen = torch.Generator().manual_seed(cfg["seed"])
    order_rng = np.random.RandomState(cfg["seed"])
    history, best = [], None
    for epoch in range(cfg["epochs"]):
        model.train()
        perm = order_rng.permutation(len(train_ex))
        total, batches = 0.0, 0
        for start in range(0, len(perm), cfg["batch_size"]):
            batch = [train_ex[i] for i in perm[start:start + cfg["batch_size"]]]
            kp_mask = torch.from_numpy(np.stack([a["keypoint_mask"] for _, a in batch]))
            hide = _hide_mask(kp_mask, cfg["hide_fraction"], gen)
            if not hide.any():
                continue
            x, valid, target, _ = to_tensors(batch, hide)
            out = model(x, valid)
            B, _, T = out["reconstruction"].shape
            pred = out["reconstruction"].permute(0, 2, 1).reshape(B, T, -1, 2)
            score = hide.unsqueeze(-1).float()
            loss = (((pred - target) ** 2) * score).sum() / score.sum().clamp(min=1.0) / 2
            if classes:
                idx = [i for i, (m, _) in enumerate(batch) if label_of(m) in classes]
                if idx:
                    y = torch.tensor([classes.index(label_of(batch[i][0])) for i in idx])
                    loss = loss + 0.5 * torch.nn.functional.cross_entropy(out["logits"][idx], y)
            opt.zero_grad()
            loss.backward()
            opt.step()
            total += loss.item()
            batches += 1
        val = reconstruction_metrics(model, val_ex, baseline, seed=cfg["seed"] + 1, fraction=cfg["hide_fraction"])
        history.append({"epoch": epoch + 1, "train_loss": round(total / max(batches, 1), 6), "val_mse": val["model_mse"]})
        if val["model_mse"] is not None and (best is None or val["model_mse"] < best[0]):
            best = (val["model_mse"], {k: v.clone() for k, v in model.state_dict().items()}, epoch + 1)
    if best is not None:
        model.load_state_dict(best[1])
    evaluation = {"train": {"n_examples": len(train_ex), **reconstruction_metrics(model, train_ex, baseline, seed=cfg["seed"] + 2, fraction=cfg["hide_fraction"])},
                  "val": {"n_examples": len(val_ex), **reconstruction_metrics(model, val_ex, baseline, seed=cfg["seed"] + 1, fraction=cfg["hide_fraction"])},
                  "test": {"n_examples": len(test_ex), **reconstruction_metrics(model, test_ex, baseline, seed=cfg["seed"] + 3, fraction=cfg["hide_fraction"])},
                  "baseline": "per-joint mean observed position of the training examples",
                  "best_epoch": best[2] if best else None}
    if classes:
        evaluation["classification"] = {"classes": classes, "test": classification_metrics(model, test_ex, classes),
                                        "val": classification_metrics(model, val_ex, classes)}
        evaluation["classification"]["test_macro_f1"] = evaluation["classification"]["test"].get("macro_f1", 0)
    split_ids = {s: sorted(ex[i][0]["example_id"] for i in splits[s]) for s in splits}
    refs = sorted({m["consent"].get("consent_ref") for m, _ in ex if m["consent"].get("consent_ref")})
    report = {"dataset": {"version": dataset["version"], "fingerprint": dataset["fingerprint"], "synthetic_only": dataset["synthetic_only"],
                          "n_examples": len(ex), "split_sizes": {s: len(v) for s, v in splits.items()},
                          "split_sha256": hashlib.sha256(json.dumps(split_ids, sort_keys=True).encode()).hexdigest()},
              "training": {**cfg, "requested_by_ref": requested_by_ref, "architecture": MODEL_ARCHITECTURE, "device": "cpu",
                           "deterministic": True, "history": history, "trained_at": (now or datetime.now(timezone.utc).replace(tzinfo=None)).isoformat()},
              "classifier": {"trained": bool(classes), "reason": why},
              "evaluation": evaluation,
              "consent_audit": {"passed": True, "n_examples": len(ex), "consent_refs": refs,
                                "synthetic_only": dataset["synthetic_only"]},
              "leakage_check": {"passed": True, "rule": "players, source videos and assessments are disjoint across splits"},
              "no_player_scores": True}
    return registry.save_candidate(Path(root), name, model, report=report, classes=classes)


def embed(model: PoseTemporalCNN, examples: list[tuple[dict, dict]], batch_size: int = 64) -> np.ndarray:
    """Movement embeddings. A description of movement, not a rating of the player."""
    model.eval()
    out = []
    with torch.no_grad():
        for start in range(0, len(examples), batch_size):
            x, valid, _, _ = to_tensors(examples[start:start + batch_size])
            out.append(model(x, valid)["embedding"].numpy())
    return np.concatenate(out) if out else np.zeros((0, model.config["embedding_dim"]), np.float32)
