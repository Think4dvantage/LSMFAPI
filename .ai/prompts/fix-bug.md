# Prompt: Fix a Bug

Use this prompt when a bug is reported or a feature is behaving unexpectedly.

---

Fix the following bug in `{entity/feature}`:

`[Description of the bug / unexpected behavior]`

## Step 1 — Reproduction (Mandatory)

**Before applying any fix**, you must create a reproduction test case that fails because of this bug.

1.  **Backend bug**: Create a new test in `tests/backend/test_reproduce_bug.py` that fails.
2.  **Frontend bug**: Create a new Playwright test in `tests/frontend/test_reproduce_bug.spec.js` (or `.py`) that fails.

**Run the test** and confirm it fails.

## Step 2 — Root Cause Analysis

Analyze the code and explain why the bug is happening. Do not guess — use the reproduction test output to guide your analysis.

## Step 3 — Apply the Fix

Apply the minimal code change necessary to fix the bug. Follow all project conventions and constraints.

## Step 4 — Verification

1.  **Run the reproduction test**: Confirm it now passes.
2.  **Run the full test suite**: Confirm no regressions were introduced.
3.  **Delete the reproduction test**: Once the fix is verified and permanent tests are updated, delete the temporary reproduction file.

## Step 5 — Sync

Run `.ai/prompts/sync.md` to ensure any changes to behavior are reflected in the documentation.
