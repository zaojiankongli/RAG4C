"""Drop 全部空壳 catalog 表（0 行），保留有数据的表。

背景：数据库处于半迁移状态——0017-0036 的表已存在但结构不完整（列/约束缺失），
且全部为 0 行空壳（有数据的 9 张表均为 0015 早期表）。要干净升级到 head，
必须清除这些空壳表让 alembic 重建。

安全措施：
1. 只 drop 行数为 0 的表（有数据的表先经 scripts/backup_catalog_data.py 备份）；
2. FOREIGN_KEY_CHECKS=0 期间执行（避免空表间 FK 顺序问题），完毕恢复；
3. drop 前再次确认行数为 0。
用法：python scripts/drop_empty_catalog_tables.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, ".")
os.environ.setdefault("RAG4C_ENV_FILE", "config/.env.bench")

from sqlalchemy import create_engine, inspect, text  # noqa: E402

from config.settings import get_settings  # noqa: E402

# 有数据的表（保护名单，绝不 drop）
PROTECTED = {
    "accounts", "tenants", "tenant_members", "datasets", "documents",
    "chunk_heads", "document_ingest_attempts", "document_ingest_spans",
    "index_operations",
}


def main() -> int:
    url = get_settings().catalog.db_url
    engine = create_engine(url)
    try:
        insp = inspect(engine)
        tables = insp.get_table_names()
        to_drop = []
        with engine.connect() as c:
            for t in tables:
                if t == "alembic_version" or t in PROTECTED:
                    continue
                n = c.execute(text(f"SELECT COUNT(*) FROM `{t}`")).scalar()
                if n == 0:
                    to_drop.append(t)
        if not to_drop:
            print("no empty tables to drop")
            return 0
        print(f"dropping {len(to_drop)} empty tables: {', '.join(sorted(to_drop))}")
        with engine.begin() as c:
            c.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            for t in to_drop:
                c.execute(text(f"DROP TABLE IF EXISTS `{t}`"))
            c.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        after = set(inspect(engine).get_table_names())
        print(f"tables remaining: {len(after)}")
        print(f"protected data tables intact: {PROTECTED <= after}")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
