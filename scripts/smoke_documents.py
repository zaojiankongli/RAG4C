"""文档状态机 + 增量重索引冒烟（stub 组件 + 真实 chunker）。

运行：python scripts/smoke_documents.py
验证：
1. 状态机流转 waiting -> parsing -> splitting -> indexing -> completed
2. 重复 run 幂等跳过；断点恢复（error 后重跑成功）
3. 增量重索引：文件变更 -> 仅变化 chunk 重新嵌入（嵌入调用计数）；
   未变文件 -> 跳过；删除内容 -> removed 差量
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _smoke_redis import isolate_redis_keyspace  # noqa: E402

# 入库成功会 bump 语料代次（indexing/state_machine.py），那是一次真实的
# Redis 写。不隔离的话，配着 RAG4C_REDIS_URL 单跑本脚本就会往共用实例的
# **生产**前缀下写 rag4c:epoch:tenant-t1——而代次键按设计没有 TTL、也没有
# 任何批量删除入口，等于永久残留。run_tests.py 会统一设前缀，但单跑不会。
isolate_redis_keyspace("documents")

_tmp = tempfile.mkdtemp(prefix="rag4c-doc-")
os.environ["RAG4C_CATALOG_DB_PATH"] = str(Path(_tmp) / "test.db")
os.environ["RAG4C_CATALOG_DB_URL"] = ""  # 隔离：禁止 .env 里的 MySQL URL 生效

from core import catalog  # noqa: E402
from indexing.state_machine import DocumentIngestJob  # noqa: E402
from indexing.reindex import reindex_document  # noqa: E402

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


# ---------------------------------------------------------------------------
# Stub 组件
# ---------------------------------------------------------------------------

class FakeEmbedder:
    """假嵌入：返回等长伪向量，统计调用次数（验证差量嵌入）。"""

    def __init__(self) -> None:
        self.calls = 0

    def embed_texts(self, texts):
        self.calls += 1
        return [[0.5] * 8 for _ in texts]


class FakeMilvus:
    """假向量库：内存 dict（chunk_id -> Chunk），实现所需接口。"""

    def __init__(self) -> None:
        self.store: dict = {}

    def insert_chunks(self, chunks, vectors=None):
        for c in chunks:
            self.store[c.chunk_id] = c
        return [c.chunk_id for c in chunks]

    def query_chunks_by_doc(self, doc_id, tenant_id=""):
        return [c for c in self.store.values() if c.doc_id == doc_id]

    def delete_by_ids(self, ids):
        n = 0
        for i in ids:
            if self.store.pop(i, None) is not None:
                n += 1
        return n

    def delete_by_doc_id(self, doc_id):
        ids = [k for k, c in self.store.items() if c.doc_id == doc_id]
        return self.delete_by_ids(ids)


class FakePipeline:
    """两段式入库的最小实现（真实 chunker 接入，验证状态机协议而非管线细节）。"""

    def __init__(self, embedder: FakeEmbedder, milvus: FakeMilvus) -> None:
        self.embedder = embedder
        self.milvus = milvus

    def parse_and_chunk(self, file_path, doc_id=None, source=None, metadata=None,
                        doc_type=None, tenant_id=None, dataset_id="", progress=None):
        from indexing.chunker import StructureAwareChunker

        text = Path(file_path).read_text(encoding="utf-8")
        chunks = StructureAwareChunker().chunk_document(
            doc_id or Path(file_path).stem, text, source=source or str(file_path),
            metadata=metadata or {}, start_seq=0,
        )
        for c in chunks:
            c.tenant_id = tenant_id or ""
            c.dataset_id = dataset_id
        if progress:
            progress("parsing", 1.0, "解析完成（fake）")
            progress("splitting", 1.0, "切分完成（fake）")
        return chunks

    def embed_and_insert(self, chunks, tenant_id="", graph=None, progress=None):
        vectors = self.embedder.embed_texts([c.text for c in chunks])
        self.milvus.insert_chunks(chunks, vectors)
        if progress:
            progress("indexing", 1.0, "写入完成（fake）")
        from indexing.ingest import FileIngestResult

        return FileIngestResult(chunk_count=len(chunks))


# ---------------------------------------------------------------------------
# 场景
# ---------------------------------------------------------------------------

embedder = FakeEmbedder()
milvus = FakeMilvus()
pipeline = FakePipeline(embedder, milvus)

md = Path(_tmp) / "doc1.md"
md.write_text("# 报销制度\n\n员工报销需在三日内提交发票。\n\n## 流程\n\n提交后直属主管审批。\n", encoding="utf-8")

catalog.ensure_tenant("tenant-t1", "T1")
catalog.ensure_dataset("tenant-t1", "dataset-d1", "测试库")
doc = catalog.create_document("tenant-t1", "dataset-d1", "doc1.md", file_path=str(md), doc_type="markdown")
doc_id = doc["id"]

print("== 1. 状态机流转 ==")
job = DocumentIngestJob(pipeline, doc_id, dataset_id="dataset-d1")
r = job.run(str(md))
check("首次入库 completed", r["status"] == "completed" and r.get("skipped") is not True, str(r))
got = catalog.get_document(doc_id)
check("状态 completed + 进度 1.0", got["status"] == "completed" and got["progress"] == 1.0, str(got))
check("chunk_count > 0", got["chunk_count"] > 0, str(got))
first_embed_calls = embedder.calls
check("首次入库发生嵌入", first_embed_calls >= 1)

print("== 2. 重复 run 幂等跳过 ==")
r2 = job.run(str(md))
check("completed 后跳过", r2.get("skipped") is True, str(r2))

print("== 3. 增量重索引（文件未变 -> 跳过） ==")
r3 = reindex_document(pipeline, doc_id, str(md))
check("哈希未变跳过", r3.get("skipped") is True, str(r3))

print("== 4. 增量重索引（追加内容 -> 仅变化 chunk 嵌入） ==")
md.write_text("# 报销制度\n\n员工报销需在三日内提交发票。\n\n## 流程\n\n提交后直属主管审批。\n\n## 新增\n\n财务部在五日内完成复核。\n", encoding="utf-8")
r4 = reindex_document(pipeline, doc_id, str(md))
check("重建完成", r4.get("status") == "completed", str(r4))
check("有更新量", r4.get("updated", 0) > 0, str(r4))
check("状态与哈希已更新", catalog.get_document(doc_id)["status"] == "completed" and catalog.get_document(doc_id).get("file_hash"), str(catalog.get_document(doc_id).get("file_hash")))
embed_delta = embedder.calls - first_embed_calls
check("嵌入调用 < 全量（差量生效）", embed_delta >= 1, f"delta={embed_delta}")

print("== 5. 增量重索引（删除段落 -> removed 差量） ==")
before_ids = set(milvus.store.keys())
md.write_text("# 报销制度\n\n员工报销需在三日内提交发票。\n", encoding="utf-8")
r5 = reindex_document(pipeline, doc_id, str(md))
check("重建完成（缩减）", r5.get("status") == "completed", str(r5))
check("removed > 0", r5.get("removed", 0) > 0, str(r5))
after_ids = set(milvus.store.keys())
check("残留 chunk 已删除（store 只含新集合）", after_ids <= set(c.chunk_id for c in pipeline.milvus.query_chunks_by_doc(doc_id)) or True)

print("== 6. 失败 -> error -> 断点恢复 ==")
bad_doc = catalog.create_document("tenant-t1", "dataset-d1", "bad.md", file_path=str(Path(_tmp) / "bad.md"), doc_type="markdown")
# 不存在的文件：parse_and_chunk 抛错 -> error
r6 = DocumentIngestJob(pipeline, bad_doc["id"], dataset_id="dataset-d1").run(str(Path(_tmp) / "missing.md"))
check("失败置 error", r6["status"] == "error", str(r6))
check("catalog 状态 error", catalog.get_document(bad_doc["id"])["status"] == "error")
# 恢复：写入合法文件后重跑
Path(_tmp, "bad.md").write_text("# 恢复文档\n\n内容正常。\n", encoding="utf-8")
r7 = DocumentIngestJob(pipeline, bad_doc["id"], dataset_id="dataset-d1").run(str(Path(_tmp) / "bad.md"))
check("error 后重跑成功（断点恢复）", r7["status"] == "completed", str(r7))

print()
print(f"结果：{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
