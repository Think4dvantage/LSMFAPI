# Prompt: Create Implementation Plan

> **Purpose**: Create a technical implementation plan from a feature specification.
> **Prerequisites**: `spec.md` must exist. Clarification recommended but optional.
> **Output**: `plan.md`, `research.md`, `data-model.md`, `contracts/`
> **Next step**: `checklist.md` and/or `tasks.md`

---

## Execution Steps

### Phase 0 — Research

1. **Load context**:
   - Read `specs/<current-feature>/spec.md`
   - Read `.ai/instructions/` files for tech stack and architecture
   - Read `.ai/instructions/00-ai-usage.md` for constitution principles

2. **Identify unknowns**: For each unclear technical decision in the spec, create a research task.

3. **Resolve unknowns**: Consolidate findings in `specs/<current-feature>/research.md`:

```markdown
## [Decision Topic]
- **Decision**: [what was chosen]
- **Rationale**: [why chosen]
- **Alternatives considered**: [what else was evaluated]
```

### Phase 1 — Design

**Prerequisites**: `research.md` complete, all unknowns resolved.

1. **Technical Context** — define:
   - Tech stack choices
   - Architecture approach (patterns, layers)
   - Key dependencies and their versions
   - Performance and security considerations

2. **Constitution Check** — for each principle in `.ai/instructions/00-ai-usage.md`, verify the plan complies. If a principle is violated, either adjust the plan or explicitly justify the exception.

3. **Data Model** — write `specs/<current-feature>/data-model.md`:
   - Extract entities from the spec
   - Define fields, types, relationships
   - Specify validation rules and state transitions

4. **API Contracts** (if applicable) — write to `specs/<current-feature>/contracts/`:
   - For each user action → one endpoint
   - Use standard REST patterns
   - OpenAPI YAML format preferred

5. **File Structure** — define the exact files/folders to be created.

### Phase 2 — Plan Document

Write `specs/<current-feature>/plan.md` with:

```markdown
# Implementation Plan: [Feature Name]

## Technical Context
[Tech stack, architecture, key dependencies]

## Constitution Check
[Compliance status for each principle]

## Data Model Summary
[Key entities and their relationships]

## File Structure
[Files to be created/modified]

## Implementation Phases
### Phase 1: [Name]
[What gets built, in what order]

### Phase 2: [Name]
...

## Dependencies
[External libraries, APIs, or services required]

## Risk & Mitigations
[Identified risks and how they're addressed]
```

### Stop and Report

Report:
- Feature folder
- Path to `plan.md`
- All generated artifacts
- Any unresolved constitution violations
- Suggested next step: `tasks.md`

---

## Key Rules

- Mark unresolved technical unknowns as `[NEEDS CLARIFICATION: question]` — resolve in `research.md`
- Constitution violations are blocking — resolve before proceeding
- File paths in the plan must be absolute or relative to repo root
- Do not start implementation here — this phase ends at plan creation
