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
| `scheduler` | `ch1eps_interval_hours` | CH1-EPS collection interval (default: 3h) |
| `scheduler` | `ch2eps_interval_hours` | CH2-EPS collection interval (default: 6h) |

Never read `os.environ` directly in code — all configuration goes through `get_config()`.

---

## API Overview

No authentication. All endpoints are open — access is controlled at the network/container level.

### Forecast

| Method | Path | Description |
|---|---|---|
| GET | `/api/forecast/station` | Hourly blended station forecast — all variables below, probable + min + max, up to 120 h. Params: `lat`, `lon`, `elevation`, `hours` |
| GET | `/api/forecast/wind-grid` | 171-point Switzerland wind grid at 9 altitude levels including vertical wind. Params: `date` (YYYY-MM-DD), `level_m` |
| GET | `/accuracy` | Accuracy analysis GUI (browser) |
| GET | `/api/meta` | Returns Lenticularis base URL to the GUI |
| GET | `/health` | Service health + cache key counts |

### Recipes (v0.2)

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
| `humidity` | % | 2 m relative humidity |
| `pressure_qff` | hPa | Sea-level pressure (QFF reduction) |
| `precipitation` | mm/h | Total precipitation rate |
| `solar_direct` | W/m² | Direct shortwave radiation at surface |
| `solar_diffuse` | W/m² | Diffuse shortwave radiation at surface |
| `sunshine_minutes` | min/h | Minutes of sunshine in the hour (0–60) |
| `cloud_cover_total` | % | Total cloud cover |
| `cloud_cover_low` | % | Low cloud cover |
| `cloud_cover_mid` | % | Mid-level cloud cover |
| `cloud_cover_high` | % | High cloud cover |
| `cloud_base_convective` | m AGL | Height of convective cloud base (0 = none) |
| `boundary_layer_height` | m AGL | Planetary boundary layer height — thermal ceiling proxy |
| `freezing_level` | m ASL | Height of 0 °C isotherm |
| `cape` | J/kg | Mixed-layer CAPE — convective energy (0 = stable, >500 = significant) |
| `cin` | J/kg | Mixed-layer CIN — convective inhibition (negative) |

### Pressure levels (9 altitude bands: 500 / 800 / 1000 / 1500 / 2000 / 2500 / 3000 / 4000 / 5000 m ASL)

| Field | Unit | Description |
|---|---|---|
| `wind_speed` | m/s | Horizontal wind speed at altitude |
| `wind_direction` | degrees | Horizontal wind direction at altitude |
| `vertical_wind` | m/s | Vertical wind speed — positive = updraft, negative = downdraft/sink |

### Wind grid (per point, per hour, per altitude level)

`ws` / `ws_min` / `ws_max` — wind speed arrays  
`wd` / `wd_min` / `wd_max` — wind direction arrays  
`wv` / `wv_min` / `wv_max` — vertical wind arrays

---

## Data Sources

LSMFAPI ingests two MeteoSwiss high-resolution ensemble models downloaded via the [MeteoSwiss Open Data STAC API](https://data.geo.admin.ch/api/stac/v1/):

| Model | Resolution | Horizon | Runs/day | Members |
|---|---|---|---|---|
| ICON-CH1-EPS | 1.1 km | 0–30 h | 4 (00Z/06Z/12Z/18Z) | 11 |
| ICON-CH2-EPS | 2.2 km | 30–120 h | 2 (00Z/12Z) | 21 |

**Blending rule**: hours 0–30 from CH1-EPS (hourly, 1.1 km resolution); hours 33–120 from CH2-EPS (3h steps). Both models are cached independently and merged at read time — a CH1 re-run refreshes only the near-term slice; the CH2 long-range tail is unaffected, and vice versa.

See `docs/forecast-data-reference.md` for a plain-English explanation of what each variable means and how to interpret ensemble spread.

---

## Architecture

### Data flow

```
Container startup
  → Download grid coordinates (horizontal_constants GRIB2)
  → Build KD-tree for nearest-point lookup
  → Fetch station list from Lenticularis API
  → Trigger immediate collection run to warm in-memory cache

MeteoSwiss STAC API → GRIB2 files (one per variable per step)
  → cfgrib + xarray: decode all ensemble members
  → De-accumulate precipitation, radiation, sunshine
  → Compute RH from specific humidity + temperature + pressure
  → Ensemble engine: median, min, max across all members × runs
  → Precompute ForecastResponse for every known station
  → Store in separate CH1/CH2 in-memory dicts (keyed by station_id)

API routes
  → Dict lookup — no on-the-fly computation
  → Apply active Recipe corrections if any (v0.2)
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
├── scheduler.py             # APScheduler jobs
├── api/
│   ├── main.py              # FastAPI app factory + lifespan
│   └── routers/
│       ├── forecast.py      # GET /api/forecast/station + wind-grid
│       └── accuracy.py      # GET /accuracy (GUI) + /api/meta
├── collectors/
│   ├── base.py              # Abstract base + async download helper
│   ├── icon_ch1_eps.py      # ICON-CH1-EPS ingestor (0–30h, 11 members)
│   └── icon_ch2_eps.py      # ICON-CH2-EPS ingestor (30–120h, 21 members)
├── database/
│   ├── cache.py             # In-memory forecast cache (get/set station + grid)
│   ├── db.py                # init_db(), get_db(), _run_column_migrations()
│   └── models.py            # SQLAlchemy ORM (Recipe, RecipeRule — v0.2)
├── models/
│   └── forecast.py          # Pydantic schemas: ForecastResponse, GridResponse
└── services/
    └── ensemble.py          # Median + circular median + absolute min/max
static/
├── shared.css               # Dark theme
├── index.html + index.js    # Accuracy analysis GUI
```

### In-memory cache

Forecast data is held in Python in-process dicts — there is no time-series database. CH1 and CH2 data are stored in separate dicts and merged at read time: `get_station_forecast()` returns the CH1 hourly head (h0–h30) concatenated with the CH2 3h-step tail (h33–h120). Each collector only ever refreshes its own dict, so re-runs don't erase the other model's data.

The cache is populated on container startup and refreshed after every collection run. API calls are pure dict lookups + in-memory merge with no on-the-fly computation. The cache is persisted to `/app/data/cache.json` after each run and restored on restart.

All cache access goes through `database/cache.py` getter/setter functions so the backing store can be swapped to Redis later without touching router code.

### SQLite tables

| Table | Key columns |
|---|---|
| `recipes` | `id`, `name`, `station_id` (nullable = global), `description`, `active`, `created_at` |
| `recipe_rules` | `id`, `recipe_id` (FK), `variable`, `correction_type` (`additive`\|`multiplicative`), `value`, `condition_json` |

---

## Deployment

### Docker

```bash
docker-compose up
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up   # dev with live reload
```

The dev overlay mounts `src/` and `static/` as live volumes and adds Traefik labels for `lsmfapi-dev.lg4.ch`.

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
| `collect_ch1eps` | Every 3 h (±10 min jitter) | Downloads + parses latest CH1-EPS run, updates in-memory cache |
| `collect_ch2eps` | Every 6 h | Downloads + parses latest CH2-EPS run (30–120 h slice), updates cache |

---

## Roadmap

### v0.1 — MVP ✅ Shipped

- ICON-CH1-EPS + CH2-EPS collectors via MeteoSwiss STAC API
- Full variable set: winds, temperature, humidity, pressure, precipitation, radiation, cloud cover, boundary layer height, CAPE/CIN, freezing level, vertical wind at 9 altitude bands
- `GET /api/forecast/station` and `GET /api/forecast/wind-grid`
- Accuracy analysis GUI
- Docker + docker-compose + Traefik

### v0.2 — Dashboard + cache merge ✅ Shipped

- Operational dashboard with live collection status and cache health
- Data Inspector GUI
- Wind grid fully functional
- GitHub Actions Docker pipeline + remote deploy script
- CH1/CH2 cache merge: CH1 hourly head (h0–h30) + CH2 3h tail (h33–h120) served as a single blended response

### v0.3 — Recipes

- `Recipe` + `RecipeRule` SQLite models
- CRUD endpoints (`GET/POST/PUT/DELETE /api/recipes`)
- Recipe engine: apply additive/multiplicative corrections in `/api/forecast/station`
- Recipe editor GUI

### v0.4 — Enhancements

- Bilinear interpolation for smoother station-level values
- Statistical recipe suggestions (auto-compute mean bias from accuracy data)
- Local LLM integration (Ollama): accuracy + bias stats → natural-language analysis + Recipe suggestions
- Push notifications when a new forecast run is ingested
- Configurable percentile bands (p10/p90) as alternative to absolute min/max
