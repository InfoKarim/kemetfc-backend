"""The analyzer interface. NO real analyzer is built in.

An analyzer looks at a locally registered media file and returns timestamped notes. This module only defines what it must return and
checks it. With no analyzer configured the answer is ANALYZER_NOT_CONFIGURED, never a generic text called "understanding". A note without
a usable video time is discarded (and counted), because a statement nobody can check against the video is not knowledge: for example,
a number of cones or which foot was used is kept only if a note with a time says so.

Existing movement code in this repository (app/video_ai: pose and image-plane motion proxies) measures body movement, not drill steps,
equipment or errors, so it is not wired in as a drill analyzer. External (cloud) analyzers are refused in this build."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

NOTE_KINDS = ("observation", "step", "equipment", "common_error", "safety_note", "measure_hint")
SUGGESTION_KEYS = {"title", "objective", "skill_tags", "measure", "age_min", "age_max", "duration_minutes"}
MAX_NOTES = 60


class AnalyzerNotConfigured(Exception):
    code = "ANALYZER_NOT_CONFIGURED"


@dataclass
class AnalysisOutput:
    notes: list = field(default_factory=list)            # dicts: kind, text, t_start_s, t_end_s, confidence (optional)
    duration_s: float | None = None
    suggestion: dict = field(default_factory=dict)       # optional analyzer suggestions; NEVER verified
    uncertainties: list = field(default_factory=list)    # things the analyzer says it could not tell
    limits: list = field(default_factory=list)


class Analyzer(Protocol):
    name: str
    version: str
    method: str
    external: bool        # True = would send content to another service: refused in this build
    fixture: bool         # True = test double, its output is labelled and never counts as real analysis

    def analyze(self, path: Path) -> AnalysisOutput: ...


class NoAnalyzer:
    name, version, method, external, fixture = "none", "0", "none", False, False

    def analyze(self, path: Path) -> AnalysisOutput:
        raise AnalyzerNotConfigured()


def validate_output(out: AnalysisOutput) -> tuple[list[dict], int]:
    """(valid notes, number rejected). A note is valid only with a known kind, readable text, and a time range inside the video."""
    valid, rejected = [], 0
    for raw in (out.notes or [])[:MAX_NOTES]:
        try:
            kind, text = raw["kind"], str(raw["text"]).strip()
            start, end = float(raw["t_start_s"]), float(raw["t_end_s"])
            conf = raw.get("confidence")
            conf = None if conf is None else float(conf)
        except (KeyError, TypeError, ValueError):
            rejected += 1
            continue
        ok_time = 0 <= start <= end and (out.duration_s is None or end <= out.duration_s + 0.5)
        if kind not in NOTE_KINDS or not 3 <= len(text) <= 500 or not ok_time or (conf is not None and not 0 <= conf <= 1):
            rejected += 1
            continue
        valid.append({"kind": kind, "text": text, "t_start_s": start, "t_end_s": end, "confidence": conf})
    rejected += max(0, len(out.notes or []) - MAX_NOTES)
    return valid, rejected


def draft_content(valid_notes: list[dict], suggestion: dict) -> dict:
    """The analyzer's PROPOSED drill content. Steps, equipment and safety text come only from timed notes. Anything about age, duration,
    safety, objective, skills or measurement is a suggestion and is flagged `unverified_fields` until a coach confirms it."""
    ordered = sorted(valid_notes, key=lambda n: (n["t_start_s"], n["t_end_s"]))
    steps = [n["text"] for n in ordered if n["kind"] == "step" and len(n["text"]) >= 5]
    equipment = []
    for n in ordered:
        if n["kind"] == "equipment" and n["text"] not in equipment:
            equipment.append(n["text"][:60])
    safety = " ".join(n["text"] for n in ordered if n["kind"] == "safety_note")
    content: dict = {"category": "dribbling"}
    if steps:
        content["steps"] = steps[:20]
    if equipment:
        content["equipment"] = equipment[:20]
    flags = []
    sug = {k: v for k, v in (suggestion or {}).items() if k in SUGGESTION_KEYS}
    if isinstance(sug.get("title"), str) and 3 <= len(sug["title"].strip()) <= 120:
        content["title"] = sug["title"].strip()
    for key in ("objective", "skill_tags", "measure", "age_min", "age_max", "duration_minutes"):
        if key in sug:
            content[key] = sug[key]
            flags.append(key)
    if safety:
        content["safety_notes"] = safety[:500]
        flags.append("safety_notes")
    if flags:
        content["unverified_fields"] = sorted(flags)
    return content
