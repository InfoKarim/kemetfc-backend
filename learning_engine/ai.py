"""Optional general AI drafts through the OFFICIAL OpenAI and Anthropic HTTP APIs. Disabled by default; nothing here runs in
the default configuration, and no code path sends anything until every blocker in config.paid_blockers is absent.

What is sent: ONE fixed, generic, child-free prompt per provider (see PROMPT), the model name the operator configured, and
output limits. What is never sent: videos, images, pose data, names, ids, ratings, source titles or any metadata discovered
from YouTube. A draft is stored as an unverified general idea, attached to NO source video, with no authority to approve,
execute or change anything.

Endpoints (official docs, checked 2026-10-08): Anthropic `POST https://api.anthropic.com/v1/messages` with `Authorization: Bearer <key>` (the older
`x-api-key` is documented as a still-supported fallback), `anthropic-version` and `content-type: application/json`, body model / max_tokens / messages
(platform.claude.com/docs/en/api/overview, .../api/messages). OpenAI `POST https://api.openai.com/v1/responses` with Bearer
auth, body model / input / max_output_tokens / store; `store` defaults to true when omitted, so it is sent as false
(developers.openai.com/api/reference/overview, .../resources/responses/methods/create). I could not confirm the exact
response `usage` field names from the OpenAI reference page itself; a response without recognised usage is settled as
UNCERTAIN (its reserved cost stays counted) rather than guessed.
"""
import hashlib
import json
import math

from learning_engine.budget import Budget, BudgetExceeded
from learning_engine.config import Config, paid_blockers
from learning_engine.http import HttpRequest, TransportError
from learning_engine.store import ResearchStore, iso, utc_now

PROMPT = ("Propose one generic football dribbling drill for coach review: title, equipment, step-by-step instructions, "
          "a suggested duration, and safety considerations. Do not claim the drill is suitable for any particular age. "
          "Do not cite or describe any specific video, person or source. This is unverified draft advice.")
ALLOWED_PROMPTS = frozenset({PROMPT})
ENDPOINTS = {"openai": "https://api.openai.com/v1/responses", "anthropic": "https://api.anthropic.com/v1/messages"}
ANTHROPIC_VERSION = "2023-06-01"
RETRYABLE_DEFINITE = {429, 500, 502, 503, 504}


class ProviderDisabled(Exception):
    def __init__(self, blockers: list[str]):
        super().__init__(",".join(blockers))
        self.blockers = blockers


class PayloadRefused(Exception):
    pass


def estimate_tokens(text: str) -> int:
    """A deliberately high estimate (about 3 characters per token), used only to reserve; it is not a tokenizer."""
    return math.ceil(len(text) / 3) + 8


def build_request(provider: str, model: str, max_output_tokens: int, api_key: str, prompt: str = PROMPT, timeout_s: float = 30.0) -> HttpRequest:
    if prompt not in ALLOWED_PROMPTS:
        raise PayloadRefused("only the fixed generic prompt may be sent")
    if provider == "openai":
        body = {"model": model, "input": prompt, "max_output_tokens": max_output_tokens, "store": False}
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    elif provider == "anthropic":
        body = {"model": model, "max_tokens": max_output_tokens, "messages": [{"role": "user", "content": prompt}]}
        headers = {"content-type": "application/json", "anthropic-version": ANTHROPIC_VERSION, "Authorization": f"Bearer {api_key}"}
    else:
        raise PayloadRefused("unknown provider")
    return HttpRequest("POST", ENDPOINTS[provider], headers, json.dumps(body).encode(), timeout_s, 262_144)


def parse_response(provider: str, data: dict) -> tuple[str, int | None, int | None]:
    if provider == "openai":
        text = "\n".join(c.get("text", "") for block in (data.get("output") or []) if isinstance(block, dict)
                         for c in (block.get("content") or []) if isinstance(c, dict) and c.get("type") == "output_text")
        usage = data.get("usage") or {}
        return text, usage.get("input_tokens"), usage.get("output_tokens")
    text = "\n".join(c.get("text", "") for c in (data.get("content") or []) if isinstance(c, dict) and c.get("type") == "text")
    usage = data.get("usage") or {}
    return text, usage.get("input_tokens"), usage.get("output_tokens")


class AIDrafts:
    def __init__(self, store: ResearchStore, config: Config, budget: Budget, transport, env: dict, clock=utc_now):
        self.store, self.config, self.budget, self.transport, self.env, self.clock = store, config, budget, transport, env, clock

    def generate(self, provider: str) -> dict:
        """One general draft from one provider. Raises ProviderDisabled (nothing sent) when any condition is unmet and
        BudgetExceeded when a limit would be passed. A definite 429/5xx response is retried within the limits, each attempt
        reserving again; a timeout or lost connection is UNCERTAIN and is never retried."""
        blockers = paid_blockers(self.config, provider, self.env)
        if blockers:
            raise ProviderDisabled(blockers)
        p = self.config.providers[provider]
        key = self.env[p.api_key_env]
        est_in = estimate_tokens(PROMPT)
        last_error = "NO_ATTEMPT"
        for attempt in range(1 + p.max_retries):
            reservation = self.budget.reserve_ai(provider, est_in, p.max_output_tokens)      # may raise BudgetExceeded
            request = build_request(provider, p.model, p.max_output_tokens, key, timeout_s=p.timeout_s)
            try:
                response = self.transport(request)
            except TransportError as exc:
                self.budget.mark_uncertain(reservation, f"transport {exc.code}")
                raise
            except Exception:
                self.budget.mark_uncertain(reservation, "unexpected error while sending")
                raise
            if response.status in RETRYABLE_DEFINITE and attempt < p.max_retries:
                self.budget.release_ai(reservation, f"HTTP {response.status}, not processed")
                last_error = f"HTTP_{response.status}"
                continue
            if response.status != 200:
                self.budget.mark_uncertain(reservation, f"HTTP {response.status}")
                raise TransportError(f"HTTP_{response.status}")
            try:
                text, tin, tout = parse_response(provider, json.loads(response.body))
            except (ValueError, AttributeError):
                self.budget.mark_uncertain(reservation, "unreadable response")
                raise TransportError("BAD_RESPONSE") from None
            outcome = self.budget.settle_ai(reservation, tin, tout)
            return self._store_draft(provider, p.model, text, outcome, reservation)
        raise TransportError(last_error)

    def _store_draft(self, provider: str, model: str, text: str, outcome: str, reservation) -> dict:
        now = self.clock()
        text = text.strip()[:6000]
        content = {"ai_text": text, "unverified": True, "source_analysis": False, "provider": provider, "model": model}
        digest = hashlib.sha256(text.encode()).hexdigest()
        item_id = f"AI-{provider}-{digest[:12]}"
        with self.store.transaction() as db:
            if db.execute("SELECT 1 FROM review_items WHERE item_id=?", (item_id,)).fetchone():
                return {"item_id": item_id, "duplicate": True, "usage": outcome}
            db.execute("INSERT INTO review_items(item_id,origin,source_video_id,stage,review_status,current_version,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                       (item_id, "AI_GENERAL_DRAFT", None, "ai_draft", "pending", 1, iso(now), iso(now)))
            db.execute("INSERT INTO item_versions VALUES (?,?,?,?,?,?,?)",
                       (item_id, 1, json.dumps(content, sort_keys=True), digest, "AI_DRAFT", f"{provider}:{model}", iso(now)))
            self.store.audit("AI_DRAFT_STORED", f"{provider}:{model}", {"item_id": item_id, "usage": outcome, "reservation": reservation.id}, now)
        return {"item_id": item_id, "duplicate": False, "usage": outcome}
