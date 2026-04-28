"""Track call groups to deduplicate 3CX's dual-participant pattern.

3CX creates two participants per call on an extension (signaling + media leg).
Only one participant progresses to 'connected'; the other terminates when
the call is picked up. This module groups those participants so we only
write DB entries for the primary one.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger()

CALL_GROUP_WINDOW = 5.0  # seconds — participants arriving within this window are one call
MAX_GROUPS = 200  # prevent unbounded memory growth


@dataclass
class CallGroup:
    participants: list[str] = field(default_factory=list)
    primary: str | None = None
    ring_time: float = 0.0

    def add(self, participant_id: str) -> None:
        if participant_id not in self.participants:
            self.participants.append(participant_id)

    def set_primary(self, participant_id: str) -> None:
        self.primary = participant_id

    def is_phantom(self, participant_id: str) -> bool:
        """Return True if this participant should be suppressed."""
        if self.primary is None:
            return False
        return participant_id != self.primary

    def phantom_participants(self) -> list[str]:
        """Return participant IDs that are NOT the primary."""
        if self.primary is None:
            return []
        return [p for p in self.participants if p != self.primary]


# extension -> CallGroup
_groups: dict[str, CallGroup] = {}


def _cleanup_stale() -> None:
    """Remove groups older than 120 seconds."""
    now = time.monotonic()
    stale = [ext for ext, g in _groups.items() if now - g.ring_time > 120]
    for ext in stale:
        del _groups[ext]


def get_or_create_group(extension: str, participant_id: str) -> CallGroup:
    """Get or create a call group for an extension.

    If an existing group is within the CALL_GROUP_WINDOW, the participant
    is added to it.  Otherwise a fresh group is created.
    """
    if len(_groups) > MAX_GROUPS:
        _cleanup_stale()

    now = time.monotonic()
    existing = _groups.get(extension)

    if existing and (now - existing.ring_time) < CALL_GROUP_WINDOW:
        existing.add(participant_id)
        return existing

    # New call group
    group = CallGroup(ring_time=now)
    group.add(participant_id)
    _groups[extension] = group
    return group


def find_group(extension: str, participant_id: str) -> CallGroup | None:
    """Find the call group that contains this participant."""
    group = _groups.get(extension)
    if group and participant_id in group.participants:
        return group
    return None


def mark_connected(extension: str, participant_id: str) -> list[str]:
    """Mark a participant as the primary (connected) one.

    Returns the list of phantom participant IDs whose DB entries should
    be cleaned up.
    """
    group = find_group(extension, participant_id)
    if not group:
        return []

    group.set_primary(participant_id)
    phantoms = group.phantom_participants()
    if phantoms:
        logger.info(
            "call_tracker.phantoms_identified",
            extension=extension,
            primary=participant_id,
            phantoms=phantoms,
        )
    return phantoms


def should_suppress(extension: str, participant_id: str) -> bool:
    """Return True if events for this participant should be suppressed.

    A participant is suppressed when it's part of a call group where
    another participant has already been marked as primary (connected).
    """
    group = find_group(extension, participant_id)
    if group and group.is_phantom(participant_id):
        logger.debug(
            "call_tracker.suppressed",
            extension=extension,
            participant_id=participant_id,
            primary=group.primary,
        )
        return True
    return False


def remove_group(extension: str) -> None:
    """Remove the call group for an extension after the call ends."""
    _groups.pop(extension, None)
