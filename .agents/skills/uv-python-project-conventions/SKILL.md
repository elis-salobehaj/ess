---
name: uv-python-project-conventions
description: >
  Enforces uv-native Python conventions for ESS (Eye of Sauron Service).
  Covers uv commands, FastAPI patterns, pydantic validation, async safety,
  tool adapter conventions, ruff linting, pytest testing, and documentation
  requirements. Use whenever implementing, reviewing, or debugging Python
  code in this repository.
argument-hint: 'Describe the Python task: what you are implementing, reviewing, or debugging.'
license: Apache-2.0
---

# uv Python Project Conventions

Use this skill for all Python implementation, review, and debugging work in the
ESS repository.

## Outcome

Code that follows all ESS conventions: uv-only workflows, pydantic boundaries,
async-first I/O with timeouts, observer-only constraint, ruff-clean style,
passing tests, structured logging, and updated documentation. Agents must leave
behind production-grade code, not scaffolding, placeholders, or happy-path-only
implementations.

## Procedure

### Step 1 — Verify toolchain

- Use `uv sync` to install dependencies. Never use pip, poetry, or manual venvs.
- Use `uv add <package>` to add runtime deps and `uv add --dev <package>` for dev deps.
- Run code/tests with `uv run`: `uv run pytest`, `uv run uvicorn ...`, `uv run ruff check .`.
- Use `uv run python -m <module>` when running modules directly.
- Treat `uv.lock` as authoritative after dependency changes.

### Step 2 — FastAPI and async patterns

- All HTTP handlers must be `async def`.
- Use pydantic models for request/response validation — FastAPI integrates natively.
- Subprocess calls (Pup CLI) must use `asyncio.create_subprocess_exec` with `asyncio.wait_for` timeout.
- HTTP calls (Sentry, Log Scout, Teams) must use `aiohttp.ClientSession` with `aiohttp.ClientTimeout`.
- LLM calls (Bedrock) must use boto3 client with reasonable timeout handling.
- Use `asyncio.Semaphore` to limit concurrent external calls.
- Do not introduce blocking I/O inside async flows unless it is isolated with
  `asyncio.to_thread` and justified.

### Step 3 — Validate at boundaries

- **Incoming HTTP requests**: pydantic models with FastAPI automatic validation.
- **Tool responses**: parse subprocess stdout as JSON, handle `JSONDecodeError` gracefully.
- **Config**: pydantic-settings `BaseSettings` in `src/config.py`. Never raw `os.getenv()`.
- **Sentry/Log Scout responses**: validate expected shape before passing to orchestrator.
- **Internal boundaries**: prefer typed models or typed dataclasses when data moves between adapters,
  orchestrator, and publishers.
- Never use `cast`, unchecked dict access, or shape assumptions to bypass validation.

### Step 4 — Observer-only constraint

- ESS must NEVER take remediation actions (no rollbacks, no restarts, no infra changes).
- Health check reports may recommend actions but must not execute them.
- All tool calls are read-only queries (monitors list, logs search, issues query).
- If a suggested implementation would mutate infrastructure or service state,
  stop and redesign it to keep ESS read-only.

### Step 5 — Resilience patterns

- **Circuit breaker**: tool adapters must track consecutive failures and disable
  after 3 failures. Agent must be informed when a tool is unavailable.
- **Timeouts**: every external call must have an explicit timeout.
- **Rate limiting**: semaphores for concurrent subprocess/HTTP calls.
- **Graceful degradation**: if one tool fails, continue health checks with remaining tools.
- **Retries**: use bounded retries with backoff only when the remote system is expected
  to recover quickly. Never retry indefinitely.

### Step 6 — Ruff linting and formatting

- Run `uv run ruff check .` before committing. Fix all errors.
- Run `uv run ruff format .` to auto-format.
- Do not introduce alternative linters or formatters.

### Step 7 — Testing

- Use pytest with pytest-asyncio for async tests.
- Mock external calls: `asyncio.create_subprocess_exec` for Pup, `aiohttp` for HTTP, boto3 for Bedrock.
- Integration tests marked with `@pytest.mark.integration` for real external calls.
- Every new feature must include tests. Every bug fix must include a regression test.
- Run full suite with `uv run pytest` before considering work complete.
- Cover failure paths, timeout paths, malformed tool responses, and degraded mode behavior.

### Step 8 — Documentation

- Update `docs/context/` files when architecture, config, or workflows change.
- Update `docs/README.md` when plan status changes.
- Update plan frontmatter when tasks are completed.
- Keep `AGENTS.md` current if stack essentials or critical rules change.

## Completion Checks

Before finishing, verify:
- `uv run ruff check .` passes
- `uv run ruff format --check .` passes or formatting was applied
- `uv run pytest` passes for the impacted area, and full-suite coverage was considered
- All new external I/O paths have timeout, validation, and failure handling
- No code path violates the observer-only rule
- Any architecture, config, workflow, or plan bookkeeping change is documented
