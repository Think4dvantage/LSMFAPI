# Constraints — What NOT to Do

## Frontend

**Never add npm or a build step.** The frontend is intentionally dependency-free. No webpack, vite, rollup, parcel, or any bundler. No `package.json`. All UI is plain HTML + vanilla JS.

**English only.** No i18n system. All strings hardcoded in English.

---

## Secrets

**Never commit secrets.** `config.yml` and `.env` are gitignored. Only `config.yml.example` (with placeholder values) is committed.

---

## Configuration

**Never read `os.environ` directly.** All configuration goes through `get_config()`. Add new keys to `config.py` Pydantic models AND to `config.yml.example`.

The `scheduler:` section in `config.yml` is dead config — the scheduler uses hardcoded cron triggers (02/08/14/20Z for CH1, 03/09/15/21Z for CH2). Do not add scheduler keys to `config.py`.

---

## Database Migrations

**No Alembic.** Schema migrations use raw `ALTER TABLE` inside `_run_column_migrations()` in `db.py`. New columns must be added with an idempotent `ALTER TABLE` checked against `PRAGMA table_info`. Never use SQLAlchemy's `Base.metadata.create_all()` for schema drift.

---

## Data Collection

**Never hardcode ensemble member counts.** `N_MEMBERS` in collector files is labelled `# informational only`. Member count is always read from the first valid GRIB result at runtime.

**Never add HBAS_CON or HPBL back to SURFACE_VARS.** Neither variable is published in the CH1-EPS or CH2-EPS STAC catalog — adding them wastes 68 STAC calls per run with zero return.

**Never use QV for humidity.** Relative humidity is computed from TD_2M (dew point) + T_2M via Magnus formula. QV is a 3D field (~100MB per file) — using it would multiply download time by 10×.

**QFF only.** All pressure fields are `pressure_qff`. Never use QNH.

---

## Scheduler

**Never start two collections of the same model concurrently.** The `_ch1_lock` / `_ch2_lock` asyncio locks in `scheduler.py` enforce this. If a cron trigger fires while the previous run is still active, it logs a skip and returns. This prevents `_purge_stale` from deleting the active GRIB directory mid-download.

---

## Traefik / Deployment

**`docker-compose.yml` in this repo has no Traefik labels.** It is the DEV base only. PRD Traefik labels live in a separate compose file on the production server outside this repo. Never add PRD labels back to `docker-compose.yml` — they would bleed into the DEV container via overlay merge and cause Traefik to load-balance between PRD and DEV.

---

## Code Quality

- Don't add features, refactor code, or make "improvements" beyond what was asked.
- Don't add error handling, fallbacks, or validation for scenarios that can't happen.
- Don't create helpers or abstractions for one-time operations.
- Don't design for hypothetical future requirements.
- Don't add comments explaining WHAT the code does — only add comments for non-obvious WHY (hidden constraints, subtle invariants, specific bug workarounds).
- Don't use feature flags or backwards-compatibility shims when you can just change the code.
- No print statements in production code — always use the `logging` module.
- One router per domain — never put routes in `main.py`.
