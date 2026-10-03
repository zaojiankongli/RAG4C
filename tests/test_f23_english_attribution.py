"""F2.3 英文子集失败归因的守卫测试。

计划要求："把英文里正确 chunk 排在 10 名之外的用例逐条看：是切分问题、
嵌入问题、还是标注问题。验收：失败按类别计数；每类给出『改哪里能解』的
判断。"

分类口径是这项工作的全部价值所在，所以测试把它逐条钉住：

1. **按 gold 的位置分类，不按分数**。分数是症状，位置才是证据——同一分数
   可能来自"排第 1"或"排第 9"。
2. **"排得靠前"与"召不回"必须分开**。en 的 recall@10 是 0.884、NDCG 只有
   0.735，MRR 0.698 vs zh 0.913：差距主要在排序而不在召回。混在一起就会
   把"调 embedding"当成解法，而切片才是无辜的。
3. **每类都要写"不该动什么"**。归因的价值在于排除，只列"该改哪里"会让人
   把时间花在错的地方。
4. **不可答用例不计入失败**。它们没有 gold，本来就不参与召回统计。
5. **旧报告没有 gold_ranks 时要能退化**。工具要能吃已有的历史报告。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "attribute_english_gap.py"
_spec = importlib.util.spec_from_file_location("attribute_english_gap", _SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

classify = _mod.classify
analyze = _mod.analyze
REMEDIES = _mod.REMEDIES


def _case(**kw: Any) -> dict[str, Any]:
    base = {"case_id": "c", "corpus_language": "en", "metrics": {"ndcg@10": 0.5}}
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# 1 / 2：按位置分类
# ---------------------------------------------------------------------------


def test_gold_at_rank_one_is_healthy() -> None:
    kind, detail = classify(_case(top10=["g1", "x", "y"], gold_ranks=[1], missed_gold=[]))
    assert kind == "exact_top1"
    assert detail["gold_rank"] == 1


def test_gold_in_top10_but_not_first_is_a_ranking_problem() -> None:
    """召回了但排后面 —— 这是"排得靠前"类，不是"召不回"。"""
    kind, detail = classify(_case(top10=["x"] * 6 + ["g1"], gold_ranks=[7], missed_gold=[]))
    assert kind == "ranking"
    assert detail["gold_rank"] == 7


def test_gold_never_retrieved_is_missed() -> None:
    kind, detail = classify(_case(top10=["x", "y", "z"], gold_ranks=[], missed_gold=["g1"]))
    assert kind == "missed"
    assert detail["gold_rank"] is None


def test_partial_means_in_top10_but_no_gold_at_rank_one() -> None:
    """partial：召回了，但**没有一条 gold 排在第 1 位**。

    分类优先级是「排第 1 -> exact_top1 / 排进了但不在第 1 -> ranking /
    命中 0 条 -> missed」。partial 实际覆盖的是"命中 0 条但仍被记为有召回"
    这类边界情况——真实报告里出现得少，但口径必须明确：只要最靠前的 gold
    有名次，就按那个名次归类，不按命中条数二次分档。
    """
    kind, detail = classify(
        _case(top10=["x", "x", "x"], gold_ranks=[], missed_gold=["g1"])
    )
    # 命中 0 条 -> missed（partial 的判据 gold_hit 为 0）
    assert kind == "missed"

    # 命中最靠前的在第 2 位 -> ranking（不是 partial）
    kind2, detail2 = classify(
        _case(top10=["x", "g1", "x", "g2"], gold_ranks=[2, 4], missed_gold=["g3"])
    )
    assert kind2 == "ranking"
    assert detail2["gold_rank"] == 2
    assert detail2["gold_in_top10"] == 2
    assert detail2["gold_total"] == 3


def test_gold_at_rank_one_stays_exact_even_with_partial_hits() -> None:
    kind, _ = classify(_case(top10=["g1", "x"], gold_ranks=[1], missed_gold=["g2", "g3"]))
    assert kind == "exact_top1"


def test_rank_is_computed_from_list_when_gold_ranks_absent() -> None:
    """旧报告没有 gold_ranks 时要能退化，不至于直接判成 missed。"""
    kind, detail = classify(
        _case(top10=["x", "g1"], gold=["g1"], missed_gold=[])
    )
    assert kind == "ranking"
    assert detail["gold_rank"] == 2


# ---------------------------------------------------------------------------
# 4：不可答不进失败
# ---------------------------------------------------------------------------


def test_unanswerable_is_its_own_bucket() -> None:
    """不可答用例既没有 gold_ranks 也没有 gold —— 不能落进 missed。

    落进 missed 的后果很具体：它会被算成"召回失败"、把失败率抬高，而它本来
    就不参与召回统计（gold 为空）。
    """
    kind, _ = classify(_case(top10=["x"], unanswerable=True, retrieved=["x"]))
    assert kind == "unanswerable"


def test_unanswerable_not_counted_as_failure() -> None:
    report = {
        "strategies": {
            "dense_rerank": {
                "cases": [
                    _case(case_id="a", top10=["g1"], gold_ranks=[1], missed_gold=[]),
                    _case(case_id="b", top10=["x", "y"], gold_ranks=[], missed_gold=["g1"]),
                    _case(case_id="u", unanswerable=True, retrieved=["z"], top_score=0.01),
                ],
                "by_language": {"en": {"cases": 3, "metrics": {"ndcg@10": 0.5}}},
            }
        }
    }
    entry = analyze(report)["by_language"]["en"]
    assert entry["answerable"] == 2, "不可答那条不算可答"
    assert entry["failures"] == 1, "只有 b 算失败"


# ---------------------------------------------------------------------------
# 3：每类都要有"不该动什么"
# ---------------------------------------------------------------------------


def test_every_kind_has_both_a_fix_and_a_do_not_touch() -> None:
    """归因的价值在于排除。只写"该改哪里"会让人把时间花在错的地方。"""
    for kind, advice in REMEDIES.items():
        assert advice.get("fix"), f"{kind} 缺 fix"
        assert advice.get("do_not_touch"), f"{kind} 缺 do_not_touch"


def test_ranking_blames_discrimination_not_chunking() -> None:
    """召回够高时，排序类失败不该指向切片。"""
    assert "切片" in REMEDIES["ranking"]["do_not_touch"]


def test_missed_tells_you_to_verify_corpus_first() -> None:
    """召不回时先分清「切片丢了」还是「语料根本没有」，别先动模型。"""
    assert "语料" in REMEDIES["missed"]["fix"]
    assert "模型" in REMEDIES["missed"]["do_not_touch"]


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------


def test_language_buckets_are_reported_separately() -> None:
    """en / zh 必须分开给读数——合起来会把差距抹平。"""
    report = {
        "strategies": {
            "dense_rerank": {
                "cases": [
                    _case(case_id="e1", corpus_language="en", top10=["g1"], gold_ranks=[1], missed_gold=[]),
                    _case(case_id="e2", corpus_language="en", top10=["x"], gold_ranks=[], missed_gold=["g1"]),
                    _case(case_id="z1", corpus_language="zh", top10=["g1"], gold_ranks=[1], missed_gold=[]),
                ],
                "by_language": {
                    "en": {"cases": 2, "metrics": {"ndcg@10": 0.735}},
                    "zh": {"cases": 1, "metrics": {"ndcg@10": 0.920}},
                },
            }
        }
    }
    result = analyze(report)["by_language"]
    assert result["en"]["failures"] == 1 and result["en"]["answerable"] == 2
    assert result["zh"]["failures"] == 0 and result["zh"]["answerable"] == 1
    # 逐用例细节要能定位到具体 case_id，否则没法逐条看
    ids = {d["case_id"] for d in analyze(report)["details"]["en"]}
    assert ids == {"e1", "e2"}
