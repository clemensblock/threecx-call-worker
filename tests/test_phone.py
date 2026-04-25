from __future__ import annotations

import pytest

from worker.phone import normalize_phone


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # German landline with +49
        ("+4930123456", "+4930123456"),
        # German mobile with +49 and spaces
        ("+49 171 5551234", "+491715551234"),
        # German with 0049 prefix
        ("004930123456", "+4930123456"),
        # German local with leading 0
        ("030123456", "+4930123456"),
        # German mobile with leading 0
        ("01715551234", "+491715551234"),
        # With dashes and parens
        ("(030) 123-456", "+4930123456"),
        # With dots
        ("030.123.456", "+4930123456"),
        # Austrian number
        ("+43199887766", "+43199887766"),
        # International with 00 prefix
        ("004311234567", "+4311234567"),
        # Swiss number
        ("+41441234567", "+41441234567"),
        # Already E.164
        ("+491715551234", "+491715551234"),
        # Mixed whitespace
        ("  +49 30  123 456  ", "+4930123456"),
        # With dots and dashes combined
        ("030-123.456", "+4930123456"),
        # Empty string
        ("", None),
        # None
        (None, None),
        # Only whitespace
        ("   ", None),
        # Only special chars
        ("(--)", None),
        # Short German number
        ("0123", "+49123"),
        # Just a zero
        ("0", None),
        # Single digit (not a valid phone number)
        ("5", None),
        # Long German mobile
        ("+49 (0) 171 555 12 34", "+4901715551234"),
        # 0049 with spaces
        ("0049 30 123456", "+4930123456"),
    ],
)
def test_normalize_phone(raw: str | None, expected: str | None) -> None:
    assert normalize_phone(raw) == expected


PARITY_TEST_INPUTS = [
    "+4930123456",
    "+49 171 5551234",
    "004930123456",
    "030123456",
    "01715551234",
    "(030) 123-456",
    "030.123.456",
    "+43199887766",
    "004311234567",
    "+41441234567",
    "+491715551234",
    "  +49 30  123 456  ",
    "030-123.456",
    "",
    "   ",
    "(--)",
    "0123",
    "+49 (0) 171 555 12 34",
    "0049 30 123456",
    "0171-55512345",
    "+49 30 12345678",
    "+43 1 99887766",
    "+49 40 1234567",
    "089 / 123 456 78",
    "00 49 89 123 456",
    "+49(0)89/12345678",
    "0049 (0) 89 123 456 78",
    "+1 212 555 1234",
    "001 212 555 1234",
    "040-9999-123",
]
"""30 inputs used by both Python and SQL parity tests."""
