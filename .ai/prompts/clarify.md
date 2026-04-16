# Prompt: Clarify Specification

> **Purpose**: Identify and resolve underspecified areas in the current feature spec via targeted questions.
> **Prerequisites**: `spec.md` must exist (run `specify.md` first)
> **Output**: Updated `spec.md` with clarifications integrated
> **Next step**: `plan.md`

---

## Execution Steps

### 1. Load the spec

Read `specs/<current-feature>/spec.md`. Identify the active feature by checking the current git branch or the most recently modified spec folder.

If spec is missing, instruct user to run the specify workflow first.

### 2. Scan for ambiguity

Perform a structured scan across these categories. Mark each: **Clear / Partial / Missing**.

| Category | What to check |
|---|---|
| Functional Scope | Core user goals, explicit out-of-scope, user roles |
| Domain & Data | Entities, attributes, relationships, state transitions, scale |
| Interaction & UX | Critical user journeys, error/empty/loading states, accessibility |
| Performance | Latency targets, throughput, scalability limits |
| Security & Privacy | AuthN/Z, data protection, compliance |
| Integration | External services, failure modes, data formats |
| Edge Cases | Negative scenarios, concurrency, rate limiting |
| Terminology | Canonical terms, avoided synonyms |
| Acceptance | Testable criteria, measurable Definition of Done |

### 3. Generate clarification questions

From categories marked Partial or Missing, generate a prioritized queue of **max 5** questions.

Each question must:
- Be answerable with a short multiple-choice selection (2–5 options) OR a short phrase (≤5 words)
- Materially impact architecture, data modeling, task decomposition, or compliance
- Not be about implementation details (tech stack, frameworks, hosting)

Priority: scope > security/privacy > user experience > technical details

**Never ask about**:
- Things that already have reasonable industry defaults
- Implementation choices (unless lack of clarity blocks functional understanding)

### 4. Ask questions sequentially (one at a time)

For multiple-choice questions:
- Identify the best option based on the project context (read `.ai/context/` files)
- Present it as **Recommended: Option [X] — [1–2 sentence reasoning]**
- Show all options as a Markdown table
- Add: "Reply with option letter, 'yes' to accept recommendation, or your own short answer."

For short-answer questions:
- Provide **Suggested: [answer] — [brief reasoning]**
- Add: "Answer in ≤5 words, or say 'yes' to accept suggestion."

Stop when:
- All critical ambiguities are resolved
- User signals completion ("done", "good", "no more")
- 5 questions reached

### 5. Integrate each answer immediately

After each accepted answer:

1. Ensure a `## Clarifications` section exists (create after the Overview section if missing)
2. Add a `### Session YYYY-MM-DD` subheading for today
3. Append: `- Q: [question] → A: [answer]`
4. Apply the clarification to the appropriate spec section
5. Save the spec after each integration

### 6. Validate after each write

- No duplicate clarification bullets
- No lingering vague placeholders the answer was meant to resolve
- No contradictory earlier statements
- Terminology is consistent across all updated sections

### 7. Report completion

- Number of questions asked and answered
- Path to updated spec
- Sections touched
- Suggested next step
