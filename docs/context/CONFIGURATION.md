# ESS Configuration

## Environment Variables

All configuration is loaded via pydantic-settings from `config/.env`. Import
settings from `src/config.py` — never use raw `os.getenv()`.

### LLM — AWS Bedrock

| Variable | Required | Default | Description |
|---|---|---|---|
| `LLM_PROVIDER` | No | `bedrock` | LLM provider: `bedrock`, `anthropic`, `openai` |
| `TRIAGE_MODEL` | No | `global.anthropic.claude-haiku-4-5` | Model for triage cycles |
| `INVESTIGATION_MODEL` | No | `global.anthropic.claude-sonnet-4-6` | Model for investigation cycles |
| `AWS_BEDROCK_REGION` | No | `us-west-2` | AWS region for Bedrock |
| `AWS_BEARER_TOKEN_BEDROCK` | Yes | — | ABSK-format bearer token (decoded at startup) |
| `AWS_EC2_METADATA_DISABLED` | No | `true` | Disable IMDS for local dev |

### Datadog (Pup CLI)

| Variable | Required | Default | Description |
|---|---|---|---|
| `DD_API_KEY` | Yes | — | Datadog API key |
| `DD_APP_KEY` | Yes | — | Datadog application key |
| `DD_SITE` | No | `datadoghq.com` | Datadog site |
| `PUP_MAX_CONCURRENT` | No | `10` | Max parallel Pup CLI calls |
| `PUP_DEFAULT_TIMEOUT` | No | `60` | Pup subprocess timeout (seconds) |

### Sentry

| Variable | Required | Default | Description |
|---|---|---|---|
| `SENTRY_AUTH_TOKEN` | Yes | — | Sentry auth token (project:read, event:read, issue:read) |
| `SENTRY_HOST` | No | `sentry.example.com` | Self-hosted Sentry URL |
| `SENTRY_ORG` | No | `example` | Sentry organisation slug |

### Log Scout

| Variable | Required | Default | Description |
|---|---|---|---|
| `DEFAULT_LOG_SCOUT_URL` | No | `http://syslog.example.com:8090` | Default log scout endpoint |

### Monitoring Defaults

| Variable | Required | Default | Description |
|---|---|---|---|
| `DEFAULT_MONITORING_WINDOW_MINUTES` | No | `30` | Default monitoring window |
| `DEFAULT_CHECK_INTERVAL_MINUTES` | No | `5` | Default check interval |
| `MAX_MONITORING_WINDOW_MINUTES` | No | `120` | Maximum allowed window |

### MS Teams

| Variable | Required | Default | Description |
|---|---|---|---|
| `DEFAULT_TEAMS_WEBHOOK_URL` | No | — | Default Teams webhook URL |

## ABSK Bearer Token Auth

The `AWS_BEARER_TOKEN_BEDROCK` env var uses the format `ABSK<Base64(key_id:secret)>`.
At startup, the config loader:

1. Strips the `ABSK` prefix
2. Base64-decodes the payload to `key_id:secret`
3. Splits on `:` into `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`
4. Syncs both to `os.environ` for boto3 consumption

This avoids storing raw AWS credentials in config files.

## Config Loader Location

`src/config.py` — pydantic-settings `BaseSettings` subclass `ESSConfig`.
