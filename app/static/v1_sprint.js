/* Kemet V1 - S1 20 m sprint manual time entry (coach web page).
 *
 * Two parts in one file:
 *   1. Logic (no DOM): input validation, plain-language errors, idempotency keys, and the
 *      save/retry/correct flows. Exported for node (`module.exports`) so it can be tested
 *      without a browser (tests/test_v1_sprint_ui.py runs them with `node --test` when node
 *      is installed).
 *   2. Page wiring (only when `document` exists): builds the screen with textContent, never
 *      innerHTML, so assessment content is never interpreted as markup.
 *
 * Rules kept here on purpose:
 *   - Official times are STRINGS. Nothing here parses a time into a JS number.
 *   - The server response is the only source of truth: "Saved" appears after a 200/201.
 *   - No plausibility thresholds, scores, benchmarks, age bands or AI anywhere.
 *   - Nothing about an assessment is kept in the browser except in-flight idempotency keys
 *     (sessionStorage, key only, removed as soon as the server answers definitively).
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.V1Sprint = api;
  if (typeof document !== "undefined") api.start();
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  // The metric behind "S1 - 20 m sprint time". The coach never sees this id.
  const SPRINT_METRIC = "PHYS-SPR-020M-MAN-001";
  const STATION = "S1";
  const REQUEST_TIMEOUT_MS = 15000;

  // Backend-supported lists (kept equal to app/v1/definitions.py by a test).
  const NOT_RECORDED_REASONS = [
    ["TIMER_FAILURE", "Stopwatch failed or stopped working"],
    ["TIMER_MISSED_STOP", "Timer missed the stop"],
    ["TIMER_RESET_EARLY", "Stopwatch was reset before the time was written down"],
    ["TIMER_WRONG_EVENT", "Timer stopped on the wrong moment"],
  ];
  const INVALID_REASONS = [
    ["FALSE_START", "False start (moved before the signal)"],
    ["LANE_VIOLATION", "Left the lane"],
    ["STOPPED_EARLY", "Stopped before the line"],
    ["FALL_OR_TRIP", "Fell or tripped"],
    ["INTERFERENCE", "Interference"],
  ];
  const CORRECTION_REASONS = [
    ["DECIMAL_SLIP", "Decimal point in the wrong place"],
    ["TYPING_ERROR", "Typing error"],
    ["MISREAD_DISPLAY", "Misread the stopwatch display"],
    ["WRONG_TRIAL", "Entered on the wrong attempt"],
    ["OTHER", "Other (write the reason)"],
  ];

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
      return "This attempt already has a different saved entry. Refresh before continuing.";
    }
    if (status === 422) {
      if (context === "time") return TIME_HELP;
      if (context === "correction") return "Check the corrected time, the reason and the person confirming.";
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

    async function ensureStationRun(assessmentId) {
      const result = await request(fetchImpl, "POST", `/v1/assessments/${assessmentId}/station-runs`, { station_id: STATION });
      return outcome(result, "station", (b) => ({ run: b }));
    }

    async function addAttempt({ runId, attemptNumber, invalidReason }) {
      const body = { attempt_number: attemptNumber, validity: invalidReason ? "INVALID" : "VALID" };
      if (invalidReason) body.invalid_reason = invalidReason;
      const result = await request(fetchImpl, "POST", `/v1/station-runs/${runId}/trials`, body);
      return outcome(result, "attempt", (b) => ({ trial: b }));
    }

    async function saveTime({ trialId, text }) {
      const checked = validateTimeText(text);
      if (!checked.ok) return { state: "INVALID_INPUT", message: checked.message, code: checked.code };
      const scope = `mm:${trialId}`;
      const key = store.getKey(scope);
      // The value is sent exactly as typed, as a string.
      const result = await request(fetchImpl, "POST", `/v1/trials/${trialId}/manual-measurements`, {
        metric_id: SPRINT_METRIC, displayed_value: checked.value, client_entry_id: key,
      });
      const done = outcome(result, "time", (b) => ({ measurement: b }));
      if (done.state !== "UNCERTAIN") store.clear(scope);
      return done;
    }

    async function saveNotRecorded({ trialId, reason }) {
      if (!NOT_RECORDED_REASONS.some(([code]) => code === reason)) {
        return { state: "INVALID_INPUT", message: "Choose what went wrong with the stopwatch." };
      }
      const scope = `nr:${trialId}`;
      const key = store.getKey(scope);
      const result = await request(fetchImpl, "POST", `/v1/trials/${trialId}/manual-measurements`, {
        metric_id: SPRINT_METRIC, entry_status: "NOT_RECORDED", not_recorded_reason: reason, client_entry_id: key,
      });
      const done = outcome(result, "time", (b) => ({ measurement: b }));
      if (done.state !== "UNCERTAIN") store.clear(scope);
      return done;
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
      if (!confirmerId) return { state: "INVALID_INPUT", message: "Choose the second person who confirms this correction.", field: "confirmer" };
      if (confirmerId === selfId) return { state: "INVALID_INPUT", message: "The person confirming must be someone other than you.", field: "confirmer" };
      const body = {
        corrected_value: checked.value, expected_effective_value: expectedEffective,
        reason_code: reasonCode, confirmed_by_user_id: confirmerId,
      };
      if (reasonCode === "OTHER") body.reason_text = String(reasonText).trim();
      const result = await request(fetchImpl, "POST", `/v1/manual-measurements/${measurementId}/corrections`, body);
      return outcome(result, "correction", (b) => ({ measurement: b.measurement, correction: b.correction }));
    }

    return { createAssessment, ensureStationRun, addAttempt, saveTime, saveNotRecorded, correct };
  }

  function nextAttemptNumber(trials) {
    return (trials || []).reduce((max, t) => Math.max(max, t.attempt_number), 0) + 1;
  }

  /** Words for one attempt row, from server facts only. Trial validity and the time
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

  const exported = {
    SPRINT_METRIC, NOT_RECORDED_REASONS, INVALID_REASONS, CORRECTION_REASONS, TIME_HELP,
    validateTimeText, friendlyMessage, newKey, createPendingStore, request, outcome, makeFlows,
    nextAttemptNumber, describeAttempt,
  };

  // ==================================================================== page wiring
  exported.start = function start() {
    const $ = (id) => document.getElementById(id);
    const state = {
      me: null, players: [], player: null, assessmentId: null, tree: null, runId: null,
      confirmers: null, confirmersFailed: false, busy: new Set(), open: null, flash: null,
    };
    const store = createPendingStore(window.sessionStorage);
    const flows = makeFlows({ fetchImpl: (u, i) => window.fetch(u, i), store });
    const canWrite = () => state.me && (state.me.role === "admin" || state.me.role === "coach");

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
      const r = await request((u, i) => window.fetch(u, i), "GET", path);
      return r;
    }

    async function loadPlayers() {
      const r = await load("/players");
      if (r.status !== 200 || !Array.isArray(r.body)) {
        say(friendlyMessage(r.status, detailOf(r)), "error");
        return;
      }
      // Keep only what the screen needs; the rest of each profile is dropped here.
      state.players = r.body.map((p) => ({ id: p.player_id, name: `${p.first_name_en || ""} ${p.last_name_en || ""}`.trim() }));
      state.players.sort((a, b) => a.name.localeCompare(b.name));
      renderPlayerOptions("");
    }

    function renderPlayerOptions(filter) {
      const select = $("player-select");
      select.replaceChildren(el("option", { value: "", text: "Select a player" }));
      const f = filter.trim().toLowerCase();
      for (const p of state.players) {
        if (f && !p.name.toLowerCase().includes(f)) continue;
        select.append(el("option", { value: p.id, text: p.name }));
      }
      if (state.player) select.value = state.player.id;
    }

    async function loadConfirmers() {
      if (state.confirmers || !canWrite()) return;
      const r = await load("/messages/recipients");
      if (r.status !== 200 || !Array.isArray(r.body)) { state.confirmersFailed = true; state.confirmers = []; return; }
      // Only active coaches and admins can confirm (the server enforces the same rule).
      state.confirmers = r.body
        .filter((u) => (u.role === "coach" || u.role === "admin") && u.user_id !== state.me.user_id)
        .map((u) => ({ id: u.user_id, name: u.username }));
    }

    async function refreshTree() {
      if (!state.assessmentId) return;
      const r = await load(`/v1/assessments/${state.assessmentId}`);
      if (r.status !== 200) { say(friendlyMessage(r.status, detailOf(r)), "error"); return; }
      state.tree = r.body;
      const run = (r.body.station_runs || []).find((x) => x.station_id === STATION);
      state.runId = run ? run.station_run_id : null;
      renderAssessment();
    }

    async function selectPlayer(playerId) {
      const found = state.players.find((p) => p.id === playerId) || null;
      state.player = found; state.assessmentId = null; state.tree = null; state.runId = null;
      history.replaceState(null, "", location.pathname);
      renderAssessment();
      $("assessments-card").hidden = !found;
      if (!found) return;
      $("player-name").textContent = found.name;
      await refreshAssessmentList();
    }

    async function refreshAssessmentList() {
      const r = await load(`/v1/players/${state.player.id}/assessments`);
      const list = $("assessment-list");
      list.replaceChildren();
      if (r.status !== 200) { say(friendlyMessage(r.status, detailOf(r)), "error"); return; }
      const rows = r.body.assessments || [];
      if (!rows.length) list.append(el("li", { class: "muted", text: "No V1 assessment yet for this player." }));
      for (const a of rows) {
        const open = el("button", { type: "button", class: "ghost", text: "Open" });
        open.setAttribute("aria-label", `Open assessment from ${a.assessment_date}`);
        open.addEventListener("click", () => openAssessment(a.assessment_id));
        list.append(el("li", {}, el("span", { text: `${a.title || "V1 assessment"} · ${a.assessment_date} · ${a.status === "IN_PROGRESS" ? "In progress" : a.status}` }), open));
      }
    }

    async function openAssessment(id) {
      state.assessmentId = id;
      history.replaceState(null, "", `${location.pathname}?assessment=${encodeURIComponent(id)}`);
      await loadConfirmers();
      if (canWrite()) {
        const run = await flows.ensureStationRun(id);
        if (run.state !== "SAVED") { say(run.message, "error"); }
      }
      await refreshTree();
    }

    // ---------------------------------------------------------------- render
    function renderAssessment() {
      const panel = $("sprint");
      panel.hidden = !state.tree;
      // While an assessment is open the player pickers step aside, so the entry area is on screen.
      for (const section of document.querySelectorAll(".picker")) {
        section.hidden = section.id === "assessments-card" ? (!state.player || !!state.tree) : !!state.tree;
      }
      if (!state.tree) return;
      const player = state.players.find((p) => p.id === state.tree.player_id);
      $("assessment-player").textContent = player ? player.name : "Player";
      $("write-actions").hidden = !canWrite();
      $("readonly-note").hidden = canWrite();
      const list = $("attempts");
      list.replaceChildren();
      const run = (state.tree.station_runs || []).find((x) => x.station_id === STATION);
      const trials = run ? run.trials : [];
      if (!trials.length) list.append(el("li", { class: "muted", text: "No attempts yet." }));
      for (const t of trials) list.append(renderAttempt(t));
      $("add-attempt").disabled = !canWrite() || !state.runId;
      $("add-invalid-attempt").disabled = !canWrite() || !state.runId;
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
        trial.invalid_reason ? el("span", { class: "chip", text: reasonLabel(INVALID_REASONS, trial.invalid_reason) }) : null,
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
      if (canWrite() && !m && trial.validity === "VALID") card.append(renderEntryForm(trial));
      if (canWrite() && m && m.entry_status === "RECORDED") {
        const btn = el("button", { type: "button", class: "ghost", text: "CORRECT ENTRY" });
        btn.setAttribute("aria-label", `Correct entry for attempt ${trial.attempt_number}`);
        btn.addEventListener("click", () => { state.open = trial.trial_id; renderAssessment(); });
        if (state.open !== trial.trial_id) card.append(btn);
        else card.append(renderCorrectionForm(trial, m));
      }
      return card;
    }

    function reasonLabel(list, code) {
      const found = list.find(([c]) => c === code);
      return found ? found[1] : "Reason recorded";
    }

    function field(id, labelText, control, helpId) {
      return el("div", { class: "field" }, el("label", { for: id, text: labelText }), control, el("p", { id: helpId, class: "msg", hidden: true, role: "alert" }));
    }

    function renderEntryForm(trial) {
      const id = `time-${trial.trial_id}`;
      const input = el("input", {
        id, type: "text", inputmode: "decimal", autocomplete: "off", spellcheck: "false",
        class: "time-input", "aria-describedby": `${id}-msg`, "data-trial": trial.trial_id,
      });
      const form = el("form", { class: "entry", novalidate: true });
      form.append(field(id, "STOPWATCH TIME (seconds)", input, `${id}-msg`));
      const save = el("button", { type: "submit", class: "primary big", text: "SAVE TIME", "data-action": `save-${trial.trial_id}` });
      const problem = el("button", { type: "button", class: "ghost", text: "TIMER PROBLEM / TIME NOT RECORDED", "data-action": `nr-${trial.trial_id}` });
      form.append(el("div", { class: "row" }, save, problem));
      const status = el("p", { id: `${id}-status`, class: "msg", role: "status", hidden: true });
      form.append(status);
      const nrBox = el("div", { class: "nr", hidden: true });
      const sel = el("select", { id: `nr-${trial.trial_id}` });
      sel.append(el("option", { value: "", text: "What went wrong?" }));
      for (const [c, t] of NOT_RECORDED_REASONS) sel.append(el("option", { value: c, text: t }));
      nrBox.append(el("p", { class: "muted", text: "No number is saved. The player did not fail the attempt; it can be repeated." }));
      nrBox.append(field(`nr-${trial.trial_id}`, "Reason", sel, `nr-${trial.trial_id}-msg`));
      const confirm = el("button", { type: "button", class: "primary", text: "SAVE AS NOT RECORDED", "data-action": `nrsave-${trial.trial_id}` });
      nrBox.append(confirm);
      form.append(nrBox);

      const showError = (box, message) => { box.textContent = message; box.hidden = !message; };
      const msg = form.querySelector(`#${CSS.escape(id)}-msg`);
      const nrMsg = nrBox.querySelector(`#${CSS.escape(`nr-${trial.trial_id}`)}-msg`);

      input.addEventListener("input", () => {
        const v = input.value.trim() === "" ? { ok: true } : validateTimeText(input.value);
        showError(msg, v.ok ? "" : v.message);
        input.setAttribute("aria-invalid", v.ok ? "false" : "true");
      });
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        await once(`save-${trial.trial_id}`, async () => {
          showError(msg, ""); status.hidden = false; status.className = "msg saving"; status.textContent = "Saving…";
          const result = await flows.saveTime({ trialId: trial.trial_id, text: input.value });
          await afterSave(result, { input, msg, status, retryLabel: save });
        });
      });
      problem.addEventListener("click", () => { nrBox.hidden = !nrBox.hidden; if (!nrBox.hidden) sel.focus(); });
      confirm.addEventListener("click", async () => {
        await once(`nrsave-${trial.trial_id}`, async () => {
          showError(nrMsg, ""); status.hidden = false; status.className = "msg saving"; status.textContent = "Saving…";
          const result = await flows.saveNotRecorded({ trialId: trial.trial_id, reason: sel.value });
          await afterSave(result, { input: sel, msg: nrMsg, status, retryLabel: confirm });
        });
      });
      return form;
    }

    async function afterSave(result, ui) {
      if (result.state === "INVALID_INPUT") {
        ui.status.hidden = true; ui.msg.textContent = result.message; ui.msg.hidden = false; ui.msg.className = "msg error";
        ui.input.setAttribute("aria-invalid", "true"); ui.input.focus();
        return;
      }
      if (result.state === "SAVED") {
        const m = result.measurement;
        ui.status.className = "msg saved";
        ui.status.textContent = m.entry_status === "NOT_RECORDED" ? "SAVED: time not recorded" : `SAVED ${m.displayed_value} sec`;
        state.flash = { trialId: m.trial_id, text: ui.status.textContent };
        await refreshTree();
        return;
      }
      if (result.state === "UNCERTAIN") {
        // Keep the typed value; the retry reuses the same key, so it cannot save twice.
        ui.status.className = "msg warn";
        ui.status.textContent = `${result.message} Press the button again to retry safely.`;
        ui.input.setAttribute("readonly", "true");
        return;
      }
      ui.status.hidden = true; ui.msg.textContent = `${result.message} (code ${result.status})`; ui.msg.hidden = false; ui.msg.className = "msg error";
      ui.input.setAttribute("aria-invalid", "true");
      if (result.status === 403) ui.input.setAttribute("readonly", "true"); // no bypass for consent or role
    }

    function renderCorrectionForm(trial, m) {
      const base = `corr-${trial.trial_id}`;
      const form = el("form", { class: "correction", novalidate: true, "aria-label": `Correct entry for attempt ${trial.attempt_number}` });
      form.append(el("p", { class: "facts", text: `Current saved time: ${m.effective_value} sec` }));
      const value = el("input", { id: `${base}-value`, type: "text", inputmode: "decimal", autocomplete: "off", class: "time-input", "aria-describedby": `${base}-value-msg` });
      form.append(field(`${base}-value`, "Corrected time (seconds)", value, `${base}-value-msg`));
      const reason = el("select", { id: `${base}-reason`, "aria-describedby": `${base}-reason-msg` });
      reason.append(el("option", { value: "", text: "Select a reason" }));
      for (const [c, t] of CORRECTION_REASONS) reason.append(el("option", { value: c, text: t }));
      form.append(field(`${base}-reason`, "Reason", reason, `${base}-reason-msg`));
      const reasonText = el("input", { id: `${base}-reason-text`, type: "text", maxlength: "200", autocomplete: "off", "aria-describedby": `${base}-reason-text-msg` });
      const textWrap = field(`${base}-reason-text`, "Write the reason", reasonText, `${base}-reason-text-msg`);
      textWrap.hidden = true;
      form.append(textWrap);
      reason.addEventListener("change", () => { textWrap.hidden = reason.value !== "OTHER"; });

      const confirmer = el("select", { id: `${base}-confirmer`, "aria-describedby": `${base}-confirmer-msg` });
      confirmer.append(el("option", { value: "", text: "Select the second person" }));
      for (const c of state.confirmers || []) confirmer.append(el("option", { value: c.id, text: c.name }));
      form.append(field(`${base}-confirmer`, "Confirmed by (a different coach or admin)", confirmer, `${base}-confirmer-msg`));
      const unavailable = !(state.confirmers && state.confirmers.length);
      if (unavailable) {
        form.append(el("p", { class: "msg warn", role: "alert", text: state.confirmersFailed
          ? "The list of people who can confirm could not be loaded, so corrections are unavailable on this screen."
          : "No other active coach or admin is available to confirm, so a correction cannot be made right now." }));
      }
      const status = el("p", { id: `${base}-status`, class: "msg", role: "status", hidden: true });
      const submit = el("button", { type: "submit", class: "primary", text: "SUBMIT CORRECTION", "data-action": `correct-${trial.trial_id}` });
      submit.disabled = unavailable;
      const cancel = el("button", { type: "button", class: "ghost", text: "Cancel" });
      cancel.addEventListener("click", () => { state.open = null; renderAssessment(); });
      form.append(el("div", { class: "row" }, submit, cancel), status);

      const boxes = { value: `${base}-value-msg`, reason: `${base}-reason-msg`, "reason-text": `${base}-reason-text-msg`, confirmer: `${base}-confirmer-msg` };
      const controls = { value, reason, "reason-text": reasonText, confirmer };
      function clearErrors() { for (const id of Object.values(boxes)) { const b = form.querySelector(`#${CSS.escape(id)}`); b.hidden = true; b.textContent = ""; } }
      function fieldError(name, message) {
        const b = form.querySelector(`#${CSS.escape(boxes[name] || boxes.value)}`);
        b.textContent = message; b.hidden = false; b.className = "msg error";
        const c = controls[name] || value; c.setAttribute("aria-invalid", "true"); c.focus();
      }
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        await once(`correct-${trial.trial_id}`, async () => {
          clearErrors(); status.hidden = false; status.className = "msg saving"; status.textContent = "Saving…";
          const result = await flows.correct({
            measurementId: m.measurement_id, text: value.value, expectedEffective: m.effective_value,
            reasonCode: reason.value, reasonText: reasonText.value, confirmerId: confirmer.value, selfId: state.me.user_id,
          });
          if (result.state === "INVALID_INPUT") { status.hidden = true; fieldError(result.field || "value", result.message); return; }
          if (result.state === "SAVED") {
            status.className = "msg saved";
            status.textContent = `SAVED: ${result.measurement.original_value} → ${result.measurement.effective_value} sec`;
            state.flash = { trialId: result.measurement.trial_id, text: status.textContent };
            state.open = null; await refreshTree(); return;
          }
          if (result.state === "UNCERTAIN") { status.className = "msg warn"; status.textContent = `${result.message} Press the button again to retry safely.`; return; }
          status.hidden = true; fieldError("value", `${result.message} (code ${result.status})`);
        });
      });
      return form;
    }

    // ---------------------------------------------------------------- page actions
    function wireStaticControls() {
      $("change-player").addEventListener("click", () => {
        state.tree = null; state.assessmentId = null; state.runId = null; state.open = null; state.flash = null;
        history.replaceState(null, "", location.pathname);
        renderAssessment();
        if (state.player) refreshAssessmentList();
        $("player-select").focus();
      });
      $("player-filter").addEventListener("input", (e) => renderPlayerOptions(e.target.value));
      $("player-select").addEventListener("change", (e) => selectPlayer(e.target.value));
      $("start-assessment").addEventListener("click", async () => {
        if (!state.player) { say("Select a player first.", "error", "assessment-msg"); $("player-select").focus(); return; }
        await once("start-assessment", async () => {
          say("Starting…", "saving", "assessment-msg");
          const result = await flows.createAssessment({ playerId: state.player.id, date: today(), title: "V1 assessment" });
          if (result.state === "SAVED") { say("", "", "assessment-msg"); await refreshAssessmentList(); await openAssessment(result.assessment.assessment_id); return; }
          if (result.state === "UNCERTAIN") { say(`${result.message} Press START again to retry safely.`, "warn", "assessment-msg"); return; }
          say(`${result.message} (code ${result.status})`, "error", "assessment-msg");
        });
      });
      const add = async (invalidReason) => {
        if (!state.runId) return;
        await once("add-attempt", async () => {
          const trials = ((state.tree.station_runs || []).find((x) => x.station_id === STATION) || {}).trials || [];
          const result = await flows.addAttempt({ runId: state.runId, attemptNumber: nextAttemptNumber(trials), invalidReason });
          if (result.state === "SAVED") { say("", "", "attempt-msg"); $("invalid-box").hidden = true; await refreshTree(); return; }
          say(result.state === "UNCERTAIN" ? `${result.message} Check the list, then press ADD ATTEMPT again.` : `${result.message} (code ${result.status})`, "error", "attempt-msg");
          if (result.state === "UNCERTAIN") await refreshTree();
        });
      };
      $("add-attempt").setAttribute("data-action", "add-attempt");
      $("add-invalid-attempt").setAttribute("data-action", "add-attempt");
      $("add-attempt").addEventListener("click", () => add(null));
      $("add-invalid-attempt").addEventListener("click", () => { $("invalid-box").hidden = !$("invalid-box").hidden; if (!$("invalid-box").hidden) $("invalid-reason").focus(); });
      const reasons = $("invalid-reason");
      for (const [c, t] of INVALID_REASONS) reasons.append(el("option", { value: c, text: t }));
      $("invalid-save").setAttribute("data-action", "add-attempt");
      $("invalid-save").addEventListener("click", () => {
        if (!reasons.value) { say("Choose the rule that was broken.", "error", "attempt-msg"); reasons.focus(); return; }
        add(reasons.value);
      });
    }

    async function init() {
      wireStaticControls();
      const me = await load("/auth/me");
      if (me.status !== 200) { say(friendlyMessage(me.status, detailOf(me)), "error"); return; }
      state.me = me.body.user || me.body;
      $("who").textContent = `${state.me.username} · ${state.me.role}`;
      await loadPlayers();
      const wanted = new URLSearchParams(location.search).get("assessment");
      if (wanted) {
        const r = await load(`/v1/assessments/${encodeURIComponent(wanted)}`);
        if (r.status === 200) {
          const p = state.players.find((x) => x.id === r.body.player_id);
          if (p) { state.player = p; $("player-select").value = p.id; $("player-name").textContent = p.name; $("assessments-card").hidden = false; await refreshAssessmentList(); }
          await openAssessment(wanted);
        } else say(friendlyMessage(r.status, detailOf(r)), "error");
      }
      document.body.dataset.ready = "true";
    }
    init();
  };

  return exported;
});
