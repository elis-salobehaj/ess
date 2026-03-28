# ESS — Eye of Sauron Service: Agent Operating Manual

## Mission

Agentic AI post-deploy monitoring service. ESS watches production deployments in
real time using Datadog, Sentry, and log search tools — orchestrated by an LLM
reasoning loop — and escalates to MS Teams when issues are detected.

ESS does NOT remediate. It watches, investigates, and reports.

## Stack Essentials

- **Runtime**: Python 3.14+
- **Package manager and runner**: uv
- **HTTP framework**: FastAPI + uvicorn
- **Validation and config**: pydantic v2, pydantic-settings v2
- **Async model**: asyncio with subprocess streaming (Pup CLI) and aiohttp (HTTP clients)
- **LLM**: AWS Bedrock converse API (current runtime: Claude Sonnet 4.6 for triage and investigation) via bearer token auth
- **Datadog**: Pup CLI (subprocess, 320+ commands, agent-mode JSON output)
- **Sentry**: REST API client (self-hosted, aiohttp-based)
- **Log search**: Remote ESS Log Scout agent (HTTP calls to syslog servers)
- **Scheduling**: APScheduler (AsyncIOScheduler)
- **Notifications**: MS Teams incoming webhook (Adaptive Cards)
- **Testing**: pytest and pytest-asyncio
- **Linting and formatting**: ruff
- **Containerisation**: Docker

## Critical Rules

1. **Use uv exclusively** for Python workflows: `uv sync`, `uv add`, `uv run pytest`,
   `uv run ruff check .`, `uv run uvicorn ...`. Do not introduce pip, poetry, or
   ad hoc virtualenv instructions.
2. **Validate at boundaries**: all HTTP request bodies, deploy trigger payloads,
   tool responses, and config values must use pydantic models. Never trust raw
   external data.
3. **Keep I/O async and bounded**: subprocess calls (Pup CLI), HTTP requests
   (Sentry, Log Scout, Teams), and LLM calls must use timeouts, concurrency
   limits (semaphores), and circuit breakers.
4. **Do not bypass config**: import settings from `src/config.py`. Outside
   `src/config.py`, raw environment access is forbidden: do not use
   `os.getenv()`, `os.environ`, `os.putenv()`, or `os.unsetenv()` in
   application code. If an SDK or subprocess needs environment variables,
   expose a typed helper on `ESSConfig` and consume that helper instead.
5. **Observer only**: ESS must never take remediation actions. No rollbacks, no
   restarts, no infrastructure changes. Observation and reporting only.
6. **Bedrock auth via ABSK bearer token**: keep Bedrock auth in
   `AWS_BEARER_TOKEN_BEDROCK` and route it through `src/config.py` only.
   `src/config.py` may sync the token into the runtime environment for
   botocore's native bearer-token support, but application code must never
   decode it into raw AWS access-key/secret pairs or store those credentials in
   config files.
7. **Update plan tracking** when work is plan-driven: adjust frontmatter,
   `date_updated`, [docs/README.md](docs/README.md), and the relevant plan file.
8. **Structured logging only**: JSON-formatted structured logs via structlog or
   Python logging JSON formatter. No print statements.

## Guides

- Start here: [docs/README.md](docs/README.md)
- Architecture: [docs/context/ARCHITECTURE.md](docs/context/ARCHITECTURE.md)
- Configuration: [docs/context/CONFIGURATION.md](docs/context/CONFIGURATION.md)
- Workflows: [docs/context/WORKFLOWS.md](docs/context/WORKFLOWS.md)
- Getting started: [docs/guides/GETTING_STARTED.md](docs/guides/GETTING_STARTED.md)
- Development: [docs/guides/DEVELOPMENT.md](docs/guides/DEVELOPMENT.md)

## Documentation Structure

- Navigation hub: [docs/README.md](docs/README.md)
- Context (architecture, config, workflows): [docs/context/](docs/context/)
- Design decisions: [docs/designs/](docs/designs/)
- Guides and onboarding: [docs/guides/](docs/guides/)
- Plans: [docs/plans/active/](docs/plans/active/), [docs/plans/backlog/](docs/plans/backlog/),
  [docs/plans/implemented/](docs/plans/implemented/)
- Review reports: [docs/plans/review-reports/](docs/plans/review-reports/)

Agents should prefer concise context docs in `docs/context/` before reading
full plans or historical reports.

## Agent Skills

Skills follow the [Agent Skills open standard](https://agentskills.io).
Located at `.agents/skills/<skill-name>/SKILL.md`.
Auto-discovered by Cursor, VSCode Copilot, OpenCode, and Antigravity.

Current repo skills include:
- `uv-python-project-conventions` for uv-native Python, FastAPI, testing, and ruff workflows
- `plan-implementation` for creating repo-aware implementation plans grounded in ESS docs and code
- `review-plan-implementation` for pre-implementation audits of plan architecture, dependencies, and risk
- `review-plan-phase` for implementation-vs-plan audits with remediation guidance
- `conventional-commits` for writing compliant commit messages scoped to this repo
- `scaffold-repo` for bootstrapping new repositories with full agent-ready structure (AGENTS.md, skills, docs tree)

## Plan Completion Gate

When work is driven by a markdown plan file, do not mark a phase, milestone, or
plan item complete until `review-plan-phase` has been run or the same review
standard has been applied manually.

For plan-driven work, agents must:
- compare the implementation against the governing plan item by item
- verify adherence to this file, including uv-only workflows, pydantic validation,
  async safety, observer-only constraint, and documentation maintenance
- inspect whether the implementation is complete rather than scaffolded or happy-path only
- verify tests are present and meaningful where the plan introduces new behavior
- verify all required documentation and plan bookkeeping updates were completed,
  especially [docs/README.md](docs/README.md) and the relevant plan file
- produce a report that distinguishes what was implemented correctly from what remains open

If the review identifies gaps, do not present the phase as complete. Auto-remediate
all safe items first, then escalate only the remaining architectural, operational,
or scope decisions that truly require human input.

## Active Work

Always check [docs/README.md](docs/README.md) for current priorities, recent reports,
and the latest plan status before starting implementation work.
