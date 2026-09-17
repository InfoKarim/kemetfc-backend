"""The model registry: the single source of truth for which
model_name/model_version produced a given tracking or assessment result,
and the mechanism for safe rollback (flip status, never delete a row a
past result references). See db_models.MLModelRegistryDB."""

from datetime import datetime, UTC

from sqlalchemy.orm import Session

from app.db_models import MLModelRegistryDB
from app.services.id_service import next_entity_id


class ModelRegistryError(ValueError):
    pass


class ModelRegistryService:
    def __init__(self, db: Session):
        self.db = db

    def register_model(
        self,
        actor_user_id: str | None,
        model_name: str,
        model_version: str,
        model_type: str,
        status: str = "experimental",
        training_dataset_version: str | None = None,
        evaluation_metrics: dict | None = None,
        coreml_artifact_version: str | None = None,
        backend_artifact_version: str | None = None,
        notes: str | None = None,
    ) -> MLModelRegistryDB:
        existing = (
            self.db.query(MLModelRegistryDB)
            .filter(
                MLModelRegistryDB.model_name == model_name,
                MLModelRegistryDB.model_version == model_version,
            )
            .first()
        )
        if existing is not None:
            raise ModelRegistryError(
                f"{model_name} {model_version} is already registered"
            )

        now = datetime.now(UTC).replace(tzinfo=None)
        model = MLModelRegistryDB(
            model_id=next_entity_id(self.db, "ml_model"),
            model_name=model_name,
            model_version=model_version,
            model_type=model_type,
            status=status,
            deployment_date=now if status == "active" else None,
            training_dataset_version=training_dataset_version,
            evaluation_metrics=evaluation_metrics,
            coreml_artifact_version=coreml_artifact_version,
            backend_artifact_version=backend_artifact_version,
            notes=notes,
            created_by_user_id=actor_user_id,
            created_at=now,
        )
        self.db.add(model)
        self.db.commit()
        self.db.refresh(model)
        return model

    def set_status(self, model_id: str, status: str) -> MLModelRegistryDB:
        model = self.db.get(MLModelRegistryDB, model_id)
        if model is None:
            raise ModelRegistryError("Model not found")
        model.status = status
        if status == "active" and model.deployment_date is None:
            model.deployment_date = datetime.now(UTC).replace(tzinfo=None)
        self.db.commit()
        self.db.refresh(model)
        return model

    def get_active_model(self, model_name: str) -> MLModelRegistryDB | None:
        """The currently active model for a given name — falls back to
        the newest experimental one if nothing has been promoted yet, so
        a brand-new deployment always has SOME resolvable model rather
        than a hard failure, but the caller can always see model_status
        in the result to know it's experimental."""
        active = (
            self.db.query(MLModelRegistryDB)
            .filter(MLModelRegistryDB.model_name == model_name, MLModelRegistryDB.status == "active")
            .order_by(MLModelRegistryDB.created_at.desc())
            .first()
        )
        if active is not None:
            return active
        return (
            self.db.query(MLModelRegistryDB)
            .filter(MLModelRegistryDB.model_name == model_name, MLModelRegistryDB.status == "experimental")
            .order_by(MLModelRegistryDB.created_at.desc())
            .first()
        )

    def list_models(self, model_name: str | None = None) -> list[MLModelRegistryDB]:
        query = self.db.query(MLModelRegistryDB)
        if model_name is not None:
            query = query.filter(MLModelRegistryDB.model_name == model_name)
        return query.order_by(MLModelRegistryDB.model_name.asc(), MLModelRegistryDB.created_at.desc()).all()

    def ensure_default_registry(self, actor_user_id: str | None) -> None:
        """Seed the registry with the models this platform actually ships
        today, so the registry is never empty in a fresh deployment. Only
        inserts entries that don't already exist — safe to call every
        startup."""
        defaults = [
            ("player-detector", "vision-v1", "on_device_vision", "active",
             "Apple Vision VNDetectHumanRectanglesRequest — no custom training, no facial recognition."),
            ("pose-model", "vision-v1", "on_device_vision", "active",
             "Apple Vision VNDetectHumanBodyPoseRequest — 17-joint 2D body pose, no custom training."),
            ("ball-detector", "classical-cv-v0.1", "on_device_classical_cv", "experimental",
             "Color/circularity heuristic fallback — NOT a trained model. See KemetFCTracker BallDetector.swift "
             "for the CoreMLBallDetector integration point a trained model should replace this with."),
            ("skill-inference", "rule-based-experimental-v0.1", "rule_based", "experimental",
             "Transparent rule-based scorer, not a trained temporal neural network — see skill_inference.py."),
        ]
        for name, version, model_type, status, notes in defaults:
            existing = (
                self.db.query(MLModelRegistryDB)
                .filter(MLModelRegistryDB.model_name == name, MLModelRegistryDB.model_version == version)
                .first()
            )
            if existing is None:
                self.register_model(
                    actor_user_id,
                    model_name=name,
                    model_version=version,
                    model_type=model_type,
                    status=status,
                    notes=notes,
                )
