# Testing Conventions

## Philosophy

Testing is mandatory for any project built with this blueprint. It ensures that the AI-assisted development process remains reliable and that new changes don't introduce regressions.

---

## Backend: Pytest

Use `pytest` and `pytest-asyncio` for all backend tests.

### Location
All backend tests live in `tests/backend/`.

### Standards
- **Naming**: `test_*.py`
- **Async**: Use `async def` and `@pytest.mark.asyncio` for any code that uses `await`.
- **API Testing**: Use `httpx.AsyncClient` with the FastAPI `app` object.
- **Database**: Use an in-memory SQLite database (`sqlite:///:memory:`) for fast, isolated tests.

### Example API Test

```python
import pytest
from httpx import AsyncClient
from [package].api.main import app

@pytest.mark.asyncio
async def test_get_users():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.get("/api/auth/users")
    assert response.status_code == 200
```

---

## Frontend: Playwright

Because this project uses a "no-build" frontend (Vanilla JS), we use `Playwright` for End-to-End (E2E) testing. This is the most reliable way to test that the UI behaves correctly in real browsers.

### Location
All frontend tests live in `tests/frontend/`.

### Standards
- **Naming**: `test_*.py` (using Playwright's Python library).
- **Setup**: Playwright should point to the dev instance or a local test server.
- **Interactions**: Use standard Playwright selectors (e.g., `page.get_by_text()`, `page.get_by_role()`).
- **i18n**: Test with different locale settings to ensure translations load correctly.

### Example E2E Test

```python
import pytest
from playwright.sync_api import Page, expect

def test_login_page_renders(page: Page):
    page.goto("http://localhost:8000/login")
    expect(page.get_by_text("Email Address")).to_be_visible()
    expect(page.locator("button[type='submit']")).to_be_visible()
```

---

## CI / Automation

- All tests should run automatically on every Pull Request via GitHub Actions.
- Ensure the test suite is "green" before merging any new feature or fix.
- **Coverage**: Aim for 80%+ coverage, but prioritize testing critical paths (Auth, Data Collection, API Contracts).
