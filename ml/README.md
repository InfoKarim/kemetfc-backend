# Reproducible football ML workflow

## 1. Collect and annotate

Store private data under `datasets/football/` (ignored by Git). Copy
`ml/manifest.example.csv` to `datasets/football/manifest.csv` and add one row per
image. Never put child names in filenames or the manifest.

For an unsplit manifest, assign deterministic match-level splits first:

```bash
python -m scripts.assign_football_splits \
  --input datasets/football/manifest_unsplit.csv \
  --output datasets/football/manifest.csv --seed 42
```

## 2. Validate before training

```bash
python -m scripts.validate_football_dataset \
  --manifest datasets/football/manifest.csv \
  --dataset-root datasets/football
```

This validates normalized YOLO labels, consent references, missing files,
duplicate images, and match leakage. It writes a content fingerprint and slice
counts to `ml/reports/dataset_validation.json`.

## 3. Train with a baseline

```bash
python -m scripts.train_football_detector \
  --base-model yolo26n.pt --baseline-model models/football_previous.pt \
  --epochs 100 --image-size 1280 --device 0 \
  --run-name phone-gimbal-v1
```

The script evaluates an optional **task-compatible** prior checkpoint on the
test split, trains with a fixed seed, evaluates validation and test separately,
and saves parameters, Git revision, dataset fingerprint, and baseline
improvement. A generic COCO checkpoint is not accepted as a four-class numeric
baseline because its class IDs do not represent goalkeeper and referee; compare
generic remapped predictions separately with the evaluation command.

## 4. Evaluate exported predictions and events

```bash
python -m scripts.evaluate_football_predictions \
  --ground-truth ml/private/test_ground_truth.json \
  --predictions ml/private/test_predictions.json \
  --event-ground-truth ml/private/test_events.json \
  --event-predictions ml/private/predicted_events.json
```

The command exits with status `2` when the release gate fails. Preserve the
generated report with the checkpoint release record, but do not commit private
examples or child imagery.

## 5. Review errors

Review every reported false positive/negative against the video. Categorize
occlusion, motion blur, distance, camera loss, similar kits, crowded boxes,
substitutions, and lighting. Update the model and repeat on the unchanged test
set only after selecting the final candidate on validation data.
