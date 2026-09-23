"""通知 `source_kind` 的投影规则：一种来源一行声明，而不是投影函数里的一条分支。

轴 #8 的台账把三层不同的东西加成了「~13 处」：

- **投影**（本模块服务的那一层）：来源 id 用哪个字段兜底、强制哪个 category、
  `source_dataset_id` 是必填还是必须为空、缺省事件语义、配哪条 route、route 参数的
  精确 schema、哪些 route 参数必须回指来源字段或 safe facts。这些是数据。
- **回执**（`enterprise_notification_receipts.py` 的 `_safe_route` / `_handoff`）：
  每种来源有自己的权限查询与新旧参数形态。那是行为，不在这张表里。
- **物化**（两个 `materialize_*`）：生产者，各自锁来源行、挑收件人、组 payload。
  也不是这张表能替掉的。

存储侧 `CHECK` 仍是另一份契约。往这张表注册一种来源，**不会**让那一行能落库；
忘了迁移由测试对账抓，不在投影路径上假装 CHECK 已经跟着变了。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_CODE_RE = re.compile(r"[a-z][a-z0-9_.-]{0,63}")


@dataclass(frozen=True)
class NotificationSourceKindSpec:
    """一种通知来源在投影层的全部数据形状。"""

    kind: str
    category: str
    route_code: str
    source_id_field: str
    requires_dataset: bool
    default_semantic: str
    route_params: tuple[str, ...]
    source_id_route_param: str
    dataset_route_param: str | None = None
    fact_route_params: tuple[str, ...] = ()


def _code(value: object, field: str) -> str:
    if not isinstance(value, str) or _CODE_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase safe code")
    return value


def _validate(spec: NotificationSourceKindSpec) -> None:
    if not isinstance(spec, NotificationSourceKindSpec):
        raise TypeError(
            f"expected NotificationSourceKindSpec, got {type(spec).__name__}"
        )
    _code(spec.kind, "kind")
    _code(spec.category, "category")
    _code(spec.route_code, "route_code")
    _code(spec.source_id_field, "source_id_field")
    _code(spec.default_semantic, "default_semantic")
    _code(spec.source_id_route_param, "source_id_route_param")
    if type(spec.requires_dataset) is not bool:
        raise ValueError("requires_dataset must be a bool")
    if not isinstance(spec.route_params, tuple) or not spec.route_params:
        raise ValueError("route_params must be a non-empty tuple")
    if len(set(spec.route_params)) != len(spec.route_params):
        raise ValueError("route_params must be unique")
    for name in spec.route_params:
        _code(name, "route_params")
    if "tenant_id" not in spec.route_params:
        raise ValueError("route_params must include tenant_id")
    if spec.source_id_route_param not in spec.route_params:
        raise ValueError("route_params must include source_id_route_param")
    if spec.requires_dataset:
        _code(spec.dataset_route_param, "dataset_route_param")
        if spec.dataset_route_param not in spec.route_params:
            raise ValueError("dataset_route_param must be in route_params")
        if spec.dataset_route_param in {"tenant_id", spec.source_id_route_param}:
            raise ValueError("dataset_route_param collides with an identity param")
    elif spec.dataset_route_param is not None:
        raise ValueError("dataset_route_param must be null when dataset is forbidden")
    if not isinstance(spec.fact_route_params, tuple):
        raise ValueError("fact_route_params must be a tuple")
    reserved = {"tenant_id", spec.source_id_route_param, spec.dataset_route_param}
    for name in spec.fact_route_params:
        _code(name, "fact_route_params")
        if name not in spec.route_params or name in reserved:
            raise ValueError(f"fact_route_params entry is not a free route param: {name}")


BUILTIN_NOTIFICATION_SOURCE_KINDS: tuple[NotificationSourceKindSpec, ...] = (
    NotificationSourceKindSpec(
        kind="quality_alert",
        category="quality",
        route_code="knowledge_quality_operations",
        source_id_field="alert_id",
        requires_dataset=True,
        default_semantic="opened",
        route_params=("tenant_id", "dataset_id", "release_id", "channel_id", "alert_id"),
        source_id_route_param="alert_id",
        dataset_route_param="dataset_id",
        fact_route_params=("release_id", "channel_id"),
    ),
    NotificationSourceKindSpec(
        kind="approval_pending_for_me",
        category="approval",
        route_code="enterprise_approval",
        source_id_field="approval_request_id",
        requires_dataset=False,
        default_semantic="pending",
        route_params=("tenant_id", "approval_request_id"),
        source_id_route_param="approval_request_id",
    ),
)


_BY_KIND: dict[str, NotificationSourceKindSpec] = {}
_BY_ROUTE: dict[str, NotificationSourceKindSpec] = {}


def register_notification_source_kind(spec: NotificationSourceKindSpec) -> None:
    """声明一种来源。同名 kind 或同名 route_code 直接拒，不覆盖内置。"""
    _validate(spec)
    if spec.kind in _BY_KIND:
        raise ValueError(f"notification source kind already registered: {spec.kind}")
    if spec.route_code in _BY_ROUTE:
        raise ValueError(f"notification route code already registered: {spec.route_code}")
    _BY_KIND[spec.kind] = spec
    _BY_ROUTE[spec.route_code] = spec


def unregister_notification_source_kind(kind: str) -> None:
    """撤回一条声明。未知名字忽略，方便测试收尾。"""
    spec = _BY_KIND.pop(kind, None)
    if spec is not None and _BY_ROUTE.get(spec.route_code) is spec:
        _BY_ROUTE.pop(spec.route_code, None)


def notification_source_kind(kind: object) -> NotificationSourceKindSpec | None:
    if not isinstance(kind, str):
        return None
    return _BY_KIND.get(kind)


def notification_source_kind_for_route(
    route_code: object,
) -> NotificationSourceKindSpec | None:
    if not isinstance(route_code, str):
        return None
    return _BY_ROUTE.get(route_code)


def allowed_notification_categories() -> frozenset[str]:
    return frozenset(spec.category for spec in _BY_KIND.values())


for _spec in BUILTIN_NOTIFICATION_SOURCE_KINDS:
    register_notification_source_kind(_spec)
