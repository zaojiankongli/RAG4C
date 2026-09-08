"""Durable at-least-once source execution dispatcher with leases and heartbeats."""

from __future__ import annotations

import queue
import threading
import time
import uuid
from concurrent.futures import Future, wait
from pathlib import Path
from typing import Any, Callable

from core import catalog
from core.source_schedules import SourceScheduleRepository
from core.source_sync_ledger import SourceSyncLedger, SourceSyncLedgerConflict
from models.orm import DataSourceRecord, SourceSyncRun
from server import documents as documents_api
from sources.runner import SourceSpec, SourceSyncer


def _engine(application: Any) -> Any:
    configured = getattr(application.state, "knowledge_auth_engine", None)
    return configured if configured is not None else catalog.get_engine()


def _settings(application: Any) -> Any:
    configured = getattr(application.state, "knowledge_auth_settings", None)
    if configured is not None:
        return configured
    from config.settings import get_settings

    return get_settings()


def _source_spec(source: DataSourceRecord) -> SourceSpec:
    effective = source.effective_config if isinstance(source.effective_config, dict) else {}
    return SourceSpec(
        name=source.name,
        type=source.source_type,
        dataset_id=source.dataset_id,
        params=dict(effective.get("params") or {}),
        metadata=dict(effective.get("metadata") or {}),
        tenant_id=source.tenant_id,
        enabled=source.status == "active",
    )


class _DaemonWorkerPool:
    def __init__(self, max_workers: int, queue_size: int) -> None:
        self._queue: queue.Queue[tuple[Future[Any], Callable[[], Any]] | None] = queue.Queue(
            maxsize=max(queue_size, max_workers)
        )
        self._closed = False
        self._lock = threading.Lock()
        self._threads = [
            threading.Thread(
                target=self._worker,
                daemon=True,
                name=f"source-execution-{index}",
            )
            for index in range(max_workers)
        ]
        for thread in self._threads:
            thread.start()

    def _worker(self) -> None:
        while True:
            entry = self._queue.get()
            try:
                if entry is None:
                    return
                future, callback = entry
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    future.set_result(callback())
                except BaseException as exc:  # preserve process-death simulation
                    future.set_exception(exc)
            finally:
                self._queue.task_done()

    def submit(self, callback: Callable[[], Any]) -> Future[Any]:
        with self._lock:
            if self._closed:
                raise RuntimeError("source worker queue is closed")
            future: Future[Any] = Future()
            self._queue.put_nowait((future, callback))
            return future

    def shutdown(self, *, cancel_futures: bool) -> None:
        with self._lock:
            self._closed = True
        if cancel_futures:
            while True:
                try:
                    entry = self._queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    if entry is not None:
                        entry[0].cancel()
                finally:
                    self._queue.task_done()
        for _thread in self._threads:
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                break


class SourceExecutionCancelled(SourceSyncLedgerConflict):
    pass


class ExecutionGuard:
    def __init__(
        self,
        ledger: SourceSyncLedger,
        run_id: str,
        owner: str,
        lease_seconds: float,
        cancellation: threading.Event | None = None,
    ) -> None:
        self.ledger = ledger
        self.run_id = run_id
        self.owner = owner
        self._lease_deadline_monotonic = time.monotonic() + lease_seconds
        self._cancelled = threading.Event()
        self._external_cancellation = cancellation
        self._lock = threading.Lock()

    @property
    def lease_deadline_monotonic(self) -> float:
        with self._lock:
            return self._lease_deadline_monotonic

    def renew(self, lease_seconds: float) -> None:
        with self._lock:
            self._lease_deadline_monotonic = time.monotonic() + lease_seconds

    def cancel(self) -> None:
        self._cancelled.set()

    def check(self) -> None:
        if self._cancelled.is_set() or (
            self._external_cancellation is not None
            and self._external_cancellation.is_set()
        ):
            raise SourceExecutionCancelled("source execution lease cancelled")
        self.ledger.assert_execution_owned(self.run_id, owner=self.owner)


class _OwnedExecutionLedger:
    """Bind SourceSyncer ledger calls to one durable execution owner."""

    def __init__(
        self,
        ledger: SourceSyncLedger,
        source: DataSourceRecord,
        run: SourceSyncRun,
        owner: str,
        guard: ExecutionGuard,
    ) -> None:
        self._ledger = ledger
        self.engine = ledger.engine
        self._source = source
        self._run = run
        self._owner = owner
        self._guard = guard

    @property
    def execution_owner(self) -> str:
        return self._owner

    def ensure_source(self, _spec: SourceSpec) -> DataSourceRecord:
        return self._ledger.get_source_scoped(
            self._source.tenant_id, self._source.dataset_id, self._source.id
        )

    def start_run(self, _source_id: str, **_kwargs: Any) -> SourceSyncRun:
        return self._ledger.get_run_scoped(
            self._source.tenant_id,
            self._source.dataset_id,
            self._source.id,
            self._run.id,
        )

    def assert_run_current(self, run_id: str) -> SourceSyncRun:
        self._guard.check()
        return self._ledger.assert_run_current(run_id)

    def record_item(self, run_id: str, **kwargs: Any) -> Any:
        self._guard.check()
        return self._ledger.record_item(
            run_id, execution_owner=self._owner, **kwargs
        )

    def upsert_state(self, source_id: str, **kwargs: Any) -> Any:
        self._guard.check()
        return self._ledger.upsert_state(
            source_id, execution_owner=self._owner, **kwargs
        )

    def finish_run(self, run_id: str, **kwargs: Any) -> SourceSyncRun:
        self._guard.check()
        return self._ledger.finish_run(
            run_id,
            execution_owner=self._owner,
            **kwargs,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ledger, name)


def _default_execute(
    application: Any,
    ledger: SourceSyncLedger,
    source: DataSourceRecord,
    run: SourceSyncRun,
    owner: str,
    guard: ExecutionGuard,
) -> None:
    settings = _settings(application)
    pipeline_factory = getattr(
        application.state,
        "knowledge_source_pipeline_factory",
        documents_api._get_ingest_pipeline,
    )
    cache_dir = Path(
        getattr(getattr(settings, "sources", None), "cache_dir", ".rag4c_cache/sources")
    )
    SourceSyncer(
        pipeline_factory(),
        cache_dir,
        settings,
        ledger=_OwnedExecutionLedger(ledger, source, run, owner, guard),
        state_mode="database",
        execution_guard=guard,
    ).sync(_source_spec(source), force=bool(run.force_full), dry_run=bool(run.dry_run))


def _policy(application: Any) -> Any:
    return getattr(_settings(application), "sources", None)


def execute_source_run(
    application: Any,
    run_id: str,
    *,
    lease_seconds: float,
    heartbeat_seconds: float,
    reservation_owner: str | None = None,
    cancellation: threading.Event | None = None,
) -> Callable[[], str]:
    """Build one durable at-least-once execution attempt."""

    if lease_seconds <= 0 or heartbeat_seconds <= 0 or heartbeat_seconds >= lease_seconds:
        raise ValueError("execution heartbeat must be positive and shorter than the lease")

    def task() -> str:
        ledger = SourceSyncLedger(_engine(application))
        owner = f"source-execution-{uuid.uuid4().hex}"
        run = ledger.claim_execution(
            run_id,
            owner=owner,
            lease_seconds=lease_seconds,
            reservation_owner=reservation_owner,
        )
        if run is None:
            return "not_claimed"
        source = ledger.get_source_scoped(run.tenant_id, run.dataset_id, run.source_id)
        stop_heartbeat = threading.Event()
        guard = ExecutionGuard(ledger, run.id, owner, lease_seconds, cancellation)
        safety_margin = max(0.05, min(heartbeat_seconds, lease_seconds / 3))
        retry_interval = max(0.01, min(0.1, heartbeat_seconds / 4))

        def heartbeat() -> None:
            while not stop_heartbeat.wait(heartbeat_seconds):
                while not stop_heartbeat.is_set():
                    if cancellation is not None and cancellation.is_set():
                        guard.cancel()
                        return
                    if time.monotonic() + safety_margin >= guard.lease_deadline_monotonic:
                        guard.cancel()
                        return
                    try:
                        ledger.heartbeat_execution(
                            run.id,
                            owner=owner,
                            lease_seconds=lease_seconds,
                        )
                        guard.renew(lease_seconds)
                        break
                    except Exception:
                        if stop_heartbeat.wait(retry_interval):
                            return

        heartbeat_thread = threading.Thread(
            target=heartbeat,
            daemon=True,
            name=f"source-heartbeat-{run.id[:24]}",
        )
        heartbeat_thread.start()
        try:
            guard.check()
            executor = getattr(application.state, "knowledge_source_sync_executor", None)
            if executor is not None:
                executor(_source_spec(source), run.id)
                guard.check()
                ledger.finish_run(
                    run.id,
                    status="completed",
                    cursor_after=run.cursor_before or {},
                    counts={},
                    execution_owner=owner,
                )
            else:
                _default_execute(application, ledger, source, run, owner, guard)
            return "completed"
        except (SourceExecutionCancelled, SourceSyncLedgerConflict):
            return "lease_lost"
        except Exception as exc:  # noqa: BLE001 - retryable execution infrastructure failure
            try:
                guard.check()
                settings = _policy(application)
                ledger.mark_execution_failed(
                    run.id,
                    owner=owner,
                    error=str(exc),
                    retry_base_seconds=float(
                        getattr(settings, "execution_retry_base_seconds", 1.0)
                    ),
                    retry_max_seconds=float(
                        getattr(settings, "execution_retry_max_seconds", 300.0)
                    ),
                    retry_jitter_ratio=float(
                        getattr(settings, "execution_retry_jitter_ratio", 0.2)
                    ),
                    max_attempts=int(getattr(settings, "execution_max_attempts", 10)),
                )
                return "failed"
            except (SourceExecutionCancelled, SourceSyncLedgerConflict):
                return "lease_lost"
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join(timeout=max(0.2, heartbeat_seconds * 2))

    return task


class SourceDispatchRuntime:
    """Bounded cross-replica dispatcher using durable reservation leases."""

    def __init__(
        self,
        application: Any,
        *,
        poll_interval_seconds: float = 1.0,
        lease_seconds: float = 30.0,
        heartbeat_seconds: float = 10.0,
        max_workers: int = 2,
        batch_size: int = 50,
        reservation_seconds: float = 5.0,
        shutdown_grace_seconds: float = 5.0,
    ) -> None:
        if poll_interval_seconds <= 0 or max_workers < 1 or batch_size < 1:
            raise ValueError("source dispatcher settings must be positive")
        if heartbeat_seconds <= 0 or heartbeat_seconds >= lease_seconds:
            raise ValueError("heartbeat must be shorter than execution lease")
        if reservation_seconds <= 0 or shutdown_grace_seconds < 0:
            raise ValueError("reservation and shutdown grace must be nonnegative")
        self.application = application
        self.poll_interval_seconds = poll_interval_seconds
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.batch_size = batch_size
        self.reservation_seconds = reservation_seconds
        self.shutdown_grace_seconds = shutdown_grace_seconds
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._scheduled: set[str] = set()
        self._futures: set[Future[Any]] = set()
        self._executor = _DaemonWorkerPool(
            max_workers=max_workers,
            queue_size=max(batch_size, max_workers * 2),
        )
        self._thread: threading.Thread | None = None

    def start(self) -> "SourceDispatchRuntime":
        # Authority-critical probe: startup must fail if durable recovery cannot read.
        SourceSyncLedger(_engine(self.application)).list_dispatchable_run_ids(limit=1)
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop,
                daemon=True,
                name="source-dispatcher",
            )
            self._thread.start()
        return self

    def submit_reserved(
        self,
        run_id: str,
        reservation_owner: str,
        *,
        slot_held: bool = False,
    ) -> bool:
        if not slot_held:
            with self._lock:
                if self._stop.is_set() or run_id in self._scheduled:
                    return False
                self._scheduled.add(run_id)
        task = execute_source_run(
            self.application,
            run_id,
            lease_seconds=self.lease_seconds,
            heartbeat_seconds=self.heartbeat_seconds,
            reservation_owner=reservation_owner,
            cancellation=self._stop,
        )

        def invoke() -> str:
            return task()

        try:
            future = self._executor.submit(invoke)
        except Exception:
            with self._lock:
                self._scheduled.discard(run_id)
            return False
        with self._lock:
            self._futures.add(future)

        def done(completed: Future[Any]) -> None:
            with self._lock:
                self._scheduled.discard(run_id)
                self._futures.discard(completed)

        future.add_done_callback(done)
        return True

    def submit_run(self, run_id: str) -> bool:
        with self._lock:
            if self._stop.is_set() or run_id in self._scheduled:
                return False
            self._scheduled.add(run_id)
        ledger = SourceSyncLedger(_engine(self.application))
        reservation_owner = f"source-reservation-{uuid.uuid4().hex}"
        try:
            reserved = ledger.reserve_run_dispatch(
                run_id,
                owner=reservation_owner,
                lease_seconds=self.reservation_seconds,
            )
        except Exception:
            reserved = False
        if not reserved:
            with self._lock:
                self._scheduled.discard(run_id)
            return False
        return self.submit_reserved(run_id, reservation_owner, slot_held=True)

    def wake(self) -> None:
        self._wake.set()

    def _poll_once(self) -> None:
        engine = _engine(self.application)
        try:
            SourceScheduleRepository(engine).enqueue_due(limit=self.batch_size)
        except Exception:
            # Schedule authority is isolated from already-durable pending work.
            # A transient schedule failure must not stall normal outbox recovery.
            pass
        ledger = SourceSyncLedger(engine)
        reservation_owner = f"source-reservation-{uuid.uuid4().hex}"
        run_ids = ledger.reserve_dispatch_batch(
            owner=reservation_owner,
            lease_seconds=self.reservation_seconds,
            limit=self.batch_size,
        )
        for run_id in run_ids:
            self.submit_reserved(run_id, reservation_owner)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception:
                pass
            self._wake.wait(self.poll_interval_seconds)
            self._wake.clear()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=min(1.0, max(0.05, self.shutdown_grace_seconds)))
        with self._lock:
            futures = set(self._futures)
        for future in futures:
            future.cancel()
        if futures and self.shutdown_grace_seconds > 0:
            wait(futures, timeout=self.shutdown_grace_seconds)
        self._executor.shutdown(cancel_futures=True)


def request_source_dispatch(application: Any, run_id: str) -> bool:
    """Best-effort immediate reservation and enqueue; durable state stays nonterminal."""

    runtime = getattr(application.state, "knowledge_source_dispatcher", None)
    if runtime is not None:
        return bool(runtime.submit_run(run_id))
    lock = getattr(application.state, "knowledge_source_immediate_dispatch_lock", None)
    if lock is None:
        lock = threading.Lock()
        application.state.knowledge_source_immediate_dispatch_lock = lock
    scheduled = getattr(application.state, "knowledge_source_immediate_dispatch_ids", None)
    if scheduled is None:
        scheduled = set()
        application.state.knowledge_source_immediate_dispatch_ids = scheduled
    with lock:
        if run_id in scheduled:
            return False
        scheduled.add(run_id)
    settings = _policy(application)
    reservation_seconds = float(getattr(settings, "reservation_lease_seconds", 5.0))
    reservation_owner = f"source-reservation-{uuid.uuid4().hex}"
    ledger = SourceSyncLedger(_engine(application))
    try:
        if not ledger.reserve_run_dispatch(
            run_id,
            owner=reservation_owner,
            lease_seconds=reservation_seconds,
        ):
            return False
    except Exception:
        with lock:
            scheduled.discard(run_id)
        return False
    task = execute_source_run(
        application,
        run_id,
        lease_seconds=float(getattr(settings, "execution_lease_seconds", 30.0)),
        heartbeat_seconds=float(getattr(settings, "execution_heartbeat_seconds", 10.0)),
        reservation_owner=reservation_owner,
    )
    def invoke() -> str:
        try:
            return task()
        finally:
            with lock:
                scheduled.discard(run_id)

    submitter = getattr(application.state, "knowledge_source_task_submitter", None)
    try:
        if submitter is not None:
            submitter(invoke)
        else:
            documents_api.start_ingest_executor().submit(invoke)
    except Exception:
        with lock:
            scheduled.discard(run_id)
        return False
    return True


def start_source_dispatch_runtime(application: Any, settings: Any) -> SourceDispatchRuntime:
    configured = getattr(settings, "sources", None)
    runtime = SourceDispatchRuntime(
        application,
        poll_interval_seconds=float(
            getattr(configured, "dispatch_poll_interval_seconds", 1.0)
        ),
        lease_seconds=float(getattr(configured, "execution_lease_seconds", 30.0)),
        heartbeat_seconds=float(
            getattr(configured, "execution_heartbeat_seconds", 10.0)
        ),
        max_workers=int(getattr(configured, "execution_workers", 2)),
        batch_size=int(getattr(configured, "dispatch_batch_size", 50)),
        reservation_seconds=float(getattr(configured, "reservation_lease_seconds", 5.0)),
        shutdown_grace_seconds=float(
            getattr(configured, "execution_shutdown_grace_seconds", 5.0)
        ),
    ).start()
    application.state.knowledge_source_dispatcher = runtime
    return runtime


__all__ = [
    "SourceDispatchRuntime",
    "execute_source_run",
    "request_source_dispatch",
    "start_source_dispatch_runtime",
]
