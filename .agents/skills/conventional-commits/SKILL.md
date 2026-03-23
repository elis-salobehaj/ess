---
name: conventional-commits
description: >
  Conventional Commits guidance for ESS. Use when creating a git commit message,
  reviewing a commit message, or summarizing a change set for commit. Produces a
  commit message with type, optional scope, subject, body, and footer following
  https://www.conventionalcommits.org/en/v1.0.0/.
argument-hint: 'Describe the changes to commit, or paste the diff or file list to summarize.'
license: Apache-2.0
---

# Conventional Commits

Use this skill whenever composing a commit message for ESS.

## Outcome

Produce a complete commit message that:
- uses the correct type and optional scope
- has an imperative subject line no longer than 72 characters
- includes a body when the change is non-trivial
- includes a `BREAKING CHANGE:` footer when behavior or interfaces change incompatibly

## Commit Message Format

```text
<type>[optional scope]: <subject>

[optional body]

[optional footer(s)]
```

### Types

| Type | When to use |
|---|---|
| `feat` | New capability (tool adapter, API endpoint, agent behavior) |
| `fix` | Bug fix |
| `docs` | Documentation-only changes |
| `style` | Formatting or whitespace only |
| `refactor` | Internal restructuring with no behavior change |
| `test` | Test-only changes |
| `chore` | Tooling, config, scripts, or maintenance work |
| `perf` | Performance improvement |
| `build` | Dependency or build-system changes |
| `revert` | Revert of a previous commit |

### Preferred Scopes For ESS

| Scope | Area |
|---|---|
| `skills` | `.agents/skills/` |
| `agents` | `AGENTS.md` or agent workflow docs |
| `api` | `src/main.py` or trigger endpoint behavior |
| `config` | `src/config.py` or `config/` files |
| `datadog` | Pup CLI tool adapter |
| `sentry` | Sentry REST API adapter |
| `logs` | Log Scout adapter |
| `agent` | AI orchestrator / ReAct loop |
| `notify` | MS Teams notification publisher |
| `scheduler` | APScheduler job management |
| `models` | Pydantic schema definitions |
| `docs` | `docs/` tree |
| `docker` | Dockerfile, docker-compose |
| `deps` | dependency changes in `pyproject.toml` |

Omit scope for cross-cutting changes.

### Subject Line Rules

- Use imperative mood
- Do not capitalize the first letter
- Do not end with a period
- Keep the subject at 72 characters or less
- Summarize what changed and why when possible

## Procedure

1. Identify the primary change intent.
2. Pick the most accurate type and scope.
3. Write the subject line.
4. Add a short body when the change spans multiple areas or the why is not obvious.
5. Add footers for breaking changes, issues, or co-authors.
6. Validate the final message against the format rules.

## Decision Rules

- New tool adapters, API endpoints, or agent behaviors map to `feat`.
- Bug fixes in tool parsing, validation, or scheduling map to `fix`.
- Plan or guide updates with no code changes map to `docs`.
- Tests that accompany a feature or fix stay under `feat` or `fix`, not `test`.
- If a change alters the deploy trigger schema, tool result shape, or config in
  an incompatible way, include `BREAKING CHANGE:`.

## Examples

```text
feat(datadog): add Pup CLI tool adapter with monitor and APM queries
```

```text
fix(sentry): handle 429 rate limit with Retry-After backoff
```

```text
docs(plans): move master plan to active and update README index
```

```text
chore(docker): add Pup CLI binary to container image
```
