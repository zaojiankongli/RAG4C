"""Strategies for source-specific notification materialization and reconciliation."""

from __future__ import annotations

import inspect
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

from sqlalchemy.orm import Session

from core.providers import ProviderRegistry

_SOURCE_KIND = re.compile(r"[a-z][a-z0-9_.-]{0,63}")


@dataclass(frozen=True)
class NotificationMaterializationRequest:
    source_kind: str
    engine: Any
    tenant_id: str
    source_id: str
    expected_source_revision: int
    expected_source_digest: str
    now: datetime
    source_scope: Mapping[str, str]

    def __post_init__(self) -> None:
        if not isinstance(self.source_scope, Mapping):
            raise TypeError("source_scope must be a mapping of string keys and values")
        scope = dict(self.source_scope)
        if any(type(key) is not str or type(value) is not str for key, value in scope.items()):
            raise TypeError("source_scope must contain only string keys and values")
        object.__setattr__(self, "source_scope", MappingProxyType(scope))


@dataclass(frozen=True)
class NotificationSourceDiscoveryContext:
    engine: Any
    session: Session
    tenant_id: str
    now: datetime


@dataclass(frozen=True)
class NotificationMaterializerPolicy:
    order: int
    materialize: Callable[[NotificationMaterializationRequest], Mapping[str, Any]]
    discover: Callable[
        [NotificationSourceDiscoveryContext], Sequence[NotificationMaterializationRequest]
    ]


class InvalidNotificationMaterializer(ValueError):
    """A notification materializer registration or discovery result is invalid."""


_RESERVED_BUILTIN_POLICY_KEYS = frozenset({"quality_alert", "approval_pending_for_me"})
_NOTIFICATION_MATERIALIZERS: ProviderRegistry[None, NotificationMaterializerPolicy] = (
    ProviderRegistry("notification materializer")
)
_REGISTRATION_LOCK = threading.RLock()
_BUILTIN_POLICIES: dict[str, NotificationMaterializerPolicy] = {}


def _validate_kind(source_kind: object) -> str:
    if type(source_kind) is not str or _SOURCE_KIND.fullmatch(source_kind) is None:
        raise InvalidNotificationMaterializer("source_kind must be a canonical lowercase code")
    return source_kind


def _validate_sync_callback(callback: object, *, label: str, context_name: str) -> None:
    if not callable(callback):
        raise TypeError(f"notification materializer {label} must be callable")
    methods = (callback, getattr(callback, "__call__", None))
    if any(
        inspect.iscoroutinefunction(method)
        or inspect.isasyncgenfunction(method)
        or inspect.isgeneratorfunction(method)
        for method in methods
        if method is not None
    ):
        raise TypeError(f"notification materializer {label} must be synchronous")
    try:
        inspect.signature(callback).bind(object())
    except (TypeError, ValueError) as exc:
        raise TypeError(
            f"notification materializer {label} must accept one positional {context_name}"
        ) from exc


def _validate_policy(policy: object) -> NotificationMaterializerPolicy:
    if type(policy) is not NotificationMaterializerPolicy:
        raise TypeError("expected a NotificationMaterializerPolicy")
    if type(policy.order) is not int or policy.order < 0:
        raise InvalidNotificationMaterializer("policy order must be a non-negative integer")
    _validate_sync_callback(policy.materialize, label="materialize", context_name="request")
    _validate_sync_callback(policy.discover, label="discover", context_name="discovery context")
    return policy


def register_notification_materializer(
    source_kind: str,
    policy: NotificationMaterializerPolicy,
    *,
    replace: bool = False,
) -> None:
    kind = _validate_kind(source_kind)
    _validate_policy(policy)

    def policy_factory(_context: None) -> NotificationMaterializerPolicy:
        return policy

    with _REGISTRATION_LOCK:
        if kind in _RESERVED_BUILTIN_POLICY_KEYS:
            raise InvalidNotificationMaterializer(
                f"built-in notification materializer {kind!r} is immutable"
            )
        _NOTIFICATION_MATERIALIZERS.register(kind, policy_factory, replace=replace)


def unregister_notification_materializer(source_kind: str) -> None:
    kind = _validate_kind(source_kind)
    with _REGISTRATION_LOCK:
        if kind in _RESERVED_BUILTIN_POLICY_KEYS:
            raise InvalidNotificationMaterializer(
                f"built-in notification materializer {kind!r} cannot be unregistered"
            )
        _NOTIFICATION_MATERIALIZERS.unregister(kind)


def _same_policy(
    actual: NotificationMaterializerPolicy,
    expected: NotificationMaterializerPolicy,
) -> bool:
    return (
        actual.order == expected.order
        and actual.materialize is expected.materialize
        and actual.discover is expected.discover
    )


def notification_materializer_snapshot() -> tuple[tuple[str, NotificationMaterializerPolicy], ...]:
    with _REGISTRATION_LOCK:
        kinds = tuple(sorted(_NOTIFICATION_MATERIALIZERS.names()))
        policies = {
            kind: _validate_policy(_NOTIFICATION_MATERIALIZERS.create(kind, None))
            for kind in kinds
        }
        missing = _RESERVED_BUILTIN_POLICY_KEYS.difference(policies)
        if missing:
            raise InvalidNotificationMaterializer(
                f"required built-in notification materializers are missing: {sorted(missing)!r}"
            )
        for kind, expected in _BUILTIN_POLICIES.items():
            if not _same_policy(policies[kind], expected):
                raise InvalidNotificationMaterializer(
                    f"built-in notification materializer {kind!r} was replaced"
                )
    return tuple(sorted(policies.items(), key=lambda item: (item[1].order, item[0])))


def invoke_notification_materializer(
    policy: NotificationMaterializerPolicy,
    request: NotificationMaterializationRequest,
) -> Mapping[str, Any]:
    result = policy.materialize(request)
    if inspect.isawaitable(result) or inspect.isgenerator(result) or inspect.isasyncgen(result):
        cancel = getattr(result, "cancel", None)
        if callable(cancel):
            cancel()
        close = getattr(result, "close", None)
        if callable(close):
            close()
        raise TypeError("notification materializer must be synchronous")
    if not isinstance(result, Mapping):
        raise TypeError("notification materializer must return a mapping")
    return result


def discover_notification_sources(
    policy: NotificationMaterializerPolicy,
    context: NotificationSourceDiscoveryContext,
) -> tuple[NotificationMaterializationRequest, ...]:
    result = policy.discover(context)
    if inspect.isawaitable(result) or inspect.isgenerator(result) or inspect.isasyncgen(result):
        cancel = getattr(result, "cancel", None)
        if callable(cancel):
            cancel()
        close = getattr(result, "close", None)
        if callable(close):
            close()
        raise TypeError("notification source discovery must be synchronous")
    if not isinstance(result, Sequence) or isinstance(result, (str, bytes)):
        raise TypeError("notification source discovery must return a sequence")
    if not all(isinstance(item, NotificationMaterializationRequest) for item in result):
        raise TypeError("notification source discovery returned an invalid request")
    return tuple(result)


def materialize_notification_source(
    request: NotificationMaterializationRequest,
) -> Mapping[str, Any]:
    if not isinstance(request, NotificationMaterializationRequest):
        raise TypeError("request must be a NotificationMaterializationRequest")
    kind = _validate_kind(request.source_kind)
    with _REGISTRATION_LOCK:
        policy = _validate_policy(_NOTIFICATION_MATERIALIZERS.create(kind, None))
        expected = _BUILTIN_POLICIES.get(kind)
        if expected is not None and not _same_policy(policy, expected):
            raise InvalidNotificationMaterializer(
                f"built-in notification materializer {kind!r} was replaced"
            )
    return invoke_notification_materializer(policy, request)


def _install_builtin_notification_materializers(
    policies: Mapping[str, NotificationMaterializerPolicy],
) -> None:
    """Install the frozen schema-backed source policies during module initialization."""
    global _BUILTIN_POLICIES
    validated: dict[str, NotificationMaterializerPolicy] = {}
    for kind, policy in policies.items():
        canonical_kind = _validate_kind(kind)
        validated[canonical_kind] = _validate_policy(policy)
    if frozenset(validated) != _RESERVED_BUILTIN_POLICY_KEYS:
        raise InvalidNotificationMaterializer(
            "built-in notification materializers must install every reserved source kind"
        )
    with _REGISTRATION_LOCK:
        if _BUILTIN_POLICIES:
            raise RuntimeError("built-in notification materializers are already installed")
        collisions = _RESERVED_BUILTIN_POLICY_KEYS.intersection(
            _NOTIFICATION_MATERIALIZERS.names()
        )
        if collisions:
            raise InvalidNotificationMaterializer(
                f"reserved built-in notification materializers already registered: "
                f"{sorted(collisions)!r}"
            )
        for kind, policy in validated.items():

            def policy_factory(
                _context: None,
                *,
                installed_policy: NotificationMaterializerPolicy = policy,
            ) -> NotificationMaterializerPolicy:
                return installed_policy

            _NOTIFICATION_MATERIALIZERS.register(kind, policy_factory)
        _BUILTIN_POLICIES = dict(validated)


__all__ = [
    "InvalidNotificationMaterializer",
    "NotificationMaterializationRequest",
    "NotificationMaterializerPolicy",
    "NotificationSourceDiscoveryContext",
    "discover_notification_sources",
    "invoke_notification_materializer",
    "materialize_notification_source",
    "notification_materializer_snapshot",
    "register_notification_materializer",
    "unregister_notification_materializer",
]
