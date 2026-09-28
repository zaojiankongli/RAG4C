"""Generation-aware single-value caches.

This module contains the small amount of synchronization needed by process
level lazy components that can be invalidated while a previous build is still
running.  ``functools.lru_cache`` cannot express that boundary: a call which
started before ``cache_clear`` may finish afterwards and repopulate the cache
with a stale value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import Callable, Generic, TypeVar, cast


T = TypeVar("T")


class GenerationCacheSaturated(RuntimeError):
    """Too many obsolete builds are still running to start another one."""


@dataclass
class _BuildState(Generic[T]):
    """One in-flight build and the result shared with its waiters."""

    event: threading.Event = field(default_factory=threading.Event)
    completed: bool = False
    result: T | None = None
    error: BaseException | None = None


class GenerationAwareSingletonCache(Generic[T]):
    """Build and cache one value per invalidation generation.

    A generation is advanced by :meth:`clear`.  Builds started in an older
    generation may finish and be returned to the caller that started them, but
    they can never overwrite a value built for the current generation.
    Concurrent callers for the same generation share one build.  If an
    invalidation happens while a caller is waiting for an old build, that
    caller retries against the new generation instead of accepting the stale
    result.
    """

    def __init__(
        self,
        builder: Callable[[], T],
        *,
        max_stale_builds: int = 1,
    ) -> None:
        if not callable(builder):
            raise TypeError("builder must be callable")
        if type(max_stale_builds) is not int or max_stale_builds < 0:
            raise ValueError("max_stale_builds must be a non-negative integer")
        self._builder = builder
        self._max_stale_builds = max_stale_builds
        self._lock = threading.RLock()
        self._generation = 0
        self._cached_generation: int | None = None
        self._cached_value: T | None = None
        self._inflight: dict[int, _BuildState[T]] = {}

    @property
    def generation(self) -> int:
        """Return the current invalidation generation."""

        with self._lock:
            return self._generation

    def clear(self) -> None:
        """Invalidate the cached value and advance the generation."""

        with self._lock:
            self._generation += 1
            self._cached_generation = None
            self._cached_value = None
            # Wake callers waiting on an older generation. They will observe
            # the generation mismatch and join/build the current generation.
            for state in self._inflight.values():
                state.event.set()

    cache_clear = clear

    def get(self) -> T:
        """Return the current value, building it once for this generation."""

        while True:
            with self._lock:
                generation = self._generation
                if self._cached_generation == generation:
                    return cast(T, self._cached_value)

                state = self._inflight.get(generation)
                owner = state is None
                if owner:
                    stale_builds = sum(
                        1 for inflight_generation in self._inflight
                        if inflight_generation != generation
                    )
                    if stale_builds > self._max_stale_builds:
                        raise GenerationCacheSaturated(
                            "generation cache has too many obsolete builds in flight"
                        )
                    state = _BuildState()
                    self._inflight[generation] = state

            if not owner:
                state.event.wait()
                with self._lock:
                    # A clear may have happened while this caller was waiting.
                    # Do not consume an old result; loop and join/build the
                    # current generation instead.
                    if self._generation != generation:
                        continue
                    if state.error is not None:
                        raise state.error
                    if state.completed:
                        return cast(T, state.result)
                    # Defensive only: event publication and state mutation are
                    # performed under the same lock by the owner.
                    continue

            try:
                value = self._builder()
            except BaseException as exc:
                with self._lock:
                    state.error = exc
                    state.completed = True
                    self._inflight.pop(generation, None)
                    state.event.set()
                raise

            with self._lock:
                state.result = value
                state.completed = True
                if self._generation == generation:
                    self._cached_generation = generation
                    self._cached_value = value
                self._inflight.pop(generation, None)
                state.event.set()
            return value


__all__ = ["GenerationAwareSingletonCache", "GenerationCacheSaturated"]
