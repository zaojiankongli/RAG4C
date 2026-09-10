"""循环清障升级：自动 drop "Table already exists" 冲突表，直到 upgrade 成功。

背景：MySQL DDL 隐式提交使迁移半应用（表建了但版本未推进）。重跑 upgrade 会撞
"Table X already exists"。本脚本解析该错误 → drop 冲突表（0 行空壳）→ 重跑，
直至成功或出现非表冲突错误。

安全：只 drop 从错误信息解析出的表名；drop 前确认该表行数为 0。
用法：python scripts/loop_upgrade_catalog.py [--max-rounds 40]
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

sys.path.insert(0, ".")
os.environ.setdefault("RAG4C_ENV_FILE", "config/.env.bench")

from sqlalchemy import create_engine, inspect, text  # noqa: E402

from config.settings import get_settings  # noqa: E402

PYTHON = sys.executable
TABLE_EXISTS = re.compile(r"Table '(\w+)' already exists")


def drop_table_if_empty(engine, table: str) -> bool:
    try:
        insp = inspect(engine)
        if table not in insp.get_table_names():
            return False
        with engine.connect() as c:
            n = c.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar()
        if n != 0:
            print(f"  SKIP {table}: has {n} rows (not empty, refusing to drop)")
            return False
        with engine.begin() as c:
            c.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            c.execute(text(f"DROP TABLE IF EXISTS `{table}`"))
            c.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        print(f"  dropped conflict table: {table}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  drop {table} failed: {exc}")
        return False


def run_upgrade() -> tuple[int, str]:
    proc = subprocess.run(
        [PYTHON, "-X", "utf8", "scripts/migrate_catalog.py", "upgrade", "--revision", "head"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return proc.returncode, proc.stderr or proc.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-rounds", type=int, default=60)
    args = parser.parse_args()

    url = get_settings().catalog.db_url
    engine = create_engine(url)
    try:
        for round_no in range(1, args.max_rounds + 1):
            code, output = run_upgrade()
            if code == 0:
                with engine.connect() as c:
                    ver = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
                print(f"UPGRADE SUCCESS after {round_no} rounds; alembic_version={ver}")
                return 0
            match = TABLE_EXISTS.search(output)
            if not match:
                print(f"round {round_no}: non-table-conflict failure, stopping")
                print(output[-800:])
                return 1
            table = match.group(1)
            print(f"round {round_no}: conflict on table {table}, dropping and retrying")
            drop_table_if_empty(engine, table)
        print(f"exhausted {args.max_rounds} rounds")
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
