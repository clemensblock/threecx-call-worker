from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from worker.event_handler import handle_event

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "events"


def _load_event(filename: str) -> dict:
    return json.loads((FIXTURES_DIR / filename).read_text())


@pytest.fixture(autouse=True)
def _mock_db():
    with (
        patch("worker.event_handler.write_call_event") as mock_write,
        patch("worker.event_handler.lookup_customer_by_phone", return_value=None),
        patch("worker.event_handler.get_connected_at", return_value=None),
        patch("worker.event_handler.get_participant_details", return_value=None),
        patch("worker.event_handler.get_monitored_extensions", return_value={"100", "101", "102"}),
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
