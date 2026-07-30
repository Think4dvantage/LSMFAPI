# LSMFAPI — Lenticularis SwissMeteo Forecast API

LSMFAPI is a dedicated forecast ingestion and delivery service that replaces the OpenMeteo dependency in the Lenticularis paragliding weather decision-support app. It downloads raw ensemble model output (ICON-CH1-EPS, ICON-CH2-EPS) directly from the MeteoSwiss open data portal, computes statistically robust forecast summaries (median + absolute min/max across all members and runs), and exposes them to Lenticularis via a REST API. It also serves an internal English-only operational dashboard and a Data Inspector GUI.

---

## Prerequisites

- Python 3.11+
- [Poetry](https://python-poetry.org/) for dependency management
- Docker + docker-compose

No system GRIB library is needed. `poetry install` pulls the `eccodeslib` wheel, which ships
`libeccodes.so`. **Do not install `libeccodes-dev`** — a system copy can only mismatch the
Python binding (see the eccodes note under Architecture).

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

All configuration lives in `config.yml` (gitignored — copy from `config.yml.example`).

| Section | Key | Description |
|---|---|---|
| `meteoswiss` | `stac_base_url` | MeteoSwiss STAC API base (default: `https://data.geo.admin.ch/api/stac/v1`) |
| `meteoswiss` | `ch1eps_collection` | ICON-CH1-EPS collection ID |
| `meteoswiss` | `ch2eps_collection` | ICON-CH2-EPS collection ID |
| `lenticularis` | `base_url` | Lenticularis API base URL (station list) |
| — | `grib_cache_dir` | Where downloaded GRIB2 files persist (default: `/tmp/lsmfapi_grib`) — bind-mount this onto a volume with headroom, a CH2 run can peak in the hundreds of GB |

Unknown keys are rejected at startup (`extra="forbid"`), and a missing `config.yml` fails fast
with a clear log line rather than booting "healthy" with no config. Never read `os.environ`
directly in code — all configuration goes through `get_config()`.

---

## API Overview

No authentication. All endpoints are open — access is controlled at the network/container level.

### Forecast

| Method | Path | Description |
|---|---|---|
| GET | `/api/forecast/station` | Hourly blended station forecast — surface variables below, probable + min + max, up to 120 h. Params: `station_id`, `hours` |
| GET | `/api/forecast/altitude-winds` | Hourly pressure-level wind forecast at 9 altitude bands (500–5000 m ASL). Params: `station_id`, `hours` |
| GET | `/api/forecast/wind-grid` | ~1 km Switzerland wind grid (ws / wd / surface RH) at one altitude level. Params: `level_m`, `bbox`, `stride_km` |
| GET | `/api/forecast/thermal-grid` | ~1 km thermal/convection grid (solar, sunshine, cloud covers, freezing level, CAPE, CIN, LCL, LFC, TKE). Params: `bbox`, `stride_km` |
| GET | `/api/stations` | Proxy to Lenticularis station list (CORS-safe) |
| GET | `/data` | Data Inspector GUI — query station, altitude-wind, and thermal-grid endpoints |
| GET | `/health` | Real subsystem checks (SQLite, scheduler, cache) — `503` if SQLite is unreachable or the scheduler is stopped; a merely stale cache is not an error |
| GET | `/dashboard` | Operational dashboard: live collection status, cache state, error log (aggregated, not a raw feed) |

**Grid response limits.** `/wind-grid` and `/thermal-grid` return one value list per point per frame, so a fine `stride_km` over the full domain can be enormous. Requests exceeding 10 million values (`points × frames × fields`) are rejected with `400 response_too_large` — increase `stride_km` or request a smaller `bbox`. Accepted `stride_km`: 1, 2, 5, 10 (default 10). `bbox` is `lat_min,lat_max,lon_min,lon_max` within the ICON-CH1 domain.

### Recipes (v0.4)

| Method | Path | Description |
|---|---|---|
| GET | `/api/recipes` | List all recipes |
| POST | `/api/recipes` | Create a recipe |
| PUT | `/api/recipes/{id}` | Update a recipe |
| DELETE | `/api/recipes/{id}` | Delete a recipe |

---

## Forecast Variables

Each variable carries `{ <value>, <value>_min, <value>_max }` — ensemble median and absolute min/max across all members and all model runs blended for the forecast window.

### Station surface (per hour) — `/api/forecast/station`

The station response carries surface weather only. Solar, cloud, and convection fields are **not** in the station response — they are served spatially via `/api/forecast/thermal-grid`.

| Field | Unit | Description |
|---|---|---|
| `wind_speed` | km/h | 10 m wind speed |
| `wind_gust` | km/h | 10 m wind gusts (max in step) |
| `wind_direction` | degrees | 10 m wind direction (0/360 = N). `_min`/`_max` use circular statistics, not plain min/max — members straddling 0°/360° report their true spread, not a false ~360° one |
| `temperature` | °C | 2 m air temperature |
| `humidity` | % | 2 m relative humidity (computed from TD_2M via Magnus formula) |
| `pressure_qff` | hPa | Sea-level pressure (QFF reduction) |
| `precipitation` | mm | Precipitation in the hour (de-accumulated) |

### Altitude winds (9 bands: 500 / 800 / 1000 / 1500 / 2000 / 2500 / 3000 / 4000 / 5000 m ASL) — `/api/forecast/altitude-winds`

| Field | Unit | Description |
|---|---|---|
| `wind_speed` | km/h | Horizontal wind speed at altitude |
| `wind_direction` | degrees | Horizontal wind direction at altitude |
| `vertical_wind` | m/s | Vertical wind speed — positive = updraft, negative = downdraft/sink |

### Thermal / convection grid — `/api/forecast/thermal-grid`

Ensemble median (+ `_min` / `_max`) per grid point per hour, sampled on a ~1 km grid:

| Field | Unit | Description |
|---|---|---|
| `solar` | W/m² | Total incoming solar (direct + diffuse, de-accumulated) |
| `sunshine` | min/h | Minutes of sunshine in the hour |
| `cloud_cover` / `cloud_low` / `cloud_mid` / `cloud_high` | % | Total / low / mid / high cloud cover |
| `freezing_level` | m ASL | Height of 0 °C isotherm |
| `cape` | J/kg | Mixed-layer CAPE — convective energy (0 = stable, >500 = significant) |
| `cin` | J/kg | Mixed-layer CIN — convective inhibition (null = ICON fill value) |
| `lcl` | m | Lifted condensation level (cloud-base proxy) |
| `lfc` | m | Level of free convection |
| `tke` | J/kg | Turbulent kinetic energy (boundary-layer turbulence) |

### Wind grid — `/api/forecast/wind-grid`

Ensemble-median `ws` (km/h) + `wd` (degrees) at one requested altitude level, plus surface `rh` (%), sampled on the same ~1 km grid.

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
  → Persistent GRIB cache: {grib_cache_dir}/{model}/{ref_dt}/ (skip re-downloads on restart)
  → Raw eccodes API: decode all ensemble members (no cfgrib/xarray — removed, unused)
  → De-accumulate precipitation, radiation, sunshine
  → Compute RH from TD_2M (dew point) + T_2M via Magnus formula
  → Ensemble engine: median, min, max across all members × runs
  → Precompute station forecast + altitude winds for every known station
  → Sample wind-grid + thermal-grid onto a ~1 km grid (float16)
  → Store: separate CH1/CH2 station+altitude dicts; one combined 121-frame grid store
  → save_cache(): persist to /app/data/cache.json + grid_cache.npz + thermal_grid_cache.npz

API routes
  → Dict lookup / contiguous grid view — no on-the-fly computation
  → Apply active Recipe corrections if any (v0.4)
  → Return JSON to Lenticularis
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
│       ├── forecast.py      # GET /api/forecast/station + altitude-winds + wind-grid + thermal-grid
│       └── dashboard.py     # GET /dashboard + /api/dashboard + /data (Data Inspector) + /api/stations proxy
├── collectors/
│   ├── base.py              # Abstract base + async download helper
│   ├── grib_cache.py        # grib_run_dir() context manager; persistent GRIB files, configurable dir
│   ├── _icon_eps_base.py    # Shared STAC/GRIB/ensemble pipeline (CH1/CH2 are 81% identical code)
│   ├── icon_ch1_eps.py      # ICON-CH1-EPS: thin subclass (h0–h33, 1h steps, ~10 members)
│   └── icon_ch2_eps.py      # ICON-CH2-EPS: thin subclass (h34–h120, 1h steps, ~21 members)
├── database/
│   ├── cache.py             # In-memory cache: station + altitude dicts + combined float16 grid store
│   ├── collection_state.py  # Runtime collection state (status, files_done, files_ok)
│   ├── telemetry.py         # HTTP + download error log (last 20 errors → dashboard)
│   ├── db.py                # init_db(), get_db(), _run_column_migrations()
│   └── models.py            # SQLAlchemy ORM (Recipe, RecipeRule — v0.4)
├── models/
│   └── forecast.py          # Pydantic + dataclass schemas incl. GridWindCache / ThermalGridCache
└── services/
    └── ensemble.py          # Median + circular median + absolute min/max
static/
├── shared.css               # Dark theme
├── dashboard.html + dashboard.js   # Operational dashboard
├── data.html + data.js      # Data Inspector (station / altitude-wind / thermal-grid)
```

### In-memory cache

Forecast data is held in Python in-process dicts — there is no time-series database. CH1 and CH2 data are stored in separate dicts and merged at read time: `get_station_forecast()` returns the CH1 hourly head (h0–h33, 1h steps) concatenated with the CH2 hourly tail (h34–h120, 1h steps). Each collector only ever refreshes its own dict, so re-runs don't erase the other model's data.

The cache is populated on container startup and refreshed after every collection run. API calls are pure dict lookups + in-memory merge with no on-the-fly computation. The cache is persisted to `/app/data/cache.json` after each run and restored on restart, so the API serves data immediately while the background warm-up runs.

All cache access goes through `database/cache.py` getter/setter functions so the backing store can be swapped to Redis later without touching router code.

### Grid cache — combined store, float16

The wind grid and thermal grid are pre-sampled onto a fixed ~1 km grid over Switzerland (~234 × 523 ≈ 122 k points) across the full 121-frame horizon axis (h0–h120). To keep RAM bounded:

- **One combined store per grid** (not separate CH1/CH2 caches). Each collector writes its own contiguous horizon slice **in place** — CH1 → rows 0–33, CH2 → rows 34–120 — and reads return a contiguous numpy **view** (zero copy). No `concatenate` merge on the request path.
- **float16 field arrays.** Grid values are ensemble medians for map rendering; float16 has far finer resolution than the data's real accuracy and halves resident grid memory (`lats`/`lons` stay float32).
- Persisted as single `grid_cache.npz` / `thermal_grid_cache.npz`; legacy per-model files are removed on load.

Together with the grid response cap, this keeps peak service RAM around ~2 GB (previously it could spike past 8 GB and OOM).

### GRIB file persistence

GRIB files are stored in `{grib_cache_dir}/{model}/{YYYYMMDDTHHMMZ}/` (default
`/tmp/lsmfapi_grib`, configurable — bind-mount it onto a volume with headroom, not the
container's writable layer). Files survive container restarts: if the `ref_dt` hasn't
changed, previously downloaded files are reused. When the `ref_dt` advances (new model run),
old directories are deleted automatically on the next collector start. Corrupt files (eccodes
parse failure) are deleted immediately so they are re-downloaded on the next run. Startup logs
the resolved directory and warn if the volume has less than 550 GB free — a CH2 run alone can
peak in the hundreds of GB there today (a known, tracked inefficiency — see Roadmap).

### eccodes stack

Three parts must stay in step, and all three come from `poetry.lock` — nothing from the system:

| Part | Package | Role |
|---|---|---|
| Python binding | `eccodes` 2.47.0 | Calls into the C library |
| C library | `eccodeslib` 2.47.3.23 | Decodes the GRIB (`lib64/libeccodes.so`) |
| ICON definitions | `eccodes-cosmo-resources-python` 2.44.0.1 | Teaches ecCodes the ICON shortNames |

**The library must never be older than the definitions.** If it is, ecCodes aborts the process on
the first GRIB parse — no Python traceback, PID 1 dies, and the container restart-loops. That is
what took PRD down on v0.3.3.

Never install a system libeccodes (apt, conda, `LD_LIBRARY_PATH`). `findlibs` resolves an
installed package ahead of every system path, so a second copy can only mismatch the binding.
`eccodeslib` must stay declared explicitly in `pyproject.toml`: the `eccodes` wheel depends on it,
but PyPI's JSON metadata — which Poetry reads — omits it, so Poetry otherwise skips it silently.

To confirm which library is live, check the startup log: the vendor half of the definitions path
must sit inside `site-packages/eccodeslib/`. `/usr/share/eccodes/definitions` means a system
library leaked in.

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

### v0.3.1 — Production fixes ✅ Shipped

- Traefik label isolation (PRD/DEV no longer cross-route)
- Scheduler warm-up/cron race fixed with per-model `asyncio.Lock`
- Attempted a CI eccodes fix (conda-forge eccodes 2.38 via Miniforge) that never worked — the integration test did not run at all until v0.3.4

### v0.3.2 — Thermal forecast grid ✅ Shipped

- `GET /api/forecast/thermal-grid` — solar, sunshine, cloud covers, freezing level, CAPE, CIN, LCL, LFC, TKE on a ~1 km grid; `LCL_ML`/`LFC_ML`/`TKE` added to `SURFACE_VARS`
- `ThermalGridCache` with `.npz` persistence; dashboard + Data Inspector cards
- Accuracy GUI removed; `accuracy.py` merged into `dashboard.py` (`/data` + `/api/stations`)

### v0.3.3 — Grid memory reduction ✅ Shipped

- **float16 grid storage** — halves resident grid memory (~3.3 GB → ~1.6 GB)
- **Combined single-array grid store** — collectors write horizon slices in place; reads are contiguous views (no per-request merge copy)
- **Grid response budget cap** (10 M values) + plain-dict response build — blocks fine-`stride_km` full-bbox requests that could allocate 10+ GB of Python floats
- Net: peak RAM ~8–16 GB (OOM at 8 GB) → ~2 GB, with faster reads

### v0.3.4 — eccodes stack pinned ✅ Shipped

Fixes a PRD crash loop and a 10-week CI outage that shared one root cause: there was no
`poetry.lock`, so every build resolved dependencies fresh and drifted under fixed tags.

- **PRD crash loop** — the v0.3.3 image restarted every ~15 s. `python:3.11-slim` had rolled bookworm → trixie (apt libeccodes 2.28 → 2.41.0), `eccodes` floated to 2.47.0 (which moved its C library into the separate `eccodeslib` package), and the COSMO definitions floated to 2.44.0.1. Poetry resolves from PyPI JSON metadata that omits the `eccodeslib` dependency, so it was never installed and `findlibs` fell back to apt's 2.41.0 — definitions newer than the library, which makes ecCodes abort the process on the first GRIB parse (no traceback, PID 1 dies).
- **CI red since 2026-05-08** — the v0.3.1 conda step could never solve and failed in ~26 s, so the integration test never ran. A green run would have caught the mismatch before the image shipped.
- **Fixes** — `poetry.lock` committed and now mandatory in the Dockerfile (no `*` glob); `eccodeslib` declared explicitly (`platform_system != 'Windows'`); apt `libeccodes-dev` and the conda CI step removed, leaving exactly one libeccodes. Pinned: `eccodes` 2.47.0 + `eccodeslib` 2.47.3.23 + `eccodes-cosmo-resources-python` 2.44.0.1, verified against real ICON GRIB by the integration test.

### v0.3.5 — Grid build off the event loop ✅ Shipped

- `collect_grid()` ran synchronously inside `async def collect()`, holding the event loop for the whole grid build — uvicorn served nothing, `/health` included, so the healthcheck failed and Traefik dropped the container. Measured on PRD: a 7m48s silent gap; CH2 (87 horizons) is ~20 min. Roughly 1.5–2h of dead UI per day across the 8 collection windows.
- Fixed with `await asyncio.to_thread(self.collect_grid, ...)` in both collectors — eccodes and numpy release the GIL, so the API keeps serving during collection.
- Latent since the grid feature landed; only visible once v0.3.4 let a collection finish instead of crash-looping.

### v0.3.6 — Altitude winds fixed ✅ Shipped

Altitude winds (and the wind grid at every level) had been silently wrong since launch: the
EPS `U`/`V`/`W` files carry ~80 raw model levels with no pressure coordinate, so the old
altitude→pressure mapping collapsed every band onto the same array index — all-null or
identical winds at every height. Fixed by interpolating to true geometric height (MAMSL) using
the static HHL constants instead. Same 9-band API contract, now genuinely correct and distinct
per height.

### v0.3.7 — CH2 scheduler misfire fixed ✅ Shipped

APScheduler's default misfire grace time was tight enough that a few seconds of executor
jitter (CH2's own long collection run perturbing the loop) made it skip the trigger outright
rather than run late — leaving the dashboard showing a stale CH2 cache for up to 6 hours with
no error anywhere. Fixed with a 30-minute misfire grace time; safe because the target run is
always computed from wall-clock time, not the cron slot that fired it.

### v0.3.8 – v0.3.39 — Tech-debt remediation pass ✅ Shipped

A full audit (`specs/001-tech-debt-remediation/plan.md`) covering security, reliability, CI,
and code quality, worked through top to bottom:

- **Security**: stored XSS in the operator dashboard fixed; TLS verification restored on all
  4 HTTPS clients.
- **Reliability**: cache persistence and GRIB parsing/station-building moved off the event
  loop (nothing blocks `/health` anymore); `/health` does real subsystem checks and can return
  `503`; the dashboard's error log aggregates instead of drowning in repeat noise; unhandled
  exceptions are caught, logged, and surfaced instead of vanishing; a STAC search edge case
  that could stitch two model runs together is closed; two silent-fallback bugs fixed (one a
  ~34× precipitation overstatement risk on a rare failure path).
- **Config**: `config.yml` untracked from git (was accidentally committed); unknown config
  keys now rejected at startup instead of silently ignored; the GRIB cache directory is now
  configurable with a startup low-space warning.
- **CI/quality**: releases are now gated on a green integration test (previously a tag push
  could publish on a red test); added a fast unit-test lane and linting (previously
  unenforced); added Dependabot; pinned the Docker base image and Poetry version; removed
  unused dependencies.
- **Structural**: the two collectors were 81% duplicate code — extracted into one shared base
  class, cutting each collector down to its genuinely model-specific ~200 lines.

Two items were investigated and deliberately deferred rather than rushed: fully eliminating
the GRIB cache's multi-hundred-GB disk peak (the safe fix requires interleaving grid
computation with the download phase — a bigger rewrite than a quick patch, see `.ai/RESUME.md`
for the full reasoning), and reducing STAC search call volume by ~34–87× (requires
restructuring the per-variable/horizon fetch pattern).

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
