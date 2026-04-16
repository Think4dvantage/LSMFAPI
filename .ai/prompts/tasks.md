# Prompt: Generate Task Breakdown

> **Purpose**: Generate an actionable, dependency-ordered `tasks.md` from the feature plan.
> **Prerequisites**: `spec.md` and `plan.md` must exist.
> **Output**: `specs/<current-feature>/tasks.md`
> **Next steps**: `analyze.md` → `implement.md`

---

## Execution Steps

### 1. Load documents

**Required**:
- `specs/<current-feature>/plan.md` — tech stack, architecture, file structure
- `specs/<current-feature>/spec.md` — user stories with priorities (P1, P2, P3)

**Optional** (use if they exist):
- `data-model.md` — entities and relationships
- `contracts/` — API endpoints
- `research.md` — technical decisions

### 2. Generate tasks

**Organization**: Tasks MUST be organized by user story. Each user story gets its own phase.

**Phase Structure**:
- **Phase 1 — Setup**: Project initialization, configuration, ignore files
- **Phase 2 — Foundation**: Blocking prerequisites for all user stories
- **Phase 3+ — User Stories**: One phase per story, in P1→P2→P3 priority order
  - Within each story: Models → Services → Endpoints/Screens → Integration
- **Final Phase — Polish**: Cross-cutting concerns, documentation, cleanup

**Tests**: Only generate test tasks if explicitly requested in the spec or by the user.

### 3. Task format (REQUIRED for every task)

```
- [ ] [TaskID] [P?] [Story?] Description with exact file path
```

| Component | Format | Notes |
|---|---|---|
| Checkbox | `- [ ]` | Always start with this |
| Task ID | `T001`, `T002`... | Sequential in execution order |
| `[P]` marker | Include only if parallelizable | Different files, no incomplete dependencies |
| `[US#]` label | `[US1]`, `[US2]`... | Required for user story phase tasks only |
| Description | Clear action + exact file path | Specific enough to execute without additional context |

**Examples**:
```
- [ ] T001 Create project structure per implementation plan
- [ ] T005 [P] Implement authentication middleware in src/api/dependencies.py
- [ ] T012 [P] [US1] Create Widget model in src/models/widget.py
- [ ] T014 [US1] Implement WidgetService in src/services/widget_service.py
```

### 4. Write `tasks.md`

Structure:
```markdown
# Tasks: [Feature Name]

## Summary
- Total tasks: N
- Parallel opportunities: N
- MVP scope: Phase 3 (User Story 1 only)

## Dependencies
[Diagram showing story completion order]

## Phase 1 — Setup
- [ ] T001 ...

## Phase 2 — Foundation
- [ ] T002 ...

## Phase 3 — [User Story 1 Name] [US1]
**Goal**: [what this phase delivers]
**Independent test criteria**: [how to verify this story alone]

- [ ] T003 [US1] ...
- [ ] T004 [P] [US1] ...

## Phase 4 — [User Story 2 Name] [US2]
...

## Final Phase — Polish
- [ ] TNNN ...
```

### 5. Report

Report:
- Path to `tasks.md`
- Total task count
- Task count per user story
- Parallel opportunities identified
- Suggested MVP scope (typically just User Story 1)
- Confirmation that ALL tasks follow the required format
