# Resume Notes — 2026-07-30

## What Was Done This Session

### Tech-debt remediation plan

A full audit of `src/`, `static/`, build/CI files plus live PRD inspection produced
`specs/001-tech-debt-remediation/plan.md` — 23 findings (P0–P3), each with evidence, fix, and
verification steps. User resolved the 7 open decisions that needed a human call (verify=False
rationale, dashboard auth, non-root uid scope, circular wind stats, Recipe tables, arm64, dead
script) and gave the go-ahead to implement the whole plan autonomously, sequencing group by
group per the plan's own ordering. Working through it as one commit per item, versioned
per item like every other fix in this project.

### v0.3.8 — P0-1 stored XSS in the operator dashboard fixed

**Finding**: request-controlled data (station_id echoed into 404 messages, request path recorded
by telemetry) flowed unescaped into `dashboard.js`/`data.js` via `innerHTML`, reachable
unauthenticated. See `specs/001-tech-debt-remediation/plan.md` P0-1 for the full chain.

**Fix**: `escapeHtml()` added to both `dashboard.js` and `data.js`, applied at every sink that
interpolates server data into an `innerHTML` template (`modelRows`, the errors table, the
station `<option>` list). Defense in depth: `station_id` constrained to
`pattern=r"^[A-Za-z0-9_.\-]+$", max_length=64` on both `/api/forecast/station` and
`/api/forecast/altitude-winds`; the 404 body no longer echoes raw input (moved to a structured
`details.station_id` field); `telemetry.record_error`/`record_download_error` now escape `<`/`>`
before storing, so the error ring can never carry markup regardless of caller.

**Verify** (pending PRD deploy): request `?station_id=<b>x</b>x`, confirm 422 (pattern rejects
it before it reaches the handler); open `/dashboard` and confirm error rows render literal text,
never markup.

### v0.3.9 — P0-2 TLS verification re-enabled on all HTTPS clients

**Finding**: `verify=False` on 4 `httpx.AsyncClient` calls (Lenticularis proxy in
`dashboard.py`, both collectors' `_fetch_stations`, `diag_interlaken.py`) — full MITM exposure
on the calls that decide what the service collects. The MeteoSwiss clients were unaffected
(they always verified correctly).

**User confirmed** both `lenti.cloud` and any dev Lenticularis host present valid public certs
— `verify=False` was never needed. Removed from all 4 sites, no CA-trust workaround required.

**Verify** (pending deploy): a collection run completes normally and `/api/stations` still
returns the full station list with verification on.

### v0.3.10 — P0-3 `save_cache()` no longer blocks the event loop

**Finding**: `save_cache()` ran synchronously (no `await`, no thread) from both scheduler jobs
and lifespan shutdown, serialising 170 MB JSON + two multi-hundred-MB npz files. Measured live
on PRD: a 43.2 s stall per run (2026-07-30 CH1 run: 2.66 s JSON + 15.69 s wind-grid npz +
24.78 s thermal-grid npz), with zero requests served in that window — same failure class as
the v0.3.5 grid-build stall, just in the save path instead.

**Fix**:
1. `await asyncio.to_thread(save_cache)` at both `scheduler.py` call sites. `api/main.py`'s
   shutdown call stays synchronous on purpose (comment added) — the loop is closing there, and
   losing the cache on a fast shutdown is worse than a slow one.
2. Per-grid dirty flag (`_grid_wind_dirty` / `_thermal_grid_dirty`, set by `set_grid_wind_cache`/
   `set_thermal_grid_cache`, cleared by `save_cache()` after writing): a save no longer rewrites
   a grid nothing wrote to since the last save.
3. Switched `np.savez_compressed` → `np.savez` on both grid files. **Measured locally**
   (synthetic float16 arrays at the real shape — 17 arrays/121 frames/~122k points for wind,
   36 arrays for thermal; smooth-not-random so it's not a worst-case) since this dev box can't
   run the real collector: compressed took 20.0 s / 43.7 s for 402 MB / 852 MB;
   plain `savez` took 0.46 s / 1.14 s for 480 MB / 1017 MB — **~20–40× faster for ~16% more
   disk**. The timing ratio lines up closely with PRD's observed 15.69 s / 24.78 s, which is
   good signal the synthetic benchmark is representative of the *shape* of the tradeoff even
   though absolute sizes differ from real GRIB-derived data. Chose plain `savez`: the app-data
   volume (`./data`, not the tight GRIB cache disk from P0-4) has headroom, and burning
   20–40× the CPU in a worker thread for a 16% size cut is a bad trade even off the loop.
   **Confirm real PRD file sizes/timings after deploy and revisit if disk pressure shows up
   on that volume.**

**Verify** (pending PRD deploy): gap between `collect() complete` and `executed successfully`
shrinks to a few seconds with access-log lines appearing inside it; both npz files still load
on next restart (`Grid cache loaded` / `Thermal grid cache loaded` at INFO).

### v0.3.11 — P1-1 `/health` can now actually report unhealthy

**Finding**: `/health` was `return JSONResponse({"status": "ok", **cache_stats()})` —
unconditional 200, no version, no uptime, no subsystem checks. Confirmed on PRD:
`FailingStreak=0, RestartCount=0` after 8 days despite the P0-3 43s stalls — a blocked loop
makes the Docker probe *hang* rather than fail, so the container reported healthy while
serving nothing.

**Fix**: real checks per `08-operability.md` —
- `sqlite`: cheap `SELECT 1` via new `db.check_db()` (works even before any table exists).
- `scheduler`: `CollectorScheduler.is_running()` / `.job_count()` → `"running (N jobs)"` /
  `"stopped"`.
- `cache`: `"warm (age Xs)"` / `"warm"` / `"cold"` from `cache_stats()` — **never** contributes
  to the 503 decision; a stale-but-serving cache is intended behaviour.
- Returns **503** only when SQLite is unreachable or the scheduler is stopped; logs the
  `checks` dict at WARNING whenever that happens.
- `service`, `version` (`importlib.metadata.version("lsmfapi")` — also fixes P2-1's hardcoded
  `"0.1.0"`, 7 releases stale), `uptime_seconds` (`time.monotonic()` since module import).
- Docker healthcheck's `urlopen` now passes `timeout=3` so the probe self-limits instead of
  relying on Docker's own `--timeout=5s`.

**Bundled in**: P2-9's `datetime.utcnow()` → `datetime.now(timezone.utc)` fix in `cache.py`
(2 sites) — `/health`'s cache-age subtraction is `aware − naive`, which raises `TypeError`
the moment the cache actually populates. Small enough and directly load-bearing for this
item to fix now rather than wait for the G6 cleanup batch.

**Verify** (pending PRD deploy): `/health` returns `version` matching `pyproject.toml`,
`uptime_seconds` counting up, and `checks.cache` showing an age once a collection completes;
manually stopping the scheduler (or SQLite) flips `status` to `degraded` and the HTTP code to
503.

### v0.3.12 — P1-2 telemetry error ring no longer self-saturates, errors now log visibly

**Finding**: `telemetry.py`'s `_recent_errors` was a raw `deque(maxlen=20)`, appended per
occurrence with no de-duplication. On PRD every one of the last 20 entries was the same five
recurring station 404s (stations deleted upstream, still polled) — real errors were evicted
within minutes and the panel showed nothing useful for 8 days. Compounding:
`record_error`/`record_download_error` logged at DEBUG under an INFO root logger, so none of
this ever reached the logs either.

**Fix**:
1. Errors now aggregate by `(method, path, status, detail)` in an `OrderedDict` capped at 20
   *distinct* groups — `{first_seen, last_seen, count}`, bumped on repeat instead of appended.
   `dashboard.js`/`dashboard.html` updated to render `last_seen` + `count` (escaped, per P0-1).
2. Split `error_count` (5xx + collector/download failures — "might be our fault") from
   `client_error_count` (4xx — routine, e.g. an unknown station). Both exposed in
   `/api/dashboard`'s `requests` object.
3. 5xx and download errors now log at ERROR, 4xx at WARNING.
4. Counters and the error-group dict are guarded by a `threading.Lock` — collectors call
   `record_download_error` from `asyncio.to_thread` worker threads, so the old
   `_error_count += 1` was a non-atomic read-modify-write (lost-count risk, not corruption).
5. `record_request(method, path)` ignored both parameters; dropped them (`record_request()`),
   updated the one call site in `main.py`.

**Verify** (pending PRD deploy): the five stale-station 404s collapse into one row with a
rising count instead of filling the panel; deliberately triggering a 500 shows up in the panel
**and** as an ERROR log line.

### v0.3.13 — P1-9 unhandled exceptions now get caught, logged, and recorded

**Finding**: `TelemetryMiddleware`'s `response = await call_next(request)` only records errors
for responses that complete normally through the app. An unhandled exception (no handler was
registered for the bare `Exception` type) propagates straight out of `call_next()` before that
code runs — the class of error that matters most was invisible to both the dashboard and, per
P1-2's log-level fix, effectively invisible everywhere.

**Fix**: `@app.exception_handler(Exception)` — logs at ERROR with `exc_info=True`, returns the
documented `{"error": {code, message}}` envelope with a generic message (no exception text in
the response body, consistent with the P0-1 fix). Since Starlette's `ExceptionMiddleware` sits
*inside* user middleware, once this handler converts the exception into a real `Response`,
`TelemetryMiddleware` sees it like any other ≥400 response and records/logs it automatically —
no separate telemetry call needed in the handler itself.

**Verify** (pending PRD deploy): trigger a genuine unhandled exception and confirm it now
appears in `/api/dashboard`'s error panel and as an ERROR-level log line, where previously it
appeared as neither.

### v0.3.14 — P0-4.1 GRIB cache dir configurable + startup disk warning

**Finding**: `grib_cache.py` hardcoded `_BASE = Path("/tmp/lsmfapi_grib")` with no config
override, and neither compose file bind-mounted it — so DEV writes the same ~200/480 GB peak
(see P0-4.2, still open) into the container's writable layer, exactly how the v0.3.6 disk-full
incident started. This is the cheap, urgent half of P0-4; P0-4.2 (the actual peak-disk fix,
same work as P1-4) is deferred to land alongside the single-pass GRIB extraction.

**Fix**:
- `grib_cache_dir` added to `Config`/`config.yml.example` (default `/tmp/lsmfapi_grib`, so
  existing deployments are unaffected until they opt in). `grib_cache.py` now resolves the base
  dir via `get_config()` instead of a module constant.
- `CollectorScheduler.startup()` calls `grib_cache.log_startup_status()`: logs the resolved dir
  and free space, WARNING below 550 GB (a CH2 run alone can peak at ~480 GB).
- `docker-compose.yml` bind-mounts `./grib-cache:/tmp/lsmfapi_grib` (added to `.gitignore`) so
  DEV stops filling the container layer regardless of the config value.
- **Doc correction**: `RESUME.md`'s v0.3.6 entry and the matching `features.md` row overstated
  the W-file fix — deletion runs *after* the whole download phase, so it bounds retention
  between runs, not the in-run peak. Corrected both to say peak is still ~200/480 GB pending
  P0-4.2.

**Verify** (pending PRD deploy): startup log shows `GRIB cache dir: ...` and a free-space line;
`du -sh` on the bind-mounted `./grib-cache` (not the container layer) grows during a run.

### v0.3.15 — P1-6 `config.yml` untracked

**Finding**: `config.yml` was tracked in git despite `04-constraints.md` and `README.md:35`
both stating it's gitignored. Verified no leak: `git log -p --follow -- config.yml` shows no
token/password/secret string in any revision, and the Dockerfile doesn't `COPY` it (compose
bind-mounts it), so nothing was baked into a published image. Still wrong to leave tracked —
it's the designated home for real credentials and one commit from publishing one.

**Fix**: `git rm --cached config.yml` (kept on disk, not deleted); added `config.yml` to
`.gitignore`; removed the dead `scheduler:` block from the local file (the real schedule is
hardcoded cron, per `04-constraints.md`); fixed the README wording. `config.yml.example`
already documents every key `config.py` reads (confirmed after the P0-4.1 `grib_cache_dir`
addition).

**Not yet done — needs the user's attention before/at deploy**: once untracked, `config.yml`
no longer arrives via git/rsync. Both DEV and PRD hosts must already have their own
`config.yml` on disk (they do — it was already gitignored-in-spirit and bind-mounted, never
baked into the image), but this is the point to double-check before rolling out a container
built from this commit, since PRD is off-limits to direct changes.

**Verify**: `git status` shows `config.yml` as untracked-but-present; `git log` for the file
stops advancing.

### v0.3.16 — P1-7 config is no longer silently wrong

**Finding**: `config.py` had no `extra="forbid"`, so Pydantic v2's default `extra="ignore"`
silently discarded unknown keys — the four dead `scheduler.*` keys (P1-6) were dropped on the
floor with no error, and a typo (`base_ur1:`) would be equally silent. `get_config()` did a
CWD-relative `yaml.safe_load(Path("config.yml").read_text())` with no existence check — a
missing file raised a bare `FileNotFoundError` from inside a collector, caught by
`scheduler.py`'s `mark_failed`, so **the service booted "healthy" with no config at all**.
Nothing logged the resolved config, so an operator couldn't tell which Lenticularis URL was
in use from the logs alone — directly relevant given P1-6's dev/prod mixup.

**Fix**:
- `model_config = ConfigDict(extra="forbid")` on `MeteoSwissConfig`, `LenticularisConfig`, and
  `Config` — verified locally (no eccodes/Linux needed, pure pydantic): both the real
  `config.yml` and `config.yml.example` still parse; an unknown top-level key and a typo'd
  nested key are both now rejected with a `ValidationError`.
- `get_config()` now resolves the absolute path, logs CRITICAL and raises `FileNotFoundError`
  explicitly if missing (same exception type as before, but with an intentional log line and a
  clear message instead of an accidental one from deep in a collector).
- Every resolved non-secret key logged at INFO on first load: both `stac_base_url`s, both
  collection IDs, `lenticularis.base_url`, `grib_cache_dir`.
- `main.py`'s lifespan now calls `get_config()` explicitly, before `init_db()`/`load_cache()` —
  fail-fast and the config log line both happen at the very start of startup, not whenever the
  first collector/route happens to touch it.
- **Order dependency respected**: this landed after P1-6 removed the dead `scheduler:` block
  from the local `config.yml` — `extra="forbid"` would otherwise have broken startup on this
  box's own config file.

**Verify** (pending PRD deploy): startup log shows the `Config loaded from ...` line with all
five values; deliberately introducing a typo'd key in `config.yml` now fails startup with a
clear `ValidationError` instead of silently doing nothing.

### v0.3.17 — P3-1 tagged releases can no longer ship on a red test

**Finding**: `docker-publish.yml` triggers on `push.tags: ["v*"]`; `integration-test.yml`
triggers on `push.branches: ["main"]` + PRs. GitHub's `push.branches` filter does not match tag
refs, so a tag push runs **only** `docker-publish.yml` — no `workflow_run`, no `needs:`, no
required status check ever gated it. The image, including the mutable `:latest` tag that
`docker-compose.yml` pins the deployment to, published regardless of test state. This is not
theoretical: CI was red for 10 weeks (2026-05-08 → 2026-07-16) and a broken eccodes stack
reached PRD as `v0.3.3` during that window (see the v0.3.4 entry above).

**Fix**: added a `test` job to `docker-publish.yml` (same steps as `integration-test.yml`
— checkout, Python 3.11, `poetry install --with dev`, `pytest -m integration`) and made
`build-and-push` depend on it via `needs: test`. Adds ~5 min to a release; that's the right
trade. This is this project's safety net for the riskier collector-internals work still ahead
(P0-4.2/P1-4/P3-5) — landing it first, per the plan's own sequencing.

**Verify**: next tag push shows the `test` job running before `build-and-push` starts; a
deliberately failing test on a tag blocks the image from publishing.

### v0.3.18 — P2-7 circular wind-direction min/max

**Finding**: `services/ensemble.py`'s `compute_wind_direction_stats` used builtin `min()`/
`max()` on a list of degrees, unlike `compute_stats` which correctly uses
`np.nanmedian`/`nanmin`/`nanmax`. Two defects: builtin min/max isn't NaN-safe, and min/max of a
circular quantity is meaningless — members at 359°/1° report a fake 358° spread instead of the
true 2°, which for a paragliding tool is exactly the case that matters (wind direction crossing
north).

**User confirmed** (this changes API-visible values Lenticularis consumes): fix to circular
statistics.

**Fix**: `min`/`max` are now each member's offset from the circular `probable` (median),
wrapped to `[-180°, 180°)` via `((angle − probable + 180) % 360) − 180`, taking `nanmin`/
`nanmax` of the offsets, then re-adding to `probable` and wrapping back to `[0, 360)`.
Verified locally (pure numpy, no eccodes needed): `[359°, 1°]` → `min=359°, max=1°` (true 2°
spread, was 358°); `[80°, 100°, 120°]` (no wraparound) → unchanged `min=80°, max=120°`,
confirming the fix generalizes the old behavior rather than replacing it outright; a NaN
member is now correctly skipped instead of poisoning the result.

**Verify** (pending PRD deploy/CI): the new `services/ensemble.py` unit test (P3-2) pins these
three cases so the fix can't silently regress.

### v0.3.19 — P3-2 unit tests + ruff

**Finding**: one test function gated 3,450 lines of source, no lint (ruff configured in
`pyproject.toml` but never installed, so `poetry run ruff check` couldn't run — this is why
the dead imports in P2-6 survived undetected), no fast test lane (every signal cost ~5 min and
a 172 MB download).

**Fix**:
- New `unit` job in `integration-test.yml`, running on every push/PR: `poetry install --with
  dev`, `ruff check .`, `poetry check --lock`, `pytest -m "not integration"`. Genuinely fast —
  no network, no GRIB.
- `ruff = "^0.8"` added to the dev group. **Regenerated `poetry.lock` on this Windows dev box**
  — normally forbidden territory for this repo (Poetry-on-Windows locking is the exact failure
  class that dropped `eccodeslib` once, v0.3.4) — but done safely this time: resolved in an
  *isolated scratch copy* first, diffed against the tracked lock, confirmed the **only** change
  was the new `ruff` entries and the content-hash (every `eccodes`/`eccodeslib`/
  `eccodes-cosmo-resources-python` pin byte-identical), *then* copied it over. The explicit
  `eccodeslib` stanza in `pyproject.toml` is exactly what makes this safe — it doesn't depend
  on which platform's wheel metadata Poetry's resolver happens to read.
- New unit tests (all pure, verified locally where the module's own imports allow it —
  `services/ensemble.py` and `database/cache.py` need no eccodes/scipy and were actually run
  and passed on this box; `icon_ch1_eps.py`'s helpers and the forecast router import eccodes
  transitively at module load, same as the existing integration test, so those are verified by
  hand-checked reimplementations run against local numpy and will execute for the first time
  in CI):
  - `tests/test_ensemble.py` — `compute_stats` NaN-skipping, `compute_wind_direction_stats`
    circular fix (P2-7) both wraparound and non-wraparound cases, NaN-safety.
  - `tests/test_cache_persistence.py` — combined grid store horizon-slicing, dirty-flag
    lifecycle (including proving an untouched grid's npz is *not* rewritten), and a full
    save→wipe→load round trip on both grid files. This is the exact regression the plan
    flagged: the store's `_meta` array is positional/unversioned, so a field reorder would
    silently misinterpret a persisted cache with no error.
  - `tests/test_collector_helpers.py` — `_interp_to_heights` (identity, linear-weight,
    below-terrain-null, above-top-null, shape-mismatch-returns-all-NaN — the exact cases
    `.ai/RESUME.md` had claimed were "unit-tested locally" for v0.3.6 but never committed),
    `_deaccumulate`, `_compute_rh_from_td` (Magnus formula known-value + saturated + clipping),
    `_latest_ref_dt` at three boundaries (exact 2h, 1s inside the guard — the precise edge that
    caused the v0.3.1 data-loss bug — and the midnight day-wrap).
  - `tests/test_forecast_router.py` — `_budget_error`/`_MAX_RESPONSE_CELLS` boundary math.

**Known, not fixed here**: `ruff check .` currently reports ~84 pre-existing findings (46
semicolon-joined statements, 29 unused variables — mostly the exact dead code P2-6 already
enumerates, 6 unused imports, 3 misplaced imports, 1 ambiguous name), all in
`icon_ch1_eps.py`/`icon_ch2_eps.py`/`db.py`. Left alone on purpose — fixing them here would be
exactly P2-6's job and a much larger diff than "add the lint gate." The `unit` job will show
red until P2-6 lands in Group 6. **This does not gate releases**: `docker-publish.yml`'s P3-1
`test` job only runs `pytest -m integration`, independent of `unit`.

**Verify**: CI shows the new `unit` job passing its ruff/lock/pytest steps except the
pre-existing ruff findings (expected, tracked); `e2e` unaffected.

### v0.3.20 — P3-3 dependabot

**Finding**: `.github/` contained only `workflows/` — no `dependabot.yml`, no `renovate.json`.
This repo's worst incident (v0.3.4) was unmanaged dependency drift; nothing was watching for
it going forward.

**Fix**: `.github/dependabot.yml` — `pip` + `github-actions` ecosystems, weekly, each grouped
into a single PR. `eccodes`/`eccodeslib`/`eccodes-cosmo-resources-python` explicitly excluded
via `ignore:` — those three are deliberate and must move together by hand, behind a green
integration test, never as an automatic PR.

**Verify**: next scheduled dependabot run opens at most one grouped `pip` PR and one grouped
`github-actions` PR, with no PR touching the eccodes triplet.

### v0.3.21 — P3-4 CI actions bumped, cached, concurrency-guarded, arm64 dropped

**Finding**: `actions/checkout@v4` and `setup-python@v5` run on the deprecated Node 20 runtime
(a hard break once GitHub removes it); no dependency caching anywhere, so every run
re-downloaded numpy/scipy/eccodeslib wheels; no `concurrency` group, so two pushes to `main`
could run two 172 MB integration tests in parallel against the live MeteoSwiss API;
`docker-publish.yml` built `linux/amd64,linux/arm64` under QEMU emulation with no evidence
arm64 is deployed anywhere.

**Fix** (both `integration-test.yml` and `docker-publish.yml`):
- `actions/checkout@v4` → `@v5`, `actions/setup-python@v5` → `@v6`.
- `pipx install poetry` moved before `setup-python`, which now sets `cache: "poetry"` — this
  ordering matters: `setup-python`'s Poetry cache integration shells out to `poetry config` to
  locate the cache path, so Poetry must already be on PATH when that step runs.
- `concurrency: {group: "${{ github.workflow }}-${{ github.ref }}", cancel-in-progress: true}`
  added at the workflow level in both files.
- **User confirmed**: dropped `linux/arm64` from `docker-publish.yml`'s build matrix — amd64
  only now. Removed the now-unneeded `docker/setup-qemu-action` step entirely.
- Left alone (hedged in the plan, not a firm ask): the integration test's
  `len({round(s,1) for s in speeds}) >= 2` distinctness assertion, which technically assumes
  real vertical wind shear at Interlaken rather than being a pure code invariant — changing a
  load-bearing regression assertion carries its own risk that outweighs the modest benefit here.

**Verify**: next CI run shows the Poetry cache being restored (`Cache restored from key: ...`)
and completes faster; a second push while one run is in flight cancels the superseded run;
the published image manifest lists only `linux/amd64`.

### v0.3.22 — P2-3 orphaned temp cache files now self-heal

PRD had a 161 MB `grid_cache_ch1.tmp.npz` orphaned since Jul 16 — `_remove_legacy_grid_files()`
only knew specific legacy filenames, not the `.tmp` pattern a partial write leaves behind.
`load_cache()` now also globs `*.tmp.npz`/`*.tmp` in the data dir and unlinks whatever it
finds (INFO log per file) — a temp file present at boot is by definition an interrupted write.
Deleting the existing PRD file is the user's call, not done here.

### v0.3.23 — P2-6 dead code removed (84 → 4 ruff findings)

Delegated to a subagent with the plan's exact P2-6 catalogue (each item to be re-verified via
Grep, not trusted by line number, since earlier commits this session shifted things); reviewed
its full diff myself before committing. Removed: 5 unused Pydantic response models in
`models/forecast.py` (`GridPoint`/`GridFrame`/`GridForecastResponse`/`ThermalGridFrame`/
`ThermalGridResponse`, ~76 lines — the hand-built-dict decision from v0.3.3 stands, these were
just dead schema classes nothing constructed); `known_stations` unused import; `_parse_horizon_h`
+ `import re`; `_THERMAL_ACCUM_VARS`/`_THERMAL_SURFACE_VARS`; the `keep: bool = False` param on
both collectors' `_fetch_step` (never passed non-default); `_GRID_LATS`/`_GRID_LONS` (written,
never read); CH2's unused `_deaccumulate`/`_horizon_str` imports; per-collector unused locals
from dead `surf_array`/`deaccum` calls (`clct`/`clcl`/`clcm`/`clch`/`hzerocl`/`cape_ml`/
`cin_ml`/`dursun_min`/`solar_direct`/`solar_diffuse`/`lat`/`lon`/`elev` — each verified to
appear exactly once in the pre-change file, i.e. genuinely dead, not silently relied on); plus
two I found in my own review pass that weren't in the plan's exact list but are the same class
of finding: `dashboard.py`'s dead `cached_init`/`cached_model` (also referencing a `"model"` key
`station_cache_detail()` doesn't even return at that nesting level) and `grib_cache.py`'s
unused `AbstractContextManager` import. Also split all 46 ruff-flagged semicolon-joined
statements and fixed 1 ambiguous variable name (`l` → `lvl`). Fixed the two stale docstrings
P2-6 called out: `GridWindCache`'s "(not persisted)" header (it's persisted since v0.3.3) and
the `/grid`/`/thermal-grid` router docstrings, which still named the now-deleted response
models — rewrote them to describe the actual plain-dict shape.

`PRESSURE_VARS`/`CH1_PRESSURE_VARS` left alone as instructed — both identical `["U","V","W"]`
lists but genuinely used from different places (CH1 uses `CH1_PRESSURE_VARS` internally, CH2
imports and uses `PRESSURE_VARS`).

**Verify**: `python -m py_compile` on all 7 touched files passes; `ruff check .` 84 → 4
findings (remaining 4 all in `scripts/diag_interlaken.py`, which P3-6 deletes next); local
pure-Python tests (`test_ensemble.py`, `test_cache_persistence.py`) still pass unaffected.

### v0.3.24 — P2-8 all-NaN reduction warnings fixed

**Finding**: `np.nanmin`/`nanmax`/`nanmedian` on an all-NaN slice warn — `services/ensemble.py`
and two spots in `icon_ch1_eps.py`'s grid-cache builders were unguarded, unlike
`_interp_to_heights` which already uses `np.errstate`. Post-v0.3.6 an all-NaN slice is expected
(a band below terrain, or every ensemble member failing a step), so this was pure noise
masking real warnings — CI reported "10 warnings".

**Fix**: `compute_stats`/`compute_wind_direction_stats` now check `np.all(np.isnan(...))` first
and skip the reducers entirely rather than call something guaranteed to warn — return value is
unchanged (still NaN in, NaN out; the existing Pydantic `nan_to_none` validator already turns
that into `null` at the API boundary, so no caller-visible change). Verified locally with a new
test that wraps `warnings.simplefilter("error")` around the call — proves the warning no longer
fires, not just that the code "looks" guarded. `icon_ch1_eps.py`'s three grid-level reduction
sites (thermal grid's `_median`/`_nanmin`/`_nanmax` helpers, and the wind-grid's per-altitude
`ws_cache`/`wd_cache` loop and `rh_cache` line) now wrap in `np.errstate(invalid="ignore")`,
same pattern as `_interp_to_heights`. No `warnings.filterwarnings` blanket suppression anywhere.

**Verify** (CI): the `10 warnings` line in the integration test's pytest summary should drop
to whatever's left after this and P2-9 (already applied) — any remaining warning is now
something to actually look at.

### v0.3.25 — P2-11 one error envelope, not four

**Finding**: `_err()` in `forecast.py` returns the documented flat `{"error": {...}}`, but the
`HTTPException(detail={"error": {...}})` paths (404/503 in `station_forecast`/
`altitude_winds`) got double-wrapped by FastAPI's default handler into
`{"detail": {"error": {...}}}`. `dashboard.py`'s `/api/stations` 502 was a third shape
(`detail=str(exc)`, which also leaked the upstream Lenticularis URL into a public response).
FastAPI's own 422 was a fourth. Four shapes, one documented contract
(`07-api-conventions.md`).

**Fix**:
- `@app.exception_handler(StarletteHTTPException)` in `main.py`: if `exc.detail` is already a
  dict carrying an `"error"` key, pass it straight through with the original status code
  instead of letting it get re-wrapped; otherwise wrap a plain string detail into the same
  envelope (`{"code": "http_error", "message": ...}`).
- `@app.exception_handler(RequestValidationError)`: FastAPI's automatic 422s now return
  `{"error": {"code": "validation_failed", "message": ..., "details": {"errors": [...]}}}`
  instead of the framework default shape.
- `dashboard.py`'s `/api/stations` proxy: logs the real `httpx.HTTPError` server-side
  (`exc_info=True`) and raises a generic `{"error": {"code": "upstream_unavailable", ...}}` —
  no more upstream URL/exception text in the public response body.
- **Verified locally** with an isolated FastAPI `TestClient` (no eccodes needed — plain
  fastapi/starlette, which this dev box does have) reproducing all three of the previously
  different shapes and confirming they now all normalize to `{"error": {...}}`.

**Verify** (pending CI/PRD): `?station_id=` for a real-but-unknown station now returns
`{"error": {...}}` directly (not `{"detail": {"error": {...}}}`); a malformed query param
returns the same envelope shape at 422; `/api/stations` with Lenticularis down returns a
generic message with no URL leak, while the real error still appears in the logs.

### v0.3.26 — P2-12 `docs/forecast-data-reference.md` rewritten

**Finding**: this user-facing doc described the implementation v0.3.6 deleted (an
altitude→pressure table for a mechanism with no `pv` coordinate to use), stated wrong
horizons/cadence/member counts, wrong units (m/s instead of km/h for wind), the wrong humidity
source (`QV`+Bolton instead of `TD_2M`+Magnus), and — the biggest structural error — presented
solar/cloud/CAPE/CIN/BLH as if they were fields on the per-station hourly response, when they
have only ever lived on `/thermal-grid`, a separate spatial-grid endpoint. `HPBL`/`HBAS_CON`
(boundary layer height, convective cloud base) aren't published upstream at all and were never
real fields.

**Fix**: rewrote the whole document against `architecture.md`, `04-constraints.md`, and the
actual Pydantic models (`StationForecastHour`, `AltitudeWindLevel`, `ThermalGridCache`) as
ground truth. Deleted the altitude→hPa table entirely. Split the reference into one section
per actual endpoint (`/station`, `/altitude-winds`, `/thermal-grid`, `/wind-grid`) instead of
one undifferentiated variable list, and rewrote the paragliding-workflow checklist to name
which endpoint each field actually comes from.

**Also**: the oldest dated entry in this file (2026-04-18)'s "Known Issues / Deferred" section
listed five items that are all resolved by sessions above it — added a header note marking it
historical (all five annotated with what fixed them) rather than deleting it, since it's a
legitimate record of what was true on that date.

**Left alone**: `features.md`'s own "Known Issues" `sunshine_minutes` entry is the *same* stale
claim P2-12 flagged — deliberately not touched here, since deleting it properly is P1-11's job
(it needs the accompanying real-fallback-bug fix, not just doc surgery).

### v0.3.27 — P3-6 `scripts/diag_interlaken.py` deleted

**Finding**: imported `_extract_station` from `icon_ch1_eps.py` — a symbol that has never
existed anywhere in `src/` (confirmed: the only repo-wide hit was the import line itself). The
script raised `ImportError` before executing a single line, and had been rotting undetected
because there was no lint (fixed by P3-2). Also hardcoded a container path
(`sys.path.insert(0, "/app/src")`) so it never ran from a plain checkout either.

**Fix**: deleted. Confirmed no live references anywhere outside historical RESUME.md/
features.md log entries (which correctly stay as-is — they're a record of what was true at the
time, same reasoning as the P2-12 "Known Issues" annotation above).

**Verify**: `ruff check .` — 4 → **0** findings. The lint gate added in P3-2 is now fully green.

### v0.3.28 — P3-10 repo hygiene (+ P3-7 remote.ps1)

**Findings**: `.claude/settings.local.json` (a per-developer `*.local.*` file) was tracked with
no `.claude/` gitignore entry, causing recurring spurious diffs; `.gitignore` was a 212-line
unmodified GitHub Python template covering Django/Scrapy/Celery/SageMath/Marimo/Abstra/Cursor
— none used by this project — while missing the two things that actually mattered
(`config.yml`, `.claude/`); the healthcheck was declared in both `Dockerfile` and
`docker-compose.yml` (two copies to keep in sync, the compose one redundant since Docker
inherits the image's `HEALTHCHECK` unless overridden); `start-period=10s` was thin given
`load_cache()` must JSON-parse ~170 MB and `np.load()` two npz files before uvicorn serves
anything.

Folded in **P3-7** (an item the plan's own sequencing table omitted, but the user already
confirmed during the up-front decisions round): `scripts/remote.ps1`, ~85% duplicate of
`LSMF-dev.ps1`, deployed the **base** compose with no dev overlay and printed the **production**
hostname (`lsmfapi.lg4.ch`) on success — exactly the footgun `README.md:266` warns against in
bold. No references anywhere outside itself.

**Fix**:
- `git rm --cached .claude/settings.local.json` (kept on disk); `.claude/` added to
  `.gitignore`.
- `.gitignore` trimmed from 212 to ~35 lines — kept only what this Python/FastAPI project
  actually produces (bytecode, venvs, coverage, mypy/ruff caches, logs) plus the
  project-specific secrets/state section.
- Deleted `scripts/remote.ps1`.
- Removed the `healthcheck:` block from `docker-compose.yml` (Dockerfile's `HEALTHCHECK` now
  the single source); bumped `--start-period` 10s → 60s in the `Dockerfile`.

**Deferred, noted but not done**: `pyproject.toml`'s legacy `[tool.poetry]` → PEP 621
`[project]` migration — the plan itself calls this low priority, and restructuring the file's
core sections without being able to fully exercise `poetry install`/`poetry build` on this
Windows box (see P3-2's lock-regeneration entry for how carefully that has to be handled) isn't
worth the risk for a cosmetic change. The Dockerfile's two `poetry install --only main` calls
are intentional layer caching (deps layer first, cached across source-only changes; the second
call is fast since deps are already resolved) rather than a bug — left alone; the
`PYTHONUNBUFFERED`/`PYTHONDONTWRITEBYTECODE` env vars P3-10 also flagged are deferred to P2-2,
which touches this same Dockerfile next.

**Verify**: `git status` shows `.claude/settings.local.json` as untracked-but-present; a fresh
`docker compose up` still gets a working healthcheck from the image alone.

### v0.3.29 — P2-2 Dockerfile pinning + .dockerignore

**Finding**: `FROM python:3.11-slim` floated with no digest — the exact class of drift that
caused the v0.3.3 PRD crash loop when the tag silently rolled bookworm→trixie (apt libeccodes
2.28→2.41.0). `pip install poetry` (Dockerfile) and `pipx install poetry` (both CI workflow
files, 3 sites) were all unversioned — again, literally the drift class that caused v0.3.4.
No `.dockerignore` — build context shipped `.git/`, `.ai/`, `data/`, etc. on every build.

**Fix**:
- Fetched the *real* current manifest-list digest for `python:3.11-slim` directly from the
  Docker Hub registry API (never invented): `sha256:db3ff2e1800a8581e2c48a27c3995339d47bd
  f046da21c7627accd3d51053a93`. Comment records the pin date so staleness is visible later.
- Pinned Poetry to `2.2.1` in the `Dockerfile` and all 3 CI call sites
  (`integration-test.yml`'s `unit`+`e2e` jobs, `docker-publish.yml`'s `test` job) — the same
  version this session already used locally to safely regenerate `poetry.lock` for P3-2.
- Added `.dockerignore` (`.git/`, `.github/`, `.ai/`, `.claude/`, `specs/`, `docs/`, `tests/`,
  `scripts/`, `data/`, `grib-cache/`, `*.md`, `config.yml`, caches). Nothing in it was ever
  `COPY`'d into the image (no `COPY . .` anywhere), so this is a build-speed fix, not a
  disclosure fix.

**Verify** (CI): next build shows the pinned digest resolving without a re-pull warning;
`poetry --version` in the build log reads exactly `2.2.1` everywhere; build context size drops.

### v0.3.30 — P2-4 unused dependencies removed

**Finding**: `cfgrib`, `xarray`, `aiofiles` — zero imports anywhere in `src/` (`cfgrib` appeared
only in a docstring, now fixed to say "eccodes" instead). Collectors use the raw `eccodes` API
directly, not `cfgrib`/`xarray`. `xarray` was additionally pinned `^2024.0` — a caret on a
**CalVer** package permanently freezes it in calendar-2024, unable to ever receive a 2025+ fix.

**Fix**: removed all three from `pyproject.toml`. `poetry.lock` regenerated the same verified
way as P3-2's `ruff` addition — resolved in an isolated scratch copy first, diffed against the
tracked lock before applying. Confirmed: only `cfgrib`/`xarray`/`aiofiles` and their
transitive-only dependencies (`pandas`, `python-dateutil`, `pytz`, `six` — nothing else needed
them) were removed; the `eccodes`/`eccodeslib`/`eccodes-cosmo-resources-python` versions are
byte-identical to before.

**Verify** (CI): `poetry install` + integration test stays green with a smaller dependency
tree; removes a large GRIB-adjacent chunk of the image and its attack surface.

### v0.3.31 — P2-5 numpy cap relaxed (landed alone, per the plan)

**Finding**: `numpy = "^1.26"` locked to `1.26.4` (Feb 2024) with **no dependency actually
requiring `<2.0`** — `scipy` declares `numpy>=1.26.4,<2.7`, `eccodes`/`eccodes-cosmo-resources`
declare no numpy bound at all. The ceiling came purely from Poetry's caret. Cost: real friction
— 1.26.4 has no `cp313` wheel, which is exactly why `poetry install` fails on this dev box —
and it makes `python = "^3.11"` (i.e. `<4.0`) a false advertisement, since only 3.11 has ever
actually been exercised by CI.

**Fix**: relaxed to `numpy = ">=1.26,<3"`. Regenerated `poetry.lock` the same verified way as
P2-4/P3-2 (isolated scratch copy, diffed before applying). **The diff was empty except the
content-hash** — Poetry's resolver kept the exact same `numpy==1.26.4` already in the lock
rather than eagerly jumping to 2.x, since nothing forces an upgrade during a plain re-lock. So
**this commit changes zero runtime behaviour today** — CI will still run against 1.26.4 exactly
as before. What it does change: the ceiling is gone for whenever a numpy 2.x bump is actually
proposed (dependabot or manual) — per the plan, **that must not be batched with anything else**
either, since numpy 2.x changed several semantics and the integration test is the only thing
that would catch a regression.

**Verify** (CI): integration test stays green, confirming 1.26.4 still resolves and works
identically — this change is a no-op today by design.

### v0.3.32 — P1-10 STAC search can no longer stitch two model runs together

**Finding**: `_search_item_url` sent an **open-ended** `forecast:reference_datetime` interval
(`f"{ref_dt}/.."`) and took `features[0]` with no `sortby` — feature order is server-defined.
A collection run takes 1.5–2h and MeteoSwiss publishes continuously, so if a newer run appears
mid-collection, later horizons could resolve to a **different `ref_dt`** than the one stamped
into the response's `init_time` — a silently mixed-run forecast. Also picked an arbitrary asset
via `next(iter(assets.values()))` with no signal if more than one came back.

**Fix**: closed interval (`ref_dt/ref_dt`) so only the intended run can match. Kept the reordering
as a re-usable `ref_dt_str` local rather than duplicating `strftime`. Added a WARNING log when
a search returns more than one asset (unexpected — would previously fail silently either way).
Both CH1 and CH2 share this function (CH2 imports it from `icon_ch1_eps.py`), so one fix covers
both collectors.

**Verify** (CI/PRD): no behavioural change expected in the common case (one run, one asset);
watch for the new WARNING log line if MeteoSwiss's STAC catalog ever does return >1 asset for
a single (variable, horizon, ref_dt) query.

### v0.3.33 — P1-11 two silent-fallback bugs fixed, stale Known Issue deleted

**Finding 1 (real bug)**: CH2's `_get_prior(var)` — the deaccumulation baseline for the h34
delta — fell back to `np.zeros(...)` (misleadingly named `nan_prior`; it was never NaN) on
*any* failure of the shadow h33 fetch (task missing, cancelled, exception, shape mismatch),
with **no log and no telemetry**. A zero baseline means h34's delta = the *full 34-hour*
accumulated total misread as a 1-hour rate — precipitation up to **~34× too high**, silently.

**Fix 1**: renamed to `_prior_fallback`, now genuinely NaN. Every fallback path now logs
WARNING (naming the variable) and calls `_telemetry.record_download_error` before returning
it, so a failure surfaces as `null` downstream (matching how every other missing-data case in
this codebase behaves) instead of a plausible-looking wrong number with no trace anywhere.

**Finding 2 (related, same area)**: CH1's thermal-grid solar/sunshine baseline
(`prev_aswdir`/`prev_aswdifd`/`prev_dursun`, used by both CH1 and CH2 via the shared
`_build_thermal_grid_cache`) only advanced *inside* the success branch of each per-horizon
`if raw is not None` check. A single missing horizon left the baseline stale — the *next*
successful horizon's delta then silently spanned 2 hours of accumulation while being divided
by 3600 as if it were 1 hour, double-counting solar/sunshine. The per-station path already
yields NaN in this exact situation (via the shared `_deaccumulate` helper's own NaN-propagation
behaviour), so grid and station outputs diverged.

**Fix 2**: added explicit `aswdir_baseline_ok`/`aswdifd_baseline_ok`/`dursun_baseline_ok` flags.
A missing horizon now flips the relevant flag(s) to `False`, which makes the *next* step's
cache row stay NaN too (both a WARNING log line and telemetry weren't added here — the plan
only asked for it on P1-11's CH2 finding — but a WARNING log was added since it's the same
"silent" class of bug) instead of silently computing a doubled value. The `True` baseline for
true model start (h0, no `accum_prior_h`) is unaffected.

**Also**: deleted `features.md`'s stale "Known Issues" entry claiming `sunshine_minutes` is
wrong on CH2's first step — verified `ACCUM_PRIOR_H` (`icon_ch2_eps.py:75`) + the shadow h33
fetch + `deaccum`'s prior-differencing already handle this correctly. The entry pre-dated that
fix and was never removed.

**Verify** (CI/PRD): deliberately fail the h33 shadow fetch (or a mid-run horizon) and confirm
the affected step(s) come back `null` with a WARNING log line and (for CH2) a telemetry entry,
rather than a plausible-but-wrong number with no trace.

### v0.3.34 — P3-9 silent failure paths

Six findings from the plan's "silent failure paths that hide data loss" catalogue, six
different fixes:

1. **`_read` missing file** (thermal-grid builder, both CH1 and CH2 via the shared function) —
   `if not dest.exists(): return None` had no log (only the exception branch did). Now logs
   WARNING naming the variable/horizon before returning `None`.
2. **CH2 missing STAC asset** — `if url is None: return None` was silent; CH1's equivalent
   already warned (`"CH1 STAC: no asset found..."`). Added the matching CH2 log line.
3. **Grid collection failure invisible to the dashboard** — both collectors' `collect_grid()`
   call sites caught the exception and only `logger.exception`'d it. Added
   `_telemetry.record_download_error(model, "grid", 0, str(exc))` alongside the existing log —
   deliberately **not** `collection_state.mark_failed()`, since station data did succeed and
   marking the whole run failed would be a worse lie than the one being fixed. Now a no-grids
   run shows up in the dashboard's error panel instead of reporting fully successful.
4. **`_eccodes_get`'s `perturbationNumber` fallback** — a read failure silently defaulted to
   member 0, and multiple such defaults in one file would collapse distinct ensemble members
   onto the same output row with no signal. Added a per-file counter: exactly one default is
   expected (the control run, which genuinely has no `perturbationNumber`); more than one now
   logs a WARNING naming the count.
5. **`_deaccumulate`'s `prepend=arr[:1,:]*0`** — replaced with `np.zeros_like(arr[:1,:])`. The
   old idiom turns `NaN*0` into `NaN` when the first step's data is missing, silently breaking
   the intended "no prior accumulation" zero baseline. Verified locally (no eccodes needed —
   pure numpy): a new test wraps `warnings.simplefilter("error")` around a NaN-first-step case
   and confirms it still doesn't warn, while pinning the resulting NaN pattern
   (`[NaN, NaN, 10.0]` — steps 0 and 1 are unavoidably NaN once step 0's data is missing;
   step 2 onward is unaffected).
6. **Left alone, deliberately**: `_fetch_step`'s `unlink()` in a `finally` after a parse
   failure. The plan flagged this as 🔎 (reported, not fully verified) — and on inspection, it's
   consistent with this project's own established, deliberate pattern (corrupt GRIB
   self-deletes so it re-downloads next run, shipped in v0.3). Changing it without solid
   evidence risks the opposite failure mode: a corrupt file that never gets cleaned up and
   fails to parse identically on every subsequent run forever.

**Verify** (CI/PRD): the new unit test (`test_deaccumulate_nan_first_step_does_not_warn`)
passes; a deliberately missing GRIB file or STAC asset now produces a WARNING log line where
previously there was none; a forced grid-collection exception now appears in the dashboard's
error panel.

### v0.3.35 — P0-4.2 partial fix: W deleted immediately (U/V deferred, see below for why)

**What I found tracing the real code, before touching anything**: the plan's literal P1-4
suggestion — "extract station + grid-sample columns in one read inside `_fetch_step`, then
`unlink()` immediately" — has a hidden problem for the pressure-level 3D vars (U, V, W).
The grid builders (`_build_grid_wind_cache`/`_build_thermal_grid_cache`) process horizons
**one at a time on purpose**, to keep memory bounded (their own docstrings say so). If
`_fetch_step` stashed every horizon's grid-extracted array into a dict for the grid-builder to
consume in its later, separate phase (as the plan's text implies), **CH1 alone would hold
~26 GB of pressure-level grid arrays in memory simultaneously** (34 horizons × 2 vars ×
~390 MB) — trading the ~200/480 GB disk-OOM this item exists to fix for a memory-OOM instead
(this project already had a real OOM incident, v0.3.3). Worse: the integration test patches
`HORIZONS` down to `[0, 6]` for speed, so this exact regression would go **green in CI** and
only surface in production at full 34/87-horizon scale.

**Discussed with the user** (twice — first on the general approach, then on this specific
finding): confirmed doing the *actually* safe version, which requires interleaving grid
computation with the download phase itself (compute-and-discard each horizon's grid arrays as
soon as that horizon's data arrives, bounded by the existing `DOWNLOAD_CONCURRENCY` semaphore,
instead of deferring all of it to a phase that runs after every file is already downloaded).
That is a materially bigger rewrite than either the plan or the original ask scoped — it
touches `_fetch_step`'s signature, the whole `fetch()`/`surf_tasks`/`pres_tasks` scheduling
structure, and both grid-builder functions' entire data source, for both collectors.

**What actually shipped this pass** — the safe subset with zero memory-risk, verified by
tracing every reference:
- `W` (the vertical-wind pressure variable) is used **only** by station-level altitude winds
  (`pres_array("W")` → `w_pl`, read once from the in-memory task result) — confirmed the grid
  build (`_build_grid_wind_cache`) only ever reads `U`/`V`/`T_2M`/`TD_2M`, never `W` (matches
  the wind-grid having no vertical-wind field, per the P2-12 doc rewrite). So `W`'s file is
  safe to delete **immediately** after its single station-level read — nothing else will ever
  touch it.
- `_fetch_step` gained `delete_after_read: bool = False`, set to `True` only for `"W"` in both
  collectors' `fetch()` wrappers. Removed the now-redundant bulk `for _wh in HORIZONS: unlink
  W` loop that previously ran *after* the entire download phase completed — that loop is what
  let all of a run's `W` files (~1.8 GB each) sit on disk simultaneously in the first place.
- Net effect: `W`'s contribution to the CH1 (~201 GB) / CH2 (~480 GB) peak — roughly a third
  of it, since U/V/W are the three same-sized 3D variable types — is gone. `U`/`V` still
  persist until the grid build's later separate read+delete, same as before.

**Deliberately not done in this pass**: the `U`/`V` interleaving described above. This remains
open, correctly understood now as materially larger in scope than "combine two reads into
one," and needs its own dedicated design/implementation effort rather than being rushed
through in an already very long session with zero ability to test against real GRIB data or
real memory behavior.

**Verify** (CI/PRD, the only real proof available): `du -sh` on the GRIB cache dir mid-run
should show a visibly lower peak than before, with `W_*.grib2` files never accumulating past a
handful at a time (bounded by `DOWNLOAD_CONCURRENCY`) regardless of how many horizons have
been scheduled.

### v0.3.7 — CH2 cron misfire fixed (dashboard showed CH2 stuck stale while CH1 kept updating)

**Trigger**: user reported on `lsmfapi.sdh.lol` (v0.3.6, container up 8 days) that CH2's cache
looked outdated while LSMFAPI "just sat there" and then moved on to collect CH1 — wanted the
service to prioritize catching up whatever is stale instead of proceeding on schedule and
re-downloading data it already has.

**Diagnosis** (read-only, via `ssh sdh` + `docker logs`/`docker exec curl` into the running
container — no changes made to PRD): `/api/dashboard` showed CH2 `ref_dt: 2026-07-30T00:00:00Z`,
`expected_ref_dt: 06:00:00Z`, `is_current: false`, while CH1 was healthily mid-collection for the
current `12:00Z` run. `docker logs lsmfapi --since 2026-07-25` surfaced the smoking gun — twice
in five days, CH2-only: `Run time of job "_run_ch2eps ..." was missed by 0:00:01.767140` /
`0:00:06.978634`, each with no matching `"executed successfully"` line. CH1 never missed once.
APScheduler's `add_job()` calls had no `misfire_grace_time`, so a few seconds of executor jitter
(plausibly from CH2's own ~2h `collect()` perturbing the loop) made APScheduler **skip the
trigger outright** rather than run it late — leaving the model stuck on the prior ref_dt for a
full 6h cycle with `last_error` still null (the job function was never even invoked).

**Fix (v0.3.7)**: `misfire_grace_time=1800` added to both `collect_ch1eps`/`collect_ch2eps`
`add_job()` calls in `scheduler.py`. Confirmed safe to run late: `_latest_ref_dt()` /
`_latest_ref_dt_ch2()` compute the target run from wall-clock at call time (not the cron slot),
and `grib_cache.py`'s persistence already skips re-downloading anything already fetched for that
ref_dt — so a late-but-not-skipped run only ever fetches what's actually missing, never repeats
work. `pyproject.toml` bumped 0.3.6 → 0.3.7. See [[altitude-winds-hhl-fix]] for the previous
session's unrelated v0.3.6 fix.

**Still open**:
- **Not yet deployed to PRD.** This is a code-only fix in the repo; PRD (`lsmfapi.sdh.lol`,
  container `lsmfapi`, image `ghcr.io/think4dvantage/lsmfapi:v0.3.6`) still runs the old
  scheduler and CH2 will remain stale until the next lucky on-time trigger or a redeploy with
  this fix. Deployment to that host was intentionally left for the user/pipeline — PRD is
  off-limits to direct changes per the constitution in `00-ai-usage.md`.
- No reproduction test added — this is an APScheduler timing/library behavior, not application
  logic, consistent with how past scheduler/eccodes infra bugs in this project were verified via
  PRD log evidence rather than pytest (see v0.3.1 scheduler race, v0.3.4 eccodes drift).
- After deploy, confirm on PRD: no further `"was missed by"` log lines for `collect_ch2eps`
  without a matching `"executed successfully"`, and CH2's `/api/dashboard` `ref_dt` advances
  each 6h slot without falling behind CH1.

---

# Resume Notes — 2026-07-21

## What Was Done This Session

### Disk incident → v0.3.6 (altitude winds via MAMSL heights)

**Trigger**: PRD filled `/` (450 GB). `docker system df -v` showed the `lsmfapi` container's
**writable layer at 214 GB** — the `/tmp/lsmfapi_grib` GRIB cache, one 18:00Z cycle. Probing a
`U_000.grib2` showed **1.84 GB per file**: `typeOfLevel='generalVerticalLayer'`, 80 model levels
(1–80), **`pv present: False`**. The grid build deletes U/V per horizon but never W, so 121 W
files (~1.8 GB each) lingered → the 200 GB. GRIB pool was moved off `/` to `/mnt/cache` via the
**separate PRD pipeline repo** (bind mount `/mnt/cache/lsmfapi-grib` → `/tmp/lsmfapi_grib`) — not
in this repo's compose.

**Root cause of the long-standing all-null altitude winds** (confirmed same probe): no `pv` →
`_approx_hybrid_to_pressure_hpa` never ran → `level_coords` fell to raw `[1..80]` →
`_build_level_indices` mapped all 9 pressure targets to the top index **79**. Every altitude band
(and the wind grid at every level) read one model level. See [[altitude-winds-hhl-fix]].

**Fix shipped (v0.3.6)** — interpolate to true MAMSL height instead of pressure:
- `vertical_constants_icon-ch{1,2}-eps.grib2` → `HHL` (81 half-levels, `generalVertical`,
  per-gridpoint, ~172 MB CH1). `_ensure_grid` downloads it once and caches full-level heights
  `_GRID_LEVEL_HEIGHTS` (float16, `z=0.5*(HHL[k]+HHL[k+1])`).
- New `_interp_to_heights(field, level_heights, targets)` — per-column linear interp, NaN below
  terrain. Unit-tested locally (identity + linear weight exact). Rewired station altitude winds
  (CH1+CH2) and `_build_grid_wind_cache`. Removed `ALTITUDE_TO_HPA`, `_build_level_indices`,
  `_approx_hybrid_to_pressure_hpa`, `pv` handling. `ALTITUDE_TARGETS_M` replaces the pressure map.
- **W kept** (user wants vertical wind for thermals) but W files now deleted right after the
  in-memory extraction so they don't leak. `pres_array` arrays made float32 (no interp copy).
- Router `_VALID_GRID_LEVELS` now from `ALTITUDE_TARGETS_M`. Integration test asserts altitude
  winds are distinct/ordered (guards the collapse regression).

**Verification**: interp helper unit-tested locally (identity, terrain-null, exact linear
weight, level-mismatch guard). **Committed + pushed to `main` (`41e7bda`), tagged `v0.3.6`,
tag pushed. CI GREEN — `1 passed, 10 warnings in 297.14s`** (run 29827495256), which now also
exercises the new altitude-winds distinctness assertion, so the HHL height interpolation is
proven against real ICON GRIB. The test adds a ~172 MB vertical-constants download.

**Still open — PRD deploy of v0.3.6 not yet done.** After deploy, confirm on PRD:
- log lines `CH1 model-level heights: 80 levels × 1147980 points, MAMSL range [...]` and
  `CH1/CH2 altitude winds: interpolated U/V/W to 9 MAMSL bands`;
- `/api/forecast/altitude-winds` returns distinct per-height winds with `vertical_wind` populated;
- **Correction (found during the P0-4 tech-debt audit, 2026-07-30): W deletion bounds
  *retention*, not *peak*.** The delete only runs after the whole download phase (`asyncio.gather`
  completes, then `pres_array("W")`), so all three 3-D variables (U/V/W) still coexist on disk
  for every horizon during a run — peak is unchanged at ~200 GB (CH1) / ~480 GB (CH2). See P0-4
  in `specs/001-tech-debt-remediation/plan.md` and [[altitude-winds-hhl-fix]].
See [[env-no-local-linux]] and [[altitude-winds-hhl-fix]].

---

# Resume Notes — 2026-07-16

## What Was Done This Session

### v0.3.5 — grid build moved off the event loop

**v0.3.4 confirmed working on PRD** by collector logs: GRIB parsing succeeds,
`CH1 ensemble members in GRIB: 10`, 500+ stations cached, no crash loop. The eccodes fix below
is verified in production.

That exposed the next bug. With collection finally running to completion, the **web UI became
unreachable while collecting**. `collect_grid()` was called synchronously from inside
`async def collect()` (`icon_ch1_eps.py:1056`, `icon_ch2_eps.py:478`) — no `await`, no thread —
so it held the event loop for the whole grid build. uvicorn served nothing, `/health` included;
the healthcheck (`timeout=5s`, `retries=2`) failed ~30 s in and Traefik dropped the container.

**The tell**: a 7 m 48 s gap in the PRD log with zero output —
`17:10:33 Cached forecast for wunderground-IWENGE3` → `17:18:21 Wind-grid combined store: wrote
34 frames`. CH2 spans 87 horizons vs CH1's 34, so ~20 min. ~1.5–2 h/day across 8 runs, plus
warm-up on every restart. Latent since the grid feature landed.

**Fix**: `await asyncio.to_thread(self.collect_grid, ref_dt, tmpdir, level_indices)` in both
collectors (`asyncio` was already imported in each). eccodes (cffi) and numpy release the GIL, so
the loop keeps serving. Verified by CI: `1 passed in 295.45s` — the integration test drives
`collect()`, so it exercises the threaded path.

**Trade-off accepted**: the slow part builds a *local* object touching no shared state; only
`set_grid_wind_cache()` writes the shared store (sub-second slice copy). A `/grid` reader may see
a torn frame for a fraction of a second 8×/day — better than an 8-minute outage. No lock, since
holding one across the build would recreate the outage for readers.

**CONFIRMED on PRD** (2026-07-16): deployed and the web UI stayed reachable *through* a collection
window. Dashboard also showed Expected = Cached = `2026-07-16T18:00Z` with Currency `✓ up to
date`, i.e. the 20:00Z CH1 cron collecting the 18:00Z run exactly as designed.

**Found, not fixed — altitude winds**: PRD logs
`CH1 level_indices (target_hpa→arr_idx): {500: 79, 600: 79, 700: 79, 750: 79, 800: 79, 850: 79,
900: 79, 920: 79, 950: 79}` — **all nine pressure levels resolve to the same array index 79**.
This is the long-standing "altitude winds U/V/W all null" issue and explains the
`All-NaN slice encountered` warnings from `ensemble.py:15-17` and `icon_ch1_eps.py:434/437/440`.
Start at the level-index lookup that builds `level_indices`.

---

### v0.3.4 — eccodes stack pinned (PRD crash loop + 10-week CI outage, one root cause)

Two separate-looking failures, one cause: **there was no `poetry.lock`**, so every build resolved
dependencies fresh and drifted under fixed tags.

#### Failure 1 — integration test CI red since 2026-05-08

Every run failed in `Install libeccodes 2.38 via conda-forge` with `LibMambaUnsatisfiableError`
at ~26 s, long before pytest. `conda install` targets the Miniforge **base** env where conda/mamba
live; pinning `eccodes=2.38.3` dragged in an old libnetcdf → libxml2 → icu that could not coexist
with the mamba stack. Miniforge *latest* was re-downloaded each run, so mamba advanced while the
pin stood still until the solve became unsatisfiable.

**The step never worked once** — including on the very commit that added it
(`fix(ci): install libeccodes 2.38 via conda-forge`, run 25574237954). The v0.3.1 "CI eccodes fix"
recorded as shipped in `features.md` and `RESUME.md` was never green. v0.3.3 merely inherited it.
Those docs have been corrected.

#### Failure 2 — PRD crash loop on the v0.3.3 image

Container restarted every ~15 s on the Fedora host. Died inside `_read_grid_coords()`: the log
never reached `Constants file messages (shortName): ...` (`icon_ch1_eps.py:201`), and the broad
`except Exception` → `logger.exception("CH1-EPS collection failed")` in `_run_ch1eps` never fired.
**No Python exception = not an exception** — ecCodes aborted the process; PID 1 died silently.

Three drifts combined:
- `python:3.11-slim` rolled **bookworm → trixie**: apt libeccodes 2.28 → **2.41.0**
- `eccodes` floated to **2.47.0**, which no longer ships a Linux wheel bundling the C library —
  it moved to the separate **`eccodeslib`** package
- `eccodes-cosmo-resources-python` floated to **2.44.0.1**, needing library ≥ 2.44

Poetry resolves from PyPI's JSON `info.requires_dist`, which **omits** `eccodeslib` even though
the wheel and sdist declare it (`platform_system != "Windows"`). So Poetry never installed it,
`findlibs` fell through to apt's 2.41.0, and definitions (2.44) outranked the library (2.41.0).

Diagnosis was confirmed from the log alone: `min_recommended_version_str = "2.42.0"` exists only
in eccodes-python **2.47.0**, and gribapi's `__version__` is the **C library** version — so
"ecCodes 2.42.0 or higher is recommended. You are running version 2.41.0" pins both sides exactly.
The definitions path `/usr/share/eccodes/definitions` proved `eccodeslib` was absent.

#### Fixes shipped

- **`poetry.lock` committed** (LF). Dockerfile now `COPY pyproject.toml poetry.lock ./` — the
  `poetry.lock*` glob is gone, so a missing lock fails the build instead of resolving fresh.
- **`eccodeslib` declared explicitly** in `pyproject.toml` with
  `markers = "platform_system != 'Windows'"`. Required twice over: PyPI JSON metadata omits it,
  **and** Poetry on Windows locks from the win_amd64 wheel whose metadata genuinely lacks it
  (the win wheels bundle their own DLL). Without the explicit entry, locking from this Windows
  box silently drops the C library.
- **apt `libeccodes-dev` removed** from the Dockerfile; **conda/Miniforge step removed** from CI.
  Exactly one libeccodes now exists. `findlibs` order is
  **PACKAGE → PYTHON/conda → HOME → CONFIG_PATHS → LD_LIBRARY_PATH → SYS**, so the old
  `LD_LIBRARY_PATH=$HOME/miniforge/lib` was always dead config.
- Pinned: `eccodes` 2.47.0 + `eccodeslib` 2.47.3.23 + `eckitlib` 2.1.0.23 +
  `eccodes-cosmo-resources-python` 2.44.0.1.

**Verification**: integration test green for the first time in the workflow's history —
`test_ch1_collects_interlaken PASSED, 1 passed in 252.15s` (real MeteoSwiss GRIB, run
29479520661). Image build 29504233120 confirms `Installing eccodeslib (2.47.3.23)`. Published
`ghcr.io/think4dvantage/lsmfapi:0.3.4`.

**CONFIRMED on PRD** (2026-07-16 17:10 logs): crash loop gone, GRIB parsing succeeds,
`CH1 ensemble members in GRIB: 10`, 500+ stations cached, grid caches written. This fix is
verified in production.

**Deferred**: `FROM python:3.11-slim` still floats (same class of drift, now harmless for eccodes
since apt is out of the picture — pin to a digest if it bites again). `actions/checkout@v4` /
`setup-python@v5` emit Node 20 deprecation warnings. `tests/` has no unit tests — the integration
test is the only gate.

**Note on local verification**: this dev box has no Docker and no WSL, and `eccodeslib` is
Linux-only by marker, so Linux behavior cannot be run locally — CI is the only proof. Local
`poetry install` also fails (Python 3.13 vs pinned numpy 1.26.4, which has no cp313 wheels).

---

# Resume Notes — 2026-07-15

## What Was Done This Session

### v0.3.3 — Grid Memory Reduction (peak RAM ~8–16 GB → ~2 GB)

The service was OOMing at 8 GB. Root causes traced to: (1) the `/thermal-grid` response
builder materialising hundreds of millions of Python floats at fine `stride_km`; (2) a full
`np.concatenate` merge copy of the combined grid on every request; (3) float32 resident grids
(~3.3 GB). Three fixes, all shipped and verified:

**1. Response budget guard — `api/routers/forecast.py`**
- Added `_MAX_RESPONSE_CELLS = 10_000_000` and `_budget_error()`. `/grid` and `/thermal-grid`
  reject requests where `n_pts × n_frames × n_fields` exceeds the cap with `400
  response_too_large` (logged WARNING) **before** allocating. Default `stride_km=10` full-bbox
  thermal request (~5.3 M) passes; `stride_km=1` full-bbox (~530 M) is blocked.
- Grid responses now built as plain dicts → `JSONResponse` (no intermediate `GridFrame`/
  `ThermalGridFrame`/`*Response` Pydantic models — those imports were removed from the router).
  `_to_nullable` uses one vectorised `np.round(...).tolist()`.

**2. float16 grid storage — `collectors/icon_ch1_eps.py`, `models/forecast.py`**
- `_build_thermal_grid_cache()` and `_build_grid_wind_cache()` allocate field arrays as
  `float16` and cast stats to float16 on store (computation stays float64). `lats`/`lons`
  stay float32. Resident grids ~3.3 GB → ~1.6 GB.

**3. Combined single-array grid store — `database/cache.py` (rewritten)**
- Replaced `_ch1_/_ch2_grid_wind_cache` + `_ch2_/_ch1_thermal_grid_cache` and the
  `_merge_*` functions with ONE combined 121-frame store per grid (`_grid_wind_cache`,
  `_thermal_grid_cache`) plus `_grid_wind_model_init` / `_thermal_grid_model_init` maps.
- `set_*` writes each model's horizon slice in place (CH1 rows 0–33, CH2 rows 34–120), keyed
  by `horizon = round((valid_time − init_time)/1h)`. `get_*` returns a contiguous numpy
  **view** (`arr[start:end]`, zero copy) — no merge, no per-request copy.
- Persistence collapsed to single `grid_cache.npz` / `thermal_grid_cache.npz` (all 121
  frames, `valid_times` as timestamps with NaN for unpopulated, per-model init in `_meta`).
  Legacy `*_ch1/*_ch2` npz files removed on load. Public API (get/set/save/load/detail)
  unchanged, so collectors, router, and dashboard were untouched by this part.
- `grid_cache_detail()` / `thermal_grid_cache_detail()` keep `ch1_frames`/`ch2_frames` by
  counting populated rows in the [0,34) / [34,121) ranges.

**Verification**: standalone functional test (combined store both/partial, boundary
continuity, save→wipe→load round-trip, detail counts, float16 dtype, view-not-copy, budget
helper, NaN handling) passed; all changed files byte-compile.

**Behavior change to note**: `/thermal-grid` at `stride_km ≤ 5` over the *full* domain now
returns `400 response_too_large` (must use a smaller bbox). Tune `_MAX_RESPONSE_CELLS` if the
Data Inspector needs those combinations.

### Docs / release
- `pyproject.toml` 0.3.0 → **0.3.3**.
- Updated `.ai/context/architecture.md`, `features.md`, `01-project-overview.md`, and
  `README.md`. Corrected long-standing README drift: station wind is **km/h** (not m/s), the
  station response only carries wind/temp/humidity/pressure/precip (solar/cloud/CAPE live in
  the thermal-grid, not the station response), accuracy GUI removed, wind-grid is populated.

---

# Resume Notes — 2026-05-15

## What Was Done This Session

### v0.3.2 — Thermal Forecast Grid

#### New `SURFACE_VARS` entries: LCL_ML, LFC_ML, TKE

Added three variables to `SURFACE_VARS` in `icon_ch1_eps.py` (shared with CH2 via import): `LCL_ML` (Lifted Condensation Level, m), `LFC_ML` (Level of Free Convection, m), `TKE` (Turbulent Kinetic Energy, J/kg). These are used only by the thermal grid — not exposed in the station forecast response.

#### `_build_thermal_grid_cache()` in `icon_ch1_eps.py` (imported by CH2)

New function that reads GRIB files from the run's tmpdir and builds a `ThermalGridCache`:
- De-accumulates `ASWDIR_S`, `ASWDIFD_S`, `DURSUN` (same pattern as station collector)
- Clips `CIN_ML` fill value (−999.9 → NaN)
- Computes ensemble median, min, max for 12 fields: `solar`, `sunshine`, `cloud_cover`, `cloud_low`, `cloud_mid`, `cloud_high`, `freezing_level`, `cape`, `cin`, `lcl`, `lfc`, `tke`
- Samples all fields onto a fixed ~1 km regular lat/lon grid over Switzerland using KD-tree indices (same grid as wind-grid cache)
- Shape: `[n_frames, n_lat × n_lon]` per field (3 arrays per field: median/min/max)

CH1 runs it over h0–h33; CH2 runs it over h34–h120 — same model slice split as station cache.

#### `ThermalGridCache`, `ThermalGridResponse`, `ThermalGridFrame` models

Added to `models/forecast.py`. `ThermalGridCache` is a `@dataclass` (not Pydantic) holding numpy arrays. `ThermalGridResponse` / `ThermalGridFrame` are Pydantic models for the API response.

#### Cache persistence — `.npz` files

`database/cache.py` saves/loads two `.npz` files: `/app/data/thermal_grid_cache_ch1.npz` and `_ch2.npz`. `get_thermal_grid_cache()` merges CH1+CH2 on the fly (same pattern as station cache). `set_thermal_grid_cache()` routes by `data.model`.

#### `GET /api/forecast/thermal-grid`

Added to `api/routers/forecast.py`. Same bbox/stride_km interface as wind-grid. Returns `ThermalGridResponse` — one `ThermalGridFrame` per forecast hour with 36 nullable float lists (12 fields × median/min/max).

#### Dashboard thermal grid cache card

`dashboard.py` exposes `thermal_grid_cache_detail()`. `dashboard.html` + `dashboard.js` now show a "Thermal Grid Cache" model card with warm/cold status, n_points, grid dimensions, CH1/CH2 frame counts.

#### Data Inspector thermal grid section

`data.html` + `data.js` gained a "Thermal Forecast Grid" section: stride_km selector, Load button, JSON viewer — same pattern as the wind-grid and altitude-winds sections.

### Accuracy GUI removed — router consolidated into dashboard.py

The accuracy analysis GUI (`static/index.html`, `static/index.js`) was removed entirely — it was calling Lenticularis directly from the browser (CORS-blocked). The `accuracy.py` router was deleted and its surviving routes migrated:
- `/data` (Data Inspector page) → `dashboard.py`
- `/api/stations` (Lenticularis proxy) → `dashboard.py`
- `/api/meta` (returns `lenticularis_base_url`) → **removed** (was only used by deleted JS)

`main.py` updated to remove `accuracy` router import.

## Key Files

```
src/lsmfapi/collectors/icon_ch1_eps.py   — CH1 collector; _build_thermal_grid_cache() defined here
src/lsmfapi/collectors/icon_ch2_eps.py   — CH2 collector; imports _build_thermal_grid_cache from CH1
src/lsmfapi/models/forecast.py           — ThermalGridCache / ThermalGridResponse / ThermalGridFrame
src/lsmfapi/database/cache.py            — get/set_thermal_grid_cache, _save/_load_thermal_grid_cache
src/lsmfapi/api/routers/forecast.py      — GET /api/forecast/thermal-grid endpoint
src/lsmfapi/api/routers/dashboard.py     — merged /data + /api/stations + thermal_grid_cache_detail
src/lsmfapi/api/main.py                  — accuracy router removed
static/dashboard.html / dashboard.js     — thermal grid cache card
static/data.html / data.js               — thermal grid section in Data Inspector
```

---

# Resume Notes — 2026-05-08

## What Was Done This Session

### v0.3.1 released and confirmed working on PRD

Tagged and pushed v0.3.1 (all fixes below confirmed working on lsmfapi.lg4.ch).

### README updated to reflect v0.3.0 state

Major corrections: CH2 h34–120 hourly / 4×/day; CH1 10 members dynamic; removed `cloud_base_convective` + `boundary_layer_height` (HBAS_CON/HPBL not in EPS catalog); added altitude-winds and stations endpoints to API overview; corrected architecture (GRIB cache, RH via TD_2M, updated repo layout); scheduler cron times replacing interval config; config table cleaned of non-existent scheduler keys; roadmap restructured. `pyproject.toml` bumped 0.2.0 → 0.3.0.

### CI eccodes version mismatch fixed

`ubuntu-latest` (Ubuntu 24.04) ships `libeccodes 2.34.1` via apt. `eccodes-cosmo-resources-python==2.38.3.1` definition files require ≥2.38 — the COSMO parser failed with a syntax error, all GRIB shortNames came back `<unknown>`, `_read_grid_coords` raised RuntimeError. Fix: removed apt libeccodes install; added Miniforge step to install `eccodes=2.38.3` from conda-forge; set `LD_LIBRARY_PATH=$HOME/miniforge/lib` so Python `findlibs` resolves the 2.38 C library.

### Traefik cross-routing fixed (PRD/DEV on same host)

`docker-compose.yml` was the base for the DEV overlay. Docker Compose merges label lists, so the DEV container inherited all PRD Traefik labels too. Traefik load-balanced between PRD and DEV for `lsmfapi.lg4.ch` — dashboard flipped between "no data" (fresh PRD) and "all ready" (DEV with warm cache). Fix: stripped all Traefik labels from `docker-compose.yml`. PRD compose file lives outside this repo on the XPS server.

### Scheduler warm-up / cron race fixed

Root cause: container started at 19:58Z, warm-up used `ref_dt=12:00Z` (118 min past 18Z, guard `< 2h` = true). At exactly 20:00Z the cron fired; `_latest_ref_dt()` returned `18:00Z` (7200s = not `< 7200`). New `grib_run_dir("ch1", 18:00Z)` called `_purge_stale` which deleted the `20260508T1200Z/` directory mid-download. All in-flight writes (U_10M h=2,3,4,5) failed with `FileNotFoundError`.

Fix: added `_ch1_lock` / `_ch2_lock` asyncio locks in `scheduler.py`. If a collection is already running, the incoming trigger logs a skip and returns immediately. No concurrent same-model runs → no cross-ref_dt purge race.

---

# Resume Notes — 2026-04-30

## Integration test — E2E collection smoke test

Added `tests/test_e2e_collection.py` (`@pytest.mark.integration`). Runs `IconCh1EpsCollector.collect()` with one hardcoded station (Interlaken) and HORIZONS patched to `[0, 6]`. Mocks `_fetch_stations` so CI doesn't need Lenticularis. Asserts cache is populated with non-null, physically plausible wind_speed/temperature/pressure_qff. Takes ~2–4 min.

`.github/workflows/integration-test.yml` runs on every push to `main` and every PR. Local: `pytest -m integration -v`.

`pyproject.toml` — added `[tool.poetry.group.dev.dependencies]` with pytest + pytest-asyncio, plus `[tool.pytest.ini_options]` (asyncio_mode=auto, integration marker).

---

## Bug Fix: CH1/CH2 collections both failing since last deploy

**Root cause**: `HBAS_CON` and `HPBL` were removed from `SURFACE_VARS` in the previous session, but the `surf_array("HBAS_CON")` and `surf_array("HPBL")` calls in both `collect()` methods were not removed. `surf_tasks` is built from `SURFACE_VARS`, so `surf_tasks["HBAS_CON"]` raised `KeyError: 'HBAS_CON'` and aborted every collection run.

**Fix**: Removed the two dead `surf_array()` lines from `icon_ch1_eps.py:735` and `icon_ch2_eps.py:350`. Both variables were already absent from `StationForecastHour` output — they had no consumers.

Error in logs: `Collection failed for ch1: 'HBAS_CON'` / `Collection failed for ch2: 'HBAS_CON'`

---

## What Was Done This Session

### Dashboard errors panel now includes download failures

`telemetry.py` `_recent_errors` deque was only populated from HTTP middleware. Collector failures (STAC search errors, download errors, eccodes parse errors) were tracked as counter deltas (`files_done − files_ok`) but never shown in the errors panel.

**Fix**: Added `record_download_error(model, variable, horizon_h, error_msg)` to `telemetry.py`. Uses the same dict shape as HTTP errors (`ts`, `method`, `path`, `status`, `detail`) with `method=CH1/CH2`, `path="VARNAME h+N"`, `status="DL-ERR"`. The existing `renderErrors()` JS table renders them without any frontend changes.

Both CH1 and CH2 collectors now call `_telemetry.record_download_error()` at all three failure points (STAC search exception, download exception, eccodes parse exception).

### Corrupt GRIB files now self-delete on eccodes failure

`_read_grib2_eccodes()` was catching exceptions internally and returning `(None, None)`. The caller's `except` block (which calls `dest.unlink(missing_ok=True)`) never fired — truncated GRIB files persisted in `/tmp/lsmfapi_grib/` across container restarts causing the same eccodes error every run until `ref_dt` changed.

**Fix**: Changed `return None, None` → `raise` in `_read_grib2_eccodes` exception handler. All 6 call sites were already in `try/except` or `try/finally` with `dest.unlink()` guards — safe to re-raise.

### Silent `url is None` now logs a WARNING

`_search_item_url` returns `None` when STAC returns no features. The caller `_fetch_step` was silently returning `None` with no log and no telemetry. Added `logger.warning()` to both CH1 and CH2 `_fetch_step` when `url is None`.

### HBAS_CON and HPBL removed from SURFACE_VARS

STAC catalog query confirmed: neither `hbas_con` nor `hpbl` is published in `ch.meteoschweiz.ogd-forecasting-icon-ch1` or `ch.meteoschweiz.ogd-forecasting-icon-ch2`. Every run was wasting 68 STAC calls (2 vars × 34 horizons) that always returned `features: []`.

Removed both from `SURFACE_VARS` in `icon_ch1_eps.py`. CH2 imports `SURFACE_VARS` from CH1 so the fix covers both collectors.

**Full CH1/CH2-EPS catalog** (confirmed via STAC search with `forecast:perturbed: true`):
`alb_rad`, `alhfl_s`, `ashfl_s`, `asob_s`, `aswdifd_s`, `aswdifu_s`, `aswdir_s`, `athb_s`, `cape_ml`, `cape_mu`, `ceiling`, `cin_ml`, `cin_mu`, `clc`, `clch`, `clcl`, `clcm`, `clct`, `dbz_850`, `dbz_cmax`, `dursun`, `dursun_m`, `grau_gsp`, `h_snow`, `hzerocl`, `lcl_ml`, `lfc_ml`, `p`, `pmsl`, `ps`, `qc`, `qv`, `rain_gsp`, `sdi_2`, `sli`, `snow_gsp`, `snowlmt`, `t`, `t_2m`, `t_g`, `t_snow`, `t_so`, `td_2m`, `tke`, `tmax_2m`, `tmin_2m`, `tot_pr`, `tot_prec`, `twater`, `u`, `u_10m`, `v`, `v_10m`, `vmax_10m`, `w`, `w_snow`, `z0`

---

# Resume Notes — 2026-04-29

## What Was Done This Session

### Collector horizons & schedule redesign

CH1 and CH2 now together cover the full 120-hour window hourly:

| Model | HORIZONS | Steps | Cron (UTC) | Logic |
|---|---|---|---|---|
| ICON-CH1-EPS | h0–h33 (inclusive) | hourly | 02/08/14/20Z | 2 h after each 00/06/12/18Z release |
| ICON-CH2-EPS | h34–h120 (inclusive) | hourly | 03/09/15/21Z | 3 h after each 00/06/12/18Z release |

CH2 does **not** download h0–h33 (those belong to CH1). CH1 always wins the write for its slice.

### Root cause of all-NULL station values — fixed

`N_MEMBERS` was hardcoded (11 for CH1, 21 for CH2) but MeteoSwiss currently delivers **10 members** for CH1. The shape check `r.shape == (11, n_stations)` failed at every step, so every value fell through to the all-NaN fallback.

**Fix**: `N_MEMBERS` is now labelled `# informational only`. After all fetch tasks complete, the actual count is read from the first valid 2-D result:

```python
_n_members = next(
    (t.result().shape[0] for ts in surf_tasks.values() for t in ts
     if _task_ok(t) and t.result().ndim == 2),
    1,
)
```

`_nan_surf`, `nan_prior`, `nan_pres` are all built from `_n_members`; `_get_prior()` compares against the runtime shape. Log line `"CH1 ensemble members in GRIB: %d"` confirms the detected count at every run.

### GRIB file persistence cache — `collectors/grib_cache.py`

Replaces `tempfile.TemporaryDirectory`. Files persist in `/tmp/lsmfapi_grib/{model}/{YYYYMMDDTHHMMZ}/` across container restarts.

- `grib_run_dir(model, ref_dt)` — context manager: purges stale run dirs on entry, **does not delete** on exit
- `_fetch_step()` cache-hit check: `if dest.exists() and dest.stat().st_size > 1024: skip download`
- Corrupt file guard: eccodes failure → `dest.unlink(missing_ok=True)` → re-downloaded next start
- Old runs (different `ref_dt`) are deleted automatically on next startup

### Dashboard download ok/failed counts

`collection_state.py` now tracks `files_ok` (downloaded + parsed successfully) alongside `files_done` (attempted). `failed = files_done − files_ok`.

- **During a run**: progress bar label shows `"X / Y (Z%) · ⚠ N failed"` in red when N > 0
- **After completion**: model detail card row shows `"N / T ok · ⚠ F failed"` or `"· ✓ all ok"`

Collector `fetch()` closures: `result: np.ndarray | None = None` declared before `try` so `finally` can check success and increment `progress[1]` (ok counter).

---

# Resume Notes — 2026-04-23

## What Was Done This Session

### CH1/CH2 cache merge fix

**Root cause** — `set_station_forecast()` was a plain dict overwrite (`_station_cache[key] = data`). CH2 ran last in `_warm_cache()` and overwrote CH1's entry for every station, discarding 0–30h of hourly data.

**Fix** — `database/cache.py` now uses two separate dicts:
- `_ch1_station_cache` / `_ch2_station_cache`
- `_ch1_altitude_winds_cache` / `_ch2_altitude_winds_cache`

`set_station_forecast()` routes by `data.model` (`"icon-ch1"` → CH1 dict, else → CH2 dict). `get_station_forecast()` merges on the fly: CH1 entries (h0–h30, 1h steps) + CH2 entries where `valid_time > last CH1 valid_time` (first CH2 step served is h33). Each collector refreshes only its own slice — a CH1 re-run doesn't touch the CH2 tail, and vice versa.

`save_cache()` / `load_cache()` use new JSON keys `ch1_station`, `ch2_station`, `ch1_altitude_winds`, `ch2_altitude_winds`. Old `cache.json` files (with key `station`) will start fresh on next container boot.

`station_cache_detail()` now returns `{count, ch1: {...}, ch2: {...}, combined_forecast_hours, init_time, valid_until}` instead of a flat dict. `altitude_winds_cache_detail()` returns `{count, ch1_count, ch2_count}`.

Dashboard JS (`static/dashboard.js`) updated to display CH1 and CH2 cache state separately.

---

# Resume Notes — 2026-04-18

## Status

Both collectors fully operational against live MeteoSwiss data. API serving real forecast data.
Traefik routing working. Cache persistence implemented. Altitude winds moved to separate endpoint.
Web GUI stations loading fixed (CORS proxy added).

## What Was Done This Session

### Pipeline fixes (all landed, confirmed working)

**Asset key bug** — `_search_item_url` was looking for `.get("data")` on the assets dict; MeteoSwiss
uses the filename as the asset key. Fixed to `next(iter(assets.values())).get("href")`.

**QV → TD_2M swap** — QV (specific humidity, 3D field, 80 vertical levels, ~80-100MB per file)
replaced with TD_2M (2m dew point temperature, small surface field, ~5MB). RH now computed via
Magnus formula in `_compute_rh_from_td(t_k, td_k)`. Saves ~30 min per collection run.

**3D shape mismatch** — surf_array() now handles ndim==3 results by taking `r[:, -1, :]`
(bottom model level). Was causing ValueError when any 3D variable slipped into surface processing.

**Progress logging** — counter `n/total` logged every 20 tasks and at completion.

**Diagnostic summary** — after gather: `CH1 surface fetch: X/Y tasks returned data`.

**httpx/httpcore log spam** suppressed in `main.py`.

### Architecture change: pressure_levels separated

`pressure_levels: list[PressureLevelWinds]` removed from `ForecastPoint` / station response.
Altitude winds now stored and served separately:

- New models: `AltitudeWindsPoint`, `AltitudeWindsResponse` (in `models/forecast.py`)
- New cache functions: `get_station_altitude_winds`, `set_station_altitude_winds` (in `database/cache.py`)
- New endpoint: `GET /api/forecast/altitude-winds?station_id=&hours=`
- Both CH1 and CH2 collectors build and cache altitude winds alongside the surface forecast

### Cache persistence

`database/cache.py` now persists the in-memory cache to `/app/data/cache.json`:
- `load_cache()` — called at startup before scheduler; loads stale data so API is immediately usable
- `save_cache()` — atomic write (`.tmp` → rename); called after each successful collection + on graceful shutdown
- Volume mount `./data:/app/data` added to both `docker-compose.yml` and `docker-compose.dev.yml`
- `./data` excluded from rsync in `LSMF-dev.ps1` (intentional — never overwrite remote cache)

### Traefik fixes

- `docker-compose.dev.yml` had `tls=true` but no `certresolver` → self-signed cert error. Added:
  `traefik.http.routers.lsmfapi-dev.tls.certresolver=letsencrypt`
- Added explicit port label: `traefik.http.services.lsmfapi-dev.loadbalancer.server.port=8000`
- Startup was blocking (lifespan awaited full collection before yielding) → Traefik saw container
  as "starting" / unhealthy. Fixed: initial collection now runs as `asyncio.create_task(_warm_cache())`
  so FastAPI starts serving immediately; health checks pass; Traefik routes traffic.

### Web GUI: stations CORS fix

`index.js` was fetching Lenticularis directly from the browser → CORS blocked.
Added `/api/stations` proxy endpoint in `accuracy.py` that calls Lenticularis server-side.
Also fixed field names: `s.station_id`, `s.latitude`, `s.longitude` (was `s.id`, `s.lat`, `s.lon`).

## Known Issues / Deferred (as of this 2026-04-18 session — all resolved by later sessions,
kept here only as history; see the top of this file for current state)

- ~~`sunshine_minutes` wrong for CH2 first step~~ — resolved by `ACCUM_PRIOR_H`/the shadow h33
  fetch (see v0.3+); the stale restatement of this in `features.md`'s Known Issues was itself
  corrected in the P1-11 entry above.
- ~~`cin: -999.9`~~ — fixed; `CIN_ML` is clipped to NaN→`null` (see `_CIN_FILL_THRESHOLD`).
- ~~U/V/W pressure-level data all null~~ — this was the root cause fixed in v0.3.6 (HHL height
  interpolation replaced the broken pressure mapping); see [[altitude-winds-hhl-fix]].
- ~~`fetchActuals`/`fetchForecasts` in index.js~~ — moot; the whole accuracy GUI (`index.html`/
  `index.js`/`accuracy.py`) was removed in v0.3.2, see that entry above.
- ~~Wind-grid endpoint is a stub~~ — implemented; `set_grid_wind_cache` is called from both
  collectors' `collect_grid()`.

## Key Files

```
src/lsmfapi/collectors/icon_ch1_eps.py   — CH1 collector (h0–h33 hourly); all shared helpers live here
src/lsmfapi/collectors/icon_ch2_eps.py   — CH2 collector (h34–h120 hourly); imports helpers from CH1
src/lsmfapi/collectors/grib_cache.py     — grib_run_dir() context manager; persistent GRIB dirs in /tmp
src/lsmfapi/models/forecast.py           — all Pydantic models incl. AltitudeWindsResponse
src/lsmfapi/database/cache.py            — in-memory cache + save/load persistence
src/lsmfapi/database/collection_state.py — runtime collection state (status, files_done, files_ok, errors)
src/lsmfapi/scheduler.py                 — APScheduler + _warm_cache background task
src/lsmfapi/api/routers/forecast.py      — /api/forecast/station + /api/forecast/altitude-winds
src/lsmfapi/api/routers/accuracy.py      — accuracy GUI + /api/stations proxy
src/lsmfapi/api/main.py                  — lifespan: load_cache → scheduler → save_cache on shutdown
static/dashboard.js                      — dashboard frontend; renderCollection() shows ok/failed counts
static/index.js                          — accuracy GUI frontend
docker-compose.yml                       — base compose (data volume mount)
docker-compose.dev.yml                   — dev overlay (Traefik labels, live src mount, data volume)
scripts/LSMF-dev.ps1                     — SSH deploy script (deploy/sync/restart/logs/exec)
scripts/diag_interlaken.py               — dry-run diagnostic: prints raw ensemble values for one station
config.yml                               — meteoswiss URLs, lenticularis base_url, scheduler intervals
```

## Context Files to Read

- `.ai/context/architecture.md` — STAC API, variables, eccodes, altitude mapping, GRIB cache, API contracts
- `.ai/context/features.md` — shipped vs backlog
