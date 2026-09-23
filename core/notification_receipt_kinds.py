"""Receipt behavior for one notification source kind.

Projection data stays in ``notification_source_kinds``. A receipt kind is a
callable: the host looks it up and does not choose a default. Registering a
kind does not widen the stored CHECK.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_KIND_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")


@dataclass(frozen=True)
class NotificationReceiptKindSpec:
    kind: str
    handoff: Callable[..., dict[str, Any]]


_BY_KIND: dict[str, NotificationReceiptKindSpec] = {}


def _validate(spec: NotificationReceiptKindSpec) -> None:
    if not isinstance(spec, NotificationReceiptKindSpec):
        raise TypeError(f"expected NotificationReceiptKindSpec, got {type(spec).__name__}")
    if not isinstance(spec.kind, str) or _KIND_RE.fullmatch(spec.kind) is None:
        raise ValueError(f"{spec.kind!r}: kind must be a lowercase code")
    if not callable(spec.handoff):
        raise ValueError(f"{spec.kind}: handoff must be callable")


def register_notification_receipt_kind(
    spec: NotificationReceiptKindSpec, *, replace: bool = False
) -> None:
    _validate(spec)
    if spec.kind in _BY_KIND and not replace:
        raise ValueError(f"notification receipt kind already registered: {spec.kind}")
    _BY_KIND[spec.kind] = spec


def unregister_notification_receipt_kind(kind: str) -> None:
    _BY_KIND.pop(str(kind).strip().lower(), None)


def notification_receipt_kind(kind: object) -> NotificationReceiptKindSpec | None:
    if not isinstance(kind, str):
        return None
    return _BY_KIND.get(kind)
