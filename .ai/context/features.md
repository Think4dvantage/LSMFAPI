# Feature History & Backlog

## Current Version: v0.1 (not yet started)

### Shipped Milestones

| Milestone | What shipped |
|---|---|
| — | Nothing shipped yet — greenfield project |

[Keep this table updated as milestones ship.]

---

## v0.1 — MVP: Core Service + Accuracy GUI

### Data collection
- Fetch station list from Lenticularis API on startup; refresh before each collection run
- Trigger an immediate collection run on container startup to warm the in-memory cache
- ICON-CH1-EPS collector (1.1 km resolution): download GRIB2 via MeteoSwiss STAC API, parse with cfgrib + eccodes-cosmo-resources, compute ensemble stats (median + abs min/max across all 11 members × runs), precompute ForecastResponse for every known station, store in in-memory dict
- ICON-CH2-EPS collector (2.2 km resolution): same, 30h–120h range only, 21 members

### Variables collected per hour per station (full `ForecastResponse`)
- Surface winds: speed, gusts, direction (10m)
- Temperature (2m), relative humidity (computed from QV+T+PS), QFF pressure
- Precipitation (mm/h)
- Radiation: solar direct (W/m²), solar diffuse (W/m²), sunshine minutes/h
- Cloud cover: total, low, mid, high (%), convective cloud base (m AGL)
- Thermics: boundary layer height (m AGL), freezing level (m ASL), CAPE (J/kg), CIN (J/kg)
- Pressure-level winds at 9 altitude bands (500–5000m ASL): speed, direction, **vertical wind** (m/s)

### API
- `GET /api/forecast/station` — 120h blended station forecast, all variables above
- `GET /api/forecast/wind-grid` — 171-point Switzerland wind grid at 9 altitude levels; includes vertical wind per point
- APScheduler: `collect_ch1eps` (every 3h), `collect_ch2eps` (every 6h)

### Infrastructure
- Accuracy analysis GUI (English only, read-only): station picker + date range → fetches actuals + historical forecasts from Lenticularis → bias charts + RMSE table
- Docker + docker-compose (base + dev overlay), Traefik labels

**STAC API confirmed. Variable names and de-accumulation strategy documented in architecture.md. Ready to implement collectors.**

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
