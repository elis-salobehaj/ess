# ESS Documentation

**For AI Agents & Developers**: This is your primary documentation reference.

---

## Quick Start

### For AI Agents

1. **Check current work** → [`plans/active/`](plans/active/)
2. **Understand the stack** → [`context/ARCHITECTURE.md`](context/ARCHITECTURE.md)
3. **See configuration** → [`context/CONFIGURATION.md`](context/CONFIGURATION.md)
4. **Follow workflows** → [`context/WORKFLOWS.md`](context/WORKFLOWS.md)

### For Developers

- **New to the project?** → [`guides/GETTING_STARTED.md`](guides/GETTING_STARTED.md)
- **Daily development?** → [`guides/DEVELOPMENT.md`](guides/DEVELOPMENT.md)

## Current Runtime

- Datadog-first Bedrock triage and deeper Bedrock investigation are live.
- The runtime now exposes release-aware Sentry tools during degraded-service investigation and preserves deterministic Sentry follow-up as a safety rail.
- Teams delivery now uses richer Adaptive Cards, bounded retries for retryable webhook failures, and a default `real-world` delivery mode that posts only operationally relevant cards on the current webhook transport.
- ESS now ships a Prometheus-style `/metrics` endpoint, checked-in compose and GitLab CI deployment artifacts, and a production deployment guide for the current containerised runtime.
- Native `AWS_BEARER_TOKEN_BEDROCK` auth is the supported Bedrock path.
- Claude Sonnet 4.6 is the current runtime model for both triage and deeper investigation turns.
- `_local_observability/` holds session-scoped trace artifacts, including Bedrock request/response, tool, and conversation-compaction events, plus the shared debug log when debug tracing is enabled.
- Live local validation has covered the 2-minute smoke payload, a healthy 10-minute harness run, healthy 15-minute and 30-minute runs on the real Bedrock path, and a degraded harness run through the Datadog-to-Sentry branch.
- The checked-in `ess-harness` CLI now covers both `live` runs against an already running ESS instance and a separate `degraded` validation command.
- Log Scout and the deferred Phase 6 expansion paths remain target components, not completed runtime integrations.

---

## Active Plans

| # | Plan | Status | Est. Hours |
|---|------|--------|------------|
| 1 | **ESS Master Plan** ([plan](plans/active/ess-eye-of-sauron-service.md)) | Phase 1 ✅ — Phase 1.5 implemented, review-complete, and validated through 2m, 10m, 15m, and 30m live runs; Phase 2 release-aware Sentry work is implemented; Phase 3 orchestration is review-complete; Phase 4 notification/reporting is review-complete and validated; Phase 5 deployment, observability, and hardening is now review-complete | 80-120h |

### Implemented Plans

| # | Plan | Status | Est. Hours |
|---|------|--------|------------|
| 2 | **Datadog Pup CLI Integration** ([plan](plans/implemented/ess-datadog-pup-integration.md)) | Implemented | 25-35h |

### Deliverable Plans (Backlog)

| # | Plan | Status | Est. Hours |
|---|------|--------|------------|
| 3 | **Sentry Integration** ([plan](plans/backlog/ess-sentry-integration.md)) | Phases S1-S4 are complete and review-complete; only deferred Phase S5 future work remains in backlog | 20-30h |
| 4 | **Log Scout: Syslog Search Agent** ([plan](plans/backlog/ess-log-scout-syslog-agent.md)) | Backlog | 30-40h |

---

## Context Documentation

- [Architecture](context/ARCHITECTURE.md) — Current runtime, target architecture, Mermaid diagrams, and component responsibilities
- [Configuration](context/CONFIGURATION.md) — Environment variables, config helpers, defaults, and native bearer-token auth notes
- [Workflows](context/WORKFLOWS.md) — Deploy trigger flow, current Datadog Bedrock loop, fallback path, and notification pipeline

## Design Decisions

- [Technology Decisions](designs/technology-decisions.md) — Pup CLI, Sentry REST, Log Scout, ReAct loop, Bedrock auth, telemetry, and dashboard stack
- [ESS Dashboard Architecture](designs/dashboard-architecture.md) — Python vs TypeScript dashboard options, container layout, and rollout plan
- [ESS Telemetry Backend Evaluation](designs/otlp-metrics-telemetry-evaluation.md) — evaluation of Sentry, Datadog, OTLP, Prometheus metrics, and the recommended ESS observability backend path

## Guides

- [Getting Started](guides/GETTING_STARTED.md) — Local setup and first deploy trigger
- [Development](guides/DEVELOPMENT.md) — Commands, testing, linting, Docker, and the Typer-based harness CLI
- [Deployment](guides/DEPLOYMENT.md) — Container runtime requirements, compose usage, GitLab trigger template, metrics, and production observability notes
- [Datadog + Sentry Orchestration](guides/DATADOG_SENTRY_ORCHESTRATION.md) — Phase 3 staged triage/investigation runtime, deterministic safety rails, and compaction behavior
- [Datadog-Only Unattended and Inspectable Ship](guides/DATADOG_ONLY_UNATTENDED_AND_INSPECTABLE_SHIP.md) — First-deliverable runtime modes, trace behavior, and operator checklist
- [Datadog Agent Tools](guides/DATADOG_AGENT_TOOLS.md) — Bedrock tool schemas, Bedrock turn handling, prompt fragments, and Pup dispatch helpers
- [Sentry REST Integration](guides/SENTRY_REST_INTEGRATION.md) — REST adapter behavior, Bedrock tool schemas, ToolResult mapping, and integration-test entry points
- [Trigger End-to-End Datadog Pup Integration](guides/TRIGGER_END_TO_END_DATADOG_PUP_INTEGRATION.md) — Live local smoke, `live` harness, `degraded` harness, and extended-window validation against the current runtime
- [Teams Channel Integration](guides/TEAMS_CHANNEL_INTEGRATION.md) — Incoming Webhook setup, delivery-mode behavior, harness review batches, and webhook transport limits for ESS notifications

## Recent Review Reports

- [Phase 5 Review](plans/review-reports/phase-5-review-2026-03-29-p4h8.md) — Audit of the deployment, self-observability, and hardening slice; compose and GitLab CI artifacts, `/metrics`, end-to-end coverage, and the deployment guide now satisfy master-plan E5.1-E5.7
- [Phase 4 Review](plans/review-reports/phase-4-review-2026-03-29-k7p2.md) — Audit of the richer Teams notification/reporting slice; webhook retries, correlated investigation follow-up cards, tests, docs, and plan bookkeeping now satisfy master-plan E4.1-E4.6
- [Phase 3 Review](plans/review-reports/phase-3-review-2026-03-29-k3f1.md) — Audit of the Datadog + Sentry orchestration slice; the staged triage/investigation runtime, compaction behavior, docs, and validation now satisfy master-plan E3.1-E3.7
- [Master Plan Review — Phase 3 Readiness](plans/review-reports/ess-eye-of-sauron-service-review-2026-03-29-h2q6.md) — Pre-implementation audit of the master plan after Phase 1.5 and Phase 2 completion; Phase 3+ work is now narrowed to the supported Bedrock-first, observer-only path and the stale production examples were remediated during review
- [S4 / Phase 2 Plan Review](plans/review-reports/ess-sentry-integration-plan-review-2026-03-29-r6p1.md) — Pre-implementation-quality audit of the S4 and Phase 2 plan surfaces after safe documentation remediations; the governing plans and active docs are now aligned for the release-aware Sentry slice
- [Phase S4 Review](plans/review-reports/phase-s4-review-2026-03-29-q8m4.md) — Audit of the release-aware v1 runtime slice; Datadog-first Sentry follow-up, tests, docs, and plan bookkeeping are now complete for S4 and master-plan E2.7
- [Phase 2 Sentry Review](plans/review-reports/phase-2-sentry-review-2026-03-28-k4n1.md) — Post-remediation audit of Sentry plan Phases S1/S2 and the master-plan Phase 2 slice; Bedrock tool layer, docs, and integration-test scaffolding are now in place while MCP and Log Scout remain deferred
- [Master Plan Review](plans/review-reports/ess-eye-of-sauron-service-review-2026-03-28-r8n5.md) — Pre-implementation audit of the revised Sentry-first master-plan sequence; auto-remediated plan consistency and architecture issues before Phase 2 / Phase 3 expansion begins
- [Phase 1.5 Final Follow-Up Review](plans/review-reports/phase-1-5-review-2026-03-28-m2v7.md) — Final audit after the Phase 1.5 cleanup and consistency pass, now updated with the successful 30-minute validation closeout
- [Phase 1.5 Review](plans/review-reports/phase-1-5-review-2026-03-28-r4k8.md) — Post-remediation audit of the narrowed Datadog-only unattended and inspectable ship

---

## Plans Directory Structure

```
docs/plans/
├── active/          # Currently being implemented
├── backlog/         # Approved but not yet started
├── implemented/     # Completed plans (archive)
└── review-reports/  # Phase review audit reports
```

---

## For AI Coding Agents

**When asked to implement a feature:**
1. Check `docs/plans/active/` for relevant plan
2. Read linked files in frontmatter `related_files`
3. Read context docs in `docs/context/` for architecture and conventions
4. Reference [AGENTS.md](../AGENTS.md) for critical rules

**When generating reports:**
- Save to `docs/plans/review-reports/` for plan phase audits
