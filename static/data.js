"use strict";

// Tab switching
document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    const tab = btn.dataset.tab;
    console.log(`[App:data] tab switched → ${tab}`);
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${tab}`).classList.add("active");
  });
});

// ── Station Forecast ──────────────────────────────────────────────────────────

async function loadStations() {
  const select = document.getElementById("station-select");
  console.log("[App:data] loading stations from /api/stations");
  const t0 = performance.now();
  try {
    const stations = await fetch("/api/stations").then(r => r.json());
    console.log(`[App:data] loaded ${stations.length} stations in ${(performance.now() - t0).toFixed(0)} ms`);
    select.innerHTML = stations
      .map(s => `<option value="${s.station_id}">${s.name ?? s.station_id}</option>`)
      .join("");
    document.getElementById("station-load-btn").disabled = false;
  } catch (err) {
    console.error("[App:data] failed to load stations", err);
    select.innerHTML = `<option value="">Failed to load stations</option>`;
    showStationError(`Could not load stations: ${err.message}`);
  }
}

async function loadStationForecast() {
  const stationId = document.getElementById("station-select").value;
  if (!stationId) return;
  console.log(`[App:data] loading station forecast for station_id=${stationId}`);
  clearStationError();
  setStationLoading(true);
  const url = `/api/forecast/station?station_id=${encodeURIComponent(stationId)}`;
  const t0 = performance.now();
  try {
    const res = await fetch(url);
    const elapsed = (performance.now() - t0).toFixed(0);
    const json = await res.json();
    console.log(`[App:data] station forecast received status=${res.status} in ${elapsed} ms`);
    const pretty = JSON.stringify(json, null, 2);
    document.getElementById("station-json").textContent = pretty;
    document.getElementById("station-status").textContent =
      `HTTP ${res.status} · ${(pretty.length / 1024).toFixed(1)} KB · ${elapsed} ms · ${url}`;
    document.getElementById("station-result").style.display = "";
  } catch (err) {
    console.error("[App:data] station forecast fetch failed", err);
    showStationError(`Fetch failed: ${err.message}`);
  } finally {
    setStationLoading(false);
  }
}

function showStationError(msg) {
  const el = document.getElementById("station-error");
  el.textContent = msg;
  el.style.display = "";
}

function clearStationError() {
  document.getElementById("station-error").style.display = "none";
}

function setStationLoading(on) {
  document.getElementById("station-loading").style.display = on ? "" : "none";
  document.getElementById("station-load-btn").disabled = on;
}

document.getElementById("station-load-btn").addEventListener("click", loadStationForecast);
document.getElementById("station-select").addEventListener("change", () => {
  document.getElementById("station-result").style.display = "none";
});

// ── Wind Forecast Grid ────────────────────────────────────────────────────────

async function loadWindForecast() {
  const levelM = document.getElementById("level-select").value;
  console.log(`[App:data] loading wind grid forecast for level_m=${levelM}`);
  clearWindError();
  setWindLoading(true);
  const url = `/api/forecast/grid?level_m=${levelM}`;
  const t0 = performance.now();
  try {
    const res = await fetch(url);
    const elapsed = (performance.now() - t0).toFixed(0);
    const json = await res.json();
    console.log(`[App:data] wind grid received status=${res.status} in ${elapsed} ms`);
    const pretty = JSON.stringify(json, null, 2);
    document.getElementById("wind-json").textContent = pretty;
    document.getElementById("wind-status").textContent =
      `HTTP ${res.status} · ${(pretty.length / 1024).toFixed(1)} KB · ${elapsed} ms · ${url}`;
    document.getElementById("wind-result").style.display = "";
  } catch (err) {
    console.error("[App:data] wind grid fetch failed", err);
    showWindError(`Fetch failed: ${err.message}`);
  } finally {
    setWindLoading(false);
  }
}

function showWindError(msg) {
  const el = document.getElementById("wind-error");
  el.textContent = msg;
  el.style.display = "";
}

function clearWindError() {
  document.getElementById("wind-error").style.display = "none";
}

function setWindLoading(on) {
  document.getElementById("wind-loading").style.display = on ? "" : "none";
  document.getElementById("wind-load-btn").disabled = on;
}

document.getElementById("wind-load-btn").addEventListener("click", loadWindForecast);
document.getElementById("level-select").addEventListener("change", () => {
  document.getElementById("wind-result").style.display = "none";
});

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  console.log("[App:data] page init");
  loadStations();
});
