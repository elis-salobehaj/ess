## Plan Review: ESS — Eye of Sauron Service: Agentic Post-Deploy Monitor

**Verdict**: READY

### Findings

#### BLOCKER — Phase 2 Task IDs No Longer Matched The Revised Execution Order
**Owner**: [agent]
**Status**: resolved
**What**: The completion list had been changed to a Sentry-first Phase 2, but the detailed phase design still used the old numbering and ordering for Log Scout, normalisation, tests, and documentation tasks.
**Why it matters**: An implementation agent could have executed the wrong work items or marked progress against the wrong IDs, which breaks traceability between the frontmatter checklist and the detailed design.
**Alternative**: Renumber the detailed Phase 2 sections so E2.2-E2.7 exactly match the new Sentry-first sequence and keep Log Scout explicitly deferred.

#### BLOCKER — The Phase 3 Escalation Example Conflicted With APScheduler Ownership
**Owner**: [agent]
**Status**: resolved
**What**: The escalation example used an internal `run()` loop with `asyncio.sleep()`, which implied that the monitoring session itself would own repeated timing.
**Why it matters**: That architecture would conflict with the repo's FastAPI + APScheduler design, create duplicated scheduling responsibilities, and increase the risk of a second timing loop being implemented inside session state.
**Alternative**: Rewrite the example so APScheduler remains the only timer and escalation logic is expressed as per-session state updated after each scheduler-driven cycle result.

#### RISK — Sentry Adapter Requirements Were Too Loose At The Boundary And Resilience Layer
**Owner**: [agent]
**Status**: resolved
**What**: The revised plan selected Sentry REST first, but it did not explicitly require pydantic response validation, config-owned timeout/concurrency settings, or bounded resilience behavior in the adapter section.
**Likelihood**: medium
**Alternative**: Add explicit implementation requirements for pydantic boundary models, `ESSConfig`-owned settings, aiohttp timeouts, bounded 429 retry behavior, circuit breaking, and concurrency limits.

#### RISK — Multi-Service Trigger Semantics Were Under-Specified In The Expanded Orchestrator
**Owner**: [agent]
**Status**: resolved
**What**: Earlier phases clearly support one deployment trigger containing multiple services, but the revised Phase 3 prompt and workflow language still read like a single-service orchestrator.
**Likelihood**: medium
**Alternative**: State directly that the Datadog + Sentry orchestrator must preserve multi-service deploy handling: triage across all services, focused investigation for the affected subset, and per-service aggregation in the final cycle result.

#### OPTIMIZATION — Architecture Framing Still Implied MCP-First Sentry And Incomplete Phase 1.5 Closure
**Owner**: [agent]
**Status**: resolved
**What**: The architecture section still used MCP-oriented Sentry labels even though the plan now chooses REST first, and the Phase 1.5 success criteria remained unchecked despite the shipped and validated state documented elsewhere.
**Benefit**: Reduces cognitive overhead for the next implementation agent and keeps the plan aligned with the documented runtime and recent validation history.
**Suggestion**: Re-label the target architecture to show Sentry REST as the immediate path with MCP as a future option, add an explicit note that the diagrams show the full target while the next increment is narrower, and reconcile the Phase 1.5 success criteria with the already-completed deliverable.

### Auto-Remediation Summary
- Added an explicit note that the next implementation increment is Datadog + Sentry via REST, while the high-level diagrams still represent the full ESS target.
- Updated the target architecture diagrams to show Sentry REST as the immediate backend with MCP as a future option.
- Realigned the detailed Phase 2 sections so E2.2-E2.7 match the revised Sentry-first completion list.
- Added explicit Sentry adapter requirements for pydantic boundary validation, `ESSConfig`-owned settings, aiohttp timeouts, bounded retry behavior, circuit breaking, and concurrency limits.
- Expanded the master plan `related_files` list to include the immediate Sentry and normalisation implementation files and the overlapping Sentry plan.
- Corrected the Phase 3 orchestration snippet to use async Bedrock calls and rewrote the escalation example so APScheduler remains the only timing authority.
- Tightened Phase 3 prompt and workflow language to preserve ESS's multi-service trigger model.
- Split the Phase 3 tests and documentation sections so E3.6 and E3.7 map cleanly to the completion checklist.
- Reconciled the Phase 1.5 success criteria with the already-shipped, validated deliverable state.
- Updated the docs index to point at the revised plan review.

### Human Decisions Needed
- None.