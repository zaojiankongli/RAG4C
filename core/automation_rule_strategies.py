"""Strategy declarations for Enterprise Automation condition and action codes.

The public rule vocabulary is intentionally closed by the database and HTTP
contract.  This module still gives the existing codes one implementation seam:
parameter declarations and pure behaviour live behind two
``ProviderRegistry`` instances.  Trusted runtime-only strategies can be
registered for pure/internal paths, while canonical persistence remains
fail-closed against the frozen contract sets owned by the authority module.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import inspect
import re
from threading import RLock
from typing import Any

from core.providers import ProviderRegistry, UnknownProviderError

_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PARAMETER_KINDS = frozenset({"boolean", "code", "identifier", "integer", "text"})

AUTOMATION_STATUS_CODES = frozenset(
    {
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "blocked",
        "unavailable",
        "approved",
        "rejected",
        "expired",
        "completed",
    }
)
AUTOMATION_SEVERITY_ORDER = ("info", "warning", "error", "critical")
AUTOMATION_SEVERITY_CODES = frozenset(AUTOMATION_SEVERITY_ORDER)
AUTOMATION_CONDITION_CONTRACT_CODES = (
    "always",
    "status_is",
    "action_required",
    "severity_at_least",
    "attempt_exhausted",
    "source_is_stale",
)
AUTOMATION_ACTION_CONTRACT_CODES = (
    "notify_operator",
    "request_approval",
    "open_task_attention",
    "pause_rule",
)


@dataclass(frozen=True)
class AutomationParameterSpec:
    """One required parameter and its safe normalisation contract."""

    name: str
    kind: str
    allowed_values: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    maximum_length: int | None = None


@dataclass(frozen=True)
class AutomationTargetReference:
    """Side-effect-free target selected for one action request."""

    target_kind: str
    target_id: str
    task_id: str | None = None


@dataclass(frozen=True)
class AutomationConditionStrategy:
    """Pure condition evaluator plus its parameter declarations."""

    code: str
    parameters: tuple[AutomationParameterSpec, ...]
    evaluator: Callable[[Mapping[str, Any], Mapping[str, Any]], bool]


@dataclass(frozen=True)
class AutomationActionStrategy:
    """Action parameter declarations plus its target-selection adapter."""

    code: str
    parameters: tuple[AutomationParameterSpec, ...]
    target_resolver: Callable[[Mapping[str, Any], str], AutomationTargetReference]


_CONDITION_STRATEGY_REGISTRY: ProviderRegistry[None, AutomationConditionStrategy] = (
    ProviderRegistry("automation condition strategy")
)
_ACTION_STRATEGY_REGISTRY: ProviderRegistry[None, AutomationActionStrategy] = ProviderRegistry(
    "automation action strategy"
)
_CONDITION_BUILTIN_FACTORIES: dict[str, Callable[[None], AutomationConditionStrategy]] = {}
_ACTION_BUILTIN_FACTORIES: dict[str, Callable[[None], AutomationActionStrategy]] = {}
_CONDITION_ORDER: list[str] = []
_ACTION_ORDER: list[str] = []
_LOCK = RLock()


def _canonical_key(value: str) -> str:
    return value.strip().casefold().replace("-", "_").replace(" ", "_")


def _safe_code(value: object, field: str) -> str:
    if not isinstance(value, str) or _CODE_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase safe code")
    if value != value.casefold():
        raise ValueError(f"{field} must be lowercase")
    return value


def _safe_field(value: object, field: str) -> str:
    if not isinstance(value, str) or _FIELD_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase field name")
    return value


def _validate_parameter_spec(spec: AutomationParameterSpec) -> None:
    if not isinstance(spec, AutomationParameterSpec):
        raise TypeError("automation parameter must be AutomationParameterSpec")
    _safe_field(spec.name, "parameter name")
    if spec.kind not in _PARAMETER_KINDS:
        raise ValueError(f"parameter kind is not supported: {spec.kind!r}")
    if not isinstance(spec.allowed_values, tuple):
        raise TypeError("parameter allowed_values must be a tuple")
    if len(set(spec.allowed_values)) != len(spec.allowed_values):
        raise ValueError(f"parameter allowed_values must be unique: {spec.name}")
    for value in spec.allowed_values:
        _safe_code(value, f"{spec.name} allowed value")
    canonical_values = tuple(_canonical_key(value) for value in spec.allowed_values)
    if len(set(canonical_values)) != len(canonical_values):
        raise ValueError(
            f"parameter allowed_values must be unique after normalisation: {spec.name}"
        )
    if spec.kind != "code" and spec.allowed_values:
        raise ValueError(f"{spec.name}: only code parameters may constrain allowed values")
    if spec.kind == "integer":
        for field_name, value in (("minimum", spec.minimum), ("maximum", spec.maximum)):
            if value is not None and (type(value) is not int):
                raise TypeError(f"{spec.name}: {field_name} must be an integer")
        if (
            spec.minimum is not None
            and spec.maximum is not None
            and spec.minimum > spec.maximum
        ):
            raise ValueError(f"{spec.name}: minimum must not exceed maximum")
    elif spec.minimum is not None or spec.maximum is not None:
        raise ValueError(f"{spec.name}: numeric bounds require an integer parameter")
    if spec.kind == "text":
        if spec.maximum_length is not None and (
            type(spec.maximum_length) is not int or spec.maximum_length <= 0
        ):
            raise ValueError(f"{spec.name}: maximum_length must be a positive integer")
    elif spec.maximum_length is not None:
        raise ValueError(f"{spec.name}: maximum_length requires a text parameter")


def _validate_callback(callback: object, field: str, *arguments: object) -> None:
    if not callable(callback):
        raise TypeError(f"{field} must be callable")
    call = getattr(callback, "__call__", None)
    if (
        inspect.iscoroutinefunction(callback)
        or inspect.isasyncgenfunction(callback)
        or inspect.iscoroutinefunction(call)
        or inspect.isasyncgenfunction(call)
    ):
        raise TypeError(f"{field} must be synchronous")
    try:
        signature = inspect.signature(callback)
        signature.bind(*arguments)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{field} must accept the declared positional context") from exc


def _validate_condition_strategy(strategy: AutomationConditionStrategy) -> None:
    if not isinstance(strategy, AutomationConditionStrategy):
        raise TypeError("condition strategy must be AutomationConditionStrategy")
    _safe_code(strategy.code, "condition strategy code")
    if not isinstance(strategy.parameters, tuple):
        raise TypeError("condition strategy parameters must be a tuple")
    seen: set[str] = set()
    for parameter in strategy.parameters:
        _validate_parameter_spec(parameter)
        if parameter.name in seen:
            raise ValueError(f"duplicate condition parameter: {parameter.name}")
        seen.add(parameter.name)
    _validate_callback(strategy.evaluator, "condition evaluator", {}, {})


def _validate_action_strategy(strategy: AutomationActionStrategy) -> None:
    if not isinstance(strategy, AutomationActionStrategy):
        raise TypeError("action strategy must be AutomationActionStrategy")
    _safe_code(strategy.code, "action strategy code")
    if not isinstance(strategy.parameters, tuple):
        raise TypeError("action strategy parameters must be a tuple")
    seen: set[str] = set()
    for parameter in strategy.parameters:
        _validate_parameter_spec(parameter)
        if parameter.name in seen:
            raise ValueError(f"duplicate action parameter: {parameter.name}")
        seen.add(parameter.name)
    _validate_callback(strategy.target_resolver, "action target resolver", {}, "rule-id")


def _condition_factory(strategy: AutomationConditionStrategy) -> Callable[[None], AutomationConditionStrategy]:
    def factory(_config: None) -> AutomationConditionStrategy:
        return strategy

    return factory


def _action_factory(strategy: AutomationActionStrategy) -> Callable[[None], AutomationActionStrategy]:
    def factory(_config: None) -> AutomationActionStrategy:
        return strategy

    return factory


def _register_builtin_condition(strategy: AutomationConditionStrategy) -> None:
    _validate_condition_strategy(strategy)
    factory = _condition_factory(strategy)
    key = _canonical_key(strategy.code)
    with _LOCK:
        _CONDITION_STRATEGY_REGISTRY.register(key, factory)
        _CONDITION_BUILTIN_FACTORIES[key] = factory
        _CONDITION_ORDER.append(key)


def _register_builtin_action(strategy: AutomationActionStrategy) -> None:
    _validate_action_strategy(strategy)
    factory = _action_factory(strategy)
    key = _canonical_key(strategy.code)
    with _LOCK:
        _ACTION_STRATEGY_REGISTRY.register(key, factory)
        _ACTION_BUILTIN_FACTORIES[key] = factory
        _ACTION_ORDER.append(key)


def register_condition_strategy(strategy: AutomationConditionStrategy) -> None:
    """Register one runtime-only condition strategy.

    Persistent/API vocabulary remains closed until its separate contract and
    database change is made.
    """

    _validate_condition_strategy(strategy)
    factory = _condition_factory(strategy)
    key = _canonical_key(strategy.code)
    with _LOCK:
        if key in _CONDITION_BUILTIN_FACTORIES:
            raise ValueError(f"automation condition strategy is reserved: {key}")
        if key in _CONDITION_STRATEGY_REGISTRY.names():
            raise ValueError(f"automation condition strategy already registered: {key}")
        _CONDITION_STRATEGY_REGISTRY.register(key, factory)
        _CONDITION_ORDER.append(key)


def register_action_strategy(strategy: AutomationActionStrategy) -> None:
    """Register one runtime-only action strategy."""

    _validate_action_strategy(strategy)
    factory = _action_factory(strategy)
    key = _canonical_key(strategy.code)
    with _LOCK:
        if key in _ACTION_BUILTIN_FACTORIES:
            raise ValueError(f"automation action strategy is reserved: {key}")
        if key in _ACTION_STRATEGY_REGISTRY.names():
            raise ValueError(f"automation action strategy already registered: {key}")
        _ACTION_STRATEGY_REGISTRY.register(key, factory)
        _ACTION_ORDER.append(key)


def unregister_condition_strategy(code: str) -> None:
    if not isinstance(code, str):
        raise TypeError("condition strategy code must be a string")
    key = _canonical_key(code)
    with _LOCK:
        if key in _CONDITION_BUILTIN_FACTORIES:
            raise ValueError(f"automation condition strategy is reserved: {key}")
        _CONDITION_STRATEGY_REGISTRY.unregister(key)
        if key in _CONDITION_ORDER:
            _CONDITION_ORDER.remove(key)


def unregister_action_strategy(code: str) -> None:
    if not isinstance(code, str):
        raise TypeError("action strategy code must be a string")
    key = _canonical_key(code)
    with _LOCK:
        if key in _ACTION_BUILTIN_FACTORIES:
            raise ValueError(f"automation action strategy is reserved: {key}")
        _ACTION_STRATEGY_REGISTRY.unregister(key)
        if key in _ACTION_ORDER:
            _ACTION_ORDER.remove(key)


def _resolve_condition_factory(code: str) -> Callable[[None], AutomationConditionStrategy] | None:
    try:
        factory = _CONDITION_STRATEGY_REGISTRY.get_factory(code)
    except UnknownProviderError as exc:
        if code in _CONDITION_BUILTIN_FACTORIES:
            raise TypeError(f"built-in condition strategy was unregistered: {code}") from exc
        return None
    builtin = _CONDITION_BUILTIN_FACTORIES.get(code)
    if builtin is not None and factory is not builtin:
        raise TypeError(f"built-in condition strategy contract was replaced: {code}")
    return factory


def _resolve_action_factory(code: str) -> Callable[[None], AutomationActionStrategy] | None:
    try:
        factory = _ACTION_STRATEGY_REGISTRY.get_factory(code)
    except UnknownProviderError as exc:
        if code in _ACTION_BUILTIN_FACTORIES:
            raise TypeError(f"built-in action strategy was unregistered: {code}") from exc
        return None
    builtin = _ACTION_BUILTIN_FACTORIES.get(code)
    if builtin is not None and factory is not builtin:
        raise TypeError(f"built-in action strategy contract was replaced: {code}")
    return factory


def resolve_condition_strategy(code: object) -> AutomationConditionStrategy | None:
    if not isinstance(code, str):
        return None
    key = _canonical_key(code)
    factory = _resolve_condition_factory(key)
    return None if factory is None else factory(None)


def resolve_action_strategy(code: object) -> AutomationActionStrategy | None:
    if not isinstance(code, str):
        return None
    key = _canonical_key(code)
    factory = _resolve_action_factory(key)
    return None if factory is None else factory(None)


def condition_strategy_names(*, builtin_only: bool = False) -> tuple[str, ...]:
    with _LOCK:
        names = _CONDITION_ORDER if not builtin_only else list(_CONDITION_BUILTIN_FACTORIES)
        return tuple(names)


def action_strategy_names(*, builtin_only: bool = False) -> tuple[str, ...]:
    with _LOCK:
        names = _ACTION_ORDER if not builtin_only else list(_ACTION_BUILTIN_FACTORIES)
        return tuple(names)


def condition_parameter_specs(code: object) -> tuple[AutomationParameterSpec, ...]:
    strategy = resolve_condition_strategy(code)
    if strategy is None:
        raise ValueError(f"unknown automation condition strategy: {code!r}")
    return strategy.parameters


def action_parameter_specs(code: object) -> tuple[AutomationParameterSpec, ...]:
    strategy = resolve_action_strategy(code)
    if strategy is None:
        raise ValueError(f"unknown automation action strategy: {code!r}")
    return strategy.parameters


def condition_parameter_names(code: object) -> frozenset[str]:
    return frozenset(parameter.name for parameter in condition_parameter_specs(code))


def action_parameter_names(code: object) -> frozenset[str]:
    return frozenset(parameter.name for parameter in action_parameter_specs(code))


def _always(_params: Mapping[str, Any], _event: Mapping[str, Any]) -> bool:
    return True


def _status_is(params: Mapping[str, Any], event: Mapping[str, Any]) -> bool:
    return event["status"] == params["status"]


def _action_required(params: Mapping[str, Any], event: Mapping[str, Any]) -> bool:
    return event["action_required"] is params["value"]


def _severity_at_least(params: Mapping[str, Any], event: Mapping[str, Any]) -> bool:
    return AUTOMATION_SEVERITY_ORDER.index(str(event["severity"])) >= AUTOMATION_SEVERITY_ORDER.index(
        str(params["severity"])
    )


def _attempt_exhausted(params: Mapping[str, Any], event: Mapping[str, Any]) -> bool:
    return (
        int(event["attempt_number"]) >= int(event["max_attempts"])
        and int(event["attempt_number"]) >= int(params["minimum_attempts"])
    )


def _source_is_stale(params: Mapping[str, Any], event: Mapping[str, Any]) -> bool:
    return (not event["source_current"]) is params["value"]


def _event_target(
    event: Mapping[str, Any], _rule_id: str
) -> AutomationTargetReference:
    facts = event.get("safe_facts")
    safe_facts = facts if isinstance(facts, Mapping) else {}
    task_id = safe_facts.get("task_id")
    if isinstance(task_id, str) and task_id:
        return AutomationTargetReference("task", task_id, task_id)
    source_id = safe_facts.get("source_id")
    if isinstance(source_id, str) and source_id:
        return AutomationTargetReference("source", source_id)
    approval_id = safe_facts.get("approval_request_id")
    if isinstance(approval_id, str) and approval_id:
        return AutomationTargetReference("approval_request", approval_id)
    return AutomationTargetReference("source_event", str(event["source_event_id"]))


def _pause_rule_target(
    _event: Mapping[str, Any], rule_id: str
) -> AutomationTargetReference:
    return AutomationTargetReference("automation_rule", rule_id)


_STATUS_PARAMETER = AutomationParameterSpec(
    "status",
    "code",
    tuple(sorted(AUTOMATION_STATUS_CODES)),
)
_SEVERITY_PARAMETER = AutomationParameterSpec(
    "severity",
    "code",
    tuple(AUTOMATION_SEVERITY_ORDER),
)

for _condition in (
    AutomationConditionStrategy("always", (), _always),
    AutomationConditionStrategy(
        "status_is",
        (_STATUS_PARAMETER,),
        _status_is,
    ),
    AutomationConditionStrategy(
        "action_required",
        (AutomationParameterSpec("value", "boolean"),),
        _action_required,
    ),
    AutomationConditionStrategy(
        "severity_at_least",
        (_SEVERITY_PARAMETER,),
        _severity_at_least,
    ),
    AutomationConditionStrategy(
        "attempt_exhausted",
        (AutomationParameterSpec("minimum_attempts", "integer", minimum=1, maximum=100),),
        _attempt_exhausted,
    ),
    AutomationConditionStrategy(
        "source_is_stale",
        (AutomationParameterSpec("value", "boolean"),),
        _source_is_stale,
    ),
):
    _register_builtin_condition(_condition)

for _action in (
    AutomationActionStrategy(
        "notify_operator",
        (
            AutomationParameterSpec("category", "code"),
            _SEVERITY_PARAMETER,
            AutomationParameterSpec("title", "text", maximum_length=160),
        ),
        _event_target,
    ),
    AutomationActionStrategy(
        "request_approval",
        (
            AutomationParameterSpec("action_type", "code"),
            AutomationParameterSpec("resource_type", "code"),
            AutomationParameterSpec("reason_code", "code"),
        ),
        _event_target,
    ),
    AutomationActionStrategy(
        "open_task_attention",
        (
            AutomationParameterSpec("task_id", "identifier"),
            AutomationParameterSpec("reason_code", "code"),
        ),
        _event_target,
    ),
    AutomationActionStrategy(
        "pause_rule",
        (AutomationParameterSpec("reason_code", "code"),),
        _pause_rule_target,
    ),
):
    _register_builtin_action(_action)

if tuple(_CONDITION_ORDER) != AUTOMATION_CONDITION_CONTRACT_CODES:
    raise RuntimeError(
        "automation condition strategy registry must match the frozen persistence contract"
    )
if tuple(_ACTION_ORDER) != AUTOMATION_ACTION_CONTRACT_CODES:
    raise RuntimeError(
        "automation action strategy registry must match the frozen persistence contract"
    )


__all__ = [
    "AUTOMATION_ACTION_CONTRACT_CODES",
    "AUTOMATION_CONDITION_CONTRACT_CODES",
    "AUTOMATION_SEVERITY_CODES",
    "AUTOMATION_SEVERITY_ORDER",
    "AUTOMATION_STATUS_CODES",
    "AutomationActionStrategy",
    "AutomationConditionStrategy",
    "AutomationParameterSpec",
    "AutomationTargetReference",
    "action_parameter_names",
    "action_parameter_specs",
    "action_strategy_names",
    "condition_parameter_names",
    "condition_parameter_specs",
    "condition_strategy_names",
    "register_action_strategy",
    "register_condition_strategy",
    "resolve_action_strategy",
    "resolve_condition_strategy",
    "unregister_action_strategy",
    "unregister_condition_strategy",
]
