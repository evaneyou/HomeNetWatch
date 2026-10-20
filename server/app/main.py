import asyncio
import logging
import os
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.agent_routes import router as agent_router
from app.api.auth_routes import router as auth_router
from app.api.enrollment_routes import router as enrollment_router
from app.api.readings_routes import router as readings_router
from app.api.rf_routes import router as rf_router
from app.api.routes import router
from app.api.settings_routes import router as settings_router
from app.api.kpi_routes import router as kpi_router
from app.api.server_health_routes import router as server_health_router
from app.api.speedtest_routes import router as speedtest_router
from app.api.stress_routes import router as stress_router
from app.api.telegram_routes import router as telegram_router
from app.api.synthetic_routes import router as synthetic_router
from app.config import get_settings
from app.redact import GuestAnonymizeMiddleware

log = logging.getLogger(__name__)

try:
    __version__ = version("hns-server")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    if os.environ.get("PYTEST_CURRENT_TEST"):
        yield
        return

    from app.db import SessionLocal
    from app.services.retention import run_retention
    from sqlalchemy import select

    from app.models import Monitor
    from app.synthetic import ensure_key, ensure_profiles
    from app.wireguard_tunnel import ensure_srtp_key, rewrite_peers

    try:
        with SessionLocal() as db:
            rewrite_peers(db)
            ensure_profiles(db)
            ensure_key(db)
            for monitor in db.scalars(select(Monitor)).all():
                ensure_srtp_key(monitor.id)
            db.commit()
    except Exception:
        log.exception("wireguard peer list was not written")

    async def _loop() -> None:
        while True:
            try:
                await asyncio.to_thread(run_retention)
            except Exception:
                log.exception("measurement retention failed")
            await asyncio.sleep(24 * 60 * 60)

    async def _alert_loop() -> None:
        from app import alerts

        await asyncio.sleep(90)  # let the Pis check in after a restart before judging them
        while True:
            try:
                await asyncio.to_thread(alerts.run_once)
            except Exception:
                log.exception("alert loop failed")
            await asyncio.sleep(60)

    async def _server_health_loop() -> None:
        from app.server_health import run_once

        while True:
            try:
                await asyncio.to_thread(run_once)
            except Exception:
                log.exception("server health sample failed")
            await asyncio.sleep(60)

    async def _server_advisory_loop() -> None:
        from app.server_health import run_advisories

        while True:
            try:
                await asyncio.to_thread(run_advisories)
            except Exception:
                log.exception("server advisory check failed")
            await asyncio.sleep(60 * 60)  # only fetches when the Sunday 3 AM Eastern check is due

    async def _kpi_loop() -> None:
        from app import kpi

        try:
            await asyncio.to_thread(kpi.run_backfill)
        except Exception:
            log.exception("kpi backfill failed")
        while True:
            await asyncio.sleep(60)
            try:
                await asyncio.to_thread(kpi.run_once)
            except Exception:
                log.exception("kpi loop failed")

    # Only run background loops in the primary server container, not in the enrollment-only agent-api container.
    run_bg = os.environ.get("RUN_BACKGROUND_TASKS")
    if run_bg is None:
        should_run_bg = not bool(os.environ.get("TLS_DIR"))
    else:
        should_run_bg = run_bg.strip().lower() not in {"0", "false", "no"}

    tasks = (
        [
            asyncio.create_task(_loop()),
            asyncio.create_task(_alert_loop()),
            asyncio.create_task(_kpi_loop()),
            asyncio.create_task(_server_health_loop()),
            asyncio.create_task(_server_advisory_loop()),
        ]
        if should_run_bg
        else []
    )
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="HomeNetWatch", version=__version__, lifespan=_lifespan)
    # Added first so it sits inside CORS and rewrites the body before headers are copied.
    app.add_middleware(GuestAnonymizeMiddleware)

    @app.middleware("http")
    async def _count_server_errors(request, call_next):
        # Feeds the "server is returning errors" alert. A crash that never becomes a response counts too.
        from app import alerts

        def _persist_error(status_code: int, exc_text: str = "") -> None:
            try:
                from app.db import SessionLocal
                from app.models import SystemLog

                msg = f"{request.method} {request.url.path} -> {status_code}"
                if exc_text:
                    msg = f"{msg} ({exc_text[:300]})"
                with SessionLocal() as db:
                    db.add(
                        SystemLog(
                            source="server",
                            subsystem="http",
                            severity="critical",
                            message=msg,
                            detail={"method": request.method, "path": request.url.path, "status_code": status_code},
                        )
                    )
                    db.commit()
            except Exception:
                pass

        try:
            response = await call_next(request)
        except Exception as exc:
            alerts.note_server_error()
            await asyncio.to_thread(_persist_error, 500, str(exc))
            raise
        if response.status_code >= 500:
            alerts.note_server_error()
            await asyncio.to_thread(_persist_error, response.status_code)
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # The agent command path is /monitors/command. It has to be registered before
    # /monitors/{monitor_id}, or that parameter swallows the word "command".
    app.include_router(agent_router)
    app.include_router(speedtest_router)
    app.include_router(router)
    app.include_router(auth_router)
    app.include_router(settings_router)
    app.include_router(readings_router)
    app.include_router(rf_router)
    app.include_router(enrollment_router)
    app.include_router(synthetic_router)
    app.include_router(stress_router)
    app.include_router(telegram_router)
    app.include_router(kpi_router)
    app.include_router(server_health_router)
    return app


app = create_app()
