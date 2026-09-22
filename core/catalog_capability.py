"""Declared capability states and one fail-closed classifier for them.

`inspect_workspace_authorization_capability` (and its automation / task siblings in
`core/catalog_schema.py`) report a small state string plus issues. Three consumers read
that state on authorization-sensitive paths and historically disagreed:

- `core/enterprise_workspace_control.py:636` — raises on anything but `"ready"` (closed).
- `core/enterprise_workspace_authorization.py:180-183` — tolerates `"not_available"`,
  raises on anything else non-ready (closed).
- `core/enterprise_access_control.py:254` — raised **only** on `"unavailable"`, so any
  further state fell straight through into `evaluate_workspace_authorization`.

The third is the one that decides whether a workspace authorization evaluation may run
at all. This module gives the states one declaration and one total rule: a state nobody
declared is a reason to refuse, never a reason to proceed.
"""

from __future__ import annotations

from typing import Any

__all__ = ["CAPABILITY_NOT_AVAILABLE", "CAPABILITY_READY", "CAPABILITY_STATES",
           "CAPABILITY_UNAVAILABLE", "require_safe_capability_state"]

CAPABILITY_READY = "ready"
CAPABILITY_NOT_AVAILABLE = "not_available"
CAPABILITY_UNAVAILABLE = "unavailable"

# `not_available` is a real, produced state (the capability's migration is not installed
# yet), and callers legitimately proceed on it — that is what the pre-0027 path does.
CAPABILITY_STATES: frozenset[str] = frozenset(
    {CAPABILITY_READY, CAPABILITY_NOT_AVAILABLE, CAPABILITY_UNAVAILABLE}
)


def require_safe_capability_state(
    state: Any,
    issues: tuple[str, ...] | list[str] | None,
    *,
    allow_not_available: bool,
    error: type[Exception],
) -> str:
    """Return the state when it is safe to act on, else raise `error`.

    Total by construction: anything outside CAPABILITY_STATES raises, so a fourth state
    added later cannot silently widen the set of conditions under which authorization
    is evaluated.
    """
    normalized = str(state or "")
    detail = "; ".join(str(i) for i in (issues or ())) or "schema drift"
    if normalized == CAPABILITY_READY:
        return normalized
    if normalized == CAPABILITY_NOT_AVAILABLE and allow_not_available:
        return normalized
    if normalized not in CAPABILITY_STATES:
        raise error(f"未知的 Workspace 授权 capability 状态: {normalized!r}")
    raise error(f"Workspace 授权 capability 不可安全使用: {detail}")
