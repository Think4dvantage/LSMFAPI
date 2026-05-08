# Backend Conventions

## New API Router

Create `src/lsmfapi/api/routers/<domain>.py`, register it in `main.py`.

```python
# src/lsmfapi/api/routers/widgets.py
router = APIRouter(prefix="/api/widgets", tags=["widgets"])

@router.get("")
async def list_widgets():
    ...
```

```python
# main.py
from lsmfapi.api.routers import widgets
app.include_router(widgets.router)
```

Add a static HTML page route in the same router file if a new GUI page is needed:

```python
@router.get("/widgets-page", include_in_schema=False)
async def widgets_page():
    return FileResponse("static/widgets.html")
```

---

## New SQLite Table & Migrations

No Alembic. New columns use raw `ALTER TABLE` inside `_run_column_migrations()` in `db.py`, checked against `PRAGMA table_info` for idempotency. New tables use `CREATE TABLE IF NOT EXISTS` in `init_db()`.

See `04-constraints.md` — Database Migrations section.

---

## Scheduler Jobs

Add to `CollectorScheduler` in `scheduler.py`. Use `AsyncIOScheduler` + `CronTrigger`. Always protect collection functions with the per-model asyncio lock pattern:

```python
_mymodel_lock = asyncio.Lock()

async def _run_mymodel() -> None:
    if _mymodel_lock.locked():
        logger.info("mymodel collection already in progress — skipping trigger")
        return
    async with _mymodel_lock:
        cs.mark_started("mymodel")
        ...
```

---

## Config

Add new keys to Pydantic models in `config.py` **and** to `config.yml.example`. Never read `os.environ` — always use `get_config()`.

---

## Testing

Integration tests live in `tests/` with `@pytest.mark.integration`. Run with `pytest -m integration -v`. The CI workflow (`.github/workflows/integration-test.yml`) uses Miniforge + conda-forge eccodes 2.38 because the system apt package (2.34.1 on Ubuntu 24.04) is incompatible with `eccodes-cosmo-resources-python==2.38.x`.

---

## Coding Standards

- **Always use type hints** on function signatures and class attributes.
- **Async/await** for all I/O — HTTP calls, DB operations, downloads.
- **Pydantic v2** for all data schemas and config validation.
- **SQLAlchemy 2.0 style** — use `select()`, not legacy `query()`.
- **One router per domain** — never put all routes in `main.py`.
- **Abstract base classes** (ABC + `@abstractmethod`) for collectors.
- **Log extensively** — startup sequence, every job run, every config value loaded. See `08-operability.md`.
- **No print statements** — always use the `logging` module.
