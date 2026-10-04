/* Kemet V1 - the offline queue for official manual attempts (browser side).
 *
 * WHY: a coach standing on a pitch can lose the connection for a moment. An attempt that has
 * been typed and saved must not disappear. Every attempt is first written to this queue on the
 * device, then sent to the server; it leaves the queue only when the server confirms it.
 *
 * WHAT IS STORED (localStorage, one key, this web origin only):
 *   - a random operation id (the idempotency key sent to the server as client_entry_id)
 *   - the signed-in user's opaque id (so one coach's entries are never sent by another)
 *   - the assessment id
 *   - the attempt itself: station id, series key (turning foot), attempt number, valid/not
 *     valid, the not-valid reason, and the typed time (a STRING such as "4.25") or the
 *     time-not-recorded reason
 *   - a state, timestamps, a retry counter and a short error code
 * NOT STORED: no cookie, session token, CSRF token, password, player name, video, telemetry
 * or any media. The recording system is not involved at all.
 *
 * STATES: LOCAL_PENDING (saved on the device, not confirmed by the server), SYNCING (a request
 * is in flight), SYNCED (the server confirmed it), SYNC_FAILED (the server definitively said
 * no). A network failure is never SYNC_FAILED: the entry stays LOCAL_PENDING and is retried.
 *
 * AFTER A SUCCESSFUL SYNC the typed content is deleted at once. Only a small receipt (operation
 * id, state, server ids, time) stays for 24 hours so a repeat can be recognised, then it is
 * purged. Nothing is ever deleted while it is unsent, and nothing is overwritten on the server:
 * the server answers a repeat with the saved attempt and a clash with a refusal.
 *
 * Exported for node (`module.exports`) so every rule can be tested without a browser.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.V1Outbox = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  const STORAGE_KEY = "kemet_v1_outbox_v1";
  const CORRUPT_KEY = "kemet_v1_outbox_v1_unreadable";
  const STATES = Object.freeze({
    LOCAL_PENDING: "LOCAL_PENDING", SYNCING: "SYNCING", SYNCED: "SYNCED", SYNC_FAILED: "SYNC_FAILED",
  });
  const RECEIPT_MS = 24 * 60 * 60 * 1000;
  const STALE_MS = 14 * 24 * 60 * 60 * 1000;
  // The only attempt fields that may be stored. Anything else is refused.
  const PAYLOAD_KEYS = Object.freeze([
    "station_id", "series_key", "attempt_number", "validity", "invalid_reason",
    "entry_status", "displayed_value", "not_recorded_reason",
  ]);

  function newId(cryptoImpl) {
    const c = cryptoImpl || (typeof crypto !== "undefined" ? crypto : null);
    if (c && typeof c.randomUUID === "function") return c.randomUUID();
    const bytes = new Uint8Array(16);
    if (c && c.getRandomValues) c.getRandomValues(bytes);
    else for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
    return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  }

  /** Keep only the whitelisted attempt fields; null and undefined are dropped. Any other key,
   *  or a time that is not a string, is an error: nothing else may reach the device. */
  function cleanPayload(payload) {
    const out = {};
    for (const [key, value] of Object.entries(payload || {})) {
      if (!PAYLOAD_KEYS.includes(key)) throw new Error(`Not allowed in the offline queue: ${key}`);
      if (value === null || value === undefined) continue;
      if (key === "attempt_number") {
        if (!Number.isInteger(value) || value < 1) throw new Error("attempt_number must be a whole number");
      } else if (typeof value !== "string") {
        throw new Error(`${key} must be text`);
      }
      out[key] = value;
    }
    for (const required of ["station_id", "attempt_number", "validity"]) {
      if (!(required in out)) throw new Error(`${required} is required`);
    }
    return out;
  }

  function sameAttempt(a, b) {
    return a.owner === b.owner && a.assessment_id === b.assessment_id
      && a.payload && b.payload
      && a.payload.station_id === b.payload.station_id
      && (a.payload.series_key || "") === (b.payload.series_key || "")
      && a.payload.attempt_number === b.payload.attempt_number;
  }

  /** `storage` is a localStorage-like object; `now()` returns a Date or ms; `makeId()` returns an id. */
  function createOutbox(options) {
    const storage = options && options.storage;
    const clock = (options && options.now) || (() => Date.now());
    const makeId = (options && options.newId) || newId;
    let flushing = false;
    let unreadable = false;

    const iso = () => new Date(clock()).toISOString();
    const nowMs = () => new Date(clock()).getTime();

    function available() {
      try {
        if (!storage) return false;
        storage.setItem("kemet_v1_outbox_probe", "1");
        storage.removeItem("kemet_v1_outbox_probe");
        return true;
      } catch (e) { return false; }
    }

    function read() {
      let raw = null;
      try { raw = storage ? storage.getItem(STORAGE_KEY) : null; } catch (e) { return []; }
      if (!raw) return [];
      try {
        const data = JSON.parse(raw);
        if (!data || !Array.isArray(data.entries)) throw new Error("shape");
        return data.entries;
      } catch (e) {
        // Never throw the unsent attempts away: keep the unreadable text aside, start empty.
        unreadable = true;
        try { storage.setItem(CORRUPT_KEY, raw); storage.removeItem(STORAGE_KEY); } catch (e2) { /* keep going */ }
        return [];
      }
    }

    function write(entries) {
      storage.setItem(STORAGE_KEY, JSON.stringify({ v: 1, entries }));
    }

    /** Start-up housekeeping: a request interrupted by a restart has an unknown outcome, so
     *  SYNCING goes back to LOCAL_PENDING (the repeat is idempotent); old receipts are purged. */
    function load() {
      const entries = read();
      const limit = nowMs() - RECEIPT_MS;
      let changed = false;
      const kept = [];
      for (const e of entries) {
        if (e.state === STATES.SYNCING) { e.state = STATES.LOCAL_PENDING; e.updated_at = iso(); changed = true; }
        if (e.state === STATES.SYNCED && new Date(e.updated_at).getTime() < limit) { changed = true; continue; }
        kept.push(e);
      }
      if (changed) { try { write(kept); } catch (e) { /* keep going */ } }
      return kept;
    }

    function save(entries) {
      write(entries);
      return entries;
    }

    /** Write the attempt to the device FIRST. Returns the entry (existing, if the same attempt
     *  is already queued). Throws if it cannot be written, so a caller never believes it is safe. */
    function enqueue({ owner, assessmentId, payload }) {
      if (!owner) throw new Error("owner is required");
      if (!assessmentId) throw new Error("assessmentId is required");
      const clean = cleanPayload(payload);
      const entries = read();
      const candidate = { owner, assessment_id: assessmentId, payload: clean };
      const twin = entries.find((e) => e.state !== STATES.SYNCED && sameAttempt(e, candidate));
      if (twin) return twin;
      const entry = {
        op_id: makeId(), owner, assessment_id: assessmentId, state: STATES.LOCAL_PENDING,
        created_at: iso(), updated_at: iso(), tries: 0, last_error: null, payload: clean, result: null,
      };
      entries.push(entry);
      save(entries);
      return entry;
    }

    function list(owner, includeSynced) {
      return read().filter((e) => e.owner === owner && (includeSynced || e.state !== STATES.SYNCED));
    }

    function counts(owner) {
      const out = { LOCAL_PENDING: 0, SYNCING: 0, SYNCED: 0, SYNC_FAILED: 0 };
      for (const e of read()) if (e.owner === owner) out[e.state] += 1;
      return out;
    }

    function update(opId, change) {
      const entries = read();
      const entry = entries.find((e) => e.op_id === opId);
      if (!entry) return null;
      Object.assign(entry, change, { updated_at: iso() });
      save(entries);
      return entry;
    }

    /** Send this owner's LOCAL_PENDING entries one at a time, oldest first. `send(entry)` must
     *  resolve to { state: "SAVED" | "UNCERTAIN" | "REJECTED", ... }. SAVED -> SYNCED (content
     *  deleted); UNCERTAIN (network, timeout, 5xx) -> back to LOCAL_PENDING and the run stops;
     *  REJECTED -> SYNC_FAILED (content kept). One run at a time per outbox. */
    async function flush({ owner, send }) {
      if (flushing) return { skipped: true, synced: 0, failed: 0, pending: counts(owner).LOCAL_PENDING };
      flushing = true;
      const summary = { skipped: false, synced: 0, failed: 0, stopped: false };
      try {
        const queue = read()
          .filter((e) => e.owner === owner && e.state === STATES.LOCAL_PENDING)
          .sort((a, b) => (a.created_at < b.created_at ? -1 : a.created_at > b.created_at ? 1 : 0));
        for (const entry of queue) {
          update(entry.op_id, { state: STATES.SYNCING });
          let result;
          try { result = await send({ ...entry, state: STATES.SYNCING }); }
          catch (error) { result = { state: "UNCERTAIN", message: "send failed" }; }
          if (result && result.state === "SAVED") {
            const trial = result.trial || {};
            const m = (trial.manual_measurements || [])[0] || null;
            update(entry.op_id, {
              state: STATES.SYNCED, payload: null, last_error: null,
              result: { trial_id: trial.trial_id || null, measurement_id: m ? m.measurement_id : null, synced_at: iso() },
            });
            summary.synced += 1;
          } else if (result && result.state === "REJECTED") {
            update(entry.op_id, {
              state: STATES.SYNC_FAILED, tries: (entry.tries || 0) + 1,
              last_error: { status: result.status || 0, message: String(result.message || "").slice(0, 200) },
            });
            summary.failed += 1;
          } else {
            update(entry.op_id, {
              state: STATES.LOCAL_PENDING, tries: (entry.tries || 0) + 1,
              last_error: { status: 0, message: "network" },
            });
            summary.stopped = true;
            break;
          }
        }
      } finally {
        flushing = false;
      }
      summary.pending = counts(owner).LOCAL_PENDING;
      return summary;
    }

    /** Explicit coach action on a SYNC_FAILED entry: send it again (for example after consent
     *  was recorded). The same operation id is kept, so it can never be saved twice. */
    function retry(opId, owner) {
      const entry = read().find((e) => e.op_id === opId && e.owner === owner);
      if (!entry || entry.state !== STATES.SYNC_FAILED) return null;
      return update(opId, { state: STATES.LOCAL_PENDING });
    }

    /** Explicit coach action: give up on a SYNC_FAILED entry. Only the owner, only after the
     *  server refused it. Unsent (LOCAL_PENDING) and syncing entries can never be discarded. */
    function discard(opId, owner) {
      const entries = read();
      const index = entries.findIndex((e) => e.op_id === opId && e.owner === owner && e.state === STATES.SYNC_FAILED);
      if (index < 0) return false;
      entries.splice(index, 1);
      save(entries);
      return true;
    }

    function isStale(entry) {
      return entry.state !== STATES.SYNCED && nowMs() - new Date(entry.created_at).getTime() > STALE_MS;
    }

    return {
      available, load, enqueue, list, counts, flush, retry, discard, isStale,
      wasUnreadable: () => unreadable,
    };
  }

  /** The queue on this device's own browser storage, or a queue that reports itself unavailable
   *  when the browser refuses storage (a private window), so a caller can say so honestly. */
  function createDefaultOutbox() {
    let storage = null;
    try { storage = typeof window !== "undefined" ? window.localStorage : null; } catch (e) { storage = null; }
    return createOutbox({ storage });
  }

  return {
    STORAGE_KEY, CORRUPT_KEY, STATES, PAYLOAD_KEYS, RECEIPT_MS, STALE_MS,
    newId, cleanPayload, createOutbox, createDefaultOutbox,
  };
});
