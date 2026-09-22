"""The run ``event_type`` behavioural partition — declared once.

Before this module the same partition of run event types was hand-mirrored three
times: ``core/run_events.py`` kept ``_NODE_TYPES`` / ``_RETRY_TYPES`` for the
envelope validator, ``core/run_registry.py`` re-typed the two terminal sets inside
its live reducer, and ``server/run_ops.py`` re-typed them again as literals inside
the durable-read reducer. Adding one event type cost ~8 edits across two files, and
nothing kept the three copies in step — so they were free to drift.

One :class:`RunEventTypeSpec` per event type now carries what the reducers used to
hard-code: the family, whether it opens or closes a lifecycle slot, and which effect
it has (active slot, rollup status, counter, run status). Both reducers look the
entry up instead of re-declaring membership.

The kernel is :class:`core.providers.ProviderRegistry`, same as the SQL dialect,
storage provider, chunking-mode, chunk-writer and retrieval-stage axes.

Two asymmetries between the reducers are deliberately *not* unified here, because
unifying them would change what each one counts:

* ``node.failed`` populates ``record.failed_nodes`` only in the live registry
  projection, and ``route.selected`` sets ``record.route`` only there.
  ``server/run_ops.py::_rollups_from_events`` rebuilds node rollups and nothing else,
  so it never consults ``records_failed_node`` / ``records_route`` / ``counts_retry`` /
  ``run_status``. The flags describe the vocabulary; each reducer still chooses which
  effects it applies.

An unknown event type resolves to ``None`` and is a no-op in both reducers, exactly as
before: neither used to raise on an unrecognized ``type`` and neither does now. The
closed wire vocabulary stays in :data:`core.run_events.EventType` — that ``Literal`` is
the pydantic field contract the frontend mirrors — and ``core/run_events.py`` asserts at
import time that the ``Literal`` and this table hold the same names.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

from core.providers import ProviderRegistry

__all__ = [
    "BUILTIN_RUN_EVENT_SPECS",
    "RUN_EVENT_TYPE_SPECS",
    "EventFamily",
    "EventPhase",
    "RunEventTypeSpec",
    "RunTerminalStatus",
    "register_run_event_type",
    "resolve_run_event_type",
    "unregister_run_event_type",
    "run_event_types_in_family",
    "run_event_types_with",
    "run_terminal_status",
]

EventFamily = Literal["run", "node", "retry", "annotation"]
EventPhase = Literal["start", "terminal", "standalone"]
RunTerminalStatus = Literal["completed", "failed", "cancelled"]

_FAMILIES = ("run", "node", "retry", "annotation")
_PHASES = ("start", "terminal", "standalone")
# 从上面的 Literal 派生，不再手抄一遍值集。
_RUN_TERMINAL_STATUSES: tuple[str, ...] = get_args(RunTerminalStatus)


@dataclass(frozen=True, slots=True)
class RunEventTypeSpec:
    """What every consumer of one run event type needs to know about it.

    The slot and rollup fields are shared by both reducers. ``records_failed_node``,
    ``records_route``, ``counts_retry`` and ``run_status`` are consulted only by the
    live registry projection (see the module docstring).
    """

    event_type: str
    family: EventFamily
    phase: EventPhase
    # Event-type prefix carrying the status word: "node." for node.completed.
    rollup_namespace: str = ""
    # Decoration on the status word so node and retry rollups stay distinguishable:
    # "" for node.*, "retry_" for retry.*.
    rollup_prefix: str = ""
    # Whether the envelope must carry node_id + attempt (core/run_events.py validator).
    lifecycle_fields_required: bool = False
    # The status a run terminal event drives the projected run status to.
    run_status: RunTerminalStatus | None = None
    counts_retry: bool = False
    counts_degraded: bool = False
    records_route: bool = False
    records_failed_node: bool = False

    @property
    def tracks_active_slot(self) -> bool:
        """Does this event open (start) or close (terminal) a node/retry slot?"""
        return self.family in {"node", "retry"} and self.phase in {"start", "terminal"}

    @property
    def opens_active_slot(self) -> bool:
        return self.tracks_active_slot and self.phase == "start"

    @property
    def closes_active_slot(self) -> bool:
        return self.tracks_active_slot and self.phase == "terminal"

    @property
    def owns_rollups(self) -> bool:
        return self.rollup_namespace != ""

    @property
    def carries_retry_reason(self) -> bool:
        return self.family == "retry"

    def rollup_status(self, event_type: str) -> str:
        """The status word a terminal event of this shape writes onto its rollup."""
        return self.rollup_prefix + event_type.removeprefix(self.rollup_namespace)

    @property
    def running_rollup_status(self) -> str:
        return f"{self.rollup_prefix}running"

    @property
    def unknown_rollup_status(self) -> str:
        return f"{self.rollup_prefix}unknown"


def _lifecycle_event(
    event_type: str,
    family: EventFamily,
    phase: EventPhase,
    **effects: bool,
) -> RunEventTypeSpec:
    """One node/retry event: both share the slot + rollup shape, only effects differ."""
    return RunEventTypeSpec(
        event_type=event_type,
        family=family,
        phase=phase,
        rollup_namespace=f"{family}.",
        rollup_prefix="" if family == "node" else "retry_",
        lifecycle_fields_required=True,
        **effects,
    )


BUILTIN_RUN_EVENT_SPECS: tuple[RunEventTypeSpec, ...] = (
    RunEventTypeSpec("run.started", "run", "start"),
    _lifecycle_event("node.started", "node", "start"),
    _lifecycle_event("node.completed", "node", "terminal"),
    _lifecycle_event("node.failed", "node", "terminal", records_failed_node=True),
    _lifecycle_event("node.skipped", "node", "terminal"),
    _lifecycle_event("node.cancelled", "node", "terminal"),
    RunEventTypeSpec(
        "route.selected", "annotation", "standalone", records_route=True
    ),
    _lifecycle_event("retry.started", "retry", "start", counts_retry=True),
    _lifecycle_event("retry.completed", "retry", "terminal"),
    _lifecycle_event("retry.failed", "retry", "terminal"),
    _lifecycle_event("retry.skipped", "retry", "terminal"),
    RunEventTypeSpec(
        "degraded", "annotation", "standalone", counts_degraded=True
    ),
    RunEventTypeSpec("run.completed", "run", "terminal", run_status="completed"),
    RunEventTypeSpec("run.failed", "run", "terminal", run_status="failed"),
    RunEventTypeSpec("run.cancelled", "run", "terminal", run_status="cancelled"),
)

RUN_EVENT_TYPE_SPECS: ProviderRegistry[None, RunEventTypeSpec] = ProviderRegistry(
    "run event type"
)

#: 精确拼写的声明索引，只由 :func:`register_run_event_type` /
#: :func:`unregister_run_event_type` 读写。上面那个内核注册表是声明与校验的所在地，
#: 这一份只为热路径服务：reduce 每个事件都要查一次，走内核要为每次命中排两次序。
_BY_NAME: dict[str, RunEventTypeSpec] = {}


def _validate_spec(spec: RunEventTypeSpec) -> None:
    """Reject a half-declared event type at registration, not on the first run."""
    name = spec.event_type
    if not isinstance(name, str) or not name.strip():
        raise ValueError("run event type must be a non-empty string")
    if name != name.strip().lower():
        # The shared kernel keys registries case-folded, so a mixed-case or padded
        # declaration would register under one spelling and be looked up under another.
        raise ValueError(f"run event type must be lower-case with no padding: {name!r}")
    if spec.family not in _FAMILIES:
        raise ValueError(f"{name}: unknown run event family {spec.family!r}")
    if spec.phase not in _PHASES:
        raise ValueError(f"{name}: unknown run event phase {spec.phase!r}")
    if spec.family in {"node", "retry"}:
        expected_namespace = f"{spec.family}."
        if spec.phase == "standalone":
            raise ValueError(f"{name}: a {spec.family} event must be a start or a terminal")
        if spec.rollup_namespace != expected_namespace:
            raise ValueError(f"{name}: rollup_namespace must be {expected_namespace!r}")
        # rollup_prefix 决定投影里状态词长什么样（retry 的写 retry_failed），过去无人
        # 校验：一个 retry 事件把前缀写成空串，注册照样通过，只在跑起来之后才让
        # rollup 状态与前端对不上。它其实完全由 family 推出来 —— 校验是这里的保守
        # 选项，改成派生（去掉这个字段）是留给下一位的更彻底做法。
        expected_prefix = "" if spec.family == "node" else f"{spec.family}_"
        if spec.rollup_prefix != expected_prefix:
            raise ValueError(f"{name}: rollup_prefix must be {expected_prefix!r}")
        if not spec.lifecycle_fields_required:
            raise ValueError(f"{name}: {spec.family} events require node_id and attempt")
    elif spec.rollup_namespace or spec.rollup_prefix:
        raise ValueError(f"{name}: only node and retry events own rollups")
    if spec.run_status is not None:
        if spec.family != "run" or spec.phase != "terminal":
            raise ValueError(f"{name}: run_status requires a run terminal event")
        if spec.run_status not in _RUN_TERMINAL_STATUSES:
            raise ValueError(f"{name}: unknown run status {spec.run_status!r}")
    for flag in ("counts_retry", "counts_degraded", "records_route", "records_failed_node"):
        if not isinstance(getattr(spec, flag), bool):
            raise ValueError(f"{name}.{flag} must be a bool")
    if spec.counts_retry and not (spec.family == "retry" and spec.phase == "start"):
        raise ValueError(f"{name}: counts_retry requires a retry start event")
    if spec.records_failed_node and not (spec.family == "node" and spec.phase == "terminal"):
        raise ValueError(f"{name}: records_failed_node requires a node terminal event")


def register_run_event_type(spec: RunEventTypeSpec, *, replace: bool = False) -> None:
    """Declare one event type's place in the partition."""
    _validate_spec(spec)
    if spec.event_type in _BY_NAME and not replace:
        raise ValueError(f"run event type already registered: {spec.event_type}")
    RUN_EVENT_TYPE_SPECS.register(
        spec.event_type, lambda _config, _s=spec: _s, replace=replace
    )
    _BY_NAME[spec.event_type] = spec


def unregister_run_event_type(event_type: str) -> None:
    """Withdraw a declaration (tests and plugins). Both stores move together.

    The kernel keys itself by ``name.strip().lower()``; declarations themselves are
    rejected unless already canonical, so applying the kernel's normalisation here is
    what keeps a padded or upper-cased argument from emptying one store only.
    """
    RUN_EVENT_TYPE_SPECS.unregister(event_type)
    _BY_NAME.pop(event_type.strip().lower(), None)


def resolve_run_event_type(event_type: object) -> RunEventTypeSpec | None:
    """Look one event type up; an undeclared type resolves to ``None``.

    Membership is tested against the declared names *exactly as spelled*, which is what
    ``event_type in {...}`` did. ``ProviderRegistry`` case-folds and strips its keys for
    the ergonomic provider-selection use case; reusing ``create`` as a membership test
    here would quietly turn ``"RUN.STARTED"`` and ``"run.completed "`` into hits, and
    reducers must not start applying a run terminal because a ledger row padded its type.
    ``_validate_spec`` rejects any declaration that is not already its own key, so the
    two spellings cannot diverge on the declaration side.

    ``_BY_NAME`` exists because this runs once per event on the SSE path and once per
    stored event on every detail read: going through the kernel cost two sorts per
    lookup and measured 3.8x the frozenset it replaced. It is written and cleared only
    by the two functions above, and ``test_the_two_stores_never_disagree`` keeps them
    from drifting.

    Neither reducer raised on an unrecognized ``type`` string before this module and
    neither does now, so this stays fail-soft rather than growing a raise. The ``hash()``
    call keeps the rest of the old semantics: a set membership test raises ``TypeError``
    for an unhashable ``type`` (a JSON array in a corrupt stored event) and simply misses
    for any other non-string.
    """
    if not isinstance(event_type, str):
        hash(event_type)
        return None
    return _BY_NAME.get(event_type)


def run_event_types_in_family(family: EventFamily) -> frozenset[str]:
    """Every declared event type of one family — the shape ``_NODE_TYPES`` used to be."""
    return run_event_types_with(family, "start") | run_event_types_with(
        family, "terminal"
    ) | run_event_types_with(family, "standalone")


def run_event_types_with(family: EventFamily, phase: EventPhase) -> frozenset[str]:
    """One cell of the partition, e.g. the terminal retry events."""
    return frozenset(
        name
        for name, spec in _BY_NAME.items()
        if spec.family == family and spec.phase == phase
    )


def run_terminal_status(event_type: object) -> RunTerminalStatus | None:
    """The status a run terminal event drives the projection to, or ``None``."""
    spec = resolve_run_event_type(event_type)
    return None if spec is None else spec.run_status


def _register_builtins() -> None:
    # replace=False 是刻意的：清单里写重一次就要在 import 期炸掉，而不是让后一行
    # 静默盖掉前一行（那会让"声明了两次"这件事在测试与生产里都看不见）。
    for spec in BUILTIN_RUN_EVENT_SPECS:
        register_run_event_type(spec)


_register_builtins()
