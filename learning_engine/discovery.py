"""Source discovery: metadata only. It finds out THAT a public video exists on an approved channel, never WHAT it teaches.

States a source can be in:  discovered (found by search)  ->  metadata_only (confirmed by videos.list: public, plausible
duration, not made for kids).  Whether the drill is good, safe or suitable for any age is something only a coach can say,
after inspecting the source themself. A title is never used as evidence of a drill's steps or of its suitability for children.

Duplicates: exact video id, and exact normalized-title fingerprint (Unicode NFKC, case-folded, punctuation removed,
whitespace collapsed). This is NOT semantic deduplication and does not claim to be: two differently worded titles for the
same drill are two candidates.
"""
import hashlib
import re
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
    return {"video_id": item["id"], "channel_id": snip.get("channelId"), "title": snip.get("title"),
            "duration_s": parse_duration(content.get("duration")), "privacy": status.get("privacyStatus"),
            "made_for_kids": status.get("madeForKids"), "live": snip.get("liveBroadcastContent")}


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
        db.execute("INSERT INTO review_items(item_id,origin,source_video_id,stage,review_status,current_version,created_at,updated_at) "
                   "VALUES (?,?,?,?,?,?,?,?)", (f"SRC-{found['video_id']}", "YOUTUBE_SOURCE", found["video_id"], "discovered", "pending", 1, iso(now), iso(now)))
        db.execute("INSERT INTO item_versions(item_id,version,content_json,content_hash,author_kind,author_ref,created_at) VALUES (?,?,?,?,?,?,?)",
                   (f"SRC-{found['video_id']}", 1, "{}", hashlib.sha256(b"{}").hexdigest(), "NONE", None, iso(now)))
        return "new"

    def _apply_details(self, detail: dict, now: datetime) -> str:
        """Promote to metadata_only when the details confirm the candidate; otherwise mark it excluded with a reason."""
        db = self.store.db
        reason = None
        if detail["privacy"] != "public":
            reason = "NOT_PUBLIC"
        elif detail["made_for_kids"] is True:
            reason = "MADE_FOR_KIDS"
        elif detail["live"] not in (None, "none"):
            reason = "LIVE_OR_UPCOMING_BROADCAST"
        elif detail["duration_s"] is None or not MIN_DURATION_S <= detail["duration_s"] <= MAX_DURATION_S:
            reason = "IMPLAUSIBLE_DURATION"
        row = db.execute("SELECT channel_id FROM sources WHERE video_id=?", (detail["video_id"],)).fetchone()
        if row is None:
            return "unknown_source"
        if detail["channel_id"] != row["channel_id"]:
            reason = "CHANNEL_CHANGED"
        if reason:
            db.execute("UPDATE sources SET availability='excluded', exclusion_reason=?, verified_at=? WHERE video_id=?", (reason, iso(now), detail["video_id"]))
            return "excluded"
        db.execute("UPDATE sources SET stage='metadata_only', availability='available', exclusion_reason=NULL, verified_at=?, duration_s=?, "
                   "made_for_kids=?, privacy_status=? WHERE video_id=?",
                   (iso(now), detail["duration_s"], None if detail["made_for_kids"] is None else int(detail["made_for_kids"]),
                    detail["privacy"], detail["video_id"]))
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
                for missing in set(batch) - seen:
                    self.store.db.execute("UPDATE sources SET availability='unavailable', verified_at=? WHERE video_id=?", (iso(now), missing))
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
