## Plan Review: ESS — Eye of Sauron Service: Agentic Post-Deploy Monitor

**Verdict**: READY

### Findings

#### BLOCKER — Local Trace Was Positioned As A Permanent First-Ship Surface
**Owner**: [agent]
**Status**: resolved
**What**: Decision 9 and Phase 1.5 originally described a root-level JSONL trace as the standing first-ship runtime surface. That conflicted with the requested direction that local trace output be debug-only and that the long-term observability path converge on OpenTelemetry rather than a permanent file sink.
**Why it matters**: Leaving the plan as-is would push Phase 1.5 toward always-on local file output, create unnecessary production data-retention risk, and increase the chance of a Phase 5 observability rewrite.
**Alternative**: Gate local trace output behind `ESS_DEBUG_TRACE_ENABLED`, honour `ESS_AGENT_TRACE_PATH` only when debug tracing is enabled, and require the instrumentation layer to preserve an OpenTelemetry-friendly event model while deferring exporter destination decisions to Phase 5.

#### RISK — Phase 1.5 Notification Pull-Forward Needed Stronger Runtime Boundaries
**Owner**: [agent]
**Status**: resolved
**What**: The pull-forward of Teams notification behavior into Phase 1.5 did not explicitly state that notification delivery must remain bounded async I/O, nor that trace and notification events should share a typed instrumentation seam.
**Likelihood**: medium
**Alternative**: Require explicit timeout-bounded async Teams delivery in E15.3 and route notification decisions and outcomes through the same typed instrumentation layer used by the debug trace sink so Phase 5 can export the same events through OpenTelemetry later.

#### OPTIMIZATION — Documentation Bookkeeping Had Stale References
**Owner**: [agent]
**Status**: resolved
**What**: The plan still referenced `docs/INDEX.md`, and the frontmatter `related_files` list did not include the configuration and technology decision docs now governing the Phase 1.5 design.
**Benefit**: Reduces ambiguity for future implementation and review agents and keeps plan bookkeeping aligned with the actual docs tree.
**Suggestion**: Replace `docs/INDEX.md` references with `docs/README.md` and include the relevant configuration and technology decision docs in frontmatter.

### Auto-Remediation Summary
- Updated Decision 9 in the master plan to use a Teams gate plus debug-only local trace bridge instead of an always-on JSONL trace surface.
- Added `ESS_DEBUG_TRACE_ENABLED` and clarified that `ESS_AGENT_TRACE_PATH` is only honoured when debug tracing is enabled.
- Added explicit OpenTelemetry-forward requirements so Phase 1.5 instrumentation can be reused in Phase 5 without rewriting the agent loop.
- Tightened E15.3 and E15.4 so Teams delivery remains bounded async work and notification outcomes flow through the same instrumentation seam.
- Updated success criteria to distinguish debug-trace behavior from normal runtime behavior.
- Replaced stale `docs/INDEX.md` references with `docs/README.md` and expanded the plan frontmatter `related_files` list.
- Updated the technology decision summary to match the reviewed trace strategy.

### Human Decisions Needed
- None. The decision about OTLP exporter destination, collector topology, and long-term observability storage is now explicitly deferred to Phase 5 and does not block Phase 1.5 implementation.
