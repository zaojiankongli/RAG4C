"""Adapters that translate persisted notification routes into safe UI routes."""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from core.providers import ProviderRegistry

_ROUTE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")


@dataclass(frozen=True)
class NotificationRouteContext:
    source_kind: str
    route_code: str
    tenant_id: str
    row: Any
    raw_params: Mapping[str, Any]


NotificationRouteResult = tuple[dict[str, Any], dict[str, Any]]
NotificationRouteAdapter = Callable[[NotificationRouteContext], NotificationRouteResult]
NOTIFICATION_ROUTE_ADAPTERS: ProviderRegistry[NotificationRouteContext, NotificationRouteResult] = (
    ProviderRegistry("notification route adapter")
)


class InvalidNotificationRouteAdapterKey(ValueError):
    """A persisted route key is malformed and cannot select an adapter."""


class InvalidNotificationRouteAdapterResult(TypeError):
    """A registered route adapter returned a non-synchronous result."""


def notification_route_adapter_key(source_kind: str, route_code: str) -> str:
    for label, value in (("source_kind", source_kind), ("route_code", route_code)):
        if not isinstance(value, str) or _ROUTE_CODE.fullmatch(value) is None:
            raise InvalidNotificationRouteAdapterKey(f"{label} must be a lowercase route code")
    return f"{source_kind}:{route_code}"


def register_notification_route_adapter(
    source_kind: str,
    route_code: str,
    adapter: NotificationRouteAdapter,
    *,
    replace: bool = False,
) -> None:
    key = notification_route_adapter_key(source_kind, route_code)
    if not callable(adapter):
        raise TypeError("notification route adapter must be callable")
    callable_methods = (adapter, getattr(adapter, "__call__", None))
    if any(
        inspect.iscoroutinefunction(method)
        or inspect.isasyncgenfunction(method)
        or inspect.isgeneratorfunction(method)
        for method in callable_methods
        if method is not None
    ):
        raise TypeError("notification route adapter must be synchronous")
    try:
        inspect.signature(adapter).bind(object())
    except (TypeError, ValueError) as exc:
        raise TypeError("notification route adapter must accept one positional context") from exc
    NOTIFICATION_ROUTE_ADAPTERS.register(key, adapter, replace=replace)


def unregister_notification_route_adapter(source_kind: str, route_code: str) -> None:
    NOTIFICATION_ROUTE_ADAPTERS.unregister(notification_route_adapter_key(source_kind, route_code))


def resolve_notification_route_adapter(
    key: str,
) -> NotificationRouteAdapter:
    """Resolve a registered adapter without invoking it."""

    return NOTIFICATION_ROUTE_ADAPTERS.get_factory(key)


def invoke_notification_route_adapter(
    adapter: NotificationRouteAdapter,
    context: NotificationRouteContext,
) -> Any:
    """Invoke an adapter and reject deferred work at the synchronous read boundary."""

    result = adapter(context)
    if not (
        inspect.isawaitable(result) or inspect.isgenerator(result) or inspect.isasyncgen(result)
    ):
        return result
    cancel = getattr(result, "cancel", None)
    if callable(cancel):
        cancel()
    close = getattr(result, "close", None)
    if callable(close):
        close()
    else:
        aclose = getattr(result, "aclose", None)
        if callable(aclose):
            close_awaitable = aclose()
            close_awaitable_sync = getattr(close_awaitable, "close", None)
            if callable(close_awaitable_sync):
                close_awaitable_sync()
    raise InvalidNotificationRouteAdapterResult("notification route adapter must be synchronous")


def adapt_notification_route(
    key: str, context: NotificationRouteContext
) -> NotificationRouteResult:
    """Run one previously keyed adapter; strategy exceptions propagate unchanged."""

    return invoke_notification_route_adapter(resolve_notification_route_adapter(key), context)


__all__ = [
    "InvalidNotificationRouteAdapterKey",
    "InvalidNotificationRouteAdapterResult",
    "NotificationRouteAdapter",
    "NotificationRouteContext",
    "NotificationRouteResult",
    "NOTIFICATION_ROUTE_ADAPTERS",
    "adapt_notification_route",
    "invoke_notification_route_adapter",
    "notification_route_adapter_key",
    "register_notification_route_adapter",
    "resolve_notification_route_adapter",
    "unregister_notification_route_adapter",
]
