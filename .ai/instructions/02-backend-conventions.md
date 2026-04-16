# Backend Conventions

## New API Router

Create `src/lsmfapi/api/routers/<domain>.py`, register it in `main.py`.

```python
# src/lsmfapi/api/routers/forecast.py
router = APIRouter(prefix="/api/forecast", tags=["forecast"])

@router.get("/station")
async def station_forecast(
    lat: float, lon: float, elevation: int, hours: int = 120,
):
    ...
```

```python
# main.py
from lsmfapi.api.routers import forecast as forecast_router
app.include_router(forecast_router.router)
```

Add a page route in the same router file if a new HTML page is needed:

```python
@router.get("/accuracy-page", include_in_schema=False)
async def accuracy_page():
    return FileResponse("static/index.html")
```

---

## New SQLite Table

Add ORM model in `models.py`:

```python
class Recipe(Base):
    __tablename__ = "recipes"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ...
```

New tables are created automatically by `Base.metadata.create_all()` in `db.py`. No migration needed.

For **new columns on existing tables**, add to `_run_column_migrations()` in `db.py`:

```python
if "new_col" not in cols:
    conn.execute(text("ALTER TABLE existing_table ADD COLUMN new_col TEXT"))
    conn.commit()
```

**Always make migrations idempotent** — check `PRAGMA table_info` first. Never skip `_run_column_migrations` when adding columns; SQLAlchemy's `create_all` does not alter existing tables.

---

## Forecast Cache

All forecast reads and writes go through `database/cache.py`. Never access `_station_cache` or `_grid_cache` directly from routers or collectors — always use the module-level getter/setter functions:

```python
from lsmfapi.database.cache import get_station_forecast, set_station_forecast

# In a collector, after computing:
set_station_forecast("46.68_7.86_580", forecast_response)

# In a router:
data = get_station_forecast("46.68_7.86_580")
if data is None:
    raise HTTPException(status_code=503, detail="Forecast not yet available")
```

This keeps the backing store swappable without touching router or collector code.

---

## GRIB2 Parsing (Collectors)

Use `cfgrib` + `xarray` to open GRIB2 files. Each collector downloads a GRIB2 file via `httpx` (async), saves it to a temp path, then opens it with xarray:

```python
import xarray as xr
import cfgrib

ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={"filter_by_keys": {"shortName": "10u"}})
```

Use `scipy.spatial.KDTree` for nearest-grid-point lookup:

```python
from scipy.spatial import KDTree
tree = KDTree(list(zip(lats.ravel(), lons.ravel())))
dist, idx = tree.query([target_lat, target_lon])
```

Keep KD-trees as module-level singletons (built once per collector run, not per query).

---

## Ensemble Statistics

All ensemble computation lives in `services/ensemble.py`. It receives a list of scalar values (all members × all runs for a given variable + valid_time) and returns `{probable: median, min: min, max: max}`.

Wind direction uses circular statistics — do not use plain median/min/max for angles:

```python
import numpy as np

def circular_median(angles_deg: list[float]) -> float:
    rad = np.deg2rad(angles_deg)
    return float(np.rad2deg(np.arctan2(np.nanmedian(np.sin(rad)), np.nanmedian(np.cos(rad)))) % 360)
```

---

## Config

Add new keys to `config.py` Pydantic models **and** to `config.yml.example`. Never read `os.environ` directly — always go through `get_config()`.

Key config sections:
- `meteoswiss.ch1eps_url` — base URL for ICON-CH1-EPS GRIB2 downloads
- `meteoswiss.ch2eps_url` — base URL for ICON-CH2-EPS GRIB2 downloads
- `lenticularis.base_url` — Lenticularis API base URL (station list + accuracy GUI)
- `scheduler.*` — Job intervals and jitter

---

## Scheduler Jobs

Add to `CollectorScheduler` in `scheduler.py`. Use `AsyncIOScheduler` + `IntervalTrigger`. Track health in `_collector_health` dict.

| Job | Trigger | Notes |
|---|---|---|
| `collect_ch1eps` | Every 3h (jitter ±10 min) | Downloads + parses latest CH1-EPS run, writes to InfluxDB |
| `collect_ch2eps` | Every 6h | Downloads + parses latest CH2-EPS run (30h–120h slice) |
| `purge_old_forecasts` | Daily 01:00 | Deletes InfluxDB records older than 7 days |

---

## Coding Standards

- **Always use type hints** on function signatures and class attributes.
- **Async/await** for all I/O — HTTP calls, DB writes, InfluxDB queries.
- **Pydantic v2** for all data schemas and config validation.
- **SQLAlchemy 2.0 style** — use `select()`, not legacy `query()`.
- **One router per domain** — never put all routes in `main.py`.
- **Abstract base classes** (ABC + `@abstractmethod`) for collectors.
- **Log** all collection events and errors with the standard `logging` module.
- **No print statements** in production code.
