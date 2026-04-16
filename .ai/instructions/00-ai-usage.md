# AI Instructions — Setup & Meta-Rules

This `.ai/` folder is the **single source of truth** for all AI instructions in this project. It is intentionally tool-agnostic: the same files work with Claude Code, GitHub Copilot, Cursor, Windsurf, or any other AI assistant.

---

## The Tool-Agnostic Rule

**Never put real AI instructions in tool-specific files** (`CLAUDE.md`, `.cursorrules`, `.github/copilot-instructions.md`, `.windsurfrules`, etc.).

Instead, keep all instructions in `.ai/` and use thin pointer files per tool:

**Claude Code** — `CLAUDE.md` at repo root:
```markdown
Project conventions and architecture live in `.ai/`. Read the relevant files before making changes.
```

**GitHub Copilot** — `.github/copilot-instructions.md`:
```markdown
Project conventions and architecture live in `.ai/`. Read the relevant files before making changes.
```

**Cursor** — `.cursorrules`:
```
Project conventions and architecture live in .ai/. Read the relevant files before making changes.
```

Pointer files should contain nothing else. All real content goes in `.ai/`.

---

## Setting Up a New Project

1. Copy this `.ai/` folder to the repo root.
2. Create thin pointer files for whichever AI tools the team uses (see above).
3. Find and replace every `[PROJECT NAME]` / `[package]` placeholder with real values.
4. Fill in `instructions/01-project-overview.md` — tech stack, repo layout, data sources, user roles.
5. Start filling in `context/architecture.md` as you add tables, measurements, and routes.
6. Update `context/features.md` as milestones ship.

---

## Planning Mode

**When asked to plan (e.g. `/plan`, "let's plan this"), produce a plan and stop. Never start implementing immediately after a plan is approved.**

- Write the plan file → wait for an explicit "go ahead" / "implement" instruction in a new message.
- Approval of a plan means "the plan looks good" — not "start coding now".
- Do not write, edit, or create any files (except the plan file) during or immediately after planning.
- Implementation begins only when the user sends a separate follow-up message explicitly asking to proceed.

### Planning Workflow

For any non-trivial feature, follow this sequence. Each step produces artifacts in `specs/<NNN>-<feature-name>/`.

```
Specify → Clarify → Plan → Checklist → Tasks → Analyze → Implement
```

See the matching prompt file in `.ai/prompts/` for each step.

---

## Always Read First

Before making any changes, read the relevant `.ai/` files:

- `instructions/01-project-overview.md` — what the project is, tech stack, layout
- `instructions/02-backend-conventions.md` — how to add routes, tables, migrations
- `instructions/03-frontend-conventions.md` — no-build-step JS, i18n, theming
- `instructions/04-constraints.md` — hard rules (what NOT to do)
- `instructions/05-user-profile.md` — who the user is, how to communicate, working rules
- `context/architecture.md` — SQLite schema, InfluxDB measurements, API contracts
- `context/features.md` — shipped milestones and backlog

---

## General AI Behavior Rules

- Don't add features, refactor code, or make "improvements" beyond what was asked.
- Don't add comments, docstrings, or type annotations to code you didn't change.
- Don't add error handling for scenarios that can't happen.
- Don't create helpers or abstractions for one-time operations.
- Don't design for hypothetical future requirements.
- Read files before suggesting changes to them.
- Prefer editing existing files over creating new ones.

---

## Keeping the Repo in Sync

`.ai/` is the single source of truth, but humans (and other tools) also read plain files scattered across the repo — `README.md`, `PLANNING.md`, docs, changelogs. The AI must keep these in sync.

### When to update human-readable files

Update the relevant files whenever the `.ai/` context changes:

| `.ai/` change | Human-readable file(s) to update |
|---|---|
| New feature shipped / milestone completed | `README.md`, `PLANNING.md`, `docs/` if present |
| New API route added to `context/architecture.md` | `README.md` API section (if one exists) |
| Backlog or roadmap changed in `context/features.md` | `PLANNING.md` or `README.md` roadmap section |
| New setup step or dependency | `README.md` getting-started section |
| New tech stack entry in `01-project-overview.md` | `README.md` tech stack section |

**Rule**: If the `.ai/` context is the source of truth, human-readable files are derived output. Never let them drift more than one session behind.

**Before ending a session** (or when asked to prepare for `/clear`), run `.ai/prompts/sync.md` to flush all pending updates at once.

---

## File Map

```
.ai/
  instructions/
    00-ai-usage.md              ← This file: setup, meta-rules, planning mode
    01-project-overview.md      ← What the project is, stack, layout, roles
    02-backend-conventions.md   ← Routers, migrations, auth, coding standards
    03-frontend-conventions.md  ← No-build-step JS, i18n, dark theme, logging
    04-constraints.md           ← Hard rules: what NOT to do
    05-user-profile.md          ← Who the user is, communication style, working rules
  context/
    architecture.md             ← SQLite tables, InfluxDB measurements, API contracts
    features.md                 ← Shipped milestones + backlog
  prompts/
    new-feature.md              ← End-to-end implementation checklist
    add-api-router.md           ← Router scaffolding steps
    add-sqlite-table.md         ← Table + migration steps
    add-i18n-keys.md            ← Locale file update steps
    specify.md                  ← Write a feature specification
    clarify.md                  ← Resolve ambiguity in a spec
    plan.md                     ← Write a technical implementation plan
    checklist.md                ← Generate a requirements quality checklist
    tasks.md                    ← Break a plan into an ordered task list
    analyze.md                  ← Cross-check spec/plan/tasks for consistency
    implement.md                ← Execute a task list phase by phase
    taskstoissues.md            ← Convert tasks.md into GitHub Issues
    architect.md                ← Solution architect persona for project planning
    sync.md                     ← Flush session changes to .ai/ and human-readable files; prepare for /clear
    update-readme.md            ← Sync README, PLANNING.md, and docs from .ai/ context
    update-blueprint.md         ← Pull latest framework files from central blueprint repo
```

---

## Constitution Principles

These principles govern all planning decisions. Amendments require explicit discussion.

1. **Read before acting**: Always read `.ai/` before suggesting or making changes.
2. **Plan before building**: Non-trivial features get a spec + plan before implementation.
3. **Minimal scope**: Implement what was asked, not more.
4. **Tool-agnostic instructions**: All AI context lives in `.ai/`, not in tool-specific files.
5. **Keep docs in sync**: Human-readable files (README, PLANNING, docs) are derived from `.ai/`. Run `sync.md` before ending a session.
6. **No secrets committed**: Config files and `.env` are gitignored. Only `.example` files are committed.
7. **Prod is off-limits**: Never touch production directly. All changes go through the deployment pipeline.
