# Prompt: Update the Project README

Read the following files to understand the project fully, then rewrite `README.md` so it is useful to a human reader (developer onboarding, not AI context):

## Files to read

- `.ai/instructions/01-project-overview.md` — what the project is, tech stack, repo layout, data flow, data sources, user roles
- `.ai/instructions/02-backend-conventions.md` — coding standards, patterns, config, auth, scheduler
- `.ai/instructions/03-frontend-conventions.md` — frontend rules and design tokens
- `.ai/instructions/04-constraints.md` — hard constraints (no Alembic, QFF only, no npm, etc.)
- `.ai/context/architecture.md` — SQLite schema, InfluxDB measurements, API contracts, deployment notes
- `.ai/context/features.md` — current version, shipped milestones, backlog

## What the README must cover

1. **What it is** — one-paragraph plain-English description of the service and its role in the Lenticularis ecosystem
2. **Prerequisites** — Python version, required system packages (eccodes for cfgrib), Docker, InfluxDB
3. **Local setup** — clone → `poetry install` → copy `config.yml.example` → `docker-compose up`
4. **Configuration** — explain the key sections in `config.yml` (meteoswiss URLs, influxdb, lenticularis base URL, scheduler)
5. **API overview** — brief table of all endpoints with method, path, auth requirement, and one-line description
6. **Data sources** — ICON-CH1-EPS and ICON-CH2-EPS: what they are, horizon, blending rule
7. **Architecture in brief** — data flow paragraph + repo layout tree (copy from `.ai/instructions/01-project-overview.md`)
8. **Deployment** — Docker + docker-compose, Traefik label format, healthcheck pattern, dev overlay
9. **Development notes** — no Alembic (migration pattern), QFF-only pressure, no npm, English-only GUI
10. **Roadmap** — v0.1 / v0.2 / v0.3 milestone summaries from `features.md`

## Rules

- Write for a developer who has never seen the project before.
- Use GitHub-flavored Markdown with clear headings and tables where appropriate.
- Do not invent details — only use information found in the `.ai/` files.
- Keep it factual and concise; avoid marketing language.
- Do not include AI-context notes, prompt instructions, or references to the `.ai/` folder itself in the output README.
