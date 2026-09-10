"""一次性去除「冻结当前 head 字面量」的断言（drift tax）。

背景：仓库里 26 处断言写死了「此刻的 alembic head 字符串」。每新增一条迁移，
这些断言就集体误挂——本次 stage27 之前已有 9 个文件因此转红。

这些断言分两类，都没有独立信息量：
  1) `assert catalog_schema.HEAD_REVISION == "0029_..."`：紧邻的上一行已经断言
     `scripts.get_current_head() == catalog_schema.HEAD_REVISION`（真·链一致性）。
  2) `assert api.HEAD_REVISION == "0036_..."`：api 就是从 core.catalog_schema
     导入 HEAD_REVISION 的，断言恒真。

真·不变量（链头 == 单一事实源）保留在迁移测试的上一行，一处即可，无需 26 处。

用法：python scripts/drop_frozen_head_asserts.py [--apply]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 精确匹配「整行就是一个冻结 head 字面量断言」的行
LINE_PATTERNS = [
    re.compile(r'^\s*assert\s+\w+\.HEAD_REVISION\s*==\s*"0036_enterprise_knowledge_serving_reliability"\s*$'),
    re.compile(r'^\s*assert\s+catalog_schema\.HEAD_REVISION\s*==\s*"0029_enterprise_knowledge_base_releases"\s*$'),
    re.compile(r'^\s*assert\s+catalog_schema\.HEAD_REVISION\s*==\s*"0022_scim_provisioning_data_plane"\s*$'),
    # 0034 已被 0035/0036 超越，"它是 head" 不再成立；readiness 契约由 capability 列表承担
    re.compile(r'^\s*assert\s+api\.HEAD_REVISION\s*==\s*api\.ENTERPRISE_TASK_OPERATIONS_REVISION\s*$'),
    re.compile(r'^\s*assert\s+api\.HEAD_REVISION\s*==\s*manifest\.ENTERPRISE_TASK_OPERATIONS_REVISION\s*$'),
]

# 上一轮遗留的半成品编辑：引用了 catalog_schema 但没导入
MISSING_IMPORT = "from core import catalog_schema\n"
IMPORT_FILES = [
    "tests/test_enterprise_notification_center_migration.py",
    "tests/test_enterprise_release_quality_migration.py",
    "tests/test_enterprise_release_quality_operations_migration.py",
    "tests/test_enterprise_task_operations_migration.py",
]


def strip_lines(path: Path) -> int:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [ln for ln in lines if not any(p.match(ln.rstrip("\n")) for p in LINE_PATTERNS)]
    removed = len(lines) - len(kept)
    if removed:
        path.write_text("".join(kept), encoding="utf-8")
    return removed


def add_import(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if MISSING_IMPORT in text:
        return False
    anchor = "from sqlalchemy import"
    idx = text.index(anchor)
    end = text.index("\n", idx) + 1
    path.write_text(text[:end] + MISSING_IMPORT + text[end:], encoding="utf-8")
    return True


def main() -> int:
    apply = "--apply" in sys.argv
    if not apply:
        print("dry-run（加 --apply 才写入）")
    total = 0
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        hits = [
            (i + 1, ln.strip())
            for i, ln in enumerate(lines)
            if any(p.match(ln.rstrip("\n")) for p in LINE_PATTERNS)
        ]
        if hits:
            total += len(hits)
            print(f"{path.relative_to(ROOT)}: 删 {len(hits)} 行")
            for n, ln in hits:
                print(f"    {n}: {ln}")
            if apply:
                strip_lines(path)
    print(f"\n合计待删 {total} 行")
    if apply:
        for rel in IMPORT_FILES:
            if add_import(ROOT / rel):
                print(f"补导入: {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
