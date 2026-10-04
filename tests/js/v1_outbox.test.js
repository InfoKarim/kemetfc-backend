// Logic tests for app/static/v1_outbox.js (no browser needed). Run: node --test tests/js
// Synthetic data only.
const test = require("node:test");
const assert = require("node:assert/strict");
const O = require("../../app/static/v1_outbox.js");

function memoryStorage() {
  const data = {};
  return {
    data,
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => { data[k] = String(v); },
    removeItem: (k) => { delete data[k]; },
  };
}

function clock(start = Date.parse("2026-10-05T10:00:00Z")) {
  const c = { t: start, now: () => c.t, advance: (ms) => { c.t += ms; } };
  return c;
}

let counter = 0;
const ids = () => `op-${String(++counter).padStart(6, "0")}-test`;

function make(storage = memoryStorage(), c = clock()) {
  return { storage, c, box: O.createOutbox({ storage, now: c.now, newId: ids }) };
}

const sprint = (n, value = "4.25") => ({
  station_id: "S1", series_key: "", attempt_number: n, validity: "VALID", entry_status: "RECORDED", displayed_value: value,
});
const enqueue = (box, n = 1, value = "4.25", owner = "U1", assessmentId = "V1A000001") =>
  box.enqueue({ owner, assessmentId, payload: sprint(n, value) });

// A server that stores each operation id once, like the real endpoint does.
function server(options = {}) {
  const saved = new Map();
  const calls = [];
  const send = async (entry) => {
    calls.push(entry.op_id);
    if (options.network && options.network(calls.length)) return { state: "UNCERTAIN", message: "network" };
    if (options.reject && options.reject(entry)) return { state: "REJECTED", status: 409, message: "conflict" };
    const first = !saved.has(entry.op_id);
    if (first) saved.set(entry.op_id, { trial: { trial_id: `T-${saved.size + 1}`, manual_measurements: [{ measurement_id: `M-${saved.size + 1}` }] }, payload: entry.payload });
    const stored = saved.get(entry.op_id);
    if (options.lostResponse && options.lostResponse(calls.length)) return { state: "UNCERTAIN", message: "response lost" };
    return { state: "SAVED", created: first, trial: stored.trial };
  };
  return { send, saved, calls };
}

// ------------------------------------------------------------------ saved on the device first

test("an attempt is written to the device before anything is sent", () => {
  const { box, storage } = make();
  const entry = enqueue(box);
  assert.equal(entry.state, "LOCAL_PENDING");
  const stored = JSON.parse(storage.data[O.STORAGE_KEY]);
  assert.equal(stored.entries.length, 1);
  assert.equal(stored.entries[0].payload.displayed_value, "4.25");        // a string, exactly as typed
});

test("an attempt that cannot be written is refused loudly, never half-trusted", () => {
  const broken = { getItem: () => null, setItem: () => { throw new Error("quota"); }, removeItem: () => {} };
  const { box } = make(broken);
  assert.equal(box.available(), false);
  assert.throws(() => enqueue(box));
});

test("only attempt fields are stored; anything else is refused", () => {
  const { box } = make();
  for (const extra of ["player_name", "cookie", "csrf_token", "password", "token", "video_path", "score", "client_entry_id"]) {
    assert.throws(() => box.enqueue({ owner: "U1", assessmentId: "A1", payload: { ...sprint(1), [extra]: "x" } }), /Not allowed/, extra);
  }
  assert.throws(() => box.enqueue({ owner: "U1", assessmentId: "A1", payload: { ...sprint(1), displayed_value: 4.25 } }), /text/);
  assert.throws(() => box.enqueue({ owner: "U1", assessmentId: "A1", payload: { station_id: "S1", validity: "VALID" } }), /required/);
  assert.throws(() => box.enqueue({ owner: "", assessmentId: "A1", payload: sprint(1) }));
});

test("nothing that identifies a person, a login or a recording is ever written to the device", () => {
  const { box, storage } = make();
  enqueue(box, 1, "4.25", "U-opaque-1", "V1A000042");
  const text = storage.data[O.STORAGE_KEY].toLowerCase();
  for (const word of ["cookie", "token", "csrf", "password", "session", "name", "video", "telemetry", "email", "bearer"]) {
    assert.ok(!text.includes(word), word);
  }
  assert.deepEqual(Object.keys(JSON.parse(storage.data[O.STORAGE_KEY]).entries[0]).sort(),
    ["assessment_id", "created_at", "last_error", "op_id", "owner", "payload", "result", "state", "tries", "updated_at"]);
  assert.deepEqual(Object.keys(storage.data), [O.STORAGE_KEY]);          // exactly one key
});

test("pressing save twice for the same attempt queues it once", () => {
  const { box } = make();
  const one = enqueue(box, 1);
  const two = enqueue(box, 1);
  assert.equal(one.op_id, two.op_id);
  assert.equal(box.list("U1").length, 1);
  assert.notEqual(enqueue(box, 2).op_id, one.op_id);                      // a different attempt is a different entry
});

// ------------------------------------------------------------------------------ network failure

test("a network failure keeps the entry and its content, and is retried later", async () => {
  const { box } = make();
  const entry = enqueue(box);
  const s = server({ network: () => true });
  const summary = await box.flush({ owner: "U1", send: s.send });
  assert.equal(summary.synced, 0);
  const [kept] = box.list("U1");
  assert.equal(kept.state, "LOCAL_PENDING");
  assert.deepEqual(kept.payload, entry.payload);                           // content intact
  assert.equal(kept.tries, 1);
  assert.equal(s.saved.size, 0);
});

test("a thrown error from the sender is treated as 'unknown', not as failure", async () => {
  const { box } = make();
  enqueue(box);
  await box.flush({ owner: "U1", send: async () => { throw new Error("boom"); } });
  assert.equal(box.list("U1")[0].state, "LOCAL_PENDING");
});

test("when the network is down the run stops at the first entry instead of hammering", async () => {
  const { box } = make();
  enqueue(box, 1); enqueue(box, 2); enqueue(box, 3);
  const s = server({ network: () => true });
  const summary = await box.flush({ owner: "U1", send: s.send });
  assert.equal(s.calls.length, 1);
  assert.equal(summary.stopped, true);
  assert.equal(box.counts("U1").LOCAL_PENDING, 3);
});

// ---------------------------------------------------------------------------------------- restart

test("a restart keeps every unsent entry", () => {
  const storage = memoryStorage();
  const first = make(storage);
  enqueue(first.box, 1); enqueue(first.box, 2, "4.31");
  const second = make(storage);                                           // a new page, the same device
  const entries = second.box.load();
  assert.equal(entries.length, 2);
  assert.deepEqual(entries.map((e) => e.payload.displayed_value), ["4.25", "4.31"]);
});

test("a request interrupted by a restart goes back to LOCAL_PENDING (its outcome is unknown)", async () => {
  const storage = memoryStorage();
  const first = make(storage);
  const entry = enqueue(first.box);
  let release;
  const stuck = first.box.flush({ owner: "U1", send: () => new Promise((resolve) => { release = resolve; }) });
  await new Promise((r) => setImmediate(r));
  assert.equal(first.box.list("U1")[0].state, "SYNCING");
  const second = make(storage);                                           // the page was reloaded mid-request
  assert.equal(second.box.load()[0].state, "LOCAL_PENDING");
  assert.equal(second.box.list("U1")[0].op_id, entry.op_id);
  release({ state: "UNCERTAIN" }); await stuck;
});

// -------------------------------------------------------------------------------- retry syncs once

test("a retry sends the saved attempt and it is stored exactly once", async () => {
  const { box } = make();
  enqueue(box);
  const s = server();
  const summary = await box.flush({ owner: "U1", send: s.send });
  assert.equal(summary.synced, 1);
  assert.equal(s.saved.size, 1);
  const second = await box.flush({ owner: "U1", send: s.send });          // nothing left to send
  assert.equal(second.synced, 0);
  assert.equal(s.calls.length, 1);
});

test("a duplicate retry after a lost response does not duplicate the server's attempt", async () => {
  const { box } = make();
  enqueue(box);
  const s = server({ lostResponse: (n) => n === 1 });                      // the server stored it, the answer never arrived
  await box.flush({ owner: "U1", send: s.send });
  assert.equal(box.list("U1")[0].state, "LOCAL_PENDING");
  assert.equal(s.saved.size, 1);
  await box.flush({ owner: "U1", send: s.send });                          // the repeat carries the same operation id
  assert.equal(s.saved.size, 1);                                           // still one attempt on the server
  assert.equal(new Set(s.calls).size, 1);
  assert.equal(box.list("U1").length, 0);
  assert.equal(box.counts("U1").SYNCED, 1);
});

test("concurrent flushes run one at a time", async () => {
  const { box } = make();
  enqueue(box);
  const s = server();
  const [a, b] = await Promise.all([box.flush({ owner: "U1", send: s.send }), box.flush({ owner: "U1", send: s.send })]);
  assert.equal([a, b].filter((x) => x.skipped).length, 1);
  assert.equal(s.calls.length, 1);
});

test("entries are sent oldest first", async () => {
  const { box, c } = make();
  enqueue(box, 1, "4.10"); c.advance(1000); enqueue(box, 2, "4.20"); c.advance(1000); enqueue(box, 3, "4.30");
  const seen = [];
  await box.flush({ owner: "U1", send: async (e) => { seen.push(e.payload.displayed_value); return { state: "SAVED", trial: {} }; } });
  assert.deepEqual(seen, ["4.10", "4.20", "4.30"]);
});

// ---------------------------------------------------------------------------------- failed sync

test("a refusal keeps the content, is not retried by itself, and never erases the local result", async () => {
  const { box } = make();
  const entry = enqueue(box);
  const s = server({ reject: () => true });
  const summary = await box.flush({ owner: "U1", send: s.send });
  assert.equal(summary.failed, 1);
  const [failed] = box.list("U1");
  assert.equal(failed.state, "SYNC_FAILED");
  assert.deepEqual(failed.payload, entry.payload);
  assert.equal(failed.last_error.status, 409);
  await box.flush({ owner: "U1", send: s.send });
  assert.equal(s.calls.length, 1);                                         // not sent again on its own
});

test("one refused entry does not hold back the entries after it", async () => {
  const { box } = make();
  enqueue(box, 1, "4.10"); enqueue(box, 2, "4.20");
  const s = server({ reject: (e) => e.payload.attempt_number === 1 });
  const summary = await box.flush({ owner: "U1", send: s.send });
  assert.deepEqual([summary.failed, summary.synced], [1, 1]);
});

test("the coach can retry a refused entry; it keeps the same operation id", async () => {
  const { box } = make();
  const entry = enqueue(box);
  let refuse = true;
  const s = server({ reject: () => refuse });
  await box.flush({ owner: "U1", send: s.send });
  refuse = false;
  assert.equal(box.retry(entry.op_id, "U1").state, "LOCAL_PENDING");
  await box.flush({ owner: "U1", send: s.send });
  assert.equal(box.counts("U1").SYNCED, 1);
  assert.equal(new Set(s.calls).size, 1);
});

test("only a refused entry can be discarded, and only by its owner", async () => {
  const { box } = make();
  const pending = enqueue(box, 1);
  assert.equal(box.discard(pending.op_id, "U1"), false);                   // unsent: never discardable
  const s = server({ reject: () => true });
  await box.flush({ owner: "U1", send: s.send });
  assert.equal(box.discard(pending.op_id, "U2"), false);                   // someone else's
  assert.equal(box.list("U1").length, 1);
  assert.equal(box.discard(pending.op_id, "U1"), true);
  assert.equal(box.list("U1").length, 0);
});

// ----------------------------------------------------------------------------- after a good sync

test("after a successful sync the typed content is deleted and only a receipt remains", async () => {
  const { box, storage } = make();
  enqueue(box, 1, "4.25");
  await box.flush({ owner: "U1", send: server().send });
  const [receipt] = JSON.parse(storage.data[O.STORAGE_KEY]).entries;
  assert.equal(receipt.state, "SYNCED");
  assert.equal(receipt.payload, null);
  assert.ok(!storage.data[O.STORAGE_KEY].includes("4.25"));
  assert.ok(receipt.result.trial_id && receipt.result.measurement_id && receipt.result.synced_at);
});

test("receipts are purged after 24 hours and unsent entries never are", async () => {
  const storage = memoryStorage();
  const first = make(storage);
  enqueue(first.box, 1);
  enqueue(first.box, 2);
  await first.box.flush({ owner: "U1", send: async (e) => (e.payload.attempt_number === 1 ? { state: "SAVED", trial: {} } : { state: "UNCERTAIN" }) });
  first.c.advance(O.RECEIPT_MS + 1000);
  const later = make(storage, first.c);
  const kept = later.box.load();
  assert.equal(kept.length, 1);
  assert.equal(kept[0].payload.attempt_number, 2);                         // the unsent one survives
});

// ------------------------------------------------------------------------------ people and time

test("one coach never sends another coach's entries", async () => {
  const { box } = make();
  enqueue(box, 1, "4.25", "U1");
  enqueue(box, 1, "5.00", "U2");
  const s = server();
  await box.flush({ owner: "U2", send: s.send });
  assert.equal(s.saved.size, 1);
  assert.equal(box.list("U1").length, 1);                                  // U1's entry is untouched
  assert.equal([...s.saved.values()][0].payload.displayed_value, "5.00");
  assert.equal(box.list("U2").length, 0);
});

test("an entry that has waited a long time is flagged, never deleted", () => {
  const { box, c } = make();
  const entry = enqueue(box);
  assert.equal(box.isStale(entry), false);
  c.advance(O.STALE_MS + 1000);
  assert.equal(box.isStale(box.list("U1")[0]), true);
  assert.equal(box.list("U1").length, 1);
});

test("an unreadable store is set aside, not silently destroyed", () => {
  const storage = memoryStorage();
  storage.data[O.STORAGE_KEY] = "{not json";
  const { box } = make(storage);
  assert.deepEqual(box.load(), []);
  assert.equal(storage.data[O.CORRUPT_KEY], "{not json");
  assert.equal(box.wasUnreadable(), true);
});

test("not-valid attempts and timer problems queue the same way", async () => {
  const { box } = make();
  box.enqueue({ owner: "U1", assessmentId: "A1", payload: { station_id: "S4", series_key: "", attempt_number: 1, validity: "INVALID", invalid_reason: "MISSED_CONE" } });
  box.enqueue({ owner: "U1", assessmentId: "A1", payload: { station_id: "S2", series_key: "L", attempt_number: 1, validity: "VALID", entry_status: "NOT_RECORDED", not_recorded_reason: "DEVICE_FAILURE" } });
  const seen = [];
  await box.flush({ owner: "U1", send: async (e) => { seen.push(e.payload); return { state: "SAVED", trial: {} }; } });
  assert.equal(seen.length, 2);
  assert.equal(seen[1].series_key, "L");
});
