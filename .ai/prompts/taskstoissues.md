# Prompt: Convert Tasks to GitHub Issues

> **Purpose**: Convert tasks from `tasks.md` into GitHub Issues in the repository.
> **Prerequisites**: `tasks.md` must exist. Repository must be hosted on GitHub.
> **Output**: GitHub Issues created for each task

---

## Execution Steps

### 1. Load tasks

Read `specs/<current-feature>/tasks.md`. Extract the full task list.

### 2. Verify GitHub remote

Run:
```bash
git config --get remote.origin.url
```

**STOP if the remote is not a GitHub URL.** Only create issues in the GitHub repository that matches the remote URL.

### 3. Create GitHub Issues

For each task in `tasks.md`, create a GitHub Issue with:

**Title**: `[T###] [US#] Brief description`

**Body**:
```markdown
## Task
[Full task description from tasks.md]

## Context
- **Feature**: [Feature name]
- **Phase**: [Phase name from tasks.md]
- **User Story**: [US# label if present]
- **Parallelizable**: [Yes/No based on [P] marker]

## Files
[File path from task description]

## Related
- Spec: `specs/<feature>/spec.md`
- Plan: `specs/<feature>/plan.md`
```

**Labels** (create if they don't exist):
- `task` — all tasks
- `phase-N` — which implementation phase
- `user-story-N` — which user story (if [US#] present)
- `parallel` — if [P] marker present

### 4. Report

After creating all issues:
- Total issues created
- Links to each created issue
- Any tasks that failed to create (with error details)

---

## Safety Rules

- **NEVER** create issues in a repository that doesn't match the git remote URL
- **NEVER** create issues if the remote is not GitHub
- If issues already exist for some tasks, skip them (check by T### in title) to avoid duplicates
