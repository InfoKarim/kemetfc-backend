"""The official YouTube Data API v3 client: search.list and videos.list only, read-only, API key, no scraping.

Endpoints and parameters follow the official reference (developers.google.com/youtube/v3/docs/search/list and
.../videos/list). Quota: the documented defaults are 100 search.list calls per day in their own bucket and 10,000 units per
day for everything else, each call costing 1 (developers.google.com/youtube/v3/determine_quota_cost). Every ATTEMPT,
including a retry, is reserved against the persisted budget before it is sent.
"""
import json
import time
import urllib.parse
from typing import Callable

from learning_engine.config import YouTubeConfig
from learning_engine.http import HttpRequest, TransportError

BASE = "https://www.googleapis.com/youtube/v3"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
KNOWN_REASONS = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded", "keyInvalid", "forbidden",
                 "badRequest", "invalidParameter", "videoNotFound", "notFound", "backendError", "accessNotConfigured", "ipRefererBlocked"}
QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded"}


class YouTubeApiError(Exception):
    """`code` is a short fixed label; the message carries no URL, header or response text."""

    def __init__(self, code: str, status: int | None = None, retryable: bool = False, quota: bool = False):
        super().__init__(code)
        self.code, self.status, self.retryable, self.quota = code, status, retryable, quota


class YouTubeClient:
    def __init__(self, transport: Callable, api_key: str, config: YouTubeConfig, *, before_attempt: Callable[[str], None],
                 pace: Callable[[], None], sleep: Callable[[float], None] = time.sleep):
        self.transport, self._key, self.config = transport, api_key, config
        self.before_attempt, self.pace, self.sleep = before_attempt, pace, sleep
        self.attempts = 0

    def _request(self, endpoint: str, params: dict) -> HttpRequest:
        headers = {"Accept": "application/json"}
        query = dict(params)
        if self.config.api_key_transport == "header":
            headers["x-goog-api-key"] = self._key
        else:
            query["key"] = self._key
        url = f"{BASE}/{endpoint}?{urllib.parse.urlencode(query)}"
        return HttpRequest("GET", url, headers, None, self.config.timeout_s, self.config.max_response_bytes)

    def _call(self, endpoint: str, params: dict, kind: str) -> dict:
        last: YouTubeApiError | None = None
        for attempt in range(1 + self.config.max_retries):
            self.before_attempt(kind)                      # reserves quota; raises when a limit would be exceeded
            self.pace()
            self.attempts += 1
            try:
                response = self.transport(self._request(endpoint, params))
            except TransportError as exc:
                last = YouTubeApiError(f"TRANSPORT_{exc.code}", None, retryable=exc.code in ("TIMEOUT", "CONNECTION"))
            else:
                if response.status == 200:
                    try:
                        data = json.loads(response.body)
                    except ValueError:
                        raise YouTubeApiError("BAD_JSON", 200) from None
                    if not isinstance(data, dict):
                        raise YouTubeApiError("BAD_SHAPE", 200)
                    return data
                reason = None
                try:
                    errors = json.loads(response.body).get("error", {}).get("errors", [])
                    reason = errors[0].get("reason") if errors else None
                except (ValueError, AttributeError, IndexError):
                    pass
                reason = reason if reason in KNOWN_REASONS else None
                quota = reason in QUOTA_REASONS
                retryable = response.status in RETRYABLE_STATUS and not quota
                last = YouTubeApiError(f"HTTP_{response.status}" + (f"_{reason}" if reason else ""), response.status, retryable, quota)
            if not last.retryable or attempt == self.config.max_retries:
                raise last
            self.sleep(self.config.backoff_base_s * (2 ** attempt))
        raise last                                           # pragma: no cover

    def search(self, channel_id: str | None, page_token: str | None = None) -> dict:
        """search.list. With a channel id the search is limited to that channel (allowlisted mode). With None it is the public keyword
        search: no channelId is sent (channelId is an optional parameter in the official reference). `safeSearch=strict` only asks
        YouTube to TRY to exclude restricted content; the documentation does not promise that results are safe, and neither does this engine."""
        params = {"part": "snippet", "type": "video", "q": self.config.query, "safeSearch": "strict",
                  "maxResults": self.config.results_per_page}
        if channel_id is not None:
            params["channelId"] = channel_id
        if page_token:
            params["pageToken"] = page_token
        return self._call("search", params, "youtube_search")

    def videos(self, video_ids: list[str]) -> dict:
        if not 1 <= len(video_ids) <= self.config.details_batch:
            raise ValueError("a videos.list batch is 1 to details_batch ids")
        return self._call("videos", {"part": "snippet,contentDetails,status", "id": ",".join(video_ids)}, "youtube_other")
