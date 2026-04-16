# Prompt: Implement Feature

> **Purpose**: Execute the implementation plan by processing all tasks in `tasks.md`.
> **Prerequisites**: `tasks.md` must exist. All checklists should be complete.
> **Output**: Implemented code, updated `tasks.md` (tasks marked complete)

---

## Execution Steps

### 1. Check checklists

If `specs/<current-feature>/checklists/` exists, scan all checklist files:

| Checklist | Total | Completed | Incomplete | Status |
|---|---|---|---|---|
| requirements.md | 12 | 12 | 0 | ✅ PASS |
| ux.md | 8 | 5 | 3 | ❌ FAIL |

If any checklist has incomplete items:
- Display the table
- Ask: "Some checklists are incomplete. Proceed anyway? (yes/no)"
- Wait for response before continuing

### 2. Load implementation context

**Required**:
- `specs/<current-feature>/tasks.md` — complete task list
- `specs/<current-feature>/plan.md` — tech stack, architecture, file structure

**Optional** (load if they exist):
- `data-model.md` — entities and relationships
- `contracts/` — API specifications
- `research.md` — technical decisions and constraints

Also read `.ai/instructions/` and `.ai/context/` for project-wide architecture decisions.

### 3. Setup verification

Before implementation begins, verify or create ignore files appropriate to the detected tech stack:
- `.gitignore` (if git repo)
- `.dockerignore` (if Dockerfile exists)

Common patterns to ensure are present:
- Python: `.venv/`, `__pycache__/`, `*.pyc`, `.env`, `config.yml`
- Flutter/Dart: `.dart_tool/`, `build/`, `*.g.dart`
- Node: `node_modules/`, `dist/`, `.env`

If an ignore file exists, append only missing critical patterns. If missing, create with a full appropriate pattern set.

### 4. Parse and execute tasks

**Parse `tasks.md`**:
- Extract task phases
- Identify task IDs, descriptions, file paths
- Note `[P]` parallel markers and `[US#]` story labels
- Map sequential vs. parallel execution

**Execution rules**:
- Complete each phase before moving to the next
- Sequential tasks run in order
- Parallel tasks `[P]` (different files, no shared dependencies) can run concurrently
- Follow TDD if tests are in the task list (test tasks run before their implementation)
- Tasks affecting the same file must run sequentially

**Implementation order**:
- Phase 1 (Setup): Initialize project structure, dependencies, configuration
- Phase 2 (Foundation): Blocking infrastructure that all stories depend on
- Phase 3+ (Stories): Models → Services → Screens/Endpoints → Integration
- Final (Polish): Cleanup, docs, cross-cutting concerns

### 5. Progress tracking

After completing each task:
1. Mark it complete in `tasks.md`: change `- [ ]` to `- [x]`
2. Report progress: "Completed T012: Created Widget model in src/models/widget.py"

If a non-parallel task fails:
- Halt execution
- Report error with context
- Suggest debugging steps
- Do not proceed until resolved

If a parallel task `[P]` fails:
- Continue with other parallel tasks
- Report the failed task at end of parallel phase

### 6. Completion validation

After all tasks are complete:
- Verify all required tasks are marked `[x]`
- Check that implementation matches the original specification
- Confirm the implementation follows the technical plan
- Report final status with summary of all completed work

---

## Notes

- If `tasks.md` is missing or incomplete, run `tasks.md` prompt first
- Do not skip tasks silently — either complete them or explicitly document why they're blocked
- Architecture decisions from `.ai/instructions/` and `.ai/context/` take precedence over local assumptions
