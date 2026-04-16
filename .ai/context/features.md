# Feature History & Backlog

## Current Version: v0.1 (not yet started)

### Shipped Milestones

| Milestone | What shipped |
|---|---|
| — | Nothing shipped yet — greenfield project |

[Keep this table updated as milestones ship.]

---

## v0.1 — MVP: Core Service + Accuracy GUI

- Fetch station list from Lenticularis API on startup; refresh before each collection run
- Trigger an immediate collection run on container startup to warm the in-memory cache
- ICON-CH1-EPS collector: download GRIB2 from MeteoSwiss open data, parse with cfgrib, compute ensemble stats (median + abs min/max across all members × runs), precompute ForecastResponse for every known station, store in in-memory dict
- ICON-CH2-EPS collector: same, 30h–120h range only
- `GET /api/forecast/station` — hourly blended station forecast (probable + min + max, 7 variables); pure cache lookup
- `GET /api/forecast/wind-grid` — 171-point Switzerland wind grid at 9 altitude levels (same geometry as Lenticularis); pure cache lookup
- APScheduler jobs: `collect_ch1eps` (every 3h), `collect_ch2eps` (every 6h) — no purge job needed (cache is in-memory)
- Accuracy analysis GUI (English only, read-only): station picker + date range → fetches actuals + historical forecasts from Lenticularis → bias charts + RMSE table
- Docker + docker-compose (base + dev overlay), Traefik labels

**First task before any code**: confirm exact GRIB2 download URLs + file naming from MeteoSwiss STAC catalog, document in architecture.md.

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
