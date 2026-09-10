"""RAG4C 顶层编排器（系统对外唯一入口）。

``answer_query`` 编排完整链路（任务书 1.x / 2.x）：

    gate -> rewrite -> router -> retrieve -> generate -> verify -> abstain

关键行为：
- **图编排优先**：``pipeline.graph_engine_on=True``（默认）时优先走
  LangGraph StateGraph 图编排（:mod:`rag_graph`）；图构建失败 / 图执行
  异常 / langgraph 未安装时自动回退到本模块的顺序编排
  （:func:`_answer_sequential`）。两种编排对外行为完全一致。
- **复杂度门控**：具体查询跳过改写与路由（零 LLM 成本），由
  :class:`retrieval.pipeline.RetrievalPipeline` 内部完成。
- **意图路由**：嵌入相似度为主、LLM 兜底；``vector_graph_rag`` / ``full``
  目标在图谱分支（预留未实现）自动降级为 hybrid 结果。
- **二轮检索**：第一轮生成 + 验证后若声明缺失证据或未被证据支撑，
  用原始查询重跑一次检索并合并两轮证据，再生成 + 验证一轮（至多 2 轮）。
- **弃权**：双重阈值（检索分数 / 蕴含分数）由
  :class:`verify.abstention.AbstentionGate` 判定，弃权时返回空答案。
- **鲁棒性**：检索失败（如 Milvus 不可用）或生成失败均降级为弃权，
  不向调用方抛出异常。

所有组件懒加载且构造不联网、不加载模型，因此 ``import rag`` 离线安全；
真正的模型 / 向量库连接发生在首次实际调用时。

用法::

    from rag import answer_query
    result = answer_query("公司的报销流程是什么？", acl=["fin"])
"""
from __future__ import annotations

import uuid
from pathlib import Path
import threading
from typing import Any

from config.settings import Settings, get_settings
from core.embedding import create_embedder
from core.llm import create_client
from core.metrics import get_metrics
from core.milvus_client import RagMilvusClient
from core.observability import bind_query_id, get_logger, setup_observability
from core.reranker import create_reranker
from core.tracing import trace_session
from generation.generator import Generator
from models.schemas import QueryResult, RetrievedChunk
from rag_common import (
    MAX_ROUNDS,
    evidence_chunks,
    generate_and_verify,
    merge_chunks,
    resolve_tenant,
    second_round_can_help,
)
from retrieval.pipeline import RetrievalPipeline, dense_cosines_of
from retrieval.rewrite import QueryRewriter
from retrieval.router import EmbeddingRouter, LlmRouterFallback, RouteResolver
from verify.abstention import AbstentionGate
from verify.verifier import CitationVerifier, resolve_verify_settings

# 项目根目录：本文件位于 <root>/rag.py
_PROJECT_ROOT = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# 组件装配（懒加载 + 进程级缓存）
# ---------------------------------------------------------------------------

def get_pipeline(settings: Settings | None = None) -> dict[str, Any]:
    """构建并缓存全链路组件（懒加载，构造不联网、不加载模型）。

    Args:
        settings: Settings 实例；None 时使用进程级单例
            :func:`config.settings.get_settings`。

    Returns:
        组件字典，键：``retrieval`` / ``generator`` / ``verifier`` /
        ``gate`` / ``settings``。
    """
    global _pipeline
    if settings is not None:
        return _build_pipeline(settings)
    if _pipeline is not None:
        return _pipeline
    with _pipeline_lock:
        if _pipeline is None:
            _pipeline = _build_pipeline(get_settings())
        return _pipeline


_pipeline: dict[str, Any] | None = None
_pipeline_lock = threading.Lock()


def _build_optional_components(
    settings: Settings,
    embedder: Any,
    milvus: RagMilvusClient,
) -> dict[str, Any]:
    """构造可插拔的查询端 / 检索端增强组件（全部容错，失败降级为 None）。

    每个组件只在对应开关打开时构造：关闭时返回 None，管线里的
    ``if 开关 and 组件 is not None`` 判断自然短路，零开销。

    构造本身不联网、不加载模型（LLM 客户端与模板均为惰性），但仍逐个
    包在 try 里——任一组件构造失败（模板缺失、可选依赖未装等）只让该
    策略退化为不可用，绝不影响其余策略与主链路可用性。

    Returns:
        ``{"hyde": ..., "subqueries": ..., "stepback": ...,
        "sentence_window": ..., "graph_retriever": ...}``，值可能为 None。
    """
    p = settings.pipeline
    log = get_logger(__name__)
    components: dict[str, Any] = {
        "hyde": None,
        "subqueries": None,
        "stepback": None,
        "sentence_window": None,
        "graph_retriever": None,
        "auto_filter": None,
    }

    def _try(name: str, factory: Any) -> None:
        try:
            components[name] = factory()
        except Exception as exc:  # noqa: BLE001 - 单个策略不可用不影响主链路
            log.warning("可选策略 %s 装配失败，本次运行按关闭处理: %s", name, exc)

    if p.hyde_on:
        from retrieval.query_enhance import HydeGenerator

        _try(
            "hyde",
            lambda: HydeGenerator(
                llm_client=create_client(settings.llm.hyde),
                template_path=_PROJECT_ROOT / "prompts" / "hyde_v1.txt",
                enabled=True,
            ),
        )

    if p.subqueries_on:
        from retrieval.query_enhance import SubQueryGenerator

        _try(
            "subqueries",
            lambda: SubQueryGenerator(
                llm_client=create_client(settings.llm.subqueries),
                template_path=_PROJECT_ROOT / "prompts" / "subqueries_v1.txt",
                enabled=True,
            ),
        )

    if p.stepback_on:
        from retrieval.query_enhance import StepbackGenerator

        _try(
            "stepback",
            lambda: StepbackGenerator(
                llm_client=create_client(settings.llm.stepback),
                template_path=_PROJECT_ROOT / "prompts" / "stepback_v1.txt",
                enabled=True,
            ),
        )

    if p.sentence_window_on:
        from retrieval.sentence_window import SentenceWindowExpander

        # 依赖入库期的父子块切分：只有 parent_child 模式会写 parent_chunk_id，
        # 用 recursive / qa 切分入库的文档展开时取不到子块，组件内部会原样返回。
        _try(
            "sentence_window",
            lambda: SentenceWindowExpander(milvus=milvus, replace=True, enabled=True),
        )

    if p.graph_retrieval_on:
        from core.graph_store import RagGraphStore
        from retrieval.graph_retriever import GraphRetriever

        _try(
            "graph_retriever",
            lambda: GraphRetriever(
                store=RagGraphStore(settings.milvus, settings.graph),
                embedder=embedder,
                # 图谱关系重排复用 judge 槽位（温度 0，确定性输出）；
                # graph.use_llm_rerank 关闭时不传 LLM，按检索分数排序。
                llm=create_client(settings.llm.judge) if settings.graph.use_llm_rerank else None,
            ),
        )

    if getattr(settings.catalog, "auto_filter_on", False):
        from retrieval.auto_filter import create_auto_filter

        _try("auto_filter", lambda: create_auto_filter(settings))

    return components


def _build_pipeline(settings: Settings) -> dict[str, Any]:
    """Build one component graph; callers control its lifecycle."""
    embedder = create_embedder(settings.embedding)
    milvus = RagMilvusClient(settings.milvus)
    reranker = create_reranker(settings.reranker)
    rewriter = QueryRewriter(
        llm_client=create_client(settings.llm.rewrite),
        template_path=_PROJECT_ROOT / "prompts" / "query_rewrite_v1.txt",
        gate_enabled=settings.pipeline.complexity_gate_on,
    )
    router = RouteResolver(
        embedding_router=EmbeddingRouter(embedder=embedder),
        llm_fallback=LlmRouterFallback(
            llm_client=create_client(settings.llm.router_llm),
            template_path=_PROJECT_ROOT / "prompts" / "router_llm_v1.txt",
        ),
    )
    # 可插拔增强策略：按 pipeline 段开关装配，关闭 / 装配失败即为 None
    optional = _build_optional_components(settings, embedder, milvus)
    # 重排熔断器在此构造（CircuitBreaker.__init__ 自动注册进全局表，
    # /api/health 与监控页因此能一并看到它的状态）。
    from core.circuit import CircuitBreaker as _CircuitBreaker
    from core.circuit import CircuitConfig as _CircuitConfig

    reranker_cb = _CircuitBreaker("reranker", _CircuitConfig.from_settings(settings.circuit))
    # 裁判（judge）熔断：L3 蕴含判定占端到端延迟 45%~50%，judge 槽位挂掉时
    # 快速失败（不耗尽超时×重试），由验证层降级为 exists_only，链路继续。
    judge_cb = _CircuitBreaker("judge_llm", _CircuitConfig.from_settings(settings.circuit))
    retrieval = RetrievalPipeline(
        embedder=embedder,
        milvus=milvus,
        reranker=reranker,
        rewriter=rewriter,
        router=router,
        settings=settings,
        graph_retriever=optional["graph_retriever"],
        hyde=optional["hyde"],
        subqueries=optional["subqueries"],
        stepback=optional["stepback"],
        sentence_window=optional["sentence_window"],
        auto_filter=optional["auto_filter"],
        reranker_cb=reranker_cb,
    )
    generator = Generator(llm_client=create_client(settings.llm.generation))
    # 验证强度由 verify 段决定（非法值在 resolve_verify_settings 内兜底为最严格）
    entailment_mode, verify_strict, verify_sample_ratio = resolve_verify_settings(settings)
    verifier = CitationVerifier(
        milvus=milvus,
        judge_llm=create_client(settings.llm.judge, circuit=judge_cb),
        embedder=embedder,  # 启用事后引用指派（任务书 6.3）
        groundedness_template=None,
        entailment_mode=entailment_mode,
        strict=verify_strict,
        sample_ratio=verify_sample_ratio,
    )
    gate = AbstentionGate.from_settings(settings)

    # ---- 熔断器接入（顺序 / 图 / 流式三条编排路径共用，零侵入） ----
    # 检索（Milvus 依赖）与生成（LLM 依赖）各自独立熔断：连续失败达阈值后
    # 打开，冷却期内快速失败（编排层按既有兜底转为弃权），避免故障放大。
    from core.circuit import CircuitBreaker, CircuitConfig, CircuitOpenError

    retrieval_cb = CircuitBreaker("retrieval", CircuitConfig.from_settings(settings.circuit))
    generation_cb = CircuitBreaker("llm.generation", CircuitConfig.from_settings(settings.circuit))

    _orig_retrieval_run = retrieval.run

    # 透传式签名：熔断包装只负责「拦一层」，不复述被包装方法的参数列表。
    # 写死签名会让 RetrievalPipeline.run 每新增一个参数就被这里静默吞掉
    # （表现为检索报 unexpected keyword argument 后降级弃权，很难定位）。
    def _guarded_retrieval_run(*args, **kwargs):
        try:
            return retrieval_cb.run(_orig_retrieval_run, *args, **kwargs)
        except CircuitOpenError as exc:
            raise RuntimeError(f"熔断开启（快速失败）: {exc}") from exc

    retrieval.run = _guarded_retrieval_run  # type: ignore[method-assign]

    _orig_generate = generator.generate

    def _guarded_generate(query, chunks):
        try:
            return generation_cb.run(_orig_generate, query, chunks)
        except CircuitOpenError as exc:
            raise RuntimeError(f"熔断开启（快速失败）: {exc}") from exc

    generator.generate = _guarded_generate  # type: ignore[method-assign]

    # 流式生成同样受熔断保护（生成器：正常结束记成功，异常记失败）
    _orig_generate_stream = generator.generate_stream

    def _guarded_generate_stream(query, chunks):
        if not generation_cb.allow():
            raise RuntimeError(f"熔断开启（快速失败）: {generation_cb.name}")

        def _stream():
            try:
                yield from _orig_generate_stream(query, chunks)
            except GeneratorExit:
                raise
            except Exception:
                generation_cb.record_failure()
                raise
            generation_cb.record_success()

        return _stream()

    generator.generate_stream = _guarded_generate_stream  # type: ignore[method-assign]

    return {
        "retrieval": retrieval,
        "generator": generator,
        "verifier": verifier,
        "gate": gate,
        "settings": settings,
        # 熔断器对象一并暴露（/api/health 深度探活联动）
        "circuits": {"retrieval": retrieval_cb, "generation": generation_cb},
    }


def reset_pipeline(close: bool = True) -> None:
    """丢弃缓存的组件，使下次请求按最新配置重建。

    Args:
        close: 是否同时关闭旧组件持有的资源（Milvus 连接、子查询线程池）。

            ``True``（默认，供测试与进程收尾使用）：立即释放资源。**要求
            调用方确保没有在途请求仍持有旧组件**，否则在途请求会撞上已
            关闭的连接。

            ``False``（供运行中的配置热更新使用）：只把单例引用置空，不动
            旧组件。在途请求可以拿着旧组件安全跑完，等最后一个引用释放后
            由 GC 回收。代价是极少数情况下会短暂多留一个 Milvus 连接和
            （仅当启用过子查询增强时）一个线程池——配置保存是低频操作，
            用这点开销换取「不打断在途请求」是划算的。
    """
    global _pipeline
    with _pipeline_lock:
        old, _pipeline = _pipeline, None
    if close and old is not None:
        retrieval = old.get("retrieval")
        milvus = getattr(retrieval, "milvus", None)
        close_fn = getattr(milvus, "close", None)
        if callable(close_fn):
            close_fn()
        # 关闭子查询并发检索线程池（RetrievalPipeline.shutdown，幂等）
        shutdown_retrieval = getattr(retrieval, "shutdown", None)
        if callable(shutdown_retrieval):
            shutdown_retrieval()
    try:
        from rag_graph import _reset_graph_cache

        _reset_graph_cache()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

def _answer_sequential(
    comp: dict[str, Any],
    query: str,
    acl: list[str] | None,
    retry: bool,
    query_id: str,
    tenant_id: str | None = None,
    dataset_id: str | None = None,
) -> QueryResult:
    """顺序编排版：检索 -> 弃权门 -> 生成验证 -> 二轮补救 -> 弃权门。

    即图编排的等价顺序实现（``pipeline.graph_engine_on=False`` 或图异常
    回退时使用）。行为与图编排完全一致：同弃权门、同二轮检索循环、
    同 QueryResult 结构与 traces 语义。

    Args:
        comp: :func:`get_pipeline` 返回的组件字典。
        query: 用户问题。
        acl: 访问控制列表（如 ``["fin", "legal"]``），None 不过滤。
        retry: 是否允许二轮检索补救。
        query_id: 查询标识（用于链路追踪）。
        tenant_id: 已解析的租户标识（由 ``answer_query`` 调用
            :func:`rag_common.resolve_tenant` 后传入），透传给检索管线。
        dataset_id: 知识库标识（None / 空串 = 不限知识库），透传给检索管线。

    Returns:
        :class:`models.schemas.QueryResult`。检索 / 生成 / 验证失败时
        返回弃权结果（``abstained=True``、空答案），不抛出异常。
    """
    gate: AbstentionGate = comp["gate"]

    with trace_session(query_id) as trace:
        traces: list[str] = []

        # ---------- 第一轮：检索 ----------
        try:
            retrieval = comp["retrieval"].run(query, acl, tenant_id=tenant_id, dataset_id=dataset_id)
        except Exception as exc:  # noqa: BLE001 - Milvus 等不可用时降级弃权
            return QueryResult(
                query=query,
                answer="",
                abstained=True,
                route="hybrid",
                traces=[f"检索失败（弃权）: {exc}"] + trace.as_list(),
            )
        traces.extend(retrieval.traces)
        chunks: list[RetrievedChunk] = list(retrieval.chunks)
        retrieval_scores = [rc.score for rc in chunks]
        # rerank 没跑成时 score 是 RRF 融合分（只由名次决定，不含相似度信息），
        # 拿它跟 retrieval_score_threshold 比会把完美命中也判成"知识库无相关内容"。
        scores_comparable = retrieval.reranked
        # 那种情况下改用这把尺子：管线预判到 rerank 不会生效时，会让 Milvus
        # 顺带回传命中向量，当场与查询向量算出真实余弦。空列表 = 这一路也没有。
        dense_cosines = dense_cosines_of(chunks)

        # ---------- 弃权门（检索阶段）：无检索结果 / 知识库无相关内容 ----------
        abstain, reason = gate.decide(
            retrieval_scores,
            None,
            retrieval_scores_comparable=scores_comparable,
            dense_cosines=dense_cosines,
        )
        if abstain:
            traces.append(f"弃权: {reason}")
            return QueryResult(
                query=query,
                answer="",
                abstained=True,
                route=retrieval.route.target,
                traces=traces + trace.as_list(),
            )

        # ---------- 第一轮：生成 + 验证 ----------
        generated, verification = generate_and_verify(comp, query, chunks)
        traces.extend(verification.notes)

        # ---------- 二轮检索补救（证据不足时） ----------
        rounds = 1
        # 先判定这一轮**有没有可能**捞到新东西：查询增强全关时检索是确定性的，
        # 重跑必然原样返回，只是白花一次嵌入 + Milvus + 重排。
        can_help = second_round_can_help(comp.get("settings") or get_settings())
        if (
            retry
            and not can_help
            and (verification.missing_evidence or not verification.supported)
        ):
            traces.append("二轮检索已跳过：查询增强（HyDE/子查询/Stepback）全关，重跑结果必然相同")
            get_metrics().incr("query.round2.skipped")
        while (
            retry
            and can_help
            and rounds < MAX_ROUNDS
            and (verification.missing_evidence or not verification.supported)
        ):
            rounds += 1
            try:
                retrieval2 = comp["retrieval"].run(query, acl, tenant_id=tenant_id, dataset_id=dataset_id)
            except Exception as exc:  # noqa: BLE001 - 补救失败沿用第一轮结果
                traces.append(f"二轮检索失败（沿用第一轮结果）: {exc}")
                break
            merged = merge_chunks(chunks, retrieval2.chunks)
            if len(merged) == len(chunks):
                # 无新增证据：再跑也无益，避免死循环
                traces.append("二轮检索无新增证据，停止补救")
                break
            chunks = merged
            retrieval_scores = [rc.score for rc in chunks]
            # 合并后的分数来自两轮。只要有一轮没重排，列表里就混进了 RRF 分，
            # 整体不再可比——取合取而不是覆盖。
            scores_comparable = scores_comparable and retrieval2.reranked
            # 余弦同理对合并后的列表现算：两轮里哪几条带了向量就用哪几条，
            # 不能只取第一轮的——那批 chunk 未必还在 merged 里。
            dense_cosines = dense_cosines_of(chunks)
            traces.extend(retrieval2.traces)
            traces.append(f"二轮检索已执行（第 {rounds} 轮，合并 {len(chunks)} 条证据）")
            generated, verification = generate_and_verify(comp, query, chunks)
            traces.extend(verification.notes)

        # ---------- 弃权门（验证阶段）：证据不足以支撑可靠声明 ----------
        # 走 gate_entailment_scores() 而不是 list(...values())：L3 没跑时它给 None
        # （不参与判定），而空列表会被门读成"评了但没一条达标"直接弃权。
        entailment_scores = verification.gate_entailment_scores()
        if generated is None:
            # 生成整个失败了（LLM 挂了 / 流中断）。没有答案就没什么可"支撑"的，
            # 弃权门问的是"证据够不够"，答不了"有没有答案"这个问题。
            # 从前这条路径被掩盖着：生成失败 -> entailment_scores 为空 -> 编排器
            # 无条件传 [] -> 门读成"证据不足"顺手弃权。阶段 2 把 [] 改成 None
            # （L3 没跑就是没跑）之后，这层遮蔽没了，真面目露出来：
            # **abstained=False + answer="" ——一个看起来成功、实际空白的响应。**
            abstain, reason = True, "生成失败"
        else:
            abstain, reason = gate.decide(
                retrieval_scores,
                entailment_scores,
                retrieval_scores_comparable=scores_comparable,
                dense_cosines=dense_cosines,
            )
        if abstain:
            traces.append(f"弃权: {reason}")
            return QueryResult(
                query=query,
                answer="",
                abstained=True,
                route=retrieval.route.target,
                traces=traces + trace.as_list(),
            )

        # ---------- 构造最终结果 ----------
        return QueryResult(
            query=query,
            answer=generated.answer if generated is not None else "",
            citations=verification.citations,
            verdict={"evidence_chunks": evidence_chunks(chunks)},
            abstained=False,
            route=retrieval.route.target,
            traces=traces + trace.as_list(),
        )


def answer_query(
    query: str,
    acl: list[str] | None = None,
    retry: bool = True,
    settings: Settings | None = None,
    tenant_id: str | None = None,
    dataset_id: str | None = None,
) -> QueryResult:
    """回答一次用户查询（完整 RAG 链路）。

    默认优先走 LangGraph StateGraph 图编排（``pipeline.graph_engine_on=True``）；
    图构建失败 / 图执行异常 / langgraph 未安装时自动回退到顺序编排
    （:func:`_answer_sequential`）。两种编排的对外行为完全一致：同弃权门、
    同二轮检索循环、同 QueryResult 结构与 traces 语义。

    Args:
        query: 用户问题。
        acl: 访问控制列表（如 ``["fin", "legal"]``），None 不过滤。
        retry: 是否允许二轮检索补救（默认 True）。
        settings: Settings 实例；None 时使用进程级单例。
        tenant_id: 租户标识（None / 空串 = 未指定）。由
            :func:`rag_common.resolve_tenant` 解析为有效租户：
            enforced=True 时空 / None 回退 ``default_tenant``
            （存量调用零改动纳入隔离）；enforced=False 时空串表示不过滤。
            解析结果贯穿检索 / 图检索全链路，跨租户数据零可见。
        dataset_id: 知识库标识（None / 空串 = 不限知识库，检索全域）。
            非空时只在该知识库内检索。与 ``tenant_id`` 的区别：租户是安全
            边界（enforced 时强制回退默认租户），知识库是检索范围，空即全域、
            不做强制回退。入库侧由 ``IngestPipeline`` 的同名参数写入。

    Returns:
        :class:`models.schemas.QueryResult`。检索 / 生成 / 验证失败时
        返回弃权结果（``abstained=True``、空答案），不抛出异常。
    """
    comp = get_pipeline(settings)
    query_id = f"query-{uuid.uuid4().hex[:12]}"
    # 租户强制解析：enforced=True 时空 / None 回退 default_tenant
    tenant = resolve_tenant(tenant_id, settings or get_settings())
    # 知识库不做强制回退：空即全域检索
    dataset = (dataset_id or "").strip()

    # 装配可观测性（幂等）：query_id 日志格式化器 + OTel 惰性接入 + span→metrics。
    # setup_logging / init_otel / _register_span_observer 均幂等，重复调用无副作用；
    # 以进程级单例配置为准，保证与调用方传入的 settings 无关。
    setup_observability((settings or get_settings()).observability)

    if (settings or get_settings()).pipeline.graph_engine_on:
        try:
            from rag_graph import run_graph_answer  # 惰性导入：避免 import rag 即 import langgraph

            with bind_query_id(query_id):
                result = run_graph_answer(
                    comp, query, acl, retry, query_id,
                    tenant_id=tenant, dataset_id=dataset,
                )
            if result is not None:
                return result
            # 图不可用（返回 None，最常见的回退路径）：累计回退指标
            get_metrics().incr("graph.fallback")
        except Exception:  # noqa: BLE001 - 图不可用一律回退顺序编排
            get_logger(__name__).exception("图编排不可用，回退顺序管线")
            get_metrics().incr("graph.fallback")

    # 与图路径一致：回退路径也要有 query_id 上下文（日志 / span 可串联）
    with bind_query_id(query_id):
        return _answer_sequential(
            comp, query, acl, retry, query_id, tenant_id=tenant, dataset_id=dataset
        )


__all__ = ["answer_query", "get_pipeline", "reset_pipeline"]
