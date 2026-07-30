# LSMFAPI — Tech Debt, Security & Efficiency Remediation Plan

**Audited**: 2026-07-30, `main` @ v0.3.7
**Method**: full read of `src/`, `static/`, build/CI files + **live inspection of PRD**
(`ssh sdh`, `docker logs`, `/api/dashboard`, `df`, `du`)
**Status**: findings only — **nothing has been fixed**

---

## How to use this document

Every item is `Finding → Evidence → Fix → Verify`. Work strictly top-down: **P0 → P1 → P2 → P3.**
One item per commit, message prefixed with the item ID (`fix(dashboard): P0-1 escape telemetry
output`).

**Verification tags** on each item:
- ✅ **verified** — I confirmed this directly in code and/or on the live PRD host.
- 🔎 **reported** — found by a sub-audit, plausible and cited, but *you must re-confirm before
  acting*. Do not trust a line number blindly; re-read the file.

### Hard rules for whoever implements this

1. **Read `.ai/instructions/04-constraints.md` first.** Several findings are violations of rules
   already written there. The rules are right; the code drifted. Never "fix" a finding by relaxing
   a constraint.
2. **No local Linux.** This dev box has no Docker and no WSL, and `eccodeslib` is Linux-only by
   marker. Anything touching the container or the GRIB stack can **only** be proven by pushing and
   watching CI. Never claim local verification of Linux behaviour.
3. **Do not touch the eccodes pins** (`eccodes` 2.47.0 / `eccodeslib` 2.47.3.23 /
   `eccodes-cosmo-resources-python` 2.44.0.1). They are deliberate and load-bearing. Library
   version must stay ≥ definitions version.
4. **Never touch PRD directly.** All changes ship through the pipeline. The PRD compose file lives
   in a *different repo* — anything needing a host-side change must be flagged to the user, not
   done.
5. **Minimal diff.** Do not refactor adjacent code, do not add comments to code you didn't change,
   do not add error handling for impossible cases.
6. Each shipped item: bump `pyproject.toml`, add a `.ai/context/features.md` + `.ai/RESUME.md`
   entry, as this project already does for every fix.
7. **Do not batch P0 items together.** They need independent verification on PRD.

---

# P0 — Fix immediately

## P0-1 · ✅ Stored XSS in the operator dashboard, injectable by any anonymous request

**Severity: critical.** This is the only item here that a third party can actively exploit.

**Finding**: untrusted request data flows unescaped into the dashboard's DOM via `innerHTML`.
The full chain, all verified:

| Step | Location | What happens |
|---|---|---|
| 1 | `api/routers/forecast.py:76`, `:103` | `station_id: str = Query(...)` — **no `max_length`, no `pattern`** |
| 2 | `api/routers/forecast.py:90` | `f"Unknown station: {station_id}"` — raw echo into the 404 body |
| 3 | `api/main.py:52-56` | middleware buffers the response body → `record_error(method, path, status, detail)`; `path` is `request.url.path`, also raw |
| 4 | `database/telemetry.py:22-28` | stores `path` + `detail` verbatim in `_recent_errors` |
| 5 | `api/routers/dashboard.py` | published **unauthenticated** at `/api/dashboard` |
| 6 | `static/dashboard.js:285-292` | `tbody.innerHTML = ... ${e.path} ... ${e.detail ?? ""}` — **no escaping** |

Confirmed there is **no `escapeHtml` helper anywhere** in `static/*.js` — `grep` for
`escapeHtml` returns nothing; the file uses `textContent` elsewhere (safe) but `innerHTML` here.

Two independent injection vectors, both anonymous and unauthenticated:
```
GET /api/forecast/station?station_id=<img src=x onerror=…>   → reflected via step 2
GET /api/<img src=x onerror=…>                                → 404, path recorded raw at step 3
```
`static/dashboard.js:3` auto-refreshes every 10 s, so the payload executes on the operator's next
poll, same-origin, with no interaction. Additional unescaped sinks at `static/dashboard.js:196-205`
(`state.last_error`) and `:177`. Lower-risk secondary sink: `static/data.js:24-26` builds
`<option>` from proxied upstream station names.

**This sink is provably live**: the PRD dashboard's error ring is *currently* full of
`Unknown station: metar-LSGC` etc. — step 2 is firing every 30 minutes in production right now.

I deliberately did **not** inject a payload into PRD; that would pollute the operator's error ring.
The code path is unambiguous without it.

**Fix**:
1. Add an `escapeHtml()` helper in `static/dashboard.js` and apply it to **every** interpolation of
   server data inside an `innerHTML` template — `e.path`, `e.detail`, `e.method`, `e.status`,
   `state.last_error`. Do the same for `static/data.js:24-26`.
   ```js
   const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g,
     c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));
   ```
   Prefer building rows with `textContent` where practical — escaping is the minimum, not the ideal.
2. **Defence in depth, do all three:**
   - Constrain the input: `Query(..., max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")` on `station_id`
     in both endpoints. Rejects the payload at the edge and cuts the 404 noise.
   - Stop echoing raw input into error messages. Use a fixed message
     (`"Unknown station"`) with the id in a structured field, not interpolated prose.
   - Truncate/sanitise in `telemetry.record_error` so the ring never stores markup.
3. Vanilla JS only — **no npm, no build step, no sanitiser library** (`04-constraints.md`).

**Verify**: request `?station_id=<b>x</b>`, open `/dashboard`, confirm the row renders the literal
text `<b>x</b>` and does **not** bold it. Check DevTools for no injected element. Then confirm the
`pattern` rejects it with a 422 before it ever reaches telemetry.

## P0-2 · ✅ TLS certificate verification disabled on 4 HTTPS clients

**Finding**: `verify=False` — full MITM exposure on every Lenticularis call:

| File:line | Caller |
|---|---|
| `api/routers/dashboard.py:34` | `/api/stations` public proxy |
| `collectors/icon_ch1_eps.py:769` | CH1 `_fetch_stations` |
| `collectors/icon_ch2_eps.py:161` | CH2 `_fetch_stations` |
| `scripts/diag_interlaken.py:40` | diagnostic script |

The station list drives *what the service collects and caches*, so a MITM can steer collection and
poison every downstream forecast. The MeteoSwiss clients correctly verify
(`icon_ch1_eps.py:705`, `:880`, `collectors/base.py:12`) — so this is an inconsistency, not a
platform constraint.

**Fix**: remove `verify=False` from all four. If it was added to work around a self-signed
internal cert, the correct fix is to trust that CA explicitly (mount the CA bundle and point
`httpx` at it via config), **never** to disable verification globally.
**Ask the user why it was added** before removing — if `lenti.cloud` / `lenti-dev.lg4.ch` presents
a self-signed cert, a blind removal breaks all collection. Test against the real host before
merging.

**Verify**: a collection run completes and `/api/stations` returns 522 stations with verification on.

## P0-3 · ✅ `save_cache()` blocks the event loop for 43 s, 8× per day

**Same bug class as v0.3.5, and a direct violation of a documented hard rule.**
`04-constraints.md`: *"Never call CPU-bound work directly from `async def` … anything synchronous
blocks every HTTP request for its full duration, `/health` included."*

**Finding**: `save_cache()` is called synchronously — no `await`, no thread — from
`async def _run_ch1eps()` (`scheduler.py:38`) and `async def _run_ch2eps()` (`scheduler.py:57`),
plus lifespan shutdown (`api/main.py:39`). It performs three large serialisations:

| Step | Code | Live size on PRD |
|---|---|---|
| `json.dumps` of station + altitude caches | `database/cache.py:275` | **170 MB** |
| `np.savez_compressed` wind grid | `database/cache.py:304` | **187 MB** |
| `np.savez_compressed` thermal grid | `database/cache.py:336` | **290 MB** |

**Evidence — measured on PRD, CH1 run of 2026-07-30:**
```
11:18:56,738  IconCh1EpsCollector.collect() complete — 520 stations
11:18:59,398  Cache saved: 524 CH1 + 524 CH2 stations → cache.json     (+ 2.66 s)
11:19:15,087  Grid cache saved … (186.9 MB)                            (+15.69 s)
11:19:39,869  Thermal grid cache saved … (290.0 MB)                    (+24.78 s)
11:19:39,941  Job "_run_ch1eps" executed successfully
                                                    total stall  =  43.20 s
```
The access log contains **zero requests** in that 43 s window, while `/dashboard` probes appear
every ~30 s immediately before and after — exactly the "gap with no output" tell that
`04-constraints.md:49` describes. Note that `np.savez_compressed` releasing the GIL does not help:
the call is on the loop thread, so the loop isn't iterating regardless.

**Impact**: 43 s × 8 runs/day ≈ **5.8 min/day of hard unavailability**, plus once per shutdown.
Currently invisible to monitoring — see P1-1.

**Fix**:
1. `await asyncio.to_thread(save_cache)` at both `scheduler.py` call sites (`asyncio` already
   imported). Do **not** hold a lock across it.
2. Leave `api/main.py:39` synchronous — the loop is closing there and losing the cache is worse
   than a slow shutdown. Add a one-line comment saying why.
3. Cut the intrinsic cost (`to_thread` still leaves `json.dumps` holding the GIL ~2.7 s):
   - Try `compresslevel=1` on both npz writes, or plain `np.savez`. These are float16 arrays;
     default zlib level 6 is burning ~40 s for modest savings. **Measure time and size both ways
     and put the numbers in the RESUME entry.**
   - Only rewrite the grid that actually changed. `save_cache()` currently rewrites **both** grids,
     all 121 frames, on every run — even though a CH1 run only touched rows 0–33. Add a per-grid
     dirty flag set by `set_grid_wind_cache()` / `set_thermal_grid_cache()`.

**Verify**: on PRD, the gap between `collect() complete` and `executed successfully` shrinks to a
few seconds and access-log lines appear *inside* it. Confirm both npz files still load on the next
restart (`Grid cache loaded` / `Thermal grid cache loaded` at INFO).

## P0-4 · ✅ GRIB peak disk is ~200 GB (CH1) / ~480 GB (CH2) — the v0.3.6 "fix" did not lower the peak

**Finding**: `.ai/RESUME.md:88` and `features.md` state W files are *"deleted right after their
in-memory extraction"* so *"the GRIB disk stops ballooning"*. **The peak is unchanged.** The
deletion (`icon_ch1_eps.py:1010-1011`, `icon_ch2_eps.py:377-378`) runs **after**
`await asyncio.gather(*all_tasks)` (`icon_ch1_eps.py:938`) and after `pres_array("W")`
(`:1005`). So for the whole download phase all three 3-D variables coexist for all horizons.
U/V are also only deleted inside `_build_grid_wind_cache` (`:625`, `:660`), which likewise starts
after every download.

**Evidence — live on PRD, mid-CH1-run:**
```
/dev/sdc1  932G  332G  600G  36%  /mnt/cache
291G  /mnt/cache/lsmfapi-grib
  ├── 201G  ch1      ← 34 horizons × 3 vars × ~1.84 GB ≈ 188 G  (matches)
  └──  91G  ch2
```
CH1 alone peaks at **~201 GB**. CH2, with 87 horizons, projects to **~480 GB**. Per the PRD logs
CH1 runs ~14:00–15:20 and CH2 ~15:00–17:05 — **they overlap ~20 min**, and the two model dirs are
purged independently (`grib_cache.py` only purges other *ref_dt* of the *same* model). A lingering
CH1 dir plus a peaking CH2 run is ~680 GB against **600 GB available**. This is the same
disk-exhaustion class that already took the host down once (v0.3.6, `/` filled at 450 GB).

Compounding: `grib_cache.py:20` hardcodes `_BASE = Path("/tmp/lsmfapi_grib")` with **no config
override**, and neither compose file bind-mounts it. The `/mnt/cache` relocation exists only in the
separate PRD repo — so **DEV still writes 200–480 GB into the container's writable layer**, which is
exactly how the original incident started.

**Fix** (staged — item 1 is cheap and urgent, item 2 is the real fix):
1. **Make the path configurable** and log it at startup: add `grib_cache_dir` to `config.py` +
   `config.yml.example`, default `/tmp/lsmfapi_grib`, read via `get_config()` (never `os.environ` —
   `04-constraints.md`). Add the bind mount to `docker-compose.yml` so DEV stops filling its
   container layer. This alone removes the DEV footgun.
2. **Delete each 3-D file as soon as it is consumed**, so peak becomes O(concurrency) not
   O(horizons). This is the same change as P1-4 (single-pass extraction): extract station columns
   *and* grid-sample columns in one read inside `_fetch_step`, then `unlink()` immediately. Peak
   drops from ~480 GB to a few GB. Treat P0-4.2 and P1-4 as one piece of work.
3. **Correct the docs.** `.ai/RESUME.md:88` and the v0.3.6 row in `features.md` overstate what was
   fixed. Rewrite to: "W files are deleted before the grid build, which bounds *retention*; peak
   during download is unchanged and still ~200/480 GB."
4. Add a startup WARNING if free space on the GRIB volume is below a threshold (say 550 GB) —
   `08-operability.md` wants failures diagnosable from logs, and this one is silent until the disk
   is full.

**Verify**: after item 2, `du -sh /mnt/cache/lsmfapi-grib/ch2` stays in single-digit GB during a
CH2 run. After item 1, the startup log names the resolved GRIB dir.

---

# P1 — High

## P1-1 · ✅ `/health` can never fail, so P0-3 and P0-4 are invisible

**Finding**: `api/main.py:74-76` —
```python
return JSONResponse({"status": "ok", **cache_stats()})
```
Unconditional `200 {"status":"ok"}`. It cannot return 503 and reports nothing about the scheduler,
the DB, or cache staleness. `08-operability.md:89-94` mandates 503 when any critical subsystem is
down, plus service name, **version**, uptime, DB status and scheduler status. `cache_stats()`
returns booleans the endpoint never inspects — a cold cache with a dead scheduler and no
`config.yml` still reports healthy.

Consequence, confirmed on PRD: `FailingStreak=0, RestartCount=0` after 8 days, despite the 43 s
stalls in P0-3. A blocked loop makes the probe *hang* rather than fail. The container reports
healthy while serving nothing — worse than reporting unhealthy.

**Fix**: implement per `08-operability.md` — `service`, `version` (P2-1), `uptime_seconds`, and a
`checks` object covering `sqlite` (cheap `SELECT 1`), `scheduler` (`running (N jobs)`; expose the
job count from `CollectorScheduler`), `cache` (warm/cold + newest `init_time` age). Return **503**
when the scheduler is stopped or SQLite is unreachable. **Do not 503 on a merely stale cache** —
stale-but-serving is intended behaviour and Traefik must not drop the container for it. Log non-ok
at WARNING. Keep it cheap; it is probed every 10–30 s.

Also pass `timeout=3` to the `urlopen` in the `Dockerfile:21` healthcheck so the probe self-limits
instead of relying on Docker's `--timeout`.

## P1-2 · ✅ Error deque is 100 % saturated by recurring 404 noise; errors never reach the logs

**Finding**: `telemetry.py:11` — `deque(maxlen=20)`, appended per occurrence with no
de-duplication. On PRD **every one of the last 20 entries** is the same five station 404s,
repeating every 30 min. `error_count` = **2128** over 8 days ≈ 5 × every 30 min — essentially all
of it one pattern. Real errors are evicted within minutes; the panel has shown nothing useful for
8 days.

**Also verified**: those five IDs (`metar-LSGC`, `metar-LSGS`, `windline-6116/6200/6679`) **no
longer exist upstream** — `/api/stations` returns 522 and none match, while the cache still holds
524. A client is polling for stations deleted upstream. A client asking for a nonexistent station
is normal traffic, not a service error, and must not be able to flood the diagnostic buffer.

**Compounding — errors are invisible in logs.** `record_error` (`telemetry.py:29`) and
`record_download_error` (`:42`) log at **DEBUG**, while root level is INFO (`api/main.py:16-19`) —
so these lines **never appear at all**. `08-operability.md:57` requires WARNING for 4xx, ERROR for
5xx.

**Fix**:
1. Aggregate instead of append: key by `(method, path, status, detail)`, store
   `{first_seen, last_seen, count}`, bump `count` on repeat. Update `static/dashboard.js` to render
   count + last_seen (**with escaping — see P0-1**).
2. Separate `client_error_count` (4xx) from `error_count` (5xx + collector failures), so the latter
   means "might be our fault".
3. Log 5xx and download errors at ERROR, 4xx at WARNING.
4. 🔎 Guard the counter increments with a `threading.Lock`. Since v0.3.5 collectors run under
   `asyncio.to_thread` and call `record_download_error` from a worker thread; `_error_count += 1`
   (`telemetry.py:34`) is a non-atomic read-modify-write. `deque.append` is already thread-safe, so
   this is lost-count accuracy, not corruption.
5. 🔎 `record_request(method, path)` (`telemetry.py:14-16`) ignores both parameters. Either use them
   (a per-path counter would be genuinely useful) or drop them.

**Verify**: the 404 group appears once with a rising count; a deliberately triggered 500 shows in
the panel **and** as an ERROR log line.

**Out of scope, tell the user**: Lenticularis should stop polling the five deleted stations.

## P1-3 · 🔎 An unauthenticated request can stall the event loop / OOM the host

**Finding**: `_MAX_RESPONSE_CELLS = 10_000_000` (`api/routers/forecast.py:44`) bounds *cell count*
but is far too permissive as a memory/CPU guard. At the cap: ~10 M JSON floats ≈ 70 MB body, built
as Python lists (`_to_nullable`, `:69-71`, ~24 B/float ⇒ ~250 MB) then serialised by `json.dumps`
— peak ~0.5 GB per request. Both grid handlers (`:176-226`, `:267-314`) are `async def` with **zero
await points**, on a single uvicorn worker (`Dockerfile:24`, no `--workers`). Neither compose file
sets `mem_limit`, so the **host** OOMs, not the container. No rate limiting anywhere.

Good news, verified by the audit: the cap **does** cover both grid paths (`:191`, `:281`) and fires
*before* the expensive materialisation; bbox validation is genuinely solid (inverted rejected at
`:158`, domain-clamped `:160-163`, NaN/inf safely rejected because all NaN comparisons are False);
`hours` is `ge=1,le=120`; `level_m` and `stride_km` are allowlists. So the **threshold and the
event-loop blocking** are the defects, not the validation.

**Fix**: lower `_MAX_RESPONSE_CELLS` to what the service should actually emit (start ~2 M and
measure the resulting body size); move the response construction to
`await asyncio.to_thread(...)`; set `mem_limit` in `docker-compose.yml`. Note the wind-grid call
site passes a hardcoded field count of `3` (`:191`) — adding a field silently under-counts the
budget; derive it instead.

## P1-4 · 🔎 Every GRIB file is read 4× in full — ~1.3 TB of disk I/O per CH2 run

**Finding**: `_read_grib2_eccodes` is deliberately two-pass (metadata scan
`icon_ch1_eps.py:328-341`, then fill `:365-382`), and it is called on the **same file** from two
places — `_fetch_step` (`:817`) and again during the grid build (`:615`, `:651`, `:460`). So U/V
files are read **4 full times**. CH2 U/V alone: 174 files × 1.84 GB × 4 ≈ **1.28 TB** read per run.

Additionally the "pressure level probe" (`:883-896`) downloads U at `HORIZONS[0]` to
`U_probe.grib2`, reads it, deletes it — then the normal fetch downloads **the same asset again** as
`U_000.grib2`. One redundant 1.84 GB download + 2 extra full reads per model per run, for a value
(`len(pres_level_nums)`) obtainable from the first successful U fetch.

**Fix**: extract station columns **and** grid-sample columns in a single read inside `_fetch_step`
(`extract_indices = concat(station_idx, sample_idx)`), then `unlink()` the file immediately. This
one change fixes the 4× re-read, removes the grid build's dependency on retained files, and
**fixes the P0-4 peak disk** — do it as one piece of work with P0-4.2. Drop the probe and take the
level count from the first U result.

This is the single highest-leverage efficiency change in the repo, but it is also the most
invasive. **Land the whole P0 tier and the P3-1 test gate first**, then do this behind a green CI.

## P1-5 · 🔎 GRIB parsing and the station-stats loop still block the event loop

**Finding**: v0.3.5 moved only `collect_grid` to a thread (`icon_ch1_eps.py:1143`,
`icon_ch2_eps.py:525`). Still synchronous on the loop:
- `_read_grib2_eccodes(dest, …)` at `icon_ch1_eps.py:817` / `icon_ch2_eps.py:204` — inside
  `async def _fetch_step`, once per file (782 CH1 / 2006 CH2 files), each a two-pass full read.
- The station-stats loop `icon_ch1_eps.py:1054-1136` / `icon_ch2_eps.py:438-520` — for CH2, ~8.7 k
  `StationForecastHour` + ~78 k `AltitudeWindLevel` Pydantic objects and ~296 k `compute_stats`
  calls.

Same failure class as v0.3.5, still present for the larger phase.

**Fix**: wrap the parse call and the station loop in `asyncio.to_thread`. Mind the shared-state
rule from v0.3.5: build local objects, and only the `set_*` cache writes touch shared state.

## P1-6 · ✅ `config.yml` is committed, contradicting the project's own constraint

**Finding**: `04-constraints.md` states *"`config.yml` and `.env` are gitignored. Only
`config.yml.example` … is committed."* **False** — `config.yml` is tracked and `.gitignore` (whose
first section is literally headed *"Project-specific secrets and local state"*) does not mention
it. `README.md:35` repeats the false claim; `scripts/LSMF-dev.ps1:70` correctly says the opposite.

**Verified — no leak to clean up, no history rewrite needed:**
- Current contents are non-secret (STAC URLs + a Lenticularis base URL).
- `git log -p --follow -- config.yml` shows **no** token/password/secret/key string in any revision.
- The Dockerfile does **not** `COPY config.yml`; compose bind-mounts it (`docker-compose.yml:6`).
  Nothing was baked into a published image.

**Why it still matters**: it is the designated home for credentials and is one commit from
publishing one. Two live secondary problems: the tracked file sets
`lenticularis.base_url: https://lenti-dev.lg4.ch` — a **dev** URL, while `01-project-overview.md`
documents prod as `https://lenti.cloud` — and it carries the dead `scheduler:` block.

**Fix**: `git rm --cached config.yml` (keep it on disk); add `config.yml` to `.gitignore`; delete
the dead `scheduler:` block; correct `README.md:35`; make sure `config.yml.example` documents every
key `config.py` reads. **Coordinate with the user first** — once untracked it no longer arrives via
git/rsync, so both hosts must already have their own copy or the deploy breaks.

## P1-7 · ✅ Config is silently ignored, unlogged, and fails without a diagnostic

**Finding**: `src/lsmfapi/config.py` (26 lines)
- No `extra="forbid"` on any model, so Pydantic v2's default `extra="ignore"` **silently discards
  unknown keys**. The four `scheduler.*` keys in the committed `config.yml` are dropped on the
  floor; the real schedule is hardcoded cron (`scheduler.py:74`, `:80`). An operator editing
  `ch1eps_interval_hours` gets no error and no effect — precisely the "silently fall back to a
  magic default" that `08-operability.md:121` forbids. A typo (`base_ur1:`) is equally silent.
- `get_config()` (`:25`) does `yaml.safe_load(Path("config.yml").read_text())` — CWD-relative, no
  existence check. A missing file raises `FileNotFoundError` from inside a collector
  (`icon_ch1_eps.py:703`), which `scheduler.py:42-44` catches into `mark_failed` — **so the service
  boots "healthy" with no config at all.**
- **Nothing logs the resolved config.** No logger in the module. `08-operability.md:118-121`
  requires every non-secret key logged at INFO on startup. An operator cannot tell from the logs
  which Lenticularis URL is in use — directly relevant given P1-6's dev/prod mixup.

**Fix**: add `extra="forbid"`; load with an explicit existence check that logs **CRITICAL** with
the resolved absolute path and fails fast; log every resolved key at INFO from the lifespan; keep
`lru_cache`. **Order matters**: removing the dead `scheduler:` block (P1-6) must land *before or
with* `extra="forbid"`, or startup breaks.

## P1-8 · 🔎 Dashboard, Data Inspector, `/api/dashboard` and `/docs` are publicly reachable

**Finding**: `docker-compose.dev.yml:14-21` defines a single Traefik router with a **Host rule
only** — no path matcher and no `basicauth`/`ipallowlist`/`forwardauth` middleware — pointing at
port 8000 for the whole app. `dashboard.router` is mounted with no prefix guard
(`api/main.py:65`), and `api/main.py:69-71` redirects `/` straight to `/dashboard`. So publicly
exposed: `/dashboard`, `/data`, `/api/dashboard` (cache internals, collection status, `last_error`
strings, the last 20 error detail bodies), plus `/docs`, `/redoc`, `/openapi.json` — the latter
because `FastAPI(...)` at `:42` never sets `docs_url=None`.

This is what makes P0-1 exploitable by a stranger rather than requiring internal access.

PRD's compose lives outside this repo so I could not verify its router — but the app provides
**no server-side separation**, so any router forwarding `/` inherits everything.

**Fix**: put the operator surface behind auth or an IP allowlist. Options, in order of preference:
1. Traefik middleware (`basicauth` or `ipallowlist`) on `/dashboard`, `/data`, `/api/dashboard`,
   `/docs`, `/redoc`, `/openapi.json` — in the DEV compose here **and** flag to the user for the
   PRD compose in the other repo.
2. Disable the docs endpoints in production (`docs_url=None, redoc_url=None, openapi_url=None`).
3. Consider a separate internal port for the operator surface.
**Ask the user which they want** — this changes how they reach their own dashboard.

## P1-9 · 🔎 Unhandled 500s are never recorded, and there is no global exception handler

**Finding**: `api/main.py:52-56` reads `response.body_iterator`, which only exists for responses
that completed through the app. An unhandled exception propagates out of `BaseHTTPMiddleware`
before that code runs — so **genuine 500s are never counted and never appear in the dashboard**.
The error class that matters most is the one the panel cannot see. `07-api-conventions.md:88`
requires a global exception handler; there is none.

**Fix**: add an `@app.exception_handler(Exception)` returning the documented
`{"error": {code, message}}` envelope, logging at ERROR with `exc_info=True`, and recording to
telemetry. Never put exception text in the response body (P0-1 feeds it to the dashboard) — log it,
return a generic message.

## P1-10 · 🔎 STAC search can stitch two different model runs into one forecast

**Finding**: `icon_ch1_eps.py:129` sends an **open-ended** interval
(`f"{ref_dt…}/.."`) and `:139` takes `features[0]`, with no `sortby`. Feature order is
server-defined. A run takes 1.5–2 h and MeteoSwiss publishes continuously, so if a newer run
appears mid-collection, later horizons can resolve to a **different `ref_dt`** than the `init_time`
stamped into the response (`:1120`, `icon_ch2_eps.py:504`) — a silently mixed-run forecast.

**Fix**: use a closed interval (`ref/ref`) so only the intended run can match. Also
`:142` picks an arbitrary asset via `next(iter(assets.values()))` — assert exactly one, or select
by name.

## P1-11 · 🔎 Silent zero-fallback can make CH2's first hour ~34× too wet — and the documented "known issue" is stale

**Two corrections to the project's own docs**, both verified:

1. **The `features.md` "Known Issues" entry is wrong.** It says `sunshine_minutes` is wrong on
   CH2's first step because the accumulation spans 34 h. **CH2 already handles this**:
   `ACCUM_PRIOR_H = 33` (`icon_ch2_eps.py:77`) drives a shadow h33 fetch (`:284-292`) over all four
   `ACCUM_VARS`, and `deaccum` differences against it (`:396-401`). CH1's `_deaccumulate`
   (`icon_ch1_eps.py:155`) is only ever applied at step 0 = h0 where accumulation is genuinely 0.
   The thermal grid gets the same baseline via `accum_prior_h` (`icon_ch2_eps.py:552`).
   **Delete the stale Known Issue** rather than "fixing" it.
2. **The real, live variant**: `_get_prior` (`icon_ch2_eps.py:382-394`) falls back to
   `nan_prior = np.zeros(...)` — **misnamed; it is zeros, not NaN** — on *any* failure (task
   cancelled, exception, shape mismatch, `None`), with **no log and no telemetry**. If the h33
   fetch fails, h34 silently gets the full 34-hour accumulation as a 1-hour delta: precipitation up
   to ~34× too high, and nothing anywhere says so.

**Fix**: make the fallback loud — log WARNING naming the variable and record to telemetry, and
prefer emitting **NaN → null** over zeros so a failure surfaces as missing data rather than a
plausible-looking wrong number. Rename `nan_prior` to match what it holds.

🔎 Related, same area, worth fixing together: `prev_aswdir`/`prev_aswdifd`/`prev_dursun` are only
advanced *inside* `if raw is not None` branches (`icon_ch1_eps.py:499-500`, `:510`), so a single
missing horizon makes the **next** step difference against a 2-hour-old baseline → silently
double-counted solar/sunshine. The station path yields NaN in the same situation, so grid and
station outputs diverge.

## P1-12 · ✅ Container runs as root

**Finding**: no `USER` directive in the `Dockerfile`. uvicorn runs as uid 0, writing to a
host-bind-mounted `./data` (`docker-compose.yml:7`) — confirmed on PRD, `/app/data` files are owned
by `root`. Any RCE gets root in-container and root-owned files on the host volume.

**Fix**:
```dockerfile
RUN useradd --create-home --uid 10001 app
RUN chown -R app:app /app      # after the COPY steps
USER app
```
**Do not ship this unilaterally.** `/app/data` is a bind mount owned by root on the host, and the
PRD compose is in another repo. A uid mismatch means `save_cache()` starts failing — and per P1-2
that failure would be invisible. **Flag to the user**: the host dir must be chowned to 10001 as
part of the same deploy.

---

# P2 — Medium

## P2-1 · ✅ App version hardcoded to `0.1.0`
`api/main.py:42` — `FastAPI(title="LSMFAPI", version="0.1.0", …)` while `pyproject.toml` is
**0.3.7**. Seven releases of drift, wrong in `/docs`, `/openapi.json`, and whatever `/health`
reports (P1-1). **Fix**: `importlib.metadata.version("lsmfapi")` once, reused by `/health`.

## P2-2 · ✅ Base image floats; Poetry unpinned; no `.dockerignore`
- `Dockerfile:1` — `FROM python:3.11-slim`, no digest. This exact tag rolling bookworm→trixie
  caused the v0.3.3 PRD crash loop; `.ai/RESUME.md:202` defers the fix until it "bites again".
  Two builds of one git SHA are not the same image. **Fix**: pin
  `FROM python:3.11-slim@sha256:<digest>` with a comment giving version + date. Get the real digest
  from the registry; never invent one.
- `Dockerfile:9` and `integration-test.yml:23` — `pip install poetry`, **unversioned**. This is
  *literally the drift class* that caused v0.3.4. Pin it.
- **No `.dockerignore`** (absent). Build context ships `.git/`, `.ai/`, and `data/` — the
  multi-hundred-MB npz caches — on every build, including `scripts/LSMF-dev.ps1`'s remote
  `up --build`. Nothing leaks into the image (there is no `COPY . .`), so this is speed, not
  disclosure. **Fix**: add one.

## P2-3 · ✅ Orphaned 161 MB temp file on PRD that cleanup can never reclaim
PRD `/app/data` holds `grid_cache_ch1.tmp.npz`, **161 MB, dated Jul 16** — a partial write from an
interrupted save, in pre-v0.3.3 naming. `_remove_legacy_grid_files()` (`cache.py:473`) iterates a
fixed `_LEGACY_GRID_FILES` list containing the legacy `*.npz` names but **not** `*.tmp.npz`, so it
is unreclaimable. **Fix**: on startup, glob `*.tmp.npz` / `*.tmp` in the data dir and unlink,
logging each at INFO — a temp file present at startup is by definition abandoned. Deleting the
existing one on PRD is the user's call.

## P2-4 · ✅ Unused dependencies: `cfgrib`, `xarray`, `aiofiles`
Verified: **zero imports** in `src/` for all three (`cfgrib` appears only in a docstring at
`_eccodes.py:10`). The collectors use the raw `eccodes` API. `xarray` is additionally pinned
`^2024.0` — a caret on a **CalVer** package, which freezes it in calendar 2024 permanently and can
never receive a 2025+ fix. **Fix**: remove all three from `pyproject.toml`, `poetry lock`, and
confirm CI stays green. Removes a large GRIB-adjacent chunk of the image and its attack surface.
Do this in its own commit so a revert is trivial.

## P2-5 · 🔎 `numpy = "^1.26"` is an unnecessary cap that blocks local dev
`pyproject.toml:26` → locked 1.26.4 (Feb 2024). **No dependency requires `<2.0`**: scipy 1.17.1
declares `numpy>=1.26.4,<2.7`; xarray `>=1.24`; cfgrib and eccodes `*`. The ceiling comes purely
from Poetry's caret. It costs real friction — 1.26.4 has no cp313 wheels, which is why
`poetry install` fails on this dev box (`.ai/RESUME.md:209`) — and it makes
`python = "^3.11"` (i.e. `<4.0`) a false advertisement, untested since CI runs 3.11 only.
**Fix**: relax to `>=1.26,<3` (or `^2`), regenerate the lock, and let CI prove it against real GRIB.
**This must not be batched** with anything else — numpy 2.x changed several semantics, and the
integration test is the only thing that will catch a regression.

## P2-6 · 🔎 Dead code — verified removable
Verified gone (nothing dangles): `ALTITUDE_TO_HPA`, `_build_level_indices`,
`_approx_hybrid_to_pressure_hpa` — only `.ai/` prose mentions them. What *does* dangle:

| Item | Location |
|---|---|
| `GridPoint`, `GridFrame`, `GridForecastResponse`, `ThermalGridFrame`, `ThermalGridResponse` | `models/forecast.py:92-161` — ~70 lines, **zero references**; only named in docstrings (`forecast.py:137`, `:237`). v0.3.3 leftovers. |
| `known_stations` import | `api/routers/forecast.py:21` — never used |
| `_parse_horizon_h` + `import re` | `icon_ch1_eps.py:94-102`, `:4` — never called |
| `_THERMAL_ACCUM_VARS`, `_THERMAL_SURFACE_VARS` | `icon_ch1_eps.py:407`, `:408-412` — never referenced (list re-hardcoded inside the function) |
| `keep: bool = False` param | `icon_ch1_eps.py:783`, `icon_ch2_eps.py:175` — never passed; its docstring describes deleted behaviour |
| `_GRID_LATS` / `_GRID_LONS` | `icon_ch1_eps.py:35-36`, assigned `:730-731` — **written, never read**; ~18 MB retained forever |
| `_deaccumulate`, `_horizon_str` imports in CH2 | `icon_ch2_eps.py:35`, `:37` |
| `PRESSURE_VARS` / `CH1_PRESSURE_VARS` | `icon_ch1_eps.py:76-77` — two identical `["U","V","W"]` lists |
| `db.py:37-38` | `with _engine.connect() as conn: pass` — a no-op "migration" |
| 7 unused `surf_array()` results + 3 unused `deaccum()` results + `lat`/`lon`/`elev` | `icon_ch1_eps.py:1001-1004`, `:1020-1022`, `:1056-1058` (and CH2 equivalents) — full `(H,M,S)` float64 arrays nothing consumes |

**Note on the response models**: deleting them means the two largest endpoints have **no OpenAPI
schema** (they already have none — they return bare `JSONResponse`). Keep the hand-built dicts (a
deliberate v0.3.3 memory decision — do not undo it), but document the real shape in the router
docstrings and fix the two stale ones (`GridForecastResponse` names a route that doesn't exist —
it's `/wind-grid`; `GridWindCache:221` still says "not persisted" though v0.3.3 added npz
persistence).

## P2-7 · ✅ `compute_wind_direction_stats` min/max is not NaN-safe and is meaningless for angles
`services/ensemble.py:27-28` uses builtin `min()`/`max()` on a list of degrees, while
`compute_stats` (`:15-17`) correctly uses `np.nanmedian/nanmin/nanmax`. Two defects: builtin
min/max does **not** skip NaN, so the result is member-order-dependent; and min/max of a circular
quantity is meaningless — members at 359° and 1° report a 358° spread when the true spread is 2°,
which for a paragliding tool is exactly when it matters.
**Fix**: `np.nanmin`/`np.nanmax` at minimum. The circular fix (min/max of
`((angle − median + 180) mod 360) − 180` offsets, re-added to the median) changes API-visible
values — **ask the user first**, Lenticularis consumes these fields.

## P2-8 · ✅ All-NaN reduction warnings still unfixed
`np.nanmin`/`nanmax` on an all-NaN vector warn from `ensemble.py:16-17`; `icon_ch1_eps.py:637-641`
and `:471-477` are likewise unwrapped, unlike `_interp_to_heights` (`:266`) which correctly uses
`np.errstate`. Post-v0.3.6 the all-NaN case is *expected* (bands below terrain are legitimately
null), so this is pure noise masking real warnings — CI reports "10 warnings".
**Fix**: guard explicitly — if `np.all(np.isnan(arr))`, return `None` without calling the reducers;
wrap the grid reductions in `np.errstate`. **Do not** blanket-suppress with
`warnings.filterwarnings`.

## P2-9 · ✅ `datetime.utcnow()` — deprecated and naive
`cache.py:87` and `:382`. Deprecated from Python 3.12, returns a **naive** datetime while the rest
of the file uses `datetime.now(timezone.utc)` (`:397`, `:429`). Feeds `_last_populated_at` →
`cache_stats()` → `/health` and the dashboard's cache age. **Fix**: mechanical, both sites. Check
nothing compares it to a naive value.

## P2-10 · 🔎 SQLite is 100 % unused, and the DB it creates isn't even persisted
- `database/models.py:10-33` defines `Recipe`/`RecipeRule`; `db.py:18` imports it so
  `create_all()` (`:22`) **creates both tables every boot**. **No endpoints exist** — `/api/recipes`
  appears only in `README.md` as planned v0.4. `get_db()`/`SessionLocal` (`db.py:27-32`) are never
  referenced outside `db.py`. Not one query is issued anywhere.
- `db.py:20` uses `sqlite:///lsmfapi.db` — CWD-relative → `/app/lsmfapi.db`, which is **not** on
  the `./data` volume, so it is discarded on every container recreate. No WAL, despite
  `08-operability.md:43` asking for WAL status to be logged.

**Fix**: leave the models (v0.4 is a real roadmap item) but either move the DB path onto the data
volume and enable WAL, or stop creating tables until v0.4 actually starts. **Ask the user** which —
this is a roadmap question, not a code one. Also fix the no-op `_run_column_migrations`.

## P2-11 · 🔎 Four different error-response shapes
`forecast.py:49-50` `_err()` returns the documented flat `{"error":{...}}`. But the
`HTTPException` paths (`:88-95`, `:115-122`) pass that dict as `detail=`, so FastAPI emits
**`{"detail":{"error":{…}}}`**. `dashboard.py:40` is a third shape (`detail=str(exc)`, which also
**leaks the upstream URL** into a public 502). FastAPI's own 422 is a fourth. Clients cannot parse
errors uniformly; `07-api-conventions.md:35-56` is satisfied by none consistently. **Fix**:
normalise on the documented envelope via the global handler from P1-9; stop putting `str(exc)` in
responses.

## P2-12 · ✅ `docs/forecast-data-reference.md` documents the implementation v0.3.6 deleted
User-facing docs, wrong in ways that matter:

| Line | Says | Reality |
|---|---|---|
| ~12 | CH1 `0–30 h`, CH2 `30–120 h` | h0–h33 / h34–h120 |
| ~12 | CH1 **11** members | ~10, read dynamically — the exact hardcoded assumption that caused the all-NULL bug |
| ~12 | CH2 `2 × daily` | 4 × daily since v0.3 |
| 174 | altitude winds "derived from ICON pressure-level fields (850/…/500 hPa mapped to approximate altitudes)" | **Deleted in v0.3.6.** No `pv` in EPS files; heights come from HHL interpolation |
| 181 | "`W` at pressure levels" | model levels interpolated to MAMSL |
| 195–207 | a full altitude→hPa mapping table | the mechanism no longer exists |

`00-ai-usage.md` rule 5: human-readable docs are derived output, never more than one session
behind. This is several versions behind and actively describes removed code.
**Fix**: rewrite from `architecture.md`'s "Altitude Level Mapping — geometric height (MAMSL), not
pressure". Delete the hPa table. Same pass: `README.md:35` (P1-6) and the stale `.ai/RESUME.md`
Known-Issues/key-files lists (4 of 5 entries stale — they still describe CH2 at h30/3-hour steps,
unmapped `cin: -999.9` (handled at `icon_ch1_eps.py:413`), all-null U/V/W (fixed v0.3.6), and
wind-grid as a stub (`set_grid_wind_cache` is called at `:842`); `:575-578` still lists the deleted
`accuracy.py` and `static/index.js` as current).

## P2-13 · 🔎 Stale/contradictory module docstrings
`icon_ch2_eps.py:1-9` says "30–120 h, 2 runs/day (00Z/12Z), 21 members, 3-hour steps" while
`:75` is `range(34,121)` (hourly) and `:95` says 4 runs/day — the module and class docstrings
contradict each other. `icon_ch1_eps.py:694` says "0–30 h" while `:64` is `range(34)`.
Also `_latest_ref_dt` (`icon_ch1_eps.py:145`) vs `_latest_ref_dt_ch2` (`icon_ch2_eps.py:80`) differ
**only** in `2 * 3600` vs `3 * 3600` — fold into P3-5 if that lands, otherwise just fix the docs.

---

# P3 — Process & structural

## P3-1 · ✅ A tagged release can ship on a red test — do this before P1-4/P2-5
`integration-test.yml:3-7` triggers on `push.branches: ["main"]` + PRs.
`docker-publish.yml:3-6` triggers on `push.tags: ["v*"]`. **A tag push matches only
docker-publish** — GitHub's `push.branches` filter does not match tag refs. There is no
`workflow_run`, no `needs:`, no required status check. The image, **including the mutable `latest`
tag** (`docker-publish.yml:52`), publishes regardless of test state — and `docker-compose.yml:3`
pins the deployment to `:latest`, so a bad tag build auto-becomes the deployed image.

`06-testing-conventions.md:68` says a green run is load-bearing and forbids tagging on red. That
rule is currently enforced by human memory only, and history shows why that is thin: CI was red
for 10 weeks and a broken eccodes stack reached PRD.

**Fix**: gate the publish — run the integration test as a `needs:` job inside `docker-publish.yml`,
or use `workflow_run` so a tag build only proceeds on a green test for that commit. Adds ~5 min to
a release; that is the right trade. **Land this early** — it is the safety net for the riskier
items.

## P3-2 · ✅ One test, no lint, no types, no scanning
`tests/` = `__init__.py` + `test_e2e_collection.py`, **one** test function
(`test_ch1_collects_interlaken:69`) with 13 asserts on one station and 2 horizons, gating 3,450
lines of source. Plausibility ranges are wide enough to pass with wrong-but-plausible values.

- **Ruff is configured but not installed** — `pyproject.toml:44-46` sets `[tool.ruff]`, but ruff is
  **not** in the dev group (`:30-32` = pytest + pytest-asyncio only). `poetry run ruff check`
  cannot run; the codebase has never been linted through the lock. This is why the dead imports in
  P2-6 survived, and why `scripts/diag_interlaken.py:13` carries a `# noqa` for a linter that
  isn't there.
- No mypy (despite fully annotated source and a reserved `.mypy_cache/` in `.gitignore`), no
  formatter check, no `pip-audit`/`bandit`/CodeQL, no `poetry check --lock`.
- **No unit-test job** — no fast lane, so every signal costs ~5 min and a 172 MB download.

**Highest-value missing unit tests** (all pure, no network, and they run on Windows):
| Target | Why |
|---|---|
| `_interp_to_heights` (`icon_ch1_eps.py:235`) | **`.ai/RESUME.md` claims this was "unit-tested locally" — the test was never committed.** `git log --all --name-only -- 'tests/*'` shows only the two existing files. The core of v0.3.6 has no committed coverage. Re-add identity / linear-weight / below-terrain-null / shape-mismatch cases. |
| `cache.py` save→wipe→load round-trip | The grid store serialises a **positional, unversioned** meta array (`:299-302` writes, `:415-418` reads back by index `meta[0..6]`). Reordering a field silently misinterprets a persisted cache — and every failure path is `except Exception: logger.exception` → "starting fresh" with no signal. Also verified once by a throwaway script per v0.3.3, never committed. |
| `_deaccumulate` (`:155`) | Pins down P1-11. Currently `precipitation` is never asserted at all. |
| `_compute_rh_from_td` (`:160`) | Magnus formula, trivial known-value asserts. `humidity` never asserted. |
| `services/ensemble.py` | 30 lines of pure stats — exactly what unit tests are for. Would have caught P2-7. |
| `_budget_error` / `_MAX_RESPONSE_CELLS` | The OOM guard that took RAM 8–16 GB → 2 GB, untested. |
| `_latest_ref_dt` boundary | Its 2 h guard already caused a production data-loss bug. |

**Fix**: add a `pytest -m "not integration"` job running on every push (seconds, no network); add
`ruff` to the dev group and run `ruff check` in CI (**lint only — do not reformat the tree**); add
the tests above; add `poetry check --lock`.

## P3-3 · ✅ No dependabot / renovate
`.github/` contains **only** `workflows/`. No `dependabot.yml`, no `renovate.json`. This repo's
worst incident was unmanaged dependency drift, and nothing will tell it that `httpx 0.27.2` or
`fastapi 0.115.14` has a CVE. **Fix**: add `.github/dependabot.yml` for `pip` + `github-actions`,
weekly, grouped. **Exclude the eccodes triplet** from automatic bumps — those are deliberate and
must move together with a CI run.

## P3-4 · ✅ Outdated actions, no caching, fragile test
- `actions/checkout@v4` (all workflows) and `setup-python@v5` (`integration-test.yml:18`) run on
  the deprecated Node 20 runtime; current majors are `checkout@v5` / `setup-python@v6` on Node 24.
  Already listed as deferred in `.ai/RESUME.md:203`. Becomes a hard CI break when Node 20 is
  removed. The Docker actions are on current majors.
- **No dependency caching at all** — no `cache: 'poetry'`, no `actions/cache`. Every run
  re-downloads numpy/scipy/eccodeslib wheels.
- **No concurrency group** — two pushes to `main` run two 172 MB tests in parallel against the live
  MeteoSwiss API.
- 🔎 **The test can fail on the weather.** `_latest_ref_dt()` picks the newest published run, so
  input data changes every 6 h, and the `len({round(s,1) for s in speeds}) >= 2` assert
  (`test_e2e_collection.py:117`) assumes vertical wind shear at Interlaken — a meteorological
  assumption, not a code invariant. Consider asserting on structure rather than distinctness, or
  pinning a `ref_dt`.
- 🔎 `docker-publish.yml:58` builds `linux/amd64,linux/arm64` under QEMU emulation. Emulated arm64
  builds of numpy/scipy/eccodes are very slow and there is no evidence arm64 is deployed anywhere.
  **Ask the user** before dropping arm64.

## P3-5 · 🔎 81 % of `icon_ch2_eps.py` is a verbatim copy of `icon_ch1_eps.py` (~450 lines removable)
Measured by the audit (normalised, `ch1`↔`ch2` tokens unified): of 469 code lines in
`icon_ch2_eps.py`, **380 (81 %) exist verbatim in CH1**; of the 89 unique lines, ~24 are imports and
~18 are log strings differing only by prefix — **~30 lines are genuinely CH2-specific**.
`_fetch_stations` is byte-identical (`ch1:767-772` ≡ `ch2:159-164`); `_ensure_grid`, `_fetch_step`
and `collect_grid` differ only in log text and model strings.

The drift this causes is already visible and is the root of several findings above:
`pres_tasks.get(var)` (`ch1:984`) vs `pres_tasks[var]` (`ch2:351`); the "no asset found" warning
present at `ch1:802` and **missing** at `ch2:189-190` (so CH2 asset gaps are invisible); two
different deaccumulation implementations (P1-11).

**Fix**: extract one `IconEpsCollector` parameterised by (collection id, model name, telemetry tag,
horizons, ref-dt guard hours, `accum_prior_h`, constants filename), with per-model module-level grid
state. CH1/CH2 become ~20-line subclasses.

**This is the largest change in the document and the last thing to do.** It must land *after*
P3-1 (test gate) and P3-2 (unit tests) exist, or there is nothing to catch a regression. Do not
combine it with P1-4.

## P3-6 · ✅ `scripts/diag_interlaken.py` is dead — it cannot even import
`scripts/diag_interlaken.py:17` imports `_extract_station` from `icon_ch1_eps`. **That symbol
does not exist anywhere in `src/`** — the only repo-wide hit is the import line itself. The script
raises `ImportError` before running a line. It has been rotting undetected because there is no lint
(P3-2). Also: `sys.path.insert(0, "/app/src")` (`:7`) hardcodes a container path so it never ran
from a checkout, and `verify=False` (`:40`, see P0-2). `.ai/RESUME.md:582` still advertises it as
live tooling. **Fix**: delete it (its value belongs in a pytest fixture per P3-2) and remove the
doc reference. Confirmed clean by contrast: nothing references the deleted `accuracy.py`.

## P3-7 · 🔎 Two deploy scripts, ~85 % duplicated, and one violates a documented rule
`scripts/LSMF-dev.ps1` (304 lines) and `scripts/remote.ps1` (261 lines) are near-identical copies
differing mainly in constants — and dangerously:

| | `LSMF-dev.ps1` | `remote.ps1` |
|---|---|---|
| `$REMOTE_DIR` | `/opt/LSMF` (`:60`) | `~/lsmfapi` (`:43`) |
| project | `lsmfapi-dev` (`:62`) | `lsmfapi` (`:44`) |
| compose | base + `.dev.yml` (`:62`) | **base only** (`:44`) |
| excludes | `.ai`, not `.claude` | `.claude`, not `.ai` |

`remote.ps1:161` runs `docker compose -f docker-compose.yml up --build` with **no dev overlay** —
i.e. no Traefik labels — then prints *"Available at https://lsmfapi.lg4.ch"*, the **production**
hostname. `README.md:266` warns in bold never to use this repo's base compose for PRD precisely
because the container becomes invisible to Traefik. It is documented nowhere and appears to be a
superseded copy. Its `Invoke-Exec` (`:216`) also hardcodes `lsmfapi-lsmfapi-1`.
**Fix**: delete `remote.ps1` (confirm with the user first — it may be someone's muscle memory), or
clearly mark it and fix the overlay. 🔎 Also both scripts' tar excludes use `--exclude=./$_`, which
anchors at the archive root and therefore does **not** match nested `./src/**/*.pyc`.

## P3-8 · 🔎 Assorted efficiency wins in the collectors
Batch these only after P3-5, or they will need doing twice:
- **~11 k STAC searches/day.** One `POST /search` per (variable, horizon): 783 CH1 + 2006 CH2 per
  run × 4 runs each ≈ **11 156/day**. Dropping `forecast:horizon` from the payload returns all
  horizons per variable → ~23 searches/run, a 34–87× reduction. Searches also share the
  `DOWNLOAD_CONCURRENCY = 6` semaphore (`icon_ch1_eps.py:790`), so metadata latency queues behind
  1.84 GB downloads for no reason.
- **numpy→list→numpy round trip per value.** `_to_ensemble_value` (`icon_ch1_eps.py:168-170`) calls
  `compute_stats(arr_1d.tolist())` and `ensemble.py:13` immediately does `np.array(values)`. At
  ~296 k calls per CH2 run this is pure waste. Change `compute_stats` to accept an ndarray.
- **float64 upcast of every surface array.** `_nan_surf` (`icon_ch1_eps.py:966`) is float64 while
  eccodes returns float32 (`:359`), so `np.stack` (`:978`) promotes all 17 surface stacks —
  2× memory. `pres_array` was already fixed for this; `surf_array` was not.
- **Repeated static copy.** `_interp_to_heights` does `z = level_heights.astype(np.float32)`
  (`:257`) on every call — ~39 MB re-materialised 174× per CH2 grid run. Hoist it.
- **Task results never released.** `surf_tasks`/`pres_tasks` (`:925-934`) hold every per-(var,
  horizon) array alive while `np.stack` makes a second full copy (`:978`, `:993`); nothing clears
  the dicts. `u_pl`/`v_pl` are dead after `_to_alt` (`:1041`) but retained through the whole
  station loop.
- **`_GRID_LEVEL_HEIGHTS`** (`:47`) keeps a full-grid float16 array (~182 MB) resident for process
  life per model, though only the ~121 k sample columns and the station columns are ever used —
  ~10× overshoot. `_load_level_heights` (`:281-296`) also peaks at ~370 MB + 182 MB simultaneously.

## P3-9 · 🔎 Silent failure paths that hide data loss
Fold into the items that touch each file:
- `icon_ch1_eps.py:455-468` `_read` returns `None` for a missing file with **no log**;
  `icon_ch2_eps.py:189-190` returns `None` for a missing STAC asset with no log (CH1 warns at
  `:802`).
- `_task_ok` (`ch1:940-946`, `ch2:310-316`) makes an exception indistinguishable from "no data";
  with `return_exceptions=True` (`:938`) no traceback is ever surfaced.
- **Grid failure is invisible to the dashboard**: `ch1:1144-1145` / `ch2:526-527` log `.exception`
  but never call `collection_state`/`telemetry`, so a run with **no grids** reports fully
  successful.
- `_eccodes_get` (`ch1:184-188`) maps `CodesInternalError` to a default — a missing
  `perturbationNumber` silently becomes member 0, collapsing all members into one row
  (`:371-379`).
- `unlink` in a `finally` (`ch1:625`, `:660`) deletes a file even when the parse failed
  transiently.
- `_deaccumulate` (`:157`) uses `prepend=arr[:1,:]*0`; if step 0 is NaN (failed fetch → `_nan_surf`)
  the prepend is NaN and steps 0 *and* 1 both come out NaN. Use `np.zeros_like`.

## P3-10 · ✅ Repo hygiene
- 🔎 `.claude/settings.local.json` is **tracked** (and currently modified). A `*.local.*` file is
  per-developer; `.gitignore` has no `.claude/` entry. Recurring spurious diffs. Note
  `remote.ps1:58` excludes `.claude` from deploys but `LSMF-dev.ps1` does **not** — so the dev
  deploy ships it to the host.
- `.gitignore` is a 212-line unmodified GitHub Python template; only lines 1–3 are
  project-specific. It covers Django, Scrapy, Celery, SageMath, Marimo — none used — while missing
  the three things it needs: `config.yml` (P1-6), `.claude/`, and a `.dockerignore`'s job (P2-2).
  Noise is why the real gaps went unnoticed.
- `pyproject.toml:1-6` still uses legacy `[tool.poetry]` rather than PEP 621 `[project]`. Low
  priority, but relevant to the unpinned-Poetry risk in P2-2.
- 🔎 `Dockerfile` runs `poetry install --only main` **twice** (`:13`, `:17`) and is single-stage, so
  Poetry and its tree ship in the runtime image; `poetry install` also leaves `~/.cache/pypoetry`
  wheels in the layer. Also missing `PYTHONUNBUFFERED=1` / `PYTHONDONTWRITEBYTECODE=1`, notable for
  a service debugged almost entirely through `docker compose logs`. Layer ordering is otherwise
  correct (deps copied before source).
- 🔎 The healthcheck is duplicated in `Dockerfile:21-22` **and** `docker-compose.yml:8-13` — two
  copies to keep in sync; the compose one is redundant. `start_period=10s` with `retries=2` is thin
  given `load_cache()` must JSON-parse 170 MB and `np.load` two npz files before uvicorn serves.

---

# Sequencing

Each group is one deployable batch. **Deploy and observe before starting the next.**

| # | Items | Rationale |
|---|---|---|
| 1 | **P0-1, P0-2** | Security. P0-1 is exploitable today by anyone; P0-2 is a 4-line change gated on one question to the user. |
| 2 | **P0-3, P1-1** | The live 43 s outage and the reason nobody saw it. Small diffs, huge effect. |
| 3 | **P1-2, P1-9** + P1-2's sub-items | Make telemetry and logging actually work. Everything after this is easier to verify because failures become visible. |
| 4 | **P0-4.1, P1-6, P1-7, P1-8** | Config & exposure. Needs user coordination on `config.yml` presence and on how they want the dashboard protected. |
| 5 | **P3-1, P3-2, P3-3, P3-4** | The safety net. **Must precede groups 6–8.** |
| 6 | **P2-1, P2-3, P2-6, P2-8, P2-9, P2-11, P2-12, P3-6, P3-10** | Pure cleanup and docs. No behaviour change; safe to batch. |
| 7 | **P2-2, P1-12, P2-4** | Dockerfile + dependency removal. One CI round-trip each; P1-12 needs host uid coordination. |
| 8 | **P2-5** alone | numpy 2.x. Never batch. |
| 9 | **P0-4.2 + P1-4**, then **P1-5**, then **P1-10, P1-11, P3-8, P3-9** | Collector correctness & efficiency, behind the group-5 net. |
| 10 | **P3-5** | The big refactor. Last, or it invalidates everything above it. |

## Decisions the user must make — do not choose unilaterally

1. **P0-2** — why was `verify=False` added? Removing it blindly may break all collection.
2. **P1-8** — how should the dashboard be protected (Traefik basicauth / IP allowlist / separate
   port)? Also affects the PRD compose in the other repo.
3. **P1-12** — non-root uid needs the host bind-mount chowned; PRD compose is not in this repo.
4. **P2-7** — should `wind_direction_min/max` become a circular arc? Changes values Lenticularis
   consumes.
5. **P2-10** — keep creating unused Recipe tables, or defer until v0.4 starts?
6. **P3-4** — drop the emulated arm64 build?
7. **P3-7** — delete `remote.ps1`?
8. **P0-3** — compression level vs disk size; present the measured numbers.

## Verification harness

No local Linux (`.ai/RESUME.md:207`, memory `env-no-local-linux`), so:
- **Local**: `python -m py_compile` on changed files; the new non-integration unit tests from P3-2
  (pure Python, they do run on Windows); `ruff check`.
- **CI**: every item lands with a green integration test before tagging. Judge a real pass by
  `1 passed in ~250s` — anything under ~60 s failed in setup (`06-testing-conventions.md:66`).
- **PRD acceptance signals** after deploy:
  - P0-1: `?station_id=<b>x</b>` renders as literal text on `/dashboard`
  - P0-3: no ~40 s gap between `collect() complete` and `executed successfully`; requests logged
    inside the window
  - P0-4: `du -sh /mnt/cache/lsmfapi-grib/ch2` stays single-digit GB mid-run
  - P1-1: `/health` shows version + subsystem checks; returns 503 when the scheduler is stopped
  - P1-2: error panel shows grouped counts; a real 500 appears in the panel *and* the logs
  - P2-3: no `*.tmp.npz` in `/app/data`
