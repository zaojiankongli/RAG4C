"""Extensible Task Operations source-kind and route strategy declarations.

The source adapter registry answers *where* a source is read from.  This
module answers the separate data-policy question: how that source is exposed
in the public/storage read model and which route/parameter contract it uses.
Keeping those concerns in a validated registry prevents category and route
maps from becoming a second, silently drifting dispatch system.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from threading import RLock
from typing import Any

from core.providers import ProviderRegistry, UnknownProviderError
from core.task_vocabulary import TASK_CATEGORY_VALUES

_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
TASK_SOURCE_CATEGORIES = frozenset(TASK_CATEGORY_VALUES)


@dataclass(frozen=True)
class TaskRouteParamSpec:
    """One route parameter and its source/fact extraction rule."""

    name: str
    source_field: str | None = None
    safe_fact_field: str | None = None
    optional: bool = False


@dataclass(frozen=True)
class TaskRouteSpec:
    """Allowed and required parameter contract for one route code."""

    route_code: str
    allowed_params: tuple[str, ...]
    required_params: tuple[str, ...]
    required_by_source_kind: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True)
class TaskSourceKindSpec:
    """All data-policy behavior associated with one task source kind."""

    kind: str
    public_category: str
    storage_category: str
    public_route_code: str
    storage_route_code: str
    default_route_code: str
    allowed_route_codes: frozenset[str]
    route_params: tuple[TaskRouteParamSpec, ...]


def _safe_code(value: object, field: str) -> str:
    if not isinstance(value, str) or _CODE_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase safe code")
    return value


def _safe_field(value: object, field: str) -> str:
    if not isinstance(value, str) or _FIELD_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase field name")
    return value


def _validate_route_spec(spec: TaskRouteSpec) -> None:
    if not isinstance(spec, TaskRouteSpec):
        raise TypeError("task route spec must be TaskRouteSpec")
    _safe_code(spec.route_code, "route_code")
    for field_name, values in (
        ("allowed_params", spec.allowed_params),
        ("required_params", spec.required_params),
    ):
        if not isinstance(values, tuple) or len(set(values)) != len(values):
            raise ValueError(f"{field_name} must be a unique tuple")
        for value in values:
            _safe_field(value, field_name)
    if not set(spec.required_params) <= set(spec.allowed_params):
        raise ValueError("required_params must be a subset of allowed_params")
    if not isinstance(spec.required_by_source_kind, tuple):
        raise ValueError("required_by_source_kind must be a tuple")
    seen_kinds: set[str] = set()
    for kind, required in spec.required_by_source_kind:
        _safe_code(kind, "required_by_source_kind kind")
        if kind in seen_kinds:
            raise ValueError(f"duplicate required_by_source_kind entry: {kind}")
        seen_kinds.add(kind)
        if not isinstance(required, tuple) or len(set(required)) != len(required):
            raise ValueError("required_by_source_kind entries must be unique tuples")
        if not set(required) <= set(spec.allowed_params):
            raise ValueError("source-kind route requirements must be allowed parameters")


def _validate_source_spec(spec: TaskSourceKindSpec) -> None:
    if not isinstance(spec, TaskSourceKindSpec):
        raise TypeError("task source kind spec must be TaskSourceKindSpec")
    for field_name in (
        "kind",
        "public_route_code",
        "storage_route_code",
        "default_route_code",
    ):
        _safe_code(getattr(spec, field_name), field_name)
    for field_name in ("public_category", "storage_category"):
        category = getattr(spec, field_name)
        _safe_code(category, field_name)
        if category not in TASK_SOURCE_CATEGORIES:
            raise ValueError(f"{field_name} is not an allowed task category: {category}")
    if not isinstance(spec.allowed_route_codes, frozenset) or not spec.allowed_route_codes:
        raise ValueError("allowed_route_codes must be a non-empty frozenset")
    for route_code in spec.allowed_route_codes:
        _safe_code(route_code, "allowed_route_codes")
    for route_code in (
        spec.default_route_code,
    ):
        if route_code not in spec.allowed_route_codes:
            raise ValueError(f"{route_code} must be listed in allowed_route_codes")
    if not isinstance(spec.route_params, tuple) or not spec.route_params:
        raise ValueError("route_params must be a non-empty tuple")
    names: set[str] = set()
    for param in spec.route_params:
        if not isinstance(param, TaskRouteParamSpec):
            raise TypeError("route_params entries must be TaskRouteParamSpec")
        _safe_field(param.name, "route parameter name")
        if param.name in names:
            raise ValueError(f"duplicate route parameter: {param.name}")
        names.add(param.name)
        if param.source_field is None and param.safe_fact_field is None:
            raise ValueError(f"route parameter has no source: {param.name}")
        if param.source_field is not None:
            _safe_field(param.source_field, "source_field")
        if param.safe_fact_field is not None:
            _safe_field(param.safe_fact_field, "safe_fact_field")
        if type(param.optional) is not bool:
            raise ValueError("route parameter optional must be a bool")


_ROUTE_SPECS: dict[str, TaskRouteSpec] = {}
_BUILTIN_ROUTE_CODES: set[str] = set()
_SOURCE_KIND_REGISTRY: ProviderRegistry[None, TaskSourceKindSpec] = ProviderRegistry(
    "task source kind"
)
_BUILTIN_SOURCE_FACTORIES: dict[str, Any] = {}
_SOURCE_KIND_ORDER: list[str] = []
_LOCK = RLock()


def register_task_route_spec(spec: TaskRouteSpec, *, builtin: bool = False) -> None:
    """Register a route parameter strategy before source kinds reference it."""

    _validate_route_spec(spec)
    with _LOCK:
        if spec.route_code in _ROUTE_SPECS:
            raise ValueError(f"task route already registered: {spec.route_code}")
        _ROUTE_SPECS[spec.route_code] = spec
        if builtin:
            _BUILTIN_ROUTE_CODES.add(spec.route_code)


def unregister_task_route_spec(route_code: str) -> None:
    with _LOCK:
        key = route_code.strip().lower()
        if key in _BUILTIN_ROUTE_CODES:
            raise ValueError(f"task route code is reserved: {key}")
        _ROUTE_SPECS.pop(key, None)


def task_route_spec(route_code: object) -> TaskRouteSpec | None:
    if not isinstance(route_code, str):
        return None
    return _ROUTE_SPECS.get(route_code.strip().lower())


def register_task_source_kind(spec: TaskSourceKindSpec, *, builtin: bool = False) -> None:
    """Register one source-kind policy; built-ins cannot be replaced publicly."""

    _validate_source_spec(spec)
    missing_routes = sorted(set(spec.allowed_route_codes) - set(_ROUTE_SPECS))
    if missing_routes:
        raise ValueError(f"source kind references unknown route codes: {missing_routes}")

    def factory(_config: None, value: TaskSourceKindSpec = spec) -> TaskSourceKindSpec:
        return value

    with _LOCK:
        if spec.kind in _SOURCE_KIND_REGISTRY.names():
            raise ValueError(f"task source kind already registered: {spec.kind}")
        _SOURCE_KIND_REGISTRY.register(spec.kind, factory)
        _SOURCE_KIND_ORDER.append(spec.kind)
        if builtin:
            _BUILTIN_SOURCE_FACTORIES[spec.kind] = factory


def unregister_task_source_kind(kind: str) -> None:
    key = kind.strip().lower()
    with _LOCK:
        if key in _BUILTIN_SOURCE_FACTORIES:
            raise ValueError(f"task source kind is reserved: {key}")
        _SOURCE_KIND_REGISTRY.unregister(key)
        if key in _SOURCE_KIND_ORDER:
            _SOURCE_KIND_ORDER.remove(key)


def _resolve_source_factory(kind: str) -> Any:
    try:
        factory = _SOURCE_KIND_REGISTRY.get_factory(kind)
    except UnknownProviderError as exc:
        if kind in _BUILTIN_SOURCE_FACTORIES:
            raise TypeError(f"built-in task source kind was unregistered: {kind}") from exc
        return None
    builtin = _BUILTIN_SOURCE_FACTORIES.get(kind)
    if builtin is not None and factory is not builtin:
        raise TypeError(f"built-in task source kind contract was replaced: {kind}")
    return factory


def task_source_kind(kind: object) -> TaskSourceKindSpec | None:
    if not isinstance(kind, str):
        return None
    factory = _resolve_source_factory(kind.strip().lower())
    return None if factory is None else factory(None)


def task_source_kind_names() -> tuple[str, ...]:
    with _LOCK:
        return tuple(_SOURCE_KIND_ORDER)


def task_source_kind_specs() -> tuple[TaskSourceKindSpec, ...]:
    return tuple(task_source_kind(name) for name in task_source_kind_names())  # type: ignore[misc]


def task_source_kinds_for_category(
    category: object,
    *,
    storage: bool = False,
) -> tuple[str, ...]:
    if not isinstance(category, str):
        return ()
    field = "storage_category" if storage else "public_category"
    return tuple(
        spec.kind
        for spec in task_source_kind_specs()
        if getattr(spec, field) == category
    )


def task_route_codes() -> frozenset[str]:
    with _LOCK:
        return frozenset(_ROUTE_SPECS)


def task_route_source_kinds(route_code: object) -> frozenset[str]:
    if not isinstance(route_code, str):
        return frozenset()
    key = route_code.strip().lower()
    return frozenset(
        spec.kind
        for spec in task_source_kind_specs()
        if key in spec.allowed_route_codes
    )


def task_route_schema(
    route_code: object,
    source_kind: str | None = None,
) -> tuple[set[str], set[str]]:
    spec = task_route_spec(route_code)
    if spec is None:
        raise ValueError("route code is not allowed")
    allowed = set(spec.allowed_params)
    required = set(spec.required_params)
    if source_kind is not None:
        required_by_kind = dict(spec.required_by_source_kind)
        required.update(required_by_kind.get(source_kind, ()))
    return allowed, required


def task_source_route_params(source: Mapping[str, Any]) -> dict[str, str]:
    kind = source.get("source_kind")
    spec = task_source_kind(kind)
    if spec is None:
        raise ValueError("source_kind is invalid")
    facts = source.get("safe_facts")
    facts = facts if isinstance(facts, Mapping) else {}
    params: dict[str, str] = {}
    for param in spec.route_params:
        value = None
        if param.safe_fact_field is not None:
            value = facts.get(param.safe_fact_field)
        if value in (None, "") and param.source_field is not None:
            value = source.get(param.source_field)
        if value is None and param.optional:
            continue
        if value is None:
            raise ValueError(f"route parameter is required: {param.name}")
        params[param.name] = str(value)
    return {key: params[key] for key in sorted(params)}


_BUILTIN_ROUTE_SPECS = (
    TaskRouteSpec(
        "knowledge_sources",
        ("tenant_id", "source_id", "run_id"),
        ("tenant_id", "source_id"),
    ),
    TaskRouteSpec(
        "enterprise_sources",
        ("tenant_id", "source_id", "run_id"),
        ("tenant_id", "source_id"),
    ),
    TaskRouteSpec(
        "enterprise_compliance",
        ("tenant_id", "export_id", "job_id"),
        ("tenant_id", "export_id"),
    ),
    TaskRouteSpec(
        "enterprise_audit",
        ("tenant_id", "export_id", "job_id"),
        ("tenant_id", "export_id"),
    ),
    TaskRouteSpec(
        "enterprise_audit_compliance",
        ("tenant_id", "export_id", "job_id"),
        ("tenant_id", "export_id"),
    ),
    TaskRouteSpec(
        "knowledge_quality_operations",
        ("tenant_id", "dataset_id", "release_id", "scan_id", "job_id"),
        ("tenant_id", "dataset_id", "release_id"),
        (
            ("release_quality_scan", ("scan_id",)),
            ("release_recertification", ("job_id",)),
        ),
    ),
    TaskRouteSpec(
        "enterprise_release_quality",
        ("tenant_id", "dataset_id", "release_id", "scan_id", "job_id"),
        ("tenant_id", "dataset_id", "release_id"),
        (
            ("release_quality_scan", ("scan_id",)),
            ("release_recertification", ("job_id",)),
        ),
    ),
    TaskRouteSpec(
        "enterprise_recycle_bin",
        ("tenant_id", "dataset_id", "document_id", "entry_id", "batch_id", "operation_id"),
        ("tenant_id", "dataset_id", "document_id"),
    ),
    TaskRouteSpec(
        "knowledge_documents",
        ("tenant_id", "dataset_id", "document_id", "attempt_id", "operation_id", "batch_id"),
        ("tenant_id", "dataset_id", "document_id"),
    ),
    TaskRouteSpec(
        "enterprise_documents",
        ("tenant_id", "dataset_id", "document_id", "attempt_id", "operation_id", "batch_id"),
        ("tenant_id", "dataset_id", "document_id"),
    ),
    TaskRouteSpec(
        "enterprise_tasks",
        ("tenant_id", "task_id"),
        ("tenant_id", "task_id"),
    ),
    TaskRouteSpec(
        "enterprise_task_operations",
        ("tenant_id", "task_id"),
        ("tenant_id", "task_id"),
    ),
)

for _route in _BUILTIN_ROUTE_SPECS:
    register_task_route_spec(_route, builtin=True)


def _params(*items: TaskRouteParamSpec) -> tuple[TaskRouteParamSpec, ...]:
    return items


_BUILTIN_SOURCE_SPECS = (
    TaskSourceKindSpec(
        "document_ingest",
        "documents",
        "documents",
        "document_operations",
        "documents",
        "knowledge_documents",
        frozenset(
            {"knowledge_documents", "enterprise_documents", "enterprise_tasks", "enterprise_task_operations"}
        ),
        _params(
            TaskRouteParamSpec("tenant_id", source_field="tenant_id"),
            TaskRouteParamSpec("dataset_id", source_field="dataset_id", optional=True),
            TaskRouteParamSpec("document_id", source_field="source_id", safe_fact_field="document_id"),
        ),
    ),
    TaskSourceKindSpec(
        "index_operation",
        "indexing",
        "documents",
        "index_operations",
        "documents",
        "enterprise_documents",
        frozenset(
            {"knowledge_documents", "enterprise_documents", "enterprise_tasks", "enterprise_task_operations"}
        ),
        _params(
            TaskRouteParamSpec("tenant_id", source_field="tenant_id"),
            TaskRouteParamSpec("dataset_id", source_field="dataset_id", optional=True),
            TaskRouteParamSpec("operation_id", source_field="source_id"),
        ),
    ),
    TaskSourceKindSpec(
        "source_sync",
        "sources",
        "sources",
        "source_control",
        "sources",
        "knowledge_sources",
        frozenset(
            {"knowledge_sources", "enterprise_sources", "enterprise_tasks", "enterprise_task_operations"}
        ),
        _params(
            TaskRouteParamSpec("tenant_id", source_field="tenant_id"),
            TaskRouteParamSpec("source_id", source_field="source_id", safe_fact_field="data_source_id"),
            TaskRouteParamSpec("run_id", source_field="source_id"),
        ),
    ),
    TaskSourceKindSpec(
        "document_delete",
        "documents",
        "documents",
        "document_deletion",
        "documents",
        "enterprise_recycle_bin",
        frozenset(
            {
                "knowledge_documents",
                "enterprise_documents",
                "enterprise_recycle_bin",
                "enterprise_tasks",
                "enterprise_task_operations",
            }
        ),
        _params(
            TaskRouteParamSpec("tenant_id", source_field="tenant_id"),
            TaskRouteParamSpec("dataset_id", source_field="dataset_id", optional=True),
            TaskRouteParamSpec("document_id", source_field="source_id", safe_fact_field="document_id"),
        ),
    ),
    TaskSourceKindSpec(
        "audit_export",
        "compliance",
        "compliance",
        "audit_compliance",
        "compliance",
        "enterprise_compliance",
        frozenset(
            {
                "enterprise_compliance",
                "enterprise_audit",
                "enterprise_audit_compliance",
                "enterprise_tasks",
                "enterprise_task_operations",
            }
        ),
        _params(
            TaskRouteParamSpec("tenant_id", source_field="tenant_id"),
            TaskRouteParamSpec("export_id", source_field="source_id"),
        ),
    ),
    TaskSourceKindSpec(
        "release_quality_scan",
        "quality",
        "quality",
        "release_quality",
        "quality",
        "knowledge_quality_operations",
        frozenset(
            {"knowledge_quality_operations", "enterprise_release_quality", "enterprise_tasks", "enterprise_task_operations"}
        ),
        _params(
            TaskRouteParamSpec("tenant_id", source_field="tenant_id"),
            TaskRouteParamSpec("dataset_id", source_field="dataset_id", optional=True),
            TaskRouteParamSpec("scan_id", source_field="source_id"),
        ),
    ),
    TaskSourceKindSpec(
        "release_recertification",
        "quality",
        "quality",
        "release_quality",
        "quality",
        "knowledge_quality_operations",
        frozenset(
            {"knowledge_quality_operations", "enterprise_release_quality", "enterprise_tasks", "enterprise_task_operations"}
        ),
        _params(
            TaskRouteParamSpec("tenant_id", source_field="tenant_id"),
            TaskRouteParamSpec("dataset_id", source_field="dataset_id", optional=True),
            TaskRouteParamSpec("job_id", source_field="source_id"),
        ),
    ),
)

for _source in _BUILTIN_SOURCE_SPECS:
    register_task_source_kind(_source, builtin=True)

TASK_SOURCE_KINDS = frozenset(task_source_kind_names())
TASK_SOURCE_KIND_NAMES = task_source_kind_names()


__all__ = [
    "TASK_SOURCE_KINDS",
    "TASK_SOURCE_KIND_NAMES",
    "TASK_SOURCE_CATEGORIES",
    "TaskRouteParamSpec",
    "TaskRouteSpec",
    "TaskSourceKindSpec",
    "register_task_route_spec",
    "register_task_source_kind",
    "task_route_codes",
    "task_route_schema",
    "task_route_source_kinds",
    "task_route_spec",
    "task_source_kind",
    "task_source_kind_names",
    "task_source_kind_specs",
    "task_source_kinds_for_category",
    "task_source_route_params",
    "unregister_task_route_spec",
    "unregister_task_source_kind",
]
