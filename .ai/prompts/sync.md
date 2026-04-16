# Prompt: Sync Session & Prepare for /clear

> **Purpose**: Flush everything learned this session into `.ai/` and human-readable files, then close all open loops so the next session can start clean.
> **Use when**: Before running `/clear`, ending a long session, or any time context has drifted ahead of the files.

---

## Why This Exists

The AI accumulates discoveries during a session — new tables, new routes, changed plans, resolved decisions — that live only in conversation context. A `/clear` command destroys that context. This prompt makes the session's knowledge durable before the wipe.

---

## Step 1 — Audit What Changed This Session

Scan the conversation and the working directory. For each category, note what is new or different since the session started:

| Category | What to check |
|---|---|
| Schema | New SQLite tables, columns, or relationships |
| API | New or changed routes, request/response shapes |
| Architecture | New services, patterns, or integration points |
| Config | New config keys or environment variables |
| Features | Milestones shipped, backlog items completed or added |
| Decisions | Technical choices made, options ruled out |
| Open issues | Bugs found but not fixed, TODOs left in code |
| In-progress work | Partially completed tasks that need to be resumed |

Write a brief internal summary before proceeding (not written to any file — just to organize the next steps).

---

## Step 2 — Update `.ai/context/`

### `context/architecture.md`

For every new or changed item found in Step 1:
- Add new SQLite tables to the **SQLite Tables** section
- Add new InfluxDB measurements to the **InfluxDB Measurements** section
- Add or update API routes in the **API Contracts** section
- Add any new deployment notes

Do not remove existing entries unless they were explicitly deleted this session.

### `context/features.md`

- Move completed milestones from "Backlog" to "Shipped Milestones" with a brief description
- Add new backlog items discovered this session
- Update the current version number if a milestone shipped

---

## Step 3 — Update `.ai/instructions/` (if needed)

Only update instruction files if a convention genuinely changed:
- New auth dependency introduced → `02-backend-conventions.md`
- New hard rule discovered → `04-constraints.md`
- New frontend pattern established → `03-frontend-conventions.md`

Do not rewrite instructions just to add detail. Update only when behavior should change.

---

## Step 4 — Update Human-Readable Files

Run `.ai/prompts/update-readme.md` now to sync all human-readable files from `.ai/`.

If `PLANNING.md` exists and milestone or roadmap data changed, update it with:
- Current status (one-line summary)
- Completed items ticked off
- In-progress items updated
- Next steps refreshed

---

## Step 5 — Leave Resume Notes

If there is in-progress or unfinished work, create or update `specs/<current-feature>/tasks.md` with:
- All completed tasks marked `[x]`
- The next task clearly marked as the starting point
- Any blockers or open questions noted inline

If no spec folder exists for the in-progress work, write a brief `RESUME.md` at the repo root:

```markdown
# Resume Notes — [DATE]

## In Progress
[What was being worked on]

## Next Step
[Exact next action to take when resuming]

## Open Questions
[Anything unresolved that needs a decision]

## Context
[Key decisions made this session that aren't yet in .ai/]
```

---

## Step 6 — Final Checklist

Before declaring the session closed:

- [ ] `context/architecture.md` reflects all new tables, routes, and measurements
- [ ] `context/features.md` reflects shipped milestones and updated backlog
- [ ] Instruction files updated if any convention changed
- [ ] README.md (and PLANNING.md if present) synced via `update-readme.md`
- [ ] In-progress tasks have a clear resume point in `tasks.md` or `RESUME.md`
- [ ] No secrets or credentials appear in any committed file
- [ ] Open issues or bugs are noted somewhere (inline TODO, `RESUME.md`, or GitHub Issue)

Report the checklist result. If any item is incomplete, complete it before finishing.

---

## After This Prompt

The session is ready to clear. Run `/clear` when ready. The next session should start by reading `.ai/` — everything needed to continue will be there.
