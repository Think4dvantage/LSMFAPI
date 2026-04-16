# Prompt: Add i18n Keys

Use this prompt whenever new user-visible strings are introduced in the frontend.

---

Add i18n keys for `{feature}` following the project conventions:

1. Add the key to **all locale files simultaneously**:
   - `static/i18n/en.json` — English (source of truth)
   - `static/i18n/de.json` — German
   - `static/i18n/fr.json` — French
   - `static/i18n/it.json` — Italian

   [Adjust locale list to match the project's supported languages.]

2. Use the existing nested key structure. Example:
   ```json
   {
     "feature_name": {
       "title": "...",
       "description": "...",
       "btn_save": "..."
     }
   }
   ```

3. Reference in HTML:
   ```html
   <span data-i18n="feature_name.title">Fallback text</span>
   ```

4. Reference in JavaScript (after `await initI18n()`):
   ```javascript
   el.textContent = window.t('feature_name.title');
   ```

**Never hardcode a user-visible string directly in HTML or JS without a corresponding i18n key.**
