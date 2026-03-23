---
name: review-plan-phase
description: >
  Principal-engineer audit of plan-driven implementation phases in ESS. Compares
  code against the governing plan, verifies adherence to AGENTS.md rules, flags
  gaps, auto-remediates all safe findings immediately, and escalates only the
  remaining human decisions. Produces structured review reports with [agent] and
  [human] remediation tags. Use after implementing a plan phase.
argument-hint: 'The plan file path and phase identifier to review, e.g. "docs/plans/active/plan.md Phase D1"'
license: Apache-2.0
---

# Review Plan Phase

Use this skill after implementing a plan phase. It performs a principal-engineer
audit comparing the implementation against the governing plan, flags gaps, and
produces a structured report.

## Outcome

Produce a structured review report saved to
[docs/plans/review-reports/](../../../docs/plans/review-reports/) that:
- lists each plan task with pass/fail/partial status
- flags deviations with severity and remediation instructions
- tags each remediation as `[agent]` (safe to auto-fix) or `[human]` (needs decision)
- applies all `[agent]` remediations before escalating any `[human]` item
- includes an overall verdict: PASS, PASS WITH CAVEATS, or FAIL

Default behavior is fix-first, escalate-last. Never stop after identifying findings
if there are safe remediations you can still complete in the same pass.

## Procedure

### Step 1 — Load Context

1. Read [AGENTS.md](../../../AGENTS.md) in full.
2. Read [docs/README.md](../../../docs/README.md).
3. Read the governing plan file.
4. Identify the specific phase being reviewed.

### Step 2 — Enumerate Phase Tasks

List every task in the phase from the plan's completion checklist. For each task,
determine the expected deliverable: file created, function implemented, test
written, doc updated, etc.

### Step 3 — Verify Implementation

For each task:
1. Locate the relevant code/doc changes.
2. Compare against what the plan specifies.
3. Check adherence to AGENTS.md conventions:
   - uv-only workflows
   - pydantic validation at boundaries
   - async safety with timeouts and semaphores
   - observer-only constraint (no remediation actions)
   - Bedrock ABSK auth pattern
   - ruff-clean code
   - structured JSON logging
4. Mark the task as: ✅ PASS, ⚠️ PARTIAL, or ❌ FAIL

### Step 4 — Check Cross-Cutting Requirements

Verify:
- Tests: are there meaningful tests for new behavior?
- Documentation: are context docs, README, and plan frontmatter updated?
- Plan bookkeeping: are completed tasks checked off in frontmatter?
- No regressions: do existing tests still pass?
- Operational safety: does the implementation remain observer-only and read-only?

### Step 5 — Classify Findings

For each gap or deviation:
- `BLOCKER` — implementation is wrong or missing; blocks phase completion
- `RISK` — works but violates conventions or introduces fragility
- `OPTIMIZATION` — functional but could be improved

For each finding, tag the remediation:
- `[agent]` — safe to auto-fix (typo, missing import, doc update, test addition)
- `[human]` — requires architectural or scope decision

Default to `[agent]` unless the fix changes architecture, scope, operational
behavior, or an external contract.

### Step 6 — Remediate Safe Findings Immediately

Regardless of whether `[human]` findings also exist:
- apply every `[agent]` fix immediately
- rerun the smallest useful validation set after each meaningful fix batch
  (`uv run pytest`, targeted tests, `uv run ruff check .`, docs/bookkeeping verification)
- update plan frontmatter and docs when the fixes complete planned work
- re-check the phase against the plan after remediation

Do not wait for a follow-up prompt before doing this work.

### Step 7 — Escalate Only The Remaining Human Decisions

If any `[human]` findings remain after auto-remediation:
- present only those unresolved items to the user
- include the recommendation, tradeoff, and exact file/plan section affected
- state clearly that all `[agent]` items are already fixed
- keep the request surgical so the user can answer quickly

### Step 8 — Save Report

Save to `docs/plans/review-reports/` with naming convention:
`<phase-id>-review-<YYYY-MM-DD>-<4char>.md`

Example: `phase-D1-review-2026-03-25-k7m2.md`

Update the plan frontmatter to check off completed tasks.

Report format must include:
- task-by-task status
- findings with severity, owner, and remediation status
- auto-remediation summary
- unresolved human decisions only
- final verdict after remediation
