from __future__ import annotations

from threading import Event, Thread

import pytest

from core.generation_cache import GenerationAwareSingletonCache, GenerationCacheSaturated


def test_clear_during_an_old_build_cannot_repopulate_the_new_generation() -> None:
    old_started = Event()
    release_old = Event()
    values = iter(("old", "new"))

    def builder() -> str:
        value = next(values)
        if value == "old":
            old_started.set()
            assert release_old.wait(timeout=2)
        return value

    cache = GenerationAwareSingletonCache(builder)
    old_result: list[str] = []
    thread = Thread(target=lambda: old_result.append(cache.get()))
    thread.start()
    assert old_started.wait(timeout=2)

    cache.clear()
    assert cache.get() == "new"
    release_old.set()
    thread.join(timeout=2)

    assert old_result == ["old"]
    assert cache.get() == "new"


def test_clear_wakes_old_waiters_so_they_can_join_the_new_generation() -> None:
    old_started = Event()
    release_old = Event()
    values = iter(("old", "new"))

    def builder() -> str:
        value = next(values)
        if value == "old":
            old_started.set()
            assert release_old.wait(timeout=2)
        return value

    cache = GenerationAwareSingletonCache(builder)
    owner_result: list[str] = []
    waiter_result: list[str] = []
    owner = Thread(target=lambda: owner_result.append(cache.get()))
    waiter = Thread(target=lambda: waiter_result.append(cache.get()))
    owner.start()
    assert old_started.wait(timeout=2)
    waiter.start()

    cache.clear()
    waiter.join(timeout=2)
    assert waiter_result == ["new"]

    release_old.set()
    owner.join(timeout=2)
    assert owner_result == ["old"]


def test_builder_failure_is_not_cached_as_a_success_value() -> None:
    calls = 0

    def builder() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient")
        return "recovered"

    cache = GenerationAwareSingletonCache(builder)
    with pytest.raises(RuntimeError, match="transient"):
        cache.get()

    assert cache.get() == "recovered"
    assert calls == 2


def test_concurrent_callers_share_one_build_per_generation() -> None:
    started = Event()
    release = Event()
    calls = 0

    def builder() -> str:
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=2)
        return "value"

    cache = GenerationAwareSingletonCache(builder)
    results: list[str] = []
    threads = [Thread(target=lambda: results.append(cache.get())) for _ in range(4)]
    for thread in threads:
        thread.start()
    assert started.wait(timeout=2)
    release.set()
    for thread in threads:
        thread.join(timeout=2)

    assert sorted(results) == ["value"] * 4
    assert calls == 1


def test_stale_builds_are_bounded_instead_of_growing_on_every_reload() -> None:
    first_started = Event()
    release_first = Event()
    second_started = Event()
    release_second = Event()
    values = iter(("first", "second"))

    def builder() -> str:
        value = next(values)
        if value == "first":
            first_started.set()
            assert release_first.wait(timeout=2)
        else:
            second_started.set()
            assert release_second.wait(timeout=2)
        return value

    cache = GenerationAwareSingletonCache(builder, max_stale_builds=1)
    first_thread = Thread(target=cache.get)
    first_thread.start()
    assert first_started.wait(timeout=2)

    cache.clear()
    second_thread = Thread(target=cache.get)
    second_thread.start()
    assert second_started.wait(timeout=2)

    cache.clear()
    with pytest.raises(GenerationCacheSaturated):
        cache.get()

    release_first.set()
    release_second.set()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)
