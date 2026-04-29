# Feature History & Backlog

## Current Version: v0.2 (shipped)

### Shipped Milestones

| Milestone | What shipped |
|---|---|
| v0.1 | Core collectors, forecast API, altitude winds endpoint, cache persistence, accuracy GUI stations |
| v0.2 | Dashboard, Data Inspector, wind grid, GitHub Actions Docker pipeline, CH1/CH2 cache merge |

---

## v0.1 — MVP: Core Service + Accuracy GUI ✓ SHIPPED

### Data collection ✓
- Station list fetched from Lenticularis before each collection run
- Startup warm-up runs as background `asyncio.create_task` (non-blocking — server healthy immediately)
- ICON-CH1-EPS collector: 11 members, 0–30h (1h steps), 4 runs/day (00/06/12/18Z)
- ICON-CH2-EPS collector: 21 members, 30–120h (3h steps), 2 runs/day (00/12Z)

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
- APScheduler: `collect_ch1eps` (every 3h), `collect_ch2eps` (every 6h)

### Infrastructure ✓
- Cache persistence: `save_cache()` / `load_cache()` to `/app/data/cache.json`
- Traefik labels + certresolver=letsencrypt + explicit port label
- Accuracy GUI: station picker loads correctly (CORS-proxied, field names fixed)

### Known issues (not yet fixed)
- `sunshine_minutes` wrong on CH2 first step (h=30 is first horizon; full 30h accumulation used as delta)
- `cin: -999.9` ICON fill value should map to `null` (clip CIN_ML where < -900)
- Altitude winds (U/V/W) all null — eccodes level-type mismatch or STAC search issue, not yet investigated
- Accuracy GUI `fetchActuals` / `fetchForecasts` call Lenticularis directly from browser → CORS-blocked

### Fixed in v0.2
- **CH1/CH2 cache overwrite bug** — CH2 was overwriting CH1's cache entry for every station, discarding the 0–30h hourly data. Now stored in separate dicts (`_ch1_station_cache` / `_ch2_station_cache`) and merged at read time: CH1 hourly head (h0–h30) + CH2 3h-step tail (h33–h120).

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
