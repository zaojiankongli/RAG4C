"""Small, testable helpers for preserving operator-managed dotenv files."""
from __future__ import annotations

import re
from collections.abc import Mapping

_ASSIGNMENT = re.compile(
    r"^(?P<prefix>\s*(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=).*$"
)


def update_dotenv_text(content: str, updates: Mapping[str, str]) -> str:
    """Update dotenv keys while preserving unrelated lines and their ordering."""
    newline = "\r\n" if "\r\n" in content else "\n"
    rendered: list[str] = []
    seen: set[str] = set()

    for line in content.splitlines():
        match = _ASSIGNMENT.match(line)
        key = match.group("key") if match else None
        if key is not None and key in updates:
            rendered.append(f"{match.group('prefix')}{updates[key]}")
            seen.add(key)
        else:
            rendered.append(line)

    if rendered and any(key not in seen for key in updates):
        if rendered[-1].strip():
            rendered.append("")
    for key, value in updates.items():
        if key not in seen:
            rendered.append(f"{key}={value}")

    return newline.join(rendered) + newline


__all__ = ["update_dotenv_text"]
