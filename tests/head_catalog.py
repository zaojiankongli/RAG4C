"""共享 fixture：把「迁移到 head 的 catalog」补成 head 期望的完整形态。

## 为什么需要这个文件

`catalog_schema.inspect_catalog_schema()` 在 head 上不仅校验**结构**（表/列/约束/索引），
还校验**数据完整性**：每租户必须有 1 个默认 workspace + 项目默认 release channels，
每个 dataset 必须有 1 条 workspace ownership（+ 配套的 primary binding）。

这条校验是 stage 18/19 引入的既有语义，不是新加的严格化——`0029` 迁移本来会给
已有 tenants 播 release channel，但**测试里的 tenants 是在迁移之后才插入的**，
于是播种空转，表仍然是空的。

于是历史上按"建库 → 插 Tenant/Dataset → 跑业务"写的测试，在 head 上会被
fail-closed 成 `{"status": "unsafe", "error": "catalog schema is incomplete; ..."}`。
症状很像产品缺陷（CLI 返回 schema incomplete），实际是**测试夹具落后于 head 的数据契约**。

这个 helper 把那批夹具共同缺的那一步抽出来，一处维护，而不是在 N 个测试文件里
各复制一遍 SQL（`scripts/seed_workspace_channel_ownership.py` 是运维侧的同一件事）。

幂等：只补缺失的行，不覆盖已有数据；可重复调用。

用法：

    from tests.head_catalog import seed_head_authority

    catalog_schema.upgrade_catalog(url)
    engine = create_engine(url)
    with Session(engine) as session:
        session.add_all([Tenant(...), Dataset(...)])
        session.commit()
    seed_head_authority(engine)      # ← 建库后、跑业务前
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text

# 与 0029 迁移 DEFAULT_CHANNELS 一致：(code, name, risk_tier, promotion_order, is_default_serving)
DEFAULT_CHANNELS = (
    ("development", "Development", "low", 10, False),
    ("testing", "Testing", "medium", 20, False),
    ("production", "Production", "high", 30, True),
)


def seed_head_authority(engine: Any, *, now: str | None = None) -> dict[str, int]:
    """给 head catalog 补齐 workspace / release channel / dataset ownership。

    Returns:
        {"workspaces": n, "channels": n, "ownerships": n, "bindings": n} —— 本次**新增**的行数
        （已存在的记 0），便于测试断言"确实补上了"而不是静默通过。
    """
    stamp = now or datetime.now().isoformat(sep=" ")[:19]
    created = {"workspaces": 0, "channels": 0, "ownerships": 0, "bindings": 0}

    with engine.begin() as connection:
        tenants = [
            str(row[0])
            for row in connection.execute(text("SELECT id FROM tenants ORDER BY id")).fetchall()
        ]
        datasets = [
            (str(row[0]), str(row[1]))
            for row in connection.execute(
                text("SELECT id, tenant_id FROM datasets ORDER BY id")
            ).fetchall()
        ]

        for tenant_id in tenants:
            workspace_id = f"ws-{tenant_id}"
            existing = connection.execute(
                text("SELECT COUNT(*) FROM tenant_workspaces WHERE tenant_id = :t"),
                {"t": tenant_id},
            ).scalar_one()
            if not existing:
                connection.execute(
                    text(
                        "INSERT INTO tenant_workspaces "
                        "(id, tenant_id, code, name, normalized_name, description, status, "
                        "environment, is_default, active_default_slot, revision, created_at, "
                        "created_by, updated_at, updated_by, archived_at, archived_by) "
                        "VALUES (:id, :t, 'default', '默认工作区', 'default', '', 'active', "
                        "'production', 1, 'default', 1, :now, 'seed', :now, 'seed', NULL, NULL)"
                    ),
                    {"id": workspace_id, "t": tenant_id, "now": stamp},
                )
                created["workspaces"] += 1

            for code, name, risk_tier, order, is_default in DEFAULT_CHANNELS:
                existing = connection.execute(
                    text(
                        "SELECT COUNT(*) FROM tenant_release_channels "
                        "WHERE tenant_id = :t AND normalized_code = :c"
                    ),
                    {"t": tenant_id, "c": code.casefold()},
                ).scalar_one()
                if existing:
                    continue
                connection.execute(
                    text(
                        "INSERT INTO tenant_release_channels "
                        "(id, tenant_id, code, normalized_code, name, status, risk_tier, "
                        "promotion_order, is_default_serving, active_default_slot, revision, "
                        "created_at, created_by, updated_at, updated_by, archived_at, archived_by) "
                        "VALUES (:id, :t, :code, :norm, :name, 'active', :risk, :order, "
                        ":default, :slot, 1, :now, 'seed', :now, 'seed', NULL, NULL)"
                    ),
                    {
                        "id": f"ch-{tenant_id}-{code}",
                        "t": tenant_id,
                        "code": code,
                        "norm": code.casefold(),
                        "name": name,
                        "risk": risk_tier,
                        "order": order,
                        "default": is_default,
                        "slot": "default" if is_default else None,
                        "now": stamp,
                    },
                )
                created["channels"] += 1

        for dataset_id, tenant_id in datasets:
            existing = connection.execute(
                text(
                    "SELECT COUNT(*) FROM dataset_workspace_ownerships "
                    "WHERE tenant_id = :t AND dataset_id = :d"
                ),
                {"t": tenant_id, "d": dataset_id},
            ).scalar_one()
            if not existing:
                connection.execute(
                    text(
                        "INSERT INTO dataset_workspace_ownerships "
                        "(id, tenant_id, dataset_id, workspace_id, revision, created_at, "
                        "created_by, updated_at, updated_by, last_transfer_at) "
                        "VALUES (:id, :t, :d, :ws, 1, :now, 'seed', :now, 'seed', NULL)"
                    ),
                    {
                        "id": f"ow-{uuid.uuid4().hex[:8]}",
                        "t": tenant_id,
                        "d": dataset_id,
                        "ws": f"ws-{tenant_id}",
                        "now": stamp,
                    },
                )
                created["ownerships"] += 1

            existing = connection.execute(
                text(
                    "SELECT COUNT(*) FROM tenant_workspace_datasets "
                    "WHERE tenant_id = :t AND dataset_id = :d AND workspace_id = :ws "
                    "AND binding_kind = 'primary'"
                ),
                {"t": tenant_id, "d": dataset_id, "ws": f"ws-{tenant_id}"},
            ).scalar_one()
            if not existing:
                connection.execute(
                    text(
                        "INSERT INTO tenant_workspace_datasets "
                        "(tenant_id, workspace_id, dataset_id, binding_kind, "
                        "active_primary_slot, status, revision, created_at, created_by, "
                        "updated_at, updated_by, removed_at, removed_by) "
                        "VALUES (:t, :ws, :d, 'primary', 'primary', 'active', 1, "
                        ":now, 'seed', :now, 'seed', NULL, NULL)"
                    ),
                    {
                        "t": tenant_id,
                        "ws": f"ws-{tenant_id}",
                        "d": dataset_id,
                        "now": stamp,
                    },
                )
                created["bindings"] += 1

    return created
