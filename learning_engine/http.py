"""The only code that touches the network, and it is small on purpose: https only, exact host allowlist, no redirects,
hard timeout, hard response-size cap, and errors that never carry a URL, a header or a response body (so a key can
never end up in a log or a report)."""
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

ALLOWED_HOSTS = frozenset({"www.googleapis.com", "api.openai.com", "api.anthropic.com"})


NEVER_SENT_CODES = frozenset({"NOT_HTTPS", "HOST_NOT_ALLOWED"})


class TransportError(Exception):
    """`code` is one of a fixed set; the message never includes request or response content. `sent` is False ONLY when the
    request is proven never to have left this process (it was refused by the checks before any connection); every other
    failure (timeout, lost connection, refused redirect, oversized answer) may have reached the provider, so it is True."""

    def __init__(self, code: str, sent: bool | None = None):
        super().__init__(code)
        self.code = code
        self.sent = (code not in NEVER_SENT_CODES) if sent is None else sent


@dataclass
class HttpRequest:
    method: str
    url: str
    headers: dict = field(default_factory=dict)
    body: bytes | None = None
    timeout_s: float = 15.0
    max_bytes: int = 262_144


@dataclass
class HttpResponse:
    status: int
    body: bytes
    headers: dict = field(default_factory=dict)        # lower-case names; only used to read Retry-After


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise TransportError("REDIRECT_REFUSED")


class UrllibTransport:
    def __init__(self, allowed_hosts=ALLOWED_HOSTS):
        self.allowed_hosts = frozenset(allowed_hosts)

    def check(self, url: str) -> None:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https":
            raise TransportError("NOT_HTTPS")
        if parts.hostname not in self.allowed_hosts or parts.username or parts.password or (parts.port not in (None, 443)):
            raise TransportError("HOST_NOT_ALLOWED")

    def __call__(self, request: HttpRequest) -> HttpResponse:
        self.check(request.url)
        req = urllib.request.Request(request.url, data=request.body, headers=request.headers, method=request.method)
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=request.timeout_s) as response:
                body = response.read(request.max_bytes + 1)
                status = response.status
                headers = {k.lower(): v for k, v in response.headers.items()}
        except urllib.error.HTTPError as exc:
            body, status = exc.read(request.max_bytes + 1), exc.code
            headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
        except TransportError:
            raise
        except (socket.timeout, TimeoutError):
            raise TransportError("TIMEOUT") from None
        except (urllib.error.URLError, OSError):
            raise TransportError("CONNECTION") from None
        if len(body) > request.max_bytes:
            raise TransportError("RESPONSE_TOO_LARGE")
        return HttpResponse(status, body, headers)


def redact(text: str, secrets=()) -> str:
    """Remove known secrets and any key-looking query value from text that might be logged."""
    import re
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return re.sub(r"(?i)(key|token|authorization)=[^&\s]+", r"\1=[REDACTED]", text)
