# Teams Channel Integration

This guide covers the simplest ESS-to-Teams setup for posting into an existing
channel.

Target channel for the current rollout:

- Channel name: `elis-test-post-deploy-monitor`
- Channel link: `https://teams.microsoft.com/l/channel/19%3A93f9a3a1a7a54c908c45bd8aa7b707b7%40thread.skype/elis-test-post-deploy-monitor?groupId=6efa101f-10d8-4902-ae35-3c8594a8e2c3&tenantId=c655950e-7130-45b5-b9c1-88f9a658b29a`

## Recommended Path For Current ESS

Use a Teams Incoming Webhook scoped to the target channel.

Why this path fits ESS today:

- ESS already posts channel-scoped HTTPS webhook payloads.
- ESS already sends Adaptive Card content through that webhook contract.
- No Entra app registration, bot registration, or Graph token handling is needed.

Important caveat:

- Microsoft is steering new connector scenarios toward Teams Workflows as Microsoft 365 Connectors approach retirement.
- ESS currently targets the direct Incoming Webhook pattern. If your tenant disables Incoming Webhooks, ESS will need a small adapter change before using a Workflows webhook instead.

## Create The Channel Webhook

1. Open the `elis-test-post-deploy-monitor` channel in Teams.
2. Select the channel menu (`...`) next to the channel name.
3. Select `Manage channel`.
4. Open the `Edit` section for channel apps/connectors.
5. Search for `Incoming Webhook` and add it.
6. If `Incoming Webhook` is already present, choose `Configure`.
7. Name it something explicit such as `ESS Post-Deploy Monitor`.
8. Copy the generated webhook URL and store it securely.

If the option is missing:

- Ask a Teams admin to enable member permission to create, update, and remove connectors for the team/channel.

## Configure ESS

Add the webhook to `config/.env`:

```env
ESS_TEAMS_ENABLED=true
DEFAULT_TEAMS_WEBHOOK_URL=https://outlook.office.com/webhook/...
ESS_TEAMS_TIMEOUT_SECONDS=10
```

Notes:

- `ESS_TEAMS_ENABLED=true` turns on warning, critical, and summary delivery.
- `DEFAULT_TEAMS_WEBHOOK_URL` is the simplest option for a fixed channel.
- You can still override the webhook per trigger with `monitoring.teams_webhook_url`.

## Validate The Webhook Before Running ESS

Use a direct webhook smoke test first:

```bash
curl -H 'Content-Type: application/json' \
  -d '{"text":"ESS webhook connectivity test"}' \
  "$DEFAULT_TEAMS_WEBHOOK_URL"
```

Expected result:

- The response body is `1`.
- A message appears in `elis-test-post-deploy-monitor`.

## Validate Through ESS

1. Enable Teams in `config/.env`.
2. Start ESS locally.
3. Trigger a monitoring session with [docs/examples/triggers/example-service-e2e.json](../examples/triggers/example-service-e2e.json), or use a longer-window local payload from `_local_observability/triggers/`.
4. Watch the ESS logs for:
   - `teams_notification_delivered`
   - `teams_notification_failed`
5. If debug tracing is enabled, inspect:
   - `_local_observability/agent_trace_<job_id>.jsonl`
   - `_local_observability/agent_trace_digest_<job_id>.md`
   - `_local_observability/ess-debug-logs.log`

## Security Notes

- Treat the webhook URL as a secret.
- Do not commit the real webhook URL to the repository.
- If the URL leaks, rotate it from the Teams channel configuration screen.

## Current ESS Behavior

With Teams enabled, ESS will post:

- immediate `CRITICAL` alerts
- repeated `WARNING` alerts on the second consecutive warning cycle
- a final end-of-window summary

These notifications are emitted on the same runtime path as the Datadog Bedrock tool loop, so Bedrock, Pup, notification, and trace events all appear in the same session-scoped observability trail when debug tracing is enabled.

Retry and backoff policy is not part of the current runtime yet. If Teams returns an error, ESS records the failure and continues monitoring.