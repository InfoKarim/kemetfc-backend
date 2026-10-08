"""Coach web screen for Player Assessment (S1B2, extended in Phase 1): access control, page content,
the supporting read route, and the browser-logic tests (run with node when it is installed).

There is no browser test framework in this repository (the web screens are static HTML with
inline script), so behaviour that needs a DOM was checked by hand in a real browser; the
logic is tested in tests/js/v1_sprint.test.js. Synthetic data only.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.v1 import definitions as d
from main import app
from tests.test_v1_api import api, count, create_assessment, enter, setup_trial  # noqa: F401 (api is a fixture)
from tests.test_privacy_s0 import ctx  # noqa: F401  (api depends on it)

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "app/static/v1_sprint.html").read_text()
RAW_SCRIPT = (ROOT / "app/static/v1_sprint.js").read_text()
RAW_OUTBOX = (ROOT / "app/static/v1_outbox.js").read_text()
# Comments explain the rules ("no scores", "never innerHTML"); the checks below are about code.
SCRIPT = re.sub(r"/\*.*?\*/", "", RAW_SCRIPT, flags=re.S)
SCRIPT = re.sub(r"(?m)^\s*//.*$|(?<=[;{}, ])//[^\n\"']*$", "", SCRIPT)
OUTBOX = re.sub(r"/\*.*?\*/", "", RAW_OUTBOX, flags=re.S)
OUTBOX = re.sub(r"(?m)^\s*//.*$|(?<=[;{}, ])//[^\n\"']*$", "", OUTBOX)


# ---- access (UI list 1-5) -------------------------------------------------------------------

def test_anonymous_users_are_sent_to_login_and_cannot_load_the_script(api):
    anonymous = TestClient(app)
    response = anonymous.get("/v1/sprint", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/login")
    assert anonymous.get("/v1/sprint.js").status_code == 401
    assert anonymous.get("/v1/outbox.js").status_code == 401


@pytest.mark.parametrize("who", ["coach", "admin", "reviewer"])
def test_staff_can_open_the_screen(api, who):
    response = getattr(api, who).get("/v1/sprint")
    assert response.status_code == 200 and "PLAYER ASSESSMENT" in response.text
    for path in ("/v1/sprint.js", "/v1/outbox.js"):
        script = getattr(api, who).get(path)
        assert script.status_code == 200 and "javascript" in script.headers["content-type"], path


def test_guardians_cannot_reach_the_screen_or_the_script(api):
    assert api.guard_a.get("/v1/sprint").status_code == 403
    assert api.guard_a.get("/v1/sprint.js").status_code == 403
    assert api.guard_a.get("/v1/outbox.js").status_code == 403


def test_a_user_without_the_assessments_feature_cannot_open_it(api):
    from app.db_models import UserDB
    with api.db() as db:
        db.get(UserDB, "COACH2").feature_permissions = ["dashboard"]
        db.commit()
    assert api.coach2.get("/v1/sprint").status_code == 403


def test_the_backend_still_refuses_writes_a_hidden_button_would_hide(api):
    """Hiding a button is only UX. The reviewer's write calls are refused by the server."""
    _, _, trial = setup_trial(api)
    assert enter(api.reviewer, trial["trial_id"], "4.25").status_code == 403
    assert api.reviewer.post("/v1/assessments", json={}).status_code == 403
    assert count(api, __import__("app.v1.models", fromlist=["V1ManualMeasurementDB"]).V1ManualMeasurementDB) == 0


# ---- the supporting read route -------------------------------------------------------------------

def test_player_assessment_list_returns_summaries_only(api):
    assessment, _, trial = setup_trial(api)
    enter(api.coach, trial["trial_id"], "4.25")
    for who in ("coach", "admin", "reviewer"):
        body = getattr(api, who).get("/v1/players/P_A/assessments").json()
        (row,) = body["assessments"]
        assert row["assessment_id"] == assessment["assessment_id"] and row["status"] == "IN_PROGRESS"
        assert "station_runs" not in row and "4.25" not in str(body)
    assert api.coach.get("/v1/players/NOPE/assessments").status_code == 404
    assert api.guard_a.get("/v1/players/P_A/assessments").status_code == 403
    assert TestClient(app).get("/v1/players/P_A/assessments").status_code == 401
    assert api.coach.get("/v1/players/P_B/assessments").json() == {"assessments": []}


# ---- page content (UI list 7, 8, 39-44, 46, 50, 51) -----------------------------------------------

def visible_text():
    return re.sub(r"<style.*?</style>|<script.*?</script>", " ", PAGE, flags=re.S | re.I)


def test_the_station_list_comes_from_the_server_and_only_the_open_stations_have_controls():
    text = visible_text()
    # The page and script never hard-code a station that is not open; the server's list is shown.
    for soon in ("Ball Mastery", "Passing", "Shooting", "1v1", "Small-Sided", "S3", "S5", "S6", "S7", "S8"):
        assert soon.lower() not in (text + SCRIPT).lower(), soon
    assert re.search(r"INVALID_REASONS_BY_STATION = \{\s*S1:", SCRIPT)
    assert re.findall(r"^    (S\d): \[", re.search(r"INVALID_REASONS_BY_STATION = \{(.*?)\n  \};", SCRIPT, re.S).group(1), re.M) == ["S1", "S2", "S4"]
    assert "COMING_SOON" in SCRIPT and "canOpen" in SCRIPT
    assert "state.overview.stations" in SCRIPT


def test_the_screen_has_no_camera_recording_or_video_control_in_phase_1():
    everything = (visible_text() + SCRIPT + OUTBOX).lower()
    for word in ("record attempt", "camera", "video", "startassessment", "stopassessment", "avfoundation", "dockkit",
                 "getusermedia", "mediarecorder", "telemetry", "tracking", "upload", "webkit.messagehandlers"):
        assert word not in everything, word
    assert "<video" not in PAGE.lower() and 'type="file"' not in PAGE.lower()


def test_the_screen_shows_no_scores_benchmarks_ai_age_bands_or_delete_actions():
    everything = (visible_text() + SCRIPT).lower()
    for word in ("overall", "score", "talent", "potential", "percentile", "benchmark", "ranking", "elite",
                 "age band", "age-band", "confidence", "artificial", "split", "gate time"):
        assert word not in everything, word
    assert re.search(r"\bai\b", everything) is None
    # No delete or remove action: no such words on the page, no DELETE request in the script.
    assert not re.search(r"delete|remove", visible_text(), re.I)
    assert '"DELETE"' not in SCRIPT and "'DELETE'" not in SCRIPT
    assert not re.search(r'"(?:DELETE|PUT|PATCH)"', SCRIPT)
    assert set(re.findall(r'"(GET|POST|PUT|PATCH|DELETE|HEAD)"', SCRIPT)) == {"GET", "POST"}


def test_no_unsafe_rendering_no_console_output_and_no_browser_persistence_of_assessments():
    assert "innerHTML" not in SCRIPT and "outerHTML" not in SCRIPT and "document.write" not in SCRIPT
    assert "dangerouslySetInnerHTML" not in SCRIPT and "eval(" not in SCRIPT
    assert "console." not in SCRIPT
    assert "localStorage" not in SCRIPT and "indexedDB" not in SCRIPT
    assert SCRIPT.count("sessionStorage") == 1                       # only the in-flight key store
    assert 'NAME = "kemet_v1_pending_keys"' in SCRIPT


def test_official_times_are_never_parsed_into_numbers_and_no_thresholds_are_hard_coded():
    for banned in ("parseFloat", "toFixed", "Math.round", "parseInt(text"):
        assert banned not in SCRIPT, banned
    assert not re.search(r"(?<![A-Za-z])Number\(", SCRIPT) and "+text" not in SCRIPT
    assert not re.search(r"\b(plausib|threshold|too fast|too slow|fast|slow)\b", SCRIPT, re.I)


def test_reason_lists_in_the_screen_equal_the_backend_lists():
    def codes(block):
        return re.findall(r'\["([A-Z_]+)",', block)

    nr_block = re.search(r"const NOT_RECORDED_REASONS = \[(.*?)\n  \];", SCRIPT, re.S).group(1)
    assert codes(nr_block) == list(d.NOT_RECORDED_REASONS)
    labels = dict(re.findall(r'\["([A-Z_]+)", "([^"]+)"\]', nr_block))
    assert labels == d.NOT_RECORDED_REASON_LABELS
    by_station = re.search(r"INVALID_REASONS_BY_STATION = \{(.*?)\n  \};", SCRIPT, re.S).group(1)
    for station_id in d.ENABLED_STATIONS:
        block = re.search(rf"\n    {station_id}: \[(.*?)\n    \],", by_station, re.S).group(1)
        assert codes(block) == sorted(codes(block), key=codes(block).index)            # no duplicates
        assert set(codes(block)) == d.INVALID_REASONS[station_id] and len(codes(block)) == len(d.INVALID_REASONS[station_id])
    assert "CONE_CONTACT" not in SCRIPT                                              # not an invalidating reason (D-V1-03)
    assert codes(re.search(r"const CORRECTION_REASONS = \[(.*?)\n  \];", SCRIPT, re.S).group(1)) == list(d.CORRECTION_REASONS)


def test_page_structure_supports_labels_keyboard_and_mobile():
    html = PAGE
    assert '<html lang="en">' in html and 'name="viewport"' in html
    assert 'class="skip"' in html and 'href="#main"' in html
    for field_id in ("player-filter", "invalid-reason"):
        assert f'<label for="{field_id}"' in html, field_id
    assert 'role="alert"' in html and 'aria-labelledby=' in html
    for control in ("add-attempt", "start-assessment"):
        assert re.search(rf'<button[^>]*id="{control}"', html), control
    assert "min-height: 52px" in html and "@media (max-width: 480px)" in html
    # Every generated input has a label tied to it and an error region tied to it.
    assert 'el("label", { for: id' in SCRIPT and '"aria-describedby"' in SCRIPT
    assert 'role: "alert"' in SCRIPT and 'role: "status"' in SCRIPT
    assert "tabindex" not in re.sub(r'id="main" tabindex="-1"', "", html)    # natural tab order only


def test_no_real_child_names_were_introduced():
    for path in (ROOT / "app/static/v1_sprint.html", ROOT / "app/static/v1_sprint.js", ROOT / "app/static/v1_outbox.js",
                 ROOT / "tests/js/v1_sprint.test.js", ROOT / "tests/js/v1_outbox.test.js", Path(__file__)):
        text = path.read_text().lower()
        for name in ("ad" + "am",):     # assembled so this file does not contain the name itself
            assert name not in text, (path.name, name)


# ---- browser logic (UI list 10-25, 28-38) -----------------------------------------------------------

@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_browser_logic_tests_pass_under_node():
    result = subprocess.run(["node", "--test", "tests/js/v1_sprint.test.js", "tests/js/v1_outbox.test.js", "tests/js/login_next.test.js"], cwd=ROOT,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1000:]
    assert re.search(r"# fail 0", result.stdout) and not re.search(r"# pass 0\b", result.stdout)


# ---- player picker without a native select (Bug 1) -------------------------------------------------------

def test_the_player_picker_is_a_tappable_list_not_a_native_select():
    html = PAGE
    assert "player-select" not in html and "player-select" not in SCRIPT and "renderPlayerOptions" not in SCRIPT
    assert re.search(r'<ul id="player-list"[^>]*aria-labelledby="player-list-label"', html)
    assert 'id="player-status"' in html and 'role="status"' in html
    assert re.search(r'<button[^>]*id="player-retry"[^>]*hidden', html)          # Retry exists, hidden until needed
    assert '<label for="player-filter">Find a player</label>' in html
    # Large touch targets, a selected state that is not colour alone, no hover dependence.
    css = re.search(r"\.player-row \{[^}]*\}", html).group(0)
    assert re.search(r"min-height:\s*(5[6-9]|[6-9]\d)px", css) and "width: 100%" in css
    assert 'content: "\\2713' in html and ":hover" not in html
    # The only selects are the two reason lists (not-valid rule, timer problem); players are never a <select>.
    assert re.findall(r'<select id="([a-z-]+)"', html) == ["nr-reason", "invalid-reason"]


def test_the_picker_script_wires_search_retry_and_never_fails_silently():
    assert 'state.playerFilter = e.target.value; renderPlayers();' in SCRIPT
    assert '$("player-retry").addEventListener("click"' in SCRIPT and "once(\"player-retry\", loadPlayers)" in SCRIPT
    assert "Could not load players" in SCRIPT and "No players available" in SCRIPT
    assert "loadFailureMessage(r)" in SCRIPT and "Could not reach the server. Check the connection, then press Retry." in SCRIPT
    assert "init().catch" in SCRIPT                      # a startup error is shown, not swallowed
    assert SCRIPT.count("state.playersStatus = \"failed\"") >= 3
    # Only the display name reaches the page; ids stay in code.
    assert "data-player" not in SCRIPT and "player_id" in SCRIPT


# ---- the offline queue's own file (Phase 1) -----------------------------------------------------------

def test_the_offline_queue_uses_one_browser_key_and_stores_no_credentials_or_media():
    assert "localStorage" in OUTBOX and "indexedDB" not in OUTBOX and "sessionStorage" not in OUTBOX
    assert 'STORAGE_KEY = "kemet_v1_outbox_v1"' in OUTBOX
    assert "document.cookie" not in OUTBOX and "fetch(" not in OUTBOX and "XMLHttpRequest" not in OUTBOX   # it never talks to the network
    assert "innerHTML" not in OUTBOX and "console." not in OUTBOX and "eval(" not in OUTBOX
    keys = re.search(r"PAYLOAD_KEYS = Object\.freeze\(\[(.*?)\]\)", OUTBOX, re.S).group(1)
    assert re.findall(r'"([a-z_]+)"', keys) == [
        "station_id", "series_key", "attempt_number", "validity", "invalid_reason",
        "entry_status", "displayed_value", "not_recorded_reason"]
    for banned in ("password", "csrf", "authorization", "bearer", "player_name", "email", "phone"):
        assert banned not in OUTBOX.lower().replace("csrf token", ""), banned


def test_the_page_never_reads_or_writes_browser_storage_itself_except_the_key_store():
    assert "localStorage" not in SCRIPT and "indexedDB" not in SCRIPT
    assert "V1Outbox.createDefaultOutbox()" in SCRIPT and "outbox.enqueue(" in SCRIPT
    assert SCRIPT.index("outbox.enqueue(") < SCRIPT.index("syncNow()", SCRIPT.index("outbox.enqueue("))   # saved on the device first, then sent


def test_unsent_attempts_can_only_be_discarded_after_the_server_refused_them():
    assert "outbox.discard(entry.op_id, owner())" in SCRIPT
    assert 'entry.state === "SYNC_FAILED"' in SCRIPT
    assert "CONFIRM: DISCARD THIS ATTEMPT" in SCRIPT                      # a second, deliberate tap
