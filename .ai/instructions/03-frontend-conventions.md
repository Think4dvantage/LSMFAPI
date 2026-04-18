# Frontend Conventions

## No Build Step

Changes to `static/` are live immediately in dev (volume-mounted). **Never introduce npm, webpack, vite, or any bundler.** The frontend is intentionally dependency-free.

---

## Internationalisation (i18n)

Every user-visible string must have a key in all locale files simultaneously.
[Adjust the locale list to match the project's supported languages.]

Default: `static/i18n/en.json`, `de.json`, `fr.json`, `it.json`

English (`en.json`) is the source of truth. Use the same nested key structure as existing keys.

### HTML

```html
<!-- Static text -->
<span data-i18n="nav.map">Map</span>

<!-- Input placeholder -->
<input data-i18n-placeholder="auth.email_placeholder">

<!-- Nav lang picker mount point (required on every page) -->
<div id="navLangPicker"></div>
```

### JavaScript

```javascript
// In module scripts (type="module") after await initI18n():
el.textContent = window.t('section.key');
el.textContent = window.t('section.key_with_var', { count: 5 });

// In non-module scripts that may run before initI18n() completes:
const t = typeof window.t === 'function' ? window.t : k => k;
btn.textContent = t('section.key');

// Lazy config objects (evaluated post-init, not at module load time):
function getFieldLabels() {
  return { field_a: window.t('editor.fields.field_a'), ... };
}
```

---

## Module Scripts

Each page has exactly **one** `<script type="module">` block that imports from `i18n.js` and `auth.js`. Non-module scripts run before `initI18n()` resolves — guard with the timing pattern above.

---

## Dark Theme

All pages share the same design system — match existing pages exactly:

| Token | Value |
|---|---|
| Body background | `#0f1117` |
| Cards / nav | `#1a1f2e` |
| Borders | `#2d3748` |
| Primary text | `#e2e8f0` |
| Accent | `#90cdf4` |

---

## Authentication

Use `fetchAuth()` from `auth.js` for all authenticated API calls. It auto-refreshes the JWT and redirects to `/login` on session expiry.

---

## Help Tips

Use `.help-tip` CSS class (defined in `shared.css`) for inline `?` tooltip buttons.

---

## Page Layout Pattern

Each page: one `.html` file + inline `<script type="module">` or companion `.js` file (for large pages). One HTML + script per domain.

---

## Browser Console Logging Policy

**Log verbosely.** Engineers must be able to diagnose any frontend behaviour solely from the browser console — no source-diving required.

### Mandatory rule: add logging whenever you touch code

**Any time you modify a frontend function or block — even for an unrelated fix — check whether it has console logging. If it does not, add it before moving on.** Logging is not optional and is not considered scope creep. Touching code without adding logging to unlogged paths is a mistake.

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

Every `console.*` call must start with a bracketed page/module prefix so logs can be filtered in DevTools:

```
[App:map]      map page
[App:auth]     auth / login page
[App:admin]    admin panel
[App:<page>]   derive from the HTML filename
```

[Update the prefix namespace (`App`) to match the project name.]

### Throttling at high speed

When a timer or animation loop fires many times per second, guard verbose output:

```javascript
if (this._speed <= 50) {
  console.log(`[App:replay] frame ${i}/${total}`);
}
```
