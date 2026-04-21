"use strict";

const REFRESH_INTERVAL_MS = 10_000;
let refreshTimer = null;

// ── Helpers ───────────────────────────────────────────────────────────────────

function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val ?? "—";
}

function fmtDt(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("sv-SE", { timeZone: "UTC" }).replace("T", " ") + " UTC";
}

function fmtDuration(seconds) {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds.toFixed(0)} s`;
  return `${Math.floor(seconds / 60)} min ${(seconds % 60).toFixed(0)} s`;
}

function fmtAgo(iso) {
  if (!iso) return "—";
  const delta = (Date.now() - new Date(iso).getTime()) / 1000;
  if (delta < 90) return `${delta.toFixed(0)} s ago`;
  if (delta < 3600) return `${(delta / 60).toFixed(0)} min ago`;
  return `${(delta / 3600).toFixed(1)} h ago`;
}

function badgeClass(status) {
  if (status === "done") return "badge-ok";
  if (status === "running") return "badge-running";
  if (status === "failed") return "badge-err";
  if (status === "not_started") return "badge-idle";
  return "badge-idle";
}

function setBadge(id, text, cls) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.className = `badge ${cls}`;
}

function modelRows(fields) {
  return fields
    .map(([label, value]) => `
      <div class="model-row">
        <span class="model-row-label">${label}</span>
        <span>${value ?? "—"}</span>
      </div>`)
    .join("");
}

// ── Render functions ──────────────────────────────────────────────────────────

function renderOverview(data) {
  const sc = data.station_cache;
  const aw = data.altitude_winds_cache;
  const gc = data.grid_cache;
  const req = data.requests;

  setText("ov-stations", sc.count ?? 0);
  setText("ov-stations-sub", sc.model ? `model: ${sc.model}` : "cache empty");

  setText("ov-altwinds", aw.count ?? 0);

  if (gc.warm) {
    setText("ov-grid-pts", (gc.n_points ?? 0).toLocaleString());
    setText("ov-grid-sub", `${gc.n_lat} × ${gc.n_lon} (${gc.levels_m?.length ?? 0} levels)`);
  } else {
    setText("ov-grid-pts", "0");
    setText("ov-grid-sub", "cache cold");
  }

  // Forecast horizon = how far ahead is the valid_until from now
  const validUntil = gc.warm ? gc.valid_until : sc.valid_until;
  if (validUntil) {
    const hoursAhead = (new Date(validUntil) - Date.now()) / 3_600_000;
    setText("ov-horizon", hoursAhead > 0 ? `+${hoursAhead.toFixed(0)} h` : "expired");
    setText("ov-horizon-sub", `valid until ${fmtDt(validUntil)}`);
  } else {
    setText("ov-horizon", "—");
    setText("ov-horizon-sub", "no data");
  }

  setText("ov-requests", (req.total ?? 0).toLocaleString());
  setText("ov-requests-sub", `since ${fmtDt(req.started_at)}`);

  const errCount = req.error_count ?? 0;
  setText("ov-errors", errCount);
  if (req.total > 0) {
    const errPct = ((errCount / req.total) * 100).toFixed(1);
    setText("ov-errors-sub", `${errPct}% error rate`);
  } else {
    setText("ov-errors-sub", "");
  }
}

function renderCollection(model, state) {
  const badgeId = `col-${model}-badge`;
  const rowsId = `col-${model}-rows`;
  const progressId = `col-${model}-progress`;
  const barId = `col-${model}-bar`;
  const progLabelId = `col-${model}-prog-label`;

  const statusLabel = {
    not_started: "Not started",
    running: "Running",
    done: "Done",
    failed: "Failed",
  }[state.status] ?? state.status;

  setBadge(badgeId, statusLabel, badgeClass(state.status));

  const progressEl = document.getElementById(progressId);
  if (state.status === "running" && state.files_total > 0) {
    progressEl.style.display = "";
    const pct = Math.round((state.files_done / state.files_total) * 100);
    document.getElementById(barId).style.width = `${pct}%`;
    document.getElementById(progLabelId).textContent =
      `${state.files_done} / ${state.files_total} files (${pct}%)`;
  } else {
    progressEl.style.display = "none";
  }

  const expectedDt = fmtDt(state.expected_ref_dt);
  const cachedDt = fmtDt(state.ref_dt);
  const currentLabel = state.is_current
    ? "✓ up to date"
    : (state.ref_dt ? "stale" : "—");

  document.getElementById(rowsId).innerHTML = modelRows([
    ["Expected dataset", expectedDt],
    ["Cached dataset", cachedDt],
    ["Currency", currentLabel],
    ["Last run started", state.started_at ? fmtAgo(state.started_at) : "—"],
    ["Last run finished", state.finished_at ? fmtAgo(state.finished_at) : "—"],
    ["Duration", fmtDuration(state.duration_s)],
    ...(state.last_error ? [["Last error", state.last_error]] : []),
  ]);
}

function renderCacheDetail(data) {
  const sc = data.station_cache;
  const gc = data.grid_cache;

  const stBadge = document.getElementById("cache-station-badge");
  if (sc.count > 0) {
    stBadge.textContent = "warm";
    stBadge.className = "badge badge-ok";
  } else {
    stBadge.textContent = "cold";
    stBadge.className = "badge badge-idle";
  }
  document.getElementById("cache-station-rows").innerHTML = modelRows([
    ["Stations", sc.count],
    ["Model", sc.model ?? "—"],
    ["Init time", fmtDt(sc.init_time)],
    ["Forecast hours", sc.forecast_hours],
    ["Valid until", fmtDt(sc.valid_until)],
    ["Altitude wind profiles", data.altitude_winds_cache.count],
  ]);

  const grBadge = document.getElementById("cache-grid-badge");
  if (gc.warm) {
    grBadge.textContent = "warm";
    grBadge.className = "badge badge-ok";
    document.getElementById("cache-grid-rows").innerHTML = modelRows([
      ["Points", (gc.n_points ?? 0).toLocaleString()],
      ["Grid size", `${gc.n_lat} × ${gc.n_lon}`],
      ["Forecast frames", gc.forecast_hours],
      ["Init time", fmtDt(gc.init_time)],
      ["Valid until", fmtDt(gc.valid_until)],
      ["Altitude levels", (gc.levels_m ?? []).map(m => `${m} m`).join(", ")],
    ]);
  } else {
    grBadge.textContent = "cold";
    grBadge.className = "badge badge-idle";
    document.getElementById("cache-grid-rows").innerHTML = modelRows([
      ["Status", "Not yet populated — waiting for first collection run"],
    ]);
  }
}

function renderErrors(errors) {
  const emptyEl = document.getElementById("errors-empty");
  const tableEl = document.getElementById("errors-table");
  const tbody = document.getElementById("errors-tbody");

  if (!errors || errors.length === 0) {
    emptyEl.style.display = "";
    tableEl.style.display = "none";
    return;
  }

  emptyEl.style.display = "none";
  tableEl.style.display = "";
  tbody.innerHTML = [...errors].reverse().map(e => `
    <tr>
      <td style="white-space:nowrap;">${fmtDt(e.ts)}</td>
      <td>${e.method}</td>
      <td style="font-family:monospace;font-size:12px;">${e.path}</td>
      <td><span class="badge badge-err">${e.status}</span></td>
      <td style="max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;">${e.detail ?? ""}</td>
    </tr>`).join("");
}

// ── Fetch and render ──────────────────────────────────────────────────────────

async function refresh() {
  const dot = document.getElementById("refresh-dot");
  const status = document.getElementById("refresh-status");

  console.log("[App:dashboard] fetching /api/dashboard");
  const t0 = performance.now();

  try {
    const res = await fetch("/api/dashboard");
    const elapsed = (performance.now() - t0).toFixed(0);

    if (!res.ok) {
      console.warn(`[App:dashboard] /api/dashboard returned ${res.status}`);
      dot.className = "dot dot-warn";
      status.textContent = `Last refresh failed (HTTP ${res.status})`;
      return;
    }

    const data = await res.json();
    console.log(`[App:dashboard] data received in ${elapsed} ms`, data);

    renderOverview(data);
    renderCollection("ch1", data.collection.ch1);
    renderCollection("ch2", data.collection.ch2);
    renderCacheDetail(data);
    renderErrors(data.recent_errors);

    // Dot colour: red if any collection failed, yellow if stale, green otherwise
    const anyFailed = data.collection.ch1.status === "failed" || data.collection.ch2.status === "failed";
    const anyStale = !data.collection.ch1.is_current || !data.collection.ch2.is_current;
    dot.className = anyFailed ? "dot dot-err" : anyStale ? "dot dot-warn" : "dot";
    status.textContent = `Last updated ${new Date().toLocaleTimeString()} · ${elapsed} ms`;

  } catch (err) {
    console.error("[App:dashboard] fetch failed", err);
    dot.className = "dot dot-err";
    status.textContent = `Refresh error: ${err.message}`;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  console.log("[App:dashboard] page init");
  refresh();
  refreshTimer = setInterval(refresh, REFRESH_INTERVAL_MS);
});
