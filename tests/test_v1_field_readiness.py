"""S1B3 field readiness: minimal player picker and confirmer list, login return path,
navigation entry, NOT_RECORDED contract, honest wording. Synthetic data only."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db_models import PlayerDB, UserDB
from app.v1 import definitions as d
from main import app
from tests.test_privacy_s0 import ctx, make_user, run_deletion  # noqa: F401  (ctx is a fixture)
from tests.test_v1_api import api  # noqa: F401  (api is a fixture)

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (ROOT / "app/static/v1_sprint.js").read_text()


# ---- player picker (minimal data) -----------------------------------------------------------

@pytest.mark.parametrize("who", ["coach", "admin", "reviewer"])
def test_player_picker_returns_only_id_and_name(api, who):
    body = getattr(api, who).get("/v1/player-picker").json()
    assert set(body) == {"players"}
    ids = {p["player_id"] for p in body["players"]}
    assert {"P_A", "P_B", "P_EMPTY"} <= ids
    for row in body["players"]:
        assert set(row) == {"player_id", "display_name"}
        assert row["display_name"].strip()
    text = str(body).lower()
    for word in ("date_of_birth", "sex", "profile", "team", "guardian", "photo", "weak_foot", "email", "phone"):
        assert word not in text, word


def test_player_picker_excludes_deleted_players_and_is_closed_to_others(api):
    assert run_deletion(api, api.guard_a, "P_A", "REQ_A").status_code == 200
    ids = {p["player_id"] for p in api.coach.get("/v1/player-picker").json()["players"]}
    assert "P_A" not in ids and "P_B" in ids
    assert api.guard_b.get("/v1/player-picker").status_code == 403
    assert TestClient(app).get("/v1/player-picker").status_code == 401


def test_player_picker_is_sorted_by_name(api):
    names = [p["display_name"].lower() for p in api.coach.get("/v1/player-picker").json()["players"]]
    assert names == sorted(names)


# ---- eligible confirmers ----------------------------------------------------------------------

def test_confirmer_list_has_only_other_active_coaches_and_admins(api):
    with api.db() as db:
        make_user(db, "COACH_OFF", "s1b3.inactive.coach", "coach")
        db.commit()
        db.get(UserDB, "COACH_OFF").active = False
        db.commit()
    body = api.coach.get("/v1/eligible-confirmers").json()
    assert set(body) == {"confirmers"}
    ids = {c["user_id"] for c in body["confirmers"]}
    assert ids == {"ADMIN", "COACH2"}                 # not self (COACH), reviewer, guardians or inactive staff
    assert all(set(c) == {"user_id", "username"} for c in body["confirmers"])
    admin_view = {c["user_id"] for c in api.admin.get("/v1/eligible-confirmers").json()["confirmers"]}
    assert admin_view == {"COACH", "COACH2"}


@pytest.mark.parametrize("who", ["reviewer", "guard_a"])
def test_confirmer_list_is_closed_to_reviewers_guardians_and_anonymous(api, who):
    assert getattr(api, who).get("/v1/eligible-confirmers").status_code == 403
    assert TestClient(app).get("/v1/eligible-confirmers").status_code == 401


def test_every_listed_confirmer_is_accepted_by_the_correction_rule(api):
    from tests.test_v1_api import correct, enter, setup_trial
    for confirmer in api.coach.get("/v1/eligible-confirmers").json()["confirmers"]:
        _, _, trial = setup_trial(api)
        mid = enter(api.coach, trial["trial_id"], "4.52").json()["measurement_id"]
        assert correct(api.coach, mid, "4.25", "4.52", confirmer=confirmer["user_id"]).status_code == 201


def test_the_screen_uses_the_minimal_endpoints_not_the_broad_ones():
    assert '"/v1/player-picker"' in SCRIPT and '"/v1/eligible-confirmers"' in SCRIPT
    assert '"/players"' not in SCRIPT and "/messages/recipients" not in SCRIPT


def test_correction_wording_does_not_claim_the_second_person_approved(api):
    assert "They are not asked to approve it." in SCRIPT
    assert "Confirmed by" not in SCRIPT and "who confirms" not in SCRIPT


# ---- login return path -------------------------------------------------------------------------

def test_logged_out_page_visit_keeps_the_query_in_the_login_return_path(api):
    anonymous = TestClient(app)
    response = anonymous.get("/v1/sprint?assessment=V1A000001", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/v1/sprint%3Fassessment%3DV1A000001"
    plain = anonymous.get("/v1/sprint", follow_redirects=False)
    assert plain.headers["location"] == "/login?next=/v1/sprint"


def test_hostile_queries_stay_encoded_inside_one_parameter(api):
    anonymous = TestClient(app)
    for query in ("x=//evil.example", "next=https://evil.example&a=b", "a=%0d%0aX:1", "<script>=1"):
        location = anonymous.get(f"/v1/sprint?{query}", follow_redirects=False).headers["location"]
        assert location.startswith("/login?next=/v1/sprint%3F")
        tail = location.split("next=", 1)[1]
        assert not re.search(r"[?&=<> ]", tail), tail          # nothing can start a second parameter
        assert "\r" not in location and "\n" not in location


def test_the_other_login_redirects_still_work(api):
    assert TestClient(app).get("/dashboard", follow_redirects=False).headers["location"] == "/login?next=/dashboard"


# ---- navigation entry -----------------------------------------------------------------------------

def test_the_assessments_page_links_to_the_sprint_screen(api):
    for who in ("coach", "admin", "reviewer"):
        page = getattr(api, who).get("/assessments-dashboard")
        assert page.status_code == 200
        assert re.search(r'<a [^>]*href="/v1/sprint"[^>]*>\s*Player Assessment: 20 m Sprint\s*</a>', page.text)
    # The legacy button is still there and unchanged.
    assert 'href="/add-assessment">Add Assessment</a>' in api.coach.get("/assessments-dashboard").text


def test_guardians_cannot_reach_the_page_that_holds_the_link(api):
    assert api.guard_a.get("/assessments-dashboard").status_code == 403
    assert api.guard_a.get("/v1/sprint").status_code == 403


# ---- NOT_RECORDED contract ---------------------------------------------------------------------------

def test_not_recorded_contract_is_one_list_everywhere(api):
    from tests.test_v1_api import SPRINT, key, setup_trial
    _, run, _ = setup_trial(api)
    for number, reason in enumerate(d.NOT_RECORDED_REASONS, start=2):
        trial = api.coach.post(f"/v1/station-runs/{run['station_run_id']}/trials", json={"attempt_number": number}).json()
        saved = api.coach.post(f"/v1/trials/{trial['trial_id']}/manual-measurements", json={
            "metric_id": SPRINT, "entry_status": "NOT_RECORDED", "not_recorded_reason": reason, "client_entry_id": key()})
        assert saved.status_code == 201 and saved.json()["not_recorded_reason"] == reason
        assert saved.json()["displayed_value"] is None
    extra = api.coach.post(f"/v1/station-runs/{run['station_run_id']}/trials", json={"attempt_number": 20}).json()
    for retired in ("TIMER_FAILURE", "TIMER_RESET_EARLY", "TIMER_WRONG_EVENT", "SLOW"):
        refused = api.coach.post(f"/v1/trials/{extra['trial_id']}/manual-measurements", json={
            "metric_id": SPRINT, "entry_status": "NOT_RECORDED", "not_recorded_reason": retired, "client_entry_id": key()})
        assert refused.status_code == 422, retired


def test_the_standard_and_the_code_agree_on_the_list():
    standard = next(ROOT.glob("pilot_package_v1/v1_manual_timing/KEMET_ASSESSMENT_V1_MANUAL_TIMING_STANDARD.md"), None)
    if standard is None:
        pytest.skip("the pilot package folder is not part of this checkout")
    line = next(l for l in standard.read_text().splitlines() if l.startswith("| `entry_status`"))
    listed = re.findall(r"`([A-Z_]+)`", line.split("`not_recorded_reason`", 1)[1])
    assert listed == list(d.NOT_RECORDED_REASONS)


# ---- the synthetic field-test launcher ---------------------------------------------------------------

def test_the_pilot_launcher_only_ever_uses_throwaway_storage():
    source = (ROOT / "scripts/v1_synthetic_pilot_server.py").read_text()
    for needed in ("tempfile.mkdtemp", '"DATABASE_URL": f"sqlite:///{tmp /', "PLAYER_VIDEO_UPLOAD_DIR", "DRILL_UPLOAD_DIR",
                   "VIDEO_ANALYSIS_OUTPUT_DIR", "shutil.rmtree(tmp", "signal.SIGTERM"):
        assert needed in source, needed
    assert "cloud_platform.db" not in source and "0.0.0.0" not in source
    assert not re.search(r"""["']\.env["']""", source) and "dotenv" not in source
    assert 'default="127.0.0.1"' in source            # this computer only unless a LAN address is given


def test_the_launcher_refuses_to_seed_anything_but_its_own_temporary_database():
    import importlib.util
    spec = importlib.util.spec_from_file_location("v1_pilot_launcher", ROOT / "scripts/v1_synthetic_pilot_server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(SystemExit, match="not the temporary one"):
        module.seed(Path("/nonexistent/not-this-database"))   # refused before any row is written
