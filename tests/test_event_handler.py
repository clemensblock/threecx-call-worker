from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from worker.event_handler import _extract_details, _routed_participants, handle_event

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "events"


def _load_event(filename: str) -> dict:
    return json.loads((FIXTURES_DIR / filename).read_text())


@pytest.fixture(autouse=True)
def _mock_db():
    _routed_participants.clear()
    with (
        patch("worker.event_handler.write_call_event") as mock_write,
        patch("worker.event_handler.lookup_customer_by_phone", return_value=None),
        patch("worker.event_handler.get_connected_at", return_value=None),
        patch("worker.event_handler.get_participant_details", return_value=None),
        patch("worker.event_handler.get_monitored_extensions", return_value={"100", "101", "102"}),
        patch("worker.event_handler.get_route_to", return_value=None),
        patch("worker.event_handler.route_participant", return_value=True),
    ):
        yield mock_write


@pytest.mark.asyncio
async def test_inbound_ringing(_mock_db: MagicMock) -> None:
    event = _load_event("01_inbound_ringing.json")
    await handle_event(event)
    _mock_db.assert_called_once()
    call_kwargs = _mock_db.call_args.kwargs
    assert call_kwargs["state"] == "ringing"
    assert call_kwargs["direction"] == "inbound"
    assert call_kwargs["extension"] == "100"
    assert call_kwargs["participant_id"] == "42"
    assert call_kwargs["caller_id_e164"] == "+493012345678"


@pytest.mark.asyncio
async def test_inbound_connected(_mock_db: MagicMock) -> None:
    event = _load_event("02_inbound_connected.json")
    await handle_event(event)
    _mock_db.assert_called_once()
    call_kwargs = _mock_db.call_args.kwargs
    assert call_kwargs["state"] == "connected"
    assert call_kwargs["direction"] == "inbound"
    assert call_kwargs["connected_at"] is not None


@pytest.mark.asyncio
async def test_inbound_terminated(_mock_db: MagicMock) -> None:
    event = _load_event("03_inbound_terminated.json")
    await handle_event(event)
    _mock_db.assert_called_once()
    call_kwargs = _mock_db.call_args.kwargs
    assert call_kwargs["state"] == "terminated"
    assert call_kwargs["terminated_at"] is not None


@pytest.mark.asyncio
async def test_outbound_connected(_mock_db: MagicMock) -> None:
    event = _load_event("04_outbound_connected.json")
    await handle_event(event)
    _mock_db.assert_called_once()
    call_kwargs = _mock_db.call_args.kwargs
    assert call_kwargs["state"] == "connected"
    assert call_kwargs["direction"] == "outbound"


@pytest.mark.asyncio
async def test_inbound_ringing_no_did(_mock_db: MagicMock) -> None:
    event = _load_event("05_inbound_ringing_no_did.json")
    await handle_event(event)
    _mock_db.assert_called_once()
    call_kwargs = _mock_db.call_args.kwargs
    assert call_kwargs["state"] == "ringing"
    assert call_kwargs["direction"] == "inbound"
    assert call_kwargs["caller_id_e164"] == "+4917155512345"


@pytest.mark.asyncio
async def test_inbound_failed(_mock_db: MagicMock) -> None:
    event = _load_event("06_inbound_failed.json")
    await handle_event(event)
    _mock_db.assert_called_once()
    call_kwargs = _mock_db.call_args.kwargs
    assert call_kwargs["state"] == "failed"
    assert call_kwargs["direction"] == "inbound"


@pytest.mark.asyncio
async def test_unmonitored_extension_ignored(_mock_db: MagicMock) -> None:
    event = _load_event("07_unmonitored_extension.json")
    await handle_event(event)
    _mock_db.assert_not_called()


@pytest.mark.asyncio
async def test_non_participant_path_ignored(_mock_db: MagicMock) -> None:
    event = {"entity": "/callcontrol/100/calls/5", "attached_data": {}}
    await handle_event(event)
    _mock_db.assert_not_called()


@pytest.mark.asyncio
async def test_idempotency_same_event_produces_one_call(_mock_db: MagicMock) -> None:
    """Replaying the same event 5 times should call write_call_event 5 times,
    but the DB layer uses ON CONFLICT DO NOTHING, so only 1 row is created.
    We verify the handler calls the write function each time."""
    event = _load_event("01_inbound_ringing.json")
    for _ in range(5):
        await handle_event(event)
    assert _mock_db.call_count == 5


@pytest.mark.asyncio
async def test_terminated_with_connected_at(_mock_db: MagicMock) -> None:
    """When a connected_at timestamp exists, duration should be computed."""
    with patch(
        "worker.event_handler.get_connected_at",
        return_value="2026-01-15T10:00:00+00:00",
    ):
        event = _load_event("03_inbound_terminated.json")
        await handle_event(event)
        call_kwargs = _mock_db.call_args.kwargs
        assert call_kwargs["duration_seconds"] is not None
        assert call_kwargs["duration_seconds"] >= 0


@pytest.mark.asyncio
async def test_customer_lookup_on_ringing() -> None:
    """When a customer is found by phone, customer_id is passed."""
    with (
        patch("worker.event_handler.write_call_event") as mock_write,
        patch(
            "worker.event_handler.lookup_customer_by_phone",
            return_value="cust-abc-123",
        ),
        patch("worker.event_handler.get_connected_at", return_value=None),
        patch("worker.event_handler.get_participant_details", return_value=None),
        patch("worker.event_handler.get_monitored_extensions", return_value={"100", "101", "102"}),
    ):
        event = _load_event("01_inbound_ringing.json")
        await handle_event(event)
        call_kwargs = mock_write.call_args.kwargs
        assert call_kwargs["customer_id"] == "cust-abc-123"


@pytest.mark.asyncio
async def test_routepoint_event_processed(_mock_db: MagicMock) -> None:
    """Events from non-numeric DNs (routepoints like crmintegration) are processed."""
    event = {
        "entity": "/callcontrol/crmintegration/participants/1344",
        "attached_data": {
            "id": 1344,
            "status": "Ringing",
            "party_caller_id": "+49 171 1234567",
            "party_dn_type": "Wexternalline",
            "party_caller_type": "Wexternalline",
            "party_did": "+4930999888",
        },
    }
    await handle_event(event)
    _mock_db.assert_called_once()
    call_kwargs = _mock_db.call_args.kwargs
    assert call_kwargs["state"] == "ringing"
    assert call_kwargs["direction"] == "inbound"
    assert call_kwargs["extension"] == "crmintegration"
    assert call_kwargs["participant_id"] == "1344"


@pytest.mark.asyncio
async def test_routepoint_remove_event_terminates(_mock_db: MagicMock) -> None:
    """event_type=1 (Remove) with empty attached_data should write terminated."""
    event = {
        "event_type": 1,
        "entity": "/callcontrol/crmintegration/participants/1355",
        "attached_data": {},
    }
    await handle_event(event)
    _mock_db.assert_called_once()
    call_kwargs = _mock_db.call_args.kwargs
    assert call_kwargs["state"] == "terminated"
    assert call_kwargs["extension"] == "crmintegration"
    assert call_kwargs["terminated_at"] is not None


@pytest.mark.asyncio
async def test_wrapped_response_details(_mock_db: MagicMock) -> None:
    """attached_data with WebSocket Response wrapper is unwrapped correctly."""
    event = {
        "entity": "/callcontrol/crmintegration/participants/1360",
        "attached_data": {
            "StatusCode": 200,
            "Response": {
                "id": 1360,
                "status": "Connected",
                "dn": "crmintegration",
                "party_caller_id": "+49 30 123456",
                "party_dn_type": "Wexternalline",
                "party_caller_type": "Wexternalline",
                "party_did": "+493099900",
            },
        },
    }
    await handle_event(event)
    _mock_db.assert_called_once()
    call_kwargs = _mock_db.call_args.kwargs
    assert call_kwargs["state"] == "connected"
    assert call_kwargs["extension"] == "crmintegration"


class TestExtractDetails:
    def test_direct_participant_object(self) -> None:
        event = {"attached_data": {"status": "Ringing", "party_caller_id": "+49"}}
        assert _extract_details(event) == {"status": "Ringing", "party_caller_id": "+49"}

    def test_websocket_response_wrapper(self) -> None:
        event = {"attached_data": {"StatusCode": 200, "Response": {"status": "Connected", "id": 1}}}
        assert _extract_details(event) == {"status": "Connected", "id": 1}

    def test_empty_attached_data(self) -> None:
        assert _extract_details({"attached_data": {}}) is None

    def test_no_attached_data(self) -> None:
        assert _extract_details({}) is None

    def test_response_list(self) -> None:
        event = {"attached_data": {"Response": [{"status": "Ringing", "id": 5}]}}
        assert _extract_details(event) == {"status": "Ringing", "id": 5}


@pytest.mark.asyncio
async def test_routepoint_triggers_route_to() -> None:
    """When a routepoint has route_to configured, route_participant is called."""
    _routed_participants.clear()
    with (
        patch("worker.event_handler.write_call_event") as mock_write,
        patch("worker.event_handler.lookup_customer_by_phone", return_value=None),
        patch("worker.event_handler.get_connected_at", return_value=None),
        patch("worker.event_handler.get_participant_details", return_value=None),
        patch(
            "worker.event_handler.get_monitored_extensions",
            return_value={"crmintegration", "1000"},
        ),
        patch("worker.event_handler.get_route_to", return_value="1000"),
        patch("worker.event_handler.route_participant", return_value=True) as mock_route,
    ):
        event = {
            "entity": "/callcontrol/crmintegration/participants/1400",
            "attached_data": {
                "status": "Connected",
                "party_caller_id": "+49 171 9999999",
                "party_dn_type": "Wexternalline",
                "party_caller_type": "Wexternalline",
            },
        }
        await handle_event(event)
        mock_route.assert_called_once_with(
            dn="crmintegration",
            participant_id="1400",
            destination="1000",
        )
        mock_write.assert_called_once()


@pytest.mark.asyncio
async def test_routepoint_no_duplicate_route() -> None:
    """Second event for same participant should NOT trigger route again."""
    _routed_participants.clear()
    with (
        patch("worker.event_handler.write_call_event"),
        patch("worker.event_handler.lookup_customer_by_phone", return_value=None),
        patch("worker.event_handler.get_connected_at", return_value=None),
        patch("worker.event_handler.get_participant_details", return_value=None),
        patch(
            "worker.event_handler.get_monitored_extensions",
            return_value={"crmintegration"},
        ),
        patch("worker.event_handler.get_route_to", return_value="1000"),
        patch("worker.event_handler.route_participant", return_value=True) as mock_route,
    ):
        event = {
            "entity": "/callcontrol/crmintegration/participants/1401",
            "attached_data": {
                "status": "Connected",
                "party_caller_id": "+49 171 8888888",
                "party_dn_type": "Wexternalline",
                "party_caller_type": "Wexternalline",
            },
        }
        await handle_event(event)
        await handle_event(event)
        assert mock_route.call_count == 1


@pytest.mark.asyncio
async def test_no_route_for_regular_extension(_mock_db: MagicMock) -> None:
    """Regular extensions (no route_to) should NOT trigger route_participant."""
    with patch("worker.event_handler.route_participant") as mock_route:
        event = _load_event("01_inbound_ringing.json")
        await handle_event(event)
        mock_route.assert_not_called()
