"""R2-A：补 head schema 预期种子数据（workspace / release channel / dataset ownership）。

背景：完整重建后，tenant_workspaces / tenant_release_channels /
dataset_workspace_ownerships 三张表为空（0029 迁移的 channel 播种在重建时
因 tenants 为空而空转），而 head schema 完整性校验期望：每租户 1 个默认
workspace + 项目默认 release channels；每个 dataset 1 条 workspace ownership。

幂等：仅当相关表为空/缺记录时补（不覆盖已有数据）。
用法：python scripts/seed_workspace_channel_ownership.py
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime

sys.path.insert(0, ".")
os.environ.setdefault("RAG4C_ENV_FILE", "config/.env.bench")

from sqlalchemy import create_engine, text  # noqa: E402

from config.settings import get_settings  # noqa: E402

# 与 0029 迁移 DEFAULT_CHANNELS 一致：(code, name, risk_tier, promotion_order, is_default_serving)
DEFAULT_CHANNELS = (
    ("development", "Development", "low", 10, False),
    ("testing", "Testing", "medium", 20, False),
    ("production", "Production", "high", 30, True),
)

url = get_settings().catalog.db_url
engine = create_engine(url)
now = datetime.now().isoformat(sep=" ")[:19]


def main() -> int:
    with engine.connect() as c:
        tenants = [r[0] for r in c.execute(text("SELECT id FROM tenants ORDER BY id")).fetchall()]
        datasets = c.execute(text("SELECT id, tenant_id FROM datasets")).fetchall()
        ws_count = c.execute(text("SELECT COUNT(*) FROM tenant_workspaces")).scalar()
        ch_count = c.execute(text("SELECT COUNT(*) FROM tenant_release_channels")).scalar()
        ow_count = c.execute(text("SELECT COUNT(*) FROM dataset_workspace_ownerships")).scalar()
        print(f"tenants={len(tenants)} datasets={len(datasets)} "
              f"workspaces={ws_count} channels={ch_count} ownerships={ow_count}")

    with engine.begin() as c:
        for tid in tenants:
            if ws_count == 0:
                ws_id = f"ws-{tid}"
                c.execute(
                    text(
                        "INSERT INTO tenant_workspaces "
                        "(id, tenant_id, code, name, normalized_name, description, status, "
                        "environment, is_default, active_default_slot, revision, created_at, "
                        "created_by, updated_at, updated_by, archived_at, archived_by) "
                        "VALUES (:id, :tid, 'default', '默认工作区', 'default', '', 'active', "
                        "'production', :is_default, 'default', 1, :now, 'seed', :now, 'seed', NULL, NULL)"
                    ),
                    {"id": ws_id, "tid": tid, "now": now, "is_default": True},
                )
                print(f"workspace seeded: {ws_id}")
            if ch_count == 0:
                for code, name, risk_tier, promotion_order, is_default_serving in DEFAULT_CHANNELS:
                    ch_id = f"ch-{tid}-{code}"
                    c.execute(
                        text(
                            "INSERT INTO tenant_release_channels "
                            "(id, tenant_id, code, normalized_code, name, status, risk_tier, "
                            "promotion_order, is_default_serving, active_default_slot, revision, "
                            "created_at, created_by, updated_at, updated_by, archived_at, archived_by) "
                            "VALUES (:id, :tid, :code, :norm, :name, 'active', :risk, :order, "
                            ":def, :slot, 1, :now, 'seed', :now, 'seed', NULL, NULL)"
                        ),
                        {
                            "id": ch_id, "tid": tid, "code": code, "norm": code.casefold(),
                            "name": name, "risk": risk_tier, "order": promotion_order,
                            "def": is_default_serving,
                            "slot": "default" if is_default_serving else None,
                            "now": now,
                        },
                    )
                    print(f"release channel seeded: {ch_id} ({code})")
            for did, dtid in datasets:
                if dtid != tid:
                    continue
                existing = c.execute(
                    text(
                        "SELECT COUNT(*) FROM dataset_workspace_ownerships "
                        "WHERE tenant_id=:t AND dataset_id=:d"
                    ),
                    {"t": tid, "d": did},
                ).scalar()
                if existing == 0:
                    ow_id = f"ow-{uuid.uuid4().hex[:8]}"
                    c.execute(
                        text(
                            "INSERT INTO dataset_workspace_ownerships "
                            "(id, tenant_id, dataset_id, workspace_id, revision, created_at, "
                            "created_by, updated_at, updated_by, last_transfer_at) "
                            "VALUES (:id, :t, :d, :ws, 1, :now, 'seed', :now, 'seed', NULL)"
                        ),
                        {"id": ow_id, "t": tid, "d": did, "ws": f"ws-{tid}", "now": now},
                    )
                    print(f"ownership seeded: {ow_id} (dataset {did})")
            # 4. primary workspace-dataset binding（ownership 的配套，幂等）
            for did, dtid in datasets:
                if dtid != tid:
                    continue
                existing = c.execute(
                    text(
                        "SELECT COUNT(*) FROM tenant_workspace_datasets "
                        "WHERE tenant_id=:t AND dataset_id=:d AND workspace_id=:ws "
                        "AND binding_kind='primary'"
                    ),
                    {"t": tid, "d": did, "ws": f"ws-{tid}"},
                ).scalar()
                if existing == 0:
                    c.execute(
                        text(
                            "INSERT INTO tenant_workspace_datasets "
                            "(tenant_id, workspace_id, dataset_id, binding_kind, "
                            "active_primary_slot, status, revision, created_at, created_by, "
                            "updated_at, updated_by, removed_at, removed_by) "
                            "VALUES (:t, :ws, :d, 'primary', 'primary', 'active', 1, "
                            ":now, 'seed', :now, 'seed', NULL, NULL)"
                        ),
                        {"t": tid, "ws": f"ws-{tid}", "d": did, "now": now},
                    )
                    print(f"primary binding seeded (dataset {did})")

    with engine.connect() as c:
        print("after:",
              "ws=", c.execute(text("SELECT COUNT(*) FROM tenant_workspaces")).scalar(),
              "ch=", c.execute(text("SELECT COUNT(*) FROM tenant_release_channels")).scalar(),
              "ow=", c.execute(text("SELECT COUNT(*) FROM dataset_workspace_ownerships")).scalar())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
