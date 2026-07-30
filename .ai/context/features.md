# Feature History & Backlog

## Current Version: v0.3.7

### Shipped Milestones

| Milestone | What shipped |
|---|---|
| v0.1 | Core collectors, forecast API, altitude winds endpoint, cache persistence, accuracy GUI stations proxy |
| v0.2 | Dashboard, Data Inspector, GitHub Actions Docker pipeline, CH1/CH2 cache merge |
| v0.3 | Hourly CH2 tail (h34–120), 4×/day schedule, NULL fix (dynamic N_MEMBERS), GRIB persistence cache, dashboard ok/failed counts, error recording, HBAS_CON/HPBL removed |
| v0.3.1 | Traefik label isolation (PRD/DEV), scheduler lock (warm-up/cron race). Also shipped a CI eccodes "fix" that never worked — see v0.3.4 |
| v0.3.2 | Thermal grid endpoint: `GET /api/forecast/thermal-grid` — LCL_ML, LFC_ML, TKE added to SURFACE_VARS; `_build_thermal_grid_cache()` in CH1 + CH2; `ThermalGridCache` with npz persistence; accuracy GUI removed, `accuracy.py` merged into `dashboard.py` |
| v0.3.3 | Grid memory reduction: float16 grid storage, combined single-array grid store (no merge-on-read copy), `/grid` + `/thermal-grid` response budget cap. Peak RAM ~8–16 GB → ~2 GB |
| v0.3.4 | eccodes stack pinned: `poetry.lock` committed, explicit `eccodeslib`, apt libeccodes and the conda CI step removed. Fixes the v0.3.3 PRD crash loop and turns CI green for the first time |
| v0.3.5 | Grid build moved off the event loop (`asyncio.to_thread`) — the API stayed unreachable for the whole build (~8 min CH1, ~20 min CH2), ~1.5–2 h/day |
| v0.3.6 | Altitude winds + wind-grid-at-altitude fixed: interpolate U/V/W to true MAMSL heights from HHL (EPS files are model levels with no pv, so pressure matching collapsed all bands onto one level). W files now deleted right after extraction (they filled ~200 GB); GRIB pool moved to a bigger disk via the PRD pipeline |
| v0.3.7 | CH2 cron misfire fixed: `misfire_grace_time=1800` on both scheduler jobs — APScheduler's ~1s default was silently skipping CH2 triggers late by only a few seconds, leaving the cache stuck on a stale run for a full 6h cycle while CH1 kept updating normally |
| v0.3.8 | Tech-debt remediation P0-1: stored XSS in the operator dashboard fixed — `escapeHtml()` applied to every server-data `innerHTML` interpolation in `dashboard.js`/`data.js`; `station_id` constrained to `[A-Za-z0-9_.-]{1,64}`; 404 no longer echoes raw input; telemetry sanitizes path/detail before storing |
| v0.3.9 | Tech-debt remediation P0-2: `verify=False` removed from all 4 HTTPS clients (Lenticularis proxy, both collectors' `_fetch_stations`, `diag_interlaken.py`) — confirmed with the user that both hosts present valid public certs, so TLS verification was never actually needed |
| v0.3.10 | Tech-debt remediation P0-3: `save_cache()` moved off the event loop (`asyncio.to_thread` at both scheduler call sites; shutdown stays sync on purpose); per-grid dirty flag so an unrelated run's save no longer rewrites an unchanged grid; switched `np.savez_compressed` → `np.savez` on both grid npz writes (measured ~20–40× faster for ~16% more disk on synthetic same-shape data — see RESUME) |
| v0.3.11 | Tech-debt remediation P1-1: `/health` is a real check now — `service`/`version`/`uptime_seconds` + `checks: {sqlite, scheduler, cache}`, returns 503 when SQLite is unreachable or the scheduler is stopped (never on a merely stale cache). App version now comes from `importlib.metadata` instead of a hardcoded `"0.1.0"` (also resolves P2-1). Docker healthcheck's `urlopen` now self-limits with `timeout=3`. Bundled the trivial P2-9 fix (`datetime.utcnow()` → `datetime.now(timezone.utc)` in `cache.py`) since `/health`'s cache-age calculation would otherwise crash on the naive/aware datetime mismatch |
| v0.3.12 | Tech-debt remediation P1-2: telemetry error ring now aggregates by `(method, path, status, detail)` with `first_seen`/`last_seen`/`count` instead of raw append, so recurring 404 noise no longer saturates the last-20 buffer. Split `error_count` (5xx + collector/download failures) from `client_error_count` (4xx). 5xx/download errors log at ERROR, 4xx at WARNING (was DEBUG under an INFO root logger — invisible either way). Counters guarded with `threading.Lock` since collectors call from worker threads. Dropped `record_request()`'s unused params |
| v0.3.13 | Tech-debt remediation P1-9: added `@app.exception_handler(Exception)` — unhandled exceptions previously propagated straight through `TelemetryMiddleware`'s `call_next()` (no registered handler existed for bare `Exception`), so genuine 500s were never logged, never counted, and never reached the dashboard. Now logs ERROR with `exc_info=True` and returns the documented `{"error": {code, message}}` envelope with no exception text in the body; `TelemetryMiddleware` picks the resulting response up automatically like any other ≥400 response |
| v0.3.14 | Tech-debt remediation P0-4.1: `grib_cache_dir` is now a config key (default `/tmp/lsmfapi_grib`, same as before) instead of hardcoded, logged at startup along with free space on that volume (WARNING below 550 GB — a CH2 run peaks at ~480 GB). `docker-compose.yml` now bind-mounts `./grib-cache` onto it so DEV stops filling the container's writable layer. Corrected the v0.3.6 docs: W-file deletion bounds *retention*, not *peak* — peak during download is unchanged at ~200/480 GB (real fix is P0-4.2, still open) |
| v0.3.15 | Tech-debt remediation P1-6: `config.yml` untracked (`git rm --cached`, kept on disk) and added to `.gitignore` — it was tracked despite `04-constraints.md` and `README.md` both claiming otherwise. No secret ever leaked (verified via `git log -p`). Also removed the dead `scheduler:` block from the local `config.yml` (real schedule is hardcoded cron) and fixed the README wording |
| v0.3.16 | Tech-debt remediation P1-7: `config.py` now has `extra="forbid"` on all three Pydantic models (a typo or the dead `scheduler:` block was previously silently discarded), an explicit existence check that logs CRITICAL with the resolved absolute path and fails fast instead of a bare `FileNotFoundError` from inside a collector, and logs every resolved non-secret key at INFO on first load. `main.py`'s lifespan now calls `get_config()` explicitly and early, before `init_db()`/`load_cache()` |
| v0.3.17 | Tech-debt remediation P3-1: a tagged release could ship on a red test — `docker-publish.yml` triggers on `push.tags`, which `integration-test.yml`'s `push.branches` filter never matches, so the image (including mutable `:latest`) published regardless of test state. Added a `test` job inside `docker-publish.yml` that `build-and-push` now `needs:`, so a tag build only proceeds after a green integration test run for that exact commit |
| v0.3.18 | Tech-debt remediation P2-7: `compute_wind_direction_stats` min/max fixed — was plain `min()`/`max()` on raw degrees (not NaN-safe, and meaningless for a circular quantity: 359°/1° reported a fake 358° spread instead of the true 2°). Now offsets each member from the circular median, wrapped to [-180°,180°), and re-adds the extremes — NaN-safe and circularly correct. **API-visible values change** (confirmed with the user first, since Lenticularis consumes these fields) |
| v0.3.19 | Tech-debt remediation P3-2: added a fast `unit` CI job (`pytest -m "not integration"`, seconds not minutes, no network) alongside the existing `e2e` job in `integration-test.yml`; added `ruff` to the dev group + `poetry check --lock` + `ruff check .` to that job. New unit tests: `services/ensemble.py` (incl. the P2-7 circular fix), `cache.py`'s combined-grid save→wipe→load round trip + dirty-flag behaviour, `_interp_to_heights`/`_deaccumulate`/`_compute_rh_from_td`/`_latest_ref_dt` boundary in `icon_ch1_eps.py`, and `_budget_error`. **Known**: `ruff check` currently reports ~84 pre-existing findings in `icon_ch1_eps.py`/`icon_ch2_eps.py`/`db.py` (mostly the exact dead variables P2-6 already lists) — the `unit` job will show red until P2-6 lands; this does **not** gate releases, `docker-publish.yml`'s own P3-1 gate only depends on the `e2e` integration test |
| v0.3.20 | Tech-debt remediation P3-3: added `.github/dependabot.yml` (pip + github-actions, weekly, each grouped into one PR). The eccodes triplet (`eccodes`/`eccodeslib`/`eccodes-cosmo-resources-python`) is explicitly excluded from automatic bumps — deliberate and load-bearing per `04-constraints.md`, must move together by hand behind a green integration test |
| v0.3.21 | Tech-debt remediation P3-4: `actions/checkout@v4→v5`, `setup-python@v5→v6` (off deprecated Node 20) in both workflows; added Poetry dependency caching (`pipx install poetry` before `setup-python` so its `cache: poetry` can find it); added a `concurrency` group to both workflows (was: two pushes to `main` could run two 172 MB integration tests in parallel against the live MeteoSwiss API); dropped `linux/arm64` from `docker-publish.yml` (confirmed with the user — no evidence it's deployed anywhere, and emulated arm64 builds of numpy/scipy/eccodes under QEMU are slow), removed the now-unneeded `setup-qemu-action` step |
| v0.3.22 | Tech-debt remediation P2-3: startup now globs `*.tmp.npz`/`*.tmp` in the data dir and unlinks any it finds (logged at INFO) — a temp file present at boot is by definition an interrupted write, and the old `_remove_legacy_grid_files()` only knew about specific legacy filenames, not the `.tmp` pattern, so PRD's orphaned 161 MB `grid_cache_ch1.tmp.npz` (dated Jul 16) could never self-heal |
| v0.3.23 | Tech-debt remediation P2-6: removed verified-dead code — 5 unused Pydantic response models (`GridPoint`/`GridFrame`/`GridForecastResponse`/`ThermalGridFrame`/`ThermalGridResponse`, ~76 lines), `known_stations` unused import, `_parse_horizon_h`+`import re`, `_THERMAL_ACCUM_VARS`/`_THERMAL_SURFACE_VARS`, the never-passed `keep` param (CH1+CH2), `_GRID_LATS`/`_GRID_LONS`, CH2's unused `_deaccumulate`/`_horizon_str` imports, ~13 unused locals per collector (`clct`/`clcl`/`clcm`/`clch`/`hzerocl`/`cape_ml`/`cin_ml`/`dursun_min`/`solar_direct`/`solar_diffuse`/`lat`/`lon`/`elev`), dashboard.py's dead `cached_init`/`cached_model`, and `grib_cache.py`'s unused `AbstractContextManager` import. Split all 46 semicolon-joined statements and fixed 1 ambiguous variable name flagged by ruff. Fixed 2 stale docstrings (`GridWindCache` "not persisted" claim, `/grid` and `/thermal-grid` router docstrings still naming the deleted response models). `ruff check` findings: 84 → 4 (remaining 4 are all in `scripts/diag_interlaken.py`, deleted next by P3-6) |
| v0.3.24 | Tech-debt remediation P2-8: all-NaN reduction warnings fixed. `services/ensemble.py`'s `compute_stats`/`compute_wind_direction_stats` now guard `np.all(np.isnan(...))` and skip the reducers entirely (verified locally: a new test using `warnings.simplefilter("error")` proves no warning fires and NaN still flows through — unchanged behavior at the Pydantic boundary, which already turns NaN into `null`). `icon_ch1_eps.py`'s grid-level reductions (thermal grid `_median`/`_nanmin`/`_nanmax`, wind-grid `ws_cache`/`wd_cache`/`rh_cache`) now wrap in `np.errstate(invalid="ignore")`, matching `_interp_to_heights`'s existing pattern — bands below terrain are legitimately all-NaN post-v0.3.6, so this was pure noise (CI reported "10 warnings") |
| v0.3.25 | Tech-debt remediation P2-11: normalized all error responses onto the documented `{"error": {code, message, details}}` envelope. New `@app.exception_handler(StarletteHTTPException)` unwraps routes that already raise `HTTPException(detail={"error": {...}})` instead of double-nesting into FastAPI's default `{"detail": {"error": {...}}}`; new `RequestValidationError` handler puts FastAPI's own 422s in the same envelope. Fixed `dashboard.py`'s `/api/stations` 502, which leaked the raw upstream exception (incl. the Lenticularis URL) via `detail=str(exc)` — now logs server-side and returns a generic `upstream_unavailable` message. Verified with an isolated `TestClient` smoke test (fastapi/starlette run locally without eccodes) covering all 3 previously-different shapes |
| v0.3.26 | Tech-debt remediation P2-12: rewrote `docs/forecast-data-reference.md` from scratch against the actual current API — corrected CH1/CH2 horizons+cadence+member counts, wind units (km/h not m/s), humidity source (TD_2M Magnus not QV/Bolton), altitude winds (HHL geometric-height interpolation, deleted the obsolete altitude→hPa table entirely), and reframed the thermal/convective fields (solar, cloud, CAPE/CIN, LCL/LFC/TKE) as belonging to `/thermal-grid` (a spatial grid endpoint) rather than the per-station response — they were never part of it. Noted `HPBL`/`HBAS_CON` are not published upstream and not in this API. Also annotated the oldest (2026-04-18) `.ai/RESUME.md` session entry's "Known Issues" list as historical/all-resolved, since it read as a current-state list otherwise |
| v0.3.27 | Tech-debt remediation P3-6: deleted `scripts/diag_interlaken.py` — it imported `_extract_station` from `icon_ch1_eps.py`, a symbol that has never existed in `src/` (the only repo-wide hit was the import line itself), so the script raised `ImportError` before running a single line. `ruff check .`: 4 → **0** findings |
| v0.3.28 | Tech-debt remediation P3-10 (+ P3-7): untracked `.claude/settings.local.json` (`git rm --cached`, kept on disk, added `.claude/` to `.gitignore`); trimmed the 212-line unmodified GitHub Python `.gitignore` template down to ~35 lines of what this project actually needs (was covering Django/Scrapy/Celery/SageMath/Marimo — none used — while the real gaps, `config.yml`/`.claude/`, went unnoticed under the noise); deleted `scripts/remote.ps1` (confirmed with the user — ~85% duplicate of `LSMF-dev.ps1` that deployed the base compose with no dev overlay and printed the PRD hostname, exactly what the README warns against); de-duplicated the healthcheck (was declared in both `Dockerfile` and `docker-compose.yml` — removed the compose copy, the image's `HEALTHCHECK` already applies) and bumped `start-period` 10s→60s (`load_cache()` JSON-parses ~170 MB and loads two npz files before uvicorn serves — 10s risked a false-unhealthy on a cold start) |
| v0.3.29 | Tech-debt remediation P2-2: `FROM python:3.11-slim` pinned to the current digest (fetched live from the registry, not invented) — this exact tag rolling bookworm→trixie caused the v0.3.4 PRD crash loop; two builds of one git SHA are now the same image. Poetry pinned to `2.2.1` in the Dockerfile and both CI workflow files (previously unversioned in all three — the same drift class that caused v0.3.4). Added `.dockerignore` (`.git/`, `.ai/`, `data/`, `tests/`, etc. — build context previously shipped all of it on every build) |
| v0.3.30 | Tech-debt remediation P2-4: removed unused dependencies `cfgrib`, `xarray`, `aiofiles` — verified zero imports anywhere in `src/` (collectors use the raw `eccodes` API directly). `xarray` was additionally pinned `^2024.0`, a caret on a CalVer package that froze it in calendar-2024 forever. `poetry.lock` regenerated the same verified way as P3-2 (isolated scratch copy, diffed before applying) — confirms only `cfgrib`/`xarray`/`aiofiles` and their transitive-only deps (`pandas`, `python-dateutil`, `pytz`, `six`) dropped, eccodes triplet versions byte-identical |

---

## v0.1 — MVP: Core Service ✓ SHIPPED

- ICON-CH1-EPS (h0–h33) and ICON-CH2-EPS (h34–h120) collectors via MeteoSwiss STAC API
- Ensemble member count read dynamically from GRIB (not hardcoded); CH1 delivers ~10 members
- Surface variables: winds, temperature, RH (from TD_2M), pressure, precipitation, radiation, cloud cover, freezing level, CAPE, CIN
- Altitude winds (separate endpoint): 9 bands 500–5000 m ASL
- `GET /api/forecast/station`, `GET /api/forecast/altitude-winds`, `GET /api/stations` proxy
- Cache persistence: `save_cache()` / `load_cache()` to `/app/data/cache.json`
- Traefik labels + certresolver=letsencrypt

---

## v0.2 — Dashboard + Infrastructure ✓ SHIPPED

- Operational dashboard with live collection status, cache health, error log
- Data Inspector GUI
- GitHub Actions Docker pipeline (build + push to GHCR)
- `scripts/LSMF-dev.ps1` remote deploy script
- CH1/CH2 cache merge: CH1 hourly head (h0–h33) + CH2 hourly tail (h34–h120) served as one blended response

---

## v0.3 — Reliability Hardening ✓ SHIPPED

- CH2 upgraded from 3h steps to 1h steps (h34–h120 hourly)
- Both models now run 4×/day; CH1 at 02/08/14/20Z, CH2 at 03/09/15/21Z
- NULL fix: N_MEMBERS read dynamically (was hardcoded to 11; CH1 delivers 10 → shape check failed → all-NaN)
- GRIB persistence cache: `/tmp/lsmfapi_grib/{model}/{ref_dt}/` — skip re-downloads on container restart
- Dashboard errors panel: download failures (STAC, HTTP, eccodes) now visible alongside HTTP errors
- Corrupt GRIB self-delete: eccodes failure deletes file so it re-downloads next run
- Silent STAC miss now warns: `_fetch_step` logs WARNING when STAC returns no features
- Removed HBAS_CON + HPBL from SURFACE_VARS (not published in EPS catalog)
- Integration test: `tests/test_e2e_collection.py` (`pytest -m integration`)

---

## v0.3.1 — Production Fixes ✓ SHIPPED

- **CI eccodes**: ⚠️ **this fix never worked.** `ubuntu-latest` ships libeccodes 2.34.1 and the COSMO definitions required 2.38+, so a Miniforge + conda-forge step was added. It failed on its very first run and every run after, so the integration test did not execute at all between 2026-05-08 and 2026-07-16. Properly fixed in v0.3.4.
- **Traefik cross-routing**: base `docker-compose.yml` had PRD Traefik labels that bled into DEV container via overlay merge, causing Traefik to load-balance between PRD and DEV. Fixed by removing all labels from the base file (PRD labels live in server-side compose only).
- **Scheduler warm-up/cron race**: container starts at 19:58Z with ref_dt=12Z; cron fires at exactly 20:00Z with ref_dt=18Z; `_purge_stale` deletes the active 12Z GRIB directory mid-download. Fixed with per-model `asyncio.Lock()` in `scheduler.py` — concurrent same-model runs skip instead of overlapping.

---

## v0.3.2 — Thermal Forecast Grid ✓ SHIPPED

- Added `LCL_ML`, `LFC_ML`, `TKE` to `SURFACE_VARS` (shared by CH1 + CH2)
- New `_build_thermal_grid_cache()` function in `icon_ch1_eps.py` (imported by CH2):
  - De-accumulates `ASWDIR_S`, `ASWDIFD_S`, `DURSUN`; clips CIN fill value (−999.9 → NaN)
  - Samples 12 fields on the same ~1 km regular grid as wind-grid using KD-tree indices
  - Returns `ThermalGridCache` dataclass (numpy arrays: shape `[n_frames, n_grid_pts]` per field)
- `ThermalGridCache` + `ThermalGridResponse` + `ThermalGridFrame` models in `models/forecast.py`
- Cache persistence: saved/loaded as `.npz` files alongside wind-grid cache
- `GET /api/forecast/thermal-grid?bbox=&stride_km=` endpoint (same bbox/stride_km interface as wind-grid)
  - Fields: `solar` (W/m²), `sunshine` (min/h), `cloud_cover/cloud_low/cloud_mid/cloud_high` (%), `freezing_level` (m), `cape`/`cin`/`lcl`/`lfc`/`tke` (J/kg or m)
- Accuracy GUI (`static/index.html`, `static/index.js`) removed — Data Inspector (`/data`) is the only GUI alongside Dashboard
- `accuracy.py` router merged into `dashboard.py`: `/api/stations` proxy + `/data` page moved; `/api/meta` endpoint removed
- Dashboard thermal grid cache card added (warm/cold, n_points, CH1/CH2 frames)
- Data Inspector updated with Thermal Forecast Grid section

---

## v0.3.3 — Grid Memory Reduction ✓ SHIPPED

Cut the service's peak RAM from ~8–16 GB (was OOMing at 8 GB) to ~2 GB without sacrificing
performance. Three independent changes:

- **float16 grid storage** — all wind/thermal grid field arrays are now `float16` (stats
  computed in float64, cast only on store). Resident grid memory ~3.3 GB → ~1.6 GB; `lats`/
  `lons` kept float32. Every field's range fits inside float16's ±65504.
- **Combined single-array grid store** (`database/cache.py`) — replaced the separate
  `_ch1_/_ch2_` grid caches + per-request `np.concatenate` merge with one combined 121-frame
  store. Each collector writes its horizon slice in place (CH1 rows 0–33, CH2 rows 34–120);
  `get_*` returns a contiguous numpy **view** (zero copy). Removes the ~1 GB-per-request merge
  allocation and recompute. Persistence collapsed to single `grid_cache.npz` /
  `thermal_grid_cache.npz`; legacy per-model files auto-removed on load.
- **Response budget cap** — `/grid` and `/thermal-grid` reject requests exceeding
  `_MAX_RESPONSE_CELLS = 10_000_000` (points × frames × fields) with `400 response_too_large`
  before allocating, and build responses as plain dicts (no intermediate Pydantic frame
  models). Blocks the fine-`stride_km` full-bbox request that could allocate 10+ GB of Python
  floats. Default `stride_km=10` full-bbox request is unaffected.

---

## v0.3.4 — eccodes Stack Pinned ✓ SHIPPED

Fixes two failures with one root cause: **unpinned dependencies drifting under fixed tags**.
There was no `poetry.lock`, so every build resolved fresh.

**PRD crash loop (v0.3.3 image)** — container restarted every ~15 s, dying inside
`_read_grid_coords()` with no traceback and no `logger.exception` output. Three drifts combined:

- `python:3.11-slim` rolled bookworm → trixie: apt libeccodes 2.28 → **2.41.0**
- `eccodes` floated to **2.47.0**, which no longer ships a Linux wheel bundling the C library —
  that moved to the separate `eccodeslib` package
- `eccodes-cosmo-resources-python` floated to **2.44.0.1**, needing library ≥ 2.44

Poetry resolves from PyPI's JSON metadata, which **omits** the `eccodeslib` dependency the wheel
actually declares — so it was never installed, `findlibs` fell through to apt's 2.41.0, and
definitions (2.44) outranked the library (2.41.0). ecCodes aborted the process on the first GRIB
parse. An abort is not a Python exception, so the broad `except Exception` in `_run_ch1eps` never
logged and PID 1 died silently.

**CI red since 2026-05-08** — the v0.3.1 conda step targeted the Miniforge base env where
conda/mamba live; the old eccodes pin dragged in libnetcdf → libxml2 → icu versions that could
not coexist with mamba. `LibMambaUnsatisfiableError` at ~26 s, before pytest ever started. It
never passed once. Had it been green it would have caught the mismatch before the image shipped.

**Fixes**

- `poetry.lock` committed; `COPY pyproject.toml poetry.lock` in the Dockerfile (no `*` glob —
  a missing lock now fails the build instead of silently resolving fresh)
- `eccodeslib` declared explicitly with `markers = "platform_system != 'Windows'"` — required
  because PyPI JSON metadata omits it, and because Poetry on Windows locks from the win_amd64
  wheel whose metadata genuinely lacks it
- apt `libeccodes-dev` removed from the Dockerfile and the conda step removed from CI — exactly
  one libeccodes now exists (the `eccodeslib` wheel), so the image matches the green CI env
- Pinned stack: `eccodes` 2.47.0 + `eccodeslib` 2.47.3.23 + `eccodes-cosmo-resources-python`
  2.44.0.1, verified against real ICON GRIB by the integration test

---

## v0.3.5 — Grid Build Off The Event Loop ✓ SHIPPED

`collect_grid()` was called synchronously from inside `async def collect()`, so it held the event
loop for the entire grid build and uvicorn served nothing — `/health` included. The healthcheck
(`timeout=5s`, `retries=2`) failed ~30 s in, Traefik dropped the container, and the web UI
vanished while collection itself was perfectly healthy.

Measured on PRD: a **7 m 48 s** gap between the last `Cached forecast for ...` line and
`Wind-grid combined store: wrote 34 frames`. CH2 spans 87 horizons to CH1's 34, so its window is
~20 min. Across 8 runs/day that is **~1.5–2 h of daily unavailability**, plus warm-up on every
restart.

Latent since the grid feature landed — only visible once v0.3.4 let a collection run to
completion instead of crash-looping.

**Fix**: `await asyncio.to_thread(self.collect_grid, ...)` in both collectors. eccodes (via cffi)
and numpy's array ops release the GIL, so the loop keeps serving through the heavy parts.

**Trade-off accepted**: this introduces real concurrency the blocking loop previously prevented.
The slow part (`_build_grid_wind_cache`) builds a **local** object touching no shared state; only
`set_grid_wind_cache()` writes the shared store, and that is a sub-second slice copy. So a `/grid`
reader may observe a torn frame for a fraction of a second 8×/day — strictly better than an
8-minute outage. No lock: holding one across the build would just recreate the outage for readers.

**Rule going forward**: never call a GRIB/numpy-heavy function directly from `async def`. See
`04-constraints.md`.

---

## v0.3.6 — Altitude Winds via MAMSL Heights ✓ SHIPPED

Root cause of the long-standing all-null altitude winds (and the wind grid being wrong at
altitude): the EPS `U`/`V`/`W` files are ~80 `generalVerticalLayer` model levels with **no
`pv`**, so the old pressure-mapping (`ALTITUDE_TO_HPA` → nearest level index) fell through to
raw level numbers `[1..80]` and every altitude band collapsed onto index 79.

- **HHL height interpolation** — `_ensure_grid` now also downloads the static
  `vertical_constants_icon-ch{1,2}-eps.grib2` asset, extracts `HHL` (81 half-levels), and
  caches full-level geometric heights per grid point (`_GRID_LEVEL_HEIGHTS`, float16). New
  `_interp_to_heights()` linearly interpolates each member's U/V/W onto the nine fixed MAMSL
  bands per point; bands below terrain resolve to null. Removed `ALTITUDE_TO_HPA`,
  `_build_level_indices`, `_approx_hybrid_to_pressure_hpa`, and the `pv` handling.
- **Same API contract** — still nine bands (500–5000 m); the wind grid still omits 800 m. Only
  the values change (now correct and distinct per height).
- **W retained, retention bounded (not peak)** — vertical wind (`vertical_wind`) is real data
  again; W files (~1.8 GB each) are deleted after their in-memory extraction so they don't
  linger past the run that produced them. **Correction (found 2026-07-30, see P0-4 in
  `specs/001-tech-debt-remediation/plan.md`): this does not lower the peak.** The delete runs
  after the whole download phase completes, so U/V/W all still coexist on disk for every
  horizon during a run — peak is unchanged at ~200 GB (CH1) / ~480 GB (CH2).
- **CI guard** — the integration test now asserts altitude winds resolve to distinct,
  ordered values, so the collapse bug cannot silently regress.

Storage: the GRIB pool (~200 GB/run, dominated by the 3D U/V/W files) was relocated off `/`
to a larger disk via the separate PRD pipeline repo (compose bind mount), not in this repo.

---

## v0.3.7 — CH2 Scheduler Misfire Fixed ✓ SHIPPED

**Symptom (reported on PRD, lsmfapi.sdh.lol)**: dashboard showed CH2's station/grid/thermal
caches stuck on the `00:00Z` run (`is_current: false`) hours after the `06:00Z` and later runs
had been published, while CH1 kept advancing normally through `06Z → 12Z` on schedule.

**Root cause**: `docker logs` showed, exactly twice in five days, a CH2-only line —
`Run time of job "_run_ch2eps ..." was missed by 0:00:01.767140` (and another missed by
`0:00:06.978634`) — with **no corresponding "executed successfully" line** for that trigger.
APScheduler's `add_job()` was called with no `misfire_grace_time`, so it used the library
default (on the order of 1s). Any executor jitter past that — GIL contention from the other
model's `asyncio.to_thread` GRIB/numpy work, a slow event-loop tick, anything — makes
APScheduler classify the trigger as **misfired and skip it outright**, not run it late. CH1
never hit this in five days of logs; CH2's ~2h collect() apparently perturbs the loop enough
to occasionally lose the race by single-digit seconds.

Once a trigger is skipped this way the model silently stays on the previous ref_dt until the
*next* 6-hourly slot — up to 6h of visible "stuck" cache with no error logged anywhere
(`last_error` stays null, since the job function was never even invoked).

**Why the fix is safe**: `_latest_ref_dt()` / `_latest_ref_dt_ch2()` compute the target run
purely from `datetime.now(timezone.utc)` at call time, not from which cron slot fired them —
so letting a job run up to 30 min late still resolves to the correct (or newer) ref_dt. The
per-model `_ch1_lock`/`_ch2_lock` already prevent overlapping runs of the same model
independently of this. And the GRIB persistence cache (`grib_cache.py`) already skips
re-downloading any file it already holds for that ref_dt, so a late-but-not-skipped run never
re-fetches data it already has — it only ever fetches what's actually missing.

**Fix**: `misfire_grace_time=1800` (30 min) added to both `add_job()` calls in
`scheduler.py`. Generous enough to absorb the observed jitter (seconds) with wide margin,
while still small relative to the 6h cycle and the collectors' own 2–3h "already published"
guard windows, so a genuinely stuck process still skips forward cleanly instead of piling up
stale triggers.

**Verification**: not reproducible as a unit test (APScheduler's own misfire detection, not
application logic) — confirmed via PRD log history (`docker logs lsmfapi`, both misses were
CH2-only, both under 7s) and via the dashboard's `is_current`/`ref_dt` per model. Post-deploy,
confirm no further `"was missed by"` lines for `collect_ch2eps` without a matching hourly
`executed successfully`, and that CH2's `ref_dt` advances each 6h slot going forward.

---

## v0.4 — Recipes (not started)

- `Recipe` + `RecipeRule` SQLite models (additive / multiplicative corrections per variable, per station or global)
- `GET/POST/PUT/DELETE /api/recipes` CRUD endpoints
- Recipe engine: apply corrections transparently in `/api/forecast/station` response
- Recipe editor GUI: per-station bias table → define correction rules → save

---

## v0.5 — Enhancements (backlog)

- Bilinear interpolation for smoother station-level values
- Statistical recipe suggestions (auto-compute mean bias from accuracy data)
- Local LLM integration (Ollama): accuracy + bias stats → natural-language analysis + Recipe suggestions
- Push notifications when new forecast run is ingested
- Configurable percentile bands (p10/p90) as alternative to absolute min/max

---

## Known Issues (not yet fixed)

- `sunshine_minutes` wrong on CH2 first step (h=34 accumulation spans 34h not 1h)
