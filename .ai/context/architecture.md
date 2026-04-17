# Architecture Reference

## In-Memory Forecast Cache

All computed forecast data is held in a Python in-process dict. There is no time-series database.

### Station cache

```python
# database/cache.py
_station_cache: dict[str, ForecastResponse] = {}
# key: "{lat}_{lon}_{elev}"  e.g. "46.68_7.86_580"
```

- Populated after every collection run (CH1-EPS every 3 h, CH2-EPS every 6 h)
- Also populated once at container startup (immediate collection run triggered in lifespan)
- A cache entry is a fully computed `ForecastResponse` — API calls are pure dict lookups, no on-the-fly computation
- Station list is fetched from the Lenticularis API on startup and refreshed before each collection run

### Wind grid cache

```python
_grid_cache: dict[str, GridResponse] = {}
# key: "{YYYY-MM-DD}_{level_m}"  e.g. "2024-06-01_2000"
```

- Same lifecycle as station cache
- 171 grid points × 9 altitude levels

### Altitude winds cache

```python
_altitude_winds_cache: dict[str, AltitudeWindsResponse] = {}
# key: station_id
```

Pressure-level wind data (U/V/W at 9 altitude bands) is stored separately from the surface forecast.
Populated alongside `_station_cache` after each collection run.

### Cache module interface

```python
# database/cache.py
def get_station_forecast(station_key: str) -> ForecastResponse | None: ...
def set_station_forecast(station_key: str, data: ForecastResponse) -> None: ...
def get_station_altitude_winds(station_key: str) -> AltitudeWindsResponse | None: ...
def set_station_altitude_winds(station_key: str, data: AltitudeWindsResponse) -> None: ...
def get_grid_forecast(date: str, level_m: int) -> GridResponse | None: ...
def set_grid_forecast(date: str, level_m: int, data: GridResponse) -> None: ...
def save_cache() -> None: ...   # atomic write to /app/data/cache.json
def load_cache() -> None: ...   # restore all caches from /app/data/cache.json on startup
def cache_stats() -> dict: ...  # keys count, last_populated_at — for health/debug
```

All reads and writes go through these functions so the backing store can be swapped (e.g. to Redis) without touching router code.

### Cache persistence

`/app/data/cache.json` is written after every successful collection and on graceful shutdown.
On container startup, `load_cache()` restores all three caches before the scheduler fires —
the API serves stale-but-valid data immediately while the background warm-up runs.
Volume mount: `./data:/app/data` (in both compose files). The `./data` directory is excluded
from the rsync deploy so the remote cache is never overwritten by a deploy.

---

## SQLite Tables

| Table | Key columns |
|---|---|
| `recipes` | `id`, `name`, `station_id` (nullable — NULL = global), `description`, `active`, `created_at` |
| `recipe_rules` | `id`, `recipe_id` (FK → recipes), `variable`, `correction_type` (additive\|multiplicative), `value`, `condition_json` |

SQLite is used exclusively for relational data (recipes). Forecast data is never written here. There is no users table — the service is unauthenticated.

[Document every table here as it is added. This is the source of truth for the data model.]

---

## MeteoSwiss Open Data — STAC API

### Base endpoint

```
https://data.geo.admin.ch/api/stac/v1/
```

### Collection IDs

| Model | Collection ID |
|---|---|
| ICON-CH1-EPS | `ch.meteoschweiz.ogd-forecasting-icon-ch1` |
| ICON-CH2-EPS | `ch.meteoschweiz.ogd-forecasting-icon-ch2` |

### File discovery — STAC search

Files are discovered at runtime via a `POST` to the search endpoint. There is no static filename pattern to construct.

```
POST https://data.geo.admin.ch/api/stac/v1/search
Content-Type: application/json

{
  "collections": ["ch.meteoschweiz.ogd-forecasting-icon-ch1"],
  "forecast:reference_datetime": "2025-03-12T12:00:00Z",
  "forecast:variable": "U_10M",
  "forecast:perturbed": true,
  "forecast:horizon": "P0DT02H00M00S"
}
```

Response contains `features[].assets` with `href` (pre-signed download URL for a GRIB2 file).

Key search parameters:
- `forecast:reference_datetime` — run initialisation time (ISO 8601 UTC)
- `forecast:variable` — ICON variable name (see table below)
- `forecast:perturbed` — `false` = control run, `true` = ensemble perturbations
- `forecast:horizon` — lead time as ISO 8601 duration (e.g. `P0DT06H00M00S` = +6h)

**Data availability window**: 24 hours from publication. Scheduler intervals (3h / 6h) are well within this window.

### ICON variable names

**Note**: ICON uses non-standard GRIB2 shortNames. The `eccodes-cosmo-resources` definitions package is required for cfgrib to decode them correctly.

Legend — Accum: field is accumulated from model start; must be de-accumulated to per-hour values by differencing consecutive steps.

#### Surface / 2D fields

| Physical variable | ICON name | Accum | Notes |
|---|---|---|---|
| Wind U-component (10m) | `U_10M` | no | Combine with V → speed + direction |
| Wind V-component (10m) | `V_10M` | no | |
| Wind gusts (10m) | `VMAX_10M` | no | Max gust since last output step |
| Temperature (2m) | `T_2M` | no | Kelvin |
| Dew point temperature (2m) | `TD_2M` | no | Used to compute RH — see below |
| QFF pressure | `PMSL` | no | Sea-level reduced (QFF convention) |
| Precipitation | `TOT_PREC` | **yes** | De-accumulate to mm/h |
| Sunshine duration | `DURSUN` | **yes** | De-accumulate; convert s → min/h |
| Direct SW radiation | `ASWDIR_S` | **yes** | De-accumulate → W/m² mean over hour |
| Diffuse SW radiation | `ASWDIFD_S` | **yes** | De-accumulate → W/m² mean over hour |
| Total cloud cover | `CLCT` | no | % (0–100) |
| Low cloud cover | `CLCL` | no | % |
| Medium cloud cover | `CLCM` | no | % |
| High cloud cover | `CLCH` | no | % |
| Convective cloud base height | `HBAS_CON` | no | m AGL; 0 when no convective cloud |
| Boundary layer height | `HPBL` | no | m AGL — thermal ceiling proxy |
| Freezing level | `HZEROCL` | no | m ASL height of 0 °C isotherm |
| Mixed-layer CAPE | `CAPE_ML` | no | J/kg — convective energy (0 = stable) |
| Mixed-layer CIN | `CIN_ML` | no | J/kg — convective inhibition (negative) |

#### Pressure-level / 3D fields (typeOfLevel = isobaricInhPa)

| Physical variable | ICON name | Notes |
|---|---|---|
| Wind U-component | `U` | → compute speed + direction |
| Wind V-component | `V` | |
| Vertical wind speed | `W` | m/s; positive = updraft |

#### De-accumulation

For accumulated fields (`TOT_PREC`, `DURSUN`, `ASWDIR_S`, `ASWDIFD_S`), convert to per-step rates:

```python
# arr shape: (members, steps, lat, lon)
rates = np.diff(arr, axis=1, prepend=0)  # step 0 value is the rate for that first hour
```

Divide by step length in seconds where a rate (W/m², mm/h) is needed. `DURSUN` is in seconds of sunshine per step → divide by step_seconds × 60 to get fraction, or convert to minutes.

### Grid coordinates

Downloaded GRIB2 forecast files do **not** include coordinate information. Grid coordinates (lat/lon) must be downloaded separately from the collection's static assets:

- `horizontal_constants_icon-ch1-eps.grib2`
- `horizontal_constants_icon-ch2-eps.grib2`

Download once at container startup and cache in memory. Build the KD-tree from this file.

### Ensemble member counts

| Model | Members |
|---|---|
| ICON-CH1-EPS | 11 |
| ICON-CH2-EPS | 21 |

### How to get all ensemble members

There is **no `forecast:member` search parameter**. A single `POST /search` with `forecast:perturbed: true` returns one GRIB2 file that contains all N members as separate GRIB2 messages (one message per member per variable). cfgrib reads them as a stacked xarray Dataset dimension `number`.

Control run: `forecast:perturbed: false` → one message.

### RELHUM_2M — compute from TD_2M + T_2M

`RELHUM_2M` is not a published variable. Compute from `TD_2M` (dew point) and `T_2M` via Magnus formula.
`TD_2M` is a small surface field (~5MB); this replaced the earlier `QV + PS` approach which required
downloading a 3D field (~80–100MB, all model vertical levels).

```python
def _compute_rh_from_td(t_k: np.ndarray, td_k: np.ndarray) -> np.ndarray:
    t_c  = t_k  - 273.15
    td_c = td_k - 273.15
    rh = 100.0 * np.exp(17.625 * td_c / (243.04 + td_c)) / np.exp(17.625 * t_c / (243.04 + t_c))
    return np.clip(rh, 0.0, 100.0)
```

### PMSL — assume QFF

`PMSL` uses actual temperature reduction (QFF convention), consistent with MeteoSwiss operational synoptic practice and COSMO/ICON heritage. Not explicitly documented — if QFF/QNH distinction becomes critical, confirm with MeteoSwiss support.

### eccodes-cosmo-resources setup

```bash
pip install eccodes-cosmo-resources-python  # bundles COSMO GRIB2 definition files
```

Call once at process startup before any cfgrib/xarray operations:

```python
import eccodes
import eccodes_cosmo_resources

vendor_path = eccodes.codes_definition_path()
cosmo_path = eccodes_cosmo_resources.get_definitions_path()
eccodes.codes_set_definitions_path(f"{cosmo_path}:{vendor_path}")
```

**No system package needed beyond `libeccodes-dev`** (already in Dockerfile). No env var required if using the Python API above.

**Pinned versions** (from MeteoSwiss opendata-nwp-demos):
- `eccodes==2.38.3`
- `eccodes-cosmo-resources-python==2.38.3.1`
- `cfgrib==0.9.15.0`

---

## Altitude Level Mapping

| Altitude (m ASL) | Pressure (hPa) |
|---|---|
| 500 | 950 |
| 800 | 920 |
| 1000 | 900 |
| 1500 | 850 |
| 2000 | 800 |
| 2500 | 750 |
| 3000 | 700 |
| 4000 | 600 |
| 5000 | 500 |

---

## Lenticularis Station API

Collectors fetch the station list from Lenticularis before every collection run:

```
GET https://lenticularis.lg4.ch/api/stations        # full list
GET https://lenticularis.lg4.ch/api/stations?network=...
GET https://lenticularis.lg4.ch/api/stations?canton=...
```

No authentication required (public endpoint). Response is a JSON array:

```json
[
  {
    "station_id": "INT-001",
    "name": "Interlaken",
    "network": "...",
    "latitude": 46.68,
    "longitude": 7.86,
    "elevation": 580,
    "canton": "BE",
    "member_ids": [...]
  }
]
```

`station_id` is the canonical key used in the LSMFAPI forecast cache and in all API responses.

---

## API Contracts

No authentication. All endpoints are open — access is controlled at the network/container level.

### Forecast
- `GET /api/forecast/station` — `?station_id=&hours=` → hourly ForecastResponse (probable + min + max per variable, 120h max); served from in-memory cache. Does NOT include pressure-level winds.
- `GET /api/forecast/altitude-winds` — `?station_id=&hours=` → AltitudeWindsResponse; per-hour wind speed, direction, vertical wind at 9 altitude bands (500–5000m ASL); served from in-memory cache
- `GET /api/forecast/wind-grid` — `?date=YYYY-MM-DD&level_m=` → GridResponse (stub — `set_grid_forecast` not yet called by any collector)

### Accuracy GUI + proxy
- `GET /accuracy` — serves `static/index.html`
- `GET /api/meta` — returns `{"lenticularis_base_url": "..."}` for JS use
- `GET /api/stations` — **proxy** to `{lenticularis.base_url}/api/stations`; avoids browser CORS.
  Returns the Lenticularis station array as-is (fields: `station_id`, `name`, `latitude`, `longitude`, `elevation`, ...)

### Recipes (v0.2)
- `GET /api/recipes` → list of recipes
- `POST /api/recipes` → create recipe
- `PUT /api/recipes/{id}` → update recipe
- `DELETE /api/recipes/{id}` → delete recipe

[Add all routes here as they are implemented, grouped by router domain.]

---

## Deployment

### Traefik Label Format

This homelab requires **list format** labels, not map format:

```yaml
# CORRECT
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.lsmfapi.rule=Host(`lsmfapi.lg4.ch`)"
```

When a container is on multiple Docker networks, add `traefik.docker.network=proxy`.

### Healthcheck

`python:3.11-slim` does not include `curl`. Use Python stdlib:

```yaml
healthcheck:
  test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/')\""]
```

### Dev Overlay

`docker-compose.dev.yml` extends the base with:
- Live `src/` and `static/` volume mounts (`:ro,z`)
- `proxy` external network + Traefik labels for `lsmfapi-dev.lg4.ch`
- `PYTHONPYCACHEPREFIX=/tmp/pycache` — prevents stale `.pyc` files from shadowing volume-mounted sources
