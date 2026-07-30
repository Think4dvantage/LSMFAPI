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

**Shared modules used by both collectors must never call `get_config()` (or read any other
patchable global like `datetime.now()`) themselves — take the resolved value as a parameter
instead.** `tests/test_e2e_collection.py` monkeypatches `get_config`/`HORIZONS`/`datetime` on
the `icon_ch1_eps` **module's own namespace**; a shared module (`collectors/grib_cache.py`,
`collectors/_icon_eps_base.py`) that resolves these itself gets a *different* binding of the
same name and silently bypasses the patch. This broke CI once already (`grib_cache.py`'s
`_base_dir()` called `get_config()` directly — see `.ai/RESUME.md` v0.3.39): the fix was to
pass `grib_cache_dir` in from the caller (`_icon_eps_base.py`'s `collect()` via `self._cfg()`)
instead. See `.ai/context/architecture.md`'s "Collector architecture — shared base class"
section for the full pattern (`_cfg()`/`_compute_ref_dt()`/`HORIZONS`-as-property) if adding a
third model or touching this indirection.

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

## Async / Event Loop

**Never call CPU-bound work directly from `async def`.** GRIB parsing, KD-tree builds, and large numpy reductions must go through `await asyncio.to_thread(...)`. The API and the collectors share one event loop and one process: anything synchronous blocks *every* HTTP request for its full duration, `/health` included, which fails the healthcheck and makes Traefik drop the container.

This is not theoretical. `collect_grid()` was called inline from `async def collect()` and took the web UI down for ~8 min per CH1 run and ~20 min per CH2 run — ~1.5–2 h/day — while collection itself looked perfectly healthy in the logs. Fixed in v0.3.5.

**The tell**: a long gap between consecutive log lines with no output. If a function can log "started" and then say nothing for minutes, it is blocking the loop.

eccodes (via cffi) and numpy release the GIL, so a worker thread genuinely restores responsiveness rather than merely moving the stall.

---

## Dependencies & eccodes

**Never remove `poetry.lock`, and never make it optional.** The Dockerfile does `COPY pyproject.toml poetry.lock ./` — deliberately not `poetry.lock*`. The glob silently fell back to a fresh resolve, which is how three inputs drifted under a fixed image tag and crash-looped PRD (v0.3.3). A missing lock must fail the build loudly.

**Never install a system libeccodes** — no `apt-get install libeccodes-dev`, no conda, no Miniforge, no `LD_LIBRARY_PATH`. The C library ships in the `eccodeslib` wheel, and `findlibs` prefers an installed package over every system path. A second copy can only mismatch the binding. Debian's version tracks the base image suite (bookworm 2.28 → trixie 2.41.0) and will drift out from under you.

**Never drop the explicit `eccodeslib` entry from `pyproject.toml`.** The `eccodes` wheel declares it, but PyPI's JSON metadata — Poetry's resolution source — omits it, so Poetry skips it and findlibs falls through to a system library. Poetry compounds this on Windows by locking from the win_amd64 wheel, whose metadata genuinely lacks the dependency. The explicit entry (with `markers = "platform_system != 'Windows'"`) is the only thing that survives both.

**Keep library ≥ definitions.** `eccodeslib` must never be older than `eccodes-cosmo-resources-python`. Definitions newer than the library make ecCodes abort the process on the first GRIB parse — no Python exception, no traceback, PID 1 dies, container restart-loops. Bump both together.

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
