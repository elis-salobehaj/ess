# ESS Technology Decisions

Summary of technology choices made in the ESS master plan. See the
[master plan](../plans/active/ess-eye-of-sauron-service.md) for full evaluation
tables and rationale.

## Decision Summary

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Datadog integration | Pup CLI (subprocess) | 320+ commands, agent-mode JSON, replaces manual API client that caused 400/403s |
| 2 | Sentry integration | REST API (port from log-ai) | Simpler than MCP stdio, no Node.js dep, proven working. MCP as future upgrade |
| 3 | Log search | Remote Log Scout agent | Local ripgrep on syslog server, HTTP results to ESS. Avoids NFS/SSH |
| 4 | AI framework | Custom Bedrock tool loop | Simpler than LangGraph for the current first deliverable, with LangGraph still available if workflow complexity grows |
| 5 | HTTP framework | FastAPI + uvicorn | Async-native, pydantic built-in, OpenAPI docs |
| 6 | Job scheduler | APScheduler (AsyncIOScheduler) | Dynamic jobs, async, minimal config, perfect for "every N min for M min" |
| 7 | Notifications | MS Teams incoming webhook | Adaptive Cards, zero setup, webhook URL is the only secret |
| 8 | LLM auth | Bedrock bearer token (ABSK) via botocore native bearer support | Consistent with Vellum/Wellspring stack, single token to manage, no raw AWS key/secret material in config |
| 9 | First-ship runtime mode | Teams gated by env; local trace sink only in debug mode with an OpenTelemetry-aligned event model | Keeps the narrowed Datadog-only ship inspectable without making a local file trace the permanent observability surface |
| 10 | ESS self-telemetry backend | Datadog-first, with an OpenTelemetry Collector seam and Sentry kept out of the primary metrics path | Datadog supports Prometheus/OpenMetrics scraping and OTLP metrics/traces, while Sentry explicitly does not support OTLP metrics |
| 11 | Dashboard surface | Bun + Hono + React in a separate container, same mono-repo | Best fit for a self-hosted internal dashboard with low lock-in, explicit server seams, and a modern React UI without taking on unnecessary framework runtime complexity |

## LLM Model Selection

| Role | Model | Rationale |
|------|-------|-----------|
| Triage | Claude Sonnet 4.6 (`global.anthropic.claude-sonnet-4-6`) | Current runtime default; validated for live Bedrock tool-calling turns |
| Investigation | Claude Sonnet 4.6 (`global.anthropic.claude-sonnet-4-6`) | Reused for deeper Datadog investigation turns in the current runtime |
| Fallback | Deterministic Pup triage | Preserves the monitoring window if the Bedrock path fails or returns no tool calls |
