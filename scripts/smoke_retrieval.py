"""检索模块离线冒烟测试。

完全离线：无网络、无模型、无 Milvus（全部使用确定性桩）。

覆盖断言：
(a) 简单短查询 -> 复杂度门控命中：不改写、不路由（LLM / 路由零调用），
    直接返回 hybrid（置信度 1.0）；
(b) source_diversity="group_only" -> FakeMilvus 收到 group_by_field="doc_id"
    与 group_size；
(c) source_diversity="group_mmr" -> MMR 后过滤生效（结果条数 <= k，
    文档多样性提升）；
(d) 路由目标为 vector_graph_rag 且图谱分支未实现 -> 保留 hybrid 结果，
    degraded=True，并记录降级 trace；
(e) 重排失败 -> 保持原始顺序，记录 trace。

运行：
    python scripts/smoke_retrieval.py

退出码：全部通过为 0，任一断言失败为 1。
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# 保证从任意工作目录运行都能找到 config / models / core / retrieval
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import PipelineSettings
from core.llm import LLMError
from core.reranker import RerankError
from core.tracing import trace_session
from models.schemas import Chunk, RetrievedChunk, RouteDecision
from retrieval.diversity import mmr_select
from retrieval.pipeline import RetrievalPipeline
from retrieval.rewrite import QueryRewriter
from retrieval.router import EmbeddingRouter, LlmRouterFallback, RouteResolver

REWRITE_TEMPLATE = PROJECT_ROOT / "prompts" / "query_rewrite_v1.txt"
ROUTER_TEMPLATE = PROJECT_ROOT / "prompts" / "router_llm_v1.txt"

_NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 确定性桩
# ---------------------------------------------------------------------------

class FakeEmbedder:
    """确定性哑嵌入：按文本内容映射到固定向量，无需任何模型。"""

    # 示例查询 -> 固定向量（与检索模块默认示例一一对应）
    _EXEMPLAR_MAP: dict[str, tuple[float, float, float]] = {
        "什么是Milvus": (1.0, 0.0, 0.0),
        "公司的报销流程是什么": (1.0, 0.0, 0.0),
        "谁在什么时间发明了什么": (0.0, 1.0, 0.0),
        "A和B是什么关系": (0.0, 1.0, 0.0),
        "帮我分析一下这个情况": (0.0, 0.0, 1.0),
        "解释一下这个问题的背景": (0.0, 0.0, 1.0),
    }

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)

    @classmethod
    def _vec(cls, text: str) -> list[float]:
        if text in cls._EXEMPLAR_MAP:
            return list(cls._EXEMPLAR_MAP[text])
        # 关键词规则：构造可预测的相似度场景
        if "关系" in text and "分析" in text:
            return [0.5, 0.5, 0.5]  # 与三路示例等距 -> 低置信度
        if "关系" in text:
            return [0.0, 1.0, 0.0]
        if "分析" in text or "背景" in text:
            return [0.0, 0.0, 1.0]
        return [1.0, 0.0, 0.0]


class FakeLLM:
    """LLM 桩：返回预设 JSON 或抛预设异常，记录调用次数。"""

    def __init__(self, result: dict | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    def chat_json(self, messages: list[dict], schema_hint: str | None = None) -> dict:
        self.calls += 1
        if self.error is not None:
            raise self.error
        if self.result is None:
            raise LLMError("桩未配置返回结果")
        return self.result


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
    """Milvus 桩：记录 hybrid_search 参数，返回预设候选（深拷贝，防污染）。"""

    def __init__(self, canned: list[RetrievedChunk]) -> None:
        self.canned = canned
        self.calls: list[dict] = []

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


class FakeReranker:
    """重排桩：默认保持候选顺序，可选反向或抛错。"""

    def __init__(self, fail: bool = False, reverse: bool = False) -> None:
        self.fail = fail
        self.reverse = reverse
        self.calls = 0

    def rerank(self, query: str, candidates: list[Chunk]) -> list[float]:
        self.calls += 1
        if self.fail:
            raise RerankError("桩：模拟重排失败")
        n = len(candidates)
        # 分数与候选同序；reverse 时分数递增 -> 排序后候选顺序反转
        return [float(i + 1) if self.reverse else float(n - i) for i in range(n)]


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

def make_chunk(doc_id: str, chunk_id: str, text: str, score: float) -> RetrievedChunk:
    chunk = Chunk(
        chunk_id=chunk_id,
        doc_id=doc_id,
        text=text,
        text_hash=f"hash-{chunk_id}",
        created_at=_NOW,
        updated_at=_NOW,
    )
    return RetrievedChunk(chunk=chunk, score=score, rank=0, branch="hybrid")


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
        # 0. 桩与真实客户端的签名契约（必须最先跑）
        # ------------------------------------------------------------------ #
        # 这一条不测产品代码，测的是**测试自己**。管线按鸭子类型注入 Milvus：
        # 真实客户端的 `hybrid_search` 加一个新形参，产品侧一切正常，而各冒烟
        # 脚本里手写死签名的桩会当场 `TypeError: unexpected keyword argument`，
        # 四个脚本一起红——看上去像产品代码坏了，实际是桩没跟上。
        #
        # 阶段 7 在 `delete_document` 的 `unregister` 形参上刚踩过同一个坑。
        # 所以放在 main 的最前面：真出现漂移时，先报出"桩缺少形参 X"这句人话，
        # 而不是让后面某个不相干的断言先炸出一片看不懂的堆栈。
        import inspect

        from core.milvus_client import RagMilvusClient

        real_params = set(
            inspect.signature(RagMilvusClient.hybrid_search).parameters
        ) - {"self"}
        missing = real_params - (
            set(inspect.signature(FakeMilvus.hybrid_search).parameters) - {"self"}
        )
        ck.check(
            not missing,
            "(0) FakeMilvus.hybrid_search 签名覆盖真实客户端的全部形参",
            f"桩缺少形参 {sorted(missing)}——真实客户端加了新参数，桩没跟上",
        )

        # ------------------------------------------------------------------ #
        # 1. QueryRewriter：复杂度门控与 LLM 改写
        # ------------------------------------------------------------------ #
        gate_llm = FakeLLM(error=AssertionError("复杂度门控不应调用 LLM"))
        rewriter = QueryRewriter(gate_llm, REWRITE_TEMPLATE, gate_enabled=True)
        out, changed = rewriter.rewrite("报销流程")
        ck.check(
            out == "报销流程" and changed is False,
            "复杂度门控：具体短查询不调用 LLM，原样返回",
            f"got ({out!r}, {changed})",
        )
        ck.check(gate_llm.calls == 0, "复杂度门控：LLM 零调用", f"calls={gate_llm.calls}")

        llm = FakeLLM(
            result={
                "rewritten_query": "公司的报销流程有哪些步骤",
                "changed": True,
                "reason": "补充范围",
            }
        )
        rewriter = QueryRewriter(llm, REWRITE_TEMPLATE, gate_enabled=True)
        out, changed = rewriter.rewrite("什么是Milvus")
        ck.check(
            out == "公司的报销流程有哪些步骤" and changed is True and llm.calls == 1,
            "复杂查询走 LLM 改写并解析 JSON",
            f"got ({out!r}, {changed}), calls={llm.calls}",
        )

        bad_llm = FakeLLM(error=LLMError("网络不可用"))
        rewriter = QueryRewriter(bad_llm, REWRITE_TEMPLATE, gate_enabled=True)
        out, changed = rewriter.rewrite("什么是Milvus")
        ck.check(
            out == "什么是Milvus" and changed is False,
            "LLM 失败静默降级为原文",
            f"got ({out!r}, {changed})",
        )

        # ------------------------------------------------------------------ #
        # 2. EmbeddingRouter：置信度与降级
        # ------------------------------------------------------------------ #
        embedder = FakeEmbedder()
        e_router = EmbeddingRouter(embedder, threshold=0.1)
        d = e_router.route("什么是Milvus")
        ck.check(
            d.target == "hybrid" and not d.degraded and abs(d.confidence - 1.0) < 1e-9,
            "嵌入路由：事实查询 -> hybrid 高置信度",
            str(d),
        )
        d = e_router.route("A和B是什么关系")
        ck.check(
            d.target == "vector_graph_rag" and not d.degraded,
            "嵌入路由：关系查询 -> vector_graph_rag",
            str(d),
        )
        d = e_router.route("帮我分析一下这个情况")
        ck.check(d.target == "full" and not d.degraded, "嵌入路由：模糊查询 -> full", str(d))
        d = e_router.route("帮我分析一下A和B的关系")
        ck.check(
            d.degraded is True,
            "嵌入路由：多路等距 -> 低置信度降级",
            str(d),
        )

        # ------------------------------------------------------------------ #
        # 3. LlmRouterFallback：LLM 分类与失败兜底
        # ------------------------------------------------------------------ #
        llm = FakeLLM(result={"route": "full", "confidence": 0.8, "reason": "模糊"})
        fallback = LlmRouterFallback(llm, ROUTER_TEMPLATE)
        d = fallback.route("帮我分析一下A和B的关系")
        ck.check(
            d.target == "full" and abs(d.confidence - 0.8) < 1e-9 and not d.degraded,
            "LLM 兜底：解析 JSON 路由决策",
            str(d),
        )
        bad_llm = FakeLLM(error=LLMError("超时"))
        fallback = LlmRouterFallback(bad_llm, ROUTER_TEMPLATE)
        d = fallback.route("任何查询")
        ck.check(
            d.target == "hybrid" and d.degraded is True,
            "LLM 兜底：失败降级为 degraded hybrid",
            str(d),
        )

        # ------------------------------------------------------------------ #
        # 4. RouteResolver：组合与异常安全
        # ------------------------------------------------------------------ #
        embedder = FakeEmbedder()
        llm = FakeLLM(result={"route": "full", "confidence": 0.8, "reason": "模糊"})
        resolver = RouteResolver(EmbeddingRouter(embedder), LlmRouterFallback(llm, ROUTER_TEMPLATE))
        d = resolver.route("帮我分析一下A和B的关系")
        ck.check(
            d.target == "full" and not d.degraded,
            "RouteResolver：嵌入降级后由 LLM 兜底",
            str(d),
        )
        resolver = RouteResolver(
            EmbeddingRouter(FakeEmbedder()),
            LlmRouterFallback(FakeLLM(error=LLMError("boom")), ROUTER_TEMPLATE),
        )
        d = resolver.route("帮我分析一下A和B的关系")
        ck.check(
            d.target == "hybrid" and d.degraded is True,
            "RouteResolver：LLM 失败不抛错，降级 hybrid",
            str(d),
        )

        # ------------------------------------------------------------------ #
        # 5. mmr_select：文档感知 MMR
        # ------------------------------------------------------------------ #
        mmr_items = [
            make_chunk("A", "a1", "a1", 1.0),
            make_chunk("A", "a2", "a2", 0.9),
            make_chunk("A", "a3", "a3", 0.8),
            make_chunk("B", "b1", "b1", 0.7),
            make_chunk("B", "b2", "b2", 0.6),
            make_chunk("C", "c1", "c1", 0.5),
        ]
        picked = mmr_select(mmr_items, lambda_=0.5, k=4)
        picked_ids = [rc.chunk.chunk_id for rc in picked]
        picked_docs = {rc.chunk.doc_id for rc in picked}
        ck.check(
            picked_ids == ["a1", "b1", "c1", "a2"],
            "MMR：贪心挑选顺序（相关度 + 多样性）",
            str(picked_ids),
        )
        ck.check(
            len(picked_docs) == 3 and len(picked) == 4,
            "MMR：文档多样性提升且条数 <= k",
            f"docs={picked_docs}",
        )
        ck.check(
            mmr_select(mmr_items, lambda_=0.5, k=4) == picked,
            "MMR：结果确定性（两次一致）",
        )
        ck.check(
            mmr_select(mmr_items, lambda_=0.5, k=0) == []
            and len(mmr_select(mmr_items, lambda_=0.5, k=99)) == 6,
            "MMR：边界（k=0 空，k 超长全量）",
        )

        # ------------------------------------------------------------------ #
        # 6. 管线场景 (a)：复杂度门控跳过改写与路由
        # ------------------------------------------------------------------ #
        gate_llm = FakeLLM(error=AssertionError("门控命中时不应调用 LLM"))
        gate_router = FakeRouter(RouteDecision(target="vector_graph_rag", confidence=0.9))
        canned = [make_chunk("D", "d1", "d1 文本", 0.9), make_chunk("D", "d2", "d2 文本", 0.8)]
        milvus = FakeMilvus(canned)
        pipeline = RetrievalPipeline(
            embedder=FakeEmbedder(),
            milvus=milvus,
            reranker=FakeReranker(),
            rewriter=QueryRewriter(gate_llm, REWRITE_TEMPLATE, gate_enabled=True),
            router=gate_router,
            settings=PipelineSettings(complexity_gate_on=True, source_diversity="off"),
        )
        with trace_session("smoke-a") as trace:
            result = pipeline.run("报销流程")
        ck.check(
            result.route.target == "hybrid"
            and abs(result.route.confidence - 1.0) < 1e-9
            and not result.route.degraded,
            "(a) 门控命中：路由为 hybrid 置信度 1.0",
            str(result.route),
        )
        ck.check(
            gate_llm.calls == 0 and len(gate_router.calls) == 0,
            "(a) 门控命中：LLM 与路由零调用",
            f"llm={gate_llm.calls}, router={len(gate_router.calls)}",
        )
        ck.check(len(result.chunks) == 2, "(a) 返回检索结果", f"n={len(result.chunks)}")
        span_names = {s.split(":")[0] for s in result.traces}
        ck.check(
            {"gate", "rewrite", "route", "embed", "search", "diversity", "graph", "rerank"} <= span_names,
            "(a) 八个阶段 span 均已记录",
            str(span_names),
        )
        ck.check(
            len(trace.spans) == 8,
            "(a) span 写入 current_trace（trace_session 生效）",
            f"n={len(trace.spans)}",
        )

        # ------------------------------------------------------------------ #
        # 7. 管线场景 (b)：group_only 透传分组参数
        # ------------------------------------------------------------------ #
        rewriter = FakeRewriter("简单报销流程", True)
        router = FakeRouter(RouteDecision(target="hybrid", confidence=0.9))
        milvus = FakeMilvus(canned)
        pipeline = RetrievalPipeline(
            embedder=FakeEmbedder(),
            milvus=milvus,
            reranker=FakeReranker(),
            rewriter=rewriter,
            router=router,
            settings=PipelineSettings(
                source_diversity="group_only",
                complexity_gate_on=False,
                group_by_field="doc_id",
                group_size=3,
            ),
        )
        result = pipeline.run("什么是Milvus")
        last_call = milvus.calls[-1]
        ck.check(
            last_call["group_by_field"] == "doc_id" and last_call["group_size"] == 3,
            "(b) group_only：FakeMilvus 收到分组参数",
            str(last_call),
        )
        ck.check(
            last_call["top_k"] == 16 and last_call["query_text"] == "简单报销流程",
            "(b) 候选放大 top_k*2 且使用改写后查询",
            str(last_call),
        )
        ck.check(
            len(rewriter.calls) == 1 and router.calls == ["简单报销流程"],
            "(b) 改写与路由按序执行",
            f"rewriter={rewriter.calls}, router={router.calls}",
        )

        # ------------------------------------------------------------------ #
        # 8. 管线场景 (c)：group_mmr 提升文档多样性
        # ------------------------------------------------------------------ #
        milvus = FakeMilvus(mmr_items)
        pipeline = RetrievalPipeline(
            embedder=FakeEmbedder(),
            milvus=milvus,
            reranker=FakeReranker(),
            rewriter=FakeRewriter("关系查询", True),
            router=FakeRouter(RouteDecision(target="hybrid", confidence=0.9)),
            settings=PipelineSettings(
                source_diversity="group_mmr",
                complexity_gate_on=False,
                top_k=2,
                mmr_lambda=0.5,
            ),
        )
        result = pipeline.run("A和B是什么关系")
        ck.check(
            milvus.calls[-1]["group_by_field"] == "doc_id",
            "(c) group_mmr：分组参数透传",
            str(milvus.calls[-1]),
        )
        ck.check(
            len(result.chunks) == 2
            and result.chunks[0].chunk.doc_id != result.chunks[1].chunk.doc_id,
            "(c) MMR 生效：结果条数 <= top_k 且文档不重复",
            [rc.chunk.chunk_id for rc in result.chunks],
        )

        # ------------------------------------------------------------------ #
        # 9. 管线场景 (d)：vector_graph_rag 降级为 hybrid 结果
        # ------------------------------------------------------------------ #
        canned4 = [
            make_chunk("D", "c0", "c0 文本", 4.0),
            make_chunk("D", "c1", "c1 文本", 3.0),
            make_chunk("D", "c2", "c2 文本", 2.0),
            make_chunk("D", "c3", "c3 文本", 1.0),
        ]
        milvus = FakeMilvus(canned4)
        pipeline = RetrievalPipeline(
            embedder=FakeEmbedder(),
            milvus=milvus,
            reranker=FakeReranker(reverse=True),
            rewriter=FakeRewriter("关系查询", True),
            router=FakeRouter(RouteDecision(target="vector_graph_rag", confidence=0.9)),
            settings=PipelineSettings(source_diversity="off", complexity_gate_on=False),
        )
        result = pipeline.run("谁在什么时间发明了什么")
        ck.check(
            result.route.target == "vector_graph_rag" and result.route.degraded is True,
            "(d) 图谱分支未实现：degraded=True",
            str(result.route),
        )
        ck.check(
            len(result.chunks) == 4 and result.chunks[0].chunk.chunk_id == "c3",
            "(d) 保留 hybrid 检索结果且重排生效（顺序反转）",
            [rc.chunk.chunk_id for rc in result.chunks],
        )
        ck.check(
            any("降级 hybrid" in t for t in result.traces),
            "(d) 记录降级 trace",
            str(result.traces),
        )

        # ------------------------------------------------------------------ #
        # 10. 管线场景 (e)：重排失败保持原始顺序
        # ------------------------------------------------------------------ #
        canned3 = [
            make_chunk("E", "e0", "e0 文本", 3.0),
            make_chunk("E", "e1", "e1 文本", 2.0),
            make_chunk("E", "e2", "e2 文本", 1.0),
        ]
        milvus = FakeMilvus(canned3)
        pipeline = RetrievalPipeline(
            embedder=FakeEmbedder(),
            milvus=milvus,
            reranker=FakeReranker(fail=True),
            rewriter=FakeRewriter("简单查询", True),
            router=FakeRouter(RouteDecision(target="hybrid", confidence=0.9)),
            settings=PipelineSettings(source_diversity="off", complexity_gate_on=False),
        )
        result = pipeline.run("报销流程")
        ck.check(
            [rc.chunk.chunk_id for rc in result.chunks] == ["e0", "e1", "e2"]
            and [rc.score for rc in result.chunks] == [3.0, 2.0, 1.0],
            "(e) 重排失败：保持原始顺序与分数",
            [rc.chunk.chunk_id for rc in result.chunks],
        )
        ck.check(
            any("rerank 失败" in t for t in result.traces),
            "(e) 记录重排失败 trace",
            str(result.traces),
        )

        # ------------------------------------------------------------------ #
        # 10b. 管线场景 (e2)：重排熔断——连续失败后**不再调用**重排器
        #
        # 光有"失败即降级"还不够：重排器真挂了的时候，每个请求依然要付满
        # 超时×重试才轮到降级。熔断要保证的是——开了之后连调都不调。
        # ------------------------------------------------------------------ #
        from core.circuit import CircuitBreaker, CircuitConfig

        cb_reranker = FakeReranker(fail=True)
        cb = CircuitBreaker(
            "reranker-smoke", CircuitConfig(failure_threshold=2, cooldown_s=60.0)
        )
        cb_pipeline = RetrievalPipeline(
            embedder=FakeEmbedder(),
            milvus=FakeMilvus(canned3),
            reranker=cb_reranker,
            rewriter=FakeRewriter("简单查询", True),
            router=FakeRouter(RouteDecision(target="hybrid", confidence=0.9)),
            settings=PipelineSettings(source_diversity="off", complexity_gate_on=False),
            reranker_cb=cb,
        )
        for _ in range(2):
            cb_pipeline.run("报销流程")
        ck.check(cb.state() == "open", "(e2) 连续 2 次失败后熔断打开", cb.state())
        calls_before = cb_reranker.calls
        result_open = cb_pipeline.run("报销流程")
        ck.check(
            cb_reranker.calls == calls_before,
            "(e2) 熔断打开后不再调用重排器（省掉白等的超时）",
            f"calls {calls_before} -> {cb_reranker.calls}",
        )
        ck.check(
            any("熔断" in t for t in result_open.traces),
            "(e2) trace 说明是熔断跳过，而非静默",
            str(result_open.traces),
        )
        ck.check(
            [rc.chunk.chunk_id for rc in result_open.chunks] == ["e0", "e1", "e2"],
            "(e2) 熔断跳过后仍返回召回顺序（降级不丢结果）",
            [rc.chunk.chunk_id for rc in result_open.chunks],
        )

        # ------------------------------------------------------------------ #
        # 11. 管线场景 (f)：ACL 过滤表达式构造
        # ------------------------------------------------------------------ #
        milvus = FakeMilvus(canned3)
        pipeline = RetrievalPipeline(
            embedder=FakeEmbedder(),
            milvus=milvus,
            reranker=FakeReranker(),
            rewriter=FakeRewriter("简单查询", True),
            router=FakeRouter(RouteDecision(target="hybrid", confidence=0.9)),
            settings=PipelineSettings(source_diversity="off", complexity_gate_on=False),
        )
        pipeline.run("报销流程", acl=["fin", "legal"])
        expr = milvus.calls[-1]["filter_expr"]
        ck.check(
            isinstance(expr, str)
            and "tenant_id" in expr
            and "acl IN ['fin', 'legal']" in expr
            and expr.index("tenant_id") < expr.index("acl"),
            "(f) ACL + 租户过滤表达式组合传给 Milvus（tenant 在前，AND 拼接）",
            str(expr),
        )

        # ------------------------------------------------------------------ #
        # (g) 桩与真实客户端的签名契约
        # ------------------------------------------------------------------ #
        print()
        print(f"SMOKE RETRIEVAL PASSED (exit 0)  --  {ck.steps} 项断言全部通过")
        return 0

    except AssertionError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        print("SMOKE RETRIEVAL FAILED (exit 1)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
