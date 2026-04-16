# Forecast Data Reference

This document explains what each variable in the LSMFAPI forecast response means, where it comes from, and how to interpret it — particularly in the context of paragliding and outdoor aviation.

---

## Where the data comes from

LSMFAPI ingests two Swiss high-resolution numerical weather prediction (NWP) models published by MeteoSwiss on their open data portal:

| Model | Resolution | Forecast window | Runs per day | Ensemble members |
|---|---|---|---|---|
| ICON-CH1-EPS | **1.1 km** | 0–30 h | 4 × daily (00Z / 06Z / 12Z / 18Z UTC) | 11 |
| ICON-CH2-EPS | 2.2 km | 30–120 h | 2 × daily (00Z / 12Z UTC) | 21 |

ICON-CH1-EPS is one of the highest-resolution operational ensemble NWP systems in the world. At 1.1 km grid spacing it can resolve valley–ridge wind patterns, local channelling effects, and convective initiation that coarser models (including what OpenMeteo typically provides) smooth away.

The two models are **blended**: hours 0–30 use CH1-EPS, hours 30–120 use CH2-EPS.

---

## What "ensemble" means

Each model run produces N independent forecasts ("members") that start from slightly different initial conditions. Real atmospheric evolution is chaotic — small differences in initial state grow over time. The ensemble captures this uncertainty.

LSMFAPI reduces the N members to three numbers per variable per hour:

| Field | Meaning |
|---|---|
| `probable` | Median across all members — the most likely single value |
| `min` | Absolute minimum across all members — the worst case in one direction |
| `max` | Absolute maximum across all members — the worst case in the other direction |

**Narrow spread** (min ≈ max) means the atmosphere is well-constrained and the forecast is reliable.  
**Wide spread** means the atmosphere is sensitive to initial conditions and the outcome is genuinely uncertain — not a model error, but a physical reality.

For wind: a `probable` of 8 m/s with `min` 2 and `max` 18 means there is real uncertainty. For CAPE: a `probable` of 50 J/kg with `max` 1200 means one member is developing a thunderstorm even if most do not.

---

## Variable-by-variable reference

### Surface winds

**`wind_speed`** — m/s at 10 m above ground  
Standard 10-minute mean wind speed at 10 m. This is the "sustained wind" figure, not a gust.

**`wind_gusts`** — m/s at 10 m above ground  
Maximum gust recorded during the output time step (~1 h). In ICON the variable is `VMAX_10M`. This is the instantaneous peak, not a mean.

**`wind_direction`** — degrees (meteorological convention)  
0° and 360° = wind coming from the North. 90° = from the East. 180° = from the South. Note: direction is where the wind *comes from*, not where it *goes*.

> **Paragliding relevance**: Ensemble spread on wind direction is meaningful — a spread of 20° is normal forecast uncertainty; a spread of 90° means the model does not agree on whether you get valley wind or slope wind.

---

### Temperature and humidity

**`temperature`** — °C at 2 m above ground  
Standard screen-level temperature. Converted from Kelvin (GRIB2 source unit).

**`humidity`** — % relative humidity at 2 m  
Computed by LSMFAPI from specific humidity (`QV`), temperature (`T_2M`), and surface pressure (`PS`) using the Bolton formula. MeteoSwiss does not publish relative humidity directly.

---

### Pressure

**`pressure_qff`** — hPa, sea-level reduced (QFF)  
Station pressure reduced to sea level using the **actual measured temperature** at the station (QFF convention). This is the standard in Swiss and European meteorology. It is **not** QNH (which uses a standard-atmosphere lapse rate). All pressure values in LSMFAPI are QFF — never QNH.

---

### Precipitation

**`precipitation`** — mm/h  
Total precipitation rate (rain + snow liquid-equivalent). De-accumulated from the ICON `TOT_PREC` field, which is a running total from model start. Values below ~0.1 mm/h are trace precipitation.

---

### Radiation

The two radiation variables are averages over the output hour, de-accumulated from ICON's running totals.

**`solar_direct`** — W/m²  
Direct beam shortwave radiation reaching the surface (ICON: `ASWDIR_S`). This is the component that drives shadow formation, panel heating, and — importantly — surface heating and thermal development on south-facing slopes.

**`solar_diffuse`** — W/m²  
Diffuse shortwave radiation (ICON: `ASWDIFD_S`). Scattered radiation that illuminates the sky uniformly. High values with low direct = overcast but bright. Low values of both = fog/thick cloud.

**`sunshine_minutes`** — minutes of sunshine in the hour (0–60)  
De-accumulated from ICON `DURSUN`. 60 = full sunshine. 0 = completely overcast. Values between indicate partial cloud cover.

> **Paragliding relevance**: `solar_direct` drives thermal development on sunny slopes. A sequence of hours with high solar_direct → high BLH → good thermal conditions. Low solar_direct even at midday = thermics suppressed.

---

### Cloud cover

All values 0–100%. Ensemble spread on cloud cover is often high — 20–30% spread is normal.

**`cloud_cover_total`** — %  
Total sky coverage by cloud at any level.

**`cloud_cover_low`** — %  
Low cloud (below ~2000 m, corresponding to pressure levels above ~800 hPa). Stratus, fog, low cumulus. Low cloud = poor visibility, no thermics.

**`cloud_cover_mid`** — %  
Mid-level cloud (roughly 2000–6000 m). Altocumulus, altostratus. Limits solar heating; relevant for overdevelopment assessment.

**`cloud_cover_high`** — %  
High cloud (above ~6000 m). Cirrus. Usually does not affect thermics directly but reduces direct solar radiation.

**`cloud_base_convective`** — m AGL  
Height of the base of convective (cumulonimbus/towering cumulus) clouds. 0 = no convective cloud detected. When non-zero, this is the altitude at which thermals stop rising freely and enter the cloud. In paragliding, thermalling into convective cloud is dangerous — this value, combined with `boundary_layer_height`, tells you where the safe ceiling is.

> High `cloud_base_convective` with high `cape` and low `cin` = classic thunderstorm setup. Be off the hill.

---

### Thermics and convection

These variables are the most novel addition compared to what OpenMeteo provides. They are model-derived diagnostics directly relevant to paragliding.

**`boundary_layer_height`** — m AGL (ICON: `HPBL`)  
The height of the planetary boundary layer — the well-mixed turbulent layer above the surface. This is the **primary thermal ceiling proxy**. Thermals that originate at the surface can rise to approximately this height before mixing stops. In practice, thermal height on a good flying day correlates closely with BLH.

- Morning (pre-heating): 200–400 m AGL — thermics not yet developed
- Good flying afternoon: 1500–2500 m AGL
- Storm conditions: 3000+ m AGL — strong thermals, convective risk

Note: BLH is a model diagnostic, not directly measured. It can underestimate thermal height on days with strong insolation.

**`freezing_level`** — m ASL (ICON: `HZEROCL`)  
Height of the 0°C isotherm above sea level. Relevant for:
- Flight safety at altitude (ice formation)
- Whether precipitation falls as rain or snow
- As a rough indicator of atmospheric moisture — low freezing level = cold, damp, unstable air

In summer over the Swiss Plateau: typically 3500–4500 m ASL. In winter: can be below 1000 m ASL.

**`cape`** — J/kg, Mixed-Layer CAPE (ICON: `CAPE_ML`)  
Convective Available Potential Energy. The amount of energy available for a parcel of air rising from the mixed layer to develop into a thunderstorm. Higher = more explosive convective potential.

| CAPE (J/kg) | Interpretation |
|---|---|
| 0 | Stable — no convective development |
| 1–100 | Marginally unstable — weak convection possible |
| 100–500 | Moderately unstable — showers and isolated storms possible |
| 500–1500 | Significant — thunderstorms likely if trigger present |
| >1500 | Severe — explosive development, large hail, strong wind shear |

In the Alps, CAPE of 300–500 J/kg with an afternoon trigger (ridge heating) is sufficient for rapidly developing thunderstorms. Do not fly.

**`cin`** — J/kg, Mixed-Layer CIN (ICON: `CIN_ML`)  
Convective Inhibition. The energy that must be overcome before convection can begin — a "cap" on the atmosphere. CIN values are negative (energy barrier).

| CIN (J/kg) | Interpretation |
|---|---|
| 0 | No inhibition — convection fires freely |
| -10 to -50 | Weak cap — storms can develop without much forcing |
| -50 to -200 | Moderate cap — storms need strong forcing (heating, cold front) |
| < -200 | Strong cap — storms unlikely unless cap breaks violently |

The combination that matters for paragliding: **high CAPE + weak CIN** = storms can develop quickly and without warning. **High CAPE + strong CIN** = potential for a "cap break" where convection suddenly explodes after being suppressed.

> Watch the evolution through the day: CIN weakens as the surface heats up. When CIN drops to near 0 with CAPE >300, storm initiation is imminent.

---

### Pressure-level winds (9 altitude bands)

Wind data at 9 fixed altitude levels from 500 m to 5000 m ASL, derived from ICON pressure-level fields (850/800/750/700/600/500 hPa mapped to approximate altitudes over Switzerland).

**`wind_speed`** — m/s  
Horizontal wind speed at altitude, computed from U and V components.

**`wind_direction`** — degrees (meteorological convention, same as surface)

**`vertical_wind`** — m/s (ICON: `W` at pressure levels)  
This is the **modelled vertical wind velocity** at the altitude. This is a direct model output, not a derived diagnostic.

- Positive = updraft (air rising)
- Negative = downdraft / sink

> **How to use this for paragliding**: Vertical wind at pressure levels is a direct indicator of where the model sees organised vertical motion — thermals, convergence lines, orographic lift, and sink. A column of +0.5 to +2 m/s across multiple altitude levels indicates a well-developed thermal. A column of -1 to -3 m/s indicates strong sink (rotor downwind of ridge, subsidence).

Note: ICON output is on a 1.1 km grid for CH1-EPS. Vertical wind is inherently noisy at this resolution — individual values are less meaningful than the ensemble spread and spatial patterns. Use the `probable` value as directional guidance, not a precise measurement.

---

## Altitude level to pressure mapping

| Altitude (m ASL) | Approx. pressure (hPa) |
|---|---|
| 500 | 950 |
| 800 | 920 |
| 1000 | 900 |
| 1500 | 850 |
| 2000 | 800 |
| 2500 | 750 |
| 3000 | 700 |
| 4000 | 600 |
| 5000 | 500 |

These are approximate. The actual geometric altitude of a pressure level varies with temperature. Over a warm summer Alps, 850 hPa is typically closer to 1600 m ASL; in winter it drops to ~1300 m ASL.

---

## Wind grid

The `GET /api/forecast/wind-grid` endpoint returns 171 fixed grid points covering Switzerland at a chosen altitude level and date. Each point includes:

- `ws` / `ws_min` / `ws_max` — wind speed per hour
- `wd` / `wd_min` / `wd_max` — wind direction per hour
- `wv` / `wv_min` / `wv_max` — vertical wind per hour (positive = updraft)

This is derived from the CH1-EPS 1.1 km grid (for dates within the 30h window) and CH2-EPS for longer ranges. The 171-point geometry matches the existing Lenticularis grid.

---

## Typical paragliding forecast workflow

A useful reading order for a go/no-go decision:

1. **`cloud_cover_total`** — overcast? No thermics, possibly no visibility.
2. **`cape`** — >300 J/kg in the afternoon? Storm risk. Check max across ensemble.
3. **`cin`** — weakening through the day with high CAPE? Explosive convection risk.
4. **`boundary_layer_height`** — thermal ceiling. Also compare with `cloud_base_convective`.
5. **`solar_direct`** + **`sunshine_minutes`** — is the sun actually reaching the surface to drive thermics?
6. **`wind_speed`** at surface and key altitude levels — any wind shear (different speed/direction at different levels = turbulence)?
7. **`vertical_wind`** at 1500–3000 m — organised lift/sink patterns.
8. **`freezing_level`** — if below planned max altitude, check precipitation type.

The ensemble spread on each of these is as important as the probable value. A probable of "safe" with a max of "dangerous" is not a safe day.
