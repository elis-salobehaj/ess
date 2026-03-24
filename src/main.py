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
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

from src.config import ESSConfig
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
from src.scheduler import ESSScheduler, MonitoringSession
from src.tools.normalise import pup_to_tool_result
from src.tools.pup_tool import PupTool

# ---------------------------------------------------------------------------
# Logging setup — configured at import time with INFO; apps that need a
# different level call configure_logging(level) after settings are loaded.
# ---------------------------------------------------------------------------


def configure_logging(level: str = "INFO") -> None:
    """Reconfigure structlog with the given log level string (e.g. 'DEBUG')."""
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level, logging.INFO)),
        logger_factory=structlog.PrintLoggerFactory(),
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

    @asynccontextmanager
    async def lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
        configure_logging(cfg.log_level)
        await scheduler.start()
        logger.info("ess_started", host=cfg.host, port=cfg.port)
        yield
        await scheduler.stop()
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
                health_check_fn=_build_pup_health_check(pup_tool),
                on_complete_fn=_stub_on_complete,
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


async def _stub_on_complete(session: MonitoringSession) -> None:
    """Placeholder completion callback — Teams notification wired up in Phase 4."""
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
