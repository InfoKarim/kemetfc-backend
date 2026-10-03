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

// ---- save flow (10-14, 22-25) -----------------------------------------------------------

test("saving sends the time as a JSON string and shows the server's value", async () => {
  const { flows, calls } = flowsWith([{ status: 201, body: measurementBody("4.25") }]);
  const r = await flows.saveTime({ trialId: "V1T000001", text: "4.25" });
  assert.equal(r.state, "SAVED");
  assert.equal(r.measurement.displayed_value, "4.25");
  assert.match(calls[0].raw, /"displayed_value":"4\.25"/);       // a string in the wire body
  assert.doesNotMatch(calls[0].raw, /"displayed_value":4\.25/);  // never a JSON number
  assert.equal(calls[0].path, "/v1/trials/V1T000001/manual-measurements");
  assert.deepEqual(Object.keys(calls[0].body).sort(), ["client_entry_id", "displayed_value", "metric_id"]);
  assert.equal(calls[0].body.metric_id, S.SPRINT_METRIC);
});

test("a typed 4.20 is sent as 4.20 and the server's 4.20 is what is shown", async () => {
  const { flows, calls } = flowsWith([{ status: 201, body: measurementBody("4.20") }]);
  const r = await flows.saveTime({ trialId: "t", text: "4.20" });
  assert.match(calls[0].raw, /"displayed_value":"4\.20"/);
  assert.equal(r.measurement.displayed_value, "4.20");
});

test("42.50 is sent unchanged", async () => {
  const { flows, calls } = flowsWith([{ status: 201, body: measurementBody("42.50") }]);
  await flows.saveTime({ trialId: "t", text: "42.50" });
  assert.equal(calls[0].body.displayed_value, "42.50");
});

test("invalid input never reaches the server", async () => {
  const { flows, calls } = flowsWith([]);
  for (const text of ["-1", "0", "4.255", "1e1", "4,25", "", "x"]) {
    const r = await flows.saveTime({ trialId: "t", text });
    assert.equal(r.state, "INVALID_INPUT");
  }
  assert.equal(calls.length, 0);
});

test("SAVED only when the server confirms; server errors are never success", async () => {
  for (const status of [400, 401, 403, 404, 409, 422, 500, 502, 503]) {
    const { flows } = flowsWith([{ status, body: { detail: "x" } }]);
    const r = await flows.saveTime({ trialId: "t", text: "4.25" });
    assert.notEqual(r.state, "SAVED", String(status));
    assert.ok(["REJECTED", "UNCERTAIN"].includes(r.state));
  }
  const { flows } = flowsWith(["network"]);
  assert.equal((await flows.saveTime({ trialId: "t", text: "4.25" })).state, "UNCERTAIN");
});

test("uncertain outcomes never say 'not saved' and keep the same key for a safe retry", async () => {
  let n = 0;
  const { flows, calls, store } = flowsWith(["network", { status: 200, body: measurementBody("4.25") }],
    () => `key-${++n}-generated`);
  const first = await flows.saveTime({ trialId: "V1T000001", text: "4.25" });
  assert.equal(first.state, "UNCERTAIN");
  assert.doesNotMatch(first.message, /not saved/i);
  assert.match(first.message, /Retry safely/);
  assert.equal(store.has("mm:V1T000001"), true);              // kept for the retry
  const retry = await flows.saveTime({ trialId: "V1T000001", text: "4.25" });
  assert.equal(retry.state, "SAVED");
  assert.equal(calls[0].body.client_entry_id, calls[1].body.client_entry_id); // same identity
  assert.equal(n, 1);                                          // only one key was ever made
  assert.equal(store.has("mm:V1T000001"), false);             // cleared after the server answered
});

test("after a definitive answer the next action gets a fresh key", async () => {
  let n = 0;
  const { flows, calls } = flowsWith([{ status: 422, body: { detail: "bad" } }, { status: 201, body: measurementBody("4.25") }],
    () => `key-${++n}-generated`);
  await flows.saveTime({ trialId: "t", text: "4.25" });
  await flows.saveTime({ trialId: "t", text: "4.25" });
  assert.notEqual(calls[0].body.client_entry_id, calls[1].body.client_entry_id);
});

test("a 500 keeps the key (the request may have been stored)", async () => {
  const { flows, store } = flowsWith([{ status: 503, body: null }]);
  const r = await flows.saveTime({ trialId: "t", text: "4.25" });
  assert.equal(r.state, "UNCERTAIN");
  assert.equal(store.has("mm:t"), true);
});

test("the pending store keeps keys only, never assessment content", async () => {
  const { flows, storage } = flowsWith(["network"]);
  await flows.saveTime({ trialId: "V1T000001", text: "4.25" });
  const stored = JSON.stringify(storage.data);
  assert.match(stored, /key-0001-fixed/);
  assert.doesNotMatch(stored, /4\.25/);
  assert.deepEqual(Object.keys(storage.data), ["kemet_v1_pending_keys"]);
});

// ---- NOT_RECORDED ---------------------------------------------------------------------------

test("not recorded sends a reason and no number", async () => {
  const body = { ...measurementBody(null), entry_status: "NOT_RECORDED", not_recorded_reason: "TIMER_FAILURE" };
  const { flows, calls } = flowsWith([{ status: 201, body }]);
  const r = await flows.saveNotRecorded({ trialId: "t", reason: "TIMER_FAILURE" });
  assert.equal(r.state, "SAVED");
  assert.equal(calls[0].body.entry_status, "NOT_RECORDED");
  assert.equal(calls[0].body.not_recorded_reason, "TIMER_FAILURE");
  assert.equal("displayed_value" in calls[0].body, false);
  assert.doesNotMatch(calls[0].raw, /\b0\b|999/);
});

test("not recorded needs one of the backend's reasons", async () => {
  const { flows, calls } = flowsWith([]);
  for (const reason of ["", "SLOW", "OTHER", null]) {
    assert.equal((await flows.saveNotRecorded({ trialId: "t", reason })).state, "INVALID_INPUT");
  }
  assert.equal(calls.length, 0);
  assert.deepEqual(S.NOT_RECORDED_REASONS.map((r) => r[0]),
    ["TIMER_FAILURE", "TIMER_MISSED_STOP", "TIMER_RESET_EARLY", "TIMER_WRONG_EVENT"]);
});

test("a timer problem is described as a recording problem, never as the player failing", () => {
  const trial = { attempt_number: 3, validity: "VALID", invalid_reason: null, manual_measurements: [
    { entry_status: "NOT_RECORDED", not_recorded_reason: "TIMER_FAILURE", effective_value: null, corrections: [] }] };
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

// ---- attempts -----------------------------------------------------------------------------------

test("attempt numbers continue from the server's list", () => {
  assert.equal(S.nextAttemptNumber([]), 1);
  assert.equal(S.nextAttemptNumber([{ attempt_number: 1 }, { attempt_number: 2 }]), 3);
});

test("adding an attempt sends its number; a repeat press resolves to the same attempt", async () => {
  const { flows, calls } = flowsWith([
    { status: 201, body: { trial_id: "V1T000001", attempt_number: 1 } },
    { status: 200, body: { trial_id: "V1T000001", attempt_number: 1 } }]);
  const a = await flows.addAttempt({ runId: "V1SR000001", attemptNumber: 1 });
  const b = await flows.addAttempt({ runId: "V1SR000001", attemptNumber: 1 });
  assert.equal(a.trial.trial_id, b.trial.trial_id);
  assert.deepEqual(calls[0].body, { attempt_number: 1, validity: "VALID" });
  const inv = flowsWith([{ status: 201, body: {} }]);
  await inv.flows.addAttempt({ runId: "r", attemptNumber: 2, invalidReason: "FALSE_START" });
  assert.deepEqual(inv.calls[0].body, { attempt_number: 2, validity: "INVALID", invalid_reason: "FALSE_START" });
});

test("assessment creation retries reuse one key until the server answers", async () => {
  let n = 0;
  const { flows, calls } = flowsWith(["network", { status: 200, body: { assessment_id: "V1A000001" } }, { status: 201, body: { assessment_id: "V1A000002" } }],
    () => `asm-key-${++n}-generated`);
  const args = { playerId: "TEST_PLAYER_A", date: "2026-09-12", title: "V1 assessment" };
  assert.equal((await flows.createAssessment(args)).state, "UNCERTAIN");
  assert.equal((await flows.createAssessment(args)).state, "SAVED");
  assert.equal(calls[0].body.client_entry_id, calls[1].body.client_entry_id);
  await flows.createAssessment(args);                       // a genuinely new assessment later
  assert.notEqual(calls[1].body.client_entry_id, calls[2].body.client_entry_id);
  assert.equal(calls[0].body.protocol_code, undefined);       // the coach never types protocol or ids
  assert.deepEqual(Object.keys(calls[0].body).sort(), ["assessment_date", "client_entry_id", "player_id", "title"]);
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
  assert.equal(S.friendlyMessage(409, "x", "time"), "This attempt already has a different saved entry. Refresh before continuing.");
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
  const r = await flows.saveTime({ trialId: "t", text: "4.25" });
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

test("the script exposes only measurement-entry behaviour", () => {
  assert.deepEqual(Object.keys(S).sort(), ["CORRECTION_REASONS", "INVALID_REASONS", "NOT_RECORDED_REASONS", "SPRINT_METRIC", "TIME_HELP",
    "createPendingStore", "describeAttempt", "friendlyMessage", "makeFlows", "newKey", "nextAttemptNumber", "outcome", "request", "start", "validateTimeText"]);
  assert.equal(S.newKey({ randomUUID: () => "abc-123" }), "abc-123");
});
