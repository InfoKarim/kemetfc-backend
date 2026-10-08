"""Source discovery: metadata only. It finds out THAT a public video exists on an approved channel, never WHAT it teaches.

States a source can be in:  discovered (found by search)  ->  metadata_only (confirmed by videos.list: public, plausible
duration, not made for kids).  Whether the drill is good, safe or suitable for any age is something only a coach can say,
after inspecting the source themself. A title is never used as evidence of a drill's steps or of its suitability for children.

Duplicates: exact video id, and exact normalized-title fingerprint (Unicode NFKC, case-folded, punctuation removed,
whitespace collapsed). This is NOT semantic deduplication and does not claim to be: two differently worded titles for the
same drill are two candidates.

Refreshing (videos.list). A source's stored title, publication date, duration, privacy, made-for-kids flag and text
fingerprint are replaced from the CURRENT response, and `verified_at` moves only when that response was complete and valid.
A partial or malformed response changes nothing and does not renew anything, so stale fields age out under retention.py. A
response that shows the video is no longer usable (not public, made for kids, live, wrong channel, implausible duration, or
not returned at all) clears the API-derived text of the source, records the reason, and sends any APPROVED item that rests on
it to `approval_state = 'review_required'` (see review.py); the coach's own content and the earlier decision stay, and a source that later
becomes public again does NOT bring the approval back.
"""
import hashlib
import re
import secrets
import unicodedata
from datetime import datetime

from learning_engine.config import Config
from learning_engine.store import ResearchStore, iso
from learning_engine.youtube import YouTubeApiError, YouTubeClient

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
ISO_DURATION = re.compile(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")
MAX_TITLE = 300
MAX_DURATION_S = 3 * 3600
MIN_DURATION_S = 20


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKC", title).casefold()
    text = re.sub(r"[^\w\s]|_", " ", text)
    return " ".join(text.split())


def fingerprint(title: str) -> str | None:
    norm = normalize_title(title)
    return hashlib.sha256(norm.encode()).hexdigest() if norm else None


def parse_duration(value) -> int | None:
    if not isinstance(value, str):
        return None
    m = ISO_DURATION.match(value)
    if not m or value in ("P", "PT"):
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def parse_search_item(item, channel_id: str) -> dict | None:
    """A search result, only if every field we rely on is present and plausible AND it belongs to the allowlisted channel."""
    if not isinstance(item, dict) or not isinstance(item.get("id"), dict) or not isinstance(item.get("snippet"), dict):
        return None
    vid, snip = item["id"].get("videoId"), item["snippet"]
    title = snip.get("title")
    if not isinstance(vid, str) or not VIDEO_ID.match(vid) or snip.get("channelId") != channel_id:
        return None
    if not isinstance(title, str) or not title.strip() or len(title) > MAX_TITLE:
        return None
    published = snip.get("publishedAt")
    if published is not None and not (isinstance(published, str) and len(published) <= 40):
        return None
    return {"video_id": vid, "channel_id": channel_id, "title": " ".join(title.split()), "published_at": published}


def parse_video_item(item) -> dict | None:
    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not VIDEO_ID.match(item["id"]):
        return None
    snip, content, status = item.get("snippet"), item.get("contentDetails"), item.get("status")
    if not all(isinstance(x, dict) for x in (snip, content, status)):
        return None
    return {"video_id": item["id"], "channel_id": snip.get("channelId"), "title": snip.get("title"), "published_at": snip.get("publishedAt"),
            "duration_s": parse_duration(content.get("duration")), "privacy": status.get("privacyStatus"),
            "made_for_kids": status.get("madeForKids"), "live": snip.get("liveBroadcastContent")}


def is_complete(detail: dict) -> bool:
    """Every field that is stored from this response is present and plausible. Only then may the stored copy be refreshed."""
    title, published = detail.get("title"), detail.get("published_at")
    return (isinstance(title, str) and bool(title.strip()) and len(title) <= MAX_TITLE
            and isinstance(published, str) and 0 < len(published) <= 40
            and detail.get("duration_s") is not None and isinstance(detail.get("privacy"), str)
            and isinstance(detail.get("made_for_kids"), bool) and isinstance(detail.get("channel_id"), str))


def new_item_id() -> str:
    """Opaque on purpose: an item id outlives the source link (retention), so it must not contain the video id."""
    return "SRC-" + secrets.token_hex(8)


class Discovery:
    def __init__(self, store: ResearchStore, config: Config, client: YouTubeClient, now_fn):
        self.store, self.config, self.client, self.now_fn = store, config, client, now_fn

    # ---- storing ---------------------------------------------------------------------------------------------------------
    def _upsert_discovered(self, found: dict, now: datetime) -> str:
        """'new', 'duplicate_id' or 'duplicate_text'. Never overwrites an existing source's stage."""
        db = self.store.db
        if db.execute("SELECT 1 FROM sources WHERE video_id=?", (found["video_id"],)).fetchone():
            return "duplicate_id"
        fp = fingerprint(found["title"])
        canonical = db.execute("SELECT video_id FROM sources WHERE text_fingerprint=? AND duplicate_of IS NULL", (fp,)).fetchone() if fp else None
        db.execute("INSERT INTO sources(video_id,channel_id,url,title,published_at,discovered_at,query_version,text_fingerprint,duplicate_of,stage,availability) "
                   "VALUES (?,?,?,?,?,?,?,?,?,'discovered','unknown')",
                   (found["video_id"], found["channel_id"], f"https://www.youtube.com/watch?v={found['video_id']}", found["title"],
                    found["published_at"], iso(now), self.config.youtube.query_version, fp, canonical["video_id"] if canonical else None))
        if canonical:
            return "duplicate_text"
        self._create_item(found["video_id"], "discovered", now)
        return "new"

    def _create_item(self, video_id: str, stage: str, now: datetime) -> str:
        db = self.store.db
        item_id = new_item_id()
        db.execute("INSERT INTO review_items(item_id,origin,source_video_id,stage,review_status,current_version,created_at,updated_at) "
                   "VALUES (?,?,?,?,?,?,?,?)", (item_id, "YOUTUBE_SOURCE", video_id, stage, "pending", 1, iso(now), iso(now)))
        db.execute("INSERT INTO item_versions(item_id,version,content_json,content_hash,author_kind,author_ref,created_at) VALUES (?,?,?,?,?,?,?)",
                   (item_id, 1, "{}", hashlib.sha256(b"{}").hexdigest(), "NONE", None, iso(now)))
        return item_id

    # ---- fingerprints ------------------------------------------------------------------------------------------------------
    def _place_fingerprint(self, video_id: str, new_fp: str | None, now: datetime) -> None:
        """Store `new_fp` for this source without ever losing a reviewed item. A canonical source whose new fingerprint equals
        ANOTHER canonical source's keeps both rows and all their items: its own fingerprint is left empty and the clash is
        recorded (`fingerprint_conflict_with`), because merging would discard one coach's content or review history. A source
        that was a duplicate follows the canonical it now matches, or stops being a duplicate and becomes its own candidate."""
        db = self.store.db
        row = db.execute("SELECT video_id, duplicate_of FROM sources WHERE video_id=?", (video_id,)).fetchone()
        others = [r["video_id"] for r in db.execute("SELECT video_id FROM sources WHERE text_fingerprint=? AND duplicate_of IS NULL AND video_id<>?",
                                                    (new_fp, video_id)).fetchall()] if new_fp else []
        if row["duplicate_of"] is None:
            if others:
                db.execute("UPDATE sources SET text_fingerprint=NULL, fingerprint_conflict_with=? WHERE video_id=?", (others[0], video_id))
                self.store.audit("FINGERPRINT_CONFLICT", "system", {"kept_separate": True, "count": len(others) + 1}, now)
            else:
                db.execute("UPDATE sources SET text_fingerprint=?, fingerprint_conflict_with=NULL WHERE video_id=?", (new_fp, video_id))
        else:
            canonical = db.execute("SELECT text_fingerprint FROM sources WHERE video_id=?", (row["duplicate_of"],)).fetchone()
            if new_fp and canonical is not None and canonical["text_fingerprint"] == new_fp:
                db.execute("UPDATE sources SET text_fingerprint=? WHERE video_id=?", (new_fp, video_id))
            elif others:
                db.execute("UPDATE sources SET text_fingerprint=?, duplicate_of=? WHERE video_id=?", (new_fp, others[0], video_id))
            else:
                db.execute("UPDATE sources SET text_fingerprint=?, duplicate_of=NULL, fingerprint_conflict_with=NULL WHERE video_id=?", (new_fp, video_id))
                if not db.execute("SELECT 1 FROM review_items WHERE source_video_id=?", (video_id,)).fetchone():
                    self._create_item(video_id, db.execute("SELECT stage FROM sources WHERE video_id=?", (video_id,)).fetchone()["stage"], now)
        self._reconcile_duplicates(video_id, now)

    def _reconcile_duplicates(self, canonical_id: str, now: datetime) -> None:
        """Sources recorded as duplicates of `canonical_id` that no longer share its fingerprint, and sources whose fingerprint clashed with it."""
        db = self.store.db
        canonical = db.execute("SELECT text_fingerprint FROM sources WHERE video_id=?", (canonical_id,)).fetchone()
        for dup in db.execute("SELECT video_id, text_fingerprint FROM sources WHERE duplicate_of=?", (canonical_id,)).fetchall():
            if canonical is not None and canonical["text_fingerprint"] is not None and canonical["text_fingerprint"] == dup["text_fingerprint"]:
                continue
            self._place_fingerprint(dup["video_id"], dup["text_fingerprint"], now)
        for clash in db.execute("SELECT video_id, title FROM sources WHERE fingerprint_conflict_with=?", (canonical_id,)).fetchall():
            self._place_fingerprint(clash["video_id"], fingerprint(clash["title"]) if clash["title"] else None, now)      # the clash may be over

    # ---- source no longer usable ---------------------------------------------------------------------------------------------
    def _invalidate_source(self, video_id: str, reason: str, now: datetime, availability: str) -> None:
        """The current evidence says this video is not usable. Clear the API-derived text, record why, and send every APPROVED
        item that rests on it back to review. Nothing the coach wrote and no earlier decision is touched."""
        db = self.store.db
        db.execute("UPDATE sources SET availability=?, exclusion_reason=?, verified_at=?, title=NULL, published_at=NULL, duration_s=NULL, "
                   "text_fingerprint=NULL, fingerprint_conflict_with=NULL WHERE video_id=?", (availability, reason, iso(now), video_id))
        self._reconcile_duplicates(video_id, now)
        for item in db.execute("SELECT item_id FROM review_items WHERE source_video_id=? AND review_status='approved' AND approval_state='current'", (video_id,)).fetchall():
            db.execute("UPDATE review_items SET approval_state='review_required', source_issue=?, source_issue_at=?, updated_at=? WHERE item_id=?",
                       (reason, iso(now), iso(now), item["item_id"]))
            self.store.audit("SOURCE_INVALIDATED_REVIEW_REQUIRED", "system", {"item_id": item["item_id"], "reason": reason, "decision_kept": "approved"}, now)

    def _apply_details(self, detail: dict, now: datetime) -> str:
        """Refresh one source from a videos.list item. Returns 'metadata_only', 'excluded' or 'incomplete' (nothing changed)."""
        db = self.store.db
        row = db.execute("SELECT channel_id FROM sources WHERE video_id=?", (detail["video_id"],)).fetchone()
        if row is None:
            return "unknown_source"
        reason = None
        if isinstance(detail["privacy"], str) and detail["privacy"] != "public":
            reason = "NOT_PUBLIC"
        elif detail["made_for_kids"] is True:
            reason = "MADE_FOR_KIDS"
        elif detail["live"] not in (None, "none"):
            reason = "LIVE_OR_UPCOMING_BROADCAST"
        elif detail["duration_s"] is not None and not MIN_DURATION_S <= detail["duration_s"] <= MAX_DURATION_S:
            reason = "IMPLAUSIBLE_DURATION"
        if isinstance(detail["channel_id"], str) and detail["channel_id"] != row["channel_id"]:
            reason = "CHANNEL_CHANGED"
        if reason:
            self._invalidate_source(detail["video_id"], reason, now, "excluded")
            return "excluded"
        if not is_complete(detail):
            return "incomplete"                     # nothing is updated and nothing is renewed: unverified fields keep ageing
        title = " ".join(detail["title"].split())
        db.execute("UPDATE sources SET stage='metadata_only', availability='available', exclusion_reason=NULL, verified_at=?, title=?, published_at=?, "
                   "duration_s=?, made_for_kids=0, privacy_status=? WHERE video_id=?",
                   (iso(now), title, detail["published_at"], detail["duration_s"], detail["privacy"], detail["video_id"]))
        self._place_fingerprint(detail["video_id"], fingerprint(title), now)
        db.execute("UPDATE review_items SET stage='metadata_only', updated_at=? WHERE source_video_id=? AND stage='discovered'", (iso(now), detail["video_id"]))
        return "metadata_only"

    def verify(self, video_ids: list[str], now: datetime, counts: dict, errors: list) -> None:
        """videos.list in small batches. A video that is not returned is recorded as unavailable (deleted, private or otherwise
        gone): the official page does not say how a multi-id request treats a missing id, so absence is never read as 'fine'."""
        size = self.config.youtube.details_batch
        for start in range(0, len(video_ids), size):
            batch = video_ids[start:start + size]
            try:
                data = self.client.videos(batch)
            except YouTubeApiError as exc:
                errors.append({"where": "videos.list", "code": exc.code})
                if exc.quota:
                    raise
                continue
            items = data.get("items")
            if not isinstance(items, list):
                errors.append({"where": "videos.list", "code": "BAD_SHAPE"})
                continue
            seen = set()
            with self.store.transaction():
                for raw in items:
                    detail = parse_video_item(raw)
                    if detail is None or detail["video_id"] not in batch:
                        counts["invalid_items"] += 1
                        continue
                    seen.add(detail["video_id"])
                    result = self._apply_details(detail, now)
                    if result in ("metadata_only", "excluded"):
                        counts[result] += 1
                    elif result == "incomplete":
                        counts["invalid_items"] += 1
                for missing in sorted(set(batch) - seen):
                    if self.store.db.execute("SELECT 1 FROM sources WHERE video_id=?", (missing,)).fetchone():
                        self._invalidate_source(missing, "NOT_RETURNED_BY_API", now, "unavailable")
                        counts["unavailable"] += 1

    # ---- one channel ---------------------------------------------------------------------------------------------------------
    def discover_channel(self, channel_id: str, now: datetime, counts: dict, errors: list, checkpoint: dict) -> bool:
        """Search one allowlisted channel, a bounded number of pages. Returns True if the channel was fully processed."""
        yt = self.config.youtube
        state = checkpoint.setdefault(channel_id, {"pages": 0, "next": None, "done": False, "ids": []})
        if state["done"]:
            return True
        while state["pages"] < yt.max_pages_per_channel:
            try:
                data = self.client.search(channel_id, state["next"])
            except YouTubeApiError as exc:
                errors.append({"where": "search.list", "channel": channel_id, "code": exc.code})
                if exc.quota:
                    raise
                return False
            items = data.get("items")
            if not isinstance(items, list):
                errors.append({"where": "search.list", "channel": channel_id, "code": "BAD_SHAPE"})
                return False
            with self.store.transaction():
                for raw in items:
                    found = parse_search_item(raw, channel_id)
                    if found is None:
                        counts["invalid_items"] += 1
                        continue
                    counts["discovered"] += 1
                    outcome = self._upsert_discovered(found, now)
                    counts[{"new": "new", "duplicate_id": "duplicate_by_video_id", "duplicate_text": "duplicate_by_text_fingerprint"}[outcome]] += 1
                    if outcome == "new":
                        state["ids"].append(found["video_id"])
            state["pages"] += 1
            token = data.get("nextPageToken")
            state["next"] = token if isinstance(token, str) and 0 < len(token) <= 200 else None
            if state["next"] is None:
                break
        state["done"] = True
        return True
