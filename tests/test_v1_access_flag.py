"""The server-side switch for Assessment V1 (V1_ASSESSMENT_ENABLED / V1_ASSESSMENT_ALLOWED_USERNAMES).

OFF is the default and answers 404 to everybody before authentication. ON opens V1 only to the people the
allowlist names, and the existing authentication, role, feature-permission and guardian-consent rules still
apply on top. Synthetic users and an in-memory database only.
"""
import pytest
from fastapi.testclient import TestClient

from main import app
from tests.test_v1_api import api, create_assessment, key  # noqa: F401  (api is a fixture)
from tests.test_privacy_s0 import ctx, make_user  # noqa: F401  (ctx is a fixture the api fixture needs)

V1_GET_PATHS = ["/v1/sprint", "/v1/sprint.js", "/v1/player-picker", "/v1/eligible-confirmers", "/v1/assessments/x",
                "/v1/players/P_A/assessments", "/v1", "/v1/"]
NOT_FOUND = {"detail": "Not Found"}


def set_flag(monkeypatch, enabled, allowed=None):
    if enabled is None:
        monkeypatch.delenv("V1_ASSESSMENT_ENABLED", raising=False)
    else:
        monkeypatch.setenv("V1_ASSESSMENT_ENABLED", enabled)
    if allowed is None:
        monkeypatch.delenv("V1_ASSESSMENT_ALLOWED_USERNAMES", raising=False)
    else:
        monkeypatch.setenv("V1_ASSESSMENT_ALLOWED_USERNAMES", allowed)


# ---- OFF: 404 before authentication --------------------------------------------------------

@pytest.mark.parametrize("value", [None, "", "false", "0", "no", "off", "maybe", "  "])
def test_off_by_default_and_for_any_non_true_value_anonymous_gets_404(api, monkeypatch, value):
    set_flag(monkeypatch, value, "*")
    anonymous = TestClient(app, follow_redirects=False)
    for path in V1_GET_PATHS:
        response = anonymous.get(path)
        assert (response.status_code, response.json()) == (404, NOT_FOUND), path


def test_off_anonymous_write_attempts_are_404_not_401_or_403(api, monkeypatch):
    set_flag(monkeypatch, "false", "*")
    anonymous = TestClient(app, follow_redirects=False)
    for method, path in (("POST", "/v1/assessments"), ("POST", "/v1/trials/T/manual-measurements"),
                         ("PUT", "/v1/x"), ("DELETE", "/v1/x"), ("PATCH", "/v1/x")):
        assert anonymous.request(method, path, json={}).status_code == 404, (method, path)


@pytest.mark.parametrize("who", ["coach", "admin", "guard_a", "reviewer"])
def test_off_authenticated_users_get_404_too(api, monkeypatch, who):
    set_flag(monkeypatch, "false", "*")
    client = getattr(api, who)
    for path in V1_GET_PATHS:
        response = client.get(path)
        assert (response.status_code, response.json()) == (404, NOT_FOUND), (who, path)
    assert create_assessment(client).status_code == 404


def test_off_changes_nothing_outside_v1(api, monkeypatch):
    set_flag(monkeypatch, "false", None)
    assert api.coach.get("/auth/me").status_code == 200
    assert TestClient(app).get("/health").status_code == 200
    assert TestClient(app, follow_redirects=False).get("/dashboard").status_code == 303
    assert TestClient(app).get("/auth/me").status_code == 401


# ---- ON: allowlist ---------------------------------------------------------------------------

@pytest.mark.parametrize("allowed", [None, "", "  ", ",,", " , "])
def test_on_with_an_empty_allowlist_admits_nobody(api, monkeypatch, allowed):
    set_flag(monkeypatch, "true", allowed)
    for who in ("coach", "admin"):
        for path in ("/v1/sprint", "/v1/player-picker"):
            response = getattr(api, who).get(path)
            assert (response.status_code, response.json()) == (404, NOT_FOUND), (who, path)
    assert create_assessment(api.coach).status_code == 404


def test_on_with_a_named_allowlist_admits_only_those_users(api, monkeypatch):
    set_flag(monkeypatch, "true", " S0.Coach , someone.else ")
    assert api.coach.get("/v1/sprint").status_code == 200                       # listed (case-insensitive)
    assert api.coach.get("/v1/player-picker").status_code == 200
    assert create_assessment(api.coach).status_code == 201
    for who in ("admin", "coach2", "reviewer", "guard_a"):                      # authorised roles, not listed
        client = getattr(api, who)
        for path in ("/v1/sprint", "/v1/player-picker"):
            response = client.get(path)
            assert (response.status_code, response.json()) == (404, NOT_FOUND), (who, path)
    assert create_assessment(api.admin).status_code == 404


def test_on_with_star_admits_every_authorised_user_and_roles_still_apply(api, monkeypatch):
    set_flag(monkeypatch, "true", "*")
    assert api.coach.get("/v1/sprint").status_code == 200 and api.admin.get("/v1/sprint").status_code == 200
    assert create_assessment(api.coach).status_code == 201
    assert create_assessment(api.reviewer).status_code == 403                    # reviewer: read only
    assert api.reviewer.get("/v1/player-picker").status_code == 200
    assert create_assessment(api.guard_a).status_code == 403                     # guardians: never


def test_allowlisted_user_without_the_assessments_feature_is_still_refused(api, monkeypatch):
    with api.db() as db:
        make_user(db, "LIMITED", "s1b.limited", "coach")
        db.commit()
        db.execute(__import__("sqlalchemy").text(
            "UPDATE users SET feature_permissions = '[\"dashboard\"]' WHERE user_id = 'LIMITED'"))
        db.commit()
    limited = api.login("s1b.limited")
    set_flag(monkeypatch, "true", "s1b.limited")
    assert limited.get("/v1/sprint").status_code == 403                          # the existing feature check


def test_on_unauthenticated_requests_keep_the_existing_login_behaviour(api, monkeypatch):
    set_flag(monkeypatch, "true", "s0.coach")
    anonymous = TestClient(app, follow_redirects=False)
    page = anonymous.get("/v1/sprint")
    assert page.status_code == 303 and page.headers["location"] == "/login?next=/v1/sprint"
    assert anonymous.get("/v1/player-picker").status_code == 401


def test_the_login_return_is_unchanged_for_v1_and_every_other_page(api, monkeypatch):
    set_flag(monkeypatch, "true", "*")
    anonymous = TestClient(app, follow_redirects=False)
    # this repository keeps the query when it sends a signed-out user to the login page (the S1B3 login-return behaviour), for V1 and for every other page
    assert anonymous.get("/v1/sprint?assessment=V1A1").headers["location"] == "/login?next=/v1/sprint%3Fassessment%3DV1A1"
    assert anonymous.get("/dashboard?x=1").headers["location"] == "/login?next=/dashboard%3Fx%3D1"


# ---- consent still applies to writes while ON -------------------------------------------------

def test_consent_still_gates_writes_when_on_and_allowlisted(api, monkeypatch):
    set_flag(monkeypatch, "true", "s0.coach")
    refused = create_assessment(api.coach, "P_EMPTY")                            # no consent at all
    assert refused.status_code in (403, 409, 422)
    assert create_assessment(api.coach, "P_A").status_code == 201                # performance_tracking consent present
    assert "performance_tracking" in str(refused.json()) or refused.status_code in (403, 409, 422)


def test_the_switch_is_read_per_request(api, monkeypatch):
    set_flag(monkeypatch, "false", "*")
    assert api.coach.get("/v1/sprint").status_code == 404
    monkeypatch.setenv("V1_ASSESSMENT_ENABLED", "true")
    assert api.coach.get("/v1/sprint").status_code == 200
    monkeypatch.setenv("V1_ASSESSMENT_ENABLED", "false")
    assert api.coach.get("/v1/sprint").status_code == 404


def test_the_unit_helpers_parse_the_settings_strictly(monkeypatch):
    from app.v1 import access
    for value, expected in (("1", True), ("TRUE", True), (" yes ", True), ("on", True), ("y", False), ("2", False),
                            ("enabled", False), ("", False)):
        monkeypatch.setenv("V1_ASSESSMENT_ENABLED", value)
        assert access.v1_enabled() is expected, value
    monkeypatch.delenv("V1_ASSESSMENT_ENABLED")
    assert access.v1_enabled() is False
    for path, expected in (("/v1", True), ("/v1/", True), ("/v1/sprint", True), ("//v1/sprint", True), ("/v10", False),
                           ("/v1x/y", False), ("/api/v1/x", False), ("/dashboard", False), ("/", False)):
        assert access.is_v1_path(path) is expected, path
    monkeypatch.setenv("V1_ASSESSMENT_ALLOWED_USERNAMES", "A.B, c.d")
    assert access.allowed_usernames() == frozenset({"a.b", "c.d"})
    assert access.username_may_enter("A.B") and not access.username_may_enter("x") and not access.username_may_enter(None)
    monkeypatch.setenv("V1_ASSESSMENT_ALLOWED_USERNAMES", "a.b, *")
    assert access.allowed_usernames() is None and access.username_may_enter("anyone")
