# Resume Notes — 2026-04-16

## Status

Scaffold complete. All research done. No collector logic implemented yet — that is the next concrete coding task.

## What Was Done This Session

### Repo scaffold created
Full project skeleton is in place and committed:
- `pyproject.toml` (Poetry, all deps including `eccodes-cosmo-resources-python`)
- `src/lsmfapi/` — full package structure with all modules as working skeletons
- `Dockerfile` + `docker-compose.yml` + `docker-compose.dev.yml`
- `static/` — accuracy GUI (HTML/CSS/JS)
- `config.yml.example` updated to STAC collection IDs

### MeteoSwiss STAC API confirmed
- Search endpoint: `POST https://data.geo.admin.ch/api/stac/v1/search`
- Collections: `ch.meteoschweiz.ogd-forecasting-icon-ch1` / `ch.meteoschweiz.ogd-forecasting-icon-ch2`
- All ensemble members delivered in a single GRIB2 file (`forecast:perturbed: true`)
- CH1-EPS: 11 members. CH2-EPS: 21 members.
- Grid coordinates in separate `horizontal_constants_*.grib2` file (must download on startup)
- Data availability window: 24h after publication

### Variable set expanded
`ForecastPoint` expanded from 8 to 24 fields — radiation, cloud cover, boundary layer height, CAPE/CIN, freezing level, vertical wind per pressure level. Full variable table in `architecture.md`.

### All four open questions resolved
See `architecture.md` — RELHUM_2M (compute from QV+T+PS), PMSL=QFF, member enumeration, eccodes setup.

## Next Step

**Implement `IconCh1EpsCollector.collect()`** in `src/lsmfapi/collectors/icon_ch1_eps.py`.

Before starting, verify the two unconfirmed shortNames by fetching the params CSV:
```
GET https://data.geo.admin.ch/api/stac/v1/collections/ch.meteoschweiz.ogd-forecasting-icon-ch1
```
Look for a CSV asset in the response and confirm `HPBL` (BLH?) and `HZEROCL`.

Then implement in this order:
1. `_download_grid_constants()` — fetch `horizontal_constants_icon-ch1-eps.grib2`, build KD-tree, cache as module-level singleton
2. `_fetch_station_list()` — GET `{lenticularis.base_url}/api/stations`, return list of `{lat, lon, elevation}` dicts
3. `_stac_search(collection, ref_dt, variable, horizon, perturbed)` → returns GRIB2 download URL
4. `_download_all_steps(variable, ref_dt)` → loops horizons P0DT00H to P0DT30H, returns stacked xarray
5. `_deaccumulate(arr)` — np.diff for TOT_PREC, DURSUN, ASWDIR_S, ASWDIFD_S
6. `_compute_rh(qv, t_k, p_pa)` — Bolton formula (already documented in architecture.md)
7. Main `collect()` — assembles ForecastPoint per station per valid_time, calls `set_station_forecast()`

`IconCh2EpsCollector` is identical except collection ID, member count (21), and horizon range (30h–120h).

## Open Questions

- [ ] Confirm `HPBL` shortName (may be `BLH`) — check params CSV
- [ ] Confirm `HZEROCL` shortName — check params CSV
- [ ] What are the exact Lenticularis API endpoints for station list, actuals, and forecast archive? (needed by accuracy GUI JS and by collectors to fetch station list)

## Key Decisions Made

- **STAC search, not static URL**: Each variable+step is discovered via POST to `/search`. No filename construction.
- **One GRIB2 file = all members**: `forecast:perturbed: true` gives a single file with all member messages stacked. cfgrib reads `number` dimension.
- **RELHUM_2M not published**: Compute from QV + T_2M + PS using Bolton formula.
- **PMSL = QFF**: Assumed, consistent with MeteoSwiss operational practice. Not formally documented.
- **eccodes-cosmo-resources-python**: pip-only, no extra apt package. Call `codes_set_definitions_path` at startup (`_eccodes.py`).
- **Extended variable set**: ForecastPoint now includes radiation, cloud, thermics/convection, vertical wind — all as EnsembleValue (probable/min/max).

## Context

Read these files to get up to speed:
- `.ai/instructions/01-project-overview.md` — tech stack, data flow
- `.ai/context/architecture.md` — STAC API, variable names, de-accumulation, eccodes setup
- `.ai/context/features.md` — v0.1 scope (expanded variable set)
- `src/lsmfapi/models/forecast.py` — full ForecastPoint/GridForecastPoint schema
- `src/lsmfapi/collectors/base.py` — BaseCollector with `download()` helper
- `src/lsmfapi/_eccodes.py` — eccodes definitions setup (called in lifespan)
