"""检索评测执行器：语料 → 索引 → 策略 → 指标 → 报告。

可插拔点只有一个维度：**检索策略**。新增一种策略（比如加 BM25、加查询改写、
加句子窗口）只要往 :data:`STRATEGY_REGISTRY` 注册一条工厂，不动这里的循环——
按本仓 ``docs/compose/spec/backend-extensibility-standard.md`` 的口径，
这就是一个"扩展轴"。

策略签名统一为::

    strategy(query: str, store: LocalVectorStore, cfg: EvalConfig) -> StrategyOutput

``StrategyOutput`` 里带排序结果 + 各阶段耗时（毫秒），这样"多了重排这一层慢
多少毫秒、换来多少 NDCG"是一次运行里同时读到的，不用两次数。
"""
from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from core.providers import ProviderRegistry
from models.schemas import RetrievedChunk

from .corpus import CorpusChunk, build_corpus, corpus_fingerprint
from .gold import (
    DATASET_VERSION,
    GOLD_SET,
    GoldCase,
    validate_against_corpus,
    validate_corpus_languages,
)
from .metrics import METRIC_NAMES, aggregate, score_case
from .store import CachedEmbedder, HashingEmbedder, LocalVectorStore

DEFAULT_TOP_K = 10
DEFAULT_CANDIDATES = 50


@dataclass(frozen=True)
class EvalConfig:
    """一次评测的运行参数（全部可配置，不在代码里写死阈值/条数）。"""

    top_k: int = DEFAULT_TOP_K
    candidates: int = DEFAULT_CANDIDATES
    ks: tuple[int, ...] = (5, 10)
    embedder: str = "api"  # api = 真实 bge-m3；hashing = 离线伪嵌入
    strategies: tuple[str, ...] = ("dense", "dense_rerank")
    cache_dir: Path = Path("eval/.cache")
    #: 向量库后端：local=进程内 numpy（零依赖）；milvus=真实 Milvus 集合
    store: str = "local"
    #: Milvus 集合名（为空则用 RAG4C_MILVUS_COLLECTION / 默认 rag4c_eval_chunks）
    milvus_collection: str = ""
    #: 语料来源：docs=仓库文档（本地切片）；milvus=生产集合（只读，不写入）
    corpus_source: str = "docs"
    #: 评测集：docs=文档代理语料标注；production=生产语料标注
    gold: str = "docs"
    #: 每条用例之间的间隔（毫秒）。上游有 TPM 限制时用来避免打满限流
    sleep_ms: int = 0
    rerank_lambda: float = 0.7
    mmr_k: int = DEFAULT_TOP_K

    def as_dict(self) -> dict[str, Any]:
        return {
            "top_k": self.top_k,
            "candidates": self.candidates,
            "ks": list(self.ks),
            "embedder": self.embedder,
            "store": self.store,
            "corpus_source": self.corpus_source,
            "gold": self.gold,
            "sleep_ms": self.sleep_ms,
            "strategies": list(self.strategies),
            "rerank_lambda": self.rerank_lambda,
            "mmr_k": self.mmr_k,
        }


@dataclass
class StrategyOutput:
    """一个策略对一条查询的产出。"""

    results: list[RetrievedChunk]
    timings_ms: dict[str, float] = field(default_factory=dict)
    degraded: str = ""


@dataclass
class EvalIndex:
    """评测用的本地索引：语料 + 向量 + 检索入口。"""

    corpus: list[CorpusChunk]
    store: LocalVectorStore
    embedder: Any
    fingerprint: str
    #: store="milvus" 时的客户端句柄，供 run_eval 结束时 close 释放连接
    milvus: Any = None

    def chunk_ids(self) -> list[str]:
        return [item.chunk_id for item in self.corpus]


def _make_embedder(name: str, cache_path: Path, fingerprint: str) -> Any:
    """按名字造嵌入器（真实 / 离线伪嵌入），都套上磁盘缓存。"""
    if name == "hashing":
        return CachedEmbedder(HashingEmbedder(), cache_path, model="hashing", fingerprint=fingerprint)
    from config.settings import get_settings
    from core.embedding import create_embedder

    settings = get_settings()
    inner = create_embedder(settings.embedding)
    model = getattr(settings.embedding, "api_model", "") or "api"
    return CachedEmbedder(inner, cache_path, model=str(model), fingerprint=fingerprint)


def _milvus_rows(corpus: Sequence[CorpusChunk], vectors: Sequence[Sequence[float]]) -> list[dict]:
    """把语料切片转成 Milvus 行（字段与生产集合 rag4c_chunks 同构）。"""
    now_ms = int(time.time() * 1000)
    rows: list[dict] = []
    for item, vector in zip(corpus, vectors):
        chunk = item.chunk
        rows.append(
            {
                "chunk_id": chunk.chunk_id,
                "doc_id": chunk.doc_id,
                "text": chunk.text,
                "text_hash": chunk.text_hash,
                "created_at": now_ms,
                "updated_at": now_ms,
                "source": chunk.source or "",
                "parent_chunk_id": "",
                "acl": "",
                "tenant_id": chunk.tenant_id,
                "dataset_id": chunk.dataset_id,
                "document_revision": 1,
                "content_revision": 1,
                "metadata": chunk.metadata or {},
                "dense_vector": [float(v) for v in vector],
            }
        )
    return rows


def build_index(root: Path, cfg: EvalConfig) -> EvalIndex:
    """构建索引（真实嵌入会被缓存，第二次起不再花钱）。

    ``store="local"`` 用进程内 numpy（零依赖、零外部服务）；
    ``store="milvus"`` 用真实 Milvus 集合（本地/虚拟机/Zilliz 都行），
    字段与生产集合同构，因此测出来的召回能外推到线上。
    """
    if cfg.corpus_source == "milvus":
        # 生产语料：不切片、不写入，只连集合（评测对生产库是**只读**的）
        from .milvus_store import MilvusStoreConfig, MilvusVectorStore

        mcfg = MilvusStoreConfig.from_env()
        if cfg.milvus_collection:
            mcfg.collection = cfg.milvus_collection
        store = MilvusVectorStore(mcfg)
        store.connect()
        store.ensure_collection()
        embedder = _make_embedder(cfg.embedder, Path(cfg.cache_dir) / "embeddings_query_cache.json", "query")
        print(f"[milvus] 只读评测集合 {mcfg.collection}（{store.count()} 行）")
        return EvalIndex(
            corpus=[], store=store, embedder=embedder,
            fingerprint=f"milvus:{mcfg.collection}", milvus=store,
        )

    corpus = build_corpus(Path(root))
    if not corpus:
        raise RuntimeError("语料为空：检查 DEFAULT_CORPUS_FILES 里的文件是否还在")
    fingerprint = corpus_fingerprint(corpus)
    cache_path = Path(cfg.cache_dir) / f"embeddings_{cfg.embedder}_{fingerprint}.json"
    embedder = _make_embedder(cfg.embedder, cache_path, fingerprint)
    vectors = embedder.embed_texts([item.chunk.text for item in corpus])
    if hasattr(embedder, "save"):
        embedder.save()

    if cfg.store == "milvus":
        from .milvus_store import MilvusStoreConfig, MilvusVectorStore

        mcfg = MilvusStoreConfig.from_env()
        if cfg.milvus_collection:
            mcfg.collection = cfg.milvus_collection
        mcfg.dim = len(vectors[0])
        store = MilvusVectorStore(mcfg)
        store.connect()
        store.ensure_collection()
        # upsert 是幂等的：同一份语料重复跑不会产生重复行（主键是 chunk_id）
        written = store.upsert(_milvus_rows(corpus, vectors))
        store.flush()
        print(f"[milvus] 集合 {mcfg.collection} 写入 {written} 行，当前 {store.count()} 行")
        return EvalIndex(
            corpus=corpus, store=store, embedder=embedder, fingerprint=fingerprint, milvus=store
        )

    store = LocalVectorStore([item.chunk for item in corpus], vectors)
    return EvalIndex(corpus=corpus, store=store, embedder=embedder, fingerprint=fingerprint)


# ---------------------------------------------------------------------------
# 策略：注册进 ProviderRegistry，新增策略 = 加一条注册，不改执行循环
# ---------------------------------------------------------------------------

STRATEGY_REGISTRY: ProviderRegistry[EvalConfig, Callable[..., StrategyOutput]] = ProviderRegistry(
    "retrieval-eval-strategy"
)


def _dense(store: LocalVectorStore, cfg: EvalConfig, query_vec: Sequence[float]) -> list[RetrievedChunk]:
    return store.hybrid_search(query_dense=query_vec, top_k=cfg.top_k, with_cosine=True)


def _strategy_dense(cfg: EvalConfig) -> Callable[..., StrategyOutput]:
    def run(query: str, index: EvalIndex, reranker: Any = None) -> StrategyOutput:
        timings: dict[str, float] = {}
        t0 = time.perf_counter()
        query_vec = index.embedder.embed_query(query)
        timings["embed_ms"] = (time.perf_counter() - t0) * 1000.0
        t0 = time.perf_counter()
        try:
            results = _dense(index.store, cfg, query_vec)
        except Exception as exc:  # noqa: BLE001 - 单条检索失败不该让整轮评测崩
            timings["search_ms"] = (time.perf_counter() - t0) * 1000.0
            return StrategyOutput(results=[], timings_ms=timings, degraded=f"search_failed:{type(exc).__name__}")
        timings["search_ms"] = (time.perf_counter() - t0) * 1000.0
        return StrategyOutput(results=results, timings_ms=timings)

    return run


def _strategy_dense_rerank(cfg: EvalConfig) -> Callable[..., StrategyOutput]:
    def run(query: str, index: EvalIndex, reranker: Any = None) -> StrategyOutput:
        timings: dict[str, float] = {}
        t0 = time.perf_counter()
        query_vec = index.embedder.embed_query(query)
        timings["embed_ms"] = (time.perf_counter() - t0) * 1000.0
        t0 = time.perf_counter()
        try:
            candidates = index.store.hybrid_search(
                query_dense=query_vec, top_k=cfg.candidates, with_cosine=True
            )
        except Exception as exc:  # noqa: BLE001 - 同上
            timings["search_ms"] = (time.perf_counter() - t0) * 1000.0
            return StrategyOutput(results=[], timings_ms=timings, degraded=f"search_failed:{type(exc).__name__}")
        timings["search_ms"] = (time.perf_counter() - t0) * 1000.0
        if reranker is None:
            return StrategyOutput(
                results=candidates[: cfg.top_k], timings_ms=timings, degraded="reranker_unavailable"
            )
        t0 = time.perf_counter()
        try:
            scores = reranker.rerank(query, [item.chunk for item in candidates])
        except Exception as exc:  # noqa: BLE001 - 单次重排失败不该让整轮评测崩
            # 降级：沿用检索顺序（与"reranker 不可用"同一条路径），并留下痕迹
            timings["rerank_ms"] = (time.perf_counter() - t0) * 1000.0
            return StrategyOutput(
                results=list(candidates)[: cfg.top_k],
                timings_ms=timings,
                degraded=f"rerank_failed:{type(exc).__name__}",
            )
        timings["rerank_ms"] = (time.perf_counter() - t0) * 1000.0
        order = sorted(range(len(candidates)), key=lambda i: float(scores[i]), reverse=True)
        reranked: list[RetrievedChunk] = []
        for rank, position in enumerate(order[: cfg.top_k], start=1):
            item = candidates[position]
            reranked.append(
                RetrievedChunk(
                    chunk=item.chunk,
                    score=float(scores[position]),
                    rank=rank,
                    branch=item.branch,
                    dense_cosine=item.dense_cosine,
                )
            )
        return StrategyOutput(results=reranked, timings_ms=timings)

    return run


def _strategy_hybrid_rrf(cfg: EvalConfig) -> Callable[..., StrategyOutput]:
    """稠密 + BM25 双路 RRF 融合（生产默认路径；**只有 Milvus 后端支持**）。

    本地 numpy 库没有 BM25，调用会按它的 fail-closed 约定抛错——这是有意为之：
    宁可显式失败，也不要用一个假数字冒充"混合检索的收益"。
    """

    def run(query: str, index: EvalIndex, reranker: Any = None) -> StrategyOutput:
        timings: dict[str, float] = {}
        t0 = time.perf_counter()
        query_vec = index.embedder.embed_query(query)
        timings["embed_ms"] = (time.perf_counter() - t0) * 1000.0
        t0 = time.perf_counter()
        try:
            candidates = index.store.hybrid_search(
                query_dense=query_vec,
                top_k=cfg.candidates,
                query_text=query,
                with_cosine=True,
            )
        except Exception as exc:  # noqa: BLE001 - 同上：单条检索失败按降级处理
            timings["search_ms"] = (time.perf_counter() - t0) * 1000.0
            return StrategyOutput(
                results=[], timings_ms=timings, degraded=f"search_failed:{type(exc).__name__}"
            )
        timings["search_ms"] = (time.perf_counter() - t0) * 1000.0
        if reranker is None:
            return StrategyOutput(
                results=list(candidates)[: cfg.top_k],
                timings_ms=timings,
                degraded="reranker_unavailable",
            )
        t0 = time.perf_counter()
        try:
            scores = reranker.rerank(query, [item.chunk for item in candidates])
        except Exception as exc:  # noqa: BLE001 - 同上：单次失败按降级处理
            timings["rerank_ms"] = (time.perf_counter() - t0) * 1000.0
            return StrategyOutput(
                results=list(candidates)[: cfg.top_k],
                timings_ms=timings,
                degraded=f"rerank_failed:{type(exc).__name__}",
            )
        timings["rerank_ms"] = (time.perf_counter() - t0) * 1000.0
        order = sorted(range(len(candidates)), key=lambda i: float(scores[i]), reverse=True)
        from models.schemas import RetrievedChunk

        results = [
            RetrievedChunk(
                chunk=candidates[i].chunk,
                score=float(scores[i]),
                rank=rank,
                branch=candidates[i].branch,
                dense_cosine=candidates[i].dense_cosine,
            )
            for rank, i in enumerate(order[: cfg.top_k], start=1)
        ]
        return StrategyOutput(results=results, timings_ms=timings)

    return run


def _strategy_dense_mmr(cfg: EvalConfig) -> Callable[..., StrategyOutput]:
    def run(query: str, index: EvalIndex, reranker: Any = None) -> StrategyOutput:
        from retrieval.diversity import mmr_select

        timings: dict[str, float] = {}
        t0 = time.perf_counter()
        query_vec = index.embedder.embed_query(query)
        timings["embed_ms"] = (time.perf_counter() - t0) * 1000.0
        t0 = time.perf_counter()
        candidates = index.store.hybrid_search(
            query_dense=query_vec, top_k=cfg.candidates, with_cosine=True
        )
        timings["search_ms"] = (time.perf_counter() - t0) * 1000.0
        t0 = time.perf_counter()
        selected = mmr_select(candidates, lambda_=cfg.rerank_lambda, k=cfg.mmr_k)
        timings["mmr_ms"] = (time.perf_counter() - t0) * 1000.0
        return StrategyOutput(results=list(selected), timings_ms=timings)

    return run


STRATEGY_REGISTRY.register("dense", _strategy_dense)
STRATEGY_REGISTRY.register("dense_rerank", _strategy_dense_rerank)
STRATEGY_REGISTRY.register("dense_mmr", _strategy_dense_mmr)
STRATEGY_REGISTRY.register("hybrid_rrf", _strategy_hybrid_rrf)


def available_strategies() -> tuple[str, ...]:
    return STRATEGY_REGISTRY.names()


# ---------------------------------------------------------------------------
# 执行与报告
# ---------------------------------------------------------------------------


def _percentiles(values: Sequence[float]) -> dict[str, float]:
    if not values:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0}

    def at(q: float) -> float:
        return float(statistics.quantiles(sorted(values), n=100, method="inclusive")[int(q * 100) - 1])

    return {"p50": at(0.50), "p95": at(0.95), "p99": at(0.99)}


def _run_strategies(
    index: EvalIndex, cfg: EvalConfig, *, reranker: Any = None,
    cases: Sequence[GoldCase] | None = None,
) -> dict[str, dict[str, Any]]:
    """跑完所有策略并汇总（拆出来是为了让 run_eval 能用 try/finally 包住连接释放）。"""
    per_strategy: dict[str, dict[str, Any]] = {}

    for name in cfg.strategies:
        strategy = STRATEGY_REGISTRY.create(name, cfg)
        case_metrics: list[dict[str, float]] = []
        metrics_by_language: dict[str, list[dict[str, float]]] = {}
        latencies: list[float] = []
        stage_totals: dict[str, list[float]] = {}
        per_case: list[dict[str, Any]] = []
        noise_scores: list[float] = []
        degraded: list[str] = []

        for case in (cases if cases is not None else GOLD_SET):
            if cfg.sleep_ms:
                time.sleep(cfg.sleep_ms / 1000.0)
            output = strategy(case.question, index, reranker)
            retrieved_ids = [item.chunk.chunk_id for item in output.results]
            if case.unanswerable:
                # 不可答用例：只记录"噪声置信度"（首条余弦），不进 Recall/MRR。
                if output.results:
                    noise_scores.append(float(output.results[0].dense_cosine or 0.0))
                per_case.append(
                    {
                        "case_id": case.case_id,
                        "unanswerable": True,
                        "corpus_language": case.corpus_language,
                        "top_score": (output.results[0].score if output.results else 0.0),
                        "retrieved": retrieved_ids[:5],
                        "retrieved10": retrieved_ids[:10],
                    }
                )
                continue
            metrics = score_case(retrieved_ids, list(case.gold_chunk_ids), ks=cfg.ks)
            case_metrics.append(metrics)
            metrics_by_language.setdefault(case.corpus_language, []).append(metrics)
            total_ms = sum(output.timings_ms.values())
            latencies.append(total_ms)
            for stage, value in output.timings_ms.items():
                stage_totals.setdefault(stage, []).append(value)
            if output.degraded:
                degraded.append(f"{case.case_id}:{output.degraded}")
            per_case.append(
                {
                    "case_id": case.case_id,
                    "unanswerable": False,
                    "corpus_language": case.corpus_language,
                    "metrics": metrics,
                    "top5": retrieved_ids[:5],
                    # top10 一并落盘：F2.3 要把「英文 NDCG 0.735 vs 中文 0.923」
                    # 归因到具体名次（gold 排第几），只有 top5 的话，"排第 7"
                    # 和"排第 30"会落进同一个桶，归因就无从谈起。top5 保留不动，
                    # 旧的下游读法不受影响。
                    "top10": retrieved_ids[:10],
                    "missed_gold": [
                        gold for gold in case.gold_chunk_ids if gold not in retrieved_ids
                    ],
                    "gold_ranks": [
                        rank
                        for rank, cid in enumerate(retrieved_ids, start=1)
                        if cid in case.gold_chunk_ids
                    ],
                    "latency_ms": round(total_ms, 2),
                }
            )

        failure_kinds: dict[str, int] = {}
        for item in degraded:
            kind = item.split(":", 1)[-1] if ":" in item else item
            failure_kinds[kind] = failure_kinds.get(kind, 0) + 1
        answered = len([c for c in per_case if not c.get("unanswerable")])
        per_strategy[name] = {
            "metrics": aggregate(case_metrics),
            # 整轮宏平均会把"某一组只有几十条"这件事抹平，所以按声明的语料语言
            # 分组各算一次，并带上条数——哪一组不足以下结论，读数自己要看得出来
            "by_language": {
                lang: {"cases": len(rows), "metrics": aggregate(rows)}
                for lang, rows in sorted(metrics_by_language.items())
            },
            # 上游失败率 / 降级率：与质量指标分开看——一次重排限流不该被
            # 误读成"检索质量变差"，也不该悄悄消失在日志里
            "degraded_cases": len(degraded),
            "degraded_rate": round(len(degraded) / answered, 4) if answered else 0.0,
            "failure_kinds": failure_kinds,
            "latency_ms": _percentiles(latencies),
            "stages_ms": {stage: _percentiles(values) for stage, values in stage_totals.items()},
            "unanswerable_top_score_mean": (
                round(sum(noise_scores) / len(noise_scores), 4) if noise_scores else 0.0
            ),
            "unanswerable_count": len(noise_scores),
            "degraded": degraded,
            "cases": per_case,
        }
    return per_strategy


def run_eval(root: Path, cfg: EvalConfig, *, reranker: Any = None) -> dict[str, Any]:
    """跑一轮评测，返回报告字典（可直接落 JSON）。"""
    index = build_index(root, cfg)
    # 标注集与语料来源是**两个独立维度**，此前被绑死在一起：
    # ``--corpus milvus`` 会隐式强制切到 PRODUCTION_GOLD，于是 ``--gold docs``
    # 传了等于没传（参数进了配置、也写进了报告元信息，唯独没被消费）。
    # 绑死的代价很具体：拿评测集合 rag4c_eval_chunks 跑 F2.1 这类实验时，
    # 只能用生产标注去对评测集合的 chunk id，于是必然报"黄金标注已失效"——
    # 一条能解释成"环境配错了"的报错，实际是选择器没接上。
    want_production = cfg.gold == "production"
    if cfg.corpus_source == "milvus" and want_production:
        from .gold_production import PRODUCTION_GOLD, validate_against_ids
        from .gold_production import DATASET_VERSION as PRODUCTION_DATASET_VERSION

        cases = PRODUCTION_GOLD
        known = {gid for case in cases for gid in case.gold_chunk_ids}
        # 只校验标注的那些 id 是否还在集合里（生产库很大，不逐条比对全量）
        present = set(index.store.query_ids(list(known)))
        missing = validate_against_ids(present)
        if missing:
            raise RuntimeError(f"生产语料的黄金标注已失效（集合里找不到）：{missing}")
        # 语言声明必须对得上原文：上一轮把"中文只占 1.2% 的语料"记成了"中文一半"，
        # 又把有答案的年假问题标成不可答——声明与语料不符时，分组读数就是假的
        texts = index.store.query_texts(sorted(known))
        language_problems = validate_corpus_languages(cases, texts)
        if language_problems:
            raise RuntimeError(f"生产标注声明的语料语言与原文不符：{language_problems}")
        gold_version = PRODUCTION_DATASET_VERSION
    else:
        if cfg.corpus_source == "milvus" and not want_production:
            # 评测集合（rag4c_eval_chunks）装的是仓库文档切片，docs 标注正是
            # 对着它们写的。不显式说明这一点，读者会以为默认行为变了。
            print(
                "[runner] 语料来自 Milvus 评测集合，标注使用 docs 集"
                "（--gold docs）；生产标注只对生产集合有效。"
            )
        cases = GOLD_SET
        # 校验口径按语料来源分两路：本地语料能一次列出全部切片 id，Milvus 侧
        # 只有 query_ids（按主键批量查），且生产库很大不能全量扫——所以那边
        # 只核对待用的那些主键。这一处此前直接调 index.chunk_ids()，那是本地
        # store 的 API，Milvus store 根本没有该方法，于是 docs 标注 + Milvus
        # 语料这个组合必然 AttributeError。
        if cfg.corpus_source == "milvus":
            known = {gid for case in cases for gid in case.gold_chunk_ids}
            present = set(index.store.query_ids(sorted(known)))
            missing = [
                f"{case.case_id}:{gid}"
                for case in cases
                for gid in case.gold_chunk_ids
                if gid not in present
            ]
        else:
            missing = validate_against_corpus(index.chunk_ids())
        if missing:
            raise RuntimeError(f"黄金标注已失效（语料切片变了）：{missing}")
        gold_version = DATASET_VERSION

    try:
        strategies = _run_strategies(index, cfg, reranker=reranker, cases=cases)
    finally:
        # Milvus 连接必须在结束时释放（哪怕中间抛错），否则进程会一直占着
        # 服务端的连接与已加载的段
        if getattr(index, "milvus", None) is not None:
            index.milvus.close()

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "dataset_version": gold_version,
        "corpus": {
            "chunks": len(index.corpus),
            "fingerprint": index.fingerprint,
        },
        "config": cfg.as_dict(),
        "strategies": strategies,
    }


def compare_reports(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """逐指标对 baseline 求 delta（新增策略/指标也能对上，缺失记 None）。"""
    deltas: dict[str, Any] = {}
    current_strategies = current.get("strategies", {})
    baseline_strategies = baseline.get("strategies", {})
    for name in sorted(set(current_strategies) | set(baseline_strategies)):
        cur = current_strategies.get(name, {})
        base = baseline_strategies.get(name, {})
        cur_metrics = cur.get("metrics", {})
        base_metrics = base.get("metrics", {})
        deltas[name] = {
            metric: {
                "current": round(cur_metrics.get(metric, 0.0), 4),
                "baseline": round(base_metrics.get(metric, 0.0), 4),
                "delta": round(cur_metrics.get(metric, 0.0) - base_metrics.get(metric, 0.0), 4),
            }
            for metric in METRIC_NAMES
        }
    return deltas


def save_report(report: dict[str, Any], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_report(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def format_report(report: dict[str, Any]) -> str:
    """人类可读摘要：一眼看到每个策略的指标与延迟。"""
    lines: list[str] = []
    lines.append(f"语料 {report['corpus']['chunks']} 切片 · 指纹 {report['corpus']['fingerprint']}")
    lines.append(f"评测集 {report['dataset_version']} · 配置 {report['config']}")
    for name, payload in report["strategies"].items():
        metrics = payload["metrics"]
        latency = payload["latency_ms"]
        lines.append("")
        lines.append(f"[{name}]")
        lines.append(
            "  "
            + "  ".join(f"{key}={metrics.get(key, 0.0):.3f}" for key in METRIC_NAMES)
        )
        for lang, group in payload.get("by_language", {}).items():
            lines.append(
                f"  [{lang}] {group['cases']} 条: "
                + "  ".join(f"{key}={group['metrics'].get(key, 0.0):.3f}" for key in METRIC_NAMES)
            )
        lines.append(
            f"  延迟 ms: p50={latency.get('p50', 0.0):.1f} "
            f"p95={latency.get('p95', 0.0):.1f} p99={latency.get('p99', 0.0):.1f}"
        )
        if payload.get("degraded"):
            lines.append(f"  降级: {payload['degraded'][:5]}")
        lines.append(
            f"  不可答噪声 top-score 均值={payload.get('unanswerable_top_score_mean', 0.0)}"
        )
        lines.append(
            f"  降级 {payload.get('degraded_cases', 0)} 条（{payload.get('degraded_rate', 0.0):.1%}）"
            f" 明细={payload.get('failure_kinds', {})}"
        )
    return "\n".join(lines)
