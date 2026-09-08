"""可插拔检索增强模块离线冒烟测试。

完全离线：无网络、无模型、无 Milvus（全部使用确定性桩）。

覆盖本轮新增的 5 个可插拔检索增强组件：

- 查询端增强（retrieval/query_enhance.py）：
  - HyDE       假设文档嵌入：开启 + LLM 返回 -> 返回假设文档；LLM 抛错 /
                 关闭 / 空查询 -> None；
  - SubQueries 子查询拆解：去重、裁剪、LLM 抛错 -> []；
  - Stepback   后退式提问：LLM 判定无需后退（空串）-> None，LLM 抛错 -> None；
- 索引端增强（indexing/contextual.py）：
  - Contextual Retrieval：上下文与输入 chunk 对齐、关闭 / LLM 失败 -> {}、
    畸形返回（list / str / None）不抛错；
- 检索端增强（retrieval/sentence_window.py）：
  - Sentence Window：replace=True 子块被父块替换（条数不变）；
    replace=False 父块追加在子块后（按 chunk_id 去重）；
    父块缺失 / milvus 抛错 -> 原样返回；
- 管线接入（retrieval/pipeline.py）：
  - 开启 hyde_on 且注入 hyde -> run() 走通并记录 hyde span；
  - 全部开关默认关闭 -> 注入与未注入行为一致（span 集合相同）；
  - 开启 subqueries_on -> 子查询分别检索并去重合并候选。

运行：
    python scripts/smoke_enhance.py

退出码：全部通过为 0，任一断言失败为 1。
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 保证从任意工作目录运行都能找到 config / models / core / retrieval / indexing
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import PipelineSettings
from core.llm import LLMError
from indexing.contextual import Contextualizer
from models.schemas import Chunk, RetrievedChunk, RouteDecision
from retrieval.pipeline import RetrievalPipeline
from retrieval.query_enhance import HydeGenerator, StepbackGenerator, SubQueryGenerator
from retrieval.sentence_window import SentenceWindowExpander

HYDE_TEMPLATE = PROJECT_ROOT / "prompts" / "hyde_v1.txt"
SUBQUERIES_TEMPLATE = PROJECT_ROOT / "prompts" / "subqueries_v1.txt"
STEPBACK_TEMPLATE = PROJECT_ROOT / "prompts" / "stepback_v1.txt"
CONTEXTUAL_TEMPLATE = PROJECT_ROOT / "prompts" / "contextual_v1.txt"

_NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 确定性桩
# ---------------------------------------------------------------------------

class StubLLM:
    """LLM 桩：返回预设 JSON 或抛预设异常，记录调用次数 / 参数。"""

    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = 0
        self.last_messages: list[dict] | None = None
        self.last_schema_hint: str | None = None

    def chat_json(
        self, messages: list[dict], schema_hint: str | None = None
    ) -> dict:
        self.calls += 1
        self.last_messages = messages
        self.last_schema_hint = schema_hint
        if self.error is not None:
            raise self.error
        return self.result


class StubMilvus:
    """Milvus 桩：记录 hybrid_search / get_chunks_by_ids 调用参数，
    按查询文本返回不同候选（深拷贝，防污染），可配置父块映射或抛错。"""

    def __init__(
        self,
        canned: list[RetrievedChunk] | None = None,
        canned_by_query: dict[str, list[RetrievedChunk]] | None = None,
        parents: list[Chunk] | None = None,
        raise_on_fetch: bool = False,
    ) -> None:
        self.canned = canned or []
        self.canned_by_query = canned_by_query or {}
        self.parents = {c.chunk_id: c for c in (parents or [])}
        self.raise_on_fetch = raise_on_fetch
        self.calls: list[dict] = []
        self.fetch_calls: list[list[str]] = []

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
        self.calls.append(
            {
                "query_dense": query_dense,
                "top_k": top_k,
                "query_text": query_text,
                "group_by_field": group_by_field,
                "group_size": group_size,
                "filter_expr": filter_expr,
            }
        )
        canned = self.canned_by_query.get(query_text or "", self.canned)
        return [rc.model_copy(deep=True) for rc in canned]

    def get_chunks_by_ids(self, ids) -> list[Chunk]:
        self.fetch_calls.append(list(ids))
        if self.raise_on_fetch:
            raise RuntimeError("milvus 暂不可用")
        return [self.parents[i] for i in ids if i in self.parents]

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


class StubEmbedder:
    """嵌入桩：记录被嵌入的文本，返回确定性向量。"""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return [1.0, 0.0, 0.0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0]] * len(texts)


class StubRewriter:
    """改写桩：返回预设结果，记录被调用的查询。"""

    def __init__(self, rewritten: str, changed: bool) -> None:
        self.rewritten = rewritten
        self.changed = changed
        self.calls: list[str] = []

    def rewrite(self, query: str) -> tuple[str, bool]:
        self.calls.append(query)
        return (self.rewritten or query), self.changed


class StubRouter:
    """路由桩：返回预设决策，记录被调用的查询。"""

    def __init__(self, decision) -> None:
        self.decision = decision
        self.calls: list[str] = []

    def route(self, query: str):
        self.calls.append(query)
        return self.decision


class StubReranker:
    """重排桩：保持候选原始顺序。"""

    def rerank(self, query: str, candidates: list[Chunk]) -> list[float]:
        return [float(len(candidates) - i) for i in range(len(candidates))]


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

def make_chunk(chunk_id: str, text: str, parent_chunk_id: str | None = None) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        doc_id=f"doc-{chunk_id}",
        text=text,
        text_hash=f"hash-{chunk_id}",
        created_at=_NOW,
        updated_at=_NOW,
        parent_chunk_id=parent_chunk_id,
    )


def make_rc(chunk: Chunk, score: float, rank: int, branch: str = "hybrid") -> RetrievedChunk:
    return RetrievedChunk(chunk=chunk, score=score, rank=rank, branch=branch)


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
        # 1. HyDE 假设文档嵌入
        # ------------------------------------------------------------------ #
        llm = StubLLM(result={"hypothetical_document": "报销流程一般包括提交、审批与打款三个阶段"})
        hyde = HydeGenerator(llm, HYDE_TEMPLATE, enabled=True)
        doc = hyde.generate("公司的报销流程是什么")
        ck.check(
            doc == "报销流程一般包括提交、审批与打款三个阶段" and llm.calls == 1,
            "HyDE①：开启且 LLM 返回假设文档 -> generate 返回该文档",
            f"got {doc!r}, calls={llm.calls}",
        )

        bad_llm = StubLLM(error=LLMError("网络不可用"))
        hyde = HydeGenerator(bad_llm, HYDE_TEMPLATE, enabled=True)
        doc = hyde.generate("公司的报销流程是什么")
        ck.check(doc is None, "HyDE②：LLM 抛错 -> 返回 None（静默降级）", f"got {doc!r}")

        hyde = HydeGenerator(StubLLM(result={"hypothetical_document": "x"}), HYDE_TEMPLATE, enabled=False)
        ck.check(
            hyde.generate("公司的报销流程是什么") is None,
            "HyDE③：enabled=False -> 返回 None（零 LLM 调用）",
        )

        hyde = HydeGenerator(StubLLM(result={"hypothetical_document": "x"}), HYDE_TEMPLATE, enabled=True)
        ck.check(
            hyde.generate("   ") is None and hyde.generate("") is None,
            "HyDE④：空查询 -> 返回 None（零 LLM 调用）",
        )

        # ------------------------------------------------------------------ #
        # 2. SubQueries 子查询拆解
        # ------------------------------------------------------------------ #
        llm = StubLLM(
            result={"sub_queries": ["报销流程有哪些步骤", "报销标准是什么", "报销流程有哪些步骤"]}
        )
        subs = SubQueryGenerator(llm, SUBQUERIES_TEMPLATE, max_sub_queries=4, enabled=True)
        out = subs.generate("公司报销流程与标准")
        ck.check(
            out == ["报销流程有哪些步骤", "报销标准是什么"],
            "SubQueries①：正常返回去重后的子查询列表",
            f"got {out!r}",
        )

        bad_llm = StubLLM(error=LLMError("超时"))
        subs = SubQueryGenerator(bad_llm, SUBQUERIES_TEMPLATE, enabled=True)
        out = subs.generate("公司报销流程与标准")
        ck.check(out == [], "SubQueries②：LLM 抛错 -> 返回 []（回退原查询）", f"got {out!r}")

        llm = StubLLM(
            result={"sub_queries": ["q1", "q2", "q3", "q4", "q5"]}
        )
        subs = SubQueryGenerator(llm, SUBQUERIES_TEMPLATE, max_sub_queries=2, enabled=True)
        out = subs.generate("多主题查询")
        ck.check(
            out == ["q1", "q2"],
            "SubQueries③：超过 max_sub_queries=2 被裁剪",
            f"got {out!r}",
        )

        llm = StubLLM(result={"sub_queries": ["a", "a", "  ", "b", "a"]})
        subs = SubQueryGenerator(llm, SUBQUERIES_TEMPLATE, enabled=True)
        out = subs.generate("去重场景")
        ck.check(
            out == ["a", "b"],
            "SubQueries④：重复项被去重、空串被丢弃",
            f"got {out!r}",
        )

        # ------------------------------------------------------------------ #
        # 3. Stepback 后退式提问
        # ------------------------------------------------------------------ #
        llm = StubLLM(result={"stepback_query": "公司的报销制度整体设计原则是什么"})
        stepback = StepbackGenerator(llm, STEPBACK_TEMPLATE, enabled=True)
        out = stepback.generate("报销单上需要填写哪些字段")
        ck.check(
            out == "公司的报销制度整体设计原则是什么" and llm.calls == 1,
            "Stepback①：正常返回后退问题",
            f"got {out!r}, calls={llm.calls}",
        )

        llm = StubLLM(result={"stepback_query": "   "})
        stepback = StepbackGenerator(llm, STEPBACK_TEMPLATE, enabled=True)
        out = stepback.generate("报销单上需要填写哪些字段")
        ck.check(
            out is None,
            "Stepback②：LLM 返回空字符串（无需后退）-> None",
            f"got {out!r}",
        )

        bad_llm = StubLLM(error=LLMError("服务不可用"))
        stepback = StepbackGenerator(bad_llm, STEPBACK_TEMPLATE, enabled=True)
        out = stepback.generate("报销单上需要填写哪些字段")
        ck.check(out is None, "Stepback③：LLM 抛错 -> None（静默降级）", f"got {out!r}")

        # ------------------------------------------------------------------ #
        # 4. Contextual Retrieval 文档级上下文
        # ------------------------------------------------------------------ #
        chunks = [("c1", "第一段文本"), ("c2", "第二段文本")]
        llm = StubLLM(
            result={
                "contexts": [
                    {"chunk_id": "c1", "context": "本章概述报销总则"},
                    {"chunk_id": "c2", "context": "本章详述审批流程"},
                    {"chunk_id": "unknown", "context": "不在输入中的 chunk"},
                ]
            }
        )
        ctx = Contextualizer(llm, CONTEXTUAL_TEMPLATE, enabled=True)
        out = ctx.contextualize("整篇报销制度文档……", chunks)
        ck.check(
            out == {"c1": "本章概述报销总则", "c2": "本章详述审批流程"},
            "Contextual①：enabled 且 LLM 正常 -> {chunk_id: context} 对齐输入（未知 id 丢弃）",
            f"got {out!r}",
        )

        ctx = Contextualizer(StubLLM(result={"contexts": []}), CONTEXTUAL_TEMPLATE, enabled=False)
        out = ctx.contextualize("文档……", chunks)
        ck.check(out == {}, "Contextual②：enabled=False -> {}（零 LLM 调用）", f"got {out!r}")

        ctx = Contextualizer(
            StubLLM(error=LLMError("调用失败")), CONTEXTUAL_TEMPLATE, enabled=True
        )
        out = ctx.contextualize("文档……", chunks)
        ck.check(out == {}, "Contextual③：LLM 抛错 -> {}（静默降级）", f"got {out!r}")

        for shape, expected in [
            ([{"chunk_id": "c1", "context": "裸数组形状"}], {"c1": "裸数组形状"}),
            ("一段纯文本", {}),
            (None, {}),
            ({"no_contexts_key": 1}, {}),
        ]:
            ctx = Contextualizer(StubLLM(result=shape), CONTEXTUAL_TEMPLATE, enabled=True)
            out = ctx.contextualize("文档……", chunks)
            ck.check(
                out == expected and isinstance(out, dict),
                f"Contextual④：畸形返回 {type(shape).__name__} 不抛错",
                f"got {out!r}",
            )

        # ------------------------------------------------------------------ #
        # 5. Sentence Window 父块回取
        # ------------------------------------------------------------------ #
        p1 = make_chunk("p1", "父块一全文")
        p2 = make_chunk("p2", "父块二全文")
        c1 = make_chunk("c1", "子块一", parent_chunk_id="p1")
        c2 = make_chunk("c2", "子块二", parent_chunk_id="p2")
        c3 = make_chunk("c3", "无父块的子块三")

        # 5.1 replace=True：子块被父块替换，条数不变
        milvus = StubMilvus(parents=[p1, p2])
        expander = SentenceWindowExpander(milvus, replace=True, enabled=True)
        items = [
            make_rc(c1, score=9.0, rank=0),
            make_rc(c2, score=8.0, rank=1),
            make_rc(c3, score=7.0, rank=2),
        ]
        out = expander.expand(items)
        ck.check(
            len(out) == 3
            and out[0].chunk.chunk_id == "p1"
            and out[1].chunk.chunk_id == "p2"
            and out[2].chunk.chunk_id == "c3",
            "SentenceWindow①：replace=True 子块被父块替换（条数不变）",
            [rc.chunk.chunk_id for rc in out],
        )
        ck.check(
            out[0].chunk.parent_chunk_id is None
            and out[0].score == 9.0
            and out[0].rank == 0
            and out[1].rank == 1,
            "SentenceWindow①：父块继承子块 rank/score 且 parent_chunk_id=None",
            f"p1=({out[0].chunk.parent_chunk_id!r},{out[0].score},{out[0].rank})",
        )

        # 5.2 replace=False：父块追加在子块后，按 chunk_id 去重
        milvus = StubMilvus(parents=[p1])
        expander = SentenceWindowExpander(milvus, replace=False, enabled=True)
        items = [
            make_rc(c1, score=9.0, rank=0),
            make_rc(make_chunk("c1b", "子块一b", parent_chunk_id="p1"), score=8.0, rank=1),
            make_rc(c3, score=7.0, rank=2),
        ]
        out = expander.expand(items)
        ck.check(
            len(out) == 4
            and [rc.chunk.chunk_id for rc in out] == ["c1", "p1", "c1b", "c3"],
            "SentenceWindow②：replace=False 父块追加在子块后且去重",
            [rc.chunk.chunk_id for rc in out],
        )

        # 5.3 父块缺失：子块原样保留
        milvus = StubMilvus(parents=[p2])  # 只回得到 p2，pX 缺失
        expander = SentenceWindowExpander(milvus, replace=True, enabled=True)
        items = [make_rc(make_chunk("cx", "孤儿子块", parent_chunk_id="pX"), score=5.0, rank=0)]
        out = expander.expand(items)
        ck.check(
            out == items and out[0].chunk.chunk_id == "cx",
            "SentenceWindow③：父块缺失 -> 子块原样保留",
            [rc.chunk.chunk_id for rc in out],
        )

        # 5.4 milvus 抛错：原样返回
        milvus = StubMilvus(parents=[p1], raise_on_fetch=True)
        expander = SentenceWindowExpander(milvus, replace=True, enabled=True)
        items = [make_rc(c1, score=9.0, rank=0)]
        out = expander.expand(items)
        ck.check(
            out == items and out[0].chunk.chunk_id == "c1",
            "SentenceWindow④：milvus 回取抛错 -> 原样返回（静默降级）",
            [rc.chunk.chunk_id for rc in out],
        )

        # ------------------------------------------------------------------ #
        # 6. 管线接入 (a)：hyde_on + 注入 hyde -> 走通并记录 hyde span
        # ------------------------------------------------------------------ #
        llm = StubLLM(result={"hypothetical_document": "假设的报销制度文档全文"})
        hyde = HydeGenerator(llm, HYDE_TEMPLATE, enabled=True)
        canned = [make_rc(make_chunk("d1", "d1 文本"), score=9.0, rank=0)]
        milvus = StubMilvus(canned=canned)
        embedder = StubEmbedder()
        pipeline = RetrievalPipeline(
            embedder=embedder,
            milvus=milvus,
            reranker=StubReranker(),
            rewriter=StubRewriter("复杂的报销制度查询", True),
            router=StubRouter(RouteDecision(target="hybrid", confidence=0.9)),
            settings=PipelineSettings(hyde_on=True, complexity_gate_on=False, top_k=8),
            hyde=hyde,
        )
        result = pipeline.run("公司报销制度是什么")
        span_names = {s.split(":")[0] for s in result.traces}
        ck.check(
            "hyde" in span_names and llm.calls == 1,
            "管线(a)：hyde_on 开启且注入 hyde -> 记录 hyde span 且 LLM 调用一次",
            str(span_names),
        )
        ck.check(
            embedder.queries == ["假设的报销制度文档全文"],
            "管线(a)：稠密嵌入使用假设文档向量（HyDE 生效）",
            str(embedder.queries),
        )
        ck.check(
            milvus.calls[-1]["query_text"] == "复杂的报销制度查询",
            "管线(a)：BM25 文本检索仍用改写后查询",
            str(milvus.calls[-1]["query_text"]),
        )
        ck.check(
            len(result.chunks) == 1 and result.chunks[0].chunk.chunk_id == "d1",
            "管线(a)：检索结果正常返回",
            [rc.chunk.chunk_id for rc in result.chunks],
        )

        # ------------------------------------------------------------------ #
        # 7. 管线接入 (b)：全部开关默认关闭 -> 注入与未注入行为一致
        # ------------------------------------------------------------------ #
        canned_b = [
            make_rc(make_chunk("d1", "d1 文本"), score=9.0, rank=0),
            make_rc(make_chunk("d2", "d2 文本"), score=8.0, rank=1),
        ]

        def build_pipeline(inject: bool):
            milvus = StubMilvus(canned=canned_b)
            return (
                milvus,
                RetrievalPipeline(
                    embedder=StubEmbedder(),
                    milvus=milvus,
                    reranker=StubReranker(),
                    rewriter=StubRewriter("报销流程", False),
                    router=StubRouter(RouteDecision(target="hybrid", confidence=0.9)),
                    settings=PipelineSettings(),  # 全部增强开关默认关闭
                    hyde=hyde if inject else None,
                    subqueries=(
                        SubQueryGenerator(
                            StubLLM(result={"sub_queries": ["q1"]}), SUBQUERIES_TEMPLATE
                        )
                        if inject
                        else None
                    ),
                    stepback=(
                        StepbackGenerator(StubLLM(result={"stepback_query": "sb"}), STEPBACK_TEMPLATE)
                        if inject
                        else None
                    ),
                    sentence_window=(
                        SentenceWindowExpander(StubMilvus(parents=[p1]), replace=True)
                        if inject
                        else None
                    ),
                ),
            )

        milvus_a, pipe_a = build_pipeline(inject=True)
        res_a = pipe_a.run("报销流程")
        milvus_b, pipe_b = build_pipeline(inject=False)
        res_b = pipe_b.run("报销流程")
        spans_a = {s.split(":")[0] for s in res_a.traces}
        spans_b = {s.split(":")[0] for s in res_b.traces}
        ck.check(
            spans_a == spans_b
            and "hyde" not in spans_a
            and "subqueries" not in spans_a
            and "stepback" not in spans_a
            and "sentence_window" not in spans_a,
            "管线(b)：全部开关默认关闭 -> 注入与未注入的 span 集合一致（无增强 span）",
            f"a={spans_a} b={spans_b}",
        )
        ck.check(
            [rc.chunk.chunk_id for rc in res_a.chunks]
            == [rc.chunk.chunk_id for rc in res_b.chunks]
            and len(milvus_a.calls) == len(milvus_b.calls) == 1,
            "管线(b)：结果与 Milvus 调用次数一致（增强未生效）",
            f"a={[rc.chunk.chunk_id for rc in res_a.chunks]}",
        )

        # ------------------------------------------------------------------ #
        # 8. 管线接入 (c)：subqueries_on -> 子查询分别检索并去重合并
        # ------------------------------------------------------------------ #
        llm = StubLLM(result={"sub_queries": ["报销流程", "报销标准"]})
        subs = SubQueryGenerator(llm, SUBQUERIES_TEMPLATE, enabled=True)
        d1 = make_rc(make_chunk("d1", "d1 文本"), score=9.0, rank=0)
        d2 = make_rc(make_chunk("d2", "d2 文本"), score=8.0, rank=1)
        d3 = make_rc(make_chunk("d3", "d3 文本"), score=7.0, rank=2)
        milvus = StubMilvus(
            canned=[d1],
            canned_by_query={
                "报销流程": [d1, d2],
                "报销标准": [d3],
            },
        )
        pipeline = RetrievalPipeline(
            embedder=StubEmbedder(),
            milvus=milvus,
            reranker=StubReranker(),
            rewriter=StubRewriter("多主题复杂查询", True),
            router=StubRouter(RouteDecision(target="hybrid", confidence=0.9)),
            settings=PipelineSettings(
                subqueries_on=True, complexity_gate_on=False, top_k=8, enhance_candidate_k=8
            ),
            subqueries=subs,
        )
        result = pipeline.run("公司报销的流程和标准")
        span_names = {s.split(":")[0] for s in result.traces}
        ck.check(
            "subqueries" in span_names and len(milvus.calls) == 3,
            "管线(c)：subqueries_on -> 主查询 + 2 个子查询共 3 次检索",
            f"spans={span_names}, calls={len(milvus.calls)}",
        )
        ck.check(
            [rc.chunk.chunk_id for rc in result.chunks] == ["d1", "d2", "d3"],
            "管线(c)：子查询结果按 chunk_id 去重合并进候选",
            [rc.chunk.chunk_id for rc in result.chunks],
        )
        # 性能优化后子查询检索并发发起（线程池 fan-out），主查询仍同步先行，
        # 因此第 1 次调用固定为主查询；后 2 次调用的相对执行顺序不再保证
        # 与生成顺序一致（只校验集合，不校验顺序）——合并进候选的顺序仍
        # 由代码按生成顺序处理 future.result()，因此上面的去重合并顺序
        # 断言保持确定。
        call_texts = [c["query_text"] for c in milvus.calls]
        ck.check(
            call_texts[0] == "多主题复杂查询" and set(call_texts[1:]) == {"报销流程", "报销标准"},
            "管线(c)：各子查询分别检索（query_text 正确透传，主查询在前，子查询并发）",
            str(call_texts),
        )

        print()
        print(f"SMOKE ENHANCE PASSED (exit 0)  --  {ck.steps} 项断言全部通过")
        return 0

    except AssertionError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        print("SMOKE ENHANCE FAILED (exit 1)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
