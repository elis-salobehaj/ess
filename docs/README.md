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

---

## Active Plans

| # | Plan | Status | Est. Hours |
|---|------|--------|------------|
| 1 | **ESS Master Plan** ([plan](plans/active/ess-eye-of-sauron-service.md)) | Backlog → Active | 80-120h |
| 2 | **Datadog Pup CLI Integration** ([plan](plans/active/ess-datadog-pup-integration.md)) | Backlog → Active | 25-35h |

### Deliverable Plans (Backlog)

| # | Plan | Status | Est. Hours |
|---|------|--------|------------|
| 3 | **Sentry Integration** ([plan](plans/backlog/ess-sentry-integration.md)) | Backlog | 20-30h |
| 4 | **Log Scout: Syslog Search Agent** ([plan](plans/backlog/ess-log-scout-syslog-agent.md)) | Backlog | 30-40h |

---

## Context Documentation

- [Architecture](context/ARCHITECTURE.md) — System design, component overview, data flow
- [Configuration](context/CONFIGURATION.md) — Environment variables, config loader, defaults
- [Workflows](context/WORKFLOWS.md) — Deploy trigger flow, health-check cycle, notification pipeline

## Design Decisions

- [Technology Decisions](designs/technology-decisions.md) — Pup CLI, Sentry REST, Log Scout, ReAct loop, Bedrock auth

## Guides

- [Getting Started](guides/GETTING_STARTED.md) — Local setup and first deploy trigger
- [Development](guides/DEVELOPMENT.md) — Commands, testing, linting, Docker

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
