# Project Overview — LSMFAPI (Lenticularis SwissMeteo Forecast API)

## What This Is

LSMFAPI is a dedicated forecast ingestion and delivery service that replaces the OpenMeteo dependency in the Lenticularis paragliding weather decision-support app. It downloads raw ensemble model output (ICON-CH1-EPS, ICON-CH2-EPS) directly from the MeteoSwiss open data portal, computes statistically robust forecast summaries (median + absolute min/max across all members and runs), and exposes them to Lenticularis via a clean REST API. It also provides an internal English-only GUI for forecast accuracy analysis and recipe-based bias correction.

---

## Tech Stack

| Concern | Tool |
|---|---|
| Language | Python 3.11+ |
| Web framework | FastAPI |
| Data validation | Pydantic v2 |
| Dependency management | Poetry (`pyproject.toml`) |
| Forecast cache | Python in-process dict (populated on startup + after each collection run) |
| Relational DB | SQLite via SQLAlchemy (no Alembic — see backend conventions) |
| Scheduler | APScheduler |
| HTTP client | httpx (async) |
| Config | YAML (`config.yml`) validated by Pydantic |
| GRIB2 parsing | `cfgrib` + `xarray` + `eccodes` |
| Spatial math | `scipy` (KD-tree nearest-point lookup) |
| Frontend | Vanilla JS (English only — no i18n system) |
| Container | Docker + docker-compose |

---

## Repository Layout

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
│   ├── icon_ch1_eps.py      # ICON-CH1-EPS (0–33h) ingestor
│   └── icon_ch2_eps.py      # ICON-CH2-EPS (34–120h) ingestor
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
├── index.html + index.js    # Accuracy analysis GUI
├── recipes.html + recipes.js # Recipe editor (v0.2)
```

---

## Data Flow

```
Container startup
  → Fetch station list from Lenticularis API
  → Trigger immediate collection run to warm the cache

MeteoSwiss open data portal (GRIB2 files)
  → Collectors download + parse (cfgrib + xarray)
  → Ensemble engine: median, min, max across all members × runs
  → Compute ForecastResponse for every known station
  → Store in in-memory dict (keyed by lat_lon_elev)

API routes
  → Dict lookup (no computation)
  → Apply active Recipe corrections (if any)
  → Return JSON to Lenticularis

Accuracy GUI (browser)
  → Fetches actuals + historical forecasts from Lenticularis directly
  → Renders bias charts + RMSE summary table
```

---

## Data Sources

| Source | Format | Variables | Horizon | Runs/day | Members |
|---|---|---|---|---|---|
| ICON-CH1-EPS | GRIB2 | See variables below | 0–33h (1h steps) | 4 (00Z/06Z/12Z/18Z) | 11 |
| ICON-CH2-EPS | GRIB2 | See variables below | 34–120h (1h steps) | 4 (00Z/06Z/12Z/18Z) | 21 |

**Forecast Variables**: wind speed (10m), wind gusts (10m), wind direction (10m), temperature (2m), relative humidity, QFF pressure, precipitation, pressure-level winds at 9 altitude bands (500m/800m/1000m/1500m/2000m/2500m/3000m/4000m/5000m ASL).

**Blending rule**: h0–h33 from CH1-EPS (hourly, 1km resolution); h34–h120 from CH2-EPS (hourly, 2.1km resolution). CH1 always wins for any overlapping valid_time. CH2 shadow-fetches h33 at collection time purely as the deaccumulation baseline for accumulated variables. Both collectors run 4×/day at 00Z/06Z/12Z/18Z — CH1 triggered at +2h, CH2 at +3h to allow for MeteoSwiss publication lag.

---

## Lenticularis Integration

LSMFAPI is a drop-in replacement for Lenticularis's two OpenMeteo collectors:
- `forecast_openmeteo.py` → calls `GET /api/forecast/station`
- `forecast_grid.py` → calls `GET /api/forecast/wind-grid`

Response shapes match Lenticularis's `ForecastPoint` and `GridForecastPoint` models. Lenticularis owns historical data archiving; LSMFAPI stores only the active 7-day forecast window.

---

## Local LLM (v0.3 — not yet implemented)

A local LLM instance (e.g. Ollama) is available in the infrastructure. Planned use: feed accuracy data and bias statistics to the LLM to get natural-language analysis and Recipe correction suggestions. Lenticularis's `api/routers/ai.py` has an existing pattern for this.
