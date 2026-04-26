from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

from worker.config import settings
from worker.metrics import get_metrics
from worker.ws_listener import is_ws_connected, last_event_at, run_ws_listener

REDACTED_KEYS = frozenset(
    {
        "threecx_client_secret",
        "supabase_service_role_key",
        "client_secret",
        "service_role_key",
        "authorization",
        "token",
        "password",
        "secret",
    }
)


def _redact_processor(
    _logger: structlog.types.WrappedLogger,
    _method: str,
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    for key in list(event_dict.keys()):
        if any(s in key.lower() for s in REDACTED_KEYS):
            event_dict[key] = "[REDACTED]"
    return event_dict


def _configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


_ws_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    global _ws_task
    _configure_logging()

    logger = structlog.get_logger()
    logger.info("worker.starting")

    loop = asyncio.get_running_loop()

    async def _shutdown() -> None:
        logger.info("worker.shutdown_signal")
        if _ws_task and not _ws_task.done():
            _ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await _ws_task

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(_shutdown()))

    _ws_task = asyncio.create_task(run_ws_listener())

    yield

    if _ws_task and not _ws_task.done():
        _ws_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _ws_task
    logger.info("worker.stopped")


app = FastAPI(title="3CX Call Worker", lifespan=lifespan)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "ws_connected": is_ws_connected(),
            "last_event_at": last_event_at(),
        }
    )


@app.get("/metrics")
async def metrics() -> PlainTextResponse:
    return PlainTextResponse(get_metrics(), media_type="text/plain; version=0.0.4; charset=utf-8")
