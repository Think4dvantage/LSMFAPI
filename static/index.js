"use strict";

let lenticularisBaseUrl = "";
let biasChart = null;

async function init() {
  const meta = await fetch("/api/meta").then(r => r.json());
  lenticularisBaseUrl = meta.lenticularis_base_url;

  await loadStations();
  setupDefaults();

  document.getElementById("analyse-btn").addEventListener("click", runAnalysis);
}

async function loadStations() {
  const select = document.getElementById("station-select");
  try {
    const stations = await fetch("/api/stations").then(r => r.json());
    select.innerHTML = stations
      .map(s => `<option value="${s.station_id}" data-lat="${s.latitude}" data-lon="${s.longitude}" data-elev="${s.elevation ?? 0}">${s.name ?? s.station_id}</option>`)
      .join("");
    document.getElementById("analyse-btn").disabled = false;
  } catch (err) {
    select.innerHTML = `<option value="">Failed to load stations</option>`;
    showError(`Could not load stations: ${err.message}`);
  }
}

function setupDefaults() {
  const today = new Date();
  const sevenDaysAgo = new Date(today);
  sevenDaysAgo.setDate(today.getDate() - 7);

  document.getElementById("date-to").value = today.toISOString().slice(0, 10);
  document.getElementById("date-from").value = sevenDaysAgo.toISOString().slice(0, 10);
}

async function runAnalysis() {
  const stationSelect = document.getElementById("station-select");
  const stationOpt = stationSelect.selectedOptions[0];
  const variable = document.getElementById("variable-select").value;
  const dateFrom = document.getElementById("date-from").value;
  const dateTo = document.getElementById("date-to").value;

  if (!stationOpt || !stationOpt.value) return;

  clearError();
  showLoading(true);

  const stationId = stationOpt.value;
  const lat = stationOpt.dataset.lat;
  const lon = stationOpt.dataset.lon;
  const elev = stationOpt.dataset.elev;

  try {
    const [actuals, forecasts] = await Promise.all([
      fetchActuals(stationId, variable, dateFrom, dateTo),
      fetchForecasts(stationId, lat, lon, elev, variable, dateFrom, dateTo),
    ]);

    const stats = computeStats(actuals, forecasts, variable);
    renderChart(stats, variable, stationOpt.text);
    renderRmseTable(stats);
  } catch (err) {
    showError(`Analysis failed: ${err.message}`);
  } finally {
    showLoading(false);
  }
}

async function fetchActuals(stationId, variable, dateFrom, dateTo) {
  const url = `${lenticularisBaseUrl}/api/actuals?station_id=${stationId}&variable=${variable}&from=${dateFrom}&to=${dateTo}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Lenticularis actuals returned ${res.status}`);
  return res.json();
}

async function fetchForecasts(stationId, lat, lon, elev, variable, dateFrom, dateTo) {
  const url = `${lenticularisBaseUrl}/api/forecast-archive?station_id=${stationId}&variable=${variable}&from=${dateFrom}&to=${dateTo}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Lenticularis forecast archive returned ${res.status}`);
  return res.json();
}

function computeStats(actuals, forecasts, variable) {
  const byLeadHour = {};

  for (const forecast of forecasts) {
    const leadH = forecast.lead_hours;
    const actual = actuals.find(a => a.valid_time === forecast.valid_time);
    if (!actual) continue;

    if (!byLeadHour[leadH]) byLeadHour[leadH] = { errors: [], biases: [] };
    const bias = forecast.probable - actual.value;
    byLeadHour[leadH].biases.push(bias);
    byLeadHour[leadH].errors.push(bias * bias);
  }

  return Object.entries(byLeadHour)
    .sort(([a], [b]) => Number(a) - Number(b))
    .map(([leadH, { errors, biases }]) => ({
      leadH: Number(leadH),
      rmse: Math.sqrt(errors.reduce((a, b) => a + b, 0) / errors.length),
      meanBias: biases.reduce((a, b) => a + b, 0) / biases.length,
      n: errors.length,
    }));
}

function renderChart(stats, variable, stationName) {
  const card = document.getElementById("chart-card");
  card.style.display = "";
  document.getElementById("chart-title").textContent =
    `${variable.replace(/_/g, " ")} — ${stationName} — mean bias by lead hour`;

  const ctx = document.getElementById("bias-chart").getContext("2d");
  if (biasChart) biasChart.destroy();

  biasChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: stats.map(s => `+${s.leadH}h`),
      datasets: [{
        label: "Mean bias",
        data: stats.map(s => s.meanBias),
        backgroundColor: stats.map(s => s.meanBias >= 0 ? "rgba(56,139,253,0.6)" : "rgba(248,81,73,0.6)"),
        borderColor: stats.map(s => s.meanBias >= 0 ? "#388bfd" : "#f85149"),
        borderWidth: 1,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: "#8b949e" }, grid: { color: "#21262d" } },
        y: { ticks: { color: "#8b949e" }, grid: { color: "#21262d" } },
      },
    },
  });
}

function renderRmseTable(stats) {
  const card = document.getElementById("rmse-card");
  card.style.display = "";
  const tbody = document.querySelector("#rmse-table tbody");
  tbody.innerHTML = stats.map(s => `
    <tr>
      <td>+${s.leadH}h</td>
      <td>${s.rmse.toFixed(3)}</td>
      <td>${s.meanBias >= 0 ? "+" : ""}${s.meanBias.toFixed(3)}</td>
      <td>${s.n}</td>
    </tr>
  `).join("");
}

function showError(msg) {
  const el = document.getElementById("form-error");
  el.textContent = msg;
  el.style.display = "";
}

function clearError() {
  const el = document.getElementById("form-error");
  el.style.display = "none";
}

function showLoading(show) {
  document.getElementById("loading-msg").style.display = show ? "" : "none";
  document.getElementById("analyse-btn").disabled = show;
}

document.addEventListener("DOMContentLoaded", init);
