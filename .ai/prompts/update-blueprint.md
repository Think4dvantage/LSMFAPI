# Prompt: Update AI Blueprint from Central Repo

> **Purpose**: Pull the latest framework files from the central AI Blueprint repo and apply them to this project's `.ai/` folder.
> **Category**: `dev-web` — FastAPI + Vanilla JS web services
> **Source**: https://github.com/Think4dvantage/ai-blueprint
> **Use when**: You want to sync improvements made to the central blueprint into this project.

---

## The Two Categories of Files

Not all `.ai/` files are updated. The blueprint owns the **framework**. The project owns its **data**.

| Category | Files | Rule |
|---|---|---|
| **Framework** — owned by blueprint | `instructions/00-ai-usage.md`, `instructions/02-backend-conventions.md`, `instructions/03-frontend-conventions.md`, `instructions/04-constraints.md`, `instructions/05-user-profile.md`, `instructions/06-testing-conventions.md`, `instructions/07-api-conventions.md`, all `prompts/*.md` | Always overwrite with latest from repo |
| **Project data** — owned by this project | `instructions/01-project-overview.md`, `context/architecture.md`, `context/features.md` | Never touch — these are project-specific |

---

## Step 0 — Fetch Blueprint Manifest

Before fetching individual files, attempt to fetch the blueprint manifest from the central repo:

**URL**: `https://raw.githubusercontent.com/Think4dvantage/ai-blueprint/main/dev-web/.ai/manifest.json`

- If the fetch is successful, use the file list in the manifest to determine which files to fetch in Step 1.
- If the fetch fails (404), proceed to Step 1 using the hardcoded list below as a fallback.

---

## Step 1 — Fetch the Latest Framework Files

Fetch each framework file from the central repo using its raw GitHub URL.

Base URL: `https://raw.githubusercontent.com/Think4dvantage/ai-blueprint/main/dev-web/.ai/`

**Hardcoded Fallback List** (only use if Step 0 fails):

| Remote path | Local path |
|---|---|
| `instructions/00-ai-usage.md` | `instructions/00-ai-usage.md` |
| `instructions/02-backend-conventions.md` | `instructions/02-backend-conventions.md` |
| `instructions/03-frontend-conventions.md` | `instructions/03-frontend-conventions.md` |
| `instructions/04-constraints.md` | `instructions/04-constraints.md` |
| `instructions/05-user-profile.md` | `instructions/05-user-profile.md` |
| `instructions/06-testing-conventions.md` | `instructions/06-testing-conventions.md` |
| `instructions/07-api-conventions.md` | `instructions/07-api-conventions.md` |
| `instructions/08-operability.md` | `instructions/08-operability.md` |
| `prompts/add-api-router.md` | `prompts/add-api-router.md` |
| `prompts/add-i18n-keys.md` | `prompts/add-i18n-keys.md` |
| `prompts/add-sqlite-table.md` | `prompts/add-sqlite-table.md` |
| `prompts/analyze.md` | `prompts/analyze.md` |
| `prompts/architect.md` | `prompts/architect.md` |
| `prompts/checklist.md` | `prompts/checklist.md` |
| `prompts/clarify.md` | `prompts/clarify.md` |
| `prompts/fix-bug.md` | `prompts/fix-bug.md` |
| `prompts/implement.md` | `prompts/implement.md` |
| `prompts/new-feature.md` | `prompts/new-feature.md` |
| `prompts/plan.md` | `prompts/plan.md` |
| `prompts/specify.md` | `prompts/specify.md` |
| `prompts/sync.md` | `prompts/sync.md` |
| `prompts/tasks.md` | `prompts/tasks.md` |
| `prompts/taskstoissues.md` | `prompts/taskstoissues.md` |
| `prompts/update-blueprint.md` | `prompts/update-blueprint.md` |
| `prompts/update-readme.md` | `prompts/update-readme.md` |

Fetch all files. If a fetch fails (404, network error), note the failure and continue with the rest — do not abort the whole update.

---

## Step 2 — Detect New Files

The central repo may have added new framework files that don't exist locally yet. Any file fetched that does not currently exist in the local `.ai/` folder is a **new addition** — write it.

---

## Step 3 — Detect Removed Files

After writing all fetched files, check if any local framework files exist that were **not** in the fetch list (from manifest or fallback). If any are found:

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

## Step 6 — Identify Contributions to the Blueprint

The goal of the blueprint is to evolve. If this project has improved a framework file or added a useful new prompt, suggest contributing it back to the central repo.

### 6.1 Scan for Local Additions
Identify any files in `.ai/prompts/` or `.ai/instructions/` (excluding project data files) that:
- Exist locally but were NOT in the blueprint manifest/fetch list.
- Have been significantly improved or customized in a way that would benefit other projects.

### 6.2 Propose the Contribution
If additions or improvements are found, present them to the user:

> **Contribution Suggestion**: I noticed you've added/improved the following framework files:
> - `prompts/new-cool-tool.md`
> - `instructions/02-backend-conventions.md` (improved auth section)
>
> Would you like to contribute these back to the central AI Blueprint?

### 6.3 Provide the "Transport" Prompt
If the user wants to contribute, generate a block they can copy:

Generate a block the user can copy into the `ai-blueprint` repo session:

```markdown
# Contribution from Project: [THIS PROJECT NAME] (dev-web category)

Please update the following blueprint files in dev-web/.ai/:

1. `prompts/new-cool-tool.md`: [Brief description of what it does]
2. `instructions/02-backend-conventions.md`: [Brief description of the improvement]

[Paste the full content of the files here]
```

---

## Notes

- This prompt updates itself (`update-blueprint.md`) — that is intentional. The update mechanism should always be current.
- Project data files are never touched. If the blueprint structure has changed significantly, the user may want to manually align the project data files with the new structure — the conflict check in Step 5 surfaces this.
- If the GitHub repo is private or unreachable, the fetch will fail. The user can manually copy the `.ai/` folder from the local clone of the blueprint repo instead.
