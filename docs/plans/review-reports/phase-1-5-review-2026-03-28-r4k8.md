---
title: "Phase 1.5 Review — Self-Unattended and Inspectable Datadog Deliverable"
plan: docs/plans/active/ess-eye-of-sauron-service.md
phase: "Phase 1.5 — Self-Unattended and Inspectable Datadog Deliverable"
reviewer: agent (review-plan-phase skill)
date: 2026-03-28
status: complete
verdict: PASS WITH CAVEATS
---

# Phase 1.5 Review — Self-Unattended and Inspectable Datadog Deliverable

Plan: [docs/plans/active/ess-eye-of-sauron-service.md](../active/ess-eye-of-sauron-service.md)

## Task Status

| Task | Expected Deliverable | Status | Notes |
|---|---|---|---|
| E15.1 | Datadog-only Bedrock tool loop with deterministic Pup fallback | ✅ PASS | [src/agent/health_check_agent.py](../../../src/agent/health_check_agent.py) runs the Datadog-only Bedrock loop, records observable execution events, and falls back to deterministic Pup triage without breaking the monitoring window |
| E15.2 | Debug-gated local trace sink with OpenTelemetry-aligned event model | ✅ PASS | [src/agent/trace.py](../../../src/agent/trace.py), [src/main.py](../../../src/main.py), and [tests/test_agent_trace.py](../../../tests/test_agent_trace.py) now provide a typed instrumentation seam, gated JSONL output, and trace coverage for cycle, fallback, notification, and completion events; the live smoke run produced session-scoped trace files under `_local_observability/` |
| E15.3 | Teams mode gate and real completion callback | ✅ PASS | [src/main.py](../../../src/main.py), [src/notifications/teams.py](../../../src/notifications/teams.py), and [src/scheduler.py](../../../src/scheduler.py) now implement config-gated Teams delivery, bounded async webhook posting, real completion handling, and per-cycle result callbacks |
| E15.4 | Warning, critical, and end-of-window notification policy | ✅ PASS | [src/notifications/teams.py](../../../src/notifications/teams.py) implements immediate `CRITICAL`, second consecutive `WARNING`, and end-of-window summary decisions; [tests/test_notifications.py](../../../tests/test_notifications.py) and [tests/test_main.py](../../../tests/test_main.py) cover those paths |
| E15.5 | Realistic shipping validation for 30-60 minute Datadog-only monitoring | ⚠️ PARTIAL | A live end-to-end smoke run using [docs/examples/triggers/example-service-e2e.json](../../../docs/examples/triggers/example-service-e2e.json) completed successfully against live Datadog and the session API, a 15-minute run also completed on the live Bedrock branch, and the remaining open item is longer-window 30-60 minute validation using local ignored payloads |
| E15.6 | Datadog-only unattended and inspectable ship documentation | ✅ PASS | [docs/guides/DATADOG_ONLY_UNATTENDED_AND_INSPECTABLE_SHIP.md](../../../docs/guides/DATADOG_ONLY_UNATTENDED_AND_INSPECTABLE_SHIP.md), [docs/guides/TRIGGER_END_TO_END_DATADOG_PUP_INTEGRATION.md](../../../docs/guides/TRIGGER_END_TO_END_DATADOG_PUP_INTEGRATION.md), and the example-service trigger fixture document the narrowed ship path and operator workflow |
| E15.7 | review-plan-phase audit for the narrowed first ship | ✅ PASS | This report closes the phase review requirement and records the post-remediation implementation state, validation evidence, and remaining caveats |

## Cross-Cutting Verification

| Requirement | Status | Notes |
|---|---|---|
| uv-only workflows | ✅ PASS | Validation used `uv run pytest`, `uv run ruff check .`, and `uv run uvicorn ...` only |
| Boundary validation | ✅ PASS | Trigger payloads, notification payloads, trace events, tool schemas, and settings remain Pydantic-validated through [src/models.py](../../../src/models.py), [src/notifications/teams.py](../../../src/notifications/teams.py), and [src/config.py](../../../src/config.py) |
| Async safety | ✅ PASS | Pup calls remain bounded by the existing semaphore/timeout path and Teams delivery uses explicit async HTTP timeouts in [src/notifications/teams.py](../../../src/notifications/teams.py) |
| Observer-only constraint | ✅ PASS | The runtime still performs observation and reporting only; no remediation paths were introduced |
| Bedrock auth pattern | ✅ PASS | [src/config.py](../../../src/config.py) routes `AWS_BEARER_TOKEN_BEDROCK` through config-owned native bearer-token wiring; the remaining live issue is the Datadog infrastructure-health Pup failure seen during the 15-minute validation run, not a Bedrock auth bypass |
| Structured logging | ✅ PASS | The phase continues to use structured logging and the new trace seam adds structured JSONL events rather than ad hoc prints |
| Documentation/bookkeeping | ✅ PASS | [docs/README.md](../../../docs/README.md), [docs/context/ARCHITECTURE.md](../../../docs/context/ARCHITECTURE.md), [docs/context/CONFIGURATION.md](../../../docs/context/CONFIGURATION.md), [docs/context/WORKFLOWS.md](../../../docs/context/WORKFLOWS.md), and the governing plan now reflect the implemented Phase 1.5 runtime |
| No regressions | ✅ PASS | `uv run pytest -q` and `uv run ruff check .` pass after the final remediation batch |

## Findings

### F1 — Teams and summary delivery were still scaffolded instead of phase-ready [BLOCKER] [agent]

Detail:
- The initial Phase 1.5 runtime still treated Teams completion reporting as a stub, which left E15.3 and E15.4 materially incomplete.
- That blocked unattended operation even though inspectable Datadog monitoring already existed.

Remediation:
- Added the bounded Teams publisher and notification policy in [src/notifications/teams.py](../../../src/notifications/teams.py).
- Added per-cycle result callbacks in [src/scheduler.py](../../../src/scheduler.py).
- Replaced the stub completion path in [src/main.py](../../../src/main.py) with real summary delivery and trace-backed notification events.
- Added focused coverage in [tests/test_notifications.py](../../../tests/test_notifications.py) and [tests/test_main.py](../../../tests/test_main.py).

Validation:
- Covered by targeted tests and included in the final full-suite pass.

Status: ✅ Fixed

### F2 — Documented `ESS_*` Phase 1.5 settings were not actually honoured in live runtime [RISK] [agent]

Detail:
- The first live smoke run showed that `ESS_DEBUG_TRACE_ENABLED=true` in `config/.env` did not produce a trace file.
- Root cause was that the Phase 1.5 settings fields in [src/config.py](../../../src/config.py) lacked explicit aliases for the documented `ESS_*` environment variable names.

Remediation:
- Bound `teams_enabled`, `teams_timeout_seconds`, `debug_trace_enabled`, and `agent_trace_path` to their documented `ESS_*` aliases in [src/config.py](../../../src/config.py).
- Added `populate_by_name=True` so direct test overrides still work cleanly.
- Isolated unit tests from the repository `config/.env` via `_env_file=None` in [tests/conftest.py](../../../tests/conftest.py), [tests/test_config.py](../../../tests/test_config.py), and [tests/test_pup_tool.py](../../../tests/test_pup_tool.py).

Validation:
- The rerun live smoke session wrote session-scoped `_local_observability/agent_trace_<job_id>.jsonl` output with `cycle.started`, `bedrock.request`, fallback, `notification.skipped`, and `session.completed` events.

Status: ✅ Fixed

### F3 — Extended validation still needs a longer window and a flaky Pup call follow-up [RISK] [human]

Detail:
- The remediated smoke and 15-minute runs both exercised the live Bedrock branch successfully.
- The 15-minute run still finished with `UNKNOWN` because one `datadog.infrastructure_health` Pup call failed during cycle 3.
- The full 30-60 minute validation window remains open.

Recommendation:
- Investigate the failing `datadog.infrastructure_health` Pup path, then rerun a 30- or 60-minute validation payload built from [docs/examples/triggers/example-service-e2e.json](../../../docs/examples/triggers/example-service-e2e.json) using a local copy under `_local_observability/triggers/` to close E15.5.

Status: ⚠️ Open

## Auto-Remediation Summary

Applied during review:
- implemented the real Teams notification runtime for cycle alerts and end-of-window summaries
- added the scheduler result callback seam needed for per-cycle notification policy evaluation
- added focused unit coverage for trace, notification policy, callback delivery, and config alias behavior
- fixed the live-runtime config alias bug that prevented documented `ESS_*` settings from taking effect
- updated the context docs, the Datadog-only ship guide, the end-to-end trigger guide, and example payloads
- reran live Datadog smoke validation after remediation and confirmed trace-backed session completion

## Unresolved Human Decisions

- Run a 30-60 minute local validation payload after addressing the flaky `datadog.infrastructure_health` Pup call if you want to close E15.5.

## Final Verdict

PASS WITH CAVEATS

Phase 1.5 is now review-complete for the implemented scope:
- the Datadog-only agent loop is inspectable,
- the debug trace seam is real and reusable,
- the minimal Teams policy is implemented and tested,
- and the documentation/bookkeeping are aligned.

The remaining caveat is operational rather than architectural: E15.5 still needs a longer live validation window and a follow-up on the flaky `datadog.infrastructure_health` Pup path.