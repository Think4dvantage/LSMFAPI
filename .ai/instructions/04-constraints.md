# Constraints — What NOT to Do

## Production

**Never touch prod directly.** All production changes go through the IaC repo. No direct SSH, no direct `docker-compose` on the prod host.

---

## Frontend

**Never add npm or a build step.** The frontend is intentionally dependency-free. No webpack, vite, rollup, parcel, or any bundler. No `package.json`.

**No i18n system.** The LSMFAPI GUI is English only — internal operator tool. Do not add locale JSON files, `data-i18n` attributes, `initI18n()` calls, or a language picker. Hardcode all strings in English directly in HTML/JS.

---

## Pressure Fields

**Always use QFF — never QNH.** All pressure fields are named `pressure_qff` in API responses, InfluxDB fields, Pydantic schemas, and UI labels. Lenticularis is being migrated from QNH to QFF; LSMFAPI uses QFF from day one.

---

## Secrets

**Never commit secrets.** `config.yml` and `.env` are gitignored. Only `config.yml.example` (with placeholder values) is committed.

---

## Database Migrations

**Never skip `_run_column_migrations`** when adding columns to existing tables. SQLAlchemy's `create_all` does not alter existing tables — new columns on existing tables require an explicit `ALTER TABLE` in `_run_column_migrations()` in `db.py`. Always make migrations idempotent by checking `PRAGMA table_info` first.

---

## Historical Data

**LSMFAPI stores no historical data.** Forecast data lives only in the in-memory cache for the duration of the process. Lenticularis owns all historical archiving. The accuracy GUI fetches historical data directly from Lenticularis.

---

## Code Quality

- Don't add features, refactor code, or make "improvements" beyond what was asked.
- Don't add error handling, fallbacks, or validation for scenarios that can't happen.
- Don't create helpers or abstractions for one-time operations.
- Don't design for hypothetical future requirements.
- Don't add docstrings, comments, or type annotations to code you didn't change.
- Don't use feature flags or backwards-compatibility shims when you can just change the code.

---

## Architecture

- No Alembic — schema migrations are done with raw `ALTER TABLE` in `_run_column_migrations()`.
- No print statements in production code — use the standard `logging` module.
- Never read `os.environ` directly — always go through `get_config()`.
- Never put all routes in `main.py` — one router per domain.
