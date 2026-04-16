# Resume Notes — 2026-04-16

## Status

Planning phase complete. No code written yet. All architectural decisions are documented in `.ai/`.

## Key Decisions Made This Session

- **Storage**: Python in-process dict replaces InfluxDB. Cache populated on container startup + after each collection run. Interface in `database/cache.py`.
- **No auth**: Service is unauthenticated. Access controlled at network/container level.
- **Station precomputation**: Station list fetched from Lenticularis API on startup. After each collection run, `ForecastResponse` precomputed for every known station and stored in cache.
- **Blueprint synced**: `.ai/` framework files updated from `Think4dvantage/ai-blueprint`. `CLAUDE.md` and `.gitattributes` added.

## Next Step

**Before writing any code**: confirm exact GRIB2 download URLs and file naming from the MeteoSwiss STAC catalog, then document them in `.ai/context/architecture.md`.

Use the `architect` or `specify` prompt to kick off v0.1 implementation planning.

## Open Questions

- What is the exact MeteoSwiss STAC catalog URL and file naming pattern for ICON-CH1-EPS and ICON-CH2-EPS GRIB2 files?
- What is the Lenticularis API endpoint that returns the station list?

## Context

Read these files to get up to speed:
- `.ai/instructions/00-ai-usage.md` — meta-rules and planning workflow
- `.ai/instructions/01-project-overview.md` — what the project is, tech stack, data flow
- `.ai/context/architecture.md` — in-memory cache design, API contracts, deployment
- `.ai/context/features.md` — v0.1 scope
