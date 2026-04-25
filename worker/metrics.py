from __future__ import annotations

from prometheus_client import Counter, generate_latest

events_received_total = Counter(
    "events_received_total",
    "Total WebSocket events received from 3CX",
)
events_processed_total = Counter(
    "events_processed_total",
    "Total events successfully processed",
)
events_failed_total = Counter(
    "events_failed_total",
    "Total events that failed processing",
)
ws_reconnects_total = Counter(
    "ws_reconnects_total",
    "Total WebSocket reconnection attempts",
)
db_writes_total = Counter(
    "db_writes_total",
    "Total database write operations",
)
db_write_errors_total = Counter(
    "db_write_errors_total",
    "Total database write errors",
)


def get_metrics() -> bytes:
    return generate_latest()
