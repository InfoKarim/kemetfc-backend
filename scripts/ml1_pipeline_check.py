"""Run the ML-1 pipeline end to end on SYNTHETIC fixtures only, to prove the plumbing works.

This trains nothing real, approves nothing and releases nothing. The output model is a CANDIDATE trained on generated
stick figures and can never be approved. Output goes to --out (default: a temporary folder, deleted afterwards).
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml1 import dataset as ds, registry, train  # noqa: E402
from app.ml1.synthetic import make_synthetic_examples  # noqa: E402

CHECK_REF = "AC-" + "0" * 16        # a visibly fake requester for this check


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--epochs", type=int, default=25)
    args = parser.parse_args()
    dataset = ds.build_dataset(make_synthetic_examples(30, 2, 5), version="synthetic-pipeline-check")
    splits = ds.split_dataset(dataset, seed=1)
    with tempfile.TemporaryDirectory() as tmp:
        root = args.out or Path(tmp)
        directory = train.train_candidate(dataset, splits, root=root, name="pose_tcn", requested_by_ref=CHECK_REF, config={"epochs": args.epochs})
        report = json.loads((directory / "report.json").read_text())
        print("dataset:", dataset["version"], dataset["fingerprint"][:12], "synthetic_only:", dataset["synthetic_only"])
        print("splits:", report["dataset"]["split_sizes"])
        print("test reconstruction improvement over baseline:", report["evaluation"]["test"]["reconstruction_improvement_over_baseline"])
        print("classifier:", report["classifier"])
        print("approval blocked because:", registry.approval_problems(directory))
        print("candidate:", directory.name, "(not approved, not released, not deployed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
