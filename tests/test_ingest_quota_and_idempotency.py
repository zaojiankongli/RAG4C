"""入库路径的配额闸门与重试幂等性回归测试。

两个缺陷共用一条链路，症状都是"没有任何报错"：

1. **配额从不生效**。``core.catalog.check_quota`` 在整个生产代码里一个调用点
   都没有（只有 smoke 脚本在调）。配额能配、设置页能显示、文档里写着"入库前
   由 check_quota 完成"——就是从来不执行。租户可以无限写。

2. **重试把整篇文档再插一遍**。写入走的是 ``insert_chunks``，而 chunk_id 是
   ``f"{doc_id}::{seq:04d}::{sha(text)}"`` 这样完全确定的主键。Milvus 的
   insert 不按主键去重，于是"嵌入成功、写入超时"后的任何一次重试都会让同一段
   内容在库里多一份。之后检索时这些副本占满 top_k、重排看到 N 个一模一样的
   候选、生成端拿到 N 份重复证据。全程零异常。

这里用假的 embedder / milvus，因为要断言的是**调用与否**和**调用哪个方法**，
不是向量库的行为。
"""
from __future__ import annotations

import pytest

from indexing.ingest import IngestError, IngestPipeline, QuotaExceededError

_TENANT = "tenant-quota-test"
_TEXT = "第一段内容。" * 40 + "\n\n" + "第二段内容。" * 40


class _RecordingEmbedder:
    """记录是否被调用过——配额应当在**花钱之前**就拦住。"""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[0.1] * 8 for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.1] * 8


class _RecordingMilvus:
    """记录写入用的是 insert 还是 upsert，以及每次写了哪些主键。"""

    def __init__(self) -> None:
        self.inserted: list[list[str]] = []
        self.upserted: list[list[str]] = []

    def insert_chunks(self, chunks, dense_vectors=None) -> list[str]:
        self.inserted.append([c.chunk_id for c in chunks])
        return [c.chunk_id for c in chunks]

    def upsert_chunks(self, chunks, dense_vectors=None) -> list[str]:
        self.upserted.append([c.chunk_id for c in chunks])
        return [c.chunk_id for c in chunks]

    def ensure_collection(self) -> None:
        pass

    def delete_by_doc_id(self, doc_id: str) -> int:
        return 0

    # 库里最终有几行（按主键去重）——这正是 insert 与 upsert 的分水岭
    @property
    def distinct_ids(self) -> set[str]:
        ids: set[str] = set()
        for batch in self.inserted + self.upserted:
            ids.update(batch)
        return ids

    @property
    def total_rows_written(self) -> int:
        return sum(len(b) for b in self.inserted) + sum(len(b) for b in self.upserted)


@pytest.fixture
def pipeline() -> tuple[IngestPipeline, _RecordingEmbedder, _RecordingMilvus]:
    embedder = _RecordingEmbedder()
    milvus = _RecordingMilvus()
    return IngestPipeline(embedder=embedder, milvus=milvus), embedder, milvus


def _chunks(pipe: IngestPipeline, doc_id: str, text: str = _TEXT):
    """直接走切分器拿 Chunk。

    不用 ``parse_and_chunk``：那条要落地文件，而这里要测的是它之后的一段
    （嵌入 + 写入），把文件 IO 拉进来只会让失败原因变模糊。
    """
    return pipe.chunker.chunk_document(doc_id, text)


@pytest.fixture
def quota(monkeypatch):
    """把 check_quota 换成可编程的假实现，返回一个可设置结果的开关对象。

    打在 ``core.catalog`` 上而不是 ingest 的引用上：被测代码是函数内 import，
    补在源头才拦得住。
    """

    class _Quota:
        def __init__(self) -> None:
            self.ok = True
            self.reason = ""
            self.calls: list[int] = []
            self.raises: Exception | None = None

        def __call__(self, tenant_id: str, add_documents: int = 0, add_chunks: int = 0):
            self.calls.append(add_chunks)
            if self.raises is not None:
                raise self.raises
            return self.ok, self.reason

    from core import catalog

    fake = _Quota()
    monkeypatch.setattr(catalog, "check_quota", fake)
    return fake


# --------------------------------------------------------------------------- #
# 配额
# --------------------------------------------------------------------------- #
def test_quota_is_checked_at_all(pipeline, quota) -> None:
    """最基本的一条：入库路径真的调了 check_quota。

    修复之前这条必然红——生产代码里没有任何调用点。
    """
    pipe, _, _ = pipeline
    chunks = _chunks(pipe, "doc-1")
    pipe.embed_and_insert(chunks, tenant_id=_TENANT)

    assert quota.calls, "入库全程没有检查配额"
    assert quota.calls[0] == len(chunks), "报给配额的 chunk 数与实际写入数不一致"


def test_quota_rejection_happens_before_embedding(pipeline, quota) -> None:
    """超限时必须在嵌入**之前**拦住。

    嵌入是整条链路最贵的一步（按 token 计费的外部调用）。放到写入前才拦，钱
    已经花掉了——配额拦住的是容量，拦不住成本。
    """
    pipe, embedder, milvus = pipeline
    quota.ok = False
    quota.reason = "chunk 配额不足：已用 100000，上限 100000"

    chunks = _chunks(pipe, "doc-1")
    with pytest.raises(QuotaExceededError) as exc:
        pipe.embed_and_insert(chunks, tenant_id=_TENANT)

    assert "配额" in str(exc.value)
    assert embedder.calls == [], "配额已超限，却仍然调用了嵌入服务（钱白花了）"
    assert milvus.total_rows_written == 0


def test_quota_error_is_distinguishable_from_a_crash(pipeline, quota) -> None:
    """配额超限要能和"入库炸了"区分开。

    二者都该被既有的 ``except IngestError`` 兜住（所以是子类），但只有前者是
    用户自己能处理的情况：重试多少次配额都还是不够，不该进重试队列。
    """
    pipe, _, _ = pipeline
    quota.ok = False
    quota.reason = "配额不足"
    chunks = _chunks(pipe, "doc-1")

    with pytest.raises(IngestError):  # 既有兜底不会漏接
        pipe.embed_and_insert(chunks, tenant_id=_TENANT)
    with pytest.raises(QuotaExceededError):  # 又能被单独识别
        pipe.embed_and_insert(chunks, tenant_id=_TENANT)


def test_quota_backend_outage_fails_open(pipeline, quota) -> None:
    """配额后端挂了要放行，不能把所有租户的入库一起拖下水。

    配额是成本 / 容量护栏，不是安全边界。安全边界是租户隔离，那条在
    milvus_client 的过滤器里，且必须 fail-closed（见 tests/test_tenant_isolation.py）。
    为一个护栏在计费库抖动时全量拒绝入库，代价远大于短暂超额。
    """
    pipe, embedder, milvus = pipeline
    quota.raises = RuntimeError("计费库连接超时")

    chunks = _chunks(pipe, "doc-1")
    result = pipe.embed_and_insert(chunks, tenant_id=_TENANT)

    assert result.chunk_count == len(chunks)
    assert milvus.total_rows_written == len(chunks)


def test_no_tenant_skips_the_quota_check(pipeline, quota) -> None:
    """单租户 / 未启用租户时不查配额（也无从查起）。"""
    pipe, _, milvus = pipeline
    chunks = _chunks(pipe, "doc-1")
    pipe.embed_and_insert(chunks, tenant_id="")

    assert quota.calls == []
    assert milvus.total_rows_written == len(chunks)


# --------------------------------------------------------------------------- #
# 重试幂等
# --------------------------------------------------------------------------- #
def test_chunk_id_is_deterministic(pipeline) -> None:
    """幂等的前提：同一份文档两次切分必须得到同样的主键。

    这条要是红了，下面的 upsert 幂等就无从谈起——upsert 只按主键去重。
    """
    pipe, _, _ = pipeline
    first = [c.chunk_id for c in _chunks(pipe, "doc-1")]
    second = [c.chunk_id for c in _chunks(pipe, "doc-1")]

    assert first == second
    assert len(set(first)) == len(first), "同一篇文档内部 chunk_id 撞了"


def test_retry_does_not_duplicate_chunks(pipeline, quota) -> None:
    """重试同一篇文档，库里不能多出副本——这是整个 #5 的正题。

    模拟的是最常见的那种失败：嵌入成功、写入超时，调用方重试整篇。
    """
    pipe, _, milvus = pipeline
    chunks = _chunks(pipe, "doc-1")

    pipe.embed_and_insert(chunks, tenant_id=_TENANT)
    pipe.embed_and_insert(_chunks(pipe, "doc-1"), tenant_id=_TENANT)

    assert milvus.inserted == [], "写入走了 insert_chunks，重试会留下重复行"
    assert len(milvus.upserted) == 2
    # 写了两轮，但主键集合不变 -> 库里仍然只有一份
    assert len(milvus.distinct_ids) == len(chunks)


def test_add_document_path_also_upserts(pipeline, quota) -> None:
    """另一条写入路径（add_document）同样要幂等。

    两条路径分别写着自己的 insert 调用，只改一处等于只修了一半——而没修的那
    半条路径同样是生产在走的。
    """
    pipe, _, milvus = pipeline
    pipe.add_document("doc-2", _TEXT, tenant_id=_TENANT)
    pipe.add_document("doc-2", _TEXT, tenant_id=_TENANT)

    assert milvus.inserted == []
    assert len(milvus.upserted) == 2
    assert len(milvus.distinct_ids) == len(milvus.upserted[0])


def test_add_document_also_enforces_quota(pipeline, quota) -> None:
    """add_document 路径的配额闸门。"""
    pipe, embedder, milvus = pipeline
    quota.ok = False
    quota.reason = "配额不足"

    with pytest.raises(QuotaExceededError):
        pipe.add_document("doc-2", _TEXT, tenant_id=_TENANT)

    assert embedder.calls == []
    assert milvus.total_rows_written == 0


def test_distinct_documents_still_produce_distinct_rows(pipeline, quota) -> None:
    """幂等不能过头：不同文档必须各写各的，不许互相覆盖。"""
    pipe, _, milvus = pipeline
    pipe.embed_and_insert(_chunks(pipe, "doc-a"), tenant_id=_TENANT)
    pipe.embed_and_insert(_chunks(pipe, "doc-b"), tenant_id=_TENANT)

    assert len(milvus.distinct_ids) == milvus.total_rows_written
