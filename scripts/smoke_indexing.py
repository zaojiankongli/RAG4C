"""RAG4C 索引模块离线冒烟测试。

覆盖内容：
- :mod:`indexing.hashing` 的 sha256 摘要正确性；
- :class:`~indexing.chunker.StructureAwareChunker` 的中文 Markdown 结构切分：
  长章节拆分为父块 + 子块、表格块与代码围栏整体保留、短章节合并、
  chunk_id 确定性、父块包含标题上下文、引用完整性；
- :class:`~indexing.ingest.IngestPipeline` 配合 FakeEmbedder（固定 1024 维
  哑向量）与 FakeMilvus（内存字典实现，不导入 pymilvus）的入库 /
  删除 / upsert 流程，以及 trace span 记录与 IngestError 包装；
- :meth:`~indexing.ingest.IngestPipeline.add_file` 完整文件入库链：
  FakeParser -> Cleaner -> MarkdownStrategy -> chunker -> embed -> insert，
  图索引开关（stub GraphBuilder）、解析器元数据合并、缺省 parser 时的
  文本直读与 IngestError 兜底。

全程不联网、不下载模型、不启动 Milvus。

运行：
    python scripts/smoke_indexing.py
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

# 保证从任意工作目录运行都能找到 config / models / core / indexing
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Windows 控制台默认 GBK，统一按 UTF-8 输出避免中文乱码
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from core.tracing import trace_session
from indexing.chunker import StructureAwareChunker
from indexing.cleaner import Cleaner
from indexing.graph_builder import GraphBuildResult
from indexing.hashing import text_hash
from indexing.ingest import IngestError, IngestPipeline
from indexing.parsers.base import DocumentParser, ParsedDocument
from indexing.strategies import MarkdownStrategy


# ---------------------------------------------------------------------------
# 测试样本：中文 Markdown
# ---------------------------------------------------------------------------

LONG_SENTENCE = (
    "企业知识库中的长文内容必须被合理切分，以便混合检索能够高效命中相关片段，"
    "父块子块两级结构同时保证上下文完整性与检索粒度，"
    "检索命中子块后可以回取父块补充完整上下文。"
)
# 单句约 70 字符，重复 40 次约 2800 字符，确保远超 max_chunk_chars(1500)
LONG_BODY = LONG_SENTENCE * 40

SAMPLE_DOC = """# 产品总览

RAG4C 是一套面向企业级场景的检索增强生成系统，核心设计目标包括模块化、可观测性与离线可测试性。模块边界清晰，配置、模型、核心封装与索引管线各自独立，任何模块都可以在没有外部服务的情况下完成单元测试。
系统采用 BGE-M3 稠密嵌入与 Milvus 内置 BM25 函数执行混合检索，并通过父块子块两级切分实现小到大检索，同时为后续的重排序、生成与裁判环节保留清晰的输入输出契约。检索命中子块后可回取父块，保证生成环节拥有完整上下文。

## 架构原则

RAG4C 的架构遵循以下原则：所有外部依赖均为可选依赖，模块导入不触发任何网络请求；配置系统基于 pydantic-settings 实现，支持环境变量覆盖；数据模型作为全系统共享契约，任何模块不得绕过模型直接操作底层存储。
此外，核心模块均提供惰性加载机制，缺少可选依赖时仍可安全导入并完成离线单测，保证企业私有化部署环境下的可交付性。追踪基础设施基于 contextvars 实现，为未来接入 OpenTelemetry 与 Langfuse 预留了平滑替换路径。

## 数据表

| 组件 | 职责 | 依赖 | 说明 |
| ---- | ---- | ---- | ---- |
| config | 配置加载与环境变量映射 | pydantic-settings | RAG4C_ 前缀分层配置 |
| models | 全系统数据契约定义 | pydantic | Chunk 与检索结果模型 |
| core | Milvus / 嵌入 / 追踪封装 | 可选依赖 | 惰性导入，离线可测 |
| indexing | 切分与入库管线 | 本模块 | 父块子块两级结构 |

## 代码示例

```python
def greet(name: str) -> str:
    # 返回问候语
    return f"你好, {name}"

def add(a: int, b: int) -> int:
    # 返回两数之和
    return a + b

def main() -> None:
    # 程序入口
    print(greet("RAG4C"), add(1, 2))
```

## 微小节

这一节非常短，将被合并到前一章节。

## 长文节

""" + LONG_BODY + "\n"


# ---------------------------------------------------------------------------
# Fake 依赖（无 pymilvus / 无网络）
# ---------------------------------------------------------------------------

class FakeEmbedder:
    """固定 1024 维哑向量嵌入器。"""

    dim = 1024

    def __init__(self) -> None:
        self.embed_texts_calls = 0

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.embed_texts_calls += 1
        return [[float(i % 7 + 1) * 0.01 for _ in range(self.dim)] for i in range(len(texts))]

    def embed_query(self, text: str) -> list[float]:
        return [0.0] * self.dim


class FakeMilvus:
    """内存版 Milvus 替身：实现与 RagMilvusClient 相同签名，记录调用。"""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.calls: list = []

    def ensure_collection(self) -> None:
        self.calls.append("ensure_collection")

    def _write(self, chunks, dense_vectors) -> list[str]:
        ids: list[str] = []
        for chunk, vector in zip(chunks, dense_vectors or []):
            self.rows[chunk.chunk_id] = {"chunk": chunk, "vector": list(vector)}
            ids.append(chunk.chunk_id)
        return ids

    def insert_chunks(self, chunks, dense_vectors=None) -> list[str]:
        self.calls.append(("insert_chunks", len(chunks)))
        return self._write(chunks, dense_vectors)

    def upsert_chunks(self, chunks, dense_vectors=None) -> list[str]:
        self.calls.append(("upsert_chunks", len(chunks)))
        return self._write(chunks, dense_vectors)

    def delete_by_doc_id(self, doc_id: str) -> int:
        hit = [k for k, r in self.rows.items() if r["chunk"].doc_id == doc_id]
        for k in hit:
            del self.rows[k]
        self.calls.append(("delete_by_doc_id", doc_id, len(hit)))
        return len(hit)

    def count(self, doc_id: str | None = None) -> int:
        if doc_id is None:
            return len(self.rows)
        return sum(1 for r in self.rows.values() if r["chunk"].doc_id == doc_id)


class BoomEmbedder:
    """总是失败的嵌入器，用于验证 IngestError 包装。"""

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("boom")

    def embed_query(self, text: str) -> list[float]:
        raise RuntimeError("boom")


class FakeParser(DocumentParser):
    """确定性文件解析器：按扩展名返回不同文本，供 add_file 链路测试。"""

    def __init__(self, supported: set[str], text: str) -> None:
        self.supported = supported
        self.text = text
        self.parse_calls: list[str] = []

    def parse(self, file_path: str) -> ParsedDocument:
        self.parse_calls.append(file_path)
        return ParsedDocument(
            text=self.text,
            metadata={"parser": "fake", "parse_mode": "offline"},
        )

    def supports(self, file_path: str) -> bool:
        return Path(file_path).suffix.lower() in self.supported


class StubGraphBuilder:
    """图索引桩：记录 build 入参，返回确定性统计，不触碰任何存储。"""

    def __init__(self) -> None:
        self.build_calls: list[list[tuple[str, str]]] = []
        self.tenant_calls: list[str] = []
        self.progress_calls: list[tuple[int, int]] = []

    def build(
        self,
        chunks: list[tuple[str, str]],
        tenant_id: str = "",
        progress: Any = None,
    ) -> GraphBuildResult:
        self.build_calls.append(list(chunks))
        self.tenant_calls.append(tenant_id or "")
        # 与真实 GraphBuilder 一致：抽取阶段按完成数上报进度
        if progress is not None:
            for done in range(1, len(chunks) + 1):
                progress(done, len(chunks))
                self.progress_calls.append((done, len(chunks)))
        return GraphBuildResult(
            chunk_count=len(chunks),
            triplet_count=2,
            entity_count=3,
            relation_count=2,
            failed_chunk_count=0,
        )


# ---------------------------------------------------------------------------
# (a) 切分器离线测试
# ---------------------------------------------------------------------------

def test_chunker() -> int:
    chunker = StructureAwareChunker()
    chunks = chunker.chunk_document(
        "doc-a", SAMPLE_DOC, source="smoke.md", metadata={"acl": "smoke"}
    )
    assert chunks, "chunk 列表不能为空"
    assert all(c.doc_id == "doc-a" for c in chunks)
    assert all(c.source == "smoke.md" for c in chunks)
    print(f"[1/6] chunker ok  共 {len(chunks)} 个 chunk")

    # 确定性：两次运行 chunk_id 与文本完全一致
    chunks2 = chunker.chunk_document(
        "doc-a", SAMPLE_DOC, source="smoke.md", metadata={"acl": "smoke"}
    )
    assert [c.chunk_id for c in chunks2] == [c.chunk_id for c in chunks]
    assert [c.text for c in chunks2] == [c.text for c in chunks]
    print("[2/6] 确定性 ok  chunk_id 两次运行一致")

    # text_hash 正确性
    for c in chunks:
        expected = hashlib.sha256(c.text.encode("utf-8")).hexdigest()
        assert c.text_hash == expected, f"chunk {c.chunk_id} text_hash 不正确"
    assert text_hash("hello") == hashlib.sha256(b"hello").hexdigest()
    assert text_hash("") == hashlib.sha256(b"").hexdigest()
    assert len(text_hash("abc")) == 64
    print("[3/6] text_hash ok  sha256 校验全部通过")

    # 父块子块结构
    ids = {c.chunk_id for c in chunks}
    parents = [c for c in chunks if c.parent_chunk_id is None]
    children = [c for c in chunks if c.parent_chunk_id is not None]
    assert len(parents) >= 4, "独立章节块数量异常"
    assert len(children) >= 2, "长章节应拆出至少 2 个子块"
    assert all(c.metadata.get("is_parent") is True for c in parents)
    assert all(c.metadata.get("is_parent") is False for c in children)
    for c in children:
        assert c.parent_chunk_id in ids, "parent_chunk_id 引用了不存在的 chunk"
    assert sorted(c.metadata["chunk_index"] for c in chunks) == list(range(len(chunks)))

    # 长章节：父块包含标题上下文，且长度不超过 parent_max_chars
    long_parents = [
        c
        for c in parents
        if "长文节" in c.text and len(c.text) > chunker.max_chunk_chars
    ]
    assert long_parents, "未找到包含标题上下文的长章节父块"
    long_parent = long_parents[0]
    assert len(long_parent.text) <= chunker.parent_max_chars
    child_of_long = [c for c in children if c.parent_chunk_id == long_parent.chunk_id]
    assert len(child_of_long) >= 2
    for c in child_of_long:
        assert len(c.text) <= chunker.max_chunk_chars + chunker.overlap_chars
    # 子块内容覆盖长章节正文（重叠拼接后应包含原文）
    assert "企业知识库" in child_of_long[0].text

    # 表格块整体保留
    table_chunks = [c for c in chunks if "|" in c.text and "数据表" in c.text]
    assert table_chunks, "未找到包含表格的章节"
    assert "| 组件 | 职责 | 依赖 |" in table_chunks[0].text

    # 代码围栏整体保留
    fence_chunks = [c for c in chunks if "```python" in c.text and "def greet" in c.text]
    assert fence_chunks, "代码围栏未整体保留"
    assert "```python" in fence_chunks[0].text and "```" in fence_chunks[0].text.split("```python", 1)[1]

    # 短章节合并进前一章节（微小节并入代码示例章节）
    merged_chunks = [c for c in chunks if "微小节" in c.text]
    assert merged_chunks, "微小节应被合并而非独立成块"
    assert any("```python" in c.text and "微小节" in c.text for c in chunks), "微小节未被合并到前一章节"
    print(f"[4/6] 结构切分 ok  独立块={len(parents)} 子块={len(children)}")

    # 元数据与字段完整性
    for c in chunks:
        json.dumps(c.metadata, ensure_ascii=False, allow_nan=False)
        assert c.metadata.get("acl") == "smoke"
        assert isinstance(c.created_at, datetime) and c.created_at.tzinfo is not None
        assert c.created_at == c.updated_at
    print("[5/6] 元数据 ok  JSON 可序列化，时间戳为 UTC")
    return len(chunks)


# ---------------------------------------------------------------------------
# (b) 入库管线离线测试
# ---------------------------------------------------------------------------

def test_ingest(expected_count: int) -> None:
    fake = FakeMilvus()
    embedder = FakeEmbedder()
    pipeline = IngestPipeline(embedder=embedder, milvus=fake)

    pipeline.ensure_collection()
    assert "ensure_collection" in fake.calls

    with trace_session("idx-smoke") as trace:
        n = pipeline.add_document(
            "doc-b", SAMPLE_DOC, source="smoke.md", metadata={"acl": "smoke"}
        )
    assert n == expected_count, "add_document 返回数应与 chunker 结果一致"
    assert fake.count("doc-b") == n
    assert embedder.embed_texts_calls == 1
    assert any(name == "embed" for name, _ in trace.spans), "缺少 embed span"
    assert any(name == "insert" for name, _ in trace.spans), "缺少 insert span"
    for row in fake.rows.values():
        assert len(row["vector"]) == 1024, "稠密向量维度应为 1024"
        assert row["chunk"].doc_id == "doc-b"
    print(f"[6/6] add_document ok  写入 {n} 行，向量维度 1024，trace span 已记录")

    # 删除
    deleted = pipeline.delete_document("doc-b")
    assert deleted == n
    assert fake.count("doc-b") == 0
    assert fake.count() == 0

    # upsert：先删旧行再重建。写入走 upsert_chunks 而不是 insert_chunks——
    # chunk_id 是 doc_id+序号+正文哈希算出来的确定性主键，配上 auto_id=False，
    # 重试同一篇文档才会覆盖旧行而不是再插一份。这条断言从前钉的是
    # insert_chunks，等于把"重试即翻倍"这个缺陷当成契约保护了起来。
    start = len(fake.calls)
    n2 = pipeline.upsert_document("doc-c", SAMPLE_DOC, source="smoke.md")
    upsert_calls = fake.calls[start:]
    assert upsert_calls[0][0] == "delete_by_doc_id"
    assert any(c[0] == "upsert_chunks" for c in upsert_calls), upsert_calls
    assert not any(c[0] == "insert_chunks" for c in upsert_calls), upsert_calls
    assert fake.count("doc-c") == n2 and fake.count() == n2
    pipeline.delete_document("doc-c")
    assert fake.count() == 0

    # 空文档不产生任何写入
    assert pipeline.add_document("doc-d", "") == 0
    assert fake.count("doc-d") == 0

    # 嵌入失败包装为 IngestError
    try:
        IngestPipeline(embedder=BoomEmbedder(), milvus=FakeMilvus()).add_document(
            "doc-e", "一些需要切分的普通文本内容。"
        )
        raise AssertionError("应当抛出 IngestError")
    except IngestError:
        pass
    assert issubclass(IngestError, RuntimeError)


# ---------------------------------------------------------------------------
# (c) add_file 完整文件入库链离线测试
# ---------------------------------------------------------------------------

FILE_MD = """# 产品手册

本文档描述产品的安装与配置流程。

## 安装步骤

第一步：下载安装包。第二步：运行安装向导。

## 配置说明

配置项 A 控制日志级别，配置项 B 控制缓存大小。
"""


def test_add_file() -> None:
    fake = FakeMilvus()
    embedder = FakeEmbedder()
    graph_stub = StubGraphBuilder()
    parser = FakeParser(
        supported={".pdf"}, text=FILE_MD + "\n\n---\n\n第 1 页\n"
    )
    pipeline = IngestPipeline(
        embedder=embedder,
        milvus=fake,
        parser=parser,
        cleaner=Cleaner(),
        strategy=MarkdownStrategy(),
        graph_builder=graph_stub,
        graph_enabled=True,
    )

    with tempfile.TemporaryDirectory() as tmp:
        file_path = str(Path(tmp) / "manual.md")
        Path(file_path).write_text(FILE_MD, encoding="utf-8")

        # 1) parser 未配置时文本类文件直接 UTF-8 读取
        plain = IngestPipeline(embedder=embedder, milvus=fake)
        with trace_session("idx-file-plain") as trace:
            r_plain = plain.add_file(file_path, doc_id="plain")
        assert r_plain.chunk_count > 0
        assert fake.count("plain") == r_plain.chunk_count
        assert r_plain.graph is None, "未配置 graph_builder 时不得建图"
        assert any(name == "parse" for name, _ in trace.spans)
        assert any(name == "chunk" for name, _ in trace.spans)
        plain.delete_document("plain")
        print(f"[7/9] add_file(无parser) ok  UTF-8 直读 {r_plain.chunk_count} chunk")

        # 2) parser 不支持的类型 -> IngestError
        try:
            plain.add_file(str(Path(tmp) / "nope.pdf"))
            raise AssertionError("应当抛出 IngestError")
        except IngestError:
            pass

        # 2b) 配了 parser、但 parser 不认这个后缀的**纯文本** -> 仍应直读
        #
        # 这条补的是一个真出过的洞：原先纯文本直读只在 parser is None 时可达，
        # 于是给 PDF 配上 MinerU 之后，最普通的 .md 反而入不了库。上面 1) 只
        # 覆盖了"没配 parser"，正好绕开了这个分支——所以 bug 一路活到了页面上。
        md_only = str(Path(tmp) / "plain_with_parser.md")
        Path(md_only).write_text(FILE_MD, encoding="utf-8")
        before = len(parser.parse_calls)
        r_fallback = pipeline.add_file(md_only, doc_id="doc-md-fallback")
        assert r_fallback.chunk_count > 0, ".md 应经直读正常入库"
        assert len(parser.parse_calls) == before, "不支持的类型不得进 parser.parse"
        pipeline.delete_document("doc-md-fallback")
        # 复位图桩：下面 3) 要断言"graph 应构建一次"，这次回退入库也会记一笔
        graph_stub.build_calls.clear()
        graph_stub.tenant_calls.clear()
        graph_stub.progress_calls.clear()
        print("[7b/9] add_file(有parser但不支持.md) ok  回退直读")

        # 3) 完整链路：parser -> cleaner -> strategy -> chunk -> embed -> insert
        with trace_session("idx-file-full") as trace:
            r = pipeline.add_file(
                str(Path(tmp) / "manual.pdf"),
                doc_id="doc-f",
                source="manual.pdf",
                metadata={"acl": "smoke"},
                doc_type="markdown",
            )
        assert r.chunk_count > 0
        assert fake.count("doc-f") == r.chunk_count
        assert r.graph is not None and r.graph.chunk_count == r.chunk_count
        assert len(graph_stub.build_calls) == 1, "graph 应构建一次"
        assert len(graph_stub.build_calls[0]) == r.chunk_count
        # graph 入参 chunk_id 与写入行一一对应
        assert {cid for cid, _ in graph_stub.build_calls[0]} == set(fake.rows)
        # 清洗生效：样板页码行（第 1 页）与水平线不进入任何 chunk
        assert not any("第 1 页" in row["chunk"].text for row in fake.rows.values()), (
            "cleaner 应移除页码样板行"
        )
        # 解析器元数据合并进 chunk 元数据
        for row in fake.rows.values():
            assert row["chunk"].metadata.get("parser") == "fake"
            assert row["chunk"].metadata.get("parse_mode") == "offline"
            assert row["chunk"].metadata.get("acl") == "smoke"
            assert row["chunk"].metadata.get("file_name") == "manual.pdf"
            assert row["chunk"].doc_id == "doc-f"
        span_names = [name for name, _ in trace.spans]
        for expected in ("parse", "clean", "chunk", "embed", "insert", "graph"):
            assert expected in span_names, f"缺少 {expected} span"
        print(
            f"[8/9] add_file(全链路) ok  chunk={r.chunk_count} "
            f"graph entity={r.graph.entity_count} relation={r.graph.relation_count} "
            f"spans={len(span_names)}"
        )

        # 4) graph 开关逐次覆盖：graph=False 时跳过建图
        fake2 = FakeMilvus()
        pipeline2 = IngestPipeline(
            embedder=FakeEmbedder(),
            milvus=fake2,
            parser=parser,
            graph_builder=graph_stub,
            graph_enabled=True,
        )
        r2 = pipeline2.add_file(
            str(Path(tmp) / "manual.pdf"), doc_id="doc-g", graph=False
        )
        assert r2.graph is None, "graph=False 应跳过建图"
        assert fake2.count("doc-g") == r2.chunk_count

        # 5) 多 segment（MarkdownStrategy 按标题分节）跨段序号连续唯一：
        #    chunk_index 全局连续、chunk_id 全局唯一（相同正文也不冲突）
        multi_text = "".join(
            f"## 章节{i}\n\n" + "内容段落" * 400 + "\n\n" for i in range(5)
        )
        multi_path = str(Path(tmp) / "multi.md")
        Path(multi_path).write_text(multi_text, encoding="utf-8")
        fake3 = FakeMilvus()
        pipeline3 = IngestPipeline(
            embedder=FakeEmbedder(),
            milvus=fake3,
            cleaner=Cleaner(),
            strategy=MarkdownStrategy(),
        )
        r3 = pipeline3.add_file(multi_path, doc_id="doc-multi")
        assert fake3.count("doc-multi") == r3.chunk_count, "写入行数应与 chunk 数一致"
        assert r3.chunk_count > 3, "多章节文档应产出多个 chunk"
        rows3 = [row["chunk"] for row in fake3.rows.values()]
        indices = sorted(c.metadata["chunk_index"] for c in rows3)
        assert indices == list(range(len(indices))), "chunk_index 应全局连续唯一"
        ids = [c.chunk_id for c in rows3]
        assert len(set(ids)) == len(ids), "chunk_id 应全局唯一（跨段不冲突）"
        # 确定性：再次入库同一文件，chunk_id 集合完全一致
        fake4 = FakeMilvus()
        pipeline3.milvus = fake4
        pipeline3.add_file(multi_path, doc_id="doc-multi")
        assert {c.chunk_id for c in (row["chunk"] for row in fake4.rows.values())} == set(ids), (
            "相同输入应产出相同 chunk_id（确定性）"
        )
        print(
            f"[9/9] add_file(多段) ok  chunk={r3.chunk_count} "
            f"index/ids 连续唯一，确定性通过"
        )


def main() -> int:
    expected_count = test_chunker()
    test_ingest(expected_count)
    test_add_file()
    print("=" * 60)
    print("SMOKE INDEXING PASSED (exit 0)")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
