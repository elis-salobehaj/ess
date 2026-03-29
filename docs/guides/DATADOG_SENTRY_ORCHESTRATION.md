# Datadog + Sentry Orchestration

## Purpose

This guide documents the Phase 3 ESS runtime path that generalises the shipped
Datadog-only agent loop into a staged Datadog + Sentry orchestrator.

The runtime remains observer-only. It watches, investigates, and reports. It
does not remediate.

## Runtime Shape

Each scheduler tick runs one health-check cycle:

1. Datadog-first triage runs through Bedrock tool calling.
2. Healthy services stop at triage and produce a Datadog-backed summary.
3. Degraded services enter a deeper investigation loop.
4. Sentry-enabled degraded services gain release-aware Sentry tools during that investigation loop.
5. If the model path degrades, deterministic Datadog fallback keeps the monitoring window alive.
6. If degraded investigation fails or never uses Sentry tools, deterministic release-aware Sentry follow-up still runs.

This keeps the Phase 1.5 unattended and inspectable guarantees while widening
the evidence available inside a single cycle.

## Triage And Investigation Split

The runtime now uses two logical Bedrock phases:

- `triage`: Datadog-only tools, aimed at deciding whether the cycle is healthy or degraded.
- `investigation`: Datadog plus release-aware Sentry tools for degraded services.

Both phases currently use Claude Sonnet 4.6. The split is architectural rather
than model-diverse at the moment. It exists so ESS can later tune cost or model
selection without changing the orchestration seam.

## Safety Rails

Phase 3 keeps the shipped safety rails intact:

- If Bedrock fails during triage, ESS falls back to deterministic Datadog triage.
- If Bedrock returns no tool calls during triage, ESS also falls back to deterministic Datadog triage.
- If degraded investigation fails, ESS does not fail the session. It falls back to deterministic release-aware Sentry follow-up for the affected service.
- If degraded investigation succeeds but never uses Sentry tools for a Sentry-enabled service, ESS still runs deterministic Sentry follow-up.

The effect is deliberate: Sentry is additive evidence, not a single point of
failure.

## Context Compaction

Longer reasoning loops can exhaust the context window. ESS now tracks an
approximate token budget per conversation. When the loop crosses the configured
threshold, it compacts older conversation turns into a short summary and keeps:

- the active system prompt
- the original cycle objective
- the most recent raw exchanges

ESS prefers a Bedrock-generated summary for this compaction step. If that fails,
it falls back to a local summary derived from earlier assistant text and tool
results.

## Trace Events

When debug tracing is enabled, `_local_observability/` now records the expanded
Phase 3 seam, including:

- `cycle.started`
- `bedrock.request`
- `bedrock.response`
- `tool.use`
- `tool.result`
- `fallback.started`
- `fallback.triggered`
- `investigation.started`
- `investigation.completed`
- `conversation.compacted`
- `cycle.completed`

This is the same instrumentation seam used by notification delivery and session
completion, so the expanded runtime remains inspectable without changing the
outer scheduler or API contracts.

## Scheduler And Escalation

Phase 3 does not add a second timing loop. APScheduler still owns when cycles
run. The monitoring session continues to track escalation state across ticks,
and Teams delivery decisions are still made from stored cycle results.

That means the orchestration changes are scoped to per-cycle reasoning, not to
scheduler ownership or notification timing.

## Deferred Scope

The following remain outside the live Phase 3 runtime path:

- Log Scout-backed raw log search
- richer multi-step reporting beyond the current Teams policy
- notification retry and failure backoff
- optional Sentry MCP backend work
