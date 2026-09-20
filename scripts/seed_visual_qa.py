"""视觉 QA 播种：给**独立**的 SQLite catalog 灌入可渲染的数据密集态。

## 为什么需要它

`frontend/scripts/visual-quality.mjs` 量的 171 个页面一直跑在空库 + 后端未连接的状态上
（违规明细里直接出现「后端服务未连接，无法读取知识库文档」，而 `data/rag4c.db` 实测
tenants/datasets/documents 均为 0）。于是 rag-tag、表格行、状态徽标、指标卡这些
**最需要无障碍审查的成分从来没有进过视觉评审**——本轮的对比度/触控结论只覆盖壳层与空态。

这个脚本把「有数据的界面」变成可复现的前置条件，而不是靠人肉点几下。

安全边界：
- 必须显式 `--db`，且**拒绝**仓库里的 dev 库与 run-history 生产默认路径；
- 只插入确定性的合成数据（固定 id/名称/时间），不含任何真实内容或密钥；
- 幂等：按固定主键 `INSERT OR REPLACE`，重复跑不会越堆越多。

用法：

    .venv\\Scripts\\python.exe scripts/seed_visual_qa.py --db data/rag4c-visual.db
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
FORBIDDEN = (WORKSPACE / "data" / "rag4c.db", WORKSPACE / "data" / "run-history.sqlite3")

TENANT = "default"
# 由调用方用 --dataset 指定；默认取源注册表里存在的数据集 id
REGISTRY_DATASET = "langchain"
OWNER = "visual-qa-owner"
CREATED = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)

# 状态/类型/进度刻意做成"每种都要有"，因为它们的徽标配色各不相同
STATUSES = ("done", "processing", "pending", "failed", "cancelled")
DOC_TYPES = ("pdf", "markdown", "docx", "xlsx", "html")
SIZES = (12_400, 486_300, 1_970_221, 74_118, 3_402_880)


def _target(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = WORKSPACE / path
    resolved = path.resolve()
    for forbidden in FORBIDDEN:
        if resolved == forbidden.resolve():
            raise SystemExit(f"拒绝写入既有数据库：{resolved}（请用独立路径，例如 data/rag4c-visual.db）")
    return resolved


def _migrate(url: str) -> None:
    from alembic import command
    from alembic.config import Config

    config = Config(str(WORKSPACE / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")


def _rows() -> list[tuple]:
    out = []
    for index in range(45):
        status = STATUSES[index % len(STATUSES)]
        doc_type = DOC_TYPES[index % len(DOC_TYPES)]
        failed = status == "failed"
        processing = status in {"processing", "pending"}
        updated = CREATED + timedelta(hours=index * 7)
        out.append(
            {
                "id": f"vq-doc-{index:03d}",
                "dataset": REGISTRY_DATASET if index % 2 else "vq-ds-primary",
                "name": f"季度运营复盘-{index:02d}.{doc_type}",
                "doc_type": doc_type,
                "status": status,
                "status_detail": (
                    "解析失败：缺少必需章节标记" if failed
                    else "分片写入中（3/5）" if processing
                    else "已完成解析与索引"
                ),
                "progress": 0 if failed else (45 if processing else 100),
                "chunk_count": 0 if failed else 12 + index,
                "error_message": "missing_required_section" if failed else "",
                "size": SIZES[index % len(SIZES)],
                "updated": updated,
            }
        )
    return out


def seed(path: Path) -> int:
    url = f"sqlite:///{path.as_posix()}"
    _migrate(url)
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        now = CREATED.strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT OR REPLACE INTO accounts (id,name,email,created_at) VALUES (?,?,?,?)",
            (OWNER, "Visual QA Owner", "visual-qa@example.invalid", now),
        )
        conn.execute(
            "INSERT OR REPLACE INTO tenants (id,name,plan,status,quota_documents,quota_chunks,"
            "doc_count,chunk_count,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (TENANT, "Visual QA Tenant", "enterprise", "active", 50_000, 5_000_000, 0, 0, now),
        )
        conn.execute(
            "INSERT OR REPLACE INTO tenant_members (id,account_id,tenant_id,role,status,revision,"
            "created_at,updated_at,updated_by) VALUES (?,?,?,?,?,?,?,?,?)",
            (1, OWNER, TENANT, "owner", "active", 1, now, now, OWNER),
        )
        # UI 的数据集下拉读 config/sources.json 声明的注册表（origin="source"），
        # 所以播种必须挂到一个注册表里已存在的 id 上，否则页面选不到、永远空态。
        # 这里不修改用户的 config，而是复用注册表已有的 id 做只读挂载。
        for dataset_id, label in (
            (REGISTRY_DATASET, "视觉 QA（挂载在源数据集上）"),
            ("vq-ds-primary", "产品与运营知识库"),
            ("vq-ds-archive", "历史归档库"),
        ):
            conn.execute(
                "INSERT OR REPLACE INTO datasets (id,tenant_id,name,description,status,profile_revision,"
                "owner_id,visibility,profile_json,parser_policy,chunk_policy,retrieval_policy,"
                "retention_policy,metadata_policy,default_language,graph_enabled,qa_enabled,"
                "mutation_generation,serving_generation,release_revision,acl_mode,acl_revision,"
                "doc_count,chunk_count,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (dataset_id, TENANT, label, "视觉 QA 合成数据", "active", 1, OWNER, "private",
                 "{}", "{}", "{}", "{}", "{}", "{}", "zh-CN", 0, 1, 0, 0, 1, "tenant_role", 1,
                 0, 0, now, now),
            )
        total_chunks = 0
        for index, row in enumerate(_rows()):
            total_chunks += row["chunk_count"]
            columns = [
                "id", "dataset_id", "tenant_id", "name", "file_path", "file_hash", "doc_type",
                "status", "status_detail", "progress", "chunk_count", "error_message",
                "parser_meta", "logical_folder_path", "source_type", "lifecycle_state",
                "retrieval_enabled", "created_at", "updated_at",
            ]
            values = [
                row["id"], row["dataset"], TENANT, row["name"], f"/visual-qa/{row['name']}",
                row["id"] * 2, row["doc_type"], row["status"], row["status_detail"], row["progress"],
                row["chunk_count"], row["error_message"],
                json.dumps({"bytes": row["size"], "pages": max(1, row["size"] // 37_000)}),
                "/运营复盘" if index % 3 else "/产品文档",
                "upload",
                "active",
                1 if row["status"] == "done" else 0,
                now,
                row["updated"].strftime("%Y-%m-%d %H:%M:%S"),
            ]
            assert len(columns) == len(values), (len(columns), len(values))
            conn.execute(
                "INSERT OR REPLACE INTO documents ("
                + ",".join(columns)
                + ") VALUES ("
                + ",".join("?" * len(values))
                + ")",
                values,
            )
            # COALESCE 不可省：空数据集的 SUM() 返回 NULL，会撞 datasets.chunk_count 的 NOT NULL
            conn.execute(
                "UPDATE datasets SET doc_count = "
                "(SELECT COUNT(*) FROM documents WHERE documents.dataset_id = datasets.id), "
                "chunk_count = "
                "(SELECT COALESCE(SUM(chunk_count), 0) FROM documents "
                "WHERE documents.dataset_id = datasets.id)"
            )
        conn.execute(
            "UPDATE tenants SET doc_count = (SELECT COUNT(*) FROM documents), chunk_count = ?",
            (total_chunks,),
        )
        conn.commit()
    finally:
        conn.close()
    # head 完整性校验要求每租户 1 个默认 workspace + 项目默认 release channels，
    # 每个 dataset 1 条 ownership —— 复用仓库既有的幂等播种器，不复制它的 SQL。
    subprocess.run(
        [sys.executable, str(WORKSPACE / "scripts" / "seed_workspace_channel_ownership.py")],
        env={**os.environ, "RAG4C_CATALOG_DB_URL": url},
        check=False,
        capture_output=True,
        text=True,
    )
    return len(_rows())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="目标 SQLite 路径（不得是既有 dev 库）")
    parser.add_argument("--dataset", default=None, help="挂载的源数据集 id（须存在于 config/sources.json）")
    args = parser.parse_args()
    global REGISTRY_DATASET
    if args.dataset:
        REGISTRY_DATASET = args.dataset
    path = _target(args.db)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = seed(path)
    print(f"seeded {path} documents={count} tenant={TENANT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
