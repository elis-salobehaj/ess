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
| 4 | AI framework | Custom ReAct loop | Simpler than LangGraph, sufficient for triage→investigate→report pattern |
| 5 | HTTP framework | FastAPI + uvicorn | Async-native, pydantic built-in, OpenAPI docs |
| 6 | Job scheduler | APScheduler (AsyncIOScheduler) | Dynamic jobs, async, minimal config, perfect for "every N min for M min" |
| 7 | Notifications | MS Teams incoming webhook | Adaptive Cards, zero setup, webhook URL is the only secret |
| 8 | LLM auth | Bedrock bearer token (ABSK) | Consistent with Vellum/Wellspring stack, single token to manage |
| 9 | First-ship runtime mode | Teams gated by env; local trace sink only in debug mode with an OpenTelemetry-aligned event model | Keeps the narrowed Datadog-only ship inspectable without making a local file trace the permanent observability surface |

## LLM Model Selection

| Role | Model | Rationale |
|------|-------|-----------|
| Triage | Claude Haiku 4.5 | Fast, cheap, sufficient for tool calls and anomaly detection |
| Investigation | Claude Sonnet 4.6 (`global.anthropic.claude-sonnet-4-6`) | Deep reasoning for root-cause analysis |
| Fallback | OpenAI GPT-4.1-mini | If Bedrock is unavailable |
