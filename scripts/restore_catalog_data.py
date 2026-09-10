"""回灌 9 张有数据的表（从 backups/catalog-data/*.json，适配 head schema）。

回灌策略：只 INSERT 备份中存在的列；head 新增列（备份里没有的）用数据库默认值
（server_default 已定义，如 status/revision/updated_at 等）。
按 FK 依赖顺序：tenants -> accounts -> tenant_members -> datasets -> documents
-> chunk_heads -> document_ingest_attempts -> document_ingest_spans -> index_operations。
幂等：表已有数据则跳过（防重复回灌）。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, ".")
os.environ.setdefault("RAG4C_ENV_FILE", "config/.env.bench")

from sqlalchemy import create_engine, inspect, text  # noqa: E402

from config.settings import get_settings  # noqa: E402

BACKUP_DIR = Path("backups/catalog-data")
ORDER = [
    "tenants", "accounts", "tenant_members", "datasets", "documents",
    "chunk_heads", "document_ingest_attempts", "document_ingest_spans",
    "index_operations",
]


def _adapt(value):
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ")[:19]
    return value


def main() -> int:
    url = get_settings().catalog.db_url
    engine = create_engine(url)
    try:
        insp = inspect(engine)
        for table in ORDER:
            backup = BACKUP_DIR / f"{table}.json"
            if not backup.exists():
                print(f"{table}: no backup, skip")
                continue
            with engine.connect() as c:
                existing = c.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar()
            if existing:
                print(f"{table}: already has {existing} rows, skip (idempotent)")
                continue
            rows = json.loads(backup.read_text(encoding="utf-8"))
            if not rows:
                print(f"{table}: empty backup, skip")
                continue
            cols = [c["name"] for c in insp.get_columns(table)]
            present = [k for k in rows[0] if k in cols]
            placeholders = ", ".join(f":{k}" for k in present)
            sql = f"INSERT INTO `{table}` ({', '.join(present)}) VALUES ({placeholders})"
            with engine.begin() as c:
                for row in rows:
                    params = {k: _adapt(row[k]) for k in present}
                    c.execute(text(sql), params)
            print(f"{table}: restored {len(rows)} rows (cols: {present})")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
