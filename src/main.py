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
)
from src.scheduler import ESSScheduler, MonitoringSession

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
                health_check_fn=_stub_health_check,
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
# Stub callbacks — replaced by the real orchestrator in Phase 3
# ---------------------------------------------------------------------------


async def _stub_health_check(session: MonitoringSession) -> HealthCheckResult:
    """Placeholder health-check that returns UNKNOWN until Phase 3 is wired up."""
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
