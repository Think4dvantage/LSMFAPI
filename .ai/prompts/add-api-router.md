# Prompt: Add a New API Router

Use this prompt when you need to add a new domain to the API.

---

Add a new FastAPI router for `{domain}` following the project conventions:

1. Create `src/[package]/api/routers/{domain}.py` with:
   - `router = APIRouter(prefix="/api/{domain}", tags=["{domain}"])`
   - All endpoints using appropriate auth dependencies (`get_current_user`, `require_admin`, etc.)
   - `db: Session = Depends(get_db)` on any endpoint that touches SQLite
   - Type hints on all function signatures
   - Async handlers for all I/O

2. Register the router in `src/[package]/api/main.py`:
   ```python
   from [package].api.routers import {domain} as {domain}_router
   app.include_router({domain}_router.router)
   ```

3. If a new HTML page is needed, add a page route in the same router file:
   ```python
   @router.get("/{page_name}", include_in_schema=False)
   async def {page_name}_page():
       return FileResponse("static/{page_name}.html")
   ```

4. Add any new Pydantic schemas to `src/[package]/models/`.

5. If new user-visible strings are needed, add i18n keys to all locale files simultaneously.

Refer to `.ai/instructions/02-backend-conventions.md` for auth dependency selection.
Refer to `.ai/context/architecture.md` for the full API contract list.
