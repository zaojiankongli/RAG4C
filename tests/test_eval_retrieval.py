"""检索层评测的离线测试（零网络、零成本，可进 CI 常驻）。

覆盖三件事：指标算得对、本地向量库行为可信、扩展点是真的可扩展。
第三条按本仓扩展轴规范的口径写：注册一个新策略不需要改执行循环；
反向验证（未注册的名字必须报错）也一并钉住。
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from core.providers import UnknownProviderError
from eval.retrieval_eval import metrics as m
from eval.retrieval_eval.gold import GOLD_SET, validate_against_corpus
from eval.retrieval_eval.runner import (
    STRATEGY_REGISTRY,
    EvalConfig,
    StrategyOutput,
    build_index,
    compare_reports,
    run_eval,
)
from eval.retrieval_eval.store import CachedEmbedder, HashingEmbedder, LocalVectorStore
from models.schemas import Chunk


def _chunk(chunk_id: str, text: str, doc_id: str = "d1") -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        doc_id=doc_id,
        text=text,
        text_hash=f"h-{chunk_id}",
        created_at=__import__("datetime").datetime(2026, 1, 1),
        updated_at=__import__("datetime").datetime(2026, 1, 1),
        metadata={"heading": "h"},
    )


# ---------------------------------------------------------------------------
# 指标
# ---------------------------------------------------------------------------


def test_duplicate_hits_consume_slots_instead_of_being_free():
    """重复命中要"占坑"：前 K 个位置里出现重复，就等于白扔了一个位置。"""
    retrieved = ["a", "a", "b", "c"]
    assert m.recall_at_k(retrieved, ["a", "b"], 5) == 1.0
    assert m.precision_at_k(retrieved, ["a", "b"], 5) == pytest.approx(2 / 5)
    # 前 2 位是 a、a：去重后只剩 1 条命中， precision 因此是 1/2 而不是 1
    assert m.precision_at_k(retrieved, ["a", "b"], 2) == pytest.approx(0.5)
    assert m.precision_at_k(["a", "b", "c"], ["a", "b"], 2) == pytest.approx(1.0)


def test_mrr_uses_first_hit_rank_and_zero_when_missed():
    assert m.mrr_at_k(["x", "y", "gold"], ["gold"], 10) == pytest.approx(1 / 3)
    assert m.mrr_at_k(["x", "y"], ["gold"], 10) == 0.0
    assert m.mrr_at_k(["gold"], ["gold"], 1) == 1.0


def test_ndcg_normalizes_against_ideal_ordering():
    perfect = m.ndcg_at_k(["a", "b", "c"], ["a", "b"], 10)
    assert perfect == pytest.approx(1.0)
    # gold 全在末尾：DCG 明显小于理想，且大于 0
    late = m.ndcg_at_k(["x", "y", "a", "b"], ["a", "b"], 10)
    assert 0.0 < late < 1.0
    assert m.ndcg_at_k(["x"], ["a"], 10) == 0.0


def test_empty_gold_and_zero_k_are_safe():
    assert m.recall_at_k(["a"], [], 5) == 0.0
    assert m.ndcg_at_k(["a"], [], 5) == 0.0
    assert m.precision_at_k(["a"], ["a"], 0) == 0.0


def test_score_case_and_aggregate_align_with_metric_names():
    scored = m.score_case(["a", "b"], ["b"], ks=(5, 10))
    assert set(scored) == set(m.METRIC_NAMES)
    aggregated = m.aggregate([scored, {name: 0.0 for name in m.METRIC_NAMES}])
    assert set(aggregated) == set(m.METRIC_NAMES)
    assert aggregated["recall@5"] == pytest.approx(scored["recall@5"] / 2)


def test_ndcg_discount_matches_log2_formula():
    # rank=1 的增益是 1/log2(2)=1；rank=2 是 1/log2(3)
    assert m.ndcg_at_k(["a"], ["a", "b"], 10) == pytest.approx(1.0 / (1 + 1 / math.log2(3)))


# ---------------------------------------------------------------------------
# 本地向量库与缓存
# ---------------------------------------------------------------------------


def test_local_store_ranks_by_cosine_and_reports_dense_cosine():
    chunks = [_chunk("a", "向量检索"), _chunk("b", "完全不相关的话题")]
    store = LocalVectorStore(chunks, [[1.0, 0.0], [0.0, 1.0]])
    hits = store.hybrid_search(query_dense=[1.0, 0.0], top_k=2, with_cosine=True)
    assert [h.chunk.chunk_id for h in hits] == ["a", "b"]
    assert hits[0].dense_cosine == pytest.approx(1.0)
    assert [h.rank for h in hits] == [1, 2]


def test_local_store_refuses_unsupported_branches_instead_of_silently_degrading():
    store = LocalVectorStore([_chunk("a", "x")], [[1.0]])
    with pytest.raises(ValueError, match="BM25"):
        store.hybrid_search(query_dense=[1.0], top_k=1, query_text="x")
    with pytest.raises(ValueError, match="过滤表达式"):
        store.hybrid_search(query_dense=[1.0], top_k=1, filter_expr="tenant_id == 't'")


def test_local_store_group_by_limits_same_group():
    chunks = [_chunk("a1", "x", doc_id="d1"), _chunk("a2", "x", doc_id="d1"), _chunk("b1", "x", doc_id="d2")]
    store = LocalVectorStore(chunks, [[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]])
    hits = store.hybrid_search(
        query_dense=[1.0, 0.0], top_k=3, group_by_field="doc_id", group_size=1
    )
    assert [h.chunk.doc_id for h in hits] == ["d1", "d2"]


def test_cached_embedder_only_calls_inner_on_miss(tmp_path):
    class _Counting:
        def __init__(self) -> None:
            self.calls = 0

        def embed_texts(self, texts):
            self.calls += 1
            return [[float(len(t))] for t in texts]

        def embed_query(self, text):
            self.calls += 1
            return [float(len(text))]

    inner = _Counting()
    cache = CachedEmbedder(inner, tmp_path / "cache.json", model="m", fingerprint="f")
    first = cache.embed_texts(["甲", "乙"])
    cache.save()
    second = CachedEmbedder(_Counting(), tmp_path / "cache.json", model="m", fingerprint="f")
    assert second.embed_texts(["甲", "乙"]) == first
    assert second.stats["misses"] == 0 and second.stats["hits"] == 2
    assert inner.calls == 1


def test_hashing_embedder_is_deterministic_and_normalized():
    embedder = HashingEmbedder(dim=32)
    assert embedder.embed_query("同一段文本") == embedder.embed_query("同一段文本")
    vector = embedder.embed_query("归一化检查")
    assert sum(v * v for v in vector) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 扩展点：策略注册表
# ---------------------------------------------------------------------------


def test_unknown_strategy_is_rejected_with_menu():
    with pytest.raises(UnknownProviderError):
        STRATEGY_REGISTRY.create("not-a-strategy", EvalConfig())


def test_new_strategy_registers_without_touching_the_runner(tmp_path):
    """注册一个新策略即可被 run_eval 消费（这是扩展轴的"零改分支"证据）。"""

    def factory(cfg: EvalConfig):
        def run(query: str, index, reranker=None) -> StrategyOutput:
            hits = index.store.hybrid_search(
                query_dense=index.embedder.embed_query(query), top_k=cfg.top_k
            )
            return StrategyOutput(results=list(reversed(hits)), timings_ms={"reverse_ms": 0.1})

        return run

    STRATEGY_REGISTRY.register("reverse_probe", factory)
    try:
        cfg = EvalConfig(
            embedder="hashing", strategies=("reverse_probe",), cache_dir=tmp_path
        )
        report = run_eval(Path("."), cfg)
        assert "reverse_probe" in report["strategies"]
        # 反序策略的首条命中应当等于原末位：确认执行循环真的调用了它
        case = next(c for c in report["strategies"]["reverse_probe"]["cases"] if not c["unanswerable"])
        assert case["top5"]
    finally:
        STRATEGY_REGISTRY.unregister("reverse_probe")


def test_strategy_registry_lists_builtin_strategies():
    names = STRATEGY_REGISTRY.names()
    assert {"dense", "dense_rerank", "dense_mmr"}.issubset(set(names))


# ---------------------------------------------------------------------------
# 端到端（离线伪嵌入，秒级）
# ---------------------------------------------------------------------------


def test_run_eval_offline_produces_all_metrics(tmp_path):
    cfg = EvalConfig(embedder="hashing", strategies=("dense",), cache_dir=tmp_path)
    report = run_eval(Path("."), cfg)
    payload = report["strategies"]["dense"]
    assert set(payload["metrics"]) == set(m.METRIC_NAMES)
    assert payload["latency_ms"]["p50"] >= 0.0
    assert payload["unanswerable_count"] == 3
    # 可答用例数为 23，指标应按其平均
    answerable = [c for c in payload["cases"] if not c["unanswerable"]]
    assert len(answerable) == len([c for c in GOLD_SET if not c.unanswerable])


def test_compare_reports_reports_delta():
    base = {"strategies": {"dense": {"metrics": {"ndcg@10": 0.4, "recall@10": 0.5}}}}
    cur = {"strategies": {"dense": {"metrics": {"ndcg@10": 0.5, "recall@10": 0.4}}}}
    deltas = compare_reports(cur, base)["dense"]
    assert deltas["ndcg@10"]["delta"] == pytest.approx(0.1)
    assert deltas["recall@10"]["delta"] == pytest.approx(-0.1)


def test_gold_set_stays_valid_against_current_corpus(tmp_path):
    index = build_index(Path("."), EvalConfig(embedder="hashing", cache_dir=tmp_path))
    broken = validate_against_corpus(index.chunk_ids())
    assert broken == [], f"黄金标注已失效: {broken}"


def test_index_build_fails_loudly_on_mismatched_inputs():
    with pytest.raises(ValueError, match="长度必须一致"):
        LocalVectorStore([_chunk("a", "x"), _chunk("b", "y")], [[1.0]])


# ---------------------------------------------------------------------------
# 语料语言维度（Round 9）：分组读数之前，先把"声明"钉住
# ---------------------------------------------------------------------------


def test_every_production_case_declares_a_corpus_language():
    """生产标注必须逐条声明语料语言，且落在词表内。

    这条在花钱调嵌入之前就该红：漏声明的用例会悄悄落进未声明组，
    而上一轮的登记文档恰好把"中文占多少语料"记错过一次。
    """
    from eval.retrieval_eval.gold import CORPUS_LANGUAGES, LANGUAGE_UNDECLARED
    from eval.retrieval_eval.gold_production import PRODUCTION_GOLD

    answerable = [case for case in PRODUCTION_GOLD if not case.unanswerable]
    assert [c.case_id for c in answerable if c.corpus_language == LANGUAGE_UNDECLARED] == []
    assert [c.case_id for c in answerable if c.corpus_language not in CORPUS_LANGUAGES] == []
    # 不可答用例没有 gold 切片，语言维度对它无意义，不该被算进任何一个分组
    assert all(c.corpus_language == LANGUAGE_UNDECLARED for c in PRODUCTION_GOLD if c.unanswerable)


def test_cjk_ratio_splits_chinese_at_the_declared_threshold():
    from eval.retrieval_eval.gold import CJK_RATIO_FOR_ZH, cjk_ratio, detect_corpus_language

    assert cjk_ratio("") == 0.0
    assert cjk_ratio("milvus index") == 0.0
    assert detect_corpus_language("Milvus 采用共享存储架构，支持横向扩展。") == "zh"
    assert detect_corpus_language("Milvus adopts a shared storage architecture.") == "en"
    # 阈值边界：恰好等于阈值不算中文（与普查用的判定一致，别让两处各写各的）
    mixed = "a" * 100
    boundary = "代" * int(CJK_RATIO_FOR_ZH * len(mixed) / (1 - CJK_RATIO_FOR_ZH)) + mixed
    assert detect_corpus_language(boundary) == "en"
    assert detect_corpus_language(boundary.replace("a", "代", 1)) == "zh"


def test_language_validator_names_every_kind_of_drift():
    from eval.retrieval_eval.gold import GoldCase, validate_corpus_languages

    cases = [
        GoldCase("undeclared", "q", ("c1",)),
        GoldCase("outside", "q", ("c1",), corpus_language="fr"),
        GoldCase("wrong", "q", ("c2",), corpus_language="zh"),
        GoldCase("internal", "q", ("c1", "c2"), corpus_language="zh"),
        GoldCase("absent", "q", ("c-not-in-corpus",), corpus_language="zh"),
        GoldCase("fine", "q", ("c2",), corpus_language="en"),
        GoldCase("noise", "q", (), unanswerable=True),
    ]
    texts = {"c1": "Milvus 的接入层负责暴露统一的 endpoint。", "c2": "access layer exposes endpoints"}
    problems = validate_corpus_languages(cases, texts)
    flagged = {item.split(":", 1)[0] for item in problems}
    assert flagged == {"undeclared", "outside", "wrong", "internal", "absent"}
    assert "fine" not in flagged and "noise" not in flagged
    # 拿不到原文必须是"无法核对"，不能默认声明没错
    assert any("拿不到原文" in item for item in problems if item.startswith("absent"))


def test_by_language_grouping_partitions_cases_and_moves_with_labels(tmp_path):
    """分组数字要真的随声明变化：标签挪一条，组里的人数和均值都得跟着动。"""
    from dataclasses import replace

    from eval.retrieval_eval.gold import GoldCase
    from eval.retrieval_eval.metrics import aggregate
    from eval.retrieval_eval.runner import _run_strategies

    cfg = EvalConfig(embedder="hashing", strategies=("dense",), cache_dir=tmp_path)
    index = build_index(Path("."), cfg)
    hit_chunk = index.corpus[0].chunk
    cases = [
        GoldCase(
            "zh_hit", hit_chunk.text[:120], (hit_chunk.chunk_id,), corpus_language="zh"
        ),
        GoldCase("en_miss", "完全无关的问题措辞", ("no-such-chunk",), corpus_language="en"),
        GoldCase("noise", "语料里没有的答案", (), unanswerable=True),
    ]
    payload = _run_strategies(index, cfg, cases=cases)["dense"]
    groups = payload["by_language"]
    assert set(groups) == {"zh", "en"}
    assert groups["zh"]["cases"] == 1 and groups["en"]["cases"] == 1
    assert groups["zh"]["metrics"]["recall@10"] == pytest.approx(1.0)
    assert groups["en"]["metrics"]["recall@10"] == pytest.approx(0.0)
    # 整轮均值是两组的中点：说明分组真的在切数据，不是把同一份数字复读两遍
    assert payload["metrics"]["recall@10"] == pytest.approx(0.5)
    for lang, group in groups.items():
        rows = [c["metrics"] for c in payload["cases"] if c["corpus_language"] == lang]
        assert group["metrics"] == aggregate(rows)

    moved = _run_strategies(
        index, cfg, cases=[replace(cases[1], corpus_language="zh"), cases[0]]
    )["dense"]
    assert set(moved["by_language"]) == {"zh"}
    assert moved["by_language"]["zh"]["cases"] == 2
    assert moved["by_language"]["zh"]["metrics"]["recall@10"] == pytest.approx(0.5)


def test_report_prints_each_language_group_with_its_case_count(tmp_path):
    from eval.retrieval_eval.runner import format_report

    cfg = EvalConfig(embedder="hashing", strategies=("dense",), cache_dir=tmp_path)
    report = run_eval(Path("."), cfg)
    rendered = format_report(report)
    for lang, group in report["strategies"]["dense"]["by_language"].items():
        assert f"[{lang}] {group['cases']} 条:" in rendered
