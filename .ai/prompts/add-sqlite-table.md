# Prompt: Add a New SQLite Table

Use this prompt when you need to persist new structured data.

---

Add a new SQLite table for `{entity}` following the project conventions:

1. Add the ORM model to `src/[package]/database/models.py`:
   ```python
   class Entity(Base):
       __tablename__ = "entities"
       id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
       name = Column(String, nullable=False)
       created_at = Column(DateTime, default=datetime.utcnow)
   ```
   `Base.metadata.create_all()` in `db.py` will create the table automatically on next startup.

2. If adding a **column to an existing table**, add it to `_run_column_migrations()` in `db.py` instead:
   ```python
   cols = {row[1] for row in conn.execute(text("PRAGMA table_info(entities)"))}
   if "new_col" not in cols:
       conn.execute(text("ALTER TABLE entities ADD COLUMN new_col TEXT"))
       conn.commit()
   ```
   Always check `PRAGMA table_info` first — migrations must be idempotent.

3. Add the corresponding Pydantic schemas (Create / Update / Out) in `src/[package]/models/`.

4. Add CRUD endpoints in the appropriate router (or create a new one — see `add-api-router.md`).

Refer to `.ai/context/architecture.md` to document the new table.
