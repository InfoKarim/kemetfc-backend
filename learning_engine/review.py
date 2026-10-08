"""Coach review: pending -> approved | rejected, never automatic, never published.

A source candidate has no drill instructions. The coach inspects the source themself and WRITES the drill content (steps,
equipment, duration, age range, safety notes); that content, not the video's title, is what gets approved, and it is
recorded as coach-reviewed content with its version and hash. Any change to approved content creates a new version and puts
the item back to pending. Approval changes nothing for any player: `published` is fixed at 0 by the database itself.

A source can stop being valid after approval (private, deleted, made for kids, other channel, excluded). The approval and the
coach's content stay in the history, but the item moves to `approval_state = 'review_required'`: it is left out of the export, shows
why and since when, and a source that later turns public again does NOT bring the approval back. A coach or admin must decide
again, on the current version, while the source is confirmed available. Whether the approval is still valid (`approval_state`) and whether the source data is still held (`source_state`: present or
removed by retention) are two separate facts. When the retention window removes the source link (retention.py) the coach's own
content remains, flagged as having no source, and an approval that had been suspended STAYS suspended with its reason and date: deleting
data never makes an item valid again. A person can approve independent content again with an explicit decision, which is recorded as
`review_basis = INDEPENDENT_CONTENT_SOURCE_REMOVED`: a review of the coach's own content, not a check of the deleted source.
Nothing here publishes anything to any player.

WHO reviewed: the reviewer id, how they were identified (`auth_mode`) and when are stored with every decision. Through the
backend API the id is an authenticated coach/admin account; through the command line it is only a name typed by whoever runs
it, which is NOT authentication.
"""
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime

from learning_engine.store import ResearchStore, iso, utc_now

AUTH_BACKEND = "backend_user"
AUTH_CLI = "local_cli_unauthenticated"
ROLES = ("coach", "admin")
CATEGORY = "dribbling"
SKILL_TAG = re.compile(r"^[a-z][a-z0-9_]{2,31}$")


class ReviewError(ValueError):
    pass


@dataclass(frozen=True)
class Reviewer:
    reviewer_id: str
    auth_mode: str
    role: str = "coach"

    def __post_init__(self):
        if not self.reviewer_id or len(self.reviewer_id.strip()) < 2 or len(self.reviewer_id) > 80:
            raise ReviewError("a reviewer identity is required")
        if self.auth_mode not in (AUTH_BACKEND, AUTH_CLI):
            raise ReviewError("unknown way of identifying the reviewer")
        if self.role not in ROLES:
            raise ReviewError("only a coach or an admin can review")


def content_problems(content: dict) -> list[str]:
    """What is missing or wrong in coach-written drill content. Empty means it is complete enough to be approved."""
    if not isinstance(content, dict):
        return ["CONTENT_NOT_AN_OBJECT"]
    out = []
    allowed = {"title", "category", "steps", "equipment", "duration_minutes", "age_min", "age_max", "safety_notes", "coach_notes",
               "objective", "skill_tags", "measure", "unverified_fields"}
    if set(content) - allowed:
        out.append("UNKNOWN_FIELDS:" + ",".join(sorted(set(content) - allowed)))
    if not isinstance(content.get("title"), str) or not 3 <= len(content["title"].strip()) <= 120:
        out.append("TITLE_REQUIRED_3_TO_120_CHARACTERS")
    if content.get("category") != CATEGORY:
        out.append("CATEGORY_MUST_BE_DRIBBLING")
    steps = content.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 20 or not all(isinstance(s, str) and 5 <= len(s.strip()) <= 500 for s in steps):
        out.append("STEPS_REQUIRED_1_TO_20_ITEMS_OF_5_TO_500_CHARACTERS")
    eq = content.get("equipment")
    if not isinstance(eq, list) or len(eq) > 20 or not all(isinstance(e, str) and 1 <= len(e.strip()) <= 60 for e in eq):
        out.append("EQUIPMENT_LIST_REQUIRED")
    d = content.get("duration_minutes")
    if isinstance(d, bool) or not isinstance(d, int) or not 1 <= d <= 180:
        out.append("DURATION_MINUTES_1_TO_180")
    lo, hi = content.get("age_min"), content.get("age_max")
    if any(isinstance(x, bool) or not isinstance(x, int) for x in (lo, hi)) or not (3 <= lo <= hi <= 21):
        out.append("AGE_RANGE_REQUIRED_3_TO_21")
    if not isinstance(content.get("safety_notes"), str) or len(content["safety_notes"].strip()) < 10:
        out.append("SAFETY_NOTES_REQUIRED")
    # optional fields the recommendation engine can use; when present they must be well formed
    if "objective" in content and not (isinstance(content["objective"], str) and 3 <= len(content["objective"].strip()) <= 300):
        out.append("OBJECTIVE_3_TO_300_CHARACTERS")
    tags = content.get("skill_tags")
    if "skill_tags" in content and not (isinstance(tags, list) and 1 <= len(tags) <= 10 and all(isinstance(t, str) and SKILL_TAG.match(t) for t in tags)):
        out.append("SKILL_TAGS_1_TO_10_LOWERCASE_NAMES_LIKE_close_control")
    m = content.get("measure")
    if "measure" in content and not (isinstance(m, dict) and set(m) == {"name", "unit", "direction", "how"} and all(isinstance(m[k], str) and m[k].strip() for k in m)
                                     and m["direction"] in ("higher_is_better", "lower_is_better") and 3 <= len(m["name"]) <= 60 and len(m["unit"]) <= 20 and 10 <= len(m["how"]) <= 300):
        out.append("MEASURE_NEEDS_NAME_UNIT_DIRECTION_AND_HOW_TO_MEASURE")
    flags = content.get("unverified_fields")
    if "unverified_fields" in content and not (isinstance(flags, list) and all(isinstance(f, str) for f in flags)):
        out.append("UNVERIFIED_FIELDS_MUST_BE_A_LIST")
    return out


def content_hash(content: dict, source_refs: list[str]) -> str:
    return hashlib.sha256(json.dumps({"content": content, "sources": sorted(source_refs)}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ReviewService:
    def __init__(self, store: ResearchStore, clock=utc_now):
        self.store, self.clock = store, clock

    # ---- reading -----------------------------------------------------------------------------------------------------------
    def _item(self, item_id: str):
        row = self.store.db.execute("SELECT * FROM review_items WHERE item_id=?", (item_id,)).fetchone()
        if row is None:
            raise ReviewError("no such item")
        return row

    def source_refs(self, item) -> list[str]:
        if item["origin"] != "YOUTUBE_SOURCE":
            return []
        src = self.store.db.execute("SELECT video_id, channel_id, url FROM sources WHERE video_id=?", (item["source_video_id"],)).fetchone()
        return [src["url"]] if src else []

    def get(self, item_id: str) -> dict:
        item = self._item(item_id)
        version = self.store.db.execute("SELECT * FROM item_versions WHERE item_id=? AND version=?", (item_id, item["current_version"])).fetchone()
        src = None
        if item["source_video_id"]:
            s = self.store.db.execute("SELECT * FROM sources WHERE video_id=?", (item["source_video_id"],)).fetchone()
            src = None if s is None else {k: s[k] for k in ("video_id", "channel_id", "url", "title", "published_at", "discovered_at", "verified_at", "duration_s",
                                                              "privacy_status", "made_for_kids", "query_version", "stage", "availability", "exclusion_reason",
                                                              "discovery_class", "discovery_query")}
        last = self.store.db.execute("SELECT version, decision, reviewer_id, at, review_basis FROM reviews WHERE item_id=? ORDER BY review_id DESC LIMIT 1", (item_id,)).fetchone()
        state, approval = item["source_state"], item["approval_state"]
        if item["origin"] == "LOCAL_MEDIA" or item["analysis_id"]:
            note = ("draft made by a local analyzer from a locally registered, rights-recorded media file: the suggestion is NOT a coach decision, "
                    "the extracted notes carry the video times that support them, and ages, duration and safety are unverified until a coach confirms them")
        elif item["origin"] != "YOUTUBE_SOURCE":
            note = "general AI draft: unverified, not based on any source video"
        elif approval == "review_required" and state == "removed":
            note = ("the source stopped being valid and was later removed by the retention window; the earlier decision is kept in the history but is "
                    "not valid for export. A new decision would be an independent review of the coach's own content, not a check of the deleted source")
        elif approval == "review_required":
            note = ("the earlier decision is kept in the history, but the source is no longer valid (see source_issue); it is excluded from the "
                    "export until a coach reviews it again")
        elif state == "removed" and last is not None and last["review_basis"] == "INDEPENDENT_CONTENT_SOURCE_REMOVED" and last["decision"] == "approved":
            note = ("the source was removed after the retention window; the current approval is an independent review of the coach's own content, "
                    "not a check of the source")
        elif state == "removed":
            note = "the source was removed after the retention window; this is the coach's own content with no source attached"
        elif src is not None and src["discovery_class"] == "PUBLIC_METADATA_DISCOVERY":
            note = ("found by a public keyword search (metadata only): the channel is not approved, no one gave permission, nothing was watched or "
                    "downloaded, and the title says nothing proven about the drill or its suitability")
        else:
            note = "metadata-only source: the title says nothing proven about the drill or its suitability"
        return {"item_id": item_id, "origin": item["origin"], "stage": item["stage"], "review_status": item["review_status"],
                "version": item["current_version"], "published": bool(item["published"]), "source": src,
                "source_state": state, "approval_state": approval, "media_id": item["media_id"], "analysis_id": item["analysis_id"], "supersedes_item_id": item["supersedes_item_id"],
                "needs_source_review": approval == "review_required",
                "valid_for_export": item["review_status"] == "approved" and approval == "current", "source_issue": item["source_issue"],
                "source_issue_at": item["source_issue_at"], "source_removed_at": item["source_removed_at"],
                "last_decision": None if last is None else dict(last),
                "content": json.loads(version["content_json"]), "content_hash": version["content_hash"], "author_kind": version["author_kind"],
                "content_problems": content_problems(json.loads(version["content_json"])), "note": note}

    def list_items(self, status: str | None = None, needs_source_review: bool = False) -> list[dict]:
        if status is not None and status not in ("pending", "approved", "rejected"):
            raise ReviewError("status must be pending, approved or rejected")
        if needs_source_review:
            rows = self.store.db.execute("SELECT item_id FROM review_items WHERE approval_state='review_required' ORDER BY created_at, item_id").fetchall()
        elif status:
            rows = self.store.db.execute("SELECT item_id FROM review_items WHERE review_status=? ORDER BY created_at, item_id", (status,)).fetchall()
        else:
            rows = self.store.db.execute("SELECT item_id FROM review_items ORDER BY created_at, item_id").fetchall()
        items = [self.get(r["item_id"]) for r in rows]
        return [i for i in items if status is None or i["review_status"] == status] if needs_source_review else items

    # ---- writing: content ------------------------------------------------------------------------------------------------------
    def save_content(self, item_id: str, content: dict, reviewer: Reviewer) -> dict:
        """A coach writes (or edits) the drill content. A new version is created; if the item was approved or rejected it goes
        back to pending, because the content that was decided on is no longer the content on file."""
        now = self.clock()
        with self.store.transaction() as db:
            item = self._item(item_id)
            refs = self.source_refs(item)
            new_hash = content_hash(content, refs)
            current = db.execute("SELECT content_hash FROM item_versions WHERE item_id=? AND version=?", (item_id, item["current_version"])).fetchone()
            if current["content_hash"] == new_hash:
                return self.get(item_id)
            version = item["current_version"] + 1
            db.execute("INSERT INTO item_versions VALUES (?,?,?,?,?,?,?)", (item_id, version, json.dumps(content, sort_keys=True), new_hash, "COACH", reviewer.reviewer_id, iso(now)))
            returned = item["review_status"] != "pending"
            db.execute("UPDATE review_items SET current_version=?, review_status='pending', updated_at=? WHERE item_id=?", (version, iso(now), item_id))
            self.store.audit("CONTENT_SAVED", reviewer.reviewer_id, {"item_id": item_id, "version": version, "content_hash": new_hash,
                                                                   "returned_to_pending": returned, "auth_mode": reviewer.auth_mode}, now)
        return self.get(item_id)

    # ---- writing: decision ---------------------------------------------------------------------------------------------------------
    def decide(self, item_id: str, version: int, decision: str, reviewer: Reviewer, reasons: str, sources_checked: list[str]) -> dict:
        if decision not in ("approved", "rejected"):
            raise ReviewError("decision must be approved or rejected")
        if not isinstance(reasons, str) or len(reasons.strip()) < 10:
            raise ReviewError("written reasons are required (at least 10 characters)")
        now = self.clock()
        with self.store.transaction() as db:
            item = self._item(item_id)
            re_review = item["approval_state"] == "review_required"
            if item["review_status"] != "pending" and not re_review:
                raise ReviewError("only a pending item can be decided (or an approved one whose source needs a new review); changing approved content sends it back to pending")
            if version != item["current_version"]:
                raise ReviewError("the content changed since you looked at it; review the current version")
            row = db.execute("SELECT content_json, content_hash FROM item_versions WHERE item_id=? AND version=?", (item_id, version)).fetchone()
            content = json.loads(row["content_json"])
            refs = self.source_refs(item)
            if decision == "approved":
                problems = content_problems(content)
                if problems:
                    raise ReviewError("the drill content is not complete: " + ", ".join(problems))
                if content.get("unverified_fields"):
                    raise ReviewError("the coach has not yet confirmed these analyzer suggestions: " + ", ".join(content["unverified_fields"])
                                      + " (edit the content and remove them from unverified_fields once checked)")
                if item["origin"] == "YOUTUBE_SOURCE" and item["source_state"] == "present":
                    src = db.execute("SELECT availability, exclusion_reason FROM sources WHERE video_id=?", (item["source_video_id"],)).fetchone()
                    if src is None or src["availability"] != "available":
                        raise ReviewError("the source is not confirmed available; it cannot be approved")
                    if not set(refs) <= set(sources_checked):
                        raise ReviewError("record the source you inspected (its link) when approving")
            basis = ("LOCAL_MEDIA_ANALYSIS" if item["origin"] == "LOCAL_MEDIA" else "AI_DRAFT_NO_SOURCE" if item["origin"] != "YOUTUBE_SOURCE" else
                     "INDEPENDENT_CONTENT_SOURCE_REMOVED" if item["source_state"] == "removed" else "SOURCE_AVAILABLE_AT_DECISION")
            db.execute("INSERT INTO reviews(item_id,version,content_hash,decision,reviewer_id,auth_mode,reasons,sources_json,at,review_basis) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (item_id, version, row["content_hash"], decision, reviewer.reviewer_id, reviewer.auth_mode, reasons.strip(),
                        json.dumps(sorted(set(sources_checked))), iso(now), basis))
            db.execute("UPDATE review_items SET review_status=?, stage='coach_reviewed', approval_state='current', source_issue=NULL, source_issue_at=NULL, updated_at=? WHERE item_id=?",
                       (decision, iso(now), item_id))
            self.store.audit(f"REVIEW_{decision.upper()}", reviewer.reviewer_id,
                             {"item_id": item_id, "version": version, "content_hash": row["content_hash"], "auth_mode": reviewer.auth_mode,
                              "re_review_after_source_change": re_review, "previous_source_issue": item["source_issue"]}, now)
        return self.get(item_id)

    # ---- the only way out: read-only ------------------------------------------------------------------------------------------------------
    def approved_content(self) -> list[dict]:
        """Approved drill content for a coach to carry into the drill library BY HAND. Nothing here publishes anything. Items whose
        source stopped being valid are left out until they are reviewed again; an item whose source was removed by retention is
        included, flagged, because its content is the coach's own."""
        return [{"item_id": i["item_id"], "version": i["version"], "content_hash": i["content_hash"], "content": i["content"],
                 "origin": i["origin"], "source_url": (i["source"] or {}).get("url"), "source_removed": i["source_state"] == "removed",
                 "review_basis": (i["last_decision"] or {}).get("review_basis")}
                for i in self.list_items("approved") if i["valid_for_export"]]

    def history(self, item_id: str) -> list[dict]:
        rows = self.store.db.execute("SELECT version, content_hash, decision, reviewer_id, auth_mode, reasons, sources_json, at, review_basis FROM reviews WHERE item_id=? ORDER BY review_id", (item_id,)).fetchall()
        return [{**dict(r), "sources_json": json.loads(r["sources_json"])} for r in rows]
