from __future__ import annotations

import re


def normalize_phone(raw: str | None) -> str | None:
    """Normalize a phone number to E.164 format.

    Rules:
    - Strip whitespace, dashes, parens, dots
    - +49... -> keep
    - 0049... -> +49...
    - 0... (German) -> +49... (drop leading 0)
    - Anything else with + prefix -> keep as-is
    - Empty/non-numeric after strip -> None
    """
    if not raw:
        return None

    cleaned = re.sub(r"[\s\-\(\)\.]", "", raw)

    if not cleaned:
        return None

    if cleaned.startswith("+49"):
        return cleaned
    if cleaned.startswith("0049"):
        return "+" + cleaned[2:]
    if cleaned.startswith("00") and len(cleaned) > 4:
        return "+" + cleaned[2:]
    if cleaned.startswith("0") and len(cleaned) > 1:
        return "+49" + cleaned[1:]
    if cleaned.startswith("+"):
        return cleaned

    remaining = re.sub(r"[^0-9]", "", cleaned)
    if not remaining:
        return None

    if len(remaining) < 2:
        return None

    return cleaned
