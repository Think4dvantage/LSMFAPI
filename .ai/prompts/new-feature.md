# Prompt: Implement a New Feature

Use this prompt as a checklist when implementing any non-trivial feature end-to-end.

---

## Backend Checklist

- [ ] New SQLite table(s) → add ORM model in `models.py`, migration in `db.py:_run_column_migrations()` if adding columns to existing tables
- [ ] New Pydantic schemas in `src/lsmfapi/models/`
- [ ] New router at `src/lsmfapi/api/routers/{domain}.py` → register in `main.py`
- [ ] New forecast cache interactions → use getter/setter functions in `database/cache.py`
- [ ] Config keys for anything configurable → add to `config.py` Pydantic models AND `config.yml.example`
- [ ] Scheduler job if periodic collection is needed → add to `scheduler.py`

## Frontend Checklist

- [ ] New `.html` page file in `static/`
- [ ] One `<script type="module">` block per page
- [ ] Dark theme colors used: `#0f1117` body, `#1a1f2e` cards, `#2d3748` borders, `#e2e8f0` text, `#90cdf4` accent
- [ ] `shared.css` linked in `<head>`
- [ ] Mobile-responsive (test at ≤640 px width)
- [ ] Console logging added to every new/modified function (see frontend conventions)

## Quality

- [ ] No hardcoded config values — all through `get_config()`
- [ ] No print statements — use `logging`
- [ ] Type hints on all function signatures
- [ ] No npm / build step introduced

Refer to `.ai/context/architecture.md` for data models and API contracts.
Refer to `.ai/context/features.md` for backlog context on what's planned.
