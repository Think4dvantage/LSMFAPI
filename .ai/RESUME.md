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

## Known Issues / Deferred

- **`sunshine_minutes` wrong for CH2 first step** — h=30 is the first CH2 horizon; the accumulated
  value from model start (30h of sunshine) is used as the delta, not 3h. Needs special-casing for
  the first step: treat the accumulated value as the per-step value, or fetch h=27 to diff against.
- **`cin: -999.9`** — ICON fill value for "no convection present". Should map to `null`.
  Fix: clip `CIN_ML` to `None` where value < -900.
- **U/V/W pressure-level data all null** — probe downloads succeed but values are null in response.
  Not yet investigated. May be eccodes level-type mismatch or STAC search returning no results.
- **`fetchActuals` / `fetchForecasts` in index.js** still call Lenticularis directly from browser
  → will CORS-fail when analysis is run. Need same proxy treatment as stations.
- **Wind-grid endpoint** (`GET /api/forecast/wind-grid`) is a stub — `set_grid_forecast` is never
  called. Not yet implemented.

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
