# LSMFAPI — Lenticularis SwissMeteo Forecast API

LSMFAPI is a dedicated forecast ingestion and delivery service that replaces the OpenMeteo dependency in the Lenticularis paragliding weather decision-support app. It downloads raw ensemble model output (ICON-CH1-EPS, ICON-CH2-EPS) directly from the MeteoSwiss open data portal, computes statistically robust forecast summaries (median + absolute min/max across all members and runs), and exposes them to Lenticularis via a REST API. It also provides an internal English-only GUI for forecast accuracy analysis and recipe-based bias correction.

---

## Prerequisites

- Python 3.11+
- [Poetry](https://python-poetry.org/) for dependency management
- `libeccodes-dev` system library (required by `cfgrib` for GRIB2 parsing — handled automatically in Docker)
- Docker + docker-compose

---

## Local Setup

```bash
git clone <repo>
cd LSMFAPI
poetry install
cp config.yml.example config.yml   # then fill in your Lenticularis base URL
docker-compose up
```

The API will be available at `http://localhost:8000`.

---

## Configuration

All configuration lives in `config.yml` (gitignored). Use `config.yml.example` as the template.

| Section | Key | Description |
|---|---|---|
| `meteoswiss` | `stac_base_url` | MeteoSwiss STAC API base (default: `https://data.geo.admin.ch/api/stac/v1`) |
| `meteoswiss` | `ch1eps_collection` | ICON-CH1-EPS collection ID |
| `meteoswiss` | `ch2eps_collection` | ICON-CH2-EPS collection ID |
| `lenticularis` | `base_url` | Lenticularis API base URL (station list + accuracy GUI) |

Never read `os.environ` directly in code — all configuration goes through `get_config()`.

---

## API Overview

No authentication. All endpoints are open — access is controlled at the network/container level.

### Forecast

| Method | Path | Description |
|---|---|---|
| GET | `/api/forecast/station` | Hourly blended station forecast — all variables below, probable + min + max, up to 120 h. Params: `station_id`, `hours` |
| GET | `/api/forecast/altitude-winds` | Hourly pressure-level wind forecast at 9 altitude bands (500–5000 m ASL). Params: `station_id`, `hours` |
| GET | `/api/forecast/wind-grid` | 171-point Switzerland wind grid at 9 altitude levels (stub — not yet populated). Params: `date`, `level_m` |
| GET | `/api/stations` | Proxy to Lenticularis station list (CORS-safe) |
| GET | `/accuracy` | Accuracy analysis GUI (browser) |
| GET | `/api/meta` | Returns Lenticularis base URL to the GUI |
| GET | `/health` | Service health + cache key counts |
| GET | `/dashboard` | Operational dashboard: live collection status, cache state, error log |

### Recipes (v0.4)

| Method | Path | Description |
|---|---|---|
| GET | `/api/recipes` | List all recipes |
| POST | `/api/recipes` | Create a recipe |
| PUT | `/api/recipes/{id}` | Update a recipe |
| DELETE | `/api/recipes/{id}` | Delete a recipe |

---

## Forecast Variables

Every variable is returned as `{ probable, min, max }` — median and absolute min/max across all ensemble members and all model runs blended for the forecast window.

### Surface (per hour, per station)

| Field | Unit | Description |
|---|---|---|
| `wind_speed` | m/s | 10 m wind speed |
| `wind_gusts` | m/s | 10 m wind gusts (max in step) |
| `wind_direction` | degrees | 10 m wind direction (0/360 = N) |
| `temperature` | °C | 2 m air temperature |
| `humidity` | % | 2 m relative humidity (computed from TD_2M via Magnus formula) |
| `pressure_qff` | hPa | Sea-level pressure (QFF reduction) |
| `precipitation` | mm/h | Total precipitation rate |
| `solar_direct` | W/m² | Direct shortwave radiation at surface |
| `solar_diffuse` | W/m² | Diffuse shortwave radiation at surface |
| `sunshine_minutes` | min/h | Minutes of sunshine in the hour (0–60) |
| `cloud_cover_total` | % | Total cloud cover |
| `cloud_cover_low` | % | Low cloud cover |
| `cloud_cover_mid` | % | Mid-level cloud cover |
| `cloud_cover_high` | % | High cloud cover |
| `freezing_level` | m ASL | Height of 0 °C isotherm |
| `cape` | J/kg | Mixed-layer CAPE — convective energy (0 = stable, >500 = significant) |
| `cin` | J/kg | Mixed-layer CIN — convective inhibition (negative) |

### Pressure levels (9 altitude bands: 500 / 800 / 1000 / 1500 / 2000 / 2500 / 3000 / 4000 / 5000 m ASL)

Served via `/api/forecast/altitude-winds`.

| Field | Unit | Description |
|---|---|---|
| `wind_speed` | m/s | Horizontal wind speed at altitude |
| `wind_direction` | degrees | Horizontal wind direction at altitude |
| `vertical_wind` | m/s | Vertical wind speed — positive = updraft, negative = downdraft/sink |

---

## Data Sources

LSMFAPI ingests two MeteoSwiss high-resolution ensemble models downloaded via the [MeteoSwiss Open Data STAC API](https://data.geo.admin.ch/api/stac/v1/):

| Model | Resolution | Horizon | Runs/day | Members |
|---|---|---|---|---|
| ICON-CH1-EPS | 1.1 km | 0–33 h (hourly) | 4 (02/08/14/20Z) | 10 (read dynamically) |
| ICON-CH2-EPS | 2.2 km | 34–120 h (hourly) | 4 (03/09/15/21Z) | 21 (read dynamically) |

**Blending rule**: hours 0–33 from CH1-EPS (hourly, 1.1 km resolution); hours 34–120 from CH2-EPS (hourly, 2.2 km). Both models are cached independently and merged at read time — a CH1 re-run refreshes only the near-term slice; the CH2 long-range tail is unaffected, and vice versa.

**Ensemble member count**: not hardcoded. The actual count is read from the first valid GRIB result at runtime. CH1 currently delivers 10 members (nominally 11).

---

## Architecture

### Data flow

```
Container startup
  → load_cache(): restore CH1 + CH2 dicts from /app/data/cache.json (API usable immediately)
  → Download grid coordinates (horizontal_constants GRIB2)
  → Build KD-tree for nearest-point lookup
  → Fetch station list from Lenticularis API
  → Trigger background collection run to warm in-memory cache

MeteoSwiss STAC API → GRIB2 files (one per variable per step)
  → Persistent GRIB cache: /tmp/lsmfapi_grib/{model}/{ref_dt}/ (skip re-downloads on restart)
  → cfgrib + xarray: decode all ensemble members
  → De-accumulate precipitation, radiation, sunshine
  → Compute RH from TD_2M (dew point) + T_2M via Magnus formula
  → Ensemble engine: median, min, max across all members × runs
  → Precompute ForecastResponse for every known station
  → Store in separate CH1/CH2 in-memory dicts (keyed by station_id)
  → save_cache(): persist to /app/data/cache.json

API routes
  → Dict lookup — no on-the-fly computation
  → Apply active Recipe corrections if any (v0.4)
  → Return JSON to Lenticularis

Accuracy GUI (browser)
  → Fetches actuals + historical forecasts from Lenticularis directly
  → Renders bias charts + RMSE summary table
```

### Repository layout

```
src/lsmfapi/
├── _eccodes.py              # ecCodes + COSMO definitions setup (called on startup)
├── config.py                # Pydantic-validated YAML config loader (singleton)
├── scheduler.py             # APScheduler cron jobs (4×/day each model)
├── api/
│   ├── main.py              # FastAPI app factory + lifespan
│   └── routers/
│       ├── forecast.py      # GET /api/forecast/station + altitude-winds + wind-grid
│       └── accuracy.py      # GET /accuracy (GUI) + /api/meta + /api/stations proxy
├── collectors/
│   ├── base.py              # Abstract base + async download helper
│   ├── grib_cache.py        # grib_run_dir() context manager; persistent GRIB files in /tmp
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
├── dashboard.html           # Operational dashboard
├── dashboard.js             # Dashboard frontend: collection status, cache state, error log
├── index.html + index.js    # Accuracy analysis GUI
```

### In-memory cache

Forecast data is held in Python in-process dicts — there is no time-series database. CH1 and CH2 data are stored in separate dicts and merged at read time: `get_station_forecast()` returns the CH1 hourly head (h0–h33, 1h steps) concatenated with the CH2 hourly tail (h34–h120, 1h steps). Each collector only ever refreshes its own dict, so re-runs don't erase the other model's data.

The cache is populated on container startup and refreshed after every collection run. API calls are pure dict lookups + in-memory merge with no on-the-fly computation. The cache is persisted to `/app/data/cache.json` after each run and restored on restart, so the API serves data immediately while the background warm-up runs.

All cache access goes through `database/cache.py` getter/setter functions so the backing store can be swapped to Redis later without touching router code.

### GRIB file persistence

GRIB files are stored in `/tmp/lsmfapi_grib/{model}/{YYYYMMDDTHHMMZ}/` (not a throwaway temp dir). Files survive container restarts: if the `ref_dt` hasn't changed, previously downloaded files are reused. When the `ref_dt` advances (new model run), old directories are deleted automatically on the next collector start. Corrupt files (eccodes parse failure) are deleted immediately so they are re-downloaded on the next run.

### SQLite tables

| Table | Key columns |
|---|---|
| `recipes` | `id`, `name`, `station_id` (nullable = global), `description`, `active`, `created_at` |
| `recipe_rules` | `id`, `recipe_id` (FK), `variable`, `correction_type` (`additive`\|`multiplicative`), `value`, `condition_json` |

---

## Deployment

### Docker

```bash
# Dev (live reload via LSMF-dev.ps1 deploy, or directly):
docker compose --project-name lsmfapi-dev -f docker-compose.yml -f docker-compose.dev.yml up --build -d
```

`docker-compose.yml` is the base with no Traefik labels. The dev overlay (`docker-compose.dev.yml`) adds only the DEV router labels (`lsmfapi-dev.lg4.ch`). The PRD deployment has its own compose file outside this repo with its own Traefik labels — **never use `docker-compose.yml` from this repo for PRD**, otherwise Traefik will not add the correct routing labels and the container will be invisible to the router.

### Healthcheck

`python:3.11-slim` has no `curl`. The healthcheck uses Python stdlib:

```yaml
healthcheck:
  test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/health')\""]
```

---

## Development Notes

**No Alembic.** Schema migrations use raw `ALTER TABLE` inside `_run_column_migrations()` in `db.py`. New columns on existing tables must be added with an idempotent `ALTER TABLE` checked against `PRAGMA table_info`.

**QFF only.** All pressure fields are `pressure_qff` everywhere. Never use QNH.

**No npm / no build step.** Frontend is plain HTML + vanilla JS. Do not add any bundler or `package.json`.

**English-only GUI.** Internal operator tool. No i18n system — all strings hardcoded in English.

**Scheduler jobs.**

| Job | Trigger | Description |
|---|---|---|
| `collect_ch1eps` | 4×/day at 02/08/14/20Z UTC | Downloads CH1-EPS (h0–h33), updates CH1 cache slice |
| `collect_ch2eps` | 4×/day at 03/09/15/21Z UTC | Downloads CH2-EPS (h34–h120), updates CH2 cache slice |

CH1 runs 2 hours after each 00/06/12/18Z model release; CH2 runs 3 hours after. A startup warm-up (CH1 then CH2) runs once when the container starts.

**HBAS_CON / HPBL** (`cloud_base_convective`, `boundary_layer_height`) are **not published** in the CH1-EPS or CH2-EPS STAC catalog. Do not re-add them to `SURFACE_VARS`.

**Dynamic ensemble member count.** `N_MEMBERS` in the collector files is labelled `# informational only` and never used as a gate. The actual count is read from the first valid GRIB result at runtime. CH1 currently delivers 10 members (nominally 11).

---

## Roadmap

### v0.1 — MVP ✅ Shipped

- ICON-CH1-EPS + CH2-EPS collectors via MeteoSwiss STAC API
- Full variable set: winds, temperature, humidity, pressure, precipitation, radiation, cloud cover, CAPE/CIN, freezing level, altitude winds at 9 bands
- `GET /api/forecast/station`, `GET /api/forecast/altitude-winds`, `GET /api/forecast/wind-grid`
- Accuracy analysis GUI
- Docker + docker-compose + Traefik
- Cache persistence to `/app/data/cache.json`

### v0.2 — Dashboard + cache merge ✅ Shipped

- Operational dashboard with live collection status and cache health
- Data Inspector GUI
- GitHub Actions Docker pipeline + remote deploy script (`scripts/LSMF-dev.ps1`)
- CH1/CH2 cache merge: CH1 hourly head + CH2 tail served as a single blended response

### v0.3 — Reliability hardening ✅ Shipped

- CH2 upgraded from 3h steps to 1h steps (h34–h120, hourly resolution)
- Both models now run 4×/day; CH1 at 02/08/14/20Z, CH2 at 03/09/15/21Z
- **NULL fix**: ensemble member count read dynamically from GRIB (was hardcoded to 11; CH1 delivers 10 → shape check always failed → all-NaN output)
- **GRIB persistence cache**: GRIB files survive container restarts; skip re-downloads when `ref_dt` unchanged
- **Dashboard error panel**: download failures (STAC search, HTTP, eccodes) now shown alongside HTTP errors
- **Corrupt GRIB self-delete**: eccodes failure deletes the bad file so it is re-downloaded on next start
- **Silent STAC miss now warns**: `_fetch_step` logs WARNING when STAC returns no features
- Removed `HBAS_CON` + `HPBL` from surface collection (not in EPS catalog; was wasting 68 STAC calls/run)
- Integration test: `tests/test_e2e_collection.py` (`pytest -m integration`)

### v0.4 — Recipes

- `Recipe` + `RecipeRule` SQLite models
- CRUD endpoints (`GET/POST/PUT/DELETE /api/recipes`)
- Recipe engine: apply additive/multiplicative corrections in `/api/forecast/station`
- Recipe editor GUI

### v0.5 — Enhancements

- Bilinear interpolation for smoother station-level values
- Statistical recipe suggestions (auto-compute mean bias from accuracy data)
- Local LLM integration (Ollama): accuracy + bias stats → natural-language analysis + Recipe suggestions
- Push notifications when a new forecast run is ingested
- Configurable percentile bands (p10/p90) as alternative to absolute min/max
