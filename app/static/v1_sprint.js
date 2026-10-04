/* Kemet V1 - Player Assessment (coach web page): pick a player, start or continue an assessment,
 * see all eight stations, and enter official manual times for Sprint, Agility and Dribbling.
 *
 * Two parts in one file:
 *   1. Logic (no DOM): input validation, plain-language errors, idempotency keys, the save and
 *      correct flows, and the words/models the screen shows. Exported for node (`module.exports`)
 *      so it can be tested without a browser (tests/test_v1_sprint_ui.py runs them with
 *      `node --test` when node is installed).
 *   2. Page wiring (only when `document` exists): builds the screen with textContent, never
 *      innerHTML, so assessment content is never interpreted as markup.
 *
 * Rules kept here on purpose:
 *   - Official times are STRINGS. Nothing here parses a time into a JS number.
 *   - The server response is the only source of truth: "SAVED" appears after a 200/201. Until
 *     then an attempt is only "saved on this device" (see v1_outbox.js, the offline queue).
 *   - Only the stations the server says are open take entries. The others show COMING SOON.
 *   - No plausibility thresholds, scores, benchmarks, age bands or AI anywhere.
 *   - Nothing about an assessment is kept in the browser except (a) in-flight idempotency keys
 *     (sessionStorage, key only) and (b) the offline queue's unsent attempts (v1_outbox.js).
 *   - Recording is not part of this screen yet; there is no camera or video control.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.V1Sprint = api;
  if (typeof document !== "undefined") api.start();
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  const REQUEST_TIMEOUT_MS = 15000;
  const SYNC_INTERVAL_MS = 20000;

  // Backend-supported lists (kept equal to app/v1/definitions.py by a test).
  const NOT_RECORDED_REASONS = [
    ["TIMER_MISSED_STOP", "Timer missed the finish"],
    ["TIMER_RESET_BEFORE_ENTRY", "Stopwatch was reset before the time was written down"],
    ["DEVICE_FAILURE", "Stopwatch stopped working"],
    ["EVENT_UNCERTAIN", "Timer was not sure of the exact moment"],
    ["OTHER", "Another timing problem"],
  ];
  const INVALID_REASONS_BY_STATION = {
    S1: [
      ["FALSE_START", "False start (moved before the signal)"],
      ["LANE_VIOLATION", "Left the lane"],
      ["STOPPED_EARLY", "Stopped before the line"],
      ["FALL_OR_TRIP", "Fell or tripped"],
      ["INTERFERENCE", "Interference"],
    ],
    S2: [
      ["FALSE_START", "False start (moved before the signal)"],
      ["TURNED_BEFORE_L", "Turned before reaching L"],
      ["FOOT_NOT_AT_L", "Foot was not at L"],
      ["WRONG_TURN_SIDE", "Turned on the wrong side"],
      ["FALL_OR_TRIP", "Fell or tripped"],
      ["LANE_VIOLATION", "Left the lane"],
      ["INTERFERENCE", "Interference"],
    ],
    S4: [
      ["MISSED_CONE", "Missed a mandatory cone or gate"],
      ["HANDS_USED", "Used the hands"],
      ["FALSE_START", "False start (moved before the signal)"],
      ["BALL_NOT_CONTROLLED_AT_START", "Ball was not under control at the start"],
      ["BALL_NOT_CONTROLLED_AT_FINISH", "Ball was more than 1.5 m away at the finish"],
      ["INTERFERENCE", "Interference"],
      ["FALL_OR_TRIP", "Fell or tripped"],
    ],
  };
  const CORRECTION_REASONS = [
    ["DECIMAL_SLIP", "Decimal point in the wrong place"],
    ["TYPING_ERROR", "Typing error"],
    ["MISREAD_DISPLAY", "Misread the stopwatch display"],
    ["WRONG_TRIAL", "Entered on the wrong attempt"],
    ["OTHER", "Other (write the reason)"],
  ];
  const STATE_WORDS = {
    NOT_STARTED: "NOT STARTED", IN_PROGRESS: "IN PROGRESS", COMPLETE: "COMPLETE", COMING_SOON: "COMING SOON",
  };

  const TIME_HELP = "Enter the stopwatch reading using seconds and up to two decimal places.";

  // ------------------------------------------------------------------ input validation

  /** Friendly, immediate check. The backend stays authoritative: this only avoids a
   *  pointless round trip and never changes what the coach typed. */
  function validateTimeText(raw) {
    const text = String(raw == null ? "" : raw).trim();
    if (text === "") return { ok: false, code: "empty", message: "Enter the stopwatch reading." };
    if (text.includes(",")) return { ok: false, code: "comma", message: "Use a decimal point, not a comma." };
    if (text.startsWith("-")) return { ok: false, code: "negative", message: "The time must be greater than zero." };
    if (!/^\d+(\.\d{1,2})?$/.test(text)) return { ok: false, code: "format", message: TIME_HELP };
    if (!/[1-9]/.test(text)) return { ok: false, code: "zero", message: "The time must be greater than zero." };
    return { ok: true, value: text };
  }

  // ------------------------------------------------------------------ error language

  function friendlyMessage(status, detail, context) {
    const text = typeof detail === "string" ? detail.toLowerCase() : "";
    if (status === 401) return "Your session has expired. Sign in again.";
    if (status === 403) {
      if (text.includes("consent")) return "This player does not currently have the required assessment consent.";
      return "Your account is not allowed to do this.";
    }
    if (status === 404) return "This item could not be found. Refresh the page and try again.";
    if (status === 409) {
      if (context === "correction") return "The saved time changed since you opened it. Refresh and check it before correcting.";
      return "This attempt number already has a different saved entry. Refresh before continuing.";
    }
    if (status === 422) {
      if (context === "time") return TIME_HELP;
      if (context === "correction") return "Check the corrected time, the reason and the second person.";
      if (context === "station") return "This station is not available yet.";
      return "The server did not accept this entry. Check it and try again.";
    }
    return "Could not confirm save. It may have reached the server. Retry safely: nothing will be saved twice.";
  }

  // ------------------------------------------------------------------ idempotency keys

  function newKey(cryptoImpl) {
    const c = cryptoImpl || (typeof crypto !== "undefined" ? crypto : null);
    if (c && typeof c.randomUUID === "function") return c.randomUUID();
    const bytes = new Uint8Array(16);
    if (c && c.getRandomValues) c.getRandomValues(bytes);
    else for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
    return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  }

  /** In-flight operation keys. A key is created once per logical action and reused for every
   *  retry of that action, then removed as soon as the server answers definitively. Only the
   *  key is stored (sessionStorage), never any assessment content. */
  function createPendingStore(storage, keyMaker) {
    const NAME = "kemet_v1_pending_keys";
    const make = keyMaker || newKey;
    function read() {
      try { return JSON.parse((storage && storage.getItem(NAME)) || "{}"); } catch (e) { return {}; }
    }
    function write(map) {
      try { if (storage) storage.setItem(NAME, JSON.stringify(map)); } catch (e) { /* private mode: keep going */ }
    }
    return {
      getKey(scope) {
        const map = read();
        if (!map[scope]) { map[scope] = make(); write(map); }
        return map[scope];
      },
      clear(scope) {
        const map = read();
        if (scope in map) { delete map[scope]; write(map); }
      },
      has(scope) { return scope in read(); },
    };
  }

  // ------------------------------------------------------------------ HTTP

  /** One JSON request. A network error or timeout is reported as `network`, never as a
   *  rejection, so callers can tell "the server said no" from "we do not know". */
  async function request(fetchImpl, method, path, body, timeoutMs) {
    const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    const timer = controller ? setTimeout(() => controller.abort(), timeoutMs || REQUEST_TIMEOUT_MS) : null;
    try {
      const init = { method, headers: { Accept: "application/json" } };
      if (body !== undefined) {
        init.headers["Content-Type"] = "application/json";
        init.body = JSON.stringify(body);
      }
      if (controller) init.signal = controller.signal;
      const response = await fetchImpl(path, init);
      let json = null;
      try { json = await response.json(); } catch (e) { json = null; }
      return { status: response.status, body: json, network: false };
    } catch (error) {
      return { status: 0, body: null, network: true };
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  function detailOf(result) {
    return result && result.body && typeof result.body.detail === "string" ? result.body.detail : "";
  }

  /** Turn a response into one of: SAVED (server confirmed), UNCERTAIN (we do not know),
   *  REJECTED (the server said no). */
  function outcome(result, context, onSaved) {
    if (result.status === 200 || result.status === 201) return { state: "SAVED", ...onSaved(result.body), status: result.status };
    if (result.network || result.status >= 500 || result.status === 0) {
      return { state: "UNCERTAIN", status: result.status, message: friendlyMessage(0, "", context) };
    }
    return { state: "REJECTED", status: result.status, message: friendlyMessage(result.status, detailOf(result), context) };
  }

  // ------------------------------------------------------------------ flows (testable)

  /** The request body for one whole attempt. Null fields are left out. `entry.op_id` is the
   *  idempotency key (the operation id the offline queue created), so every retry of one
   *  attempt carries the same key. The time stays a string exactly as typed. */
  function attemptBody(entry) {
    const p = entry.payload;
    const body = {
      station_id: p.station_id, series_key: p.series_key || "", attempt_number: p.attempt_number,
      validity: p.validity, client_entry_id: entry.op_id,
    };
    for (const key of ["invalid_reason", "entry_status", "displayed_value", "not_recorded_reason"]) {
      if (p[key] != null) body[key] = p[key];
    }
    return body;
  }

  function makeFlows(deps) {
    const { fetchImpl, store } = deps;

    async function createAssessment({ playerId, date, title }) {
      const scope = `asm:${playerId}:${date}`;
      const key = store.getKey(scope);
      const result = await request(fetchImpl, "POST", "/v1/assessments", {
        player_id: playerId, assessment_date: date, title: title || null, client_entry_id: key,
      });
      const done = outcome(result, "assessment", (b) => ({ assessment: b }));
      if (done.state !== "UNCERTAIN") store.clear(scope);
      return done;
    }

    /** Send one whole attempt in one request. Used for the first send and for every retry. */
    async function saveAttempt({ assessmentId, entry }) {
      const result = await request(fetchImpl, "POST", `/v1/assessments/${encodeURIComponent(assessmentId)}/attempts`, attemptBody(entry));
      return outcome(result, "attempt", (b) => ({ trial: b.trial, station_run: b.station_run, created: b.created }));
    }

    async function correct({ measurementId, text, expectedEffective, reasonCode, reasonText, confirmerId, selfId }) {
      const checked = validateTimeText(text);
      if (!checked.ok) return { state: "INVALID_INPUT", message: checked.message, code: checked.code, field: "value" };
      if (!CORRECTION_REASONS.some(([code]) => code === reasonCode)) {
        return { state: "INVALID_INPUT", message: "Choose why the time is being corrected.", field: "reason" };
      }
      if (reasonCode === "OTHER" && !String(reasonText || "").trim()) {
        return { state: "INVALID_INPUT", message: "Write the reason for the correction.", field: "reason-text" };
      }
      if (!confirmerId) return { state: "INVALID_INPUT", message: "Choose the second person to record with this correction.", field: "confirmer" };
      if (confirmerId === selfId) return { state: "INVALID_INPUT", message: "The second person must be someone other than you.", field: "confirmer" };
      const body = {
        corrected_value: checked.value, expected_effective_value: expectedEffective,
        reason_code: reasonCode, confirmed_by_user_id: confirmerId,
      };
      if (reasonCode === "OTHER") body.reason_text = String(reasonText).trim();
      const result = await request(fetchImpl, "POST", `/v1/manual-measurements/${measurementId}/corrections`, body);
      return outcome(result, "correction", (b) => ({ measurement: b.measurement, correction: b.correction }));
    }

    return { createAssessment, saveAttempt, correct };
  }

  // ------------------------------------------------------------------ attempts and stations (models)

  /** The next attempt number in one series: after the highest number the server holds and the
   *  highest number waiting in the offline queue, so two attempts entered offline never clash. */
  function nextAttemptNumber(trials, queuedPayloads, seriesKey) {
    const key = seriesKey || "";
    const fromServer = (trials || []).filter((t) => (t.series_key || "") === key).map((t) => t.attempt_number);
    const fromQueue = (queuedPayloads || []).filter((p) => (p.series_key || "") === key).map((p) => p.attempt_number);
    return Math.max(0, ...fromServer, ...fromQueue) + 1;
  }

  /** Words for one saved attempt, from server facts only. Trial validity and the time
   *  recording status are kept apart: a timer problem never reads as the child failing. */
  function describeAttempt(trial) {
    const m = (trial.manual_measurements || [])[0] || null;
    const validity = trial.validity === "VALID" ? "Valid attempt" : "Not a valid attempt";
    let time;
    if (!m) time = trial.validity === "VALID" ? "No time yet" : "No time for this attempt";
    else if (m.entry_status === "NOT_RECORDED") time = "Time not recorded";
    else time = `${m.effective_value} sec`;
    return { validity, time, measurement: m };
  }

  /** Words for an attempt that is still in the offline queue. */
  function describeQueued(entry) {
    const p = entry.payload || {};
    const validity = p.validity === "VALID" ? "Valid attempt" : "Not a valid attempt";
    let time;
    if (p.validity !== "VALID") time = "No time for this attempt";
    else if (p.entry_status === "NOT_RECORDED") time = "Time not recorded";
    else time = `${p.displayed_value} sec`;
    let status;
    if (entry.state === "SYNC_FAILED") {
      const why = entry.last_error && entry.last_error.message ? entry.last_error.message : "the server did not accept it";
      status = `Not accepted by the server: ${why}`;
    } else if (entry.state === "SYNCING") status = "Sending to the server…";
    else status = "Saved on this device. Waiting to reach the server.";
    return { validity, time, status, failed: entry.state === "SYNC_FAILED", state: entry.state };
  }

  /** What one row of the eight-station list shows. Only open stations can be opened. */
  function stationRowModel(station) {
    const label = `${station.order}. ${station.name}`;
    const stateText = STATE_WORDS[station.state] || station.state;
    let detail = "";
    if (station.enabled) {
      const parts = (station.series || []).map((s) =>
        `${s.label ? `${s.label}: ` : ""}valid attempts ${s.valid_recorded} of ${s.required_valid}`);
      detail = parts.join(" · ");
    }
    return { id: station.station_id, label, stateText, state: station.state, detail, canOpen: !!station.enabled };
  }

  /** The sync banner, from queue counts only. */
  function syncBannerModel(counts) {
    const waiting = (counts.LOCAL_PENDING || 0) + (counts.SYNCING || 0);
    const failed = counts.SYNC_FAILED || 0;
    if (!waiting && !failed) return { visible: false, text: "", tone: "", showSync: false };
    const parts = [];
    if (waiting) parts.push(`${waiting} attempt${waiting === 1 ? "" : "s"} saved on this device, waiting to reach the server.`);
    if (failed) parts.push(`${failed} attempt${failed === 1 ? " was" : "s were"} not accepted by the server and need${failed === 1 ? "s" : ""} your attention.`);
    return { visible: true, text: parts.join(" "), tone: failed ? "warn" : "saving", showSync: waiting > 0 };
  }

  // ------------------------------------------------------------------ player list (no native <select>)

  /** Case-insensitive "contains" on the display name. Empty text shows everyone. */
  function filterPlayers(players, text) {
    const wanted = String(text == null ? "" : text).trim().toLowerCase();
    if (!wanted) return players.slice();
    return players.filter((p) => p.name.toLowerCase().includes(wanted));
  }

  /** What the player list must show, from facts only. `loadStatus` is "loading", "ready" or
   *  "failed". Rows carry only the display name (never the id) and whether they are selected. */
  function playerListModel(players, filterText, loadStatus, selectedId) {
    if (loadStatus === "loading") return { rows: [], message: "Loading players…", showRetry: false };
    if (loadStatus === "failed") return { rows: [], message: "Could not load players", showRetry: true };
    if (!players.length) return { rows: [], message: "No players available", showRetry: false };
    const shown = filterPlayers(players, filterText);
    if (!shown.length) return { rows: [], message: "No player matches this search", showRetry: false };
    return {
      rows: shown.map((p) => ({ id: p.id, name: p.name, selected: p.id === selectedId })),
      message: null,
      showRetry: false,
    };
  }

  function selectedLabel(player) {
    return player ? `Selected: ${player.name}` : "No player selected yet";
  }

  /** Builds the rows with plain DOM calls (text only). Each row is a real <button>, so touch,
   *  keyboard (Tab, Enter, Space) and screen readers work without extra code. Only the
   *  display name is put in the page; the id stays in the closure. */
  function renderPlayerRows(doc, ui, model, onPick) {
    ui.list.replaceChildren();
    for (const row of model.rows) {
      const item = doc.createElement("li");
      const button = doc.createElement("button");
      button.setAttribute("type", "button");
      button.setAttribute("class", row.selected ? "player-row selected" : "player-row");
      button.setAttribute("aria-pressed", row.selected ? "true" : "false");
      button.textContent = row.name;
      button.addEventListener("click", () => onPick(row.id));
      item.append(button);
      ui.list.append(item);
    }
    ui.message.textContent = model.message || "";
    ui.message.hidden = !model.message;
    ui.retry.hidden = !model.showRetry;
  }

  const exported = {
    filterPlayers, playerListModel, selectedLabel, renderPlayerRows,
    NOT_RECORDED_REASONS, INVALID_REASONS_BY_STATION, CORRECTION_REASONS, STATE_WORDS, TIME_HELP,
    validateTimeText, friendlyMessage, newKey, createPendingStore, request, outcome, makeFlows, attemptBody,
    nextAttemptNumber, describeAttempt, describeQueued, stationRowModel, syncBannerModel,
  };

  // ==================================================================== page wiring
  exported.start = function start() {
    const $ = (id) => document.getElementById(id);
    const state = {
      me: null, players: [], player: null, assessmentId: null, tree: null, overview: null,
      stationId: null, seriesKey: "", confirmers: null, confirmersFailed: false, busy: new Set(),
      open: null, flash: null, playersStatus: "loading", playerFilter: "", mode: "valid-time",
      formNumber: null, discardAsked: null,
    };
    const store = createPendingStore(window.sessionStorage);
    const flows = makeFlows({ fetchImpl: (u, i) => window.fetch(u, i), store });
    const outbox = window.V1Outbox.createDefaultOutbox();
    const canWrite = () => state.me && (state.me.role === "admin" || state.me.role === "coach");
    const owner = () => (state.me ? state.me.user_id : null);

    function el(tag, attrs, ...children) {
      const node = document.createElement(tag);
      for (const [k, v] of Object.entries(attrs || {})) {
        if (v === false || v == null) continue;
        if (k === "text") node.textContent = v;
        else node.setAttribute(k, v === true ? "" : v);
      }
      for (const c of children) if (c) node.append(c);
      return node;
    }

    function say(message, kind, target) {
      const box = $(target || "notice");
      box.textContent = message || "";
      box.className = `msg ${kind || ""}`.trim();
      box.hidden = !message;
    }

    function today() {
      const d = new Date();
      const p = (n) => String(n).padStart(2, "0");
      return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
    }

    function setBusy(name, on) {
      if (on) state.busy.add(name); else state.busy.delete(name);
      for (const b of document.querySelectorAll(`[data-action="${name}"]`)) {
        b.disabled = on;
        b.setAttribute("aria-busy", on ? "true" : "false");
      }
    }
    // The page-level guard only stops double taps; the server's idempotency is the real protection.
    async function once(name, work) {
      if (state.busy.has(name)) return null;
      state.flash = null;
      setBusy(name, true);
      try { return await work(); } finally { setBusy(name, false); }
    }

    // ---------------------------------------------------------------- data
    async function load(path) {
      return request((u, i) => window.fetch(u, i), "GET", path);
    }

    // For reading data the generic "could not confirm save" wording would be wrong.
    function loadFailureMessage(r) {
      if (r.network || r.status === 0 || r.status >= 500) return "Could not reach the server. Check the connection, then press Retry.";
      return friendlyMessage(r.status, detailOf(r));
    }

    async function loadPlayers() {
      state.playersStatus = "loading";
      renderPlayers();
      try {
        const r = await load("/v1/player-picker");
        if (r.status !== 200 || !r.body || !Array.isArray(r.body.players)) {
          state.playersStatus = "failed";
          renderPlayers();
          say(loadFailureMessage(r), "error");
          return;
        }
        // The server sends only an id and a display name.
        state.players = r.body.players.map((p) => ({ id: p.player_id, name: p.display_name }));
        state.playersStatus = "ready";
        say("", "");
      } catch (error) {
        state.playersStatus = "failed";
      }
      renderPlayers();
    }

    // Large tappable rows instead of a native <select> (the iPhone wheel picker showed no options).
    function renderPlayers() {
      const model = playerListModel(state.players, state.playerFilter, state.playersStatus, state.player ? state.player.id : null);
      renderPlayerRows(document, { list: $("player-list"), message: $("player-status"), retry: $("player-retry") }, model, (id) => selectPlayer(id));
      $("selected-player").textContent = selectedLabel(state.player);
    }

    function focusPlayerList() {
      const target = $("player-list").querySelector("button.selected") || $("player-list").querySelector("button");
      if (target) target.focus();
      else $("player-filter").focus();
    }

    async function loadConfirmers() {
      if (state.confirmers || !canWrite()) return;
      const r = await load("/v1/eligible-confirmers");
      if (r.status !== 200 || !r.body || !Array.isArray(r.body.confirmers)) { state.confirmersFailed = true; state.confirmers = []; return; }
      // The server lists only active coaches and admins other than you.
      state.confirmers = r.body.confirmers.map((u) => ({ id: u.user_id, name: u.username }));
    }

    async function refreshAssessment() {
      if (!state.assessmentId) return;
      const [tree, overview] = await Promise.all([
        load(`/v1/assessments/${encodeURIComponent(state.assessmentId)}`),
        load(`/v1/assessments/${encodeURIComponent(state.assessmentId)}/stations`),
      ]);
      if (tree.status !== 200) { say(friendlyMessage(tree.status, detailOf(tree)), "error"); return; }
      if (overview.status !== 200) { say(friendlyMessage(overview.status, detailOf(overview)), "error"); return; }
      state.tree = tree.body;
      state.overview = overview.body;
      render();
    }

    async function selectPlayer(playerId) {
      const found = state.players.find((p) => p.id === playerId) || null;
      state.player = found; state.assessmentId = null; state.tree = null; state.overview = null; state.stationId = null;
      history.replaceState(null, "", location.pathname);
      renderPlayers();
      render();
      $("assessments-card").hidden = !found;
      if (!found) return;
      $("player-name").textContent = found.name;
      await refreshAssessmentList();
    }

    async function refreshAssessmentList() {
      const r = await load(`/v1/players/${encodeURIComponent(state.player.id)}/assessments`);
      const list = $("assessment-list");
      list.replaceChildren();
      const cont = $("continue-assessment");
      cont.hidden = true;
      if (r.status !== 200) { say(friendlyMessage(r.status, detailOf(r)), "error"); return; }
      const rows = r.body.assessments || [];
      if (!rows.length) list.append(el("li", { class: "muted", text: "No assessment yet for this player." }));
      const inProgress = rows.find((a) => a.status === "IN_PROGRESS") || null;
      if (inProgress && canWrite()) {
        cont.hidden = false;
        cont.dataset.assessment = inProgress.assessment_id;
        cont.textContent = "CONTINUE ASSESSMENT";
      }
      $("start-assessment").className = inProgress && canWrite() ? "ghost" : "primary";
      for (const a of rows) {
        const open = el("button", { type: "button", class: "ghost", text: "Open" });
        open.setAttribute("aria-label", `Open assessment from ${a.assessment_date}`);
        open.addEventListener("click", () => openAssessment(a.assessment_id));
        list.append(el("li", {}, el("span", { text: `${a.title || "Assessment"} · ${a.assessment_date} · ${a.status === "IN_PROGRESS" ? "In progress" : a.status}` }), open));
      }
    }

    async function openAssessment(id) {
      state.assessmentId = id;
      state.stationId = null;
      history.replaceState(null, "", `${location.pathname}?assessment=${encodeURIComponent(id)}`);
      await loadConfirmers();
      await refreshAssessment();
    }

    // ---------------------------------------------------------------- views
    function view() {
      if (state.tree && state.stationId) return "station";
      if (state.tree) return "overview";
      return "players";
    }

    function render() {
      const v = view();
      for (const section of document.querySelectorAll(".picker")) {
        section.hidden = section.id === "assessments-card" ? (!state.player || v !== "players") : v !== "players";
      }
      $("overview").hidden = v !== "overview";
      $("station").hidden = v !== "station";
      renderSyncBox();
      if (v === "overview") renderOverview();
      if (v === "station") renderStation();
    }

    function playerName() {
      const p = state.players.find((x) => x.id === (state.tree && state.tree.player_id));
      return p ? p.name : "Player";
    }

    function renderOverview() {
      const o = state.overview;
      $("overview-player").textContent = playerName();
      $("overview-assessment").textContent = o.assessment_id;
      $("overview-status").textContent = o.status === "IN_PROGRESS" ? "IN PROGRESS" : o.status;
      $("overview-note").textContent = o.completion_note || "";
      $("overview-note").hidden = !o.completion_note;
      const list = $("station-list");
      list.replaceChildren();
      for (const s of o.stations) {
        const m = stationRowModel(s);
        const row = el("li", { class: `station-row ${m.canOpen ? "open" : "soon"}`, "data-station": m.id });
        const head = el("div", { class: "station-head" },
          el("span", { class: "station-name", text: m.label }),
          el("span", { class: `chip state-${m.state.toLowerCase().replace(/_/g, "-")}`, text: m.stateText }));
        row.append(head);
        if (m.detail) row.append(el("p", { class: "muted station-detail", text: m.detail }));
        if (m.canOpen) {
          const open = el("button", { type: "button", class: "primary", text: "OPEN" });
          open.setAttribute("aria-label", `Open ${s.name}`);
          open.addEventListener("click", () => openStation(s.station_id));
          row.append(open);
        }
        list.append(row);
      }
    }

    function openStation(stationId) {
      const s = state.overview.stations.find((x) => x.station_id === stationId);
      if (!s || !s.enabled) return;
      state.stationId = stationId;
      state.seriesKey = s.series.length ? s.series[0].series_key : "";
      state.open = null; state.flash = null;
      closeForm();
      render();
      window.scrollTo(0, 0);
    }

    function currentStation() {
      return state.overview.stations.find((x) => x.station_id === state.stationId);
    }

    function trialsOfStation() {
      const run = ((state.tree && state.tree.station_runs) || []).find((r) => r.station_id === state.stationId);
      return run ? run.trials : [];
    }

    function queuedOfStation() {
      if (!owner() || !state.assessmentId) return [];
      return outbox.list(owner()).filter((e) => e.assessment_id === state.assessmentId
        && e.payload && e.payload.station_id === state.stationId);
    }

    function reasonLabel(list, code) {
      const found = list.find(([c]) => c === code);
      return found ? found[1] : "Reason recorded";
    }

    function invalidReasons() { return INVALID_REASONS_BY_STATION[state.stationId] || []; }

    function renderStation() {
      const station = currentStation();
      $("assessment-player").textContent = playerName();
      $("station-h").textContent = `${station.order}. ${station.name}`;
      $("station-description").textContent = station.description;
      $("write-actions").hidden = !canWrite();
      $("readonly-note").hidden = !!canWrite();
      // Turning-foot choice (Agility): one list of attempts per foot.
      const tabs = $("series-tabs");
      tabs.replaceChildren();
      tabs.hidden = station.series.length < 2;
      if (!tabs.hidden) {
        for (const s of station.series) {
          const b = el("button", { type: "button", class: s.series_key === state.seriesKey ? "seg selected" : "seg", text: s.label,
            "aria-pressed": s.series_key === state.seriesKey ? "true" : "false" });
          b.addEventListener("click", () => { state.seriesKey = s.series_key; closeForm(); renderStation(); });
          tabs.append(b);
        }
      }
      const series = station.series.find((s) => s.series_key === state.seriesKey) || station.series[0];
      $("series-progress").textContent = series
        ? `Valid attempts: ${series.valid_recorded} of ${series.required_valid}` +
          (series.over_attempt_limit ? ` · more than ${series.max_attempts} attempts have been used` : "")
        : "";
      const list = $("attempts");
      list.replaceChildren();
      const trials = trialsOfStation().filter((t) => (t.series_key || "") === state.seriesKey);
      const queued = queuedOfStation().filter((e) => (e.payload.series_key || "") === state.seriesKey);
      const items = [
        ...trials.map((t) => ({ number: t.attempt_number, trial: t })),
        ...queued.map((e) => ({ number: e.payload.attempt_number, entry: e })),
      ].sort((a, b) => a.number - b.number);
      if (!items.length) list.append(el("li", { class: "muted", text: "No attempts yet." }));
      for (const item of items) list.append(item.trial ? renderAttempt(item.trial) : renderQueuedAttempt(item.entry));
      $("add-attempt").disabled = !canWrite();
    }

    function renderQueuedAttempt(entry) {
      const d = describeQueued(entry);
      const card = el("li", { class: "attempt queued", "data-op": entry.op_id });
      card.append(el("h3", { text: `Attempt ${entry.payload.attempt_number}` }));
      card.append(el("p", { class: "time-line" }, el("span", { class: d.time.endsWith("sec") ? "time" : "time none", text: d.time })));
      card.append(el("p", { class: d.failed ? "msg warn" : "msg saving", role: "status", text: d.status }));
      card.append(el("p", { class: "facts" },
        el("span", { class: `chip ${entry.payload.validity === "VALID" ? "ok" : "warn"}`, text: d.validity }),
        entry.payload.invalid_reason ? el("span", { class: "chip", text: reasonLabel(invalidReasons(), entry.payload.invalid_reason) }) : null,
        entry.payload.entry_status === "NOT_RECORDED"
          ? el("span", { class: "chip", text: `Stopwatch problem: ${reasonLabel(NOT_RECORDED_REASONS, entry.payload.not_recorded_reason)}` }) : null));
      if (outbox.isStale(entry)) card.append(el("p", { class: "msg warn", text: "This attempt has been waiting for a long time. Open this page with a connection to send it." }));
      if (entry.state === "SYNC_FAILED") {
        const retry = el("button", { type: "button", class: "ghost small", text: "TRY AGAIN" });
        retry.addEventListener("click", () => { outbox.retry(entry.op_id, owner()); syncNow(); renderStation(); });
        const ask = state.discardAsked === entry.op_id;
        const discard = el("button", { type: "button", class: "ghost small", text: ask ? "CONFIRM: DISCARD THIS ATTEMPT" : "DISCARD" });
        discard.addEventListener("click", () => {
          if (!ask) { state.discardAsked = entry.op_id; renderStation(); return; }
          outbox.discard(entry.op_id, owner()); state.discardAsked = null; render();
        });
        card.append(el("div", { class: "row" }, retry, discard));
      }
      return card;
    }

    function renderAttempt(trial) {
      const d = describeAttempt(trial);
      const m = d.measurement;
      const card = el("li", { class: "attempt", "data-trial": trial.trial_id });
      card.append(el("h3", { text: `Attempt ${trial.attempt_number}` }));
      const hasTime = !!(m && m.entry_status === "RECORDED");
      card.append(el("p", { class: "time-line" }, el("span", { class: hasTime ? "time" : "time none", text: d.time })));
      if (state.flash && state.flash.trialId === trial.trial_id) {
        card.append(el("p", { class: "msg saved", role: "status", text: state.flash.text }));
      }
      card.append(el("p", { class: "facts" },
        el("span", { class: `chip ${trial.validity === "VALID" ? "ok" : "warn"}`, text: d.validity }),
        trial.invalid_reason ? el("span", { class: "chip", text: reasonLabel(invalidReasons(), trial.invalid_reason) }) : null,
        m && m.entry_status === "NOT_RECORDED"
          ? el("span", { class: "chip", text: `Stopwatch problem: ${reasonLabel(NOT_RECORDED_REASONS, m.not_recorded_reason)}` }) : null));
      if (m && m.entry_status === "NOT_RECORDED") {
        card.append(el("p", { class: "muted", text: "A stopwatch problem is not a failed attempt by the player. The attempt can be repeated." }));
      }
      if (m && m.corrections.length) {
        card.append(el("p", { class: "facts" },
          el("span", { text: `Original: ${m.original_value} sec` }), document.createTextNode(" · "),
          el("span", { text: `Current: ${m.effective_value} sec` })));
        const hist = el("ol", { class: "history", "aria-label": `Correction history for attempt ${trial.attempt_number}` });
        hist.append(el("li", { text: `Entered: ${m.original_value}` }));
        for (const c of m.corrections) {
          hist.append(el("li", { text: `${c.original_value} → ${c.corrected_value} (${reasonLabel(CORRECTION_REASONS, c.reason_code)})` }));
        }
        card.append(el("details", {}, el("summary", { text: "Correction history" }), hist));
      }
      if (canWrite() && !m && trial.validity === "VALID") {
        const add = el("button", { type: "button", class: "ghost", text: "ENTER THE TIME FOR THIS ATTEMPT" });
        add.addEventListener("click", () => openForm({ number: trial.attempt_number, mode: "valid-time" }));
        card.append(add);
      }
      if (canWrite() && m && m.entry_status === "RECORDED") {
        const btn = el("button", { type: "button", class: "ghost", text: "CORRECT ENTRY" });
        btn.setAttribute("aria-label", `Correct entry for attempt ${trial.attempt_number}`);
        btn.addEventListener("click", () => { state.open = trial.trial_id; renderStation(); });
        if (state.open !== trial.trial_id) card.append(btn);
        else card.append(renderCorrectionForm(trial, m));
      }
      return card;
    }

    function field(id, labelText, control, helpId) {
      return el("div", { class: "field" }, el("label", { for: id, text: labelText }), control, el("p", { id: helpId, class: "msg", hidden: true, role: "alert" }));
    }

    // ---------------------------------------------------------------- the attempt form
    const form = () => $("attempt-form");

    function closeForm() {
      state.formNumber = null;
      if ($("attempt-form")) form().hidden = true;
    }

    function setMode(mode) {
      state.mode = mode;
      $("valid-panel").hidden = mode === "invalid";
      $("nr-panel").hidden = mode !== "valid-nr";
      $("time-field").hidden = mode === "valid-nr";
      $("invalid-panel").hidden = mode !== "invalid";
      $("validity-valid").setAttribute("aria-pressed", mode === "invalid" ? "false" : "true");
      $("validity-invalid").setAttribute("aria-pressed", mode === "invalid" ? "true" : "false");
      $("toggle-nr").textContent = mode === "valid-nr" ? "ENTER THE TIME INSTEAD" : "TIMER PROBLEM / TIME NOT RECORDED";
      $("save-attempt").textContent = mode === "invalid" ? "SAVE NOT VALID ATTEMPT" : mode === "valid-nr" ? "SAVE AS NOT RECORDED" : "SAVE ATTEMPT";
    }

    function clearFormMessages() {
      for (const id of ["time-input-msg", "nr-reason-msg", "invalid-reason-msg"]) { $(id).hidden = true; $(id).textContent = ""; }
      for (const id of ["time-input", "nr-reason", "invalid-reason"]) $(id).setAttribute("aria-invalid", "false");
      $("attempt-status").hidden = true;
    }

    function openForm({ number, mode }) {
      const station = currentStation();
      const queuedPayloads = queuedOfStation().map((e) => e.payload);
      state.formNumber = number || nextAttemptNumber(trialsOfStation(), queuedPayloads, state.seriesKey);
      $("time-input").value = "";
      $("nr-reason").value = "";
      $("invalid-reason").value = "";
      clearFormMessages();
      const label = station.series.length > 1 ? ` (${(station.series.find((s) => s.series_key === state.seriesKey) || {}).label || ""})` : "";
      $("form-title").textContent = `Attempt ${state.formNumber}${label}`;
      setMode(mode || "valid-time");
      form().hidden = false;
      $("time-input").focus();
    }

    function fieldError(id, message) {
      $(`${id}-msg`).textContent = message; $(`${id}-msg`).hidden = false; $(`${id}-msg`).className = "msg error";
      $(id).setAttribute("aria-invalid", "true"); $(id).focus();
    }

    /** Validate the form and build the attempt, or show the first problem and return null. */
    function readForm() {
      const base = { station_id: state.stationId, series_key: state.seriesKey, attempt_number: state.formNumber };
      if (state.mode === "invalid") {
        if (!$("invalid-reason").value) { fieldError("invalid-reason", "Choose the rule that was broken."); return null; }
        return { ...base, validity: "INVALID", invalid_reason: $("invalid-reason").value };
      }
      if (state.mode === "valid-nr") {
        if (!NOT_RECORDED_REASONS.some(([c]) => c === $("nr-reason").value)) { fieldError("nr-reason", "Choose what went wrong with the stopwatch."); return null; }
        return { ...base, validity: "VALID", entry_status: "NOT_RECORDED", not_recorded_reason: $("nr-reason").value };
      }
      const checked = validateTimeText($("time-input").value);
      if (!checked.ok) { fieldError("time-input", checked.message); return null; }
      // The value is kept exactly as typed, as a string.
      return { ...base, validity: "VALID", entry_status: "RECORDED", displayed_value: checked.value };
    }

    async function syncNow() {
      if (!owner()) return null;
      const summary = await outbox.flush({
        owner: owner(),
        send: (entry) => flows.saveAttempt({ assessmentId: entry.assessment_id, entry }),
      });
      if (summary.synced && state.assessmentId) await refreshAssessment();
      else render();
      return summary;
    }

    async function submitAttempt(event) {
      event.preventDefault();
      await once("save-attempt", async () => {
        clearFormMessages();
        const payload = readForm();
        if (!payload) return;
        const status = $("attempt-status");
        status.hidden = false; status.className = "msg saving"; status.textContent = "Saving…";
        let entry;
        try {
          entry = outbox.enqueue({ owner: owner(), assessmentId: state.assessmentId, payload });
        } catch (error) {
          // This browser would not keep a copy. Send it straight to the server, and say so.
          const direct = await flows.saveAttempt({ assessmentId: state.assessmentId, entry: { op_id: newKey(), payload } });
          if (direct.state === "SAVED") { status.className = "msg saved"; status.textContent = "SAVED"; closeForm(); await refreshAssessment(); return; }
          status.className = "msg warn";
          status.textContent = `${direct.message} This device could not keep a local copy, so enter the attempt again if it did not save.`;
          return;
        }
        // It is on the device now. Show it, then send it.
        closeForm();
        render();
        await syncNow();
        const after = outbox.list(owner(), true).find((e) => e.op_id === entry.op_id);
        if (after && after.state === "SYNCED") {
          say("", "", "attempt-msg");
          state.flash = { trialId: after.result && after.result.trial_id, text: payload.validity === "INVALID" ? "SAVED: not valid attempt"
            : payload.entry_status === "NOT_RECORDED" ? "SAVED: time not recorded" : `SAVED ${payload.displayed_value} sec` };
          await refreshAssessment();
          return;
        }
        if (after && after.state === "SYNC_FAILED") {
          say(`${describeQueued(after).status}`, "error", "attempt-msg");
        } else {
          say("Saved on this device. It will be sent to the server as soon as the connection is back.", "warn", "attempt-msg");
        }
      });
    }

    // ---------------------------------------------------------------- the sync banner
    function renderSyncBox() {
      const box = $("sync-box");
      if (!owner()) { box.hidden = true; return; }
      const model = syncBannerModel(outbox.counts(owner()));
      box.hidden = !model.visible;
      $("sync-text").textContent = model.text;
      $("sync-text").className = `msg ${model.tone}`.trim();
      $("sync-now").hidden = !model.showSync;
    }

    // ---------------------------------------------------------------- corrections (online only)
    function renderCorrectionForm(trial, m) {
      const base = `corr-${trial.trial_id}`;
      const f = el("form", { class: "correction", novalidate: true, "aria-label": `Correct entry for attempt ${trial.attempt_number}` });
      f.append(el("p", { class: "facts", text: `Current saved time: ${m.effective_value} sec` }));
      const value = el("input", { id: `${base}-value`, type: "text", inputmode: "decimal", autocomplete: "off", class: "time-input", "aria-describedby": `${base}-value-msg` });
      f.append(field(`${base}-value`, "Corrected time (seconds)", value, `${base}-value-msg`));
      const reason = el("select", { id: `${base}-reason`, "aria-describedby": `${base}-reason-msg` });
      reason.append(el("option", { value: "", text: "Select a reason" }));
      for (const [c, t] of CORRECTION_REASONS) reason.append(el("option", { value: c, text: t }));
      f.append(field(`${base}-reason`, "Reason", reason, `${base}-reason-msg`));
      const reasonText = el("input", { id: `${base}-reason-text`, type: "text", maxlength: "200", autocomplete: "off", "aria-describedby": `${base}-reason-text-msg` });
      const textWrap = field(`${base}-reason-text`, "Write the reason", reasonText, `${base}-reason-text-msg`);
      textWrap.hidden = true;
      f.append(textWrap);
      reason.addEventListener("change", () => { textWrap.hidden = reason.value !== "OTHER"; });

      const confirmer = el("select", { id: `${base}-confirmer`, "aria-describedby": `${base}-confirmer-msg` });
      confirmer.append(el("option", { value: "", text: "Select the second person" }));
      for (const c of state.confirmers || []) confirmer.append(el("option", { value: c.id, text: c.name }));
      f.append(field(`${base}-confirmer`, "Second person (a different coach or admin)", confirmer, `${base}-confirmer-msg`));
      f.append(el("p", { class: "muted", text: "The person you choose is recorded with this correction. They are not asked to approve it." }));
      const unavailable = !(state.confirmers && state.confirmers.length);
      if (unavailable) {
        f.append(el("p", { class: "msg warn", role: "alert", text: state.confirmersFailed
          ? "The list of people who can be named as the second person could not be loaded, so corrections are unavailable on this screen."
          : "No other active coach or admin is available to be named as the second person, so a correction cannot be made right now." }));
      }
      const status = el("p", { id: `${base}-status`, class: "msg", role: "status", hidden: true });
      const submit = el("button", { type: "submit", class: "primary", text: "SUBMIT CORRECTION", "data-action": `correct-${trial.trial_id}` });
      submit.disabled = unavailable;
      const cancel = el("button", { type: "button", class: "ghost", text: "Cancel" });
      cancel.addEventListener("click", () => { state.open = null; renderStation(); });
      f.append(el("div", { class: "row" }, submit, cancel), status);

      const boxes = { value: `${base}-value-msg`, reason: `${base}-reason-msg`, "reason-text": `${base}-reason-text-msg`, confirmer: `${base}-confirmer-msg` };
      const controls = { value, reason, "reason-text": reasonText, confirmer };
      function clearErrors() { for (const id of Object.values(boxes)) { const b = f.querySelector(`#${CSS.escape(id)}`); b.hidden = true; b.textContent = ""; } }
      function correctionError(name, message) {
        const b = f.querySelector(`#${CSS.escape(boxes[name] || boxes.value)}`);
        b.textContent = message; b.hidden = false; b.className = "msg error";
        const c = controls[name] || value; c.setAttribute("aria-invalid", "true"); c.focus();
      }
      f.addEventListener("submit", async (event) => {
        event.preventDefault();
        await once(`correct-${trial.trial_id}`, async () => {
          clearErrors(); status.hidden = false; status.className = "msg saving"; status.textContent = "Saving…";
          const result = await flows.correct({
            measurementId: m.measurement_id, text: value.value, expectedEffective: m.effective_value,
            reasonCode: reason.value, reasonText: reasonText.value, confirmerId: confirmer.value, selfId: state.me.user_id,
          });
          if (result.state === "INVALID_INPUT") { status.hidden = true; correctionError(result.field || "value", result.message); return; }
          if (result.state === "SAVED") {
            status.className = "msg saved";
            status.textContent = `SAVED: ${result.measurement.original_value} → ${result.measurement.effective_value} sec`;
            state.flash = { trialId: result.measurement.trial_id, text: status.textContent };
            state.open = null; await refreshAssessment(); return;
          }
          if (result.state === "UNCERTAIN") { status.className = "msg warn"; status.textContent = `${result.message} Press the button again to retry safely.`; return; }
          status.hidden = true; correctionError("value", `${result.message} (code ${result.status})`);
        });
      });
      return f;
    }

    // ---------------------------------------------------------------- page actions
    function leaveAssessment() {
      state.tree = null; state.overview = null; state.assessmentId = null; state.stationId = null;
      state.open = null; state.flash = null;
      closeForm();
      history.replaceState(null, "", location.pathname);
      render();
      if (state.player) refreshAssessmentList();
      renderPlayers();
    }

    function wireStaticControls() {
      $("change-player").addEventListener("click", () => { leaveAssessment(); focusPlayerList(); });
      $("change-player-overview").addEventListener("click", () => { leaveAssessment(); focusPlayerList(); });
      $("back-to-overview").addEventListener("click", () => { state.stationId = null; state.open = null; closeForm(); render(); refreshAssessment(); });
      $("player-filter").addEventListener("input", (e) => { state.playerFilter = e.target.value; renderPlayers(); });
      $("player-retry").addEventListener("click", () => once("player-retry", loadPlayers));
      $("continue-assessment").addEventListener("click", () => {
        const id = $("continue-assessment").dataset.assessment;
        if (id) openAssessment(id);
      });
      $("start-assessment").addEventListener("click", async () => {
        if (!state.player) { say("Select a player first.", "error", "assessment-msg"); focusPlayerList(); return; }
        await once("start-assessment", async () => {
          say("Starting…", "saving", "assessment-msg");
          const result = await flows.createAssessment({ playerId: state.player.id, date: today(), title: "Assessment" });
          if (result.state === "SAVED") { say("", "", "assessment-msg"); await refreshAssessmentList(); await openAssessment(result.assessment.assessment_id); return; }
          if (result.state === "UNCERTAIN") { say(`${result.message} Press START again to retry safely.`, "warn", "assessment-msg"); return; }
          say(`${result.message} (code ${result.status})`, "error", "assessment-msg");
        });
      });
      // The attempt form (one form, reused for whichever attempt is being entered).
      const reasons = $("invalid-reason");
      const nrReasons = $("nr-reason");
      for (const [c, t] of NOT_RECORDED_REASONS) nrReasons.append(el("option", { value: c, text: t }));
      $("add-attempt").setAttribute("data-action", "add-attempt");
      $("add-attempt").addEventListener("click", () => {
        // Fill the reasons for the station on screen.
        reasons.replaceChildren(el("option", { value: "", text: "Select a reason" }));
        for (const [c, t] of invalidReasons()) reasons.append(el("option", { value: c, text: t }));
        openForm({ mode: "valid-time" });
      });
      $("validity-valid").addEventListener("click", () => { setMode("valid-time"); $("time-input").focus(); });
      $("validity-invalid").addEventListener("click", () => {
        reasons.replaceChildren(el("option", { value: "", text: "Select a reason" }));
        for (const [c, t] of invalidReasons()) reasons.append(el("option", { value: c, text: t }));
        setMode("invalid"); reasons.focus();
      });
      $("toggle-nr").addEventListener("click", () => { setMode(state.mode === "valid-nr" ? "valid-time" : "valid-nr"); });
      $("cancel-attempt").addEventListener("click", () => { closeForm(); $("add-attempt").focus(); });
      $("save-attempt").setAttribute("data-action", "save-attempt");
      form().addEventListener("submit", submitAttempt);
      const timeInput = $("time-input");
      timeInput.addEventListener("focus", () => { setTimeout(() => form().scrollIntoView({ block: "center", behavior: "smooth" }), 300); });
      timeInput.addEventListener("input", () => {
        const v = timeInput.value.trim() === "" ? { ok: true } : validateTimeText(timeInput.value);
        $("time-input-msg").textContent = v.ok ? "" : v.message; $("time-input-msg").hidden = v.ok;
        timeInput.setAttribute("aria-invalid", v.ok ? "false" : "true");
      });
      $("sync-now").addEventListener("click", () => once("sync-now", syncNow));
      window.addEventListener("online", () => { syncNow(); });
    }

    async function init() {
      wireStaticControls();
      const me = await load("/auth/me");
      if (me.status !== 200) {
        state.playersStatus = "failed";
        renderPlayers();
        say(loadFailureMessage(me), "error");
        return;
      }
      state.me = me.body.user || me.body;
      $("who").textContent = `${state.me.username} · ${state.me.role}`;
      outbox.load();
      renderSyncBox();
      await loadPlayers();
      const wanted = new URLSearchParams(location.search).get("assessment");
      if (wanted) {
        const r = await load(`/v1/assessments/${encodeURIComponent(wanted)}`);
        if (r.status === 200) {
          const p = state.players.find((x) => x.id === r.body.player_id);
          if (p) { state.player = p; renderPlayers(); $("player-name").textContent = p.name; $("assessments-card").hidden = false; await refreshAssessmentList(); }
          await openAssessment(wanted);
        } else say(friendlyMessage(r.status, detailOf(r)), "error");
      }
      // Anything saved on this device earlier is sent now, and again whenever the connection returns.
      await syncNow();
      setInterval(() => { if (outbox.counts(owner()).LOCAL_PENDING) syncNow(); }, SYNC_INTERVAL_MS);
      document.body.dataset.ready = "true";
    }
    init().catch(() => {
      state.playersStatus = "failed";
      renderPlayers();
      say("This screen could not start. Reload the page and try again.", "error");
    });
  };

  return exported;
});
