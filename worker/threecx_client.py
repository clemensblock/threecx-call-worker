from __future__ import annotations

import asyncio
import time

import httpx
import structlog

from worker.config import settings

logger = structlog.get_logger()

_token_cache: dict[str, str | float] = {}
_token_lock = asyncio.Lock()

SENSITIVE_KEYS = frozenset({"THREECX_CLIENT_SECRET", "SUPABASE_SERVICE_ROLE_KEY"})


async def get_token(force_refresh: bool = False) -> str:
    async with _token_lock:
        now = time.time()
        if (
            not force_refresh
            and "access_token" in _token_cache
            and isinstance(_token_cache.get("expires_at"), (int, float))
            and now < _token_cache["expires_at"]
        ):
            return str(_token_cache["access_token"])

        token_url = f"{settings.threecx_base_url}/connect/token"
        data = {
            "grant_type": "client_credentials",
            "client_id": settings.threecx_client_id,
            "client_secret": settings.threecx_client_secret,
        }

        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.post(token_url, data=data)
            resp.raise_for_status()
            body = resp.json()

        access_token = body["access_token"]
        expires_in = int(body.get("expires_in", 3600))
        _token_cache["access_token"] = access_token
        _token_cache["expires_at"] = now + expires_in - 60

        logger.info("threecx.token_refreshed", expires_in=expires_in)
        return str(access_token)


def invalidate_token() -> None:
    _token_cache.clear()


async def get_participant_details(extension: str, participant_id: str) -> dict | None:
    token = await get_token()
    url = f"{settings.threecx_base_url}/callcontrol/{extension}/participants/{participant_id}"
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(verify=False) as client:
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 401:
                logger.warning("threecx.401_on_rest", url=url)
                invalidate_token()
                token = await get_token(force_refresh=True)
                headers["Authorization"] = f"Bearer {token}"
                resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError:
            logger.exception("threecx.participant_fetch_failed", url=url)
            return None
