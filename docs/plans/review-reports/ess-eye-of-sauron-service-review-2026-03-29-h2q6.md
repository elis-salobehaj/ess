---
title: "Master Plan Review — Phase 3 Readiness"
plan: docs/plans/active/ess-eye-of-sauron-service.md
reviewer: agent (manual review-plan-implementation standard)
date: 2026-03-29
status: complete
verdict: READY
---

## Plan Review: ESS — Eye of Sauron Service: Agentic Post-Deploy Monitor

**Verdict**: READY

### Findings

#### BLOCKER — Production trigger examples no longer matched the current Sentry-enabled payload contract
**Owner**: [agent]
**Status**: resolved
**What**: The master plan still showed a single-service deploy signature in the sequence diagram and a GitLab CI example that omitted `deployment.release_version` and `services[].sentry_project_id`.
**Why it matters**: Those examples are likely to be copied into real deployment pipelines. Left unchanged, they would have produced invalid Sentry-enabled deploy payloads just before the next production-impact implementation phase.
**Alternative**: Update the diagram and CI template so they reflect the multi-service payload shape and the required release-aware Sentry fields.

#### RISK — Decision 4 widened runtime scope beyond the supported Bedrock-first path
**Owner**: [agent]
**Status**: resolved
**What**: The framework decision still described Anthropic and OpenAI provider fallbacks even though the documented ESS runtime standard is Bedrock converse with bearer-token auth.
**Likelihood**: medium
**Alternative**: Narrow the implementation guidance to direct Bedrock converse calls and treat any future provider abstraction as a later evaluation rather than implied Phase 3 scope.

#### RISK — Observer-only boundaries were weakened by rollback-style reporting guidance
**Owner**: [agent]
**Status**: resolved
**What**: The Phase 3 report guidance, Phase 4 card examples, and workflow summary still suggested rollback recommendations.
**Likelihood**: medium
**Alternative**: Keep recommendations limited to investigation, monitoring, and escalation guidance so the plan stays aligned with the observer-only rule and the resolved decision to defer rollback recommendations.

#### RISK — Phase 3 generalisation did not explicitly preserve current runtime fallback and inspectability guarantees
**Owner**: [agent]
**Status**: resolved
**What**: The orchestrator section described the multi-tool expansion but did not state that the shipped deterministic Datadog fallback, additive Sentry behaviour, and Phase 1.5 instrumentation seam must survive the refactor.
**Likelihood**: medium
**Alternative**: Add explicit implementation requirements that keep the current fallback path, degrade Sentry failures to Datadog-only reporting, and preserve the trace and notification event seam.

#### OPTIMIZATION — Deployment and notification examples lagged behind repo conventions
**Owner**: [agent]
**Status**: resolved
**What**: The Docker example still used `pip install uv`, and the Teams publisher example embedded a timeout literal instead of a config-owned setting.
**Benefit**: Reduces accidental divergence from repo standards during the next implementation phase.
**Suggestion**: Use an official uv installation path in the Docker example and keep Teams timeout guidance explicitly config-owned on `ESSConfig`.

### Auto-Remediation Summary
- Updated the master-plan sequence diagram and GitLab CI example to match the current multi-service, release-aware Sentry payload contract.
- Narrowed the orchestration decision text to the supported Bedrock converse path and removed implied non-Bedrock provider fallback scope.
- Added explicit Phase 3 implementation requirements to preserve deterministic Datadog fallback, additive Sentry behaviour, and the existing instrumentation seam.
- Removed rollback-style recommendation language from the master plan and the workflow doc so the review stays aligned with the observer-only constraint.
- Updated the Docker and Teams publisher examples to follow repo conventions for uv-native workflows and config-owned timeout settings.

### Human Decisions Needed
- None.

The master plan is now ready to guide Phase 3 work. Its next-step scope is clear: evolve the shipped Datadog-first runtime into a Bedrock-first Datadog + Sentry orchestrator without widening into unsupported provider work, remediation behavior, or premature Log Scout expansion.