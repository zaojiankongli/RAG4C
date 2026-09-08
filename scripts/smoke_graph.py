"""知识图谱（vector-graph-rag）索引 + 检索路径离线冒烟测试。

完全离线：无网络、无模型、无 Milvus、无 LLM、无 langgraph（全部使用
确定性内存桩）。

覆盖断言：
1. 图索引（indexing/graph_builder.py + FakeGraphStore）：
   (a) 首次构建：triplet / entity / relation 计数与 store 落库数量；
   (b) 邻接正确性：实体 relation_ids / passage_ids，关系 entity_ids /
       passage_ids，结构化三元组字段；
   (c) 无三元组 chunk 跳过（不写库）；
   (d) 增量合并：重复构建同一 chunk 不产生重复节点，邻接合并去重；
   (e) 级联删除：删除涉及 chunk 的关系 + 失去全部引用的孤儿实体，
       仍被其他关系 / 其他 passage 引用的实体保留。
2. 图检索（retrieval/graph_retriever.py）：
   (f) llm=None 跳过 LLM 重排，按关系分数排序；
   (g) passage_ids 从合并关系（扩展 + 检索命中）收集并去重；
   (h) detail 明细字段填充（entity_texts / entity_scores / relation_texts /
       expanded_relation_ids），reranked_relation_ids == []；
   (i) 空图 -> degraded=True 且无 passage。
3. 管线集成（retrieval/pipeline.py）：
   (j) vector_graph_rag 目标 + 图命中 -> 图结果合并在前（graph_first），
       route.degraded=False，图 chunk 的 branch == "graph"；
   (k) full 目标 -> hybrid 在前、图结果追加在后；
   (l) graph_retrieval_on=False -> degraded=True 且仅 hybrid；
   (m) 图检索器抛 GraphRetrieverError -> degraded=True 不崩溃；
   (n) 图结果与 hybrid 完全去重 -> degraded=True；
   (o) 未注入图检索器 / 图检索无命中 -> degraded=True（补充场景）。

运行：
    python scripts/smoke_graph.py

退出码：全部通过为 0，任一断言失败为 1。
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

# 保证从任意工作目录运行都能找到 config / models / core / retrieval / indexing
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import GraphSettings, PipelineSettings, Settings
from core.graph_store import RagGraphStoreError
from indexing.graph_builder import GraphBuilder, _stable_id
from indexing.triplet_extractor import FakeTripletExtractor
from models.schemas import Chunk, GraphRetrievalDetail, RetrievedChunk, RouteDecision
from retrieval.graph_retriever import GraphRetriever, GraphRetrieverError, GraphRetrievalResult
from retrieval.pipeline import RetrievalPipeline

_NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 确定性桩
# ---------------------------------------------------------------------------

def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """余弦相似度（纯内存计算，确定性）。"""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class FakeEmbedder:
    """确定性哑嵌入：按字符哈希构造固定向量（相同文本 -> 相同向量）。"""

    _DIM = 32

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)

    @classmethod
    def _vec(cls, text: str) -> list[float]:
        vec = [0.0] * cls._DIM
        for ch in text or "":
            vec[ord(ch) % cls._DIM] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class FakeGraphStore:
    """RagGraphStore 的内存替身（纯 dict，确定性）。

    实体按 id 存储、按 name 精确索引；关系按 id 存储、按 text 精确索引，
    语义与真实存储一致（get_*_by_texts 为精确匹配，返回 text -> 记录）。
    邻接与距离字段与真实存储返回一致，供图构建 / 图检索使用。
    """

    def __init__(self, graph: Optional[GraphSettings] = None) -> None:
        self.graph = graph if graph is not None else Settings().graph
        self._entities: dict[str, dict] = {}            # id -> 记录
        self._entity_vecs: dict[str, list[float]] = {}
        self._entity_id_by_name: dict[str, str] = {}
        self._relations: dict[str, dict] = {}           # id -> 记录
        self._relation_vecs: dict[str, list[float]] = {}
        self._relation_id_by_text: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #
    def upsert_entities(
        self,
        entities: Sequence[object],
        vectors: Optional[Sequence[Sequence[float]]] = None,
        tenant_id: str = "",
    ) -> list[str]:
        ids: list[str] = []
        for entity, vec in zip(entities, vectors or []):
            self._entities[entity.id] = {
                "id": entity.id,
                "text": entity.name,
                "relation_ids": list(entity.relation_ids),
                "passage_ids": list(entity.passage_ids),
                "tenant_id": tenant_id,
            }
            self._entity_vecs[entity.id] = list(vec)
            self._entity_id_by_name[entity.name] = entity.id
            ids.append(entity.id)
        return ids

    def upsert_relations(
        self,
        relations: Sequence[object],
        vectors: Optional[Sequence[Sequence[float]]] = None,
        tenant_id: str = "",
    ) -> list[str]:
        ids: list[str] = []
        for relation, vec in zip(relations, vectors or []):
            self._relations[relation.id] = {
                "id": relation.id,
                "text": relation.text,
                "entity_ids": list(relation.entity_ids),
                "passage_ids": list(relation.passage_ids),
                "subject": relation.subject,
                "predicate": relation.predicate,
                "object": relation.object,
                "tenant_id": tenant_id,
            }
            self._relation_vecs[relation.id] = list(vec)
            self._relation_id_by_text[relation.text] = relation.id
            ids.append(relation.id)
        return ids

    # ------------------------------------------------------------------ #
    # 读取（按 text 精确查重）
    # ------------------------------------------------------------------ #
    def get_entities_by_texts(
        self, texts: Sequence[str], tenant_id: str = ""
    ) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for text in dict.fromkeys(texts):
            eid = self._entity_id_by_name.get(text)
            if eid is None or text in out:
                continue
            record = self._entities[eid]
            if tenant_id and record.get("tenant_id", "") != tenant_id:
                continue  # 跨租户不可见
            out[text] = dict(record)
        return out

    def get_relations_by_texts(
        self, texts: Sequence[str], tenant_id: str = ""
    ) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for text in dict.fromkeys(texts):
            rid = self._relation_id_by_text.get(text)
            if rid is None or text in out:
                continue
            record = self._relations[rid]
            if tenant_id and record.get("tenant_id", "") != tenant_id:
                continue  # 跨租户不可见
            out[text] = dict(record)
        return out

    # ------------------------------------------------------------------ #
    # 读取（按 id）
    # ------------------------------------------------------------------ #
    def get_entities_by_ids(
        self, entity_ids: Sequence[str], tenant_id: str = ""
    ) -> list[dict]:
        out: list[dict] = []
        for eid in entity_ids:
            record = self._entities.get(eid)
            if record is None:
                continue
            if tenant_id and record.get("tenant_id", "") != tenant_id:
                continue  # 跨租户不可见
            out.append(dict(record))
        return out

    def get_relations_by_ids(
        self, relation_ids: Sequence[str], tenant_id: str = ""
    ) -> list[dict]:
        out: list[dict] = []
        for rid in relation_ids:
            record = self._relations.get(rid)
            if record is None:
                continue
            if tenant_id and record.get("tenant_id", "") != tenant_id:
                continue  # 跨租户不可见
            out.append(dict(record))
        return out

    # ------------------------------------------------------------------ #
    # 读取（按邻接过滤）
    # ------------------------------------------------------------------ #
    def get_relations_by_passage_ids(
        self, passage_ids: Sequence[str], tenant_id: str = ""
    ) -> list[dict]:
        wanted = set(passage_ids)
        return [
            dict(r)
            for r in self._relations.values()
            if wanted.intersection(r["passage_ids"])
            and (not tenant_id or r.get("tenant_id", "") == tenant_id)
        ]

    def get_relations_by_entity_ids(
        self, entity_ids: Sequence[str], tenant_id: str = ""
    ) -> list[dict]:
        wanted = set(entity_ids)
        return [
            dict(r)
            for r in self._relations.values()
            if wanted.intersection(r["entity_ids"])
            and (not tenant_id or r.get("tenant_id", "") == tenant_id)
        ]

    # ------------------------------------------------------------------ #
    # 检索
    # ------------------------------------------------------------------ #
    def search_entities(
        self,
        query_vec: Sequence[float],
        top_k: Optional[int] = None,
        threshold: Optional[float] = None,
        tenant_id: str = "",
    ) -> list[dict]:
        top_k = top_k if top_k is not None else self.graph.entity_top_k
        threshold = threshold if threshold is not None else self.graph.entity_similarity_threshold
        if top_k < 1:
            raise RagGraphStoreError("top_k 必须 >= 1")
        hits: list[dict] = []
        for eid, record in self._entities.items():
            if tenant_id and record.get("tenant_id", "") != tenant_id:
                continue  # 跨租户不可见
            distance = _cosine(query_vec, self._entity_vecs.get(eid, []))
            if distance < threshold:
                continue
            hit = dict(record)
            hit["distance"] = distance
            hits.append(hit)
        hits.sort(key=lambda h: (-h["distance"], h["text"]))
        return hits[:top_k]

    def search_relations(
        self,
        query_vec: Sequence[float],
        top_k: Optional[int] = None,
        threshold: Optional[float] = None,
        tenant_id: str = "",
    ) -> list[dict]:
        top_k = top_k if top_k is not None else self.graph.relation_top_k
        threshold = (
            threshold
            if threshold is not None
            else self.graph.relation_similarity_threshold
        )
        if top_k < 1:
            raise RagGraphStoreError("top_k 必须 >= 1")
        hits: list[dict] = []
        for rid, record in self._relations.items():
            if tenant_id and record.get("tenant_id", "") != tenant_id:
                continue  # 跨租户不可见
            distance = _cosine(query_vec, self._relation_vecs.get(rid, []))
            if distance < threshold:
                continue
            hit = dict(record)
            hit["distance"] = distance
            hits.append(hit)
        hits.sort(key=lambda h: (-h["distance"], h["text"]))
        return hits[:top_k]

    # ------------------------------------------------------------------ #
    # 删除
    # ------------------------------------------------------------------ #
    def delete_entities_by_ids(self, entity_ids: Sequence[str]) -> int:
        count = 0
        for eid in dict.fromkeys(entity_ids):
            record = self._entities.pop(eid, None)
            if record is not None:
                self._entity_vecs.pop(eid, None)
                self._entity_id_by_name.pop(record["text"], None)
                count += 1
        return count

    def delete_relations_by_ids(self, relation_ids: Sequence[str]) -> int:
        count = 0
        for rid in dict.fromkeys(relation_ids):
            record = self._relations.pop(rid, None)
            if record is not None:
                self._relation_vecs.pop(rid, None)
                self._relation_id_by_text.pop(record["text"], None)
                count += 1
        return count

    # ------------------------------------------------------------------ #
    # 统计
    # ------------------------------------------------------------------ #
    def count_entities(self, tenant_id: str = "") -> int:
        if tenant_id:
            return sum(1 for r in self._entities.values() if r.get("tenant_id", "") == tenant_id)
        return len(self._entities)

    def count_relations(self, tenant_id: str = "") -> int:
        if tenant_id:
            return sum(1 for r in self._relations.values() if r.get("tenant_id", "") == tenant_id)
        return len(self._relations)


class FakeRewriter:
    """改写桩：返回预设结果，记录被调用的查询。"""

    def __init__(self, rewritten: str, changed: bool) -> None:
        self.rewritten = rewritten
        self.changed = changed
        self.calls: list[str] = []

    def rewrite(self, query: str) -> tuple[str, bool]:
        self.calls.append(query)
        return (self.rewritten or query), self.changed


class FakeRouter:
    """路由桩：返回预设决策，记录被调用的查询。"""

    def __init__(self, decision: RouteDecision) -> None:
        self.decision = decision
        self.calls: list[str] = []

    def route(self, query: str) -> RouteDecision:
        self.calls.append(query)
        return self.decision


class FakeMilvus:
    """Milvus 桩：hybrid_search 返回预设候选，get_chunks_by_ids 按映射回查。"""

    def __init__(self, canned: Optional[list[RetrievedChunk]] = None, chunks_by_id=None) -> None:
        self.canned = list(canned or [])
        self.chunks_by_id = dict(chunks_by_id or {})
        self.hybrid_calls: list[dict] = []
        self.chunk_calls: list[list[str]] = []

    def hybrid_search(
        self,
        query_dense,
        top_k: int,
        query_text: str | None = None,
        group_by_field: str | None = None,
        group_size: int | None = None,
        filter_expr: str | None = None,
        with_cosine: bool = False,
    ) -> list[RetrievedChunk]:
        self.hybrid_calls.append(
            {
                "top_k": top_k,
                "query_text": query_text,
                "group_by_field": group_by_field,
                "group_size": group_size,
                "filter_expr": filter_expr,
            }
        )
        return [rc.model_copy(deep=True) for rc in self.canned]

    @staticmethod
    def build_acl_filter(acls: list[str]) -> str:
        return "acl IN [" + ", ".join(f"'{a}'" for a in acls) + "]"

    @staticmethod
    def build_tenant_filter(tenant_id: str) -> str | None:
        if not tenant_id:
            return None
        return f'tenant_id == "{tenant_id}"'

    @staticmethod
    def build_dataset_filter(dataset_id: str) -> str | None:
        if not dataset_id:
            return None
        return f'dataset_id == "{dataset_id}"'

    @classmethod
    def build_filters(
        cls,
        tenant_id: str = "",
        acl=None,
        acl_filter_on: bool = True,
        dataset_id: str = "",
        extra_expr: str | None = None,
    ) -> str | None:
        """与 RagMilvusClient.build_filters 同构的组合逻辑。

        用 cls. 调用本替身自己的原语（而非真实客户端的），保证各替身刻意
        设定的过滤语义不被真实实现覆盖。
        """
        parts = [
            e
            for e in (cls.build_tenant_filter(tenant_id), cls.build_dataset_filter(dataset_id))
            if e
        ]
        if acl and acl_filter_on:
            parts.append(cls.build_acl_filter(acl))
        if extra_expr:
            parts.append("(" + extra_expr + ")")
        return " and ".join(parts) if parts else None

    def get_chunks_by_ids(self, passage_ids: Sequence[str]) -> list[Chunk]:
        self.chunk_calls.append(list(passage_ids))
        return [self.chunks_by_id[pid] for pid in passage_ids if pid in self.chunks_by_id]


class FakeReranker:
    """重排桩：保持候选顺序（分数与顺序一致）。"""

    def __init__(self) -> None:
        pass

    def rerank(self, query: str, candidates: list[Chunk]) -> list[float]:
        if not candidates:
            return []
        n = len(candidates)
        return [float(n - i) for i in range(n)]


class FakeGraphRetriever:
    """图检索桩：返回预设结果或抛预设异常，记录被调用的查询。"""

    def __init__(
        self, result: Optional[GraphRetrievalResult] = None, error: Optional[Exception] = None
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[str] = []
        self.tenant_calls: list[str] = []

    def retrieve(self, query: str, tenant_id: str | None = None) -> GraphRetrievalResult:
        self.calls.append(query)
        self.tenant_calls.append(tenant_id or "")
        if self.error is not None:
            raise self.error
        if self.result is None:
            raise GraphRetrieverError("桩未配置返回结果")
        return self.result


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

def make_chunk_obj(chunk_id: str, text: str, doc_id: str = "D") -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        doc_id=doc_id,
        text=text,
        text_hash=f"hash-{chunk_id}",
        created_at=_NOW,
        updated_at=_NOW,
    )


def make_retrieved(chunk_id: str, text: str, score: float, doc_id: str = "D") -> RetrievedChunk:
    return RetrievedChunk(
        chunk=make_chunk_obj(chunk_id, text, doc_id), score=score, rank=0, branch="hybrid"
    )


def make_graph_result(passage_ids: list[str], degraded: bool = False) -> GraphRetrievalResult:
    return GraphRetrievalResult(
        detail=GraphRetrievalDetail(
            query_entities=["Milvus"],
            entity_texts=["Milvus"],
            entity_scores=[1.0],
            relation_texts=["Milvus 是 向量数据库"],
            relation_scores=[0.7],
            expanded_relation_ids=[_stable_id("Milvus 是 向量数据库")],
            reranked_relation_ids=[],
        ),
        passage_ids=passage_ids,
        degraded=degraded,
    )


# ---------------------------------------------------------------------------
# 断言工具
# ---------------------------------------------------------------------------

class Checker:
    """逐步断言：失败立即抛出，由 main 捕获并置退出码 1。"""

    def __init__(self) -> None:
        self.steps = 0

    def ok(self, name: str) -> None:
        self.steps += 1
        print(f"[PASS] {name}")

    def check(self, cond: bool, name: str, detail: str = "") -> None:
        if not cond:
            raise AssertionError(f"{name} 失败：{detail}")
        self.ok(name)


def main() -> int:
    ck = Checker()
    try:
        # ------------------------------------------------------------------ #
        # 1. 图索引构建（GraphBuilder + FakeGraphStore + FakeTripletExtractor）
        # ------------------------------------------------------------------ #
        store = FakeGraphStore()
        builder = GraphBuilder(
            store=store, embedder=FakeEmbedder(), extractor=FakeTripletExtractor()
        )

        # (a) 首次构建：计数
        res = builder.build([("c1", "Milvus是向量数据库")])
        ck.check(
            res.chunk_count == 1
            and res.triplet_count == 1
            and res.entity_count == 2
            and res.relation_count == 1
            and res.failed_chunk_count == 0,
            "(a) 首次构建计数（1 chunk / 1 triplet / 2 实体 / 1 关系）",
            str(res),
        )
        ck.check(
            store.count_entities() == 2 and store.count_relations() == 1,
            "(a) 落库数量与构建计数一致",
            f"entities={store.count_entities()}, relations={store.count_relations()}",
        )

        # (b) 邻接正确性
        expected_r1 = _stable_id("Milvus 是 向量数据库")
        r1 = store.get_relations_by_texts(["Milvus 是 向量数据库"])["Milvus 是 向量数据库"]
        ck.check(
            r1["id"] == expected_r1
            and r1["subject"] == "Milvus"
            and r1["predicate"] == "是"
            and r1["object"] == "向量数据库",
            "(b) 关系主键稳定 + 结构化三元组字段",
            str(r1),
        )
        ck.check(
            r1["passage_ids"] == ["c1"],
            "(b) 关系 passage_ids 指向抽取来源 chunk",
            str(r1["passage_ids"]),
        )
        e_milvus = store.get_entities_by_texts(["Milvus"])["Milvus"]
        e_vec = store.get_entities_by_texts(["向量数据库"])["向量数据库"]
        ck.check(
            set(r1["entity_ids"]) == {e_milvus["id"], e_vec["id"]},
            "(b) 关系 entity_ids 连接主语与宾语实体",
            str(r1["entity_ids"]),
        )
        ck.check(
            e_milvus["relation_ids"] == [expected_r1] and e_milvus["passage_ids"] == ["c1"],
            "(b) 实体邻接（relation_ids / passage_ids）",
            str(e_milvus),
        )
        ck.check(
            e_vec["relation_ids"] == [expected_r1] and e_vec["passage_ids"] == ["c1"],
            "(b) 宾语实体邻接",
            str(e_vec),
        )

        # (c) 无三元组 chunk 跳过，不写库
        res = builder.build([("c3", "这段文本没有关系")])
        ck.check(
            res.chunk_count == 1
            and res.triplet_count == 0
            and res.entity_count == 0
            and res.relation_count == 0,
            "(c) 无三元组 chunk 计数为零",
            str(res),
        )
        ck.check(
            store.count_entities() == 2 and store.count_relations() == 1,
            "(c) 无三元组 chunk 不写库",
            f"entities={store.count_entities()}, relations={store.count_relations()}",
        )

        # (d) 增量合并：重复构建同一 chunk 不产生重复节点
        res = builder.build([("c1", "Milvus是向量数据库")])
        ck.check(
            res.triplet_count == 1 and res.entity_count == 2 and res.relation_count == 1,
            "(d) 重复构建：本次批内计数不变",
            str(res),
        )
        ck.check(
            store.count_entities() == 2 and store.count_relations() == 1,
            "(d) 重复构建不产生重复节点",
            f"entities={store.count_entities()}, relations={store.count_relations()}",
        )
        milvus = store.get_entities_by_texts(["Milvus"])["Milvus"]
        ck.check(
            milvus["relation_ids"] == [expected_r1] and milvus["passage_ids"] == ["c1"],
            "(d) 重复构建邻接合并（无重复 passage / relation）",
            str(milvus),
        )

        # (d2) 新 chunk 引入新实体 / 新关系，并合并既有节点邻接
        expected_r2 = _stable_id("Milvus 是 开源软件")
        res = builder.build([("c2", "Milvus是开源软件")])
        ck.check(
            res.triplet_count == 1 and res.entity_count == 2 and res.relation_count == 1,
            "(d2) 新 chunk 构建计数",
            str(res),
        )
        ck.check(
            store.count_entities() == 3 and store.count_relations() == 2,
            "(d2) 新实体 / 新关系落库",
            f"entities={store.count_entities()}, relations={store.count_relations()}",
        )
        milvus = store.get_entities_by_texts(["Milvus"])["Milvus"]
        ck.check(
            set(milvus["relation_ids"]) == {expected_r1, expected_r2}
            and set(milvus["passage_ids"]) == {"c1", "c2"},
            "(d2) 共享实体合并邻接（两条关系、两个 passage）",
            str(milvus),
        )
        r2 = store.get_relations_by_texts(["Milvus 是 开源软件"])["Milvus 是 开源软件"]
        ck.check(
            r2["id"] == expected_r2 and r2["passage_ids"] == ["c2"],
            "(d2) 新关系 passage_ids 指向 c2",
            str(r2),
        )
        r1 = store.get_relations_by_texts(["Milvus 是 向量数据库"])["Milvus 是 向量数据库"]
        ck.check(r1["passage_ids"] == ["c1"], "(d2) 旧关系 passage_ids 不变", str(r1))

        # (e) 级联删除
        d = builder.delete_by_chunk_ids(["c1"])
        ck.check(
            d == (1, 1),
            "(e) 删除 c1：删 1 条关系 + 1 个孤儿实体",
            f"got {d}",
        )
        ck.check(
            store.count_relations() == 1 and store.count_entities() == 2,
            "(e) 删除后 store 剩 1 关系 / 2 实体",
            f"entities={store.count_entities()}, relations={store.count_relations()}",
        )
        ck.check(
            "Milvus" in store.get_entities_by_texts(["Milvus"]),
            "(e) 仍被其他关系引用的实体保留（Milvus）",
        )
        ck.check(
            store.get_entities_by_texts(["向量数据库"]) == {},
            "(e) 失去全部引用的孤儿实体被删除（向量数据库）",
        )
        ck.check(
            store.get_relations_by_texts(["Milvus 是 向量数据库"]) == {},
            "(e) 涉及被删 chunk 的关系被删除（r1）",
        )

        d = builder.delete_by_chunk_ids(["c2"])
        ck.check(
            d == (1, 1),
            "(e) 删除 c2：删 1 条关系 + 1 个孤儿实体",
            f"got {d}",
        )
        ck.check(
            store.count_relations() == 0 and store.count_entities() == 1,
            "(e) 删除后 store 剩 0 关系 / 1 实体",
            f"entities={store.count_entities()}, relations={store.count_relations()}",
        )
        milvus = store.get_entities_by_texts(["Milvus"])["Milvus"]
        ck.check(
            set(milvus["passage_ids"]) == {"c1", "c2"},
            "(e) 仍有 passage 引用（c1 未删）的实体保留（Milvus）",
            str(milvus),
        )
        ck.check(
            store.get_entities_by_texts(["开源软件"]) == {},
            "(e) 孤儿实体被删除（开源软件）",
        )
        ck.check(
            builder.delete_by_chunk_ids([]) == (0, 0),
            "(e) 空 chunk 列表删除返回 (0, 0)",
        )

        # ------------------------------------------------------------------ #
        # 2. 图检索（GraphRetriever，llm=None 跳过 LLM 重排）
        # ------------------------------------------------------------------ #
        store2 = FakeGraphStore()
        builder2 = GraphBuilder(
            store=store2, embedder=FakeEmbedder(), extractor=FakeTripletExtractor()
        )
        builder2.build([("c1", "Milvus是向量数据库"), ("c2", "Milvus是开源软件")])

        retriever = GraphRetriever(store=store2, embedder=FakeEmbedder(), llm=None)
        result = retriever.retrieve("Milvus")

        ck.check(
            result.degraded is False,
            "(f) 有命中时 degraded=False",
        )
        ck.check(
            result.detail.entity_texts == ["Milvus"],
            "(f) 实体命中（查询与实体同名 -> 距离 1.0 过阈值）",
            str(result.detail.entity_texts),
        )
        ck.check(
            len(result.detail.entity_scores) == 1
            and abs(result.detail.entity_scores[0] - 1.0) < 1e-9,
            "(f) 实体命中分数",
            str(result.detail.entity_scores),
        )
        ck.check(
            set(result.detail.expanded_relation_ids) == {expected_r1, expected_r2},
            "(f) 子图扩展收集命中实体的直接相连关系",
            str(result.detail.expanded_relation_ids),
        )
        ck.check(
            len(result.detail.relation_texts) == 2
            and sorted(result.detail.relation_texts)
            == sorted(["Milvus 是 向量数据库", "Milvus 是 开源软件"]),
            "(f) 关系候选 = 扩展 + 检索命中合并（去重）",
            str(result.detail.relation_texts),
        )
        ck.check(
            len(result.detail.relation_scores) == 2
            and all(isinstance(s, float) for s in result.detail.relation_scores),
            "(f) 关系分数（检索命中带分数）",
            str(result.detail.relation_scores),
        )
        ck.check(
            result.detail.reranked_relation_ids == [],
            "(f) llm=None -> 跳过 LLM 重排",
            str(result.detail.reranked_relation_ids),
        )
        ck.check(
            set(result.passage_ids) == {"c1", "c2"} and len(result.passage_ids) == 2,
            "(g) passage_ids 从合并关系收集并去重",
            str(result.passage_ids),
        )
        ck.check(
            len(result.passage_ids) <= store2.graph.final_top_k,
            "(g) passage 数量受 final_top_k 截断",
            f"n={len(result.passage_ids)}, final_top_k={store2.graph.final_top_k}",
        )
        ck.check(
            result.detail.query_entities == ["Milvus"],
            "(h) detail.query_entities 填充",
            str(result.detail.query_entities),
        )

        # (i) 空图 -> degraded
        empty_store = FakeGraphStore()
        empty = GraphRetriever(store=empty_store, embedder=FakeEmbedder(), llm=None).retrieve(
            "Milvus"
        )
        ck.check(
            empty.degraded is True
            and empty.passage_ids == []
            and empty.detail.entity_texts == []
            and empty.detail.relation_texts == []
            and empty.detail.expanded_relation_ids == []
            and empty.detail.reranked_relation_ids == [],
            "(i) 空图：degraded=True 且无任何命中 / passage",
            str(empty.detail),
        )

        # ------------------------------------------------------------------ #
        # 3. 管线集成（RetrievalPipeline + 桩依赖）
        # ------------------------------------------------------------------ #
        canned = [
            make_retrieved("h1", "h1 文本", 0.9),
            make_retrieved("h2", "h2 文本", 0.8),
        ]
        g1 = make_chunk_obj("g1", "g1 文本")
        g2 = make_chunk_obj("g2", "g2 文本")
        h1_obj = make_chunk_obj("h1", "h1 文本")
        h2_obj = make_chunk_obj("h2", "h2 文本")

        def make_pipeline(
            router: RouteDecision,
            milvus: FakeMilvus,
            graph_retriever,
            graph_retrieval_on: bool = True,
        ) -> RetrievalPipeline:
            return RetrievalPipeline(
                embedder=FakeEmbedder(),
                milvus=milvus,
                reranker=FakeReranker(),
                rewriter=FakeRewriter("Milvus是什么", True),
                router=FakeRouter(router),
                settings=PipelineSettings(
                    complexity_gate_on=True, source_diversity="off", graph_retrieval_on=graph_retrieval_on
                ),
                graph_retriever=graph_retriever,
            )

        # (j) vector_graph_rag 目标 + 图命中 -> 图结果优先
        milvus = FakeMilvus(canned=canned, chunks_by_id={"g1": g1, "g2": g2})
        graph_retriever = FakeGraphRetriever(result=make_graph_result(["g1", "g2"]))
        pipeline = make_pipeline(
            RouteDecision(target="vector_graph_rag", confidence=0.9),
            milvus,
            graph_retriever,
        )
        result = pipeline.run("Milvus是什么")
        ids = [rc.chunk.chunk_id for rc in result.chunks]
        branches = [rc.branch for rc in result.chunks]
        ck.check(
            result.route.target == "vector_graph_rag" and result.route.degraded is False,
            "(j) graph 分支成功：保持原路由且不降级",
            str(result.route),
        )
        ck.check(
            ids[:2] == ["g1", "g2"] and branches[:2] == ["graph", "graph"],
            "(j) vector_graph_rag：图结果合并在最前（graph_first）",
            f"ids={ids}, branches={branches}",
        )
        ck.check(
            ids[2:] == ["h1", "h2"] and branches[2:] == ["hybrid", "hybrid"],
            "(j) hybrid 结果紧随其后且 branch=hybrid",
            f"ids={ids}, branches={branches}",
        )
        ck.check(
            milvus.chunk_calls[-1] == ["g1", "g2"],
            "(j) get_chunks_by_ids 收到图 passage_ids",
            str(milvus.chunk_calls),
        )
        ck.check(
            any("graph 分支合并 2 条 passage" in t for t in result.traces),
            "(j) 记录图分支合并 trace",
            str(result.traces),
        )
        ck.check(
            graph_retriever.calls == ["Milvus是什么"],
            "(j) 图检索器按改写后查询被调用一次",
            str(graph_retriever.calls),
        )

        # (k) full 目标 -> hybrid 优先，图结果追加
        milvus = FakeMilvus(canned=canned, chunks_by_id={"g1": g1, "g2": g2})
        pipeline = make_pipeline(
            RouteDecision(target="full", confidence=0.9),
            milvus,
            FakeGraphRetriever(result=make_graph_result(["g1", "g2"])),
        )
        result = pipeline.run("Milvus是什么")
        ids = [rc.chunk.chunk_id for rc in result.chunks]
        branches = [rc.branch for rc in result.chunks]
        ck.check(
            result.route.target == "full" and result.route.degraded is False,
            "(k) full 目标：不降级",
            str(result.route),
        )
        ck.check(
            ids[:2] == ["h1", "h2"] and ids[2:] == ["g1", "g2"],
            "(k) full：hybrid 在前，图结果追加在后",
            f"ids={ids}, branches={branches}",
        )
        ck.check(
            branches == ["hybrid", "hybrid", "graph", "graph"],
            "(k) full：图 chunk branch=graph",
            str(branches),
        )

        # (l) graph_retrieval_on=False -> degraded + 仅 hybrid
        milvus = FakeMilvus(canned=canned, chunks_by_id={"g1": g1, "g2": g2})
        graph_retriever = FakeGraphRetriever(result=make_graph_result(["g1", "g2"]))
        pipeline = make_pipeline(
            RouteDecision(target="vector_graph_rag", confidence=0.9),
            milvus,
            graph_retriever,
            graph_retrieval_on=False,
        )
        result = pipeline.run("Milvus是什么")
        ck.check(
            result.route.target == "vector_graph_rag" and result.route.degraded is True,
            "(l) 开关关闭：degraded=True",
            str(result.route),
        )
        ck.check(
            [rc.chunk.chunk_id for rc in result.chunks] == ["h1", "h2"]
            and all(rc.branch == "hybrid" for rc in result.chunks),
            "(l) 仅返回 hybrid 结果",
            [(rc.chunk.chunk_id, rc.branch) for rc in result.chunks],
        )
        ck.check(
            graph_retriever.calls == [],
            "(l) 开关关闭：图检索器零调用",
            str(graph_retriever.calls),
        )
        ck.check(
            any("graph 开关关闭或未注入图检索器，降级 hybrid" in t for t in result.traces),
            "(l) 记录开关关闭降级 trace",
            str(result.traces),
        )

        # (m) 图检索器抛 GraphRetrieverError -> degraded，不崩溃
        milvus = FakeMilvus(canned=canned)
        pipeline = make_pipeline(
            RouteDecision(target="vector_graph_rag", confidence=0.9),
            milvus,
            FakeGraphRetriever(error=GraphRetrieverError("模拟图存储故障")),
        )
        result = pipeline.run("Milvus是什么")
        ck.check(
            result.route.degraded is True,
            "(m) 图检索异常：degraded=True",
            str(result.route),
        )
        ck.check(
            [rc.chunk.chunk_id for rc in result.chunks] == ["h1", "h2"]
            and all(rc.branch == "hybrid" for rc in result.chunks),
            "(m) 异常不崩溃：保留 hybrid 结果",
            [(rc.chunk.chunk_id, rc.branch) for rc in result.chunks],
        )
        ck.check(
            any("graph 检索失败，降级 hybrid" in t for t in result.traces),
            "(m) 记录图检索失败 trace",
            str(result.traces),
        )

        # (n) 图结果与 hybrid 完全去重 -> degraded
        milvus = FakeMilvus(canned=canned, chunks_by_id={"h1": h1_obj, "h2": h2_obj})
        pipeline = make_pipeline(
            RouteDecision(target="vector_graph_rag", confidence=0.9),
            milvus,
            FakeGraphRetriever(result=make_graph_result(["h1", "h2"])),
        )
        result = pipeline.run("Milvus是什么")
        ck.check(
            result.route.degraded is True,
            "(n) 图结果与 hybrid 完全重复：degraded=True",
            str(result.route),
        )
        ck.check(
            [rc.chunk.chunk_id for rc in result.chunks] == ["h1", "h2"]
            and all(rc.branch == "hybrid" for rc in result.chunks),
            "(n) 完全去重后仅 hybrid 结果",
            [(rc.chunk.chunk_id, rc.branch) for rc in result.chunks],
        )
        ck.check(
            any("graph 命中但 ACL 过滤 / 与混合结果完全重复，降级 hybrid" in t for t in result.traces),
            "(n) 记录完全重复降级 trace",
            str(result.traces),
        )

        # (o) 补充：未注入图检索器 -> degraded
        milvus = FakeMilvus(canned=canned)
        pipeline = RetrievalPipeline(
            embedder=FakeEmbedder(),
            milvus=milvus,
            reranker=FakeReranker(),
            rewriter=FakeRewriter("Milvus是什么", True),
            router=FakeRouter(RouteDecision(target="vector_graph_rag", confidence=0.9)),
            settings=PipelineSettings(complexity_gate_on=True, source_diversity="off"),
        )
        result = pipeline.run("Milvus是什么")
        ck.check(
            result.route.degraded is True,
            "(o) 未注入图检索器：degraded=True",
            str(result.route),
        )
        ck.check(
            [rc.chunk.chunk_id for rc in result.chunks] == ["h1", "h2"],
            "(o) 未注入图检索器：仅 hybrid 结果",
            [rc.chunk.chunk_id for rc in result.chunks],
        )

        # (p) 补充：图检索成功但无命中 -> degraded
        milvus = FakeMilvus(canned=canned)
        pipeline = make_pipeline(
            RouteDecision(target="vector_graph_rag", confidence=0.9),
            milvus,
            FakeGraphRetriever(result=make_graph_result([], degraded=True)),
        )
        result = pipeline.run("Milvus是什么")
        ck.check(
            result.route.degraded is True,
            "(p) 图检索无命中：degraded=True",
            str(result.route),
        )
        ck.check(
            [rc.chunk.chunk_id for rc in result.chunks] == ["h1", "h2"],
            "(p) 无命中：仅 hybrid 结果",
            [rc.chunk.chunk_id for rc in result.chunks],
        )
        ck.check(
            any("graph 无命中，降级 hybrid" in t for t in result.traces),
            "(p) 记录无命中降级 trace",
            str(result.traces),
        )

        print()
        print(f"SMOKE GRAPH PASSED (exit 0)  --  {ck.steps} 项断言全部通过")
        return 0

    except AssertionError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        print("SMOKE GRAPH FAILED (exit 1)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
