"""Train and validate a football-specific detector with Ultralytics YOLO."""
import argparse
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from app.football_dataset import validate_dataset


def _metric(metrics, name: str) -> float | None:
    value = getattr(getattr(metrics, "box", None), name, None)
    return round(float(value), 6) if value is not None else None


def _git_revision() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("ml/football_dataset.yaml"))
    parser.add_argument("--base-model", default="yolo26n.pt")
    parser.add_argument(
        "--baseline-model",
        help="Optional prior checkpoint with the same four-class label schema",
    )
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--image-size", type=int, default=1280)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--project", default="training/football_detector")
    parser.add_argument("--run-name", default="train")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--manifest", type=Path, default=Path("datasets/football/manifest.csv"))
    parser.add_argument("--dataset-root", type=Path, default=Path("datasets/football"))
    parser.add_argument("--output", type=Path, default=Path("ml/reports/training_run.json"))
    args = parser.parse_args()

    if not args.data.is_file():
        parser.error(f"Dataset configuration not found: {args.data}")
    if args.epochs <= 0 or args.image_size <= 0:
        parser.error("epochs and image-size must be greater than zero")

    dataset_report = validate_dataset(args.manifest, args.dataset_root)

    try:
        from ultralytics import YOLO
    except ImportError as error:
        raise RuntimeError(
            "Install requirements-vision.txt before model training"
        ) from error

    baseline = None
    if args.baseline_model:
        baseline_model = YOLO(args.baseline_model)
        baseline = baseline_model.val(
            data=str(args.data), imgsz=args.image_size, split="test"
        )
    model = YOLO(args.base_model)
    model.train(
        data=str(args.data),
        epochs=args.epochs,
        imgsz=args.image_size,
        device=args.device,
        project=args.project,
        name=args.run_name,
        seed=args.seed,
        deterministic=True,
    )
    validation = model.val(data=str(args.data), imgsz=args.image_size, split="val")
    test = model.val(data=str(args.data), imgsz=args.image_size, split="test")
    report = {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_revision": _git_revision(),
        "python_version": platform.python_version(),
        "dataset": dataset_report,
        "parameters": {
            "base_model": args.base_model,
            "baseline_model": args.baseline_model,
            "epochs": args.epochs,
            "image_size": args.image_size,
            "device": args.device,
            "seed": args.seed,
            "run_name": args.run_name,
        },
        "baseline_test": {
            "map50_95": _metric(baseline, "map"),
            "map50": _metric(baseline, "map50"),
            "note": (
                "Not evaluated; provide a prior checkpoint with the identical class schema."
                if baseline is None else None
            ),
        },
        "validation": {"map50_95": _metric(validation, "map"), "map50": _metric(validation, "map50")},
        "test": {"map50_95": _metric(test, "map"), "map50": _metric(test, "map50")},
    }
    baseline_map = report["baseline_test"]["map50_95"]
    test_map = report["test"]["map50_95"]
    report["test"]["map50_95_improvement"] = (
        round(test_map - baseline_map, 6)
        if baseline_map is not None and test_map is not None else None
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"validation_map50_95={report['validation']['map50_95']}")
    print(f"test_map50_95={report['test']['map50_95']}")
    print(f"baseline_improvement={report['test']['map50_95_improvement']}")
    print(f"report={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
