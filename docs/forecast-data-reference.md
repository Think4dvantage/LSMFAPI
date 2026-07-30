# Forecast Data Reference

This document explains what each variable in the LSMFAPI forecast responses means, where it
comes from, and how to interpret it — particularly in the context of paragliding and outdoor
aviation.

---

## Where the data comes from

LSMFAPI ingests two Swiss high-resolution numerical weather prediction (NWP) models published
by MeteoSwiss on their open data portal:

| Model | Resolution | Forecast window | Runs per day | Ensemble members |
|---|---|---|---|---|
| ICON-CH1-EPS | **1.1 km** | h0–h33 (hourly) | 4 × daily (02Z/08Z/14Z/20Z, 2 h after each 00/06/12/18Z release) | ~10 (read dynamically from GRIB, never hardcoded) |
| ICON-CH2-EPS | 2.2 km | h34–h120 (hourly) | 4 × daily (03Z/09Z/15Z/21Z, 3 h after each release) | ~21 (read dynamically) |

ICON-CH1-EPS is one of the highest-resolution operational ensemble NWP systems in the world.
At 1.1 km grid spacing it can resolve valley–ridge wind patterns, local channelling effects,
and convective initiation that coarser models (including what OpenMeteo typically provides)
smooth away.

The two models are **blended**: hours 0–33 use CH1-EPS, hours 34–120 use CH2-EPS. CH1 always
wins the write for its slice — a CH2 re-run never touches the CH1 hourly head.

---

## What "ensemble" means

Each model run produces N independent forecasts ("members") that start from slightly different
initial conditions. Real atmospheric evolution is chaotic — small differences in initial state
grow over time. The ensemble captures this uncertainty.

LSMFAPI reduces the N members to three numbers per variable per hour:

| Field | Meaning |
|---|---|
| `probable` | Median across all members — the most likely single value |
| `min` | Absolute minimum across all members — the worst case in one direction |
| `max` | Absolute maximum across all members — the worst case in the other direction |

(Wind *direction* uses circular statistics instead of plain min/max/median — see below.)

**Narrow spread** (min ≈ max) means the atmosphere is well-constrained and the forecast is
reliable. **Wide spread** means the atmosphere is sensitive to initial conditions and the
outcome is genuinely uncertain — not a model error, but a physical reality.

For wind: a `probable` of 15 km/h with `min` 5 and `max` 45 means there is real uncertainty.

---

## `GET /api/forecast/station` — surface variables

Hourly, per station, h0–h120. **All wind fields are km/h**, not m/s.

**`wind_speed`** — km/h at 10 m above ground
Standard 10-minute mean wind speed at 10 m (ICON: `U_10M`/`V_10M` combined). The "sustained
wind" figure, not a gust.

**`wind_gust`** — km/h at 10 m above ground
Maximum gust recorded during the output time step (ICON: `VMAX_10M`). Instantaneous peak, not
a mean.

**`wind_direction`** — degrees (meteorological convention)
0°/360° = wind coming from the North. 90° = from the East. 180° = from the South. Direction is
where the wind *comes from*, not where it *goes*. `min`/`max` use circular statistics (offset
from the circular median, wrapped to [-180°, 180°)) so members straddling 0°/360° report their
true spread instead of a false ~360° one.

> **Paragliding relevance**: a direction spread of 20° is normal forecast uncertainty; a spread
> of 90° means the model does not agree on whether you get valley wind or slope wind.

**`temperature`** — °C at 2 m above ground
Standard screen-level temperature, converted from Kelvin (ICON: `T_2M`).

**`humidity`** — % relative humidity at 2 m
Computed by LSMFAPI from dew point (`TD_2M`) and temperature (`T_2M`) via the Magnus formula —
**not** from specific humidity (`QV`), which MeteoSwiss does not need to publish for this and
which would be a much larger 3D download.

**`pressure_qff`** — hPa, sea-level reduced (QFF)
Station pressure reduced to sea level using the actual measured temperature (QFF convention),
matching Swiss/European meteorological practice and COSMO/ICON heritage. **Never QNH** (which
uses a standard-atmosphere lapse rate) — all pressure values in LSMFAPI are QFF.

**`precipitation`** — mm/h
Total precipitation rate (rain + snow liquid-equivalent), de-accumulated from ICON's running
`TOT_PREC` total. Values below ~0.1 mm/h are trace precipitation.

---

## `GET /api/forecast/altitude-winds` — 9 fixed geometric heights

Wind data at nine fixed heights **above mean sea level**: 500, 800, 1000, 1500, 2000, 2500,
3000, 4000, 5000 m.

**How the heights are derived**: the ICON `U`/`V`/`W` files are ~80 native model levels with
**no pressure coordinate** (`typeOfLevel=generalVerticalLayer`, no `pv`) — pressure is not
recoverable from them. Geometric height instead comes from the static **HHL** (height of
half-levels) field published alongside each collection; LSMFAPI linearly interpolates each
member's wind onto the nine target heights per grid point. A band below a point's terrain
resolves to `null`.

> Earlier versions of this service (pre-v0.3.6) mapped altitude bands to an *approximate*
> pressure level and then to the nearest raw model-level number. Because the files carry no
> `pv`, that mapping silently collapsed onto a single level and every altitude band returned
> identical (or null) values. There is no altitude↔pressure table any more — heights are exact
> geometric heights, not an approximation.

**`wind_speed`** / **`wind_direction`** — km/h / degrees, same convention as the surface fields.

**`vertical_wind`** — m/s (ICON: `W`, interpolated to height)
Direct model output — positive = updraft, negative = downdraft/sink.

> **How to use this for paragliding**: a column of +0.5 to +2 m/s across multiple altitude
> levels indicates a well-developed thermal; a column of -1 to -3 m/s indicates strong sink
> (rotor downwind of ridge, subsidence). ICON output is on a 1.1–2.2 km grid — vertical wind is
> inherently noisy at this resolution; treat the `probable` value as directional guidance, not
> a precise measurement.

---

## `GET /api/forecast/thermal-grid` — convection/thermal fields (spatial grid, not per-station)

Unlike the two endpoints above, this is a **~1 km regular lat/lon grid over Switzerland**, not
a per-station series — query with `bbox`/`stride_km`. Each field is an ensemble
median/min/max, one frame per forecast hour.

| Field | Unit | ICON source | Notes |
|---|---|---|---|
| `solar` | W/m² | `ASWDIR_S` (direct) | De-accumulated mean over the hour |
| `sunshine` | min/h | `DURSUN` | De-accumulated; 60 = full sun, 0 = fully overcast |
| `cloud_cover` / `cloud_low` / `cloud_mid` / `cloud_high` | % | `CLCT`/`CLCL`/`CLCM`/`CLCH` | Total/low/mid/high sky coverage |
| `freezing_level` | m ASL | `HZEROCL` | Height of the 0 °C isotherm |
| `cape` | J/kg | `CAPE_ML` | Mixed-layer Convective Available Potential Energy |
| `cin` | J/kg | `CIN_ML` | Mixed-layer Convective Inhibition (ICON fill −999.9 → `null`) |
| `lcl` | m | `LCL_ML` | Lifted Condensation Level — cloud-base proxy |
| `lfc` | m | `LFC_ML` | Level of Free Convection |
| `tke` | J/kg | `TKE` | Boundary-layer turbulence measure |

**Not available**: `boundary_layer_height` (`HPBL`) and `cloud_base_convective` (`HBAS_CON`) are
**not published** in the ICON-CH1/CH2-EPS catalog and are not in this API — `LCL_ML` is the
closest available thermal-ceiling proxy.

**`cape` interpretation**

| CAPE (J/kg) | Interpretation |
|---|---|
| 0 | Stable — no convective development |
| 1–100 | Marginally unstable — weak convection possible |
| 100–500 | Moderately unstable — showers and isolated storms possible |
| 500–1500 | Significant — thunderstorms likely if trigger present |
| >1500 | Severe — explosive development, large hail, strong wind shear |

**`cin` interpretation** (values are negative — an energy barrier)

| CIN (J/kg) | Interpretation |
|---|---|
| 0 | No inhibition — convection fires freely |
| -10 to -50 | Weak cap — storms can develop without much forcing |
| -50 to -200 | Moderate cap — storms need strong forcing (heating, cold front) |
| < -200 | Strong cap — storms unlikely unless cap breaks violently |

The combination that matters for paragliding: **high CAPE + weak CIN** = storms can develop
quickly and without warning. **High CAPE + strong CIN** = potential for a sudden "cap break".
Watch the evolution through the day — CIN weakens as the surface heats; when it drops to near
0 with CAPE >300, storm initiation is imminent.

---

## `GET /api/forecast/wind-grid` — wind at one altitude, spatial grid

Same ~1 km grid as `/thermal-grid`, one altitude level per request (one of the 9 altitude-winds
bands except 800 m, which is omitted from the grid). Each frame carries `ws` (km/h), `wd`
(degrees), and `rh` (% surface relative humidity) — **no vertical-wind field at the grid
level**; vertical wind is only available per-station via `/altitude-winds`.

---

## Typical paragliding forecast workflow

A useful reading order for a go/no-go decision, pulling from all three endpoints:

1. **`/thermal-grid` `cloud_cover`** — overcast? No thermics, possibly no visibility.
2. **`/thermal-grid` `cape`** — >300 J/kg in the afternoon? Storm risk. Check the ensemble max.
3. **`/thermal-grid` `cin`** — weakening through the day with high CAPE? Explosive convection risk.
4. **`/thermal-grid` `lcl`** — thermal-ceiling proxy (no direct BLH is published).
5. **`/thermal-grid` `solar`** + **`sunshine`** — is the sun actually reaching the surface?
6. **`/station` `wind_speed`**/`wind_direction` at the surface, and **`/altitude-winds`** at key
   heights — any shear (different speed/direction at different levels = turbulence)?
7. **`/altitude-winds` `vertical_wind`** at 1500–3000 m — organised lift/sink patterns.
8. **`/thermal-grid` `freezing_level`** — if below planned max altitude, check precipitation type.

The ensemble spread on each of these is as important as the probable value. A probable of
"safe" with a max of "dangerous" is not a safe day.
