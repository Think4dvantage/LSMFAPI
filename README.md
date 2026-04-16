# LSMFAPI — Lenticularis SwissMeteo Forecast API

LSMFAPI is a dedicated forecast ingestion and delivery service that replaces the OpenMeteo dependency in the Lenticularis paragliding weather decision-support app. It downloads raw ensemble model output (ICON-CH1-EPS, ICON-CH2-EPS) directly from the MeteoSwiss open data portal, computes statistically robust forecast summaries (median + absolute min/max across all members and runs), and exposes them to Lenticularis via a REST API. It also provides an internal English-only GUI for forecast accuracy analysis and recipe-based bias correction.

---

## Prerequisites

- Python 3.11+
- [Poetry](https://python-poetry.org/) for dependency management
- `eccodes` system library (required by `cfgrib` for GRIB2 parsing)
- Docker + docker-compose

---

## Local Setup

```bash
git clone <repo>
cd LSMFAPI
poetry install
cp config.yml.example config.yml   # then fill in your values
docker-compose up
```

The API will be available at `http://localhost:8000`.

---

## Configuration

All configuration lives in `config.yml` (gitignored). Use `config.yml.example` as the template. Key sections:

| Section | Key | Description |
|---|---|---|
| `meteoswiss` | `ch1eps_url` | Base URL for ICON-CH1-EPS GRIB2 downloads |
| `meteoswiss` | `ch2eps_url` | Base URL for ICON-CH2-EPS GRIB2 downloads |
| `lenticularis` | `base_url` | Lenticularis API base URL (station list + accuracy GUI) |
| `scheduler` | intervals, jitter | Job trigger intervals for collectors |

Never read `os.environ` directly in code — all configuration goes through `get_config()`.

---

## API Overview

No authentication. All endpoints are open — access is controlled at the network/container level.

### Forecast

| Method | Path | Description |
|---|---|---|
| GET | `/api/forecast/station` | Hourly blended station forecast: probable + min + max per variable, up to 120 h. Params: `lat`, `lon`, `elevation`, `hours` |
| GET | `/api/forecast/wind-grid` | 171-point Switzerland wind grid at 9 altitude levels. Params: `date` (YYYY-MM-DD), `level_m` |

### Recipes (v0.2)

| Method | Path | Description |
|---|---|---|
| GET | `/api/recipes` | List all recipes |
| POST | `/api/recipes` | Create a recipe |
| PUT | `/api/recipes/{id}` | Update a recipe |
| DELETE | `/api/recipes/{id}` | Delete a recipe |

---

## Data Sources

LSMFAPI ingests two MeteoSwiss ensemble models:

| Model | Format | Horizon | Runs/day | Members |
|---|---|---|---|---|
| ICON-CH1-EPS | GRIB2 | 0–30 h | 4 (00Z/06Z/12Z/18Z) | ~21 |
| ICON-CH2-EPS | GRIB2 | 30–120 h | 2 (00Z/12Z) | ~21 |

**Blending rule**: hours 0–30 use CH1-EPS (higher resolution); hours 30–120 use CH2-EPS.

**Forecast variables**: wind speed (10 m), wind gusts (10 m), wind direction (10 m), temperature (2 m), relative humidity, QFF pressure, precipitation, and pressure-level winds at 9 altitude bands (500 / 800 / 1000 / 1500 / 2000 / 2500 / 3000 / 4000 / 5000 m ASL).

---

## Architecture

### Data flow

```
Container startup
  → Fetch station list from Lenticularis API
  → Trigger immediate collection run to warm the in-memory cache

MeteoSwiss open data portal (GRIB2 files)
  → Collectors download + parse (cfgrib + xarray)
  → Ensemble engine: median, min, max across all members × runs
  → Precompute ForecastResponse for every known station
  → Store in in-memory dict (keyed by lat_lon_elev)

API routes
  → Dict lookup — no on-the-fly computation
  → Apply active Recipe corrections (if any)
  → Return JSON to Lenticularis

Accuracy GUI (browser)
  → Fetches actuals + historical forecasts from Lenticularis directly
  → Renders bias charts + RMSE summary table
```

### Repository layout

```
src/lsmfapi/
├── api/
│   ├── main.py              # FastAPI app factory + lifespan
│   └── routers/             # One file per domain
│       ├── forecast.py      # GET /api/forecast/station
│       ├── wind_grid.py     # GET /api/forecast/wind-grid
│       ├── recipes.py       # CRUD /api/recipes (v0.2)
│       └── accuracy.py      # GET /api/accuracy/* (GUI data)
├── collectors/
│   ├── base.py              # Abstract base + download helpers
│   ├── icon_ch1_eps.py      # ICON-CH1-EPS (30h) ingestor
│   └── icon_ch2_eps.py      # ICON-CH2-EPS (120h) ingestor
├── database/
│   ├── models.py            # SQLAlchemy ORM (Recipe, RecipeRule)
│   ├── db.py                # init_db(), get_db(), _run_column_migrations()
│   └── cache.py             # In-memory forecast cache (get/set station + grid)
├── models/                  # Pydantic request/response schemas
│   ├── forecast.py
│   └── recipe.py
├── services/
│   ├── ensemble.py          # Median + absolute min/max across members × runs
│   ├── interpolation.py     # KD-tree nearest-point + bilinear (v0.2)
│   └── recipe_engine.py     # Apply recipe corrections (v0.2)
├── config.py                # Pydantic-validated YAML config loader (singleton)
└── scheduler.py             # APScheduler jobs
static/
├── shared.css               # Dark theme
├── auth.js                  # JWT storage, fetchAuth()
├── index.html + index.js    # Accuracy analysis GUI
└── recipes.html + recipes.js # Recipe editor (v0.2)
```

### In-memory cache

Forecast data is held in a Python in-process dict — there is no time-series database. The cache is populated on container startup and refreshed after every collection run. API calls are pure dict lookups.

- Station cache key: `"{lat}_{lon}_{elev}"` → `ForecastResponse`
- Grid cache key: `"{YYYY-MM-DD}_{level_m}"` → `GridResponse`

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
docker-compose up          # production
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up   # dev overlay
```

The dev overlay adds:
- Live volume mounts for `src/` and `static/` (`:ro,z`)
- Traefik labels for `lsmfapi-dev.lg4.ch`
- `PYTHONPYCACHEPREFIX=/tmp/pycache` to prevent stale `.pyc` files

### Traefik labels

This homelab requires list format, not map format:

```yaml
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.lsmfapi.rule=Host(`lsmfapi.lg4.ch`)"
```

When the container is on multiple Docker networks, add `traefik.docker.network=proxy`.

### Healthcheck

`python:3.11-slim` does not include `curl`. Use the Python stdlib:

```yaml
healthcheck:
  test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/')\""]
```

---

## Development Notes

**No Alembic.** Schema migrations use raw `ALTER TABLE` inside `_run_column_migrations()` in `db.py`. New tables are created automatically via `Base.metadata.create_all()`. New columns on existing tables must be added with an idempotent `ALTER TABLE` checked against `PRAGMA table_info`.

**QFF only.** All pressure fields are named `pressure_qff` everywhere (API responses, Pydantic schemas, UI labels). Never use QNH.

**No npm / no build step.** The frontend is plain HTML + vanilla JS served from `static/`. Do not introduce any bundler or `package.json`.

**English-only GUI.** The accuracy and recipe GUIs are internal operator tools. No i18n system, no locale files, no language picker — all strings are hardcoded in English.

**Scheduler jobs.**

| Job | Trigger | Description |
|---|---|---|
| `collect_ch1eps` | Every 3 h (±10 min jitter) | Downloads + parses latest CH1-EPS run, updates in-memory cache |
| `collect_ch2eps` | Every 6 h | Downloads + parses latest CH2-EPS run (30–120 h slice), updates cache |

---

## Roadmap

### v0.1 — MVP (current target)

- Fetch station list from Lenticularis API on startup; warm in-memory cache immediately
- ICON-CH1-EPS and ICON-CH2-EPS collectors (GRIB2 download, cfgrib parsing, ensemble stats, precompute per station)
- `GET /api/forecast/station` — blended hourly station forecast (cache lookup)
- `GET /api/forecast/wind-grid` — 171-point Switzerland wind grid at 9 altitude levels (cache lookup)
- APScheduler jobs for collection (every 3 h / 6 h)
- Accuracy analysis GUI (read-only: station picker + date range → bias charts + RMSE table)
- Docker + docker-compose (base + dev overlay), Traefik labels

### v0.2 — Recipes

- `Recipe` + `RecipeRule` SQLite models
- CRUD endpoints (`GET/POST/PUT/DELETE /api/recipes`)
- Recipe engine: apply additive/multiplicative corrections transparently in `/api/forecast/station`
- Recipe editor GUI: per-station bias table → define correction rules → save

### v0.3 — Enhancements

- Bilinear interpolation for smoother station-level values
- Statistical recipe suggestions (auto-compute mean bias from accuracy data)
- Local LLM integration (Ollama): accuracy + bias stats → natural-language analysis + Recipe suggestions
- Push notifications when a new forecast run is ingested
- Configurable percentile bands (p10/p90) as alternative to absolute min/max
