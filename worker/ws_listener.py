from __future__ import annotations

import asyncio
import json

import structlog
import websockets
from websockets.exceptions import ConnectionClosed

from worker.config import settings
from worker.event_handler import handle_event
from worker.metrics import events_received_total, ws_reconnects_total
from worker.threecx_client import get_token, invalidate_token

logger = structlog.get_logger()

_ws_connected = False
_last_event_at: str | None = None


def is_ws_connected() -> bool:
    return _ws_connected


def last_event_at() -> str | None:
    return _last_event_at


async def _send_subscribe(ws: websockets.ClientConnection) -> None:
    subscribe_msg = json.dumps({"RequestID": "init-sub", "Path": "/callcontrol"})
    await ws.send(subscribe_msg)
    logger.info("ws.subscribed", path="/callcontrol")


async def _heartbeat(ws: websockets.ClientConnection) -> None:
    while True:
        try:
            await asyncio.sleep(30)
            await ws.ping()
            logger.debug("ws.ping_sent")
        except (ConnectionClosed, asyncio.CancelledError):
            break
        except Exception:
            logger.exception("ws.heartbeat_error")
            break


async def _token_refresh_loop() -> None:
    while True:
        try:
            await asyncio.sleep(300)
            await get_token(force_refresh=True)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("ws.token_refresh_error")


async def _listen(ws: websockets.ClientConnection) -> None:
    global _last_event_at
    from datetime import UTC, datetime

    async for raw_message in ws:
        events_received_total.inc()
        _last_event_at = datetime.now(UTC).isoformat()

        try:
            if isinstance(raw_message, bytes):
                raw_message = raw_message.decode("utf-8")
            event = json.loads(raw_message)
            await handle_event(event)
        except json.JSONDecodeError:
            logger.warning("ws.invalid_json", raw=str(raw_message)[:200])
        except Exception:
            logger.exception("ws.event_processing_error")


async def run_ws_listener() -> None:
    global _ws_connected
    backoff = 1

    while True:
        try:
            token = await get_token()
            headers = {"Authorization": f"Bearer {token}"}

            logger.info("ws.connecting", url=settings.ws_url)

            async with websockets.connect(
                settings.ws_url,
                additional_headers=headers,
                ping_interval=None,
                close_timeout=10,
            ) as ws:
                _ws_connected = True
                backoff = 1
                logger.info("ws.connected")

                await _send_subscribe(ws)

                heartbeat_task = asyncio.create_task(_heartbeat(ws))
                token_task = asyncio.create_task(_token_refresh_loop())

                try:
                    await _listen(ws)
                finally:
                    heartbeat_task.cancel()
                    token_task.cancel()
                    await asyncio.gather(heartbeat_task, token_task, return_exceptions=True)

        except ConnectionClosed as e:
            _ws_connected = False
            if e.rcvd and e.rcvd.code == 4401:
                logger.warning("ws.auth_rejected, reconnecting with new token")
                invalidate_token()
            else:
                logger.warning("ws.connection_closed", code=getattr(e.rcvd, "code", None))

        except Exception:
            _ws_connected = False
            logger.exception("ws.connection_error")

        _ws_connected = False
        ws_reconnects_total.inc()
        logger.info("ws.reconnecting", backoff_seconds=backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, settings.reconnect_max_backoff)
