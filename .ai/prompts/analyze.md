# Prompt: Analyze for Consistency

> **Purpose**: Cross-artifact consistency and quality analysis across `spec.md`, `plan.md`, and `tasks.md`.
> **Prerequisites**: All three artifacts must exist (run after `tasks.md` prompt).
> **Output**: Analysis report (read-only — no file changes)

---

## Operating Rules

**STRICTLY READ-ONLY**: Do not modify any files. Output a structured analysis report only. Offer remediation suggestions but require explicit user approval before any edits.

**Constitution Authority**: Principles in `.ai/instructions/00-ai-usage.md` are non-negotiable. Constitution conflicts are automatically CRITICAL.

---

## Execution Steps

### 1. Load artifacts

- `specs/<current-feature>/spec.md`
- `specs/<current-feature>/plan.md`
- `specs/<current-feature>/tasks.md`
- `.ai/instructions/00-ai-usage.md` (constitution principles)

### 2. Build semantic models (internal only)

- **Requirements inventory**: Each functional + non-functional requirement
- **User story inventory**: Discrete user actions with acceptance criteria
- **Task coverage mapping**: Map each task to one or more requirements
- **Constitution rules**: Extract MUST/SHOULD statements from principles

### 3. Detection passes

Limit to 50 findings total.

**A. Duplication Detection**
- Near-duplicate requirements
- Lower-quality phrasing candidates for consolidation

**B. Ambiguity Detection**
- Vague adjectives without measurable criteria (fast, scalable, secure, intuitive)
- Unresolved placeholders (TODO, ???, `[NEEDS CLARIFICATION]`)

**C. Underspecification**
- Requirements with verbs but missing object or measurable outcome
- User stories missing acceptance criteria
- Tasks referencing files not defined in spec/plan

**D. Constitution Alignment**
- Requirements or plan elements conflicting with a MUST principle
- Missing mandated sections or quality gates

**E. Coverage Gaps**
- Requirements with zero associated tasks
- Tasks with no mapped requirement/story
- Non-functional requirements not reflected in tasks

**F. Inconsistency**
- Terminology drift (same concept named differently across files)
- Data entities in plan but absent in spec (or vice versa)
- Task ordering contradictions
- Conflicting requirements

### 4. Severity assignment

| Severity | Criteria |
|---|---|
| **CRITICAL** | Violates constitution MUST; missing core artifact; requirement with zero coverage blocking baseline |
| **HIGH** | Duplicate or conflicting requirement; ambiguous security/performance attribute; untestable acceptance criterion |
| **MEDIUM** | Terminology drift; missing non-functional task coverage; underspecified edge case |
| **LOW** | Style/wording improvements; minor redundancy |

### 5. Output report

```markdown
## Specification Analysis Report

| ID | Category | Severity | Location(s) | Summary | Recommendation |
|----|----------|----------|-------------|---------|----------------|
| A1 | Duplication | HIGH | spec.md:L120 | Two similar requirements | Merge phrasing |
| C1 | Coverage | CRITICAL | tasks.md | FR-003 has zero tasks | Add task for FR-003 |

## Coverage Summary

| Requirement Key | Has Task? | Task IDs | Notes |
|-----------------|-----------|----------|-------|

## Constitution Alignment Issues
[If any]

## Unmapped Tasks
[Tasks not linked to any requirement]

## Metrics
- Total Requirements: N
- Total Tasks: N
- Coverage: N% (requirements with ≥1 task)
- Critical Issues: N
```

### 6. Next actions

- If CRITICAL issues: Recommend resolving before implementing
- If only LOW/MEDIUM: User may proceed with improvement suggestions
- Provide explicit command suggestions

### 7. Offer remediation

Ask: "Would you like concrete remediation suggestions for the top N issues?" Do NOT apply them automatically.
