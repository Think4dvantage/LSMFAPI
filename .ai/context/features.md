# Feature History & Backlog

## Current Version: v0.3.1 (shipped 2026-05-08)

### Shipped Milestones

| Milestone | What shipped |
|---|---|
| v0.1 | Core collectors, forecast API, altitude winds endpoint, cache persistence, accuracy GUI stations proxy |
| v0.2 | Dashboard, Data Inspector, GitHub Actions Docker pipeline, CH1/CH2 cache merge |
| v0.3 | Hourly CH2 tail (h34–120), 4×/day schedule, NULL fix (dynamic N_MEMBERS), GRIB persistence cache, dashboard ok/failed counts, error recording, HBAS_CON/HPBL removed |
| v0.3.1 | CI eccodes fix, Traefik label isolation (PRD/DEV), scheduler lock (warm-up/cron race) |

---

## v0.1 — MVP: Core Service ✓ SHIPPED

- ICON-CH1-EPS (h0–h33) and ICON-CH2-EPS (h34–h120) collectors via MeteoSwiss STAC API
- Ensemble member count read dynamically from GRIB (not hardcoded); CH1 delivers ~10 members
- Surface variables: winds, temperature, RH (from TD_2M), pressure, precipitation, radiation, cloud cover, freezing level, CAPE, CIN
- Altitude winds (separate endpoint): 9 bands 500–5000 m ASL
- `GET /api/forecast/station`, `GET /api/forecast/altitude-winds`, `GET /api/stations` proxy
- Cache persistence: `save_cache()` / `load_cache()` to `/app/data/cache.json`
- Traefik labels + certresolver=letsencrypt

---

## v0.2 — Dashboard + Infrastructure ✓ SHIPPED

- Operational dashboard with live collection status, cache health, error log
- Data Inspector GUI
- GitHub Actions Docker pipeline (build + push to GHCR)
- `scripts/LSMF-dev.ps1` remote deploy script
- CH1/CH2 cache merge: CH1 hourly head (h0–h33) + CH2 hourly tail (h34–h120) served as one blended response

---

## v0.3 — Reliability Hardening ✓ SHIPPED

- CH2 upgraded from 3h steps to 1h steps (h34–h120 hourly)
- Both models now run 4×/day; CH1 at 02/08/14/20Z, CH2 at 03/09/15/21Z
- NULL fix: N_MEMBERS read dynamically (was hardcoded to 11; CH1 delivers 10 → shape check failed → all-NaN)
- GRIB persistence cache: `/tmp/lsmfapi_grib/{model}/{ref_dt}/` — skip re-downloads on container restart
- Dashboard errors panel: download failures (STAC, HTTP, eccodes) now visible alongside HTTP errors
- Corrupt GRIB self-delete: eccodes failure deletes file so it re-downloads next run
- Silent STAC miss now warns: `_fetch_step` logs WARNING when STAC returns no features
- Removed HBAS_CON + HPBL from SURFACE_VARS (not published in EPS catalog)
- Integration test: `tests/test_e2e_collection.py` (`pytest -m integration`)

---

## v0.3.1 — Production Fixes ✓ SHIPPED

- **CI eccodes**: `ubuntu-latest` ships libeccodes 2.34.1; COSMO definitions require 2.38+. Fixed by installing eccodes 2.38.3 from conda-forge via Miniforge, with `LD_LIBRARY_PATH` pointing to conda lib.
- **Traefik cross-routing**: base `docker-compose.yml` had PRD Traefik labels that bled into DEV container via overlay merge, causing Traefik to load-balance between PRD and DEV. Fixed by removing all labels from the base file (PRD labels live in server-side compose only).
- **Scheduler warm-up/cron race**: container starts at 19:58Z with ref_dt=12Z; cron fires at exactly 20:00Z with ref_dt=18Z; `_purge_stale` deletes the active 12Z GRIB directory mid-download. Fixed with per-model `asyncio.Lock()` in `scheduler.py` — concurrent same-model runs skip instead of overlapping.

---

## v0.4 — Recipes (not started)

- `Recipe` + `RecipeRule` SQLite models (additive / multiplicative corrections per variable, per station or global)
- `GET/POST/PUT/DELETE /api/recipes` CRUD endpoints
- Recipe engine: apply corrections transparently in `/api/forecast/station` response
- Recipe editor GUI: per-station bias table → define correction rules → save

---

## v0.5 — Enhancements (backlog)

- Bilinear interpolation for smoother station-level values
- Statistical recipe suggestions (auto-compute mean bias from accuracy data)
- Local LLM integration (Ollama): accuracy + bias stats → natural-language analysis + Recipe suggestions
- Push notifications when new forecast run is ingested
- Configurable percentile bands (p10/p90) as alternative to absolute min/max
- Wind-grid endpoint fully populated (currently a stub)

---

## Known Issues (not yet fixed)

- `sunshine_minutes` wrong on CH2 first step (h=34 accumulation spans 34h not 1h)
- `cin: -999.9` ICON fill value should map to null (clip CIN_ML where < -900)
- Altitude winds (U/V/W) all null — eccodes level-type mismatch or STAC search issue, not yet investigated
- Accuracy GUI `fetchActuals` / `fetchForecasts` call Lenticularis directly → CORS-blocked
- Wind-grid endpoint is a stub (`set_grid_forecast` never called by collectors)
