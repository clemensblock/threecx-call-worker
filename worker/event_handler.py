from __future__ import annotations

import re
from datetime import UTC, datetime

import structlog

from worker.db import (
    get_connected_at,
    get_monitored_extensions,
    lookup_customer_by_phone,
    write_call_event,
)
from worker.metrics import events_failed_total, events_processed_total
from worker.phone import normalize_phone
from worker.threecx_client import get_participant_details

logger = structlog.get_logger()

PARTICIPANT_PATH_RE = re.compile(r"^/callcontrol/(\d+)/participants/(\d+)$")


def _determine_direction(details: dict) -> str:
    party_caller_type = details.get("party_caller_type", "")
    party_dn_type = details.get("party_dn_type", "")

    if party_dn_type == "Wexternalline" or party_caller_type == "Wexternalline":
        return "inbound"
    return "outbound"


def _extract_state(details: dict) -> str | None:
    status = details.get("status", "").lower()
    state_map = {
        "ringing": "ringing",
        "notified": "ringing",
        "connected": "connected",
        "terminated": "terminated",
        "failed": "failed",
    }
    return state_map.get(status)


async def handle_event(event: dict) -> None:
    entity = event.get("entity", "")
    match = PARTICIPANT_PATH_RE.match(entity)
    if not match:
        logger.debug("event.ignored", entity=entity)
        return

    extension = match.group(1)
    participant_id_str = match.group(2)

    if extension not in get_monitored_extensions():
        logger.debug(
            "event.unmonitored_extension",
            extension=extension,
            participant_id=participant_id_str,
        )
        return

    log = logger.bind(
        correlation_id=participant_id_str,
        extension=extension,
    )

    try:
        event_data = event.get("attached_data", event.get("data", {}))

        details = event_data if event_data.get("status") else None
        if not details:
            details = await get_participant_details(extension, participant_id_str)

        if not details:
            log.warning("event.no_participant_details")
            events_failed_total.inc()
            return

        state = _extract_state(details)
        if not state:
            log.debug("event.unknown_status", status=details.get("status"))
            return

        direction = _determine_direction(details)

        party_did = details.get("party_did", "")
        if not party_did:
            log.debug("event.party_did_empty")

        caller_id_raw = details.get("party_caller_id", "") or details.get("caller_id", "")
        caller_id_e164 = normalize_phone(caller_id_raw) if caller_id_raw else None

        now_iso = datetime.now(UTC).isoformat()

        if state == "ringing" and direction == "inbound":
            customer_id = None
            if caller_id_e164:
                customer_id = lookup_customer_by_phone(caller_id_e164)

            write_call_event(
                participant_id=participant_id_str,
                state="ringing",
                direction="inbound",
                extension=extension,
                caller_id=caller_id_raw,
                caller_id_e164=caller_id_e164,
                customer_id=customer_id,
                phone_number=caller_id_e164,
            )

        elif state == "connected":
            write_call_event(
                participant_id=participant_id_str,
                state="connected",
                direction=direction,
                extension=extension,
                caller_id=caller_id_raw,
                caller_id_e164=caller_id_e164,
                connected_at=now_iso,
            )

        elif state == "terminated":
            connected_at_str = get_connected_at(participant_id_str)
            duration = None
            if connected_at_str:
                try:
                    connected_dt = datetime.fromisoformat(connected_at_str)
                    terminated_dt = datetime.now(UTC)
                    duration = int((terminated_dt - connected_dt).total_seconds())
                except (ValueError, TypeError):
                    log.warning("event.duration_calc_failed", connected_at=connected_at_str)

            write_call_event(
                participant_id=participant_id_str,
                state="terminated",
                direction=direction,
                extension=extension,
                caller_id=caller_id_raw,
                caller_id_e164=caller_id_e164,
                terminated_at=now_iso,
                duration_seconds=duration,
            )

        elif state == "failed":
            write_call_event(
                participant_id=participant_id_str,
                state="failed",
                direction=direction,
                extension=extension,
                caller_id=caller_id_raw,
                caller_id_e164=caller_id_e164,
                terminated_at=now_iso,
            )

        events_processed_total.inc()
        log.info("event.processed", state=state, direction=direction)

    except Exception:
        events_failed_total.inc()
        log.exception("event.handler_error")
