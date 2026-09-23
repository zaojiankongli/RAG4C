"""扩展轴 #8：通知 source_kind 的投影规则收成一张声明表。

台账写「~13 处」。重数之后只有投影层是数据：来源 id 兜底字段、category、
dataset 必填还是必须为空、缺省事件语义、route 与参数 schema、route 参数回指。
回执的 `_safe_route` / `_handoff` 和两个物化函数仍是各自的行为代码，本套不声称
把它们收进来了。存储 CHECK 也还在：注册一种来源不会让它能落库，对账用例负责
在声明和 CHECK 分叉时变红。

零改分支的证据是 `test_registering_a_kind_projects_it_without_editing_the_host`：
运行时挂一种表里没有的来源，投影当场认它，且宿主文件字节不变。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.enterprise_notification_center import (
    NotificationAuthorityInvalid,
    project_notification_payload,
    project_notification_route,
    project_notification_source,
)
from core.notification_source_kinds import (
    BUILTIN_NOTIFICATION_SOURCE_KINDS,
    NotificationSourceKindSpec,
    register_notification_source_kind,
    unregister_notification_source_kind,
)

REPO = Path(__file__).resolve().parents[1]
HOST = REPO / "core" / "enterprise_notification_center.py"
ORM = REPO / "models" / "orm.py"
MIGRATION = REPO / "catalog_migrations" / "versions" / "0032_enterprise_notification_center.py"

_SCOPE_RE = re.compile(
    r"\(source_kind='([^']+)' AND source_dataset_id IS (NOT NULL|NULL) AND category='([^']+)'\)"
)
_ROUTE_RE = re.compile(r"\(target_route_code='([^']+)' AND source_kind='([^']+)'\)")


def _constraint(text: str, name: str) -> str:
    marker = f'name="{name}"'
    at = text.find(marker)
    assert at != -1, f"{name} 找不到 —— 守卫对着空气"
    start = text.rfind("CheckConstraint(", 0, at)
    assert start != -1, name
    parts = re.findall(r'"([^"]*)"', text[start:at])
    assert parts, name
    return "".join(parts)


def _in_list(sql: str, column: str) -> frozenset[str]:
    match = re.search(rf"{column} IN \(([^)]*)\)", sql)
    assert match is not None, sql
    return frozenset(part.strip().strip("'") for part in match.group(1).split(","))


def _billing(**overrides: object) -> NotificationSourceKindSpec:
    spec = NotificationSourceKindSpec(
        kind="billing_notice",
        category="billing",
        route_code="billing_operations",
        source_id_field="invoice_id",
        requires_dataset=False,
        default_semantic="issued",
        route_params=("tenant_id", "invoice_id"),
        source_id_route_param="invoice_id",
    )
    if not overrides:
        return spec
    return NotificationSourceKindSpec(**{**spec.__dict__, **overrides})


def test_builtin_projection_keeps_the_observed_defaults() -> None:
    """重构前实测：缺省语义 quality→opened、approval→pending；id 能从各自的别名字段来。"""
    quality = project_notification_source(
        {
            "source_kind": "quality_alert",
            "tenant_id": "tenant-a",
            "alert_id": "alert-a",
            "source_revision": 4,
            "source_dataset_id": "dataset-a",
            "source_digest": "a" * 64,
            "severity": "critical",
        }
    )
    assert quality["source_id"] == "alert-a"
    assert quality["category"] == "quality"
    assert quality["event_semantic"] == "opened"
    assert quality["source_dataset_id"] == "dataset-a"
    assert "alert_id" not in quality["safe_facts"]

    approval = project_notification_source(
        {
            "source_kind": "approval_pending_for_me",
            "tenant_id": "tenant-a",
            "approval_request_id": "approval-a",
            "source_revision": 2,
            "source_digest": "b" * 64,
            "severity": "warning",
        }
    )
    assert approval["source_id"] == "approval-a"
    assert approval["category"] == "approval"
    assert approval["event_semantic"] == "pending"
    assert approval["source_dataset_id"] is None

    with pytest.raises(
        NotificationAuthorityInvalid,
        match="source_dataset_id must be null for approval_pending_for_me",
    ):
        project_notification_source(
            {
                "source_kind": "approval_pending_for_me",
                "tenant_id": "tenant-a",
                "source_id": "approval-a",
                "source_dataset_id": "dataset-a",
                "source_revision": 2,
                "source_digest": "b" * 64,
                "severity": "warning",
            }
        )


def test_registering_a_kind_projects_it_without_editing_the_host() -> None:
    """零改分支：挂一种表外的来源，投影认它，宿主文件一字节不动。

    这也钉住 id 别名不会漏进 safe_facts —— 漏进去的话新来源的身份字段会变成事实。
    """
    before = HOST.read_bytes()
    register_notification_source_kind(_billing())
    try:
        projected = project_notification_source(
            {
                "source_kind": "billing_notice",
                "tenant_id": "tenant-a",
                "invoice_id": "invoice-a",
                "source_revision": 1,
                "source_digest": "c" * 64,
                "severity": "info",
            }
        )
        assert projected["source_id"] == "invoice-a"
        assert projected["category"] == "billing"
        assert projected["event_semantic"] == "issued"
        assert projected["source_dataset_id"] is None
        assert "invoice_id" not in projected["safe_facts"]

        route = project_notification_route(
            "billing_operations",
            {"tenant_id": "tenant-a", "invoice_id": "invoice-a"},
        )
        assert route == {
            "target_route_code": "billing_operations",
            "target_route_params_json": {
                "tenant_id": "tenant-a",
                "invoice_id": "invoice-a",
            },
        }
        payload = project_notification_payload(
            source={
                "source_kind": "billing_notice",
                "tenant_id": "tenant-a",
                "invoice_id": "invoice-a",
                "source_revision": 1,
                "source_digest": "c" * 64,
                "severity": "info",
                "occurred_at": "2026-08-29T12:00:00.000001Z",
            },
            route=route,
            action_required=False,
            mandatory=False,
            title_code="billing_notice_title",
            summary_code="billing_notice_summary",
        )
        assert payload["category"] == "billing"
        assert payload["target_route_code"] == "billing_operations"
        assert payload["target_route_params_json"]["invoice_id"] == "invoice-a"

        with pytest.raises(
            NotificationAuthorityInvalid,
            match="source_dataset_id must be null for billing_notice",
        ):
            project_notification_source(
                {
                    "source_kind": "billing_notice",
                    "tenant_id": "tenant-a",
                    "invoice_id": "invoice-a",
                    "source_dataset_id": "dataset-a",
                    "source_revision": 1,
                    "source_digest": "c" * 64,
                    "severity": "info",
                }
            )
        with pytest.raises(NotificationAuthorityInvalid, match="route code does not match"):
            project_notification_payload(
                source={
                    "source_kind": "billing_notice",
                    "tenant_id": "tenant-a",
                    "source_id": "invoice-a",
                    "source_revision": 1,
                    "source_digest": "c" * 64,
                    "severity": "info",
                    "occurred_at": "2026-08-29T12:00:00.000001Z",
                },
                route=project_notification_route(
                    "enterprise_approval",
                    {"tenant_id": "tenant-a", "approval_request_id": "invoice-a"},
                ),
                action_required=False,
                mandatory=False,
                title_code="billing_notice_title",
                summary_code="billing_notice_summary",
            )
    finally:
        unregister_notification_source_kind("billing_notice")

    assert HOST.read_bytes() == before
    with pytest.raises(NotificationAuthorityInvalid, match="source_kind"):
        project_notification_source(
            {
                "source_kind": "billing_notice",
                "tenant_id": "tenant-a",
                "source_id": "invoice-a",
                "source_revision": 1,
                "source_digest": "c" * 64,
                "severity": "info",
            }
        )


def test_registration_rejects_a_shape_that_would_silently_change_projection() -> None:
    with pytest.raises(ValueError, match="already registered"):
        register_notification_source_kind(_billing(kind="quality_alert", route_code="billing_ops"))
    with pytest.raises(ValueError, match="route code already registered"):
        register_notification_source_kind(
            _billing(kind="billing_notice", route_code="enterprise_approval")
        )
    with pytest.raises(ValueError, match="dataset_route_param must be null"):
        register_notification_source_kind(_billing(dataset_route_param="dataset_id"))
    with pytest.raises(ValueError, match="dataset_route_param must be a lowercase"):
        register_notification_source_kind(
            _billing(requires_dataset=True, dataset_route_param=None)
        )


@pytest.mark.parametrize("path", [ORM, MIGRATION], ids=["orm", "migration-0032"])
def test_builtin_declarations_match_stored_checks(path: Path) -> None:
    """内置声明必须和两条 CHECK（ORM 与装它们的迁移）是同一份契约。

    只比内置元组，不比运行时注册表：测试挂上的来源本来就不该进库。
    """
    text = path.read_text(encoding="utf-8")
    kinds = _in_list(_constraint(text, "ck_tenant_notifications_source_kind"), "source_kind")
    categories = _in_list(_constraint(text, "ck_tenant_notifications_category"), "category")
    scope = set(_SCOPE_RE.findall(_constraint(text, "ck_tenant_notifications_source_scope")))
    routes = set(_ROUTE_RE.findall(_constraint(text, "ck_tenant_notifications_route")))

    expected_kinds = {spec.kind for spec in BUILTIN_NOTIFICATION_SOURCE_KINDS}
    expected_categories = {spec.category for spec in BUILTIN_NOTIFICATION_SOURCE_KINDS}
    expected_scope = {
        (
            spec.kind,
            "NOT NULL" if spec.requires_dataset else "NULL",
            spec.category,
        )
        for spec in BUILTIN_NOTIFICATION_SOURCE_KINDS
    }
    expected_routes = {(spec.route_code, spec.kind) for spec in BUILTIN_NOTIFICATION_SOURCE_KINDS}

    assert kinds == expected_kinds
    assert categories == expected_categories
    assert scope == expected_scope
    assert routes == expected_routes
    assert len(BUILTIN_NOTIFICATION_SOURCE_KINDS) == 2
