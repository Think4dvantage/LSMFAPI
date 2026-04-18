# Prompt: Add a New SQLite Table

Use this prompt when you need to persist new structured data.

---

Add a new SQLite table for `{entity}` following the project conventions:

1. **Define the ORM model** in `src/[package]/database/models.py`:
   ```python
   class Entity(Base):
       __tablename__ = "entities"
       id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
       name = Column(String, nullable=False)
       created_at = Column(DateTime, default=datetime.utcnow)
   ```

2. **Create a migration script** in `src/[package]/database/migrations/`:
   Use the next sequential prefix (e.g., `0002_add_entities.sql`).
   ```sql
   CREATE TABLE IF NOT EXISTS entities (
       id TEXT PRIMARY KEY,
       name TEXT NOT NULL,
       created_at DATETIME DEFAULT CURRENT_TIMESTAMP
   );
   ```
   If adding a **column to an existing table**, use an `ALTER TABLE` statement instead:
   ```sql
   ALTER TABLE entities ADD COLUMN new_col TEXT;
   ```
   Always ensure migration SQL is as idempotent as possible (`IF NOT EXISTS`).

3. **Add the Pydantic schemas** (Create / Update / Out) in `src/[package]/models/`.

4. **Add CRUD endpoints** in the appropriate router (or create a new one — see `add-api-router.md`).

5. **Update `.ai/context/architecture.md`** to document the new table or column in the "SQLite Tables" section.

6. **Sync**: Run `.ai/prompts/sync.md` to ensure human-readable docs are updated.
