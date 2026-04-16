# Prompt: Generate Requirements Checklist

> **Purpose**: Generate a requirements quality checklist — "unit tests for English".
> **Prerequisites**: `spec.md` must exist. `plan.md` and `tasks.md` are used if available.
> **Output**: `specs/<current-feature>/checklists/<domain>.md`

---

## Core Concept

Checklists are **unit tests for requirements writing**. They validate the quality, clarity, and completeness of requirements — NOT whether the implementation works.

**NOT for**:
- ❌ "Verify the button clicks correctly"
- ❌ "Confirm the API returns 200"

**FOR**:
- ✅ "Are visual hierarchy requirements defined for all card types?" [Completeness]
- ✅ "Is 'prominent display' quantified with specific sizing/positioning?" [Clarity]
- ✅ "Are accessibility requirements defined for keyboard navigation?" [Coverage]

---

## Execution Steps

### 1. Clarify intent

Ask up to 3 questions to determine:
- **Domain/theme**: UX? Security? API? Performance? Testing?
- **Depth**: Lightweight pre-commit sanity check or formal release gate?
- **Audience**: Author (self-review) or reviewer (PR/QA)?

Skip questions that are already clear from the user's request.

### 2. Load feature context

Read from `specs/<current-feature>/`:
- `spec.md` — feature requirements and scope
- `plan.md` (if exists) — technical details
- `tasks.md` (if exists) — implementation tasks

### 3. Generate the checklist

Create `specs/<current-feature>/checklists/<domain>.md`. Use short names:
- `ux.md`, `api.md`, `security.md`, `performance.md`, `test.md`

If the file already exists, append to it (don't overwrite).

Number items starting at `CHK001`.

**Item format**:
```
- [ ] CHK001 [Question about requirement quality] [Dimension, Spec §Section]
```

**Quality dimensions**:
- `[Completeness]` — Are all necessary requirements present?
- `[Clarity]` — Are requirements unambiguous and specific?
- `[Consistency]` — Do requirements align without conflicts?
- `[Measurability]` — Can requirements be objectively verified?
- `[Coverage]` — Are all scenarios/edge cases addressed?
- `[Gap]` — A requirement that is missing entirely
- `[Ambiguity]` — A vague term needing quantification
- `[Conflict]` — Contradictory requirements
- `[Assumption]` — An undocumented assumption needing validation

**Traceability**: ≥80% of items must include `[Spec §Section]` or a dimension marker.

**Category structure**:
```markdown
## Requirement Completeness
## Requirement Clarity
## Requirement Consistency
## Acceptance Criteria Quality
## Scenario Coverage
## Edge Case Coverage
## Non-Functional Requirements
## Dependencies & Assumptions
```

**Prohibited patterns** (implementation tests, not requirement tests):
- ❌ Any item starting with: Verify, Test, Confirm, Check + implementation behavior

**Required patterns** (requirement quality tests):
- ✅ "Are [X] requirements defined/specified for [scenario]?"
- ✅ "Is '[vague term]' quantified with specific criteria?"
- ✅ "Are requirements consistent between [section A] and [section B]?"
- ✅ "Does the spec define [missing aspect]?"

Soft cap: if raw candidates exceed 40 items, prioritize by risk/impact and merge near-duplicates.

### 4. Report

Report:
- Full path to created checklist
- Total item count
- Focus areas selected
- Depth level and audience
- Reminder that multiple checklists can exist for different domains
