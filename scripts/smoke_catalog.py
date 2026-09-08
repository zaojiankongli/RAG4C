"""多租户数据层 + 元数据校验器冒烟。

运行：python scripts/smoke_catalog.py
覆盖：
1. ORM 建表与关系 CRUD（Account/Tenant/Dataset/Document/MetadataField）
2. 文档状态机迁移校验（合法 / 非法路径）
3. 配额检查
4. 过滤表达式校验器：合法表达式 / 注入攻击拒绝 / 类型不匹配拒绝
"""
from __future__ import annotations

import sys
import tempfile
import subprocess
import time
from pathlib import Path

# ruff: noqa: E402

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# 独立临时数据库，避免污染真实 catalog
_tmp = tempfile.mkdtemp(prefix="rag4c-cat-")
import os  # noqa: E402

os.environ["RAG4C_CATALOG_DB_PATH"] = str(Path(_tmp) / "test.db")
os.environ["RAG4C_CATALOG_DB_URL"] = ""  # 隔离：禁止 .env 里的 MySQL URL 生效

from core import catalog  # noqa: E402
from core.metadata import (  # noqa: E402
    FilterValidationError,
    filters_from_dict,
    schema_to_prompt_text,
    validate_filter_expr,
)
from models.orm import valid_transition  # noqa: E402

catalog.reset_engine()
passed = 0
failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {detail}")


print("== 1. 租户 / 数据集 / 文档 CRUD ==")
t = catalog.ensure_tenant("tenant-acme", "ACME 公司", "pro")
check("ensure_tenant 幂等创建", t["id"] == "tenant-acme" and t["plan"] == "pro", str(t))
d = catalog.ensure_dataset("tenant-acme", "dataset-fin", "财务知识库")
check("ensure_dataset 幂等创建", d["id"] == "dataset-fin", str(d))
doc = catalog.create_document(
    "tenant-acme", "dataset-fin", "报销制度.pdf", file_hash="abc123", doc_type="pdf"
)
check("create_document 状态 waiting", doc["status"] == "waiting", str(doc))
check("list_documents 含新文档", any(x["id"] == doc["id"] for x in catalog.list_documents("dataset-fin")))

auto_doc = catalog.create_document(
    "tenant-auto", "dataset-auto", "自动父级.txt", doc_type="txt"
)
check("create_document 自动创建父级", catalog.get_document(auto_doc["id"]) is not None)
check(
    "自动父级计数原子更新",
    catalog.ensure_dataset("tenant-auto", "dataset-auto")["doc_count"] == 1,
)
try:
    catalog.create_document("tenant-other", "dataset-auto", "越权.txt")
    check("dataset 禁止跨租户写入", False)
except ValueError:
    check("dataset 禁止跨租户写入", True)

print("== 1b. 多进程首次创建与计数竞争 ==")
worker_code = r"""
import os, sys, time
from pathlib import Path
root, db_path, start_at, index = sys.argv[1:]
sys.path.insert(0, root)
os.environ['RAG4C_CATALOG_DB_PATH'] = db_path
os.environ['RAG4C_CATALOG_DB_URL'] = ''
from core import catalog
while time.time() < float(start_at):
    time.sleep(0.002)
catalog.create_document(
    'tenant-race', 'dataset-race', f'race-{index}.txt',
    doc_id=f'doc-race-{index}', doc_type='txt'
)
"""
process_count = 6
start_at = str(time.time() + 0.5)
processes = [
    subprocess.Popen(
        [
            sys.executable,
            "-c",
            worker_code,
            str(_PROJECT_ROOT),
            str(Path(_tmp) / "test.db"),
            start_at,
            str(index),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    for index in range(process_count)
]
worker_errors: list[str] = []
for process in processes:
    stdout, stderr = process.communicate(timeout=30)
    if process.returncode != 0:
        worker_errors.append((stdout + stderr)[-1000:])
race_dataset = catalog.ensure_dataset("tenant-race", "dataset-race")
check("多进程首次创建无唯一键/锁冲突", not worker_errors, " | ".join(worker_errors))
check(
    "多进程文档计数无丢更新",
    race_dataset["doc_count"] == process_count,
    str(race_dataset),
)
check(
    "多进程文档全部落库",
    len(catalog.list_documents("dataset-race")) == process_count,
)

print("== 1c. 多进程文档配额原子占用 ==")
from sqlalchemy import update  # noqa: E402
from models.orm import Tenant  # noqa: E402

with catalog._session() as session:
    session.execute(
        update(Tenant)
        .where(Tenant.id == "tenant-quota")
        .values(quota_documents=3)
    )
    session.commit()

# Ensure the tenant exists before setting its quota.
catalog.ensure_tenant("tenant-quota")
with catalog._session() as session:
    session.execute(
        update(Tenant)
        .where(Tenant.id == "tenant-quota")
        .values(quota_documents=3)
    )
    session.commit()

quota_start = str(time.time() + 0.5)
quota_workers = 6
quota_processes = [
    subprocess.Popen(
        [
            sys.executable,
            "-c",
            worker_code.replace("tenant-race", "tenant-quota").replace(
                "dataset-race", "dataset-quota"
            ),
            str(_PROJECT_ROOT),
            str(Path(_tmp) / "test.db"),
            quota_start,
            f"quota-{index}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    for index in range(quota_workers)
]
quota_success = 0
quota_failures: list[str] = []
for process in quota_processes:
    stdout, stderr = process.communicate(timeout=30)
    if process.returncode == 0:
        quota_success += 1
    else:
        quota_failures.append(stdout + stderr)
quota_dataset = catalog.ensure_dataset("tenant-quota", "dataset-quota")
check("并发配额只允许上限内写入", quota_success == 3, str(quota_success))
check("配额拒绝是预期业务异常", all("CatalogQuotaError" in e for e in quota_failures))
check("配额下计数精确", quota_dataset["doc_count"] == 3, str(quota_dataset))

print("== 2. 文档状态机迁移 ==")
check("waiting->parsing 合法", valid_transition("waiting", "parsing"))
check("completed->indexing 非法", not valid_transition("completed", "indexing"))
check("error->parsing 合法（retry）", valid_transition("error", "parsing"))
catalog.set_document_status(doc["id"], "parsing", "解析中", progress=0.3)
check("状态更新 parsing + 进度 0.3", catalog.get_document(doc["id"])["status"] == "parsing" and abs(catalog.get_document(doc["id"])["progress"] - 0.3) < 1e-6)
try:
    catalog.set_document_status(doc["id"], "completed")
    check("parsing->completed 非法被拒", False, "应抛 ValueError")
except ValueError:
    check("parsing->completed 非法被拒", True)
catalog.set_document_status(doc["id"], "splitting", progress=0.6)
# 分段表仍可写（upsert_segment 未变），但 get_document 不再统计它——
# 生产入库路径从不写该表，却让每次轮询都触发一次关联表懒加载。
# 细粒度信息改由 parser_meta 承载，这里改为校验该字段。
catalog.upsert_segment(doc["id"], 0, "processing", 0.5)
catalog.upsert_segment(doc["id"], 1, "indexed", 1.0, chunk_count=3)
catalog.upsert_segment(doc["id"], 1, "indexed", 1.0, chunk_count=3)  # 幂等
got = catalog.get_document(doc["id"])
check("get_document 不再返回 segments（避免无谓的懒加载）", "segments" not in got, str(got.keys()))
check("parser_meta 默认为空字典", got.get("parser_meta") == {}, str(got.get("parser_meta")))

catalog.set_document_status(
    doc["id"], "splitting",
    parser_meta={"engine": "fast", "page_count": 12, "chunking_mode": "parent_child"},
)
got = catalog.get_document(doc["id"])
check(
    "parser_meta 可写入并回读",
    got["parser_meta"].get("engine") == "fast" and got["parser_meta"].get("page_count") == 12,
    str(got.get("parser_meta")),
)

# error_message 落库前必须先脱敏：页面直接把它显示给用户，绝对路径 = 把服务端
# 目录结构摊给所有能打开该页的人，doc_id 又和表格里的独立列重复。
catalog.set_document_status(
    doc["id"], "error",
    error_message=r"解析器不支持该文件类型: 'D:\\srv\\rag\\data\\test_kb.md'（doc_id='doc-abc'）",
)
emsg = catalog.get_document(doc["id"])["error_message"]
check("error_message 脱敏：绝对路径压成文件名", "srv" not in emsg and "test_kb.md" in emsg, emsg)
check("error_message 脱敏：去掉重复的 doc_id 尾巴", "doc_id" not in emsg, emsg)
check("error_message 脱敏：不留 repr 的双反斜杠", "\\\\" not in emsg, emsg)
catalog.set_document_status(doc["id"], "parsing", error_message="Milvus 连接超时（重试 3 次后失败）")
check(
    "error_message 脱敏：不含路径的文案原样保留",
    catalog.get_document(doc["id"])["error_message"] == "Milvus 连接超时（重试 3 次后失败）",
    catalog.get_document(doc["id"])["error_message"],
)

check(
    "list_documents 带上 status_detail（前端轮询靠它显示当前子阶段）",
    all("status_detail" in x for x in catalog.list_documents("dataset-fin")),
    str(catalog.list_documents("dataset-fin")[:1]),
)

print("== 3. 配额检查 ==")
ok, reason = catalog.check_quota("tenant-acme", add_chunks=100_001)
check("chunk 配额超限被拒", not ok and "chunk" in reason, reason)
# 文档数配额不走 check_quota——它由 create_document 的条件 UPDATE 原子占用，
# 上面第 2 节的多进程用例已经压过了。

print("== 4. 元数据 schema 注册 ==")
catalog.declare_metadata_field("dataset-fin", "department", "string", "manual", "部门")
catalog.declare_metadata_field("dataset-fin", "year", "number", "manual", "年份")
fields = catalog.list_metadata_fields("dataset-fin")
check("schema 注册 2 字段", len(fields) == 2, str(fields))

print("== 5. 过滤表达式校验器 ==")
valid_cases = [
    ("doc_type == 'pdf'", "doc_type == 'pdf'"),
    ("doc_type in ['pdf','docx'] && year >= 2020", None),
    ("(department == 'fin' || department == 'legal') && !(year < 2015)", None),
    ("chunk_level == 'child'", None),
]
for expr, expect in valid_cases:
    try:
        out = validate_filter_expr(expr, fields)
        check(f"合法表达式: {expr[:40]}", expect is None or out == expect, out)
    except FilterValidationError as e:
        check(f"合法表达式: {expr[:40]}", False, str(e))

attack_cases = [
    "doc_type == 'pdf' && text like '%secret%'",       # like 不支持
    "1 == 1",                                            # 非键开头
    "doc_type == 'pdf'; drop",                           # 分号注入
    "department == 'fin' || unknown_key == 'x'",         # 白名单外键
    "year == '2024'",                                    # 类型不匹配（number 给 string）
    "doc_type == 'pdf' && (year = 2020)",                # 单等号
    "password == 'abc'",                                 # 敏感猜测键
    "doc_type == 'pdf' && 'injected' == 'injected'",     # 值位置放表达式
]
for expr in attack_cases:
    try:
        validate_filter_expr(expr, fields)
        check(f"注入被拒: {expr[:50]}", False, "竟然通过了！")
    except FilterValidationError:
        check(f"注入被拒: {expr[:50]}", True)

print("== 6. filters_from_dict（manual 通道） ==")
expr = filters_from_dict({"doc_type": "pdf", "year": 2024, "department": ["fin", "legal"]}, fields)
check("dict -> 表达式", expr is not None and "department in" in expr, str(expr))

print("== 7. schema 提示文本（automatic 通道注入） ==")
prompt = schema_to_prompt_text(fields)
check("提示含系统键", "tenant_id" in prompt and "dataset_id" in prompt)
check("提示含用户键", "department" in prompt and "year" in prompt)

print()
print(f"结果：{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
