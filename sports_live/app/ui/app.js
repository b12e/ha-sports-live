"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const state = {
  picked: null,            // chosen MatchSummary (server payload)
  lights: [],              // [{entity_id, name, supports_color, …}]
  selectedLights: new Set(),
  status: null,            // last /api/match/status response
};

// ---- helpers --------------------------------------------------------------

async function api(path, opts = {}) {
  const res = await fetch(`./api${path}`, {
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${txt.slice(0, 200)}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

function setStatus(text, kind) {
  const el = $("#status");
  el.textContent = text;
  el.className = `status status-${kind}`;
}

function setPhase(text, kind = "pending") {
  const el = $("#phase-pill");
  el.textContent = text;
  el.className = `status status-${kind}`;
}

function rgbToHex(rgb) {
  if (!rgb) return "transparent";
  return "#" + rgb.map((n) => n.toString(16).padStart(2, "0")).join("");
}

// ---- match search ---------------------------------------------------------

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

const doSearch = debounce(async () => {
  const q = $("#q").value.trim();
  const provider = $("#provider").value;
  $("#results").innerHTML = "";
  if (!q) return;
  if (provider !== "sofascore" && provider !== "mock") {
    $("#results").innerHTML = `<p class="muted">Switch provider to "Sofascore" or "Mock" to search.</p>`;
    return;
  }
  try {
    const results = await api(`/match/search?provider=${provider}&q=${encodeURIComponent(q)}`);
    if (!results.length) {
      $("#results").innerHTML = `<p class="muted">No matches for "${q}".</p>`;
      return;
    }
    $("#results").innerHTML = results.map((m, i) => `
      <button class="result" data-i="${i}">
        <span class="result-comp">${escape(m.competition || "—")}</span>
        <span class="result-teams">${escape(m.home.name)} vs ${escape(m.away.name)}</span>
        <span class="result-score">${m.score_home}–${m.score_away}</span>
        <span class="result-meta">${escape(m.phase)} · ${escape(m.kickoff_utc.slice(0, 16).replace("T", " "))} UTC</span>
      </button>
    `).join("");
    $$("#results .result").forEach((b) =>
      b.addEventListener("click", () => pick(results[+b.dataset.i]))
    );
  } catch (err) {
    $("#results").innerHTML = `<p class="error">Search failed: ${escape(err.message)}</p>`;
  }
}, 350);

function pick(m) {
  state.picked = m;
  $("#picked").classList.remove("hidden");
  $("#picked-label").textContent = `${m.home.name} vs ${m.away.name} (${m.competition || "—"})`;
  $("#results").innerHTML = "";
  $("#search-row").classList.add("hidden");
  refreshStartButton();
}

function unpick() {
  state.picked = null;
  $("#picked").classList.add("hidden");
  $("#search-row").classList.remove("hidden");
  refreshStartButton();
}

// ---- lights ---------------------------------------------------------------

async function loadLights() {
  try {
    state.lights = await api("/lights");
    renderLights();
  } catch (err) {
    $("#lights").innerHTML = `<p class="error">Could not load lights: ${escape(err.message)}</p>`;
  }
}

function renderLights() {
  const html = state.lights
    .filter((l) => l.entity_id.startsWith("light."))
    .map((l) => `
      <label class="light-row ${l.supports_color ? "" : "muted"}">
        <input type="checkbox" data-eid="${l.entity_id}" ${state.selectedLights.has(l.entity_id) ? "checked" : ""}>
        <span class="light-name">${escape(l.name)}</span>
        <span class="light-eid"><code>${l.entity_id}</code></span>
        <span class="light-state ${l.state === "on" ? "on" : ""}">${l.state}</span>
      </label>`)
    .join("") || `<p class="muted">No lights found.</p>`;
  $("#lights").innerHTML = html;
  $$("#lights input[type=checkbox]").forEach((c) =>
    c.addEventListener("change", () => {
      if (c.checked) state.selectedLights.add(c.dataset.eid);
      else state.selectedLights.delete(c.dataset.eid);
      refreshStartButton();
    })
  );
}

// ---- start / stop ---------------------------------------------------------

function refreshStartButton() {
  const provider = $("#provider").value;
  const ready = (provider === "replay")
    ? Boolean($("#replay-path").value.trim()) && state.selectedLights.size > 0
    : Boolean(state.picked) && state.selectedLights.size > 0;
  $("#start").disabled = !ready;
}

async function start() {
  $("#start").disabled = true;
  try {
    const provider = $("#provider").value;
    const body = {
      provider,
      match_id: state.picked ? state.picked.id : "replay",
      lights: Array.from(state.selectedLights),
      tv_delay_s: Number($("#tv-delay").value),
      dry_run: $("#dry-run").checked,
      replay_path: provider === "replay" ? $("#replay-path").value.trim() : null,
    };
    await api("/match/start", { method: "POST", body: JSON.stringify(body) });
    await refreshStatus();
  } catch (err) {
    alert("Start failed: " + err.message);
  } finally {
    refreshStartButton();
  }
}

async function stop() {
  try {
    await api("/match/stop?restore=true", { method: "POST" });
    state.picked = null;
    state.status = null;
    await refreshStatus();
  } catch (err) {
    alert("Stop failed: " + err.message);
  }
}

// ---- status polling -------------------------------------------------------

async function refreshStatus() {
  try {
    state.status = await api("/match/status");
    renderStatus();
    setStatus("online", "ok");
  } catch (err) {
    setStatus("offline", "err");
  }
}

function renderStatus() {
  const s = state.status;
  if (!s || !s.running) {
    $("#live").classList.add("hidden");
    $("#mock").classList.add("hidden");
    $("#setup").classList.remove("hidden");
    setPhase("idle");
    return;
  }
  $("#setup").classList.add("hidden");
  $("#live").classList.remove("hidden");

  // Mock injectors visible only when last_used.provider === "mock".
  const lastProvider = (state.picked && $("#provider").value) || (state.status && state.status.provider);
  if ($("#provider").value === "mock") $("#mock").classList.remove("hidden");
  else $("#mock").classList.add("hidden");

  $("#home-name").textContent = (s.home && (s.home.short_name || s.home.name)) || "Home";
  $("#away-name").textContent = (s.away && (s.away.short_name || s.away.name)) || "Away";
  $("#home-score").textContent = s.score_home;
  $("#away-score").textContent = s.score_away;
  $("#phase").textContent = s.phase;
  setPhase(s.phase, phaseKind(s.phase));
  $("#last-event").textContent = s.last_event
    ? `${s.last_event.kind}${s.last_event.minute != null ? ` ${s.last_event.minute}'` : ""}${s.last_event.side ? ` (${s.last_event.side})` : ""}`
    : "—";
  $("#ambient-rgb").textContent = s.ambient ? `rgb(${s.ambient.join(", ")})` : "—";
  $("#ambient-swatch").style.background = s.ambient ? rgbToHex(s.ambient) : "transparent";
  $("#pending").textContent = s.pending_events;
  $("#tv-delay-live").textContent = s.tv_delay_s;
}

function phaseKind(phase) {
  if (phase === "live" || phase === "extra_time" || phase === "penalty_shootout") return "ok";
  if (phase === "fulltime" || phase === "abandoned" || phase === "postponed") return "err";
  return "pending";
}

// ---- TV delay -------------------------------------------------------------

const updateDelay = debounce(async () => {
  const v = Number($("#tv-delay").value);
  $("#tv-delay-readout").textContent = v;
  try { await api(`/match/tv_delay?seconds=${v}`, { method: "POST" }); } catch { /* ignore */ }
}, 200);

// ---- mock injection -------------------------------------------------------

async function injectMock(kind, side) {
  const body = { kind };
  if (side) body.side = side;
  try { await api("/debug/inject", { method: "POST", body: JSON.stringify(body) }); }
  catch (err) { alert("Inject failed: " + err.message); }
}

// ---- escape ---------------------------------------------------------------

function escape(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

// ---- bootstrap ------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  $("#q").addEventListener("input", doSearch);
  $("#provider").addEventListener("change", () => {
    const p = $("#provider").value;
    $("#replay-row").classList.toggle("hidden", p !== "replay");
    $("#search-row").classList.toggle("hidden", p === "replay" || Boolean(state.picked));
    refreshStartButton();
  });
  $("#replay-path").addEventListener("input", refreshStartButton);
  $("#unpick").addEventListener("click", unpick);
  $("#start").addEventListener("click", start);
  $("#kill-switch").addEventListener("click", stop);
  $("#tv-delay").addEventListener("input", () => {
    $("#tv-delay-readout").textContent = $("#tv-delay").value;
    updateDelay();
  });
  $$("#mock button").forEach((b) =>
    b.addEventListener("click", () => injectMock(b.dataset.kind, b.dataset.side))
  );
  loadLights();
  refreshStatus();
  setInterval(refreshStatus, 1500);
});
