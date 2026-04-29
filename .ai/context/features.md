# Feature History & Backlog

## Current Version: v0.3-dev (in progress)

### Shipped Milestones

| Milestone | What shipped |
|---|---|
| v0.1 | Core collectors, forecast API, altitude winds endpoint, cache persistence, accuracy GUI stations |
| v0.2 | Dashboard, Data Inspector, wind grid, GitHub Actions Docker pipeline, CH1/CH2 cache merge |
| post-v0.2 | Hourly CH2 tail (h34–120), 4×/day schedule for both models, NULL fix (dynamic N_MEMBERS), GRIB persistence cache, dashboard ok/failed counts |

---

## v0.1 — MVP: Core Service + Accuracy GUI ✓ SHIPPED

### Data collection ✓
- Station list fetched from Lenticularis before each collection run
- Startup warm-up runs as background `asyncio.create_task` (non-blocking — server healthy immediately)
- ICON-CH1-EPS collector: h0–h33 (1h steps), 4 runs/day (trigger 02/08/14/20Z — 2h after release)
- ICON-CH2-EPS collector: h34–h120 (1h steps), 4 runs/day (trigger 03/09/15/21Z — 3h after release); does NOT download h0–h33
- Ensemble member count read dynamically from GRIB (not hardcoded); CH1 currently delivers 10 members

### Variables collected per hour per station
- Surface winds: speed, gusts, direction (10m)
- Temperature (2m), relative humidity (from TD_2M via Magnus formula), QFF pressure
- Precipitation (mm/h, de-accumulated)
- Radiation: solar direct (W/m²), solar diffuse (W/m²), sunshine minutes/h (de-accumulated)
- Cloud cover: total, low, mid, high (%), convective cloud base (m AGL)
- Thermics: boundary layer height (m AGL), freezing level (m ASL), CAPE (J/kg), CIN (J/kg)
- Altitude winds (separate endpoint): speed, direction, vertical wind at 9 bands (500–5000m ASL)

### API ✓
- `GET /api/forecast/station` — surface forecast, all variables above, 120h max
- `GET /api/forecast/altitude-winds` — pressure-level wind forecast, 9 altitude bands, 120h max
- `GET /api/stations` — proxy to Lenticularis (CORS-safe)
- APScheduler: `collect_ch1eps` (4×/day: 02/08/14/20Z UTC), `collect_ch2eps` (4×/day: 03/09/15/21Z UTC)

### Infrastructure ✓
- Cache persistence: `save_cache()` / `load_cache()` to `/app/data/cache.json`
- Traefik labels + certresolver=letsencrypt + explicit port label
- Accuracy GUI: station picker loads correctly (CORS-proxied, field names fixed)

### Fixed post-v0.2
- **All-NULL station values** — `N_MEMBERS` hardcoded to 11; CH1 actually delivers 10 members → shape check always failed → silent NaN fallback everywhere. Fixed: member count read dynamically from first valid GRIB result.
- **CH2 3h steps → 1h steps** — CH2 now covers h34–h120 with hourly resolution (was 3h).
- **CH2 schedule 2×/day → 4×/day** — both models now run 4×/day; CH2 at 03/09/15/21Z (was 00/12Z only).
- **GRIB persistence cache** — GRIB files survive container restarts in `/tmp/lsmfapi_grib/`; re-downloads only happen when the ref_dt changes.
- **Dashboard download failure visibility** — progress bar and completion row now show ok/failed file counts.

### Known issues (not yet fixed)
- `sunshine_minutes` wrong on CH2 first step (h=34 is first horizon; full 34h accumulation used as delta)
- `cin: -999.9` ICON fill value should map to `null` (clip CIN_ML where < -900)
- Altitude winds (U/V/W) all null — eccodes level-type mismatch or STAC search issue, not yet investigated
- Accuracy GUI `fetchActuals` / `fetchForecasts` call Lenticularis directly from browser → CORS-blocked

---

## v0.2 — Recipes

- `Recipe` + `RecipeRule` SQLite models (additive / multiplicative corrections per variable, per station or global)
- `GET/POST/PUT/DELETE /api/recipes` CRUD endpoints
- Recipe engine: apply corrections transparently in `/api/forecast/station` response
- Recipe editor GUI: per-station bias table → define correction rules → save

---

## v0.3 — Enhancements

- Bilinear interpolation for smoother station-level values
- Statistical recipe suggestions (auto-compute mean bias from accuracy data)
- Local LLM integration (Ollama): feed accuracy + bias stats → receive natural-language analysis + Recipe correction suggestions
- Push notifications when new forecast run is ingested
- Configurable percentile bands (p10/p90) as alternative to absolute min/max

---

## Backlog (unordered, future)

- Multi-model blending (add ECMWF-EPS when MeteoSwiss publishes it)
- Forecast confidence score per variable per hour
- Admin page: collector health, last run times, manual trigger
