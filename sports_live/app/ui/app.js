"use strict";

const $ = (sel) => document.querySelector(sel);

async function checkHealth() {
  try {
    const res = await fetch("./api/healthz", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    $("#health").textContent = "ok";
    $("#version").textContent = data.version;
    setStatus("online", "ok");
  } catch (err) {
    $("#health").textContent = `error: ${err.message}`;
    setStatus("offline", "err");
  }
}

function setStatus(text, kind) {
  const el = $("#status");
  el.textContent = text;
  el.className = `status status-${kind}`;
}

checkHealth();
setInterval(checkHealth, 10_000);
