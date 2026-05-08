# Project Overview — LSMFAPI (Lenticularis SwissMeteo Forecast API)

## What This Is

LSMFAPI is a dedicated forecast ingestion and delivery service that replaces the OpenMeteo dependency in the Lenticularis paragliding weather decision-support app. It downloads raw ensemble model output (ICON-CH1-EPS, ICON-CH2-EPS) directly from the MeteoSwiss open data portal, computes statistically robust forecast summaries (median + absolute min/max across all members and runs), and exposes them to Lenticularis via a REST API. It also provides an internal English-only GUI for forecast accuracy analysis.

---

## Tech Stack

| Concern | Tool |
|---|---|
| Language | Python 3.11+ |
| Web framework | FastAPI |
| Data validation | Pydantic v2 |
| Dependency management | Poetry (`pyproject.toml`) |
| Forecast cache | Python in-process dicts (CH1 + CH2 stored separately, merged at read time) |
| Relational DB | SQLite via SQLAlchemy (no Alembic — raw ALTER TABLE in `_run_column_migrations()`) |
| Scheduler | APScheduler (cron triggers) |
| HTTP client | httpx (async) |
| Config | YAML (`config.yml`) validated by Pydantic |
| GRIB2 parsing | `cfgrib` + `xarray` + `eccodes` + `eccodes-cosmo-resources-python` |
| Spatial math | `scipy` (KD-tree nearest-point lookup) |
| Frontend | Vanilla JS (English only — no i18n, no build step, no npm) |
| Container | Docker + docker-compose |

---

## Repository Layout

```
src/lsmfapi/
├── _eccodes.py              # ecCodes + COSMO definitions setup (called on startup)
├── config.py                # Pydantic-validated YAML config loader (singleton)
├── scheduler.py             # APScheduler cron jobs; per-model asyncio locks prevent overlapping runs
├── api/
│   ├── main.py              # FastAPI app factory + lifespan
│   └── routers/
│       ├── forecast.py      # GET /api/forecast/station + /altitude-winds + /wind-grid
│       ├── accuracy.py      # GET /accuracy (GUI) + /api/meta + /api/stations proxy
│       └── dashboard.py     # GET /dashboard + /api/dashboard (collection state + telemetry)
├── collectors/
│   ├── base.py              # Abstract base + async download helper
│   ├── grib_cache.py        # grib_run_dir() context manager; persistent GRIB dirs in /tmp
│   ├── icon_ch1_eps.py      # ICON-CH1-EPS ingestor (h0–h33, 1h steps, ~10 members)
│   └── icon_ch2_eps.py      # ICON-CH2-EPS ingestor (h34–h120, 1h steps, ~21 members)
├── database/
│   ├── cache.py             # In-memory forecast cache (get/set station + altitude winds + grid)
│   ├── collection_state.py  # Runtime collection state (status, files_done, files_ok)
│   ├── telemetry.py         # HTTP + download error log (last 20 errors → dashboard)
│   ├── db.py                # init_db(), get_db(), _run_column_migrations()
│   └── models.py            # SQLAlchemy ORM (Recipe, RecipeRule — v0.4)
├── models/
│   └── forecast.py          # Pydantic schemas: ForecastResponse, AltitudeWindsResponse
└── services/
    └── ensemble.py          # Median + circular median + absolute min/max
static/
├── shared.css               # Dark theme
├── dashboard.html + dashboard.js   # Operational dashboard
├── index.html + index.js    # Accuracy analysis GUI
```

---

## Data Flow

```
Container startup
  → setup_definitions(): register COSMO GRIB2 shortName defs with ecCodes
  → init_db()
  → load_cache(): restore CH1 + CH2 dicts from /app/data/cache.json (API usable immediately)
  → CollectorScheduler.startup(): register cron jobs + asyncio.create_task(_warm_cache())

_warm_cache() runs CH1 then CH2 sequentially:
  → _ensure_grid(): download horizontal_constants GRIB2, build KD-tree (1.1M points for CH1)
  → _fetch_stations(): GET {lenticularis.base_url}/api/stations
  → concurrent GRIB downloads via asyncio.Semaphore (DOWNLOAD_CONCURRENCY)
  → GRIB file persistence: /tmp/lsmfapi_grib/{model}/{ref_dt}/ — skips re-downloads
  → eccodes parsing → numpy arrays → ensemble stats (median, min, max)
  → set_station_forecast() / set_station_altitude_winds() per station
  → save_cache(): atomic write to /app/data/cache.json

API routes → pure dict lookup, no on-the-fly computation → JSON to Lenticularis
```

---

## Data Sources

| Model | Resolution | Horizon | Trigger (UTC) | Members |
|---|---|---|---|---|
| ICON-CH1-EPS | 1.1 km | h0–h33 (1h steps) | 02/08/14/20Z | ~10 (read dynamically from GRIB) |
| ICON-CH2-EPS | 2.2 km | h34–h120 (1h steps) | 03/09/15/21Z | ~21 (read dynamically) |

Triggers are 2h (CH1) and 3h (CH2) after each 00/06/12/18Z MeteoSwiss release.

**Ensemble member count**: NEVER hardcoded. Read from the first valid GRIB result at runtime. `N_MEMBERS` in collector files is `# informational only`.

**Variables per station per hour**: wind speed/direction/gusts (10m), temperature (2m), relative humidity (from TD_2M via Magnus formula — NOT QV), QFF pressure (PMSL), precipitation (de-accumulated), solar direct/diffuse (de-accumulated), sunshine minutes (de-accumulated), cloud cover total/low/mid/high, freezing level, CAPE_ML, CIN_ML.

**Not in EPS catalog**: `HBAS_CON` (cloud base) and `HPBL` (boundary layer height) — do NOT add these back to SURFACE_VARS.

**Altitude winds** (separate endpoint): U/V/W at 9 pressure levels → mapped to 500/800/1000/1500/2000/2500/3000/4000/5000 m ASL.

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/forecast/station` | Hourly blended forecast, params: `station_id`, `hours` |
| GET | `/api/forecast/altitude-winds` | Pressure-level winds at 9 altitude bands, params: `station_id`, `hours` |
| GET | `/api/forecast/wind-grid` | 171-point wind grid (stub — not yet populated by collectors) |
| GET | `/api/stations` | Proxy to Lenticularis — avoids browser CORS |
| GET | `/dashboard` | Operational dashboard (collection state, cache, error log) |
| GET | `/accuracy` | Accuracy analysis GUI |
| GET | `/health` | Cache stats + service health |

---

## Deployment

- PRD: container `lsmfapi` on XPS, compose file lives **outside this repo** on the server. Uses image `ghcr.io/think4dvantage/lsmfapi:<tag>`.
- DEV: deployed via `scripts/LSMF-dev.ps1` using `docker compose --project-name lsmfapi-dev -f docker-compose.yml -f docker-compose.dev.yml`.
- `docker-compose.yml` in this repo is the **DEV base only** — no Traefik labels. PRD labels live in the server-side compose file.
- Cache volume: `./data:/app/data` — never overwritten by rsync deploy.
- Config: `config.yml` (gitignored). Lenticularis base URL: `https://lenti.cloud`.
