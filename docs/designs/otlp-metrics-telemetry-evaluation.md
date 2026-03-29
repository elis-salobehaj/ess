# ESS Telemetry Backend Evaluation

Date: 2026-03-29
Status: recommended

## Question

ESS now exposes Prometheus-style metrics at `/metrics` and already writes a local
debug trace sink under `_local_observability/`. What should be the long-term
backend for ESS operational telemetry?

Specifically:

- Can ESS send OTLP traces and metrics to Sentry?
- Should ESS standardize on Datadog instead?
- Should ESS keep a custom-only approach?
- Is there a better architecture that preserves future flexibility?

## Current ESS State

ESS already has these observability surfaces:

- `/health` for liveness
- `/api/v1/status` for active session state
- `/metrics` for Prometheus-style counters and gauges
- `_local_observability/` for debug JSONL traces and digests
- structured JSON application logs

ESS also already depends on Datadog operationally for the monitored systems, and
uses Sentry as a signal source during deploy-health investigations.

That existing shape matters. The telemetry backend should support:

- operational dashboards for ESS itself
- alerting on ESS runtime health
- low-friction deployment in containers
- correlation with existing Datadog service signals when possible
- a path to OpenTelemetry without hard-coding one vendor into the app forever

## Documentation Findings

### Sentry

Findings from current Sentry documentation:

- Sentry documents `OTLPIntegration` for Python and says it automatically sends
  OpenTelemetry traces to Sentry.
- Sentry's OTLP concepts page explicitly says Sentry can ingest OpenTelemetry
  traces and logs via OTLP.
- The same Sentry OTLP concepts page explicitly says: `Sentry does not support
  OTLP metrics at this time.`
- Sentry Python SDK metrics exist, but they are a separate SDK feature and are
  currently documented as open beta.
- Sentry Python logs also exist as a separate SDK feature.
- Self-hosted Sentry is viable, but it is operationally heavy and intended as a
  maintain-your-own deployment, not a lightweight metrics backend.

Implication:

- Creating an ESS project in Sentry and sending traces and logs is viable.
- Creating an ESS project in Sentry and sending OTLP metrics is not viable.
- Re-implementing ESS runtime metrics with `sentry_sdk.metrics.*` is possible,
  but that would be a second, Sentry-specific metrics pipeline rather than a
  standards-based OTLP metrics path.

Relevant docs reviewed:

- https://docs.sentry.io/concepts/otlp/
- https://docs.sentry.io/platforms/python/integrations/otlp/
- https://docs.sentry.io/platforms/python/metrics/
- https://docs.sentry.io/platforms/python/logs/
- https://develop.sentry.dev/self-hosted/

### Datadog

Findings from current Datadog documentation:

- Datadog supports OTLP traces and metrics ingestion.
- Datadog also supports OTLP logs ingestion when enabled.
- Datadog supports Prometheus and OpenMetrics scraping through the Datadog Agent.
- Datadog explicitly recommends OpenMetrics scraping for Prometheus-style text
  endpoints.
- Datadog's OTel documentation recommends either the Datadog Agent with DDOT or
  an OpenTelemetry Collector, depending on how vendor-neutral the deployment must be.
- Datadog documents how OTLP metric types map into Datadog metric types.

Implication:

- ESS can keep `/metrics` exactly as-is and have Datadog scrape it.
- ESS can also add OpenTelemetry instrumentation later and send traces, metrics,
  and logs to Datadog through OTLP.
- Datadog is the only evaluated backend here that cleanly supports both of the
  following at once: Prometheus-style scraping now and OTLP metrics later.

Relevant docs reviewed:

- https://docs.datadoghq.com/opentelemetry/
- https://docs.datadoghq.com/opentelemetry/setup/otlp_ingest_in_the_agent/
- https://docs.datadoghq.com/opentelemetry/compatibility/
- https://docs.datadoghq.com/containers/kubernetes/prometheus/
- https://docs.datadoghq.com/metrics/open_telemetry/otlp_metric_types/

### OpenTelemetry Collector

Findings from current OpenTelemetry documentation:

- The OpenTelemetry Collector is the vendor-agnostic receive/process/export layer.
- The Collector is recommended in production because it centralizes retries,
  batching, encryption, filtering, and routing.
- Direct-to-backend export is acceptable for quick starts and small deployments,
  but Collector-based routing is the recommended long-term production shape.

Implication:

- ESS should not lock itself directly to one vendor-specific exporter path inside
  the application if a Collector can sit between ESS and the backend.
- The Collector gives ESS an escape hatch if the backend decision changes later.

Relevant docs reviewed:

- https://opentelemetry.io/docs/collector/
- https://opentelemetry.io/docs/languages/python/exporters/

## Options Evaluated

### Option A: Sentry As The Primary ESS Telemetry Backend

Description:

- Create a dedicated Sentry project for ESS.
- Send traces and logs via Sentry SDK or OTLP.
- Try to use Sentry for ESS runtime metrics and dashboards.

Pros:

- Keeps ESS-internal exceptions, traces, and logs in one Sentry project.
- Sentry SDKs for Python are straightforward.
- Could be useful for debugging ESS runtime failures and trace-to-error linkage.

Cons:

- Not viable for OTLP metrics because Sentry explicitly does not support OTLP metrics.
- Sentry metrics are SDK-specific and open beta, so this would split ESS into:
  Prometheus metrics on one side and Sentry-only custom metrics on the other.
- This would not reuse the `/metrics` endpoint naturally.
- Sentry is not the operational source of truth for the systems ESS is monitoring.
- For self-hosted deployments, running Sentry as a metrics backend is a large
  operational footprint for the problem being solved.

Assessment:

- Not recommended as the primary ESS telemetry backend.
- Viable only as a secondary backend for ESS runtime errors, traces, or logs.

### Option B: Datadog As The Primary ESS Telemetry Backend

Description:

- Keep `/metrics` in ESS.
- Have the Datadog Agent scrape it as OpenMetrics/Prometheus text.
- Add OpenTelemetry traces later and send them to Datadog via Agent or Collector.

Pros:

- Aligns with the current operational stack: Datadog is already central to ESS's
  monitoring mission.
- Supports the existing `/metrics` endpoint without extra application work.
- Supports OTLP metrics and traces if ESS moves to deeper OTel instrumentation later.
- Makes it easier to correlate ESS health with Datadog signals from the services
  ESS is evaluating.
- Lowest implementation risk right now.

Cons:

- Custom metrics volume in Datadog needs attention to avoid unnecessary cost.
- Direct OTLP into the Datadog Agent is less vendor-neutral than a Collector-based
  topology.
- Some Datadog features vary depending on whether you use Agent, DDOT, OSS
  Collector, or direct OTLP.

Assessment:

- Recommended as the primary operational telemetry backend.
- Best fit for ESS's current architecture and operator workflow.

### Option C: Custom Dashboard Only

Description:

- Keep `/health`, `/api/v1/status`, `/metrics`, and `_local_observability/`.
- Build custom dashboards or ad hoc scripts around those endpoints and files.

Pros:

- No new vendor dependencies.
- Full control over presentation and storage.
- Works for local development and low-scale debugging.

Cons:

- Rebuilds storage, alerting, retention, aggregation, and dashboarding that
  observability platforms already solve.
- Harder to scale or hand off operationally.
- Harder to correlate with external service signals.
- Higher maintenance burden for low strategic value.

Assessment:

- Acceptable only as a temporary local/dev fallback.
- Not recommended for production observability.

### Option D: Prometheus/Grafana Or Other OSS Telemetry Stack

Description:

- Keep `/metrics` and scrape it with Prometheus.
- Use Grafana for dashboards.
- Add Tempo or Jaeger for traces if ESS later emits OTel tracing.

Pros:

- Strong vendor neutrality.
- Good fit for Prometheus metrics.
- Clean OpenTelemetry ecosystem alignment.

Cons:

- Adds a new operational platform the team does not otherwise appear to be using
  for ESS's main signal sources.
- Requires more infrastructure than simply integrating with the platforms already
  in the ESS workflow.
- Splits operational visibility away from Datadog, where ESS already looks for
  deployment-health evidence.

Assessment:

- Technically good, but not the best fit for this repo right now.
- Better only if the organization is intentionally standardizing away from vendor platforms.

## Recommendation

### Best Path Forward

Use Datadog as the primary ESS telemetry backend, and standardize the internal
telemetry emission seam around OpenTelemetry concepts plus an OpenTelemetry
Collector deployment option.

Concretely:

1. In the short term, keep `/metrics` and have the Datadog Agent scrape it.
2. In parallel, add OpenTelemetry traces for ESS runtime operations.
3. Route OpenTelemetry data through a Collector or Datadog Agent OTLP receiver,
   not directly from the app to multiple vendors.
4. Keep `_local_observability/` as a debug fallback, not the production backend.
5. Do not choose Sentry as the primary home for ESS metrics.

### Sentry's Role

Sentry remains useful, but for a narrower purpose:

- downstream service investigation signal source inside ESS
- optional ESS runtime error reporting
- optional ESS runtime traces/logs if the team specifically wants Sentry views

It should not be the primary ESS operational metrics backend because the OTLP
metrics path is explicitly unsupported.

## Recommended Architecture

### Phase 1: Lowest-Risk Operational Visibility

- Keep the current `/metrics` endpoint.
- Scrape it with Datadog OpenMetrics.
- Build ESS dashboards and monitors in Datadog for:
  - active sessions
  - checks executed
  - alert delivery rate
  - tool call duration
  - request/error rate for the ESS API itself

This gives immediate operational value with almost no additional ESS code.

### Phase 2: Add OpenTelemetry Tracing To ESS

- Instrument the FastAPI request path, scheduler cycles, and external-tool calls
  with OpenTelemetry spans.
- Export traces to a local Collector or Datadog Agent OTLP receiver.
- Keep span attributes low-cardinality and aligned with ESS domain concepts:
  `job_id`, `service_count`, `environment`, `cycle_number`, `tool`, `notification_kind`.

This makes the current debug trace model queryable in a real observability backend.

### Phase 3: Add OTLP Metrics Only If Needed

- Keep `/metrics` as the stable scrape surface.
- Add OTel metrics only if the team wants one instrumentation model for both
  traces and metrics.
- If OTel metrics are added, export them through a Collector or Datadog Agent.

This avoids rewriting healthy operational metrics prematurely.

## Why Not A Custom Dual-Vendor Approach?

A custom approach where ESS simultaneously:

- emits Prometheus metrics
- emits Sentry SDK metrics
- emits OTLP traces to Sentry
- emits OTLP traces or metrics to Datadog

would create overlapping telemetry pipelines, inconsistent naming, duplicated
cost, and harder operational ownership.

That complexity is not justified for ESS right now.

The right architecture is one emission seam, one primary operational backend,
and optional secondary reporting only where there is a clear product reason.

## Final Recommendation

Recommendation:

- Primary backend: Datadog
- Production telemetry transport: OpenMetrics scrape now, OTel Collector or
  Datadog Agent OTLP later
- Keep Sentry out of the primary metrics path
- Use Sentry only for ESS runtime errors/traces/logs if there is a specific need
  for Sentry-native troubleshooting views

In plain terms: sending ESS OTLP traces to Sentry is viable; sending ESS OTLP
metrics to Sentry is not. For ESS operational telemetry, Datadog is the better
fit. The most robust design is to put an OpenTelemetry Collector-shaped seam
between ESS and whichever backend receives the data.