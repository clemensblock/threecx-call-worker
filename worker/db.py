from __future__ import annotations

import structlog
from supabase import Client, create_client

from worker.config import settings
from worker.metrics import db_write_errors_total, db_writes_total

logger = structlog.get_logger()

_client: Client | None = None


def get_supabase() -> Client:
    global _client
    if _client is None:
        _client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    return _client


def lookup_customer_by_phone(phone_e164: str) -> str | None:
    try:
        result = (
            get_supabase()
            .table("customers")
            .select("id")
            .eq("phone_e164", phone_e164)
            .limit(1)
            .execute()
        )
        if result.data:
            return str(result.data[0]["id"])
    except Exception:
        logger.exception("db.customer_lookup_failed", phone_e164=phone_e164)
    return None


def write_call_event(
    *,
    participant_id: str,
    state: str,
    direction: str,
    extension: str,
    caller_id: str | None = None,
    caller_id_e164: str | None = None,
    customer_id: str | None = None,
    phone_number: str | None = None,
    connected_at: str | None = None,
    terminated_at: str | None = None,
    duration_seconds: int | None = None,
) -> None:
    row: dict = {
        "participant_id": participant_id,
        "state": state,
        "direction": direction,
        "extension": extension,
    }
    if caller_id is not None:
        row["caller_id"] = caller_id
    if caller_id_e164 is not None:
        row["caller_id_e164"] = caller_id_e164
        row["phone_number"] = caller_id_e164
    if phone_number is not None:
        row["phone_number"] = phone_number
    if customer_id is not None:
        row["customer_id"] = customer_id
    if connected_at is not None:
        row["connected_at"] = connected_at
    if terminated_at is not None:
        row["terminated_at"] = terminated_at
    if duration_seconds is not None:
        row["duration_seconds"] = duration_seconds

    try:
        get_supabase().table("call_logs").upsert(
            row,
            on_conflict="participant_id,state",
            ignore_duplicates=True,
        ).execute()
        db_writes_total.inc()
        logger.info(
            "db.call_event_written",
            participant_id=participant_id,
            state=state,
            direction=direction,
        )
    except Exception:
        db_write_errors_total.inc()
        logger.exception(
            "db.call_event_write_failed",
            participant_id=participant_id,
            state=state,
        )


def get_connected_at(participant_id: str) -> str | None:
    try:
        result = (
            get_supabase()
            .table("call_logs")
            .select("connected_at")
            .eq("participant_id", participant_id)
            .eq("state", "connected")
            .limit(1)
            .execute()
        )
        if result.data and result.data[0].get("connected_at"):
            return str(result.data[0]["connected_at"])
    except Exception:
        logger.exception("db.get_connected_at_failed", participant_id=participant_id)
    return None
