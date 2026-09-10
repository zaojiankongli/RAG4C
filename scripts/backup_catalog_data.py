"""备份有数据的 catalog 表为 JSON（重建前安全网）。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, ".")
os.environ.setdefault("RAG4C_ENV_FILE", "config/.env.bench")

from sqlalchemy import create_engine, inspect, text  # noqa: E402

from config.settings import get_settings  # noqa: E402

url = get_settings().catalog.db_url
engine = create_engine(url)
out_dir = Path("backups/catalog-data")
out_dir.mkdir(parents=True, exist_ok=True)
try:
    insp = inspect(engine)
    tables = insp.get_table_names()
    backed_up = []
    with engine.connect() as c:
        for t in sorted(tables):
            if t == "alembic_version":
                continue
            n = c.execute(text(f"SELECT COUNT(*) FROM `{t}`")).scalar()
            if n:
                rows = c.execute(text(f"SELECT * FROM `{t}`")).mappings().all()
                data = [dict(r) for r in rows]
                for row in data:
                    for k, v in list(row.items()):
                        if hasattr(v, "isoformat"):
                            row[k] = v.isoformat()
                (out_dir / f"{t}.json").write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                backed_up.append((t, n))
    print("backed up tables:")
    for t, n in backed_up:
        print(f"  {t}: {n} rows")
    print(f"to {out_dir}")
finally:
    engine.dispose()
