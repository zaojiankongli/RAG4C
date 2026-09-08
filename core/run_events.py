"""Strict, request-scoped run lifecycle events.

The sequencer is the single owner of ordering and lifecycle state for one run.
Business code should normally use :class:`RunObserver`, whose state errors are
failure-silent so observability cannot change query behavior.
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Protocol, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue as PydanticJsonValue,
    field_validator,
    model_validator,
)


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue = PydanticJsonValue
EventAttributes: TypeAlias = dict[str, PydanticJsonValue]
EventType: TypeAlias = Literal[
    "run.started",
    "node.started",
    "node.completed",
    "node.failed",
    "node.skipped",
    "node.cancelled",
    "route.selected",
    "retry.started",
    "retry.completed",
    "retry.failed",
    "retry.skipped",
    "degraded",
    "run.completed",
    "run.failed",
    "run.cancelled",
]

_LOGGER = logging.getLogger(__name__)
_NODE_TYPES = frozenset(
    {
        "node.started",
        "node.completed",
        "node.failed",
        "node.skipped",
        "node.cancelled",
    }
)
_RETRY_TYPES = frozenset(
    {"retry.started", "retry.completed", "retry.failed", "retry.skipped"}
)


def _json_value(value: Any, *, path: str = "attributes") -> JsonValue:
    value_type = type(value)
    if value is None or value_type in {str, int, bool}:
        return value
    if value_type is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite float")
        return value
    if value_type is list:
        return [
            _json_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    if value_type is dict:
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError(f"{path} contains a non-string object key")
            result[key] = _json_value(item, path=f"{path}.{key}")
        return result
    raise ValueError(f"{path} contains non-JSON value {value_type.__name__}")


class _FrozenDict(dict[str, JsonValue]):
    """A JSON object that supports reads but rejects every normal mutation API."""

    def _immutable(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("published run-event attributes are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


class _FrozenList(list[JsonValue]):
    """A JSON array that supports reads but rejects every normal mutation API."""

    def _immutable(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("published run-event attributes are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    append = _immutable
    clear = _immutable
    extend = _immutable
    insert = _immutable
    pop = _immutable
    remove = _immutable
    reverse = _immutable
    sort = _immutable
    __iadd__ = _immutable
    __imul__ = _immutable


def _freeze_json(value: JsonValue) -> JsonValue:
    if type(value) is dict:
        return _FrozenDict({key: _freeze_json(item) for key, item in value.items()})
    if type(value) is list:
        return _FrozenList(_freeze_json(item) for item in value)
    return value


class EventError(BaseModel):
    """Low-cardinality error metadata; exception messages are intentionally absent."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    type: str = Field(min_length=1)
    code: str | None = Field(default=None, min_length=1)
    recoverable: bool = False


class RunEvent(BaseModel):
    """Immutable canonical event envelope for one request run."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    seq: int = Field(ge=1)
    occurred_at: datetime
    elapsed_ms: float = Field(ge=0)
    topology_id: str = Field(min_length=1)
    topology_revision: str = Field(min_length=1)
    type: EventType
    node_id: str | None = None
    attempt: int | None = Field(default=None, ge=1)
    duration_ms: float | None = Field(default=None, ge=0)
    attributes: EventAttributes = Field(default_factory=dict)
    error: EventError | None = None

    @field_validator("occurred_at")
    @classmethod
    def _occurred_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value

    @field_validator("attributes", mode="before")
    @classmethod
    def _attributes_are_json(cls, value: Any) -> EventAttributes:
        checked = _json_value(value)
        if type(checked) is not dict:
            raise ValueError("attributes must be a JSON object")
        return checked

    @model_validator(mode="after")
    def _lifecycle_fields_match_type(self) -> "RunEvent":
        if self.type in _NODE_TYPES | _RETRY_TYPES:
            if not self.node_id:
                raise ValueError(f"{self.type} requires node_id")
            if self.attempt is None:
                raise ValueError(f"{self.type} requires attempt")
        if self.type.startswith("run.") and (self.node_id is not None or self.attempt is not None):
            raise ValueError(f"{self.type} cannot include node_id or attempt")
        object.__setattr__(self, "attributes", _freeze_json(self.attributes))
        return self


class RunEventSink(Protocol):
    def __call__(self, event: RunEvent, /) -> None: ...


class RunEventStateError(RuntimeError):
    """Raised by strict sequencer methods for an illegal lifecycle transition."""


class MemoryRunEventSink:
    """Thread-safe in-memory sink intended for tests and request-local collection."""

    def __init__(self) -> None:
        self._events: list[RunEvent] = []
        self._lock = threading.Lock()

    def __call__(self, event: RunEvent, /) -> None:
        with self._lock:
            self._events.append(event)

    @property
    def events(self) -> list[RunEvent]:
        with self._lock:
            return list(self._events)


@dataclass(slots=True)
class _AttemptState:
    repeatable: bool
    attempts: dict[int, str] = field(default_factory=dict)
    started_at: dict[int, float] = field(default_factory=dict)
    last_attempt: int = 0


class RunEventSequencer:
    """Own gapless event ordering and strict lifecycle state for one run."""

    def __init__(
        self,
        run_id: str,
        *,
        topology_id: str,
        topology_revision: str,
        monotonic: Callable[[], float] = time.monotonic,
        utcnow: Callable[[], datetime] | None = None,
        sinks: tuple[RunEventSink, ...] = (),
    ) -> None:
        if not run_id:
            raise ValueError("run_id must not be empty")
        if not topology_id:
            raise ValueError("topology_id must not be empty")
        if not topology_revision:
            raise ValueError("topology_revision must not be empty")
        self._run_id = run_id
        self._topology_id = topology_id
        self._topology_revision = topology_revision
        self._monotonic = monotonic
        self._utcnow = utcnow or (lambda: datetime.now(timezone.utc))
        self._sinks = tuple(sinks)
        self._lock = threading.RLock()
        self._started_at_monotonic = self._monotonic()
        self._last_elapsed_ms = 0.0
        self._next_seq = 1
        self._run_started = False
        self._terminal = False
        self._nodes: dict[str, _AttemptState] = {}
        self._retries: dict[str, _AttemptState] = {}

    def observer(self) -> "RunObserver":
        return RunObserver(self)

    def start_run(
        self,
        *,
        attributes: EventAttributes | None = None,
        **attribute_values: JsonValue,
    ) -> RunEvent:
        with self._lock:
            if self._run_started:
                raise RunEventStateError("run.started was already emitted")
            if self._terminal:
                raise RunEventStateError("run already has a terminal event")
            event = self._new_event_locked(
                "run.started",
                attributes=_merge_attributes(attributes, attribute_values),
            )
            if event.seq != 1:
                raise RunEventStateError("run.started must be seq 1")
            self._run_started = True
            self._publish_locked(event)
            return event

    def start_node(
        self,
        node_id: str,
        *,
        attempt: int = 1,
        repeatable: bool = False,
        attributes: EventAttributes | None = None,
        **attribute_values: JsonValue,
    ) -> RunEvent:
        with self._lock:
            self._ensure_running_locked()
            state = self._prepare_attempt_start_locked(
                self._nodes,
                node_id,
                attempt,
                repeatable=repeatable,
                label="node",
            )
            now = self._monotonic()
            event = self._new_event_locked(
                "node.started",
                node_id=node_id,
                attempt=attempt,
                attributes=_merge_attributes(attributes, attribute_values),
                now_monotonic=now,
            )
            self._nodes[node_id] = state
            state.attempts[attempt] = "active"
            state.started_at[attempt] = now
            state.last_attempt = attempt
            self._publish_locked(event)
            return event

    def complete_node(
        self,
        node_id: str,
        *,
        attempt: int = 1,
        duration_ms: float | None = None,
        attributes: EventAttributes | None = None,
        **attribute_values: JsonValue,
    ) -> RunEvent:
        return self._close_node(
            "node.completed",
            node_id,
            attempt=attempt,
            duration_ms=duration_ms,
            attributes=attributes,
            attribute_values=attribute_values,
        )

    def fail_node(
        self,
        node_id: str,
        *,
        attempt: int = 1,
        duration_ms: float | None = None,
        error: EventError | None = None,
        error_type: str | None = None,
        error_code: str | None = None,
        recoverable: bool = False,
        attributes: EventAttributes | None = None,
        **attribute_values: JsonValue,
    ) -> RunEvent:
        return self._close_node(
            "node.failed",
            node_id,
            attempt=attempt,
            duration_ms=duration_ms,
            error=_event_error(error, error_type, error_code, recoverable),
            attributes=attributes,
            attribute_values=attribute_values,
        )

    def cancel_node(
        self,
        node_id: str,
        *,
        attempt: int = 1,
        duration_ms: float | None = None,
        attributes: EventAttributes | None = None,
        **attribute_values: JsonValue,
    ) -> RunEvent:
        return self._close_node(
            "node.cancelled",
            node_id,
            attempt=attempt,
            duration_ms=duration_ms,
            attributes=attributes,
            attribute_values=attribute_values,
        )

    def skip_node(
        self,
        node_id: str,
        *,
        attempt: int = 1,
        attributes: EventAttributes | None = None,
        **attribute_values: JsonValue,
    ) -> RunEvent:
        with self._lock:
            self._ensure_running_locked()
            _validate_node_id(node_id)
            if attempt != 1:
                raise RunEventStateError(f"non-repeatable node {node_id!r} must use attempt 1")
            if node_id in self._nodes:
                raise RunEventStateError(f"started node {node_id!r} cannot be skipped")
            event = self._new_event_locked(
                "node.skipped",
                node_id=node_id,
                attempt=attempt,
                attributes=_merge_attributes(attributes, attribute_values),
            )
            self._nodes[node_id] = _AttemptState(
                repeatable=False,
                attempts={attempt: "skipped"},
                last_attempt=attempt,
            )
            self._publish_locked(event)
            return event

    def route_selected(
        self,
        node_id: str,
        *,
        route: str,
        attempt: int = 1,
        attributes: EventAttributes | None = None,
        **attribute_values: JsonValue,
    ) -> RunEvent:
        values = dict(attribute_values)
        values["route"] = route
        return self._emit_nonterminal(
            "route.selected",
            node_id=node_id,
            attempt=attempt,
            attributes=_merge_attributes(attributes, values),
        )

    def retry_started(
        self,
        node_id: str,
        *,
        attempt: int = 1,
        attributes: EventAttributes | None = None,
        **attribute_values: JsonValue,
    ) -> RunEvent:
        with self._lock:
            self._ensure_running_locked()
            state = self._prepare_attempt_start_locked(
                self._retries,
                node_id,
                attempt,
                repeatable=True,
                label="retry",
            )
            now = self._monotonic()
            event = self._new_event_locked(
                "retry.started",
                node_id=node_id,
                attempt=attempt,
                attributes=_merge_attributes(attributes, attribute_values),
                now_monotonic=now,
            )
            self._retries[node_id] = state
            state.attempts[attempt] = "active"
            state.started_at[attempt] = now
            state.last_attempt = attempt
            self._publish_locked(event)
            return event

    def retry_completed(
        self,
        node_id: str,
        *,
        attempt: int = 1,
        duration_ms: float | None = None,
        attributes: EventAttributes | None = None,
        **attribute_values: JsonValue,
    ) -> RunEvent:
        return self._close_retry(
            "retry.completed",
            node_id,
            attempt=attempt,
            duration_ms=duration_ms,
            attributes=attributes,
            attribute_values=attribute_values,
        )

    def retry_failed(
        self,
        node_id: str,
        *,
        attempt: int = 1,
        duration_ms: float | None = None,
        error: EventError | None = None,
        error_type: str | None = None,
        error_code: str | None = None,
        recoverable: bool = False,
        attributes: EventAttributes | None = None,
        **attribute_values: JsonValue,
    ) -> RunEvent:
        return self._close_retry(
            "retry.failed",
            node_id,
            attempt=attempt,
            duration_ms=duration_ms,
            error=_event_error(error, error_type, error_code, recoverable),
            attributes=attributes,
            attribute_values=attribute_values,
        )

    def retry_skipped(
        self,
        node_id: str,
        *,
        attempt: int = 1,
        attributes: EventAttributes | None = None,
        **attribute_values: JsonValue,
    ) -> RunEvent:
        with self._lock:
            self._ensure_running_locked()
            _validate_node_id(node_id)
            state = self._retries.get(node_id)
            if state is None:
                if attempt != 1:
                    raise RunEventStateError(f"retry {node_id!r} must begin with attempt 1")
                state = _AttemptState(repeatable=True)
            else:
                if attempt in state.attempts:
                    raise RunEventStateError(
                        f"retry {node_id!r} attempt {attempt} was already started or skipped"
                    )
                if any(status == "active" for status in state.attempts.values()):
                    raise RunEventStateError(f"retry {node_id!r} has an active attempt")
                if attempt != state.last_attempt + 1:
                    raise RunEventStateError(
                        f"retry {node_id!r} attempts must be contiguous; "
                        f"expected {state.last_attempt + 1}"
                    )
            event = self._new_event_locked(
                "retry.skipped",
                node_id=node_id,
                attempt=attempt,
                attributes=_merge_attributes(attributes, attribute_values),
            )
            self._retries[node_id] = state
            state.attempts[attempt] = "skipped"
            state.last_attempt = attempt
            self._publish_locked(event)
            return event

    def degraded(
        self,
        node_id: str | None = None,
        *,
        attempt: int | None = None,
        attributes: EventAttributes | None = None,
        **attribute_values: JsonValue,
    ) -> RunEvent:
        return self._emit_nonterminal(
            "degraded",
            node_id=node_id,
            attempt=attempt,
            attributes=_merge_attributes(attributes, attribute_values),
        )

    def complete_run(
        self,
        *,
        attributes: EventAttributes | None = None,
        **attribute_values: JsonValue,
    ) -> RunEvent:
        return self._terminal_event(
            "run.completed",
            attributes=_merge_attributes(attributes, attribute_values),
        )

    def fail_run(
        self,
        *,
        error: EventError | None = None,
        error_type: str | None = None,
        error_code: str | None = None,
        recoverable: bool = False,
        attributes: EventAttributes | None = None,
        **attribute_values: JsonValue,
    ) -> RunEvent:
        return self._terminal_event(
            "run.failed",
            error=_event_error(error, error_type, error_code, recoverable),
            attributes=_merge_attributes(attributes, attribute_values),
        )

    def cancel_run(
        self,
        *,
        attributes: EventAttributes | None = None,
        **attribute_values: JsonValue,
    ) -> RunEvent:
        return self._terminal_event(
            "run.cancelled",
            attributes=_merge_attributes(attributes, attribute_values),
        )

    def _close_node(
        self,
        event_type: Literal["node.completed", "node.failed", "node.cancelled"],
        node_id: str,
        *,
        attempt: int,
        duration_ms: float | None,
        attributes: EventAttributes | None,
        attribute_values: dict[str, JsonValue],
        error: EventError | None = None,
    ) -> RunEvent:
        with self._lock:
            self._ensure_running_locked()
            state = self._active_attempt_locked(self._nodes, node_id, attempt, "node")
            now = self._monotonic()
            actual_duration = _resolved_duration(duration_ms, now, state.started_at[attempt])
            event = self._new_event_locked(
                event_type,
                node_id=node_id,
                attempt=attempt,
                duration_ms=actual_duration,
                attributes=_merge_attributes(attributes, attribute_values),
                error=error,
                now_monotonic=now,
            )
            state.attempts[attempt] = "closed"
            self._publish_locked(event)
            return event

    def _close_retry(
        self,
        event_type: Literal["retry.completed", "retry.failed"],
        node_id: str,
        *,
        attempt: int,
        duration_ms: float | None,
        attributes: EventAttributes | None,
        attribute_values: dict[str, JsonValue],
        error: EventError | None = None,
    ) -> RunEvent:
        with self._lock:
            self._ensure_running_locked()
            state = self._active_attempt_locked(self._retries, node_id, attempt, "retry")
            now = self._monotonic()
            actual_duration = _resolved_duration(duration_ms, now, state.started_at[attempt])
            event = self._new_event_locked(
                event_type,
                node_id=node_id,
                attempt=attempt,
                duration_ms=actual_duration,
                attributes=_merge_attributes(attributes, attribute_values),
                error=error,
                now_monotonic=now,
            )
            state.attempts[attempt] = "closed"
            self._publish_locked(event)
            return event

    def _emit_nonterminal(
        self,
        event_type: EventType,
        *,
        node_id: str | None = None,
        attempt: int | None = None,
        attributes: EventAttributes,
    ) -> RunEvent:
        with self._lock:
            self._ensure_running_locked()
            event = self._new_event_locked(
                event_type,
                node_id=node_id,
                attempt=attempt,
                attributes=attributes,
            )
            self._publish_locked(event)
            return event

    def _terminal_event(
        self,
        event_type: Literal["run.completed", "run.failed", "run.cancelled"],
        *,
        attributes: EventAttributes,
        error: EventError | None = None,
    ) -> RunEvent:
        with self._lock:
            self._ensure_running_locked()
            active_nodes = _active_labels(self._nodes)
            active_retries = _active_labels(self._retries)
            if active_nodes or active_retries:
                active = ", ".join(active_nodes + active_retries)
                raise RunEventStateError(f"cannot emit terminal with active node or retry: {active}")
            event = self._new_event_locked(event_type, attributes=attributes, error=error)
            self._terminal = True
            self._publish_locked(event)
            return event

    def _ensure_running_locked(self) -> None:
        if not self._run_started:
            raise RunEventStateError("run.started must be emitted first")
        if self._terminal:
            raise RunEventStateError("run already has a terminal event")

    def _prepare_attempt_start_locked(
        self,
        collection: dict[str, _AttemptState],
        node_id: str,
        attempt: int,
        *,
        repeatable: bool,
        label: str,
    ) -> _AttemptState:
        _validate_node_id(node_id)
        if attempt < 1:
            raise RunEventStateError(f"{label} attempt must be at least 1")
        state = collection.get(node_id)
        if state is None:
            if attempt != 1:
                raise RunEventStateError(f"{label} {node_id!r} must begin with attempt 1")
            return _AttemptState(repeatable=repeatable)
        if attempt in state.attempts:
            raise RunEventStateError(f"{label} {node_id!r} attempt {attempt} already started")
        if not state.repeatable:
            raise RunEventStateError(f"{label} {node_id!r} is not repeatable")
        if any(status == "active" for status in state.attempts.values()):
            raise RunEventStateError(f"{label} {node_id!r} has an active attempt")
        if attempt != state.last_attempt + 1:
            raise RunEventStateError(
                f"{label} {node_id!r} attempts must be contiguous; expected {state.last_attempt + 1}"
            )
        return state

    def _active_attempt_locked(
        self,
        collection: dict[str, _AttemptState],
        node_id: str,
        attempt: int,
        label: str,
    ) -> _AttemptState:
        _validate_node_id(node_id)
        state = collection.get(node_id)
        if state is None or state.attempts.get(attempt) != "active":
            raise RunEventStateError(f"{label} {node_id!r} attempt {attempt} is not active")
        return state

    def _new_event_locked(
        self,
        event_type: EventType,
        *,
        node_id: str | None = None,
        attempt: int | None = None,
        duration_ms: float | None = None,
        attributes: EventAttributes,
        error: EventError | None = None,
        now_monotonic: float | None = None,
    ) -> RunEvent:
        now = self._monotonic() if now_monotonic is None else now_monotonic
        elapsed_ms = max(0.0, (now - self._started_at_monotonic) * 1000.0)
        elapsed_ms = max(self._last_elapsed_ms, elapsed_ms)
        event = RunEvent(
            run_id=self._run_id,
            seq=self._next_seq,
            occurred_at=self._utcnow(),
            elapsed_ms=elapsed_ms,
            topology_id=self._topology_id,
            topology_revision=self._topology_revision,
            type=event_type,
            node_id=node_id,
            attempt=attempt,
            duration_ms=duration_ms,
            attributes=attributes,
            error=error,
        )
        self._last_elapsed_ms = elapsed_ms
        return event

    def _publish_locked(self, event: RunEvent) -> None:
        self._next_seq += 1
        for sink in self._sinks:
            try:
                sink(event)
            except Exception:
                _LOGGER.debug("run event sink failed", exc_info=True)


class RunObserver:
    """Failure-silent facade for business-code instrumentation."""

    def __init__(self, sequencer: RunEventSequencer) -> None:
        self._sequencer = sequencer

    def _call(self, name: str, *args: Any, **kwargs: Any) -> RunEvent | None:
        try:
            method = getattr(self._sequencer, name)
            return method(*args, **kwargs)
        except RunEventStateError:
            _LOGGER.debug("run event lifecycle transition rejected", exc_info=True)
            return None

    def start_run(self, **kwargs: Any) -> RunEvent | None:
        return self._call("start_run", **kwargs)

    def start_node(self, node_id: str, **kwargs: Any) -> RunEvent | None:
        return self._call("start_node", node_id, **kwargs)

    def complete_node(self, node_id: str, **kwargs: Any) -> RunEvent | None:
        return self._call("complete_node", node_id, **kwargs)

    def fail_node(self, node_id: str, **kwargs: Any) -> RunEvent | None:
        return self._call("fail_node", node_id, **kwargs)

    def skip_node(self, node_id: str, **kwargs: Any) -> RunEvent | None:
        return self._call("skip_node", node_id, **kwargs)

    def cancel_node(self, node_id: str, **kwargs: Any) -> RunEvent | None:
        return self._call("cancel_node", node_id, **kwargs)

    def route_selected(self, node_id: str, **kwargs: Any) -> RunEvent | None:
        return self._call("route_selected", node_id, **kwargs)

    def retry_started(self, node_id: str, **kwargs: Any) -> RunEvent | None:
        return self._call("retry_started", node_id, **kwargs)

    def retry_completed(self, node_id: str, **kwargs: Any) -> RunEvent | None:
        return self._call("retry_completed", node_id, **kwargs)

    def retry_failed(self, node_id: str, **kwargs: Any) -> RunEvent | None:
        return self._call("retry_failed", node_id, **kwargs)

    def retry_skipped(self, node_id: str, **kwargs: Any) -> RunEvent | None:
        return self._call("retry_skipped", node_id, **kwargs)

    def degraded(self, node_id: str | None = None, **kwargs: Any) -> RunEvent | None:
        return self._call("degraded", node_id, **kwargs)

    def complete_run(self, **kwargs: Any) -> RunEvent | None:
        return self._call("complete_run", **kwargs)

    def fail_run(self, **kwargs: Any) -> RunEvent | None:
        return self._call("fail_run", **kwargs)

    def cancel_run(self, **kwargs: Any) -> RunEvent | None:
        return self._call("cancel_run", **kwargs)


def _merge_attributes(
    attributes: EventAttributes | None,
    attribute_values: dict[str, JsonValue],
) -> EventAttributes:
    merged: dict[str, JsonValue] = dict(attributes or {})
    duplicates = merged.keys() & attribute_values.keys()
    if duplicates:
        duplicate = sorted(duplicates)[0]
        raise ValueError(f"duplicate event attribute {duplicate!r}")
    merged.update(attribute_values)
    checked = _json_value(merged)
    if type(checked) is not dict:
        raise ValueError("attributes must be a JSON object")
    return checked


def _event_error(
    error: EventError | None,
    error_type: str | None,
    error_code: str | None,
    recoverable: bool,
) -> EventError:
    if error is not None:
        if error_type is not None or error_code is not None or recoverable:
            raise ValueError("pass either error or error fields, not both")
        return error
    if error_type is None:
        raise ValueError("failed events require error or error_type")
    return EventError(type=error_type, code=error_code, recoverable=recoverable)


def _resolved_duration(provided: float | None, now: float, started_at: float) -> float:
    if provided is not None:
        return provided
    return max(0.0, (now - started_at) * 1000.0)


def _validate_node_id(node_id: str) -> None:
    if not node_id:
        raise RunEventStateError("node_id must not be empty")


def _active_labels(collection: dict[str, _AttemptState]) -> list[str]:
    return [
        f"{node_id}#{attempt}"
        for node_id, state in collection.items()
        for attempt, status in state.attempts.items()
        if status == "active"
    ]


def run_event_dict(event: RunEvent) -> dict[str, Any]:
    """Return the canonical JSON-compatible event mapping."""

    return event.model_dump(mode="json", exclude_none=True)


def run_event_json(event: RunEvent) -> str:
    """Serialize one event as compact UTF-8-friendly strict JSON."""

    return json.dumps(
        run_event_dict(event),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


__all__ = [
    "EventError",
    "MemoryRunEventSink",
    "RunEvent",
    "RunEventSequencer",
    "RunEventSink",
    "RunObserver",
    "run_event_dict",
    "run_event_json",
]
