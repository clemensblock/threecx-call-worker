from __future__ import annotations

import re
from datetime import UTC, datetime

import structlog

from worker.call_tracker import (
    find_group,
    get_or_create_group,
    mark_connected,
    remove_group,
    should_suppress,
)
from worker.db import (
    delete_participant_entries,
    get_caller_info,
    get_connected_at,
    get_monitored_extensions,
    get_route_to,
    lookup_customer_by_phone,
    write_call_event,
)
from worker.metrics import events_failed_total, events_processed_total
from worker.phone import normalize_phone
from worker.threecx_client import get_participant_details, route_participant

logger = structlog.get_logger()

PARTICIPANT_PATH_RE = re.compile(r"^/callcontrol/([^/]+)/participants/(\d+)$")

# Track participants we already attempted to route (prevent duplicate routeto calls)
_routed_participants: set[str] = set()
_MAX_ROUTED_CACHE = 500


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


def _map_status(state: str) -> str:
    """Map call state to CRM status column value."""
    if state == "failed":
        return "failed"
    return "initiated"


def _resolve_agent_extension(extension: str) -> str:
    """Resolve the human agent extension from the entity.

    For routepoint extensions (e.g. crmintegration), return the route_to
    target. For direct extensions (e.g. 1000), return as-is.
    """
    route_to = get_route_to(extension)
    if route_to:
        return route_to
    return extension


def _extract_details(event: dict) -> dict | None:
    """Extract participant details from the event payload.

    3CX sends attached_data in two formats:
    - Direct participant object: {"status": "Ringing", "party_caller_id": ...}
    - WebSocket response wrapper: {"StatusCode": 200, "Response": {"status": ...}}
    """
    attached = event.get("attached_data", event.get("data"))
    if not attached or not isinstance(attached, dict):
        return None

    # Direct participant object
    if attached.get("status"):
        return attached

    # WebSocket response wrapper
    resp = attached.get("Response")
    if isinstance(resp, dict) and resp.get("status"):
        return resp

    # Response might be a list with one item
    if isinstance(resp, list) and resp and isinstance(resp[0], dict):
        return resp[0]

    return None


def _calc_duration(participant_id: str, log: structlog.stdlib.BoundLogger) -> int | None:
    """Calculate call duration from connected_at to now."""
    connected_at_str = get_connected_at(participant_id)
    if not connected_at_str:
        return None
    try:
        connected_dt = datetime.fromisoformat(connected_at_str)
        terminated_dt = datetime.now(UTC)
        return int((terminated_dt - connected_dt).total_seconds())
    except (ValueError, TypeError):
        log.warning("event.duration_calc_failed", connected_at=connected_at_str)
        return None


async def handle_event(event: dict) -> None:
    entity = event.get("entity", "")
    event_type = event.get("event_type")
    match = PARTICIPANT_PATH_RE.match(entity)
    if not match:
        logger.debug("event.ignored", entity=entity)
        return

    extension = match.group(1)
    participant_id_str = match.group(2)

    monitored = get_monitored_extensions()
    if extension not in monitored:
        if extension.isdigit():
            logger.debug(
                "event.unmonitored_extension",
                extension=extension,
                participant_id=participant_id_str,
            )
            return
        logger.info(
            "event.routepoint",
            dn=extension,
            participant_id=participant_id_str,
        )

    log = logger.bind(
        correlation_id=participant_id_str,
        extension=extension,
    )

    try:
        details = _extract_details(event)

        if not details:
            details = await get_participant_details(extension, participant_id_str)

        # event_type=1 (Remove) means participant terminated, even without details
        if not details and event_type == 1:
            if should_suppress(extension, participant_id_str):
                log.info("event.phantom_suppressed", event_type=event_type, state="terminated")
                events_processed_total.inc()
                return
            log.info("event.participant_removed", event_type=event_type)
            now_iso = datetime.now(UTC).isoformat()
            caller = get_caller_info(participant_id_str)
            duration = _calc_duration(participant_id_str, log)
            agent_ext = _resolve_agent_extension(extension)
            write_call_event(
                participant_id=participant_id_str,
                state="terminated",
                direction=caller.get("direction", "inbound"),
                extension=extension,
                caller_id=caller.get("caller_id"),
                caller_id_e164=caller.get("caller_id_e164"),
                customer_id=caller.get("customer_id"),
                terminated_at=now_iso,
                duration_seconds=duration,
                phone_number=caller.get("caller_id_e164"),
                agent_extension=agent_ext,
                status=_map_status("terminated"),
                threecx_call_id=participant_id_str,
            )
            # Clean up call group when primary terminates
            group = find_group(extension, participant_id_str)
            if group and group.primary == participant_id_str:
                remove_group(extension)
            events_processed_total.inc()
            return

        if not details:
            log.warning("event.no_participant_details", event_type=event_type)
            events_failed_total.inc()
            return

        state = _extract_state(details)
        if not state:
            log.debug("event.unknown_status", status=details.get("status"))
            return

        direction = _determine_direction(details)

        # Routepoint auto-forward: when a call arrives at a routepoint
        # with a route_to target, forward it to the destination extension.
        route_to = get_route_to(extension)
        route_key = f"{extension}:{participant_id_str}"
        if route_to and state in ("ringing", "connected") and route_key not in _routed_participants:
            if len(_routed_participants) > _MAX_ROUTED_CACHE:
                _routed_participants.clear()
            _routed_participants.add(route_key)
            log.info(
                "event.routepoint_forwarding",
                route_to=route_to,
                status=state,
            )
            routed = await route_participant(
                dn=extension,
                participant_id=participant_id_str,
                destination=route_to,
            )
            if not routed:
                log.warning("event.routepoint_forward_failed", route_to=route_to)

        party_did = details.get("party_did", "")
        if not party_did:
            log.debug("event.party_did_empty")

        caller_id_raw = details.get("party_caller_id", "") or details.get("caller_id", "")
        caller_id_e164 = normalize_phone(caller_id_raw) if caller_id_raw else None

        now_iso = datetime.now(UTC).isoformat()

        agent_ext = _resolve_agent_extension(extension)

        if state == "ringing" and direction == "inbound":
            # Register in call tracker for dedup
            get_or_create_group(extension, participant_id_str)

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
                agent_extension=agent_ext,
                status=_map_status("ringing"),
                threecx_call_id=participant_id_str,
            )

        elif state == "connected":
            # This participant is the real one — mark as primary and clean up phantoms
            phantoms = mark_connected(extension, participant_id_str)
            for phantom_id in phantoms:
                delete_participant_entries(phantom_id)

            # Enrich with caller info from ringing entry if not available
            if not caller_id_e164:
                caller = get_caller_info(participant_id_str)
                caller_id_raw = caller_id_raw or caller.get("caller_id")
                caller_id_e164 = caller.get("caller_id_e164")

            write_call_event(
                participant_id=participant_id_str,
                state="connected",
                direction=direction,
                extension=extension,
                caller_id=caller_id_raw,
                caller_id_e164=caller_id_e164,
                connected_at=now_iso,
                phone_number=caller_id_e164,
                agent_extension=agent_ext,
                status=_map_status("connected"),
                threecx_call_id=participant_id_str,
            )

        elif state == "terminated":
            if should_suppress(extension, participant_id_str):
                log.info("event.phantom_suppressed", state="terminated")
                events_processed_total.inc()
                log.info("event.processed", state=state, direction=direction)
                return

            duration = _calc_duration(participant_id_str, log)
            # Enrich with caller info from ringing entry if not available
            if not caller_id_e164:
                caller = get_caller_info(participant_id_str)
                caller_id_raw = caller_id_raw or caller.get("caller_id")
                caller_id_e164 = caller.get("caller_id_e164")

            write_call_event(
                participant_id=participant_id_str,
                state="terminated",
                direction=direction,
                extension=extension,
                caller_id=caller_id_raw,
                caller_id_e164=caller_id_e164,
                terminated_at=now_iso,
                duration_seconds=duration,
                phone_number=caller_id_e164,
                agent_extension=agent_ext,
                status=_map_status("terminated"),
                threecx_call_id=participant_id_str,
            )
            # Clean up call group when primary terminates
            group = find_group(extension, participant_id_str)
            if group and (group.primary == participant_id_str or group.primary is None):
                remove_group(extension)

        elif state == "failed":
            write_call_event(
                participant_id=participant_id_str,
                state="failed",
                direction=direction,
                extension=extension,
                caller_id=caller_id_raw,
                caller_id_e164=caller_id_e164,
                terminated_at=now_iso,
                phone_number=caller_id_e164,
                agent_extension=agent_ext,
                status=_map_status("failed"),
                threecx_call_id=participant_id_str,
            )

        events_processed_total.inc()
        log.info("event.processed", state=state, direction=direction)

    except Exception:
        events_failed_total.inc()
        log.exception("event.handler_error")
