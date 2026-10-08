"""One bounded pass over the work queue.

discovered -> (triage) awaiting_media | skipped -> ready (media attached) -> processing -> draft_ready | skipped | failed

Limits per pass: items, wall-clock seconds, file size, attempts, backoff. Nothing here opens a network connection, calls a paid service,
downloads anything or trains anything; the analyzer sees only a local file that has a rights record, and an external analyzer is refused.
There is no loop without an end: the pass stops when the item or time limit is reached or nothing is ready."""
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from learning_engine import library, media as media_mod, workqueue
from learning_engine.analyzer import AnalyzerNotConfigured, NoAnalyzer
from learning_engine.config import PipelineConfig
from learning_engine.store import ResearchStore


@dataclass
class PassResult:
    synced: int = 0
    triaged: dict = field(default_factory=dict)
    recovered: int = 0
    analyzed: int = 0                 # analyses that finished (with or without knowledge)
    knowledge_extracted: int = 0
    no_knowledge: int = 0
    drafts_created: int = 0
    skipped: dict = field(default_factory=dict)
    failed_attempts: int = 0
    failure_states: dict = field(default_factory=dict)
    blocked: list = field(default_factory=list)
    stopped_by: str = "queue_empty"


def run_pass(store: ResearchStore, cfg: PipelineConfig, analyzer, now: datetime, *, time_fn=time.monotonic) -> PassResult:
    res = PassResult()
    res.synced = workqueue.sync_discovered(store, now)
    res.triaged = workqueue.triage(store, now)
    res.recovered = workqueue.recover_stale(store, now, max_attempts=cfg.max_attempts, backoff_base_s=cfg.backoff_base_s, backoff_max_s=cfg.backoff_max_s)
    started_clock = time_fn()
    owner = "W-" + secrets.token_hex(6)
    handled = 0
    while handled < cfg.max_items_per_run:
        if time_fn() - started_clock >= cfg.max_seconds_per_run:
            res.stopped_by = "time_limit"
            break
        row = workqueue.claim_next(store, now, owner=owner, lease_minutes=cfg.lease_minutes)
        if row is None:
            break
        handled += 1
        if not _one(store, cfg, analyzer, row, owner, now, res):
            break                                     # the analyzer is not configured: every other row would be blocked the same way
    else:
        res.stopped_by = "item_limit"
    return res


def _one(store, cfg, analyzer, row: dict, owner: str, now: datetime, res: PassResult) -> bool:
    media = store.db.execute("SELECT * FROM media_files WHERE media_id=?", (row["media_id"],)).fetchone()
    skip = lambda reason: (workqueue.skip(store, row["queue_id"], owner, now, reason), res.skipped.__setitem__(reason, res.skipped.get(reason, 0) + 1))   # noqa: E731
    if media is None:
        skip("MEDIA_NOT_REGISTERED")
        return True
    problems = media_mod.media_problems(media, cfg.max_file_mb)
    if problems:
        skip(problems[0])
        return True
    if getattr(analyzer, "external", False):
        skip("EXTERNAL_ANALYSIS_NOT_ENABLED" + ("_AND_MINORS_NEVER_SENT_OUTSIDE" if media["minors_visible"] != "no" else ""))
        return True
    if isinstance(analyzer, NoAnalyzer):
        workqueue.release_unchanged(store, row["queue_id"], owner, now, "ANALYZER_NOT_CONFIGURED")
        res.blocked.append("ANALYZER_NOT_CONFIGURED")
        return False
    started = now
    try:
        out = analyzer.analyze(Path(media["local_path"]))
    except AnalyzerNotConfigured:
        workqueue.release_unchanged(store, row["queue_id"], owner, now, "ANALYZER_NOT_CONFIGURED")
        res.blocked.append("ANALYZER_NOT_CONFIGURED")
        return False
    except Exception as exc:                          # the type only: a message could carry a path
        state = workqueue.fail(store, row["queue_id"], owner, now, f"ANALYZER_ERROR_{type(exc).__name__}", max_attempts=cfg.max_attempts,
                               backoff_base_s=cfg.backoff_base_s, backoff_max_s=cfg.backoff_max_s)
        res.failed_attempts += 1
        res.failure_states[state] = res.failure_states.get(state, 0) + 1
        return True
    done = library.record_analysis(store, row, dict(media), analyzer, out, started=started, now=now, lease_owner=owner)
    if done["status"] == "lost_lease":
        return True
    res.analyzed += 1
    if done["status"] == "knowledge_extracted":
        res.knowledge_extracted += 1
        res.drafts_created += 1
    else:
        res.no_knowledge += 1
        res.skipped["NO_KNOWLEDGE_EXTRACTED"] = res.skipped.get("NO_KNOWLEDGE_EXTRACTED", 0) + 1
    return True
