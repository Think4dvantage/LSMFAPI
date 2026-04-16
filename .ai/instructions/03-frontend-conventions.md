# Frontend Conventions

## No Build Step

Changes to `static/` are live immediately in dev (volume-mounted). **Never introduce npm, webpack, vite, or any bundler.** The frontend is intentionally dependency-free.

---

## English Only — No i18n

The LSMFAPI GUI is an internal operator tool. **Do not add an i18n system.** No locale JSON files, no `data-i18n` attributes, no `initI18n()` calls, no language picker. Hardcode all strings in English directly in HTML/JS.

---

## Authentication

Use `fetchAuth()` from `auth.js` for all authenticated API calls. It auto-refreshes the JWT and redirects to `/login` on session expiry.

---

## Dark Theme

All pages share the same design system:

| Token | Value |
|---|---|
| Body background | `#0f1117` |
| Cards / nav | `#1a1f2e` |
| Borders | `#2d3748` |
| Primary text | `#e2e8f0` |
| Accent | `#90cdf4` |

---

## Module Scripts

Each page has exactly **one** `<script type="module">` block (or a companion `.js` file for large pages) that imports from `auth.js`. One HTML + script per domain.

---

## Page Layout Pattern

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>LSMFAPI — Page Title</title>
  <link rel="stylesheet" href="/shared.css">
</head>
<body>
  <nav>...</nav>
  <main>...</main>
  <script type="module" src="/page.js"></script>
</body>
</html>
```

---

## Browser Console Logging Policy

**Log verbosely.** Engineers must be able to diagnose any frontend behaviour solely from the browser console.

### Mandatory rule: add logging whenever you touch code

Any time you modify a frontend function or block — even for an unrelated fix — check whether it has console logging. If it does not, add it before moving on.

### What to log

| Event type | Level | What to include |
|---|---|---|
| Data fetches | `console.log` | URL, start (`performance.now()`), result size, elapsed ms |
| Cache hits / misses | `console.log` | Key, cache age in seconds |
| State transitions | `console.log` | Old → new state, relevant payload summary |
| User interactions | `console.log` | Action name, resolved parameters |
| Warnings / empty results | `console.warn` | What was expected, what was received |
| Errors | `console.error` | Full error object + context |

### Prefix convention

```
[LSMFAPI:accuracy]   accuracy analysis page
[LSMFAPI:recipes]    recipe editor
[LSMFAPI:auth]       login / auth
[LSMFAPI:<page>]     derive from HTML filename
```

### Throttling at high speed

When a timer or animation loop fires many times per second, guard verbose output:

```javascript
if (frameIndex % 10 === 0) {
  console.log(`[LSMFAPI:grid] frame ${frameIndex}/${total}`);
}
```
