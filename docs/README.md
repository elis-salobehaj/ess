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

- Datadog-only Bedrock tool-calling loop is live.
- Native `AWS_BEARER_TOKEN_BEDROCK` auth is the supported Bedrock path.
- Claude Sonnet 4.6 is the current runtime model for both triage and deeper investigation turns.
- `_local_observability/` holds session-scoped trace artifacts and the shared debug log when debug tracing is enabled.
- Live local validation has covered the 2-minute smoke payload plus healthy 15-minute and 30-minute runs on the real Bedrock path.
- Sentry and Log Scout remain documented target components, not current runtime integrations.

---

## Active Plans

| # | Plan | Status | Est. Hours |
|---|------|--------|------------|
| 1 | **ESS Master Plan** ([plan](plans/active/ess-eye-of-sauron-service.md)) | Phase 1 ✅ — Phase 1.5 implemented, review-complete, and validated through 2m, 15m, and 30m live runs | 80-120h |

### Implemented Plans

| # | Plan | Status | Est. Hours |
|---|------|--------|------------|
| 2 | **Datadog Pup CLI Integration** ([plan](plans/implemented/ess-datadog-pup-integration.md)) | Implemented | 25-35h |

### Deliverable Plans (Backlog)

| # | Plan | Status | Est. Hours |
|---|------|--------|------------|
| 3 | **Sentry Integration** ([plan](plans/backlog/ess-sentry-integration.md)) | Backlog | 20-30h |
| 4 | **Log Scout: Syslog Search Agent** ([plan](plans/backlog/ess-log-scout-syslog-agent.md)) | Backlog | 30-40h |

---

## Context Documentation

- [Architecture](context/ARCHITECTURE.md) — Current runtime, target architecture, Mermaid diagrams, and component responsibilities
- [Configuration](context/CONFIGURATION.md) — Environment variables, config helpers, defaults, and native bearer-token auth notes
- [Workflows](context/WORKFLOWS.md) — Deploy trigger flow, current Datadog Bedrock loop, fallback path, and notification pipeline

## Design Decisions

- [Technology Decisions](designs/technology-decisions.md) — Pup CLI, Sentry REST, Log Scout, ReAct loop, Bedrock auth

## Guides

- [Getting Started](guides/GETTING_STARTED.md) — Local setup and first deploy trigger
- [Development](guides/DEVELOPMENT.md) — Commands, testing, linting, Docker
- [Datadog-Only Unattended and Inspectable Ship](guides/DATADOG_ONLY_UNATTENDED_AND_INSPECTABLE_SHIP.md) — First-deliverable runtime modes, trace behavior, and operator checklist
- [Datadog Agent Tools](guides/DATADOG_AGENT_TOOLS.md) — Bedrock tool schemas, Bedrock turn handling, prompt fragments, and Pup dispatch helpers
- [Trigger End-to-End Datadog Pup Integration](guides/TRIGGER_END_TO_END_DATADOG_PUP_INTEGRATION.md) — Live local smoke and extended-window validation against the current runtime
- [Teams Channel Integration](guides/TEAMS_CHANNEL_INTEGRATION.md) — Incoming Webhook setup and validation for ESS cycle and summary alerts

## Recent Review Reports

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
