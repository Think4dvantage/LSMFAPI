# Prompt: Update AI Blueprint from Central Repo

> **Purpose**: Pull the latest framework files from the central AI Blueprint repo and apply them to this project's `.ai/` folder.
> **Source**: https://github.com/Think4dvantage/ai-blueprint
> **Use when**: You want to sync improvements made to the central blueprint into this project.

---

## The Two Categories of Files

Not all `.ai/` files are updated. The blueprint owns the **framework**. The project owns its **data**.

| Category | Files | Rule |
|---|---|---|
| **Framework** — owned by blueprint | `instructions/00-ai-usage.md`, `instructions/02-backend-conventions.md`, `instructions/03-frontend-conventions.md`, `instructions/04-constraints.md`, `instructions/05-user-profile.md`, all `prompts/*.md` | Always overwrite with latest from repo |
| **Project data** — owned by this project | `instructions/01-project-overview.md`, `context/architecture.md`, `context/features.md` | Never touch — these are project-specific |

---

## Step 1 — Fetch the Latest Framework Files

Fetch each framework file from the central repo using its raw GitHub URL.

Base URL: `https://raw.githubusercontent.com/Think4dvantage/ai-blueprint/main/`

Files to fetch and their local targets:

| Remote path | Local path |
|---|---|
| `.ai/instructions/00-ai-usage.md` | `.ai/instructions/00-ai-usage.md` |
| `.ai/instructions/02-backend-conventions.md` | `.ai/instructions/02-backend-conventions.md` |
| `.ai/instructions/03-frontend-conventions.md` | `.ai/instructions/03-frontend-conventions.md` |
| `.ai/instructions/04-constraints.md` | `.ai/instructions/04-constraints.md` |
| `.ai/instructions/05-user-profile.md` | `.ai/instructions/05-user-profile.md` |
| `.ai/prompts/add-api-router.md` | `.ai/prompts/add-api-router.md` |
| `.ai/prompts/add-i18n-keys.md` | `.ai/prompts/add-i18n-keys.md` |
| `.ai/prompts/add-sqlite-table.md` | `.ai/prompts/add-sqlite-table.md` |
| `.ai/prompts/analyze.md` | `.ai/prompts/analyze.md` |
| `.ai/prompts/architect.md` | `.ai/prompts/architect.md` |
| `.ai/prompts/checklist.md` | `.ai/prompts/checklist.md` |
| `.ai/prompts/clarify.md` | `.ai/prompts/clarify.md` |
| `.ai/prompts/implement.md` | `.ai/prompts/implement.md` |
| `.ai/prompts/new-feature.md` | `.ai/prompts/new-feature.md` |
| `.ai/prompts/plan.md` | `.ai/prompts/plan.md` |
| `.ai/prompts/specify.md` | `.ai/prompts/specify.md` |
| `.ai/prompts/sync.md` | `.ai/prompts/sync.md` |
| `.ai/prompts/tasks.md` | `.ai/prompts/tasks.md` |
| `.ai/prompts/taskstoissues.md` | `.ai/prompts/taskstoissues.md` |
| `.ai/prompts/update-blueprint.md` | `.ai/prompts/update-blueprint.md` |
| `.ai/prompts/update-readme.md` | `.ai/prompts/update-readme.md` |

Fetch all files. If a fetch fails (404, network error), note the failure and continue with the rest — do not abort the whole update.

---

## Step 2 — Detect New Files

The central repo may have added new framework files that don't exist locally yet. Any file fetched that does not currently exist in the local `.ai/` folder is a **new addition** — write it.

---

## Step 3 — Detect Removed Files

After writing all fetched files, check if any local framework files exist that were **not** in the fetch list above. If any are found:

- Do not delete them automatically
- List them and ask: "These local framework files have no equivalent in the central blueprint. Delete them? (yes/no per file)"

---

## Step 4 — Report Changes

For each file processed, report the outcome:

| File | Result |
|---|---|
| `instructions/00-ai-usage.md` | Updated |
| `prompts/sync.md` | No change |
| `prompts/new-prompt.md` | Added (new in blueprint) |
| `prompts/old-prompt.md` | Needs review (exists locally, not in blueprint) |

Summary line: `N files updated, N added, N unchanged, N failed, N need review`

---

## Step 5 — Check for Conflicts with Project Data

After updating, read the project data files (`01-project-overview.md`, `context/architecture.md`, `context/features.md`) and skim the updated `00-ai-usage.md` and `02-backend-conventions.md`.

If the updated framework references patterns, conventions, or sections that no longer match the project data files, flag them:

> **Conflict:** `context/architecture.md` has no "Deployment" section but `00-ai-usage.md` now references it. Consider updating the project file to match the new blueprint structure.

Do not auto-fix conflicts. Report them and ask if the user wants to address them.

---

## Step 6 — Remind About Pointer Files

Pointer files (`CLAUDE.md`, `.cursorrules`, `.github/copilot-instructions.md`) are not updated by this prompt — they are one-liners and don't change. No action needed.

---

## Notes

- This prompt updates itself (`update-blueprint.md`) — that is intentional. The update mechanism should always be current.
- Project data files are never touched. If the blueprint structure has changed significantly, the user may want to manually align the project data files with the new structure — the conflict check in Step 5 surfaces this.
- If the GitHub repo is private or unreachable, the fetch will fail. The user can manually copy the `.ai/` folder from the local clone of the blueprint repo instead.
