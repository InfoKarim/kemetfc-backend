"""Server-side switch for Assessment V1 (manual timing).

Two settings, both read from the environment on every request so a deployment setting takes effect
without code changes and tests can change them:

``V1_ASSESSMENT_ENABLED``
    OFF unless set to ``1``, ``true``, ``yes`` or ``on`` (any case). Unset, empty or anything else is OFF.
    While OFF every path under ``/v1`` answers 404 to everybody, before authentication, so the feature is
    indistinguishable from a route that does not exist and nothing about it can be probed.

``V1_ASSESSMENT_ALLOWED_USERNAMES``
    Only consulted while the feature is ON. A comma-separated list of usernames (case-insensitive).
      * a list of usernames  - only those signed-in users may enter V1; everybody else gets the same 404.
      * ``*``                - every signed-in user the existing role and feature checks allow (general
                               availability); this must be written down explicitly.
      * unset or empty       - NOBODY. This is deliberate fail-closed behaviour in every environment:
                               turning the feature on without naming who may use it opens it to no one.

The switch only decides who may reach V1. The existing authentication, the admin/coach/reviewer role
checks, the ``assessments`` feature permission and the guardian-consent checks all still apply on top of it.
It does not change the database schema, which the migrations create either way.
"""
import os
import re

ENABLED_ENV = "V1_ASSESSMENT_ENABLED"
ALLOWED_ENV = "V1_ASSESSMENT_ALLOWED_USERNAMES"
_TRUE = {"1", "true", "yes", "on"}
EVERYONE = "*"


def v1_enabled() -> bool:
    return os.getenv(ENABLED_ENV, "").strip().lower() in _TRUE


def allowed_usernames() -> frozenset[str] | None:
    """``None`` means every authorised user (``*``); an empty set means nobody."""
    names = {part.strip().lower() for part in os.getenv(ALLOWED_ENV, "").split(",") if part.strip()}
    if EVERYONE in names:
        return None
    return frozenset(names)


def is_v1_path(path: str) -> bool:
    cleaned = re.sub(r"/{2,}", "/", path)
    return cleaned == "/v1" or cleaned.startswith("/v1/")


def username_may_enter(username: str | None) -> bool:
    allowed = allowed_usernames()
    if allowed is None:
        return True
    return bool(username) and username.strip().lower() in allowed
