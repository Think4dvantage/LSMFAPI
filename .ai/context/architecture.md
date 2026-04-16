# Architecture Reference

## In-Memory Forecast Cache

All computed forecast data is held in a Python in-process dict. There is no time-series database.

### Station cache

```python
# database/cache.py
_station_cache: dict[str, ForecastResponse] = {}
# key: "{lat}_{lon}_{elev}"  e.g. "46.68_7.86_580"
```

- Populated after every collection run (CH1-EPS every 3 h, CH2-EPS every 6 h)
- Also populated once at container startup (immediate collection run triggered in lifespan)
- A cache entry is a fully computed `ForecastResponse` — API calls are pure dict lookups, no on-the-fly computation
- Station list is fetched from the Lenticularis API on startup and refreshed before each collection run

### Wind grid cache

```python
_grid_cache: dict[str, GridResponse] = {}
# key: "{YYYY-MM-DD}_{level_m}"  e.g. "2024-06-01_2000"
```

- Same lifecycle as station cache
- 171 grid points × 9 altitude levels

### Cache module interface

```python
# database/cache.py
def get_station_forecast(station_key: str) -> ForecastResponse | None: ...
def set_station_forecast(station_key: str, data: ForecastResponse) -> None: ...
def get_grid_forecast(date: str, level_m: int) -> GridResponse | None: ...
def set_grid_forecast(date: str, level_m: int, data: GridResponse) -> None: ...
def cache_stats() -> dict: ...   # keys count, last_populated_at — for health/debug
```

All reads and writes go through these functions so the backing store can be swapped (e.g. to Redis) without touching router code.

---

## SQLite Tables

| Table | Key columns |
|---|---|
| `users` | `id`, `username`, `email`, `hashed_password`, `role`, `created_at` |
| `recipes` | `id`, `name`, `station_id` (nullable — NULL = global), `description`, `active`, `created_at` |
| `recipe_rules` | `id`, `recipe_id` (FK → recipes), `variable`, `correction_type` (additive\|multiplicative), `value`, `condition_json` |

SQLite is used exclusively for relational data (users, recipes). Forecast data is never written here.

[Document every table here as it is added. This is the source of truth for the data model.]

---

## Altitude Level Mapping

| Altitude (m ASL) | Pressure (hPa) |
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

---

## API Contracts

### Auth
- `POST /auth/register` — `{username, email, password}` → `{user_id, token}`
- `POST /auth/login` — `{username, password}` → `{access_token, refresh_token}`
- `POST /auth/refresh` — `{refresh_token}` → `{access_token}`

### Forecast
- `GET /api/forecast/station` — `?lat=&lon=&elevation=&hours=` → hourly ForecastResponse (probable + min + max per variable, 120h max); served from in-memory cache
- `GET /api/forecast/wind-grid` — `?date=YYYY-MM-DD&level_m=` → GridResponse (171 grid points, frames with ws/ws_min/ws_max/wd arrays); served from in-memory cache

### Recipes (v0.2)
- `GET /api/recipes` → list of recipes
- `POST /api/recipes` → create recipe
- `PUT /api/recipes/{id}` → update recipe
- `DELETE /api/recipes/{id}` → delete recipe

[Add all routes here as they are implemented, grouped by router domain.]

---

## Deployment

### Traefik Label Format

This homelab requires **list format** labels, not map format:

```yaml
# CORRECT
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.lsmfapi.rule=Host(`lsmfapi.lg4.ch`)"
```

When a container is on multiple Docker networks, add `traefik.docker.network=proxy`.

### Healthcheck

`python:3.11-slim` does not include `curl`. Use Python stdlib:

```yaml
healthcheck:
  test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/')\""]
```

### Dev Overlay

`docker-compose.dev.yml` extends the base with:
- Live `src/` and `static/` volume mounts (`:ro,z`)
- `proxy` external network + Traefik labels for `lsmfapi-dev.lg4.ch`
- `PYTHONPYCACHEPREFIX=/tmp/pycache` — prevents stale `.pyc` files from shadowing volume-mounted sources
