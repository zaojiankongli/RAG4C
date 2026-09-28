"""Thread-safe provider registries used by pluggable backend services."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Generic, TypeVar

ConfigT = TypeVar("ConfigT")
ServiceT = TypeVar("ServiceT")


class UnknownProviderError(ValueError):
    """A named provider is absent; errors inside a registered factory propagate unchanged."""


class ProviderRegistry(Generic[ConfigT, ServiceT]):
    """Small, typed registry for one provider family.

    Registries are intentionally separated by service type.  An embedding
    provider can therefore never be selected as an LLM provider by mistake.
    """

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._factories: dict[str, Callable[[ConfigT], ServiceT]] = {}
        self._lock = threading.RLock()

    def register(
        self,
        name: str,
        factory: Callable[[ConfigT], ServiceT],
        *,
        replace: bool = False,
    ) -> None:
        key = name.strip().lower()
        if not key:
            raise ValueError("provider name must not be empty")
        if not callable(factory):
            raise TypeError("provider factory must be callable")
        with self._lock:
            if key in self._factories and not replace:
                raise ValueError(f"{self.kind} provider already registered: {key}")
            self._factories[key] = factory

    def unregister(self, name: str) -> None:
        with self._lock:
            self._factories.pop(name.strip().lower(), None)

    def names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._factories))

    def get_factory(self, name: str) -> Callable[[ConfigT], ServiceT]:
        """Resolve a registered factory without invoking it.

        Keeping lookup separate lets dispatchers distinguish a missing name from
        exceptions raised by the selected implementation.
        """
        key = name.strip().lower()
        with self._lock:
            factory = self._factories.get(key)
        if factory is not None:
            return factory
        # Only a refusal needs the menu. Building it on every hit costs a sort per
        # lookup, which is invisible for "create a client once" and expensive for a
        # registry someone reads on a per-event path.
        with self._lock:
            choices = tuple(sorted(self._factories))
        available = " / ".join(choices) or "(none)"
        raise UnknownProviderError(
            f"unknown {self.kind} provider: {name!r} (available: {available})"
        )

    def create(self, name: str, config: ConfigT) -> ServiceT:
        return self.get_factory(name)(config)


__all__ = ["ProviderRegistry", "UnknownProviderError"]
