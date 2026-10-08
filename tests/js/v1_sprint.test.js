// Logic tests for app/static/v1_sprint.js (no browser needed). Run: node --test tests/js
// Synthetic data only.
const test = require("node:test");
const assert = require("node:assert/strict");
const S = require("../../app/static/v1_sprint.js");

function memoryStorage() {
  const data = {};
  return { data, getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); } };
}

// A scripted fake server: each call takes the next scripted reply (or a network failure).
function fakeServer(replies) {
  const calls = [];
  const fetchImpl = async (path, init) => {
    calls.push({ path, method: init.method, raw: init.body, body: init.body ? JSON.parse(init.body) : null });
    const next = replies.shift();
    if (next === "network") throw new TypeError("network down");
    return { status: next.status, json: async () => next.body };
  };
  return { fetchImpl, calls };
}

function flowsWith(replies, keyMaker) {
  const server = fakeServer(replies);
  const storage = memoryStorage();
  const store = S.createPendingStore(storage, keyMaker || (() => "key-0001-fixed"));
  return { ...server, storage, store, flows: S.makeFlows({ fetchImpl: server.fetchImpl, store }) };
}

const measurementBody = (value) => ({ measurement_id: "V1MM000001", trial_id: "V1T000001", entry_status: "RECORDED",
  displayed_value: value, original_value: value, effective_value: value, corrections: [] });

// ---- input validation (15-21 of the UI list) ------------------------------------------

test("accepted readings are passed through exactly as typed", () => {
  for (const text of ["4.25", "4.20", "4.2", "42.50", "0.01", "12", " 4.25 "]) {
    const v = S.validateTimeText(text);
    assert.equal(v.ok, true, text);
    assert.equal(v.value, text.trim());
    assert.equal(typeof v.value, "string");
  }
});

test("bad readings are blocked with a friendly message", () => {
  const cases = { "-4.25": "negative", "0": "zero", "0.00": "zero", "4.255": "format", "1e1": "format", "4e1": "format",
    "4,25": "comma", "": "empty", "   ": "empty", "abc": "format", "4.": "format", ".5": "format", "+4.25": "format",
    "NaN": "format", "Infinity": "format", "4.2.5": "format", "٤.٢٥": "format" };
  for (const [text, code] of Object.entries(cases)) {
    const v = S.validateTimeText(text);
    assert.equal(v.ok, false, JSON.stringify(text));
    assert.equal(v.code, code, JSON.stringify(text));
    assert.ok(v.message.length > 5);
  }
  assert.equal(S.validateTimeText(null).ok, false);
  assert.equal(S.validateTimeText(undefined).ok, false);
});

test("no time is ever converted to a number or reformatted", () => {
  assert.equal(S.validateTimeText("42.50").value, "42.50"); // not 4.25, not 42.5
  assert.equal(S.validateTimeText("4.20").value, "4.20");   // trailing zero kept
});

// ---- saving one whole attempt ---------------------------------------------------------------

const entryOf = (payload, op = "op-0001-test") => ({ op_id: op, payload: {
  station_id: "S1", series_key: "", attempt_number: 1, validity: "VALID", entry_status: "RECORDED", displayed_value: "4.25", ...payload } });
const savedReply = (value = "4.25", extra = {}) => ({ status: 201, body: { created: true, station_run: { station_run_id: "V1SR000001" },
  trial: { trial_id: "V1T000001", attempt_number: 1, validity: "VALID", manual_measurements: [measurementBody(value)] }, ...extra } });

test("saving sends the whole attempt in one request, the time as a JSON string", async () => {
  const { flows, calls } = flowsWith([savedReply("4.25")]);
  const r = await flows.saveAttempt({ assessmentId: "V1A000001", entry: entryOf({}) });
  assert.equal(r.state, "SAVED");
  assert.equal(r.trial.manual_measurements[0].displayed_value, "4.25");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, "POST");
  assert.equal(calls[0].path, "/v1/assessments/V1A000001/attempts");
  assert.match(calls[0].raw, /"displayed_value":"4\.25"/);       // a string on the wire
  assert.doesNotMatch(calls[0].raw, /"displayed_value":4\.25/);  // never a JSON number
  assert.deepEqual(Object.keys(calls[0].body).sort(),
    ["attempt_number", "client_entry_id", "displayed_value", "entry_status", "series_key", "station_id", "validity"]);
  assert.equal(calls[0].body.client_entry_id, "op-0001-test");  // the queue's operation id is the idempotency key
});

test("a typed 4.20 and 42.50 are sent unchanged and the server's text is what is shown", async () => {
  for (const typed of ["4.20", "42.50"]) {
    const { flows, calls } = flowsWith([savedReply(typed)]);
    const r = await flows.saveAttempt({ assessmentId: "A1", entry: entryOf({ displayed_value: typed }) });
    assert.equal(calls[0].body.displayed_value, typed);
    assert.equal(r.trial.manual_measurements[0].displayed_value, typed);
  }
});

test("the body carries the station, the turning foot and the attempt number, and leaves empty fields out", () => {
  const body = S.attemptBody(entryOf({ station_id: "S2", series_key: "L", attempt_number: 2 }));
  assert.equal(body.station_id, "S2");
  assert.equal(body.series_key, "L");
  assert.equal(body.attempt_number, 2);
  for (const missing of ["invalid_reason", "not_recorded_reason"]) assert.equal(missing in body, false, missing);
  const invalid = S.attemptBody({ op_id: "op-0002-test", payload: { station_id: "S4", series_key: "", attempt_number: 1, validity: "INVALID", invalid_reason: "MISSED_CONE" } });
  assert.deepEqual(invalid, { station_id: "S4", series_key: "", attempt_number: 1, validity: "INVALID", invalid_reason: "MISSED_CONE", client_entry_id: "op-0002-test" });
});

test("a timer problem sends a reason and no number", async () => {
  const { flows, calls } = flowsWith([savedReply(null)]);
  await flows.saveAttempt({ assessmentId: "A1", entry: entryOf({ entry_status: "NOT_RECORDED", not_recorded_reason: "DEVICE_FAILURE", displayed_value: null }) });
  assert.equal(calls[0].body.entry_status, "NOT_RECORDED");
  assert.equal(calls[0].body.not_recorded_reason, "DEVICE_FAILURE");
  assert.equal("displayed_value" in calls[0].body, false);
});

test("SAVED only when the server confirms; server errors are never success", async () => {
  for (const status of [400, 401, 403, 404, 409, 422, 500, 502, 503]) {
    const { flows } = flowsWith([{ status, body: { detail: "x" } }]);
    const r = await flows.saveAttempt({ assessmentId: "A1", entry: entryOf({}) });
    assert.notEqual(r.state, "SAVED", String(status));
    assert.ok(["REJECTED", "UNCERTAIN"].includes(r.state));
  }
  const { flows } = flowsWith(["network"]);
  assert.equal((await flows.saveAttempt({ assessmentId: "A1", entry: entryOf({}) })).state, "UNCERTAIN");
});

test("uncertain outcomes never say 'not saved' and every retry carries the same operation id", async () => {
  const { flows, calls } = flowsWith(["network", { status: 200, body: { ...savedReply().body, created: false } }]);
  const entry = entryOf({});
  const first = await flows.saveAttempt({ assessmentId: "A1", entry });
  assert.equal(first.state, "UNCERTAIN");
  assert.doesNotMatch(first.message, /not saved/i);
  assert.match(first.message, /Retry safely/);
  const retry = await flows.saveAttempt({ assessmentId: "A1", entry });
  assert.equal(retry.state, "SAVED");                              // the server's 200 says it already had it
  assert.equal(calls[0].body.client_entry_id, calls[1].body.client_entry_id);
});

test("a refusal is reported as a refusal with the server's reason in plain words", async () => {
  const { flows } = flowsWith([{ status: 409, body: { detail: "This attempt number already exists with different content" } }]);
  const r = await flows.saveAttempt({ assessmentId: "A1", entry: entryOf({}) });
  assert.equal(r.state, "REJECTED");
  assert.equal(r.status, 409);
  assert.match(r.message, /already has a different saved entry/);
});

test("the reasons the page offers match the backend's lists", () => {
  assert.deepEqual(S.NOT_RECORDED_REASONS.map((r) => r[0]),
    ["TIMER_MISSED_STOP", "TIMER_RESET_BEFORE_ENTRY", "DEVICE_FAILURE", "EVENT_UNCERTAIN", "OTHER"]);
  assert.deepEqual(Object.keys(S.INVALID_REASONS_BY_STATION), ["S1", "S2", "S4"]);   // only the open stations
  assert.equal(S.INVALID_REASONS_BY_STATION.S4.some(([c]) => c === "MISSED_CONE"), true);
  assert.equal(Object.values(S.INVALID_REASONS_BY_STATION).flat().some(([c]) => c === "CONE_CONTACT"), false);
});

test("a timer problem is described as a recording problem, never as the player failing", () => {
  const trial = { attempt_number: 3, validity: "VALID", invalid_reason: null, manual_measurements: [
    { entry_status: "NOT_RECORDED", not_recorded_reason: "DEVICE_FAILURE", effective_value: null, corrections: [] }] };
  const d = S.describeAttempt(trial);
  assert.equal(d.validity, "Valid attempt");
  assert.equal(d.time, "Time not recorded");
  const shown = `${d.validity} ${d.time}`.toLowerCase();   // the words shown about the attempt itself
  for (const word of ["fail", "slow", "bad", "poor"]) assert.equal(shown.includes(word), false, word);
});

test("validity and time status are separate facts", () => {
  const invalid = S.describeAttempt({ attempt_number: 1, validity: "INVALID", invalid_reason: "FALSE_START", manual_measurements: [] });
  assert.equal(invalid.validity, "Not a valid attempt");
  assert.equal(invalid.time, "No time for this attempt");
  const timed = S.describeAttempt({ attempt_number: 2, validity: "VALID", manual_measurements: [
    { entry_status: "RECORDED", effective_value: "4.31", original_value: "4.31", corrections: [] }] });
  assert.equal(timed.time, "4.31 sec");
});

// ---- attempts waiting on this device -----------------------------------------------------------------

test("attempt numbers continue after the server's list and after the attempts waiting on the device", () => {
  assert.equal(S.nextAttemptNumber([], [], ""), 1);
  assert.equal(S.nextAttemptNumber([{ attempt_number: 1, series_key: "" }, { attempt_number: 2, series_key: "" }], [], ""), 3);
  assert.equal(S.nextAttemptNumber([{ attempt_number: 1, series_key: "" }], [{ attempt_number: 2, series_key: "" }, { attempt_number: 3, series_key: "" }], ""), 4);
});

test("each turning foot counts its own attempts", () => {
  const trials = [{ attempt_number: 1, series_key: "L" }, { attempt_number: 2, series_key: "L" }, { attempt_number: 1, series_key: "R" }];
  assert.equal(S.nextAttemptNumber(trials, [], "L"), 3);
  assert.equal(S.nextAttemptNumber(trials, [], "R"), 2);
  assert.equal(S.nextAttemptNumber(trials, [{ attempt_number: 2, series_key: "R" }], "R"), 3);
});

test("a queued attempt says plainly where it is, and a refused one says the server did not accept it", () => {
  const base = { payload: { station_id: "S1", series_key: "", attempt_number: 2, validity: "VALID", entry_status: "RECORDED", displayed_value: "4.25" } };
  const pending = S.describeQueued({ ...base, state: "LOCAL_PENDING" });
  assert.equal(pending.time, "4.25 sec");
  assert.match(pending.status, /Saved on this device/);
  assert.equal(pending.failed, false);
  assert.match(S.describeQueued({ ...base, state: "SYNCING" }).status, /Sending/);
  const failed = S.describeQueued({ ...base, state: "SYNC_FAILED", last_error: { status: 409, message: "This attempt number already has a different saved entry." } });
  assert.equal(failed.failed, true);
  assert.match(failed.status, /Not accepted by the server/);
  assert.equal(S.describeQueued({ payload: { ...base.payload, validity: "INVALID", invalid_reason: "FALSE_START", entry_status: null, displayed_value: null }, state: "LOCAL_PENDING" }).time, "No time for this attempt");
});

test("the sync banner is silent when nothing waits and honest when something does", () => {
  assert.equal(S.syncBannerModel({ LOCAL_PENDING: 0, SYNCING: 0, SYNCED: 3, SYNC_FAILED: 0 }).visible, false);
  const waiting = S.syncBannerModel({ LOCAL_PENDING: 2, SYNCING: 0, SYNCED: 0, SYNC_FAILED: 0 });
  assert.equal(waiting.visible, true);
  assert.match(waiting.text, /2 attempts saved on this device/);
  assert.equal(waiting.showSync, true);
  const one = S.syncBannerModel({ LOCAL_PENDING: 1 });
  assert.match(one.text, /1 attempt saved/);
  const failed = S.syncBannerModel({ LOCAL_PENDING: 0, SYNC_FAILED: 1 });
  assert.match(failed.text, /not accepted by the server/);
  assert.equal(failed.tone, "warn");
  assert.equal(failed.showSync, false);
});

// ---- the eight-station list -----------------------------------------------------------------------

const station = (over) => ({ station_id: "S1", order: 1, name: "Sprint", enabled: true, state: "NOT_STARTED", series: [
  { series_key: "", label: "", valid_recorded: 0, required_valid: 3 }], ...over });

test("an open station shows its state and its progress, and can be opened", () => {
  const m = S.stationRowModel(station({ state: "IN_PROGRESS", series: [{ series_key: "", label: "", valid_recorded: 1, required_valid: 3 }] }));
  assert.equal(m.label, "1. Sprint");
  assert.equal(m.stateText, "IN PROGRESS");
  assert.equal(m.detail, "valid attempts 1 of 3");
  assert.equal(m.canOpen, true);
});

test("agility shows each turning foot", () => {
  const m = S.stationRowModel(station({ station_id: "S2", order: 2, name: "Agility", series: [
    { series_key: "L", label: "Turning foot: left", valid_recorded: 2, required_valid: 2 },
    { series_key: "R", label: "Turning foot: right", valid_recorded: 0, required_valid: 2 }] }));
  assert.equal(m.detail, "Turning foot: left: valid attempts 2 of 2 · Turning foot: right: valid attempts 0 of 2");
});

test("a station that is coming soon shows only that, with nothing to open and no invented result", () => {
  const m = S.stationRowModel({ station_id: "S3", order: 3, name: "Ball Mastery", enabled: false, state: "COMING_SOON", series: [] });
  assert.equal(m.stateText, "COMING SOON");
  assert.equal(m.canOpen, false);
  assert.equal(m.detail, "");
  assert.deepEqual(Object.keys(S.STATE_WORDS).sort(), ["COMING_SOON", "COMPLETE", "IN_PROGRESS", "NOT_STARTED"]);
});

test("assessment creation retries reuse one key until the server answers", async () => {
  let n = 0;
  const { flows, calls } = flowsWith(["network", { status: 200, body: { assessment_id: "V1A000001" } }, { status: 201, body: { assessment_id: "V1A000002" } }],
    () => `asm-key-${++n}-generated`);
  const args = { playerId: "TEST_PLAYER_A", date: "2026-09-12", title: "Assessment" };
  assert.equal((await flows.createAssessment(args)).state, "UNCERTAIN");
  assert.equal((await flows.createAssessment(args)).state, "SAVED");
  assert.equal(calls[0].body.client_entry_id, calls[1].body.client_entry_id);
  await flows.createAssessment(args);                       // a genuinely new assessment later
  assert.notEqual(calls[1].body.client_entry_id, calls[2].body.client_entry_id);
  assert.equal(calls[0].body.protocol_code, undefined);       // the coach never types protocol or ids
  assert.deepEqual(Object.keys(calls[0].body).sort(), ["assessment_date", "client_entry_id", "player_id", "title"]);
});

test("the pending store keeps keys only, never assessment content", async () => {
  const { flows, storage } = flowsWith(["network"]);
  await flows.createAssessment({ playerId: "TEST_PLAYER_A", date: "2026-09-12", title: "Assessment" });
  const stored = JSON.stringify(storage.data);
  assert.match(stored, /key-0001-fixed/);
  assert.doesNotMatch(stored, /Assessment|4\.25|name/i);        // no title, no time, no name; the key's scope holds an opaque player id and a date
  assert.deepEqual(Object.keys(storage.data), ["kemet_v1_pending_keys"]);
});

// ---- corrections ---------------------------------------------------------------------------------

const correctArgs = { measurementId: "V1MM000001", text: "4.25", expectedEffective: "4.52", reasonCode: "TYPING_ERROR",
  reasonText: "", confirmerId: "TEST_COACH_2", selfId: "TEST_COACH_1" };

test("a correction is a new request with the value the coach saw, never an edit", async () => {
  const reply = { status: 201, body: { correction: { corrected_value: "4.25" }, measurement: { original_value: "4.52", effective_value: "4.25", corrections: [{}] } } };
  const { flows, calls } = flowsWith([reply]);
  const r = await flows.correct(correctArgs);
  assert.equal(r.state, "SAVED");
  assert.equal(calls[0].method, "POST");
  assert.equal(calls[0].path, "/v1/manual-measurements/V1MM000001/corrections");
  assert.deepEqual(calls[0].body, { corrected_value: "4.25", expected_effective_value: "4.52",
    reason_code: "TYPING_ERROR", confirmed_by_user_id: "TEST_COACH_2" });
  assert.equal(r.measurement.original_value, "4.52");
  assert.equal(r.measurement.effective_value, "4.25");
});

test("self-confirmation, a missing confirmer, a missing reason and a bad value are blocked before sending", async () => {
  const { flows, calls } = flowsWith([]);
  const blocked = [
    { ...correctArgs, confirmerId: "TEST_COACH_1" }, { ...correctArgs, confirmerId: "" },
    { ...correctArgs, reasonCode: "" }, { ...correctArgs, reasonCode: "OTHER", reasonText: "  " },
    { ...correctArgs, text: "0" }, { ...correctArgs, text: "4,25" }, { ...correctArgs, text: "4.255" }];
  for (const args of blocked) assert.equal((await flows.correct(args)).state, "INVALID_INPUT");
  assert.equal(calls.length, 0);
  assert.equal((await flows.correct({ ...correctArgs, confirmerId: "" })).field, "confirmer");
});

test("OTHER sends its text", async () => {
  const { flows, calls } = flowsWith([{ status: 201, body: { measurement: {}, correction: {} } }]);
  await flows.correct({ ...correctArgs, reasonCode: "OTHER", reasonText: " read the wrong line " });
  assert.equal(calls[0].body.reason_text, "read the wrong line");
});

test("a stale correction shows the conflict message", async () => {
  const { flows } = flowsWith([{ status: 409, body: { detail: "changed" } }]);
  const r = await flows.correct(correctArgs);
  assert.equal(r.state, "REJECTED");
  assert.match(r.message, /changed since you opened it/);
});

// ---- messages (36-38, 35) ------------------------------------------------------------------------

test("plain-language errors", () => {
  assert.equal(S.friendlyMessage(401, ""), "Your session has expired. Sign in again.");
  assert.equal(S.friendlyMessage(403, "No active guardian consent for performance tracking exists for this player"),
    "This player does not currently have the required assessment consent.");
  assert.equal(S.friendlyMessage(409, "x", "time"), "This attempt number already has a different saved entry. Refresh before continuing.");
  assert.equal(S.friendlyMessage(422, "x", "time"), "Enter the stopwatch reading using seconds and up to two decimal places.");
  assert.match(S.friendlyMessage(500, ""), /Could not confirm save/);
  assert.match(S.friendlyMessage(0, ""), /Retry safely/);
  for (const status of [401, 403, 404, 409, 422, 500]) {
    const text = S.friendlyMessage(status, "Traceback sqlalchemy /Users/x/file.py");
    assert.doesNotMatch(text, /Traceback|sqlalchemy|\/Users/);
  }
});

test("a consent refusal is a refusal with no bypass path in the logic", async () => {
  const { flows } = flowsWith([{ status: 403, body: { detail: "No active guardian consent for performance tracking exists for this player" } }]);
  const r = await flows.saveAttempt({ assessmentId: "A1", entry: entryOf({}) });
  assert.equal(r.state, "REJECTED");
  assert.equal(r.status, 403);
  assert.match(r.message, /required assessment consent/);
  assert.equal(Object.keys(S).some((k) => /override|bypass|force|skip/i.test(k)), false);
});

test("a timeout is reported as unknown, not as failure", async () => {
  const slow = async (_p, init) => new Promise((_, reject) => init.signal.addEventListener("abort", () => reject(new Error("aborted"))));
  const r = await S.request(slow, "POST", "/x", {}, 20);
  assert.equal(r.network, true);
  assert.equal(S.outcome(r, "time", () => ({})).state, "UNCERTAIN");
});

test("the script exposes only assessment-entry behaviour", () => {
  assert.deepEqual(Object.keys(S).sort(), ["CORRECTION_REASONS", "INVALID_REASONS_BY_STATION", "NOT_RECORDED_REASONS", "STATE_WORDS", "TIME_HELP",
    "attemptBody", "createPendingStore", "describeAttempt", "describeQueued", "filterPlayers", "friendlyMessage", "makeFlows", "newKey",
    "nextAttemptNumber", "outcome", "playerListModel", "renderPlayerRows", "request", "selectedLabel", "start", "stationRowModel",
    "syncBannerModel", "validateTimeText"]);
  assert.equal(S.newKey({ randomUUID: () => "abc-123" }), "abc-123");
});


// ---- player list (large tappable rows, no native <select>) ------------------------------------------

function fakeDocument() {
  const make = (tag) => ({
    tag, children: [], attrs: {}, listeners: {}, textContent: "", hidden: false,
    setAttribute(k, v) { this.attrs[k] = String(v); },
    append(child) { this.children.push(child); },
    replaceChildren() { this.children = []; },
    addEventListener(type, fn) { this.listeners[type] = fn; },
    tap() { this.listeners.click(); },
  });
  return { createElement: make, make };
}

const PLAYERS = [{ id: "PILOT_PLAYER_A", name: "Test Player A" }, { id: "PILOT_PLAYER_B", name: "Test Player B" }];

function drawList(players, filter, status, selectedId, picked) {
  const doc = fakeDocument();
  const ui = { list: doc.make("ul"), message: doc.make("p"), retry: doc.make("button") };
  const model = S.playerListModel(players, filter, status, selectedId);
  S.renderPlayerRows(doc, ui, model, (id) => picked.push(id));
  return { ui, model };
}
const rowButtons = (ui) => ui.list.children.map((li) => li.children[0]);

test("two returned players are two rows showing only their display names", () => {
  const { ui } = drawList(PLAYERS, "", "ready", null, []);
  const buttons = rowButtons(ui);
  assert.deepEqual(buttons.map((b) => b.textContent), ["Test Player A", "Test Player B"]);
  assert.deepEqual(ui.list.children.map((li) => li.tag), ["li", "li"]);
  assert.equal(ui.message.hidden, true);
  assert.equal(ui.retry.hidden, true);
  // The player id is never put into the page: not as text, not as an attribute.
  const everything = JSON.stringify(ui.list.children.map((li) => [li.attrs, li.children.map((b) => [b.attrs, b.textContent])]));
  assert.doesNotMatch(everything, /PILOT_PLAYER|_A"|player_id/);
});

test("tapping a row selects that player's id, and only that one", () => {
  const picked = [];
  const { ui } = drawList(PLAYERS, "", "ready", null, picked);
  const [a, b] = rowButtons(ui);
  a.tap();
  b.tap();
  assert.deepEqual(picked, ["PILOT_PLAYER_A", "PILOT_PLAYER_B"]);
});

test("the selected row is marked in the markup (not by colour alone) and the others are not", () => {
  const { ui } = drawList(PLAYERS, "", "ready", "PILOT_PLAYER_B", []);
  const [a, b] = rowButtons(ui);
  assert.equal(a.attrs["aria-pressed"], "false");
  assert.equal(b.attrs["aria-pressed"], "true");
  assert.match(b.attrs.class, /\bselected\b/);
  assert.doesNotMatch(a.attrs.class, /selected/);
  assert.equal(S.selectedLabel(PLAYERS[1]), "Selected: Test Player B");
  assert.equal(S.selectedLabel(null), "No player selected yet");
});

test("the picked player's id is what the next workflow step sends", async () => {
  const picked = [];
  const { ui } = drawList(PLAYERS, "", "ready", null, picked);
  rowButtons(ui)[1].tap();
  assert.deepEqual(picked, ["PILOT_PLAYER_B"]);
  const { flows, calls } = flowsWith([{ status: 201, body: { assessment_id: "V1A000001" } }]);
  await flows.createAssessment({ playerId: picked[0], date: "2026-10-04", title: "V1 assessment" });
  assert.equal(calls[0].body.player_id, "PILOT_PLAYER_B");
  assert.equal(calls[0].path, "/v1/assessments");
});

test("search narrows the rows; empty search brings everyone back", () => {
  const names = (filter) => rowButtons(drawList(PLAYERS, filter, "ready", null, []).ui).map((b) => b.textContent);
  assert.deepEqual(names("Player A"), ["Test Player A"]);
  assert.deepEqual(names("player a"), ["Test Player A"]);
  assert.deepEqual(names("b"), ["Test Player B"]);
  assert.deepEqual(names("A"), ["Test Player A", "Test Player B"]);   // "contains": the letter A is in "Player"
  assert.deepEqual(names(""), ["Test Player A", "Test Player B"]);
  assert.deepEqual(names("   "), ["Test Player A", "Test Player B"]);
  const none = drawList(PLAYERS, "zzz", "ready", null, []);
  assert.equal(none.ui.list.children.length, 0);
  assert.equal(none.ui.message.textContent, "No player matches this search");
  assert.equal(none.ui.message.hidden, false);
  assert.equal(none.ui.retry.hidden, true);
});

test("a selection survives a search that hides it", () => {
  const picked = [];
  const { ui } = drawList(PLAYERS, "Player A", "ready", "PILOT_PLAYER_B", picked);
  assert.equal(rowButtons(ui).length, 1);                 // B is hidden by the search ...
  assert.equal(rowButtons(ui)[0].attrs["aria-pressed"], "false");
  const again = drawList(PLAYERS, "", "ready", "PILOT_PLAYER_B", picked);
  assert.equal(rowButtons(again.ui)[1].attrs["aria-pressed"], "true");   // ... and is still selected when it returns
});

test("no players, a failed load and a slow load each say so", () => {
  const empty = drawList([], "", "ready", null, []);
  assert.equal(empty.ui.message.textContent, "No players available");
  assert.equal(empty.ui.message.hidden, false);
  assert.equal(empty.ui.retry.hidden, true);
  const failed = drawList([], "", "failed", null, []);
  assert.equal(failed.ui.message.textContent, "Could not load players");
  assert.equal(failed.ui.message.hidden, false);
  assert.equal(failed.ui.retry.hidden, false);               // a Retry button is shown
  assert.equal(failed.ui.list.children.length, 0);
  const loading = drawList([], "", "loading", null, []);
  assert.equal(loading.ui.message.textContent, "Loading players…");
  assert.equal(loading.ui.retry.hidden, true);
});

test("a failed load never shows stale rows, and retrying to success clears the message and the button", () => {
  const doc = fakeDocument();
  const ui = { list: doc.make("ul"), message: doc.make("p"), retry: doc.make("button") };
  const draw = (status, players) => S.renderPlayerRows(doc, ui, S.playerListModel(players, "", status, null), () => {});
  draw("ready", PLAYERS);
  assert.equal(ui.list.children.length, 2);
  draw("failed", PLAYERS);                                   // even if old players are still in memory
  assert.equal(ui.list.children.length, 0);
  assert.equal(ui.retry.hidden, false);
  draw("loading", PLAYERS);                                  // Retry pressed
  assert.equal(ui.retry.hidden, true);
  draw("ready", PLAYERS);
  assert.equal(ui.list.children.length, 2);
  assert.equal(ui.message.hidden, true);
  assert.equal(ui.retry.hidden, true);
});

test("every row is a real button with a text name, so keyboard and screen readers work", () => {
  const { ui } = drawList(PLAYERS, "", "ready", null, []);
  for (const button of rowButtons(ui)) {
    assert.equal(button.tag, "button");
    assert.equal(button.attrs.type, "button");
    assert.ok(button.textContent.length > 0);
    assert.equal("tabindex" in button.attrs, false);         // natural Tab order, Enter and Space activate it
    assert.ok(typeof button.listeners.click === "function");
  }
});

test("filtering never changes the stored players", () => {
  const before = JSON.stringify(PLAYERS);
  S.filterPlayers(PLAYERS, "b");
  assert.equal(JSON.stringify(PLAYERS), before);
  assert.notEqual(S.filterPlayers(PLAYERS, ""), PLAYERS);    // a copy, not the same array
});
