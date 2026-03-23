---
name: plan-implementation
description: >
  Produces thorough, repo-aware implementation plans for ESS. Gathers deep
  context from AGENTS.md, docs/, and source code before proposing architecture,
  tech stack decisions, tradeoffs, and phased implementation steps. Produces a
  markdown plan file consistent with existing plan conventions. Asks the user
  clarifying questions when decisions impact architecture, structure, or tech stack
  before finalizing.
argument-hint: 'Describe the feature, change, or capability to plan. Include any constraints or preferences.'
license: Apache-2.0
---

# Plan Implementation

Use this skill to produce a detailed, actionable implementation plan for a new
feature, capability, or architectural change in ESS. The plan must be grounded
in the actual codebase — not generic advice — and must follow the conventions
established by existing plans in this repository.

## Outcome

Produce a markdown plan file saved to `docs/plans/backlog/` (default) or
`docs/plans/active/` (when the user explicitly requests active, or when no
active plans exist) that:
- is grounded in the current architecture, tech stack, and conventions of ESS
- evaluates technology choices and tradeoffs when the feature introduces new tools,
  libraries, or patterns
- proactively suggests superior alternatives when a clearly better option exists
- proposes a phased implementation with concrete, checkable tasks per phase
- identifies affected files and systems
- includes a YAML frontmatter block consistent with existing plans
- includes a completion checklist with unchecked task IDs that match the phase structure
- places architectural diagrams and plan overview at the top of the document
- follows the structure, depth, and conventions of existing plans in `docs/plans/`
- gives every task a concrete deliverable and a clear verification target

The plan must not be finalized without first presenting key decisions and tradeoffs
to the user for feedback, when those decisions impact the architecture or structure
of the repository.

## When To Use

Use this skill for:
- planning a new feature or capability before implementation begins
- planning an architectural change, refactor, or migration
- evaluating and deciding on new libraries, tools, or runtime changes
- breaking down a large initiative into phased, reviewable implementation steps

Do not use this skill for:
- implementing code directly — this skill produces a plan, not code
- reviewing an existing implementation against a plan — use `review-plan-phase` instead
- minor bug fixes or one-line changes that do not warrant a plan

## Procedure

### Phase 1 — Deep Context Gathering

Before any analysis or proposal work, build a thorough understanding of the
repository by reading primary sources in this order:

1. Read [AGENTS.md](../../../AGENTS.md) in full.
2. Read [docs/README.md](../../../docs/README.md) to understand the documentation index.
3. Read context documentation under `docs/context/`:
   - [ARCHITECTURE.md](../../../docs/context/ARCHITECTURE.md)
   - [CONFIGURATION.md](../../../docs/context/CONFIGURATION.md)
   - [WORKFLOWS.md](../../../docs/context/WORKFLOWS.md)
4. Read relevant design documents in `docs/designs/` if the feature touches areas
   with existing decisions.
5. Read active plans under `docs/plans/active/` to understand in-flight work.
6. If the feature touches existing source code, read the relevant source files.
7. If the feature introduces external dependencies, research their compatibility
  with the ESS stack (Python 3.14+, asyncio, FastAPI, pydantic, uv).
8. Do not ask the user questions that can be answered from the repo, attached plans,
  or library documentation.

### Phase 2 — Feature Analysis and Scoping

1. Restate the feature to confirm understanding.
2. Identify the blast radius: affected modules, new files, conflicts with active plans.
3. Identify constraints from AGENTS.md: uv-only, pydantic validation, async safety,
   observer-only constraint, Bedrock auth, structured logging.

### Phase 3 — Technology Evaluation and Tradeoffs

When the feature introduces new dependencies or patterns:
1. List candidate approaches or libraries.
2. Evaluate against ESS stack constraints.
3. Produce a decision table when multiple choices exist.
4. Sketch architecture with Mermaid diagrams for multi-component changes.

Skip this phase for purely behavioral changes with no new dependencies.

### Phase 4 — User Feedback Loop

Present decisions and tradeoffs to the user when:
- A decision impacts architecture or module structure
- Multiple viable approaches exist with different tradeoffs
- You have a recommended alternative worth discussing

When asking for input:
- ask only the minimum set of questions needed to unblock architectural decisions
- provide a recommendation, the tradeoff, and the consequence of each option
- continue drafting everything else that is already clear

### Phase 5 — Plan Drafting

Produce the plan file with:

#### YAML Frontmatter
```yaml
---
title: "<Descriptive Plan Title>"
status: backlog
priority: <high | medium | low>
estimated_hours: <range>
created: <YYYY-MM-DD>
date_updated: <YYYY-MM-DD>
related_files:
  - <files to create or modify>
tags:
  - <relevant tags>
completion:
  - "# Phase X1 — Phase Title"
  - [ ] X1.1 Task description
---
```

#### Plan Body Structure
1. **Title** (H1)
2. **Architecture diagram** (Mermaid, when applicable) — must be first after title
3. **Executive Summary** — 3-5 sentences
4. **Technology Decisions** (when Phase 3 produced decisions)
5. **Problem Statement or Goal**
6. **Directory Structure** (when new files introduced)
7. **Phased Implementation** — per-phase sections with concrete tasks
8. **Open Questions** (if any remain)

#### Phase Design Conventions
- Consistent task ID scheme: `<Prefix><Phase>.<Task>` (e.g., `P1.1`, `D2.3`)
- Order phases by dependency
- Every phase must end with doc update and test coverage steps
- Final phase includes a `review-plan-phase` audit step
- Every task must reference the file, module, or artifact it changes whenever feasible
- Avoid vague tasks like "implement integration" or "add tests" without scope and acceptance criteria

### Phase 6 — Save and Update Index

1. Save the plan to `docs/plans/backlog/<kebab-case-name>.md`
2. Update [docs/README.md](../../../docs/README.md) with a link to the new plan
3. Confirm success to the user

## Quality Bar

Before declaring the plan complete, verify:
- the plan is specific enough that an implementation agent can execute it without guessing
- open questions are explicit and limited to true decisions, not missing research
- phases are sequenced so earlier work enables later work
- testing, docs, and review gates are present in every relevant phase
