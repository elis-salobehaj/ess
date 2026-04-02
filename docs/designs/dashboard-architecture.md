# ESS Dashboard Architecture

Date: 2026-03-31
Status: recommended

## Question

ESS currently posts compact critical cards to Teams. What is the best way to build a custom dashboard that is easy to digest at a glance, yet allows each card to expand into detailed service findings?

## Current ESS State

- ESS already exposes `GET /api/v1/status`, `GET /api/v1/deploy/{job_id}`, `GET /health`, and `GET /metrics`.
- `JobStatusResponse.latest_result` already carries the latest cycle findings, raw tool outputs, and severity for a monitoring session.
- Teams is intentionally concise, especially in `real-world` mode.
- ESS is a Python/FastAPI backend; there is no dashboard UI today.
- The dashboard must be read-only and must not add remediation behavior.
- Monitoring sessions are currently stored in memory, so ESS does not yet expose a durable service-by-service deploy history API.

## What The Dashboard Needs

- A severity-first overview that can be read in seconds.
- One card per service or per service/session, with enough context to understand why the service is healthy, warning, or critical.
- Drilldown for findings, evidence, links, timestamps, and deploy context.
- Low operational overhead and fast first delivery.
- A clear path to richer filtering, timelines, and live refresh later.

## Options Evaluated

### Streamlit

- Fastest prototype.
- Good for simple internal tools.
- Weak fit for information-dense cards, custom layouts, and polished operator UX.
- Best as a disposable proof of concept, not the end state.

### Dash

- Strong for analytical apps and charts.
- Mature Python ecosystem.
- More callback-heavy than this problem needs.
- Good if the dashboard is chart-first; less compelling for a product-style incident surface.

### NiceGUI

- Modern Python UI with a FastAPI underlay.
- Easier to style than Streamlit.
- Smaller ecosystem and less proven at larger operator-tool scale than Dash or React-based stacks.

### Reflex

- Full-stack Python with React under the hood.
- Promising for Python-only teams.
- Still young enough that ESS would be taking framework risk on a critical operator view.

### Bun + Hono + React

- Bun is a fast all-in-one toolkit: runtime, package manager, test runner, and bundler can all come from the same toolchain.
- Hono is a small Web-standards server framework with multi-runtime support across Bun, Node.js, Cloudflare Workers, AWS Lambda, and other targets.
- Tailwind CSS v4 and shadcn/ui work well here because they are not tied to Next.js.
- Biome fits this stack well for formatting and linting because it keeps the frontend toolchain small and fast.
- This stack is attractive when the team wants a low-magic architecture with explicit routing, explicit middleware, and no provider-shaped runtime assumptions.

What this stack gives ESS:

- A thin, explicit backend-for-frontend layer if the dashboard needs auth, request validation, aggregation, or API proxying.
- A straightforward React frontend that can be bundled directly with Bun instead of introducing a second build tool by default.
- Lower framework lock-in because both the UI and the server can move between runtimes more easily.

What this stack does not give automatically:

- It does not prescribe page routing, layout conventions, or frontend data loading patterns.
- The team still chooses those pieces directly instead of inheriting them from a framework.
- That is more work up front than a batteries-included framework, but it is also less opinionated and easier to control over time.

Questioning the need for Vite in this stack:

- Vite is excellent, but once ESS has already chosen Bun and Hono, Vite becomes an extra frontend-specific layer rather than a necessary foundation.
- Bun already provides install, run, test, and bundling surfaces. For a dashboard that is internal, card-heavy, and not especially animation- or plugin-driven, Bun's native build pipeline should be enough for v1.
- The main reason to add Vite would be if the frontend starts needing plugin ecosystems or development ergonomics that Bun's frontend toolchain does not yet match well enough.
- That makes Vite a fallback option, not the default recommendation.

### Next.js + TypeScript

- Next.js does provide a full application model: routing, layouts, route handlers, middleware/proxy, server components, and a coherent deployment story.
- It is self-hostable and does not require Vercel or premium services to run.
- It is strongest when the application genuinely benefits from server rendering, framework-managed caching, and deep integration between UI routes and server routes.

What Next.js actually gives, and what it does not:

- It does give a lot more than plain React: route structure, route handlers, middleware/proxy, streaming, cache primitives, and self-hosted server support are real capabilities.
- It does not eliminate implementation work. Auth, authorization, domain-specific APIs, deployment, and operational hardening are still your responsibility.
- It is not vendor lock-in in the hosting sense, but it can become framework lock-in if you deeply adopt App Router features like server actions, cache tags, ISR, image optimization, or partial prerendering.

The important distinction for ESS is this:

- Next.js is not likely to force premium hosting costs.
- Next.js can still impose framework-specific operational concepts that you would have to keep understanding and maintaining.
- For a small internal dashboard, those concepts may be unnecessary overhead rather than leverage.

## Next.js vs Bun + Hono

### Vendor Lock-In

There are two different lock-in risks here.

#### Hosting Lock-In

Next.js:

- No hard requirement to use Vercel.
- Official self-hosting guidance exists for `next start`, Docker, reverse proxies, build IDs, runtime env vars, and multi-instance deployments.
- You can run it in your own containers and your own infrastructure.

Bun + Hono:

- Even less hosting coupling.
- Hono is explicitly designed for many runtimes and hosts.
- The server code is closer to raw Web APIs, so portability is higher.

#### Framework Lock-In

Next.js:

- Low if used mostly as a conventional React app with simple route handlers.
- Higher if the app leans on server components, ISR/cache tags, server actions, `next/image`, proxy runtime behavior, or other framework-managed features.
- In multi-instance self-hosting, those features introduce real operational concerns: shared caches, deployment IDs, cache handlers, proxy buffering, and server action key coordination.

Bun + Hono:

- Lower framework lock-in because the architecture stays explicit.
- You own your caching, routing, auth, and aggregation decisions directly.
- That means more code, but much less framework-shaped runtime behavior.

### Operational Complexity

Next.js:

- Simple to start.
- More nuanced to self-host well once you use its advanced runtime features.
- The self-hosting docs explicitly call out reverse proxies, cache coordination across pods, deployment identifiers, and server-action encryption keys for multi-instance setups.

Bun + Hono:

- Simpler operational model for ESS because the dashboard can remain a straightforward Bun server plus static frontend bundle.
- No framework-managed cache layer to coordinate unless you add one.
- Easier to reason about for a private internal dashboard that polls ESS.

### What ESS Actually Needs

ESS does not currently need:

- SEO
- public content pages
- ISR
- partial prerendering
- image optimization
- server actions
- framework-managed cache invalidation

ESS does need:

- a stable internal dashboard
- drilldown cards and timelines
- low operational overhead
- freedom to self-host without provider pressure
- an optional BFF layer for auth and aggregation later

That pushes the decision toward Bun + Hono rather than Next.js.

## Recommendation

Use a TypeScript dashboard built with Bun, Hono, React, Bun-native bundling, Biome, Tailwind CSS v4, shadcn/ui, and a small data-fetching layer such as TanStack Query or SWR.

Why this is the best fit for ESS:

- It keeps the UI modern and high-quality without binding the dashboard to a framework-specific server runtime.
- It gives ESS the lowest lock-in path among the TypeScript options considered.
- It still supports a thin backend-for-frontend layer through Hono when the dashboard needs auth, request validation, or aggregation.
- It keeps first delivery simple: Bun-native frontend build, explicit API proxying or direct ESS reads, and Biome for consistent lint/format enforcement.
- It avoids pulling in advanced runtime features that ESS does not need yet but would still have to operate.

## Architecture Decision

### Service Layout

Use a separate dashboard container.

- ESS stays the system of record for monitoring, orchestration, and data collection.
- The dashboard becomes a read-only client of ESS.
- Frontend dependency changes do not restart the monitoring engine.
- The dashboard can scale and deploy independently if its traffic grows.

Do not couple the dashboard into the ESS runtime process. A single binary would make the monitoring path inherit UI build and release risk for no operational gain.

### Repository Layout

Use a mono-repo.

- The dashboard should live alongside ESS so API changes, model changes, docs, and UI changes move together.
- A mono-repo keeps the initial build faster and prevents schema drift.
- Multi-repo only becomes attractive if the dashboard later gets a different release cadence or a separate team.

### Runtime Topology

- ESS continues to expose its existing JSON APIs.
- The dashboard container serves the React frontend and may expose a thin Hono BFF layer under the same origin.
- The dashboard polls the ESS read endpoints on a short interval for the first version, either directly or via Hono proxy routes.
- Server-sent events or WebSockets should wait until the polling version proves the data shape needs live updates.
- The dashboard should never depend on direct access to ESS internals or the monitoring session store.

### Initial Implementation Shape

- React for the dashboard UI
- Bun for installs, scripts, tests, and production runtime
- Bun native build pipeline for frontend bundling
- Hono for optional `/api/*` proxy and aggregation routes
- Biome for formatting and linting
- Tailwind CSS v4 and shadcn/ui for the card system and detail layout

Treat Hono as a thin, explicit seam. Do not build a second application platform inside the dashboard container.

## Data Contract

The first dashboard version can use the existing ESS read surfaces for active-session visibility:

- `GET /api/v1/status` for the active-session overview
- `GET /api/v1/deploy/{job_id}` for session drilldown
- `GET /metrics` for operational summary metrics

That is enough for a minimal, informative dashboard today because `latest_result` already includes:

- overall severity
- per-cycle findings
- services checked
- raw tool outputs

For the longer-term public API, grouped service history is the better model.

However, it should be added as a new read API, not by overloading the current `GET /api/v1/deploy/{job_id}` route.

Why a new API exposure is better:

- `GET /api/v1/deploy/{job_id}` is a monitoring-session resource, not a service-history resource.
- Reusing `/api/v1/deploy/{service_name}` would create ambiguous path semantics because the current route already uses a single path segment for `job_id`.
- Session lookup and service history solve different problems and should remain separate resources.
- Dashboards and third-party consumers will want stable service-oriented history queries without depending on internal session IDs.

Recommended shape:

- Keep `GET /api/v1/deploy/{job_id}` as the operational session/debug endpoint.
- Add a new service-history read model under a new namespace.

Recommended new endpoints:

- `GET /api/v1/services`
- `GET /api/v1/services/{service_name}`
- `GET /api/v1/services/{service_name}/environments/{environment}/deploys`
- `GET /api/v1/services/{service_name}/environments/{environment}/deploys/{release_version}`

Recommended responsibilities:

- `GET /api/v1/services` returns the human-facing service names ESS has monitored within the default retention window, along with enough summary metadata to support a simple explorer UI.
- `GET /api/v1/services/{service_name}` returns a service overview resource, including observed environments, latest deploy summaries per environment, and links to environment-scoped history.
- `GET /api/v1/services/{service_name}/environments/{environment}/deploys` returns deploy history for one service in one environment, newest first.
- `GET /api/v1/services/{service_name}/environments/{environment}/deploys/{release_version}` returns the release-specific history view for direct comparison, bookmarking, and external frontend drilldowns.

Why environment belongs in the resource identity:

- `release_version` should not be assumed globally unique across all environments.
- Deploy comparison is usually environment-specific.
- This keeps history queries unambiguous for QA, staging, and production.

Suggested query parameters for `GET /api/v1/services/{service_name}/environments/{environment}/deploys`:

- `latest_only=true|false`
- `limit=<n>`
- `severity=healthy,warning,critical`
- `include_raw=true|false`
- `include_debug=true|false`

Chosen query semantics:

- `latest_only=true` still returns a list, but a list with one item, so the response schema remains stable.
- `severity` filtering should be supported on day one for dashboard and third-party frontend filtering.
- `include_raw` and `include_debug` remain separate flags because they serve different use cases and allow tighter control over expensive or sensitive debug surfaces.

Why `release_version` is a better stable secondary key than `job_id` for history:

- It matches how teams think about deploy comparison.
- It aligns with Sentry release-aware analysis already present in ESS.
- It is more useful for longitudinal analysis than an opaque monitoring session identifier.

Important architectural implication:

- A service-history API is not only a route change. It requires a durable read model because ESS currently keeps monitoring sessions in memory.
- If ESS should support historical comparisons and external dashboard consumers, it needs to persist deploy findings by service and release.
- That persistence layer can still be append-only and read-optimized. It does not need to interfere with the current monitoring loop.

Recommended direction:

1. Keep the current session API for immediate operational inspection.
2. Add a new persisted service-history projection for external consumers and dashboard use.
3. Treat the service-history API as the long-term standard public read surface.
4. Only deprecate `GET /api/v1/deploy/{job_id}` later if it truly becomes redundant. It may remain valuable indefinitely for debugging and trace correlation.
5. Add a service-discovery endpoint so humans and third-party frontends can enumerate what ESS has monitored without already knowing the canonical service names.

This means the better standard path is not `GET /api/v1/deploy/{service_name}`. The better path is a new service-oriented namespace with explicit history semantics.

Chosen contract decisions for this design:

- Public path key should be the human-facing service name from `ServiceTarget.name`.
- Datadog and Sentry identifiers remain ESS-internal mapping details rather than public API keys.
- Environment is part of the history resource shape.
- Default retention target is 90 days.
- `GET /api/v1/services` should expose all monitored human-facing service names seen within the retention window by default.
- `GET /api/v1/services/{service_name}` should be a first-class overview resource, not just a redirect target, because environment-scoped history still needs one more level of navigation.
- `latest_only=true` should return a one-element list rather than a single object so clients keep one schema shape.
- Day-one severity filtering should be supported on the service-history endpoint.
- Default responses should include summaries, evidence links, and structured findings.
- Raw tool outputs and debug/trace references should be opt-in via separate request flags.

## Proposed UI Shape

### Top Level

- A compact severity summary strip
- Active session count
- Counts for healthy, warning, and critical services
- Last refresh timestamp

### Session / Service Cards

Each card should answer four questions immediately:

- What service is this?
- What is the current severity?
- Why is it in that state?
- What should I inspect next?

Each card should show:

- service name and environment
- current severity badge
- latest finding summary
- deploy release and region metadata
- check progress and next check time
- links to Teams message, deploy detail, and raw evidence

### Detail View

The expanded view should include:

- chronological findings timeline
- Datadog, Sentry, and log evidence grouped by cycle
- raw tool output in a collapsed section
- deploy metadata and affected regions
- link back to the Teams card that was posted for the same result

Keep color use sparse and meaningful. Let typography, spacing, and section order do most of the work.

## Rollout Plan

### Phase 1

- Ship a minimal read-only dashboard using the current ESS endpoints.
- Support polling refresh and a severity-sorted session list.
- Focus on one page that is useful immediately.

### Phase 2

- Add a durable service-history projection inside ESS.
- Expose grouped read endpoints by service and release version.
- Add richer drilldown and timeline presentation.
- Start moving dashboard drilldowns toward the service-history API instead of session IDs.
- Add retention management for the 90-day history window.

### Phase 3

- Add optional auth, saved filters, and live updates only after the dashboard has proven useful.
- Consider SSE or WebSockets only if polling becomes inadequate.
- Decide whether the session endpoint remains a debug surface or is formally deprecated from the public API contract.

## Resolved Decisions

- Use `ServiceTarget.name` as the canonical public service key.
- Keep Datadog and Sentry service identifiers internal to ESS.
- Scope service-history endpoints by environment.
- Target 90 days of retained deploy-history data.
- Expose `GET /api/v1/services` as the discovery endpoint for human-facing service names within the retention window.
- Expose `GET /api/v1/services/{service_name}` as a service overview resource.
- Keep `latest_only=true` schema-compatible by returning a one-item list.
- Support severity filtering from day one on the history endpoint.
- Return summaries, evidence links, and structured findings by default.
- Keep `include_raw` and `include_debug` as separate flags.
- Only return raw tool outputs and debug/trace references when explicitly requested.
- Allow either browser-to-ESS calls or a Hono same-origin facade, depending on deployment needs.

## Remaining Clarifications

- None for the current API surface. The contract is sufficiently defined to proceed into an implementation plan.

## Final Recommendation

Build the ESS dashboard as a separate Bun + Hono + React container in the same mono-repo as ESS. Use Bun-native bundling and Biome rather than adding Vite by default, keep Hono thin and explicit, consume the current FastAPI session endpoints first, and then add a new persisted service-history API under a dedicated `services` namespace, including service discovery and service overview endpoints, for long-term dashboard and third-party frontend use.

## Alternatives Rejected

- Streamlit: fastest prototype, but the wrong end state for a polished operations dashboard.
- Dash: strong analytics, but too callback-heavy for the UI shape ESS needs.
- NiceGUI and Reflex: attractive Python-first options, but not as strong as Next.js for a custom, high-density operator interface.
- Next.js: capable and self-hostable, but its strongest features are not needed for ESS right now and would add framework-specific runtime and caching concepts that the team would have to operate.