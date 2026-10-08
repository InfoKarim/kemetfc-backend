"""Helpers for the knowledge-pipeline tests. EVERYTHING here is a test double: the analyzer returns scripted notes, the media files are a few
made-up bytes. Nothing here understands a video."""
import json
from datetime import timedelta
from pathlib import Path

from learning_engine import media as media_mod
from learning_engine.analyzer import AnalysisOutput
from learning_engine.config import PipelineConfig
from learning_engine.pipeline import run_pass
from learning_engine.review import AUTH_BACKEND, ReviewService, Reviewer
from learning_engine.store import ResearchStore
from tests.le_helpers import NOW

COACH = Reviewer("test-reviewer", AUTH_BACKEND, "coach")
PIPE = PipelineConfig(enabled=True, max_items_per_run=5, max_seconds_per_run=600, max_file_mb=5, max_attempts=3, backoff_base_s=60, backoff_max_s=3600, lease_minutes=30)

NOTES = [
    {"kind": "equipment", "text": "four small markers in a line", "t_start_s": 2.0, "t_end_s": 6.0, "confidence": 0.8},
    {"kind": "step", "text": "Dribble forward with the ball close to the feet", "t_start_s": 8.0, "t_end_s": 14.0, "confidence": 0.7},
    {"kind": "step", "text": "Weave through the markers, changing direction at each one", "t_start_s": 14.0, "t_end_s": 25.0, "confidence": 0.7},
    {"kind": "common_error", "text": "The ball drifts away from the feet on the last turn", "t_start_s": 22.0, "t_end_s": 24.0, "confidence": 0.5},
    {"kind": "safety_note", "text": "Keep the area clear of other players", "t_start_s": 1.0, "t_end_s": 3.0, "confidence": 0.6},
]
SUGGESTION = {"title": "[FIXTURE] Marker weave", "objective": "close control while changing direction", "skill_tags": ["close_control", "change_of_direction"],
              "measure": {"name": "clean_passes_through_markers_in_30s", "unit": "count", "direction": "higher_is_better", "how": "count the runs through all markers without losing the ball in 30 seconds"},
              "age_min": 8, "age_max": 12, "duration_minutes": 10}


class FixtureAnalyzer:
    """A scripted analyzer. `fixture = True`: its output is labelled and never counts as real analysis."""
    name, version, method, external, fixture = "FIXTURE_ANALYZER_NOT_REAL", "test-1", "scripted notes (no video is understood)", False, True

    def __init__(self, notes=None, suggestion=None, duration_s=60.0, fail=None, uncertainties=None):
        self.notes = NOTES if notes is None else notes
        self.suggestion = SUGGESTION if suggestion is None else suggestion
        self.duration_s, self.fail, self.uncertainties = duration_s, fail, uncertainties or ["how many markers there were was not clear"]
        self.calls = []

    def analyze(self, path: Path) -> AnalysisOutput:
        self.calls.append(path)
        if self.fail:
            raise self.fail
        return AnalysisOutput(notes=[dict(n) for n in self.notes], duration_s=self.duration_s, suggestion=dict(self.suggestion), uncertainties=list(self.uncertainties))


def make_file(directory: Path, name: str = "clip.mp4", content: bytes | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(content if content is not None else (name.encode() * 64))
    return path


def register(store, path: Path, *, minors="no", queue_id=None, now=NOW, basis="OWN_RECORDING"):
    return media_mod.register_media(store, path, rights_basis=basis, rights_grantor="the club (test fixture)", rights_note="fixture", minors_visible=minors,
                                    recorded_by="test-reviewer", now=now, max_file_mb=PIPE.max_file_mb, display_name=path.name, queue_id=queue_id)


def pass_(store, analyzer, now=NOW, cfg=PIPE, **kw):
    return run_pass(store, cfg, analyzer, now, **kw)


def approve_item(store, item_id: str, content: dict | None = None, sources: list | None = None):
    """Coach path: confirm the draft (remove the unverified flags, fill what is missing), then approve the current version."""
    svc = ReviewService(store)
    view = svc.get(item_id)
    base = {k: v for k, v in view["content"].items() if k != "unverified_fields"}
    base.setdefault("title", "Marker weave (test)")
    base.setdefault("equipment", ["markers"])
    base.setdefault("steps", ["Weave through the markers"])
    base.setdefault("duration_minutes", 10)
    base.setdefault("age_min", 8)
    base.setdefault("age_max", 12)
    base.setdefault("safety_notes", "Keep the area clear of other players.")
    base.update(content or {})
    base = {k: v for k, v in base.items() if v is not None}            # a None in `content` removes that field
    saved = svc.save_content(item_id, base, COACH)
    return svc.decide(item_id, saved["version"], "approved", COACH, "Test: confirmed every field myself.", sources or [])


def new_store(tmp_path: Path) -> ResearchStore:
    return ResearchStore(tmp_path / "state")


def later(minutes=0, days=0):
    return NOW + timedelta(minutes=minutes, days=days)


def dump(store) -> str:
    return "\n".join(store.db.iterdump())


def j(x):
    return json.dumps(x, sort_keys=True)
