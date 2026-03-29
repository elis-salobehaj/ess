# ESS Dashboard Architecture

Date: 2026-03-29
Status: recommended

## Question

ESS currently posts compact critical cards to Teams. What is the best way to build a custom dashboard that is easy to digest at a glance, yet allows each card to expand into detailed service findings?

## Current ESS State

- ESS already exposes `GET /api/v1/status`, `GET /api/v1/deploy/{job_id}`, `GET /health`, and `GET /metrics`.
- `JobStatusResponse.latest_result` already carries the latest cycle findings, raw tool outputs, and severity for a monitoring session.
- Teams is intentionally concise, especially in `real-world` mode.
- ESS is a Python/FastAPI backend; there is no dashboard UI today.
- The dashboard must be read-only and must not add remediation behavior.

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
- This stack is attractive when the team wants a low-magic architecture with explicit routing, explicit middleware, and no provider-shaped runtime assumptions.

What this stack gives ESS:

- A thin, explicit backend-for-frontend layer if the dashboard needs auth, request validation, aggregation, or API proxying.
- A straightforward React frontend, typically built with Vite, that remains portable and easy to reason about.
- Lower framework lock-in because both the UI and the server can move between runtimes more easily.

What this stack does not give automatically:

- It does not prescribe page routing, layout conventions, or frontend data loading patterns.
- The team still chooses those pieces directly instead of inheriting them from a framework.
- That is more work up front than a batteries-included framework, but it is also less opinionated and easier to control over time.

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

Use a TypeScript dashboard built with Bun, Hono, React, Vite, Tailwind CSS v4, shadcn/ui, and a small data-fetching layer such as TanStack Query or SWR.

Why this is the best fit for ESS:

- It keeps the UI modern and high-quality without binding the dashboard to a framework-specific server runtime.
- It gives ESS the lowest lock-in path among the TypeScript options considered.
- It still supports a thin backend-for-frontend layer through Hono when the dashboard needs auth, request validation, or aggregation.
- It keeps first delivery simple: static frontend plus explicit API proxying or direct ESS reads.
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

- React + Vite for the dashboard UI
- Bun for installs, scripts, tests, and production runtime
- Hono for optional `/api/*` proxy and aggregation routes
- Tailwind CSS v4 and shadcn/ui for the card system and detail layout

Treat Hono as a thin, explicit seam. Do not build a second application platform inside the dashboard container.

## Data Contract

The first dashboard version should use the existing ESS read surfaces:

- `GET /api/v1/status` for the active-session overview
- `GET /api/v1/deploy/{job_id}` for session drilldown
- `GET /metrics` for operational summary metrics

That is enough for a minimal, informative dashboard today because `latest_result` already includes:

- overall severity
- per-cycle findings
- services checked
- raw tool outputs

Add dedicated dashboard endpoints only if the UI outgrows the current read model. The likely next step would be a small read-only aggregation endpoint, not a new write path.

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

- Add richer drilldown and timeline presentation.
- Add a dedicated aggregation endpoint only if the existing response shape starts to feel awkward.

### Phase 3

- Add optional auth, saved filters, and live updates only after the dashboard has proven useful.
- Consider SSE or WebSockets only if polling becomes inadequate.

## Final Recommendation

Build the ESS dashboard as a separate Bun + Hono + React container in the same mono-repo as ESS. Use React + Vite for the UI, keep Hono thin and explicit, consume the current FastAPI read endpoints first, and start with a minimal severity-first card layout before adding richer drilldowns.

## Alternatives Rejected

- Streamlit: fastest prototype, but the wrong end state for a polished operations dashboard.
- Dash: strong analytics, but too callback-heavy for the UI shape ESS needs.
- NiceGUI and Reflex: attractive Python-first options, but not as strong as Next.js for a custom, high-density operator interface.
- Next.js: capable and self-hostable, but its strongest features are not needed for ESS right now and would add framework-specific runtime and caching concepts that the team would have to operate.