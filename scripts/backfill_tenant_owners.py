"""为 ownerless 的 active 租户补 owner（0016 前置校验通过所需）。

背景：catalog schema 升级到 head 被 0016 前置校验拦住——default / demo /
probe-tenant 三个 active 租户在 tenant_members 里没有 role='owner' 记录。
accounts 与 tenant_members 表当前均为空。

本脚本（幂等）：
1. 创建系统 owner 账号（accounts，id='sys-owner'，若已存在则复用）；
2. 为上述租户各插入 role='owner' 的成员记录（若已存在则跳过）。
全程一个事务；执行前打印将做的操作；执行后复跑 0016 前置校验断言。

用法：python scripts/backfill_tenant_owners.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, ".")
os.environ.setdefault("RAG4C_ENV_FILE", "config/.env.bench")

from sqlalchemy import create_engine, text  # noqa: E402

from config.settings import get_settings  # noqa: E402

OWNER_ACCOUNT_ID = "sys-owner"
OWNER_ACCOUNT_NAME = "系统所有者"
OWNER_ACCOUNT_EMAIL = "sys-owner@rag4c.local"
TARGET_TENANTS = ("default", "demo", "probe-tenant")


def main() -> int:
    url = get_settings().catalog.db_url
    engine = create_engine(url)
    now = datetime.now()
    try:
        with engine.begin() as c:
            # 1. 创建系统 owner 账号（幂等）
            existing = c.execute(
                text("SELECT id FROM accounts WHERE id = :id"), {"id": OWNER_ACCOUNT_ID}
            ).fetchone()
            if existing is None:
                c.execute(
                    text(
                        "INSERT INTO accounts (id, name, email, created_at) "
                        "VALUES (:id, :name, :email, :created_at)"
                    ),
                    {"id": OWNER_ACCOUNT_ID, "name": OWNER_ACCOUNT_NAME,
                     "email": OWNER_ACCOUNT_EMAIL, "created_at": now},
                )
                print(f"account created: {OWNER_ACCOUNT_ID}")
            else:
                print(f"account exists: {OWNER_ACCOUNT_ID}")

            # 2. 为每个目标租户补 owner 成员（幂等）
            for tenant_id in TARGET_TENANTS:
                existing_member = c.execute(
                    text(
                        "SELECT id FROM tenant_members "
                        "WHERE tenant_id = :t AND account_id = :a AND role = 'owner'"
                    ),
                    {"t": tenant_id, "a": OWNER_ACCOUNT_ID},
                ).fetchone()
                if existing_member is None:
                    c.execute(
                        text(
                            "INSERT INTO tenant_members (account_id, tenant_id, role, created_at) "
                            "VALUES (:a, :t, 'owner', :created_at)"
                        ),
                        {"a": OWNER_ACCOUNT_ID, "t": tenant_id, "created_at": now},
                    )
                    print(f"owner added for tenant: {tenant_id}")
                else:
                    print(f"owner already present for tenant: {tenant_id}")

        # 3. 复跑 0016 前置校验（只读断言）
        with engine.connect() as c:
            ownerless = c.execute(
                text(
                    "SELECT t.id FROM tenants AS t "
                    "LEFT JOIN tenant_members AS m "
                    "  ON m.tenant_id = t.id AND m.role = 'owner' "
                    "WHERE t.status = 'active' "
                    "GROUP BY t.id HAVING COUNT(m.id) = 0 ORDER BY t.id"
                )
            ).fetchall()
        if ownerless:
            print(f"FAIL: still ownerless active tenants: {[r[0] for r in ownerless]}")
            return 1
        print("OK: all active tenants now have an owner")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
