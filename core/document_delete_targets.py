"""Ordered, durable projection targets required by document deletion."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from collections.abc import Callable
import inspect

from core.providers import ProviderRegistry

_STORE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_BUILTIN_ORDERS = {"milvus_chunks": 10, "graph_projection": 20}
_RESERVED_TARGETS = frozenset({*_BUILTIN_ORDERS, "catalog_finalize"})
_REGISTRATION_LOCK = threading.RLock()


@dataclass(frozen=True)
class DocumentDeleteTargetPolicy:
    """Stable ordering for one projection store in the durable delete barrier."""

    order: int
    runtime_check: Callable[[str], None] | None = None


_DELETE_TARGET_POLICIES: ProviderRegistry[None, DocumentDeleteTargetPolicy] = ProviderRegistry(
    "document delete target"
)


def _validate_target(target_store: str) -> None:
    if type(target_store) is not str or _STORE_CODE.fullmatch(target_store) is None:
        raise ValueError("document delete target must be a lowercase store code")


def _validate_policy(policy: DocumentDeleteTargetPolicy) -> None:
    if type(policy) is not DocumentDeleteTargetPolicy:
        raise TypeError("document delete target policy must be a DocumentDeleteTargetPolicy")
    if type(policy.order) is not int or policy.order < 0:
        raise ValueError("document delete target order must be a non-negative integer")
    if policy.runtime_check is not None:
        if not callable(policy.runtime_check):
            raise TypeError("document delete target runtime_check must be callable")
        callable_methods = (
            policy.runtime_check,
            getattr(policy.runtime_check, "__call__", None),
        )
        if any(
            inspect.iscoroutinefunction(method)
            or inspect.isasyncgenfunction(method)
            or inspect.isgeneratorfunction(method)
            for method in callable_methods
            if method is not None
        ):
            raise TypeError("document delete target runtime_check must be synchronous")
        try:
            inspect.signature(policy.runtime_check).bind("target_store")
        except (TypeError, ValueError) as exc:
            raise TypeError(
                "document delete target runtime_check must accept one positional target"
            ) from exc


def _register_document_delete_target(
    target_store: str,
    policy: DocumentDeleteTargetPolicy,
) -> None:
    """Register a custom delete target before creating workers or delete requests."""

    _validate_target(target_store)
    if target_store in _RESERVED_TARGETS:
        raise ValueError(f"document delete target {target_store!r} is built-in or reserved")
    _validate_policy(policy)
    if policy.runtime_check is None:
        raise ValueError("custom document delete target requires a runtime preflight")

    def policy_factory(_context: None) -> DocumentDeleteTargetPolicy:
        return policy

    with _REGISTRATION_LOCK:
        _DELETE_TARGET_POLICIES.register(target_store, policy_factory)


def _unregister_document_delete_target(target_store: str) -> None:
    """Remove a custom target; do not do so while its operations or data remain."""

    _validate_target(target_store)
    if target_store in _RESERVED_TARGETS:
        raise ValueError(f"built-in document delete target cannot be unregistered: {target_store}")
    with _REGISTRATION_LOCK:
        _DELETE_TARGET_POLICIES.unregister(target_store)


def _document_delete_target_snapshot() -> tuple[
    tuple[str, ...], dict[str, DocumentDeleteTargetPolicy]
]:
    with _REGISTRATION_LOCK:
        names = tuple(_DELETE_TARGET_POLICIES.names())
        policies = {
            target_store: _DELETE_TARGET_POLICIES.create(target_store, None)
            for target_store in names
        }

    missing_builtins = set(_BUILTIN_ORDERS).difference(policies)
    if missing_builtins:
        raise RuntimeError(
            f"required document delete targets are missing: {sorted(missing_builtins)!r}"
        )
    for target_store, expected_order in _BUILTIN_ORDERS.items():
        policy = policies[target_store]
        if (
            type(policy) is not DocumentDeleteTargetPolicy
            or type(policy.order) is not int
            or policy.order != expected_order
        ):
            raise RuntimeError(f"built-in document delete target was replaced: {target_store}")

    for target_store, policy in policies.items():
        _validate_target(target_store)
        _validate_policy(policy)
    targets = tuple(sorted(policies, key=lambda target: (policies[target].order, target)))
    return targets, policies


def _document_delete_projection_targets_snapshot() -> tuple[str, ...]:
    """Return the configured targets without invoking extension preflight callbacks."""

    targets, _policies = _document_delete_target_snapshot()
    return targets


def document_delete_projection_targets() -> tuple[str, ...]:
    """Return an ordered target snapshot after validating each custom runtime."""

    targets, policies = _document_delete_target_snapshot()
    for target_store in targets:
        if target_store in _RESERVED_TARGETS:
            continue
        runtime_check = policies[target_store].runtime_check
        if runtime_check is None:
            raise RuntimeError(
                f"custom document delete target {target_store!r} lacks runtime preflight"
            )
        result = runtime_check(target_store)
        if inspect.isawaitable(result) or inspect.isgenerator(result) or inspect.isasyncgen(result):
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
                    close_sync = getattr(close_awaitable, "close", None)
                    if callable(close_sync):
                        close_sync()
            raise TypeError("document delete target runtime_check must be synchronous")
        if result is not None:
            raise TypeError("document delete target runtime_check must return None")
    return targets


for _target_store, _order in _BUILTIN_ORDERS.items():

    def _builtin_factory(_context: None, *, _order: int = _order) -> DocumentDeleteTargetPolicy:
        return DocumentDeleteTargetPolicy(order=_order)

    _DELETE_TARGET_POLICIES.register(_target_store, _builtin_factory)

del _order, _target_store


__all__ = [
    "DocumentDeleteTargetPolicy",
    "document_delete_projection_targets",
]
