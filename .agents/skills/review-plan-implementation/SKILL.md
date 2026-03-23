---
name: review-plan-implementation
description: >
  Ruthless pre-implementation review of ESS implementation plans. Evaluates
  architecture, dependency choices, security, resilience, testability, and plan
  completeness before any code is written. Produces findings categorized as
  BLOCKER, RISK, or OPTIMIZATION, each with a concrete alternative, and
  auto-remediates all safe plan/documentation fixes before escalating only the
  remaining human decisions.
argument-hint: 'Path to the plan file to review, or omit to review the most recent plan.'
license: Apache-2.0
---

# Review Plan Implementation

Use this skill to audit an implementation plan before coding starts.

## Outcome

Produce a review report that:
- evaluates architecture, dependencies, security, resilience, and structure
- classifies findings as `BLOCKER`, `RISK`, or `OPTIMIZATION`
- includes a concrete corrective alternative for every finding
- auto-remediates all safe plan and documentation fixes immediately
- states whether the plan is ready to implement

This skill is fix-first, escalate-last. It must not wait for a follow-up prompt to
repair obvious plan defects, missing sections, weak task wording, or documentation
bookkeeping issues that it can safely resolve on its own.

## Procedure

### Step 0 — Load Context

1. Read [AGENTS.md](../../../AGENTS.md).
2. Read [docs/README.md](../../../docs/README.md).
3. Read the context documentation under [docs/context/](../../../docs/context/).
4. Read the plan file to be reviewed.
5. Read related active plans when overlap or dependency risk is possible.

### Step 1 — Architecture Review

Evaluate the plan's architecture against ESS conventions:
- Does the design maintain the observer-only constraint?
- Are async patterns and timeout requirements addressed?
- Are pydantic models used for all boundary validation?
- Is the FastAPI + APScheduler + tool layer architecture respected?
- Are new modules properly scoped within the existing structure?

### Step 2 — Dependency Review

For each new dependency or tool introduced:
- Is it compatible with Python 3.14+ and uv?
- Is it actively maintained (recent commits, release cadence)?
- Is the license compatible with Apache-2.0?
- Does it overlap with existing dependencies?
- Are there fewer, simpler alternatives?

### Step 3 — Security Review

- Are credentials handled through the config layer (never raw env vars)?
- Is the ABSK bearer token pattern used for Bedrock auth?
- Are external inputs validated at the boundary?
- Are subprocess calls properly sandboxed (no shell injection)?
- Is rate limiting in place for external API calls?

### Step 4 — Resilience Review

- Does every external call have a timeout?
- Are circuit breakers in place for tool adapters?
- Is graceful degradation handled (tool unavailable → continue)?
- Are retry strategies bounded (no infinite loops)?

### Step 5 — Completeness Review

- Does every phase have documentation and test coverage steps?
- Does the final phase include a `review-plan-phase` audit?
- Are task IDs consistent and traceable to frontmatter completion list?
- Is the scope focused (not over-engineered)?
- Are tasks concrete enough that an implementation agent can execute them without guessing?

### Step 6 — Classify Findings And Decide Remediation Ownership

For each finding, decide whether it is:
- `[agent]` — safe to fix now by editing the plan, review report, or docs index
- `[human]` — requires a product, architecture, operational, or scope decision

Default to `[agent]` unless changing it could alter intended scope or architecture.

Typical `[agent]` fixes:
- missing or weak task wording
- missing acceptance criteria or verification steps
- missing docs/test/review tasks
- stale `date_updated`, status text, or docs index references
- inconsistent task IDs or frontmatter bookkeeping

Typical `[human]` items:
- choosing between materially different architectures
- changing the product scope or supported workflows
- accepting operational risk, cost, or vendor lock-in tradeoffs

### Step 7 — Auto-Remediate Everything Safe

Before talking to the user:
1. Edit the plan to fix every `[agent]` finding.
2. Update `docs/README.md` or related plan metadata if the fix requires it.
3. Re-read the modified sections to confirm the fix closed the issue.
4. Recompute the verdict based on the remaining unresolved findings only.

Never stop early just because one `[human]` item exists. Fix everything else first.

### Step 8 — Produce Report

Write findings as a structured report:

```markdown
## Plan Review: <plan title>

**Verdict**: READY / NOT READY

### Findings

#### BLOCKER — <title>
**Owner**: [agent] / [human]
**Status**: resolved / unresolved
**What**: <description>
**Why it matters**: <impact>
**Alternative**: <concrete fix>

#### RISK — <title>
**Owner**: [agent] / [human]
**Status**: resolved / unresolved
**What**: <description>
**Likelihood**: <low/medium/high>
**Alternative**: <mitigation>

#### OPTIMIZATION — <title>
**Owner**: [agent] / [human]
**Status**: resolved / unresolved
**What**: <description>
**Benefit**: <improvement>
**Suggestion**: <concrete change>

### Auto-Remediation Summary
- <list of fixes applied automatically>

### Human Decisions Needed
- <only unresolved [human] items, each with recommendation>
```

Save the report to `docs/plans/review-reports/` with the naming convention:
`<plan-name>-review-<YYYY-MM-DD>-<4char>.md`

### Step 9 — Escalate Surgically When Needed

If unresolved `[human]` findings remain:
- present only those unresolved decisions to the user
- show the recommendation, tradeoff, and the exact plan area affected
- make it explicit that all safe remediations are already complete

If no unresolved `[human]` findings remain:
- report the plan as ready or not ready based on the remaining technical gaps
- do not ask for unnecessary confirmation
