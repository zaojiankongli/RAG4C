from __future__ import annotations

from server import documents


def test_index_worker_does_not_start_outside_active_mode(monkeypatch) -> None:
    monkeypatch.setattr(documents, "_configured_ingest_ledger_mode", lambda: "shadow")
    monkeypatch.setattr(
        documents.threading,
        "Thread",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("thread should not start")),
    )

    assert documents.start_index_operation_worker() is None


class FakeThread:
    def __init__(self, *, target, daemon: bool, name: str):
        self.target = target
        self.daemon = daemon
        self.name = name
        self.started = False
        self.joined = False

    def start(self) -> None:
        self.started = True

    def is_alive(self) -> bool:
        return self.started and not self.joined

    def join(self, timeout=None) -> None:
        self.joined = True


def test_active_index_worker_starts_and_stops_recreatably(monkeypatch) -> None:
    created: list[FakeThread] = []
    monkeypatch.setattr(documents, "_configured_ingest_ledger_mode", lambda: "active")
    monkeypatch.setattr(
        documents.threading,
        "Thread",
        lambda **kwargs: created.append(FakeThread(**kwargs)) or created[-1],
    )
    documents.shutdown_index_operation_worker()

    first = documents.start_index_operation_worker()
    documents.shutdown_index_operation_worker()
    second = documents.start_index_operation_worker()

    assert first is created[0]
    assert first.name == "rag-index-operations"
    assert first.daemon is True
    assert first.started is True
    assert first.joined is True
    assert second is created[1]
    assert second.started is True
    documents.shutdown_index_operation_worker()
