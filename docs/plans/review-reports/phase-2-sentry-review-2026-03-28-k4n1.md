---
title: "Phase 2 Sentry Review — REST Adapter, Resilience, and Tool Layer"
plan: docs/plans/backlog/ess-sentry-integration.md
master_plan: docs/plans/active/ess-eye-of-sauron-service.md
phase: "Phase S1, Phase S2, and Master Plan Phase 2 (Sentry-first slice)"
reviewer: agent (manual review-plan-phase standard)
date: 2026-03-28
status: complete
verdict: PASS WITH CAVEATS
---

# Phase 2 Sentry Review — REST Adapter, Resilience, and Tool Layer

Plans:
- [docs/plans/backlog/ess-sentry-integration.md](../backlog/ess-sentry-integration.md)
- [docs/plans/active/ess-eye-of-sauron-service.md](../active/ess-eye-of-sauron-service.md)

This review covers the implemented Sentry REST slice in the detailed Sentry plan
(Phases S1 and S2) and the corresponding Phase 2 items in the master plan. Safe
remediations were applied during the review before issuing the final verdict.

## Task Status

### Sentry Deliverable Plan — Phase S1

| Task | Expected Deliverable | Status | Notes |
|---|---|---|---|
| S1.1 | Async Sentry REST client ported into ESS | ✅ PASS | [src/tools/sentry_tool.py](../../../src/tools/sentry_tool.py) implements the aiohttp-based client with typed result envelopes |
| S1.2 | `query_issues` for unresolved issues | ✅ PASS | [src/tools/sentry_tool.py](../../../src/tools/sentry_tool.py) validates issue payloads into `SentryIssue`; [tests/test_sentry_tool.py](../../../tests/test_sentry_tool.py) covers the success path |
| S1.3 | `get_issue_details` with latest event merge | ✅ PASS | [src/tools/sentry_tool.py](../../../src/tools/sentry_tool.py) merges the latest event into `SentryIssueDetail`; [tests/test_sentry_tool.py](../../../tests/test_sentry_tool.py) covers the merge and validation path |
| S1.4 | Historical trace-search slice from the earlier REST-first review scope | ✅ PASS | This review originally covered a typed `search_traces` path in [src/tools/sentry_tool.py](../../../src/tools/sentry_tool.py); the shipped runtime has since been narrowed so Datadog owns traces and latency |
| S1.5 | Shared `ToolResult` normalisation for Sentry | ✅ PASS | [src/tools/normalise.py](../../../src/tools/normalise.py) converted issues, issue detail, and, at that time, trace results into the shared `ToolResult` contract; the current shipped path now keeps traces on Datadog |
| S1.6 | Unit tests with mocked HTTP responses | ✅ PASS | [tests/test_sentry_tool.py](../../../tests/test_sentry_tool.py) covers success, JSON failure, validation failure, retry, timeout, and circuit-breaker behavior |

### Sentry Deliverable Plan — Phase S2

| Task | Expected Deliverable | Status | Notes |
|---|---|---|---|
| S2.1 | Auth and self-hosted config via `ESSConfig` | ✅ PASS | [src/config.py](../../../src/config.py) owns `sentry_host`, `sentry_org`, auth token, timeout, retry, concurrency, and circuit-breaker settings |
| S2.2 | 429 handling and bounded rate-limit behavior | ✅ PASS | [src/tools/sentry_tool.py](../../../src/tools/sentry_tool.py) respects `Retry-After`, uses bounded retries, and enforces a semaphore via `SENTRY_MAX_CONCURRENT` |
| S2.3 | Circuit breaker for consecutive failures | ✅ PASS | [src/tools/sentry_tool.py](../../../src/tools/sentry_tool.py) opens the circuit after the configured threshold; [tests/test_sentry_tool.py](../../../tests/test_sentry_tool.py) covers the open-circuit path |
| S2.4 | Real-environment integration tests marked `@pytest.mark.integration` | ✅ PASS | [tests/test_sentry_tool.py](../../../tests/test_sentry_tool.py) included opt-in real-Sentry tests for issues, issue details, and, at review time, traces; the current shipped validation surface no longer includes Sentry traces |

### Master Plan — Phase 2

| Task | Expected Deliverable | Status | Notes |
|---|---|---|---|
| E2.1 | Datadog Pup CLI adapter | ✅ PASS | Existing Datadog D3 seam remains implemented in [src/agent/datadog_tools.py](../../../src/agent/datadog_tools.py) and validated by [tests/test_datadog_tools.py](../../../tests/test_datadog_tools.py) |
| E2.2 | Sentry REST adapter | ✅ PASS | [src/tools/sentry_tool.py](../../../src/tools/sentry_tool.py) satisfies the REST-first Sentry adapter slice with typed validation and resilience controls |
| E2.3 | Unified Sentry normalisation | ✅ PASS | [src/tools/normalise.py](../../../src/tools/normalise.py) provided the shared Sentry normalisation seam reviewed at the time; the current shipped contract now excludes Sentry traces |
| E2.4 | Sentry adapter and Bedrock tool-layer tests | ✅ PASS | Review remediation added [src/agent/sentry_tools.py](../../../src/agent/sentry_tools.py), [tests/test_sentry_tools.py](../../../tests/test_sentry_tools.py), and real-Sentry integration test stubs in [tests/test_sentry_tool.py](../../../tests/test_sentry_tool.py) |
| E2.5 | Sentry-first integration guide | ✅ PASS | Review remediation added [docs/guides/SENTRY_REST_INTEGRATION.md](../../guides/SENTRY_REST_INTEGRATION.md) and linked it from [docs/README.md](../../README.md) |
| E2.6 | Evaluate Sentry MCP follow-on path | ⚠️ PARTIAL | Intentionally deferred by the revised plan sequence; no MCP evaluation or backend toggle is implemented yet |
| E2.7 | Log Scout HTTP adapter | ⚠️ PARTIAL | Intentionally deferred until the Datadog + Sentry orchestrator is stable |

## Cross-Cutting Verification

| Requirement | Status | Notes |
|---|---|---|
| uv-only workflows | ✅ PASS | Validation used `uv run ruff check .` and `uv run pytest ...` only |
| Boundary validation | ✅ PASS | Sentry HTTP payloads, Bedrock tool inputs, and config values are all validated with pydantic in [src/tools/sentry_tool.py](../../../src/tools/sentry_tool.py), [src/agent/sentry_tools.py](../../../src/agent/sentry_tools.py), and [src/config.py](../../../src/config.py) |
| Async safety | ✅ PASS | The Sentry adapter uses explicit aiohttp timeouts, a semaphore, bounded 429 retries, and a circuit breaker |
| Observer-only constraint | ✅ PASS | The Sentry slice observes and reports only; no remediation behavior was introduced |
| Bedrock auth pattern | ✅ PASS | [src/config.py](../../../src/config.py) remains the only runtime environment boundary; no raw AWS key/secret decoding was introduced |
| Structured logging | ✅ PASS | [src/tools/sentry_tool.py](../../../src/tools/sentry_tool.py) uses structlog events for validation failure, retry, and circuit state rather than prints |
| Documentation/bookkeeping | ✅ PASS | The Sentry plan and adjacent docs were aligned during the review, and the new Sentry guide documents the implemented slice; the plan now lives in backlog because only deferred future work remains |
| No regressions | ✅ PASS | `uv run ruff check .`, `uv run pytest tests/test_sentry_tool.py tests/test_sentry_tools.py tests/test_config.py tests/test_datadog_tools.py`, and `uv run pytest tests/test_health_check_agent.py tests/test_main.py` all passed |

## Findings

### F1 — Sentry plan location and bookkeeping did not match active implementation status [RISK] [agent]

Detail:
- The Sentry deliverable plan still lived under `docs/plans/backlog/` even though its frontmatter status was already `active` and the docs index presented it as active work.
- The master plan and docs index still linked to the backlog path, which made the active-work navigation inconsistent.
- The Sentry plan frontmatter also had malformed `related_files` indentation.

Remediation:
- Moved the plan to [docs/plans/backlog/ess-sentry-integration.md](../backlog/ess-sentry-integration.md).
- Updated [docs/plans/active/ess-eye-of-sauron-service.md](../active/ess-eye-of-sauron-service.md) and [docs/README.md](../../README.md) to keep the plan index aligned with the implemented slice.
- Fixed the Sentry plan frontmatter and related-file list.

Status: ✅ Fixed

### F2 — Phase 2 lacked the documented Sentry Bedrock tool seam, guide, and integration-test scaffold [RISK] [agent]

Detail:
- The REST adapter and normalisation layer were present, but the Bedrock-facing Sentry tool definitions required by S3.1-S3.4 and master-plan E2.4 were still missing.
- There was no dedicated Sentry integration guide for the implemented REST-first slice.
- Real-environment Sentry integration tests were not yet defined, even as opt-in `@pytest.mark.integration` coverage.

Remediation:
- Added [src/agent/sentry_tools.py](../../../src/agent/sentry_tools.py) with Bedrock-compatible schemas, prompt fragment generation, validated dispatch, and `toolResult` message helpers.
- Added [tests/test_sentry_tools.py](../../../tests/test_sentry_tools.py) covering schemas, prompt fragments, validation, dispatch, and mock Bedrock tool-use round trips.
- Added opt-in real-Sentry integration tests to [tests/test_sentry_tool.py](../../../tests/test_sentry_tool.py).
- Added [docs/guides/SENTRY_REST_INTEGRATION.md](../../guides/SENTRY_REST_INTEGRATION.md) and linked it from [docs/README.md](../../README.md).
- Updated plan bookkeeping to mark S2.4, S3.1-S3.5, E2.4, and E2.5 complete.

Validation:
- `uv run ruff check .`
- `uv run pytest tests/test_sentry_tool.py tests/test_sentry_tools.py tests/test_config.py tests/test_datadog_tools.py`
- `uv run pytest tests/test_health_check_agent.py tests/test_main.py`

Status: ✅ Fixed

## Auto-Remediation Summary

Applied during this review:
- moved the Sentry deliverable plan from backlog to active and repaired its frontmatter
- implemented the missing Sentry Bedrock tool layer and mock end-to-end tool-use coverage
- added opt-in real-Sentry integration test coverage
- added a dedicated Sentry REST integration guide
- updated the master plan, docs index, and architecture context to reflect the implemented Sentry tool seam

## Unresolved Human Decisions

None.

## Final Verdict

PASS WITH CAVEATS

Phases S1 and S2 of the Sentry deliverable plan met the implemented review bar
at the time of this audit, and the master-plan Phase 2 Sentry slice was
documented and tested through the Bedrock tool seam. This report should be read
as historical context: the shipped runtime has since been narrowed so Datadog
owns traces and latency while Sentry remains focused on release-aware issue
follow-up. MCP evaluation (E2.6 / S4) and Log Scout integration (E2.7) were
still open at the time of this review.