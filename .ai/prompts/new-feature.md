# Prompt: Implement a New Feature

Use this prompt as a checklist when implementing any non-trivial feature end-to-end.

---

## Backend Checklist

- [ ] New SQLite table(s) → add ORM model in `models.py`, migration in `db.py:_run_column_migrations()` if adding columns to existing tables
- [ ] New Pydantic schemas in `src/[package]/models/`
- [ ] New router at `src/[package]/api/routers/{domain}.py` → register in `main.py`
- [ ] Auth dependencies applied correctly (see `.ai/instructions/02-backend-conventions.md`)
- [ ] New InfluxDB queries → add methods to `InfluxClient` in `influx.py`
- [ ] Config keys for anything configurable → add to `config.py` Pydantic models AND `config.yml.example`
- [ ] Scheduler job if periodic collection is needed → add to `scheduler.py`

## Frontend Checklist

- [ ] New `.html` page file in `static/`
- [ ] One `<script type="module">` block per page; imports `initI18n` from `i18n.js` and `renderNavAuth` from `auth.js`
- [ ] All user-visible strings have i18n keys in all locale files
- [ ] Dark theme colors used: `#0f1117` body, `#1a1f2e` cards, `#2d3748` borders, `#e2e8f0` text, `#90cdf4` accent
- [ ] `fetchAuth()` used for all authenticated API calls
- [ ] `shared.css` linked in `<head>`
- [ ] Mobile-responsive (test at ≤640 px width)
- [ ] Console logging added to every new/modified function (see frontend conventions)

## Quality

- [ ] No hardcoded config values — all through `get_config()`
- [ ] No print statements — use `logging`
- [ ] Type hints on all function signatures
- [ ] No npm / build step introduced

Refer to `.ai/context/architecture.md` for data models and API contracts.
Refer to `.ai/context/features.md` for backlog context on what's planned.
