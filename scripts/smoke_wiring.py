"""生产装配冒烟测试：验证可插拔策略真的被注入到生产管线。

**这个脚本存在的理由**：本项目的组件、配置开关、管线判断分处三地，
判断一律写成 ``if 开关 and 组件 is not None``。历史上出现过「组件写好了、
开关也读了，但生产装配点从没把组件传进去」的缺口——配置里打开策略，
运行时却毫无效果，且所有既有 smoke 都是绿的（因为它们直接用桩构造管线，
绕过了生产装配函数）。本脚本直接测那两个装配函数，堵住这个盲区。

覆盖：
- ``rag._build_optional_components``：五个检索端策略按开关注入 / 关闭时为 None
- ``server.documents._build_ingest_optional``：三个入库端策略同上
- ``RetrievalPipeline``：``hybrid_search_on`` / ``rerank_on`` 两个开关真的生效
- ``verify`` 段：验证强度（蕴含判定 / 严格模式 / 抽样比例）传到 CitationVerifier，
  且非法值兜底为最严格
- ``milvus.ef``：HNSW 检索期候选宽度真的下发，且满足 ef >= limit 约束
- ``observability.tracing_enabled``：关闭后 current_trace() 返回 None
- 配置热更新：``rag.reset_pipeline`` / ``documents.reset_ingest_pipelines`` 使缓存失效

完全离线：不联网、不加载模型、不连 Milvus。装配函数只构造对象
（LLM 客户端 / 向量库客户端均为惰性连接），检索开关部分用确定性桩。

运行：
    python scripts/smoke_wiring.py

退出码：全部通过为 0，任一断言失败为 1。
"""
from __future__ import annotations

import os
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import PipelineSettings, get_settings  # noqa: E402
from models.schemas import Chunk, RetrievedChunk, RouteDecision  # noqa: E402
from retrieval.pipeline import RetrievalPipeline  # noqa: E402

_NOW = datetime.now(timezone.utc)

# 本脚本会改动的环境变量（结束时恢复，避免污染同进程后续调用）
_FLAG_ENV = (
    "RAG4C_PIPELINE_HYDE_ON",
    "RAG4C_PIPELINE_SUBQUERIES_ON",
    "RAG4C_PIPELINE_STEPBACK_ON",
    "RAG4C_PIPELINE_SENTENCE_WINDOW_ON",
    "RAG4C_PIPELINE_GRAPH_RETRIEVAL_ON",
    "RAG4C_PIPELINE_CLEAN_ON",
    "RAG4C_PIPELINE_CONTEXTUAL_ON",
    "RAG4C_PIPELINE_GRAPH_INDEX_ON",
    "RAG4C_VERIFY_ENTAILMENT_MODE",
    "RAG4C_VERIFY_STRICT",
    "RAG4C_VERIFY_SAMPLE_RATIO",
)


# ---------------------------------------------------------------------------
# 断言工具（与其余 smoke 脚本一致）
# ---------------------------------------------------------------------------

class Checker:
    """逐步断言：失败立即抛出，由 main 捕获并置退出码 1。"""

    def __init__(self) -> None:
        self.steps = 0

    def check(self, cond: bool, name: str, detail: Any = "") -> None:
        if not cond:
            raise AssertionError(f"{name} 失败：{detail}")
        self.steps += 1
        print(f"[PASS] {name}")


def _set_flags(value: bool, *names: str) -> None:
    """批量设置开关环境变量并让 Settings 缓存失效。"""
    for name in names:
        os.environ[name] = "true" if value else "false"
    get_settings.cache_clear()


def _set_env(**pairs: str) -> None:
    """设置任意环境变量（字符串值）并让 Settings 缓存失效。"""
    for name, value in pairs.items():
        os.environ[name] = value
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 检索开关部分用的确定性桩
# ---------------------------------------------------------------------------

def make_chunk(chunk_id: str, text: str) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        doc_id=f"doc-{chunk_id}",
        text=text,
        text_hash=f"hash-{chunk_id}",
        created_at=_NOW,
        updated_at=_NOW,
    )


class StubMilvus:
    """记录 hybrid_search 收到的参数（重点看 query_text 是否为 None）。"""

    def __init__(self, canned: list[RetrievedChunk]) -> None:
        self.canned = canned
        self.calls: list[dict] = []

    def hybrid_search(
        self,
        query_dense: Any,
        top_k: int,
        query_text: str | None = None,
        group_by_field: str | None = None,
        group_size: int | None = None,
        filter_expr: str | None = None,
        with_cosine: bool = False,
    ) -> list[RetrievedChunk]:
        self.calls.append(
            {"query_text": query_text, "top_k": top_k, "with_cosine": with_cosine}
        )
        return [rc.model_copy(deep=True) for rc in self.canned]

    @staticmethod
    def build_acl_filter(acls: list[str]) -> str:
        return "acl IN []"

    @staticmethod
    def build_tenant_filter(tenant_id: str) -> str | None:
        return None

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
    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0]] * len(texts)


class StubReranker:
    """把顺序反转的重排器：调用与否一眼可辨。"""

    def __init__(self) -> None:
        self.calls = 0

    def rerank(self, query: str, candidates: list[Chunk]) -> list[float]:
        self.calls += 1
        return [float(i) for i in range(len(candidates))]


class StubRewriter:
    def rewrite(self, query: str) -> tuple[str, bool]:
        return query, False


class StubRouter:
    def route(self, query: str) -> RouteDecision:
        return RouteDecision(target="hybrid", confidence=0.9)


def _build_stub_pipeline(**flags: Any) -> tuple[StubMilvus, StubReranker, RetrievalPipeline]:
    """构造一条全桩检索管线；flags 直接覆盖 PipelineSettings 的开关。"""
    milvus = StubMilvus(
        canned=[
            RetrievedChunk(chunk=make_chunk("d1", "第一段"), score=9.0, rank=0),
            RetrievedChunk(chunk=make_chunk("d2", "第二段"), score=8.0, rank=1),
            RetrievedChunk(chunk=make_chunk("d3", "第三段"), score=7.0, rank=2),
        ]
    )
    reranker = StubReranker()
    pipeline = RetrievalPipeline(
        embedder=StubEmbedder(),
        milvus=milvus,
        reranker=reranker,
        rewriter=StubRewriter(),
        router=StubRouter(),
        settings=PipelineSettings(complexity_gate_on=False, top_k=8, **flags),
    )
    return milvus, reranker, pipeline


# ---------------------------------------------------------------------------

def main() -> int:
    ck = Checker()
    saved = {name: os.environ.get(name) for name in _FLAG_ENV}
    try:
        # ------------------------------------------------------------------ #
        # 1. 检索端装配：全部开关打开 -> 五个策略组件都应被注入
        # ------------------------------------------------------------------ #
        import rag

        _set_flags(
            True,
            "RAG4C_PIPELINE_HYDE_ON",
            "RAG4C_PIPELINE_SUBQUERIES_ON",
            "RAG4C_PIPELINE_STEPBACK_ON",
            "RAG4C_PIPELINE_SENTENCE_WINDOW_ON",
            "RAG4C_PIPELINE_GRAPH_RETRIEVAL_ON",
        )
        # 装配函数只构造对象，embedder / milvus 仅被透传，用哑对象即可离线运行
        optional = rag._build_optional_components(get_settings(), object(), object())
        for key in ("hyde", "subqueries", "stepback", "sentence_window", "graph_retriever"):
            ck.check(
                optional[key] is not None,
                "检索装配：开关打开时 {} 已注入".format(key),
                "got None（开关生效但组件未构造，配置改了也不会有效果）",
            )

        # ------------------------------------------------------------------ #
        # 2. 检索端装配：全部关闭 -> 全部为 None（关闭即零开销）
        # ------------------------------------------------------------------ #
        _set_flags(
            False,
            "RAG4C_PIPELINE_HYDE_ON",
            "RAG4C_PIPELINE_SUBQUERIES_ON",
            "RAG4C_PIPELINE_STEPBACK_ON",
            "RAG4C_PIPELINE_SENTENCE_WINDOW_ON",
            "RAG4C_PIPELINE_GRAPH_RETRIEVAL_ON",
        )
        optional_off = rag._build_optional_components(get_settings(), object(), object())
        ck.check(
            all(v is None for v in optional_off.values()),
            "检索装配：开关关闭时不构造任何组件（零开销）",
            optional_off,
        )

        # ------------------------------------------------------------------ #
        # 3. 入库端装配：开关打开 -> 三个策略组件都应被注入
        # ------------------------------------------------------------------ #
        from server import documents

        _set_flags(
            True,
            "RAG4C_PIPELINE_CLEAN_ON",
            "RAG4C_PIPELINE_CONTEXTUAL_ON",
            "RAG4C_PIPELINE_GRAPH_INDEX_ON",
        )
        ingest_opt = documents._build_ingest_optional(get_settings())
        for key in ("cleaner", "contextualizer", "graph_builder"):
            ck.check(
                ingest_opt[key] is not None,
                f"入库装配：开关打开时 {key} 已注入",
                "got None（入库策略不会生效）",
            )

        # ------------------------------------------------------------------ #
        # 4. 入库端装配：全部关闭 -> 全部为 None
        # ------------------------------------------------------------------ #
        _set_flags(
            False,
            "RAG4C_PIPELINE_CLEAN_ON",
            "RAG4C_PIPELINE_CONTEXTUAL_ON",
            "RAG4C_PIPELINE_GRAPH_INDEX_ON",
        )
        ingest_off = documents._build_ingest_optional(get_settings())
        ck.check(
            all(v is None for v in ingest_off.values()),
            "入库装配：开关关闭时不构造任何组件（零开销）",
            ingest_off,
        )

        # ------------------------------------------------------------------ #
        # 5. hybrid_search_on 开关真的生效
        # ------------------------------------------------------------------ #
        milvus_on, _, pipe_on = _build_stub_pipeline(hybrid_search_on=True)
        pipe_on.run("公司的报销流程")
        ck.check(
            milvus_on.calls[0]["query_text"] == "公司的报销流程",
            "hybrid_search_on=True：向 Milvus 传入 query_text（BM25 分支参与）",
            milvus_on.calls[0],
        )

        milvus_off, _, pipe_off = _build_stub_pipeline(hybrid_search_on=False)
        result_off = pipe_off.run("公司的报销流程")
        ck.check(
            milvus_off.calls[0]["query_text"] is None,
            "hybrid_search_on=False：不传 query_text（降级为纯稠密检索）",
            milvus_off.calls[0],
        )
        ck.check(
            any("hybrid_search 关闭" in t for t in result_off.traces),
            "hybrid_search_on=False：记录降级 trace",
            result_off.traces,
        )

        # ------------------------------------------------------------------ #
        # 6. rerank_on 开关真的生效
        # ------------------------------------------------------------------ #
        _, reranker_on, pipe_r_on = _build_stub_pipeline(rerank_on=True)
        res_r_on = pipe_r_on.run("查询")
        ck.check(
            reranker_on.calls == 1,
            "rerank_on=True：重排器被调用",
            reranker_on.calls,
        )
        ck.check(
            [rc.chunk.chunk_id for rc in res_r_on.chunks] == ["d3", "d2", "d1"],
            "rerank_on=True：结果按重排分数重新排序",
            [rc.chunk.chunk_id for rc in res_r_on.chunks],
        )

        _, reranker_off, pipe_r_off = _build_stub_pipeline(rerank_on=False)
        res_r_off = pipe_r_off.run("查询")
        ck.check(
            reranker_off.calls == 0,
            "rerank_on=False：重排器零调用（真正省下这笔开销）",
            reranker_off.calls,
        )
        ck.check(
            [rc.chunk.chunk_id for rc in res_r_off.chunks] == ["d1", "d2", "d3"],
            "rerank_on=False：保留召回顺序",
            [rc.chunk.chunk_id for rc in res_r_off.chunks],
        )
        ck.check(
            any("rerank 关闭" in t for t in res_r_off.traces),
            "rerank_on=False：记录 trace",
            res_r_off.traces,
        )

        # ------------------------------------------------------------------ #
        # 7. verify 段：验证强度传到 CitationVerifier
        # ------------------------------------------------------------------ #
        from verify.verifier import resolve_verify_settings

        _set_env(
            RAG4C_VERIFY_ENTAILMENT_MODE="skip",
            RAG4C_VERIFY_STRICT="false",
            RAG4C_VERIFY_SAMPLE_RATIO="0.5",
        )
        mode, strict, ratio = resolve_verify_settings(get_settings())
        ck.check(
            (mode, strict, ratio) == ("skip", False, 0.5),
            "verify 段：entailment_mode / strict / sample_ratio 正确读出",
            (mode, strict, ratio),
        )

        # 非法值兜底：不能让配置写错拖垮整条管线的装配
        _set_env(
            RAG4C_VERIFY_ENTAILMENT_MODE="nonsense",
            RAG4C_VERIFY_SAMPLE_RATIO="9.9",
        )
        mode_bad, _, ratio_bad = resolve_verify_settings(get_settings())
        ck.check(
            mode_bad == "llm" and ratio_bad == 1.0,
            "verify 段：非法值兜底为最严格（llm / 全量），不抛异常",
            (mode_bad, ratio_bad),
        )

        _set_env(
            RAG4C_VERIFY_ENTAILMENT_MODE="llm",
            RAG4C_VERIFY_STRICT="true",
            RAG4C_VERIFY_SAMPLE_RATIO="1.0",
        )

        # ------------------------------------------------------------------ #
        # 8. milvus.ef：HNSW 检索期候选宽度真的下发
        # ------------------------------------------------------------------ #
        from config.settings import MilvusSettings
        from core.milvus_client import RagMilvusClient

        captured: dict[str, Any] = {}

        class _FakeAnnRequest:
            def __init__(self, **kwargs: Any) -> None:
                # 只捕获稠密分支（anns_field=dense_vector）的检索参数
                if kwargs.get("anns_field") == "dense_vector":
                    captured["param"] = kwargs.get("param")
                    captured["limit"] = kwargs.get("limit")

        class _FakeClient:
            def hybrid_search(self, **kwargs: Any) -> list[list[dict]]:
                captured["ranker"] = kwargs.get("ranker")
                return [[]]

        client = RagMilvusClient(MilvusSettings(index_type="HNSW", ef=64, candidate_factor=4))
        client._client = _FakeClient()
        # hybrid_search 内部 `from pymilvus import AnnSearchRequest` 与
        # `from pymilvus import Function, FunctionType`，用一个假的 pymilvus
        # 模块顶替，避免依赖真实安装。
        class _FakeFunction:
            def __init__(self, **kwargs: Any) -> None:
                self.kwargs = kwargs

        class _FakeFunctionType:
            RERANK = "RERANK"
            BM25 = "BM25"

        fake_pymilvus = types.ModuleType("pymilvus")
        fake_pymilvus.AnnSearchRequest = _FakeAnnRequest  # type: ignore[attr-defined]
        fake_pymilvus.Function = _FakeFunction  # type: ignore[attr-defined]
        fake_pymilvus.FunctionType = _FakeFunctionType  # type: ignore[attr-defined]
        saved_pymilvus = sys.modules.get("pymilvus")
        sys.modules["pymilvus"] = fake_pymilvus
        try:
            client.hybrid_search(query_dense=[0.1, 0.2], top_k=8, query_text="x")
        finally:
            if saved_pymilvus is None:
                sys.modules.pop("pymilvus", None)
            else:
                sys.modules["pymilvus"] = saved_pymilvus

        ck.check(
            captured.get("param", {}).get("params", {}).get("ef") is not None,
            "milvus.ef：HNSW 索引下 ef 已下发到 param['params']（pymilvus 约定的嵌套层级）",
            captured.get("param"),
        )
        ck.check(
            captured["param"]["params"]["ef"] >= captured["limit"],
            "milvus.ef：满足 Milvus 的 ef >= limit 约束（自动取上界）",
            (captured["param"]["params"].get("ef"), captured.get("limit")),
        )

        # ---- 8b. RRF 融合排序器用的是 Milvus 3.0 的 Function 写法 -------- #
        # 旧的 RRFRanker 类属于 ORM 风格 API，计划在 PyMilvus 3.1 移除。
        # 这里断言三件事：类型是 RERANK、reranker 选的是 rrf、input_field_names
        # 是空列表（RRF 只看名次不读字段，传字段名会被服务端拒绝）。
        ranker_kwargs = getattr(captured.get("ranker"), "kwargs", {})
        ck.check(
            ranker_kwargs.get("function_type") == "RERANK",
            "milvus ranker：用 3.0 的 FunctionType.RERANK 写法（而非 RRFRanker 类）",
            ranker_kwargs.get("function_type"),
        )
        ck.check(
            (ranker_kwargs.get("params") or {}).get("reranker") == "rrf",
            "milvus ranker：params.reranker == 'rrf'",
            ranker_kwargs.get("params"),
        )
        ck.check(
            ranker_kwargs.get("input_field_names") == [],
            "milvus ranker：input_field_names 为空列表（RRF 不读字段）",
            ranker_kwargs.get("input_field_names"),
        )
        ck.check(
            (ranker_kwargs.get("params") or {}).get("k") == MilvusSettings().rrf_k,
            "milvus ranker：rrf_k 配置真的下发到 params.k",
            (ranker_kwargs.get("params") or {}).get("k"),
        )

        # IVF 索引不应下发 ef，而是 nprobe
        captured.clear()
        client_ivf = RagMilvusClient(MilvusSettings(index_type="IVF_FLAT", nprobe=16))
        client_ivf._client = _FakeClient()
        sys.modules["pymilvus"] = fake_pymilvus
        try:
            client_ivf.hybrid_search(query_dense=[0.1], top_k=8, query_text="x")
        finally:
            if saved_pymilvus is None:
                sys.modules.pop("pymilvus", None)
            else:
                sys.modules["pymilvus"] = saved_pymilvus
        ck.check(
            "ef" not in captured.get("param", {}).get("params", {})
            and "nprobe" in captured.get("param", {}).get("params", {}),
            "milvus.ef：IVF 索引下只发 nprobe，不发 ef",
            captured.get("param"),
        )

        # ------------------------------------------------------------------ #
        # 9. tracing_enabled：关闭后不再记录 span
        # ------------------------------------------------------------------ #
        from core.tracing import current_trace, set_tracing_enabled, trace_session

        set_tracing_enabled(True)
        with trace_session("q-on"):
            ck.check(
                current_trace() is not None,
                "tracing_enabled=True：current_trace() 返回上下文",
            )
        set_tracing_enabled(False)
        with trace_session("q-off"):
            ck.check(
                current_trace() is None,
                "tracing_enabled=False：current_trace() 返回 None（span 记录短路）",
            )
        set_tracing_enabled(True)

        # ------------------------------------------------------------------ #
        # 10. 配置热更新：管线缓存真的失效
        # ------------------------------------------------------------------ #
        from server import documents as documents_module

        rag.reset_pipeline(close=False)
        ck.check(
            rag._pipeline is None,
            "reset_pipeline(close=False)：检索管线缓存已置空（下次请求按新配置重建）",
        )

        gen_before = documents_module._pipeline_generation
        documents_module.reset_ingest_pipelines()
        ck.check(
            documents_module._pipeline_generation == gen_before + 1,
            "reset_ingest_pipelines()：代次递增，各工作线程下次取用时重建",
            (gen_before, documents_module._pipeline_generation),
        )

        print()
        print(f"SMOKE WIRING PASSED (exit 0)  --  {ck.steps} 项断言全部通过")
        return 0

    except AssertionError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        print("SMOKE WIRING FAILED (exit 1)", file=sys.stderr)
        return 1
    finally:
        # 恢复环境变量与配置缓存，避免污染同进程内的后续调用
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        get_settings.cache_clear()


if __name__ == "__main__":
    sys.exit(main())
