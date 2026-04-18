# Backend Conventions

## New API Router

Create `src/[package]/api/routers/<domain>.py`, register it in `main.py`.

```python
# src/[package]/api/routers/widgets.py
router = APIRouter(prefix="/api/widgets", tags=["widgets"])

@router.get("")
def list_widgets(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ...
```

```python
# main.py
from [package].api.routers import widgets as widgets_router
app.include_router(widgets_router.router)
```

Add a page route in the same router file if a new HTML page is needed:

```python
@router.get("/widgets-page", include_in_schema=False)
async def widgets_page():
    return FileResponse("static/widgets.html")
```

---

## New SQLite Table & Migrations

### 1. Define ORM Model
Add the ORM model in `models.py`:

```python
class Widget(Base):
    __tablename__ = "widgets"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ...
```

### 2. Create Migration Script
Create a new `.sql` file in `src/[package]/database/migrations/` using a sequential prefix:

`0001_initial_schema.sql` → `0002_add_widgets.sql` → `0003_add_user_bio.sql`

Example migration:
```sql
CREATE TABLE IF NOT EXISTS widgets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### 3. Implementation Logic (db.py)
The `init_db()` function in `db.py` must track and apply these scripts using a `_migrations` table. Never skip migrations — SQLAlchemy's `Base.metadata.create_all()` is only for initial bootstrap and does not handle schema drift.

#### SQLite WAL Mode
To support concurrent writes from collectors and reads from the API, always enable **Write-Ahead Logging (WAL)** mode on the SQLite connection:

```python
def init_db(engine):
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL;"))
        run_migrations(conn)
```

```python
def run_migrations(conn):
    conn.execute(text("CREATE TABLE IF NOT EXISTS _migrations (filename TEXT PRIMARY KEY)"))
    applied = {row[0] for row in conn.execute(text("SELECT filename FROM _migrations"))}
    
    migration_dir = Path(__file__).parent / "migrations"
    for sql_file in sorted(migration_dir.glob("*.sql")):
        if sql_file.name not in applied:
            with open(sql_file) as f:
                conn.execute(text(f.read()))
            conn.execute(text("INSERT INTO _migrations (filename) VALUES (:f)"), {"f": sql_file.name})
    conn.commit()
```

---

## Testing Conventions

See `06-testing-conventions.md` for the full strategy.
- **Backend**: Pytest in `tests/backend/`. Use `httpx.AsyncClient`.
- **Frontend**: Playwright in `tests/frontend/`.

---

## New InfluxDB Query

Add a method to `InfluxClient` in `influx.py`. Keep Flux query strings inside the method. Return plain Python dicts/lists (no ORM objects).

---

## Auth Dependencies

Import from `[package].api.dependencies`:

| Dependency | Who passes |
|---|---|
| `get_current_user` | Any logged-in user |
| `require_admin` | `admin` only |

[Add additional role-based dependencies here as they are introduced.]

---

## Config

Add new keys to `config.py` Pydantic models **and** to `config.yml.example`. Never read `os.environ` directly — always go through `get_config()`.

---

## Scheduler Jobs

Add to `CollectorScheduler` in `scheduler.py`. Use `AsyncIOScheduler` + `IntervalTrigger`. Track health in `_collector_health` dict.

---

## Coding Standards

- **Always use type hints** on function signatures and class attributes.
- **Async/await** for all I/O — HTTP calls, DB writes, InfluxDB queries.
- **Pydantic v2** for all data schemas and config validation.
- **SQLAlchemy 2.0 style** — use `select()`, not legacy `query()`.
- **One router per domain** — never put all routes in `main.py`.
- **Abstract base classes** (ABC + `@abstractmethod`) for collectors.
- **Log extensively** — startup sequence, every request, every job run, every config value loaded. See `08-operability.md` for the full doctrine.
- **No print statements** in production code — always use the `logging` module.
