"""ESS FastAPI application — deploy-trigger API and health endpoints.

Routes
------
POST   /api/v1/deploy            Accept a deploy trigger; schedule monitoring.
DELETE /api/v1/deploy/{job_id}   Cancel an active monitoring session.
GET    /api/v1/deploy/{job_id}   Get status of a monitoring session.
GET    /api/v1/status            List all active sessions.
GET    /health                   Liveness probe for container orchestrators.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

import structlog
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

from src.agent.health_check_agent import DatadogHealthCheckAgent
from src.agent.trace import AgentTraceRecorder
from src.config import ESSConfig
from src.llm_client import make_investigation_client, make_triage_client
from src.models import (
    CancelResponse,
    DeployResponse,
    DeployTrigger,
    HealthCheckResult,
    HealthFinding,
    HealthSeverity,
    JobStatusResponse,
    ServiceTarget,
    ToolResult,
)
from src.notifications import (
    NotificationDecision,
    NotificationKind,
    TeamsDeliveryMode,
    TeamsDeliveryResult,
    TeamsPublisher,
    build_completion_warning_notification,
    build_investigation_notification,
    build_summary_notification,
    build_teams_card,
    evaluate_cycle_notification,
    resolve_teams_delivery_mode,
    resolve_webhook_url,
    supports_thread_replies,
)
from src.scheduler import ESSScheduler, MonitoringSession
from src.tools.normalise import pup_to_tool_result
from src.tools.pup_tool import PupTool
from src.tools.sentry_tool import SentryTool

# ---------------------------------------------------------------------------
# Logging setup — configured at import time with INFO; apps that need a
# different level call configure_logging(level) after settings are loaded.
# ---------------------------------------------------------------------------

_LOCAL_OBSERVABILITY_DIR = Path("_local_observability")
_DEBUG_LOG_PATH = _LOCAL_OBSERVABILITY_DIR / "ess-debug-logs.log"
_DEBUG_LOG_HANDLE: TextIO | None = None


def _configure_log_output(debug_mode: bool) -> TextIO:
    global _DEBUG_LOG_HANDLE

    if _DEBUG_LOG_HANDLE is not None:
        _DEBUG_LOG_HANDLE.close()
        _DEBUG_LOG_HANDLE = None

    if debug_mode:
        _LOCAL_OBSERVABILITY_DIR.mkdir(parents=True, exist_ok=True)
        _DEBUG_LOG_HANDLE = _DEBUG_LOG_PATH.open("a", encoding="utf-8")
        return _DEBUG_LOG_HANDLE

    return sys.stdout


def configure_logging(level: str = "INFO", *, debug_mode: bool = False) -> None:
    """Reconfigure structlog with the given log level string (e.g. 'DEBUG')."""
    output = _configure_log_output(debug_mode)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level, logging.INFO)),
        logger_factory=structlog.WriteLoggerFactory(file=output),
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
    )


configure_logging()

logger: structlog.BoundLogger = structlog.get_logger(__name__)  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(config: ESSConfig | None = None) -> FastAPI:
    cfg = config or ESSConfig()
    scheduler = ESSScheduler(max_sessions=cfg.max_concurrent_sessions)
    pup_tool = PupTool(config=cfg)
    trace_recorder = AgentTraceRecorder(
        enabled=cfg.debug_trace_enabled,
        path=cfg.agent_trace_path,
    )
    teams_publisher = TeamsPublisher(
        timeout_seconds=cfg.teams_timeout_seconds,
        retry_attempts=cfg.teams_retry_attempts,
        retry_backoff_seconds=cfg.teams_retry_backoff_seconds,
    )
    triage_client = make_triage_client(cfg)
    investigation_client = make_investigation_client(cfg)
    sentry_tool = SentryTool(config=cfg)
    datadog_agent = DatadogHealthCheckAgent(
        bedrock_client=triage_client,
        pup_tool=pup_tool,
        investigation_client=investigation_client,
        sentry_tool=sentry_tool,
        trace_recorder=trace_recorder,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
        configure_logging(cfg.log_level, debug_mode=cfg.debug_trace_enabled)
        await scheduler.start()
        logger.info("ess_started", host=cfg.host, port=cfg.port)
        yield
        await scheduler.stop()
        await sentry_tool.close()
        logger.info("ess_stopped")

    app = FastAPI(
        title="ESS — Eye of Sauron Service",
        description="Agentic post-deploy monitoring service",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Store shared state on the app instance for access in route handlers.
    app.state.config = cfg
    app.state.scheduler = scheduler
    app.state.datadog_agent = datadog_agent
    app.state.trace_recorder = trace_recorder
    app.state.teams_publisher = teams_publisher

    # -----------------------------------------------------------------------
    # Exception handlers
    # -----------------------------------------------------------------------

    @app.exception_handler(ValueError)
    async def _value_error_handler(_req: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": str(exc)},
        )

    # -----------------------------------------------------------------------
    # Liveness / readiness
    # -----------------------------------------------------------------------

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, Any]:
        active = len(scheduler.active_sessions())
        return {
            "status": "ok",
            "active_sessions": active,
            "timestamp": datetime.now(tz=UTC).isoformat(),
        }

    # -----------------------------------------------------------------------
    # Status — active monitoring sessions
    # -----------------------------------------------------------------------

    @app.get("/api/v1/status", tags=["monitoring"])
    async def list_sessions() -> dict[str, Any]:
        sessions = scheduler.active_sessions()
        return {
            "active_sessions": len(sessions),
            "sessions": [
                {
                    "job_id": s.job_id,
                    "services": [svc.name for svc in s.deploy.services],
                    "environment": s.deploy.deployment.environment,
                    "status": s.status,
                    "checks_completed": s.checks_completed,
                    "checks_planned": s.checks_planned,
                    "started_at": s.started_at.isoformat(),
                    "next_check_at": s.next_check_at.isoformat() if s.next_check_at else None,
                }
                for s in sessions
            ],
        }

    # -----------------------------------------------------------------------
    # POST /api/v1/deploy
    # -----------------------------------------------------------------------

    @app.post(
        "/api/v1/deploy",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=DeployResponse,
        tags=["monitoring"],
    )
    async def trigger_deploy(payload: DeployTrigger) -> DeployResponse:
        job_id = f"ess-{uuid.uuid4().hex[:8]}"
        window = payload.monitoring.window_minutes
        interval = payload.monitoring.check_interval_minutes
        checks_planned = max(1, window // interval)

        try:
            await scheduler.schedule_monitoring(
                job_id=job_id,
                deploy=payload,
                health_check_fn=datadog_agent.run_health_check,
                on_complete_fn=_build_completion_callback(
                    cfg,
                    trace_recorder,
                    teams_publisher,
                ),
                on_result_fn=_build_result_callback(
                    cfg,
                    trace_recorder,
                    teams_publisher,
                    scheduler,
                ),
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc

        logger.info(
            "deploy_trigger_accepted",
            job_id=job_id,
            services=[s.name for s in payload.services],
            environment=payload.deployment.environment,
            window_minutes=window,
        )

        return DeployResponse(
            job_id=job_id,
            status="scheduled",
            services_monitored=len(payload.services),
            checks_planned=checks_planned,
            regions=payload.deployment.regions,
            monitoring_window_minutes=window,
            check_interval_minutes=interval,
        )

    # -----------------------------------------------------------------------
    # GET /api/v1/deploy/{job_id}
    # -----------------------------------------------------------------------

    @app.get(
        "/api/v1/deploy/{job_id}",
        response_model=JobStatusResponse,
        tags=["monitoring"],
    )
    async def get_session(job_id: str) -> JobStatusResponse:
        session = scheduler.get_session(job_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No monitoring session found for job_id={job_id!r}",
            )
        return JobStatusResponse(
            job_id=session.job_id,
            status=session.status,
            services_monitored=len(session.deploy.services),
            checks_completed=session.checks_completed,
            checks_planned=session.checks_planned,
            started_at=session.started_at,
            next_check_at=session.next_check_at,
            deploy_context=session.deploy.deployment,
            latest_result=session.results[-1] if session.results else None,
        )

    # -----------------------------------------------------------------------
    # DELETE /api/v1/deploy/{job_id}
    # -----------------------------------------------------------------------

    @app.delete(
        "/api/v1/deploy/{job_id}",
        response_model=CancelResponse,
        tags=["monitoring"],
    )
    async def cancel_session(job_id: str) -> CancelResponse:
        cancelled = await scheduler.cancel_monitoring(job_id)
        if not cancelled:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No active monitoring session found for job_id={job_id!r}",
            )
        logger.info("deploy_monitoring_cancelled", job_id=job_id)
        return CancelResponse(job_id=job_id, status="cancelled")

    return app


# ---------------------------------------------------------------------------
# Datadog-backed health check for real end-to-end trigger testing
# ---------------------------------------------------------------------------


def _build_pup_health_check(pup_tool: PupTool):
    async def _health_check(session: MonitoringSession) -> HealthCheckResult:
        cycle = session.checks_completed + 1
        environment = session.deploy.deployment.environment.value
        per_service_results = await asyncio.gather(
            *[
                _run_triage_for_service(pup_tool, service, environment)
                for service in session.deploy.services
            ]
        )

        findings: list[HealthFinding] = []
        raw_tool_outputs: dict[str, Any] = {}
        overall_severity = HealthSeverity.HEALTHY if per_service_results else HealthSeverity.UNKNOWN

        for service_name, tool_results in per_service_results:
            for tool_result in tool_results:
                finding = _tool_result_to_finding(service_name, tool_result)
                findings.append(finding)
                raw_tool_outputs[f"{service_name}:{tool_result.tool}"] = {
                    "success": tool_result.success,
                    "summary": tool_result.summary,
                    "error": tool_result.error,
                    "data": tool_result.data,
                    "raw": tool_result.raw,
                }
                overall_severity = _max_severity(overall_severity, finding.severity)

        logger.info(
            "health_check_completed",
            job_id=session.job_id,
            cycle=cycle,
            services=[s.name for s in session.deploy.services],
            overall_severity=overall_severity.value,
            findings=len(findings),
        )
        return HealthCheckResult(
            job_id=session.job_id,
            cycle_number=cycle,
            checked_at=datetime.now(tz=UTC),
            overall_severity=overall_severity,
            findings=findings,
            services_checked=[service.name for service in session.deploy.services],
            raw_tool_outputs=raw_tool_outputs,
        )

    return _health_check


async def _run_triage_for_service(
    pup_tool: PupTool,
    service: ServiceTarget,
    environment: str,
) -> tuple[str, list[ToolResult]]:
    monitor_result, log_result, apm_result = await asyncio.gather(
        pup_tool.get_monitor_status(service.datadog_service_name, environment),
        pup_tool.search_error_logs(service.datadog_service_name),
        pup_tool.get_apm_stats(service.datadog_service_name, environment),
    )
    return service.name, [
        pup_to_tool_result(monitor_result, "monitor_status"),
        pup_to_tool_result(log_result, "error_logs"),
        pup_to_tool_result(apm_result, "apm_stats"),
    ]


def _tool_result_to_finding(service_name: str, tool_result: ToolResult) -> HealthFinding:
    severity = _severity_from_tool_result(tool_result)
    return HealthFinding(
        tool=tool_result.tool,
        severity=severity,
        summary=f"{service_name}: {tool_result.summary}",
        details=tool_result.error,
    )


def _severity_from_tool_result(tool_result: ToolResult) -> HealthSeverity:
    if not tool_result.success:
        return HealthSeverity.UNKNOWN

    payload = json.dumps(tool_result.data).lower()

    if tool_result.tool == "datadog.monitor_status":
        if any(token in payload for token in ("alert", "critical")):
            return HealthSeverity.CRITICAL
        if any(token in payload for token in ("warn", "warning", "no data")):
            return HealthSeverity.WARNING
        return HealthSeverity.HEALTHY

    if tool_result.tool == "datadog.error_logs":
        if _estimate_collection_size(tool_result.data) > 0:
            return HealthSeverity.WARNING
        return HealthSeverity.HEALTHY

    return HealthSeverity.HEALTHY


def _estimate_collection_size(payload: dict[str, Any]) -> int:
    for key in ("items", "logs", "data", "results", "entries"):
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
        if isinstance(value, dict):
            for nested in value.values():
                if isinstance(nested, list):
                    return len(nested)
    return 0


def _max_severity(left: HealthSeverity, right: HealthSeverity) -> HealthSeverity:
    order = {
        HealthSeverity.HEALTHY: 0,
        HealthSeverity.WARNING: 1,
        HealthSeverity.CRITICAL: 2,
        HealthSeverity.UNKNOWN: 3,
    }
    return left if order[left] >= order[right] else right


async def _stub_health_check(session: MonitoringSession) -> HealthCheckResult:
    """Legacy placeholder kept for tests and phased rollout reference."""
    logger.info(
        "health_check_stub",
        job_id=session.job_id,
        cycle=session.checks_completed + 1,
        services=[s.name for s in session.deploy.services],
    )
    return HealthCheckResult(
        job_id=session.job_id,
        cycle_number=session.checks_completed + 1,
        checked_at=datetime.now(tz=UTC),
        overall_severity=HealthSeverity.UNKNOWN,
        findings=[
            HealthFinding(
                tool="stub",
                severity=HealthSeverity.UNKNOWN,
                summary="Health-check orchestrator not yet wired up (Phase 3)",
            )
        ],
        services_checked=[s.name for s in session.deploy.services],
    )


def _build_completion_callback(
    cfg: ESSConfig,
    trace_recorder: AgentTraceRecorder,
    teams_publisher: TeamsPublisher,
):
    async def _on_complete(session: MonitoringSession) -> None:
        overall_severity = _aggregate_session_severity(session)
        delivery_mode = resolve_teams_delivery_mode(cfg, session)

        await trace_recorder.emit(
            "session.completed",
            trace_id=session.job_id,
            cycle_number=session.checks_completed or None,
            attributes={
                "checks_completed": session.checks_completed,
                "checks_planned": session.checks_planned,
                "overall_severity": overall_severity.value,
                "services": [service.name for service in session.deploy.services],
                "latest_result": (
                    session.results[-1].model_dump(mode="json") if session.results else None
                ),
            },
        )

        if delivery_mode == TeamsDeliveryMode.ALL:
            await _deliver_notification(
                cfg,
                trace_recorder,
                teams_publisher,
                session,
                build_summary_notification(session),
            )
        else:
            warning_decision = build_completion_warning_notification(session)
            if warning_decision is not None:
                await _deliver_notification(
                    cfg,
                    trace_recorder,
                    teams_publisher,
                    session,
                    warning_decision,
                )
            else:
                await trace_recorder.emit(
                    "notification.skipped",
                    trace_id=session.job_id,
                    cycle_number=session.checks_completed or None,
                    attributes={
                        "kind": "completion",
                        "reason": "completion_report_suppressed_real_world",
                        "teams_enabled": cfg.teams_enabled,
                        "overall_severity": overall_severity.value,
                    },
                )

        logger.info(
            "monitoring_session_completed",
            job_id=session.job_id,
            checks_completed=session.checks_completed,
            overall_severity=overall_severity.value,
            teams_enabled=cfg.teams_enabled,
        )

    return _on_complete


def _build_result_callback(
    cfg: ESSConfig,
    trace_recorder: AgentTraceRecorder,
    teams_publisher: TeamsPublisher,
    scheduler: ESSScheduler | None = None,
):
    async def _on_result(session: MonitoringSession, result: HealthCheckResult) -> None:
        delivery_mode = resolve_teams_delivery_mode(cfg, session)
        decision, reason = evaluate_cycle_notification(session, result)
        if decision is None:
            await trace_recorder.emit(
                "notification.skipped",
                trace_id=session.job_id,
                cycle_number=result.cycle_number,
                attributes={
                    "kind": "cycle",
                    "reason": reason,
                    "teams_enabled": cfg.teams_enabled,
                    "overall_severity": result.overall_severity.value,
                },
            )
            return

        if (
            delivery_mode == TeamsDeliveryMode.REAL_WORLD
            and decision.kind == NotificationKind.WARNING
        ):
            await trace_recorder.emit(
                "notification.skipped",
                trace_id=session.job_id,
                cycle_number=result.cycle_number,
                attributes={
                    "kind": decision.kind.value,
                    "reason": "warning_deferred_until_completion",
                    "teams_enabled": cfg.teams_enabled,
                    "overall_severity": result.overall_severity.value,
                },
            )
            return

        delivery = await _deliver_notification(
            cfg,
            trace_recorder,
            teams_publisher,
            session,
            decision,
        )
        if (
            delivery_mode == TeamsDeliveryMode.REAL_WORLD
            and decision.kind == NotificationKind.CRITICAL
            and scheduler is not None
        ):
            stop_requested = await scheduler.request_early_completion(
                session.job_id,
                reason="critical_alert_detected",
            )
            if stop_requested:
                await trace_recorder.emit(
                    "monitoring.early_completion_requested",
                    trace_id=session.job_id,
                    cycle_number=result.cycle_number,
                    attributes={
                        "reason": "critical_alert_detected",
                        "notification_delivered": delivery.ok if delivery is not None else False,
                        "teams_enabled": cfg.teams_enabled,
                        "overall_severity": result.overall_severity.value,
                    },
                )
        if delivery is None or not delivery.ok:
            return

        investigation_decision = build_investigation_notification(session, result, decision)
        if investigation_decision is None:
            return

        if (
            delivery_mode == TeamsDeliveryMode.REAL_WORLD
            and not supports_thread_replies(cfg, session)
        ):
            await trace_recorder.emit(
                "notification.skipped",
                trace_id=session.job_id,
                cycle_number=result.cycle_number,
                attributes={
                    "kind": investigation_decision.kind.value,
                    "reason": "thread_reply_not_supported_for_webhook",
                    "teams_enabled": cfg.teams_enabled,
                    "overall_severity": result.overall_severity.value,
                },
            )
            return

        await _deliver_notification(
            cfg,
            trace_recorder,
            teams_publisher,
            session,
            investigation_decision,
        )

    return _on_result


async def _deliver_notification(
    cfg: ESSConfig,
    trace_recorder: AgentTraceRecorder,
    teams_publisher: TeamsPublisher,
    session: MonitoringSession,
    decision: NotificationDecision,
) -> TeamsDeliveryResult | None:
    webhook_url, webhook_source = resolve_webhook_url(session, cfg.default_teams_webhook_url)

    if not cfg.teams_enabled:
        await trace_recorder.emit(
            "notification.skipped",
            trace_id=session.job_id,
            cycle_number=decision.cycle_number,
            attributes={
                "kind": decision.kind.value,
                "reason": "teams_disabled",
                "teams_enabled": False,
            },
        )
        return None

    if webhook_url is None:
        await trace_recorder.emit(
            "notification.skipped",
            trace_id=session.job_id,
            cycle_number=decision.cycle_number,
            attributes={
                "kind": decision.kind.value,
                "reason": "missing_webhook_url",
                "teams_enabled": True,
            },
        )
        logger.warning(
            "teams_notification_skipped_missing_webhook",
            job_id=session.job_id,
            kind=decision.kind.value,
        )
        return None

    await trace_recorder.emit(
        "notification.attempted",
        trace_id=session.job_id,
        cycle_number=decision.cycle_number,
        attributes={
            "kind": decision.kind.value,
            "reason": decision.reason,
            "overall_severity": decision.overall_severity.value,
            "webhook_source": webhook_source,
        },
    )

    delivery = await teams_publisher.post_card(
        webhook_url,
        build_teams_card(cfg, session, decision),
    )
    if delivery.ok:
        await trace_recorder.emit(
            "notification.delivered",
            trace_id=session.job_id,
            cycle_number=decision.cycle_number,
            attributes={
                "kind": decision.kind.value,
                "status_code": delivery.status_code,
                "response_text": delivery.response_text,
                "attempts": delivery.attempts,
                "webhook_source": webhook_source,
            },
        )
        logger.info(
            "teams_notification_delivered",
            job_id=session.job_id,
            kind=decision.kind.value,
            status_code=delivery.status_code,
            attempts=delivery.attempts,
        )
        return delivery

    await trace_recorder.emit(
        "notification.failed",
        trace_id=session.job_id,
        cycle_number=decision.cycle_number,
        attributes={
            "kind": decision.kind.value,
            "status_code": delivery.status_code,
            "error": delivery.error,
            "response_text": delivery.response_text,
            "attempts": delivery.attempts,
            "webhook_source": webhook_source,
        },
    )
    logger.warning(
        "teams_notification_failed",
        job_id=session.job_id,
        kind=decision.kind.value,
        status_code=delivery.status_code,
        error=delivery.error,
        attempts=delivery.attempts,
    )
    return delivery


def _aggregate_session_severity(session: MonitoringSession) -> HealthSeverity:
    overall = HealthSeverity.HEALTHY if session.results else HealthSeverity.UNKNOWN
    for result in session.results:
        overall = _max_severity(overall, result.overall_severity)
    return overall


async def _stub_on_complete(session: MonitoringSession) -> None:
    """Legacy placeholder kept for older tests and phased rollout reference."""
    logger.info(
        "monitoring_session_complete_stub",
        job_id=session.job_id,
        checks_completed=session.checks_completed,
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

app = create_app()


def run() -> None:
    import uvicorn

    cfg = app.state.config
    uvicorn.run(
        "src.main:app",
        host=cfg.host,
        port=cfg.port,
        log_level=cfg.log_level.lower(),
        reload=False,
    )


if __name__ == "__main__":
    run()
