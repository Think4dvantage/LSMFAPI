# Prompt: Specify Feature

> **Purpose**: Create a feature specification from a natural language description.
> **Output**: `specs/<NNN>-<feature-name>/spec.md` + `checklists/requirements.md`
> **Next steps**: `clarify.md` (optional) → `plan.md`

---

## Input

The feature description is provided by the user after invoking this prompt.

---

## Execution Steps

### 1. Generate a short name (2–4 words)

Analyze the feature description and extract the most meaningful keywords.

- Use action-noun format: `add-user-auth`, `fix-payment-timeout`, `analytics-dashboard`
- Preserve technical terms and acronyms (OAuth2, API, JWT, ELO)

### 2. Determine the feature number

1. Check `specs/` directory for existing feature folders (e.g. `001-first-feature`)
2. Use the next available sequential number (zero-padded to 3 digits)

Create the folder: `specs/<NNN>-<short-name>/`

### 3. Write the specification

Use the template structure below. Replace all placeholders with concrete content.

**Mandatory sections** (always include):
- Overview
- User Stories (with P1/P2/P3 priorities)
- Functional Requirements (testable, unambiguous)
- Success Criteria (measurable, technology-agnostic)
- Assumptions

**Optional sections** (include when relevant):
- Key Entities / Data Model
- Non-Functional Requirements
- Out of Scope
- Edge Cases
- Dependencies

**Rules**:
- Focus on WHAT users need and WHY — never HOW to implement
- No mention of languages, frameworks, databases, or APIs
- Written for non-technical stakeholders
- Success criteria must be measurable and technology-agnostic
  - ✅ "Users can complete checkout in under 3 minutes"
  - ❌ "API response time is under 200ms" (too technical)
- Maximum **3** `[NEEDS CLARIFICATION: specific question]` markers — make informed guesses for everything else

### 4. Create requirements checklist

After writing the spec, create `specs/<NNN>-<feature-name>/checklists/requirements.md`:

```markdown
# Specification Quality Checklist: [FEATURE NAME]

**Created**: [DATE]
**Feature**: [Link to spec.md]

## Content Quality
- [ ] No implementation details (languages, frameworks, APIs)
- [ ] Focused on user value and business needs
- [ ] All mandatory sections completed

## Requirement Completeness
- [ ] No [NEEDS CLARIFICATION] markers remain
- [ ] Requirements are testable and unambiguous
- [ ] Success criteria are measurable and technology-agnostic
- [ ] All acceptance scenarios are defined
- [ ] Edge cases are identified
- [ ] Dependencies and assumptions identified

## Feature Readiness
- [ ] All functional requirements have clear acceptance criteria
- [ ] User scenarios cover primary flows
- [ ] No implementation details leak into specification
```

Review each item. If items fail, update the spec and re-validate (max 3 iterations).

If `[NEEDS CLARIFICATION]` markers remain, present them to the user as structured questions. Wait for answers, then integrate into spec.

### 5. Report completion

Report:
- Feature folder path
- Spec file path
- Checklist results (items passed/failed)
- Whether clarification is needed before planning
- Suggested next step: `clarify.md` or `plan.md`

---

## Spec Template

```markdown
# Feature: [Name]

## Overview
[1–3 sentence summary: what this feature does and why it matters]

## User Stories

### P1 — [Must Have]
As a [user type], I want to [action] so that [benefit].

**Acceptance Criteria**:
- [Criterion 1]
- [Criterion 2]

### P2 — [Should Have]
...

### P3 — [Nice to Have]
...

## Functional Requirements
- FR-001: [Requirement]
- FR-002: [Requirement]

## Non-Functional Requirements (if applicable)
- NFR-001: [Performance/Security/Accessibility requirement]

## Success Criteria
- [Measurable, user-facing outcome]

## Key Entities (if data involved)
| Entity | Key Attributes | Notes |
|--------|---------------|-------|

## Out of Scope
- [Explicitly excluded items]

## Assumptions
- [Reasonable defaults assumed]

## Dependencies
- [External dependencies or prerequisites]

## Edge Cases
- [Boundary conditions and failure scenarios]
```
