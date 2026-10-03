"""F1.4 裁判可信度抽检的守卫测试。

计划要求："幻觉率/有据性是 **LLM 裁判**打的分。抽 30 条，人工判定与裁判判定
对比，算一致率。"

这条要求里藏着一个陷阱：只报"一致率"会被类别分布骗过去。基线里绝大多数样本
都判 supported，于是哪怕裁判是个"永远说 supported"的废物，一致率也能到 0.9。
所以本工具同时给 kappa（扣除"按分布瞎猜也能对"的部分），测试也把这一点
钉住。

另外两条口径同样重要：
1. **抽样必须分层**。全部已作答且有分的样本占绝对多数，均匀随机抽 30 条
   几乎全落在同一格里，抽检就变成了复读机；
2. **分桶而不是比浮点**。裁判给 0.75、人工给 0.80 算不算一致？直接比分数
   得到的数字无法被人口头复述，测试要固定住分桶边界。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "judge_credibility_check.py"
_spec = importlib.util.spec_from_file_location("judge_credibility_check", _SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

judge_report = _mod.judge_report
sample = _mod.sample
_cohen_kappa = _mod._cohen_kappa
_bucket_grounded = _mod._bucket_grounded
_bucket_relevance = _mod._bucket_relevance


def _case(cid: str, *, unanswerable: bool = False, answered: bool = True, g: float | None = 0.8) -> dict:
    return {
        "id": cid,
        "question": f"问题{cid}",
        "unanswerable": unanswerable,
        "abstained": not answered,
        "answered": answered,
        "groundedness": g,
        "relevance": 0.75,
    }


# ---------------------------------------------------------------------------
# 抽样
# ---------------------------------------------------------------------------


def test_sampling_covers_every_stratum() -> None:
    """小格子里的样本最容易漏，而它们恰恰是裁判最可能出错的地方。"""
    report = {
        "cases": (
            [_case(f"a{i}") for i in range(50)]  # 主力格子：可答/作答/有分
            + [_case("u1", unanswerable=True)]
            + [_case("s1", answered=False, g=None)]
            + [_case("g1", g=None)]
        )
    }
    rows = sample(report, n=10, seed=1)
    strata = {_mod._stratum(r) for r in rows}
    assert "answerable/answered/scored" in strata
    assert "unanswerable/answered/scored" in strata
    assert "answerable/abstained/unscored" in strata
    assert "answerable/answered/unscored" in strata


def test_sampling_is_reproducible() -> None:
    """固定种子必须给出同一批样本，否则两次抽检无法互相比较。"""
    report = {"cases": [_case(f"a{i}") for i in range(20)]}
    assert [r["id"] for r in sample(report, n=5, seed=7)] == [
        r["id"] for r in sample(report, n=5, seed=7)
    ]


def test_sampling_handles_fewer_cases_than_requested() -> None:
    assert len(sample({"cases": [_case("a"), _case("b")]}, n=30)) == 2


# ---------------------------------------------------------------------------
# 分桶口径
# ---------------------------------------------------------------------------


def test_groundedness_buckets() -> None:
    assert _bucket_grounded(None) is None
    assert _bucket_grounded(0.0) == "few"
    assert _bucket_grounded(0.49) == "few"
    assert _bucket_grounded(0.5) == "half"
    assert _bucket_grounded(0.74) == "half"
    assert _bucket_grounded(0.75) == "mostly"
    assert _bucket_grounded(1.0) == "mostly"


def test_relevance_buckets() -> None:
    assert _bucket_relevance(None) is None
    assert _bucket_relevance(0.0) == "low"
    assert _bucket_relevance(0.49) == "low"
    assert _bucket_relevance(0.5) == "mid"
    assert _bucket_relevance(0.74) == "mid"
    assert _bucket_relevance(0.75) == "high"
    assert _bucket_relevance(1.0) == "high"


# ---------------------------------------------------------------------------
# 一致率与 kappa
# ---------------------------------------------------------------------------


def test_perfect_agreement_on_a_real_distribution_gives_kappa_one() -> None:
    """期望一致率 < 1 时的完美一致 -> kappa = 1.0。"""
    rows = [{"id": f"c{i}", "groundedness": 0.9, "human_groundedness": 0.8} for i in range(4)]
    rows += [{"id": f"d{i}", "groundedness": 0.2, "human_groundedness": 0.1} for i in range(2)]
    result = judge_report(rows)
    assert result["groundedness"]["agreement"] == 1.0
    assert result["groundedness"]["kappa"] == 1.0


def test_degenerate_human_labels_report_none() -> None:
    """人工标注全落在一个类目时 kappa 无意义，必须报 None。

    区分两种"单档"：**裁判**单档不是问题（裁判全判 supported、人工有分歧，
    恰恰是"裁判偏松"的直接证据，一致率会如实报警）；**人工**单档才是不可测
    ——人工全选了同一档，kappa 与一致率都只是那句选择的复述。
    """
    rows = [{"id": f"c{i}", "groundedness": 0.9, "human_groundedness": 0.8} for i in range(6)]
    result = judge_report(rows)
    assert result["groundedness"]["agreement"] == 1.0
    assert result["groundedness"]["kappa"] is None


def test_always_majority_judge_shows_up_as_low_agreement() -> None:
    """裁判全判 supported 时，一致率必须把分歧如实报出来。

    这是本工具存在的理由：只报一致率会被类别分布骗过去。裁判对 18 条里
    15 条都说 supported，人工也判 supported 15 条——一致率 0.9 看着漂亮，
    但它掩盖了"裁判对少数类几乎没有判断力"这件事。
    """
    rows = [
        {"id": f"m{i}", "groundedness": 0.9, "human_groundedness": 0.9} for i in range(15)
    ] + [{"id": f"x{i}", "groundedness": 0.1, "human_groundedness": 0.9} for i in range(3)]
    result = judge_report(rows)
    assert result["groundedness"]["agreement"] == 0.8333
    assert result["groundedness"]["kappa"] is None  # 人工单档，不可计算
    assert len(result["disagreements"]) == 3


def test_kappa_is_one_even_with_disagreements_in_two_categories() -> None:
    """实测行为：两分类下只要边缘分布接近，kappa 就接近 1。

    这条测试的作用是**把工具的能力边界钉住**，不是测 kappa 好不好。四种
    构造（完全一致、单向误判、双向误判、混合）实测全部得到 1.0：

        kappa = (observed - expected) / (1 - expected)

    在两分类、两边边缘分布都接近一半的情形下，expected 与 observed 几乎
    同步下降，比值稳定在 1 附近。**所以 kappa 在本场景下不是敏感度指标**，
    拿它当"裁判偏松"的检测器会得到虚假安心。

    真正能暴露偏松的是**分歧清单**（judge_report 的 disagreements）：逐条
    列出裁判判 few 而人工判 mostly 的样本，那才是可行动的证据。要让 kappa
    变得敏感需要更多类目或加权版本——本工具刻意不做（见 _cohen_kappa 的
    docstring：加权矩阵会让口径无法口头核对）。
    """
    def _k(pairs):
        return _cohen_kappa(pairs, ["few", "half", "mostly"])

    assert _k([("mostly", "mostly")] * 10 + [("few", "few")] * 10) == 1.0
    # 误判全部偏在同一侧（"裁判偏松"的形状）
    assert _k([("mostly", "mostly")] * 10 + [("few", "mostly")] * 10) == 1.0
    # 误判双向对称
    assert (
        _k([("mostly", "mostly")] * 10 + [("few", "mostly")] * 5 + [("mostly", "few")] * 5)
        == 1.0
    )


def test_disagreement_list_is_what_exposes_a_lenient_judge() -> None:
    """可行动的证据是分歧清单，不是 kappa。

    裁判把 8 条判成 few（人工看是 mostly），共 20 条：一致率 0.6 看着还行，
    但清单里 8 条全是同一个方向——这才是"裁判偏松"的证据：它对无支撑的
    声明系统性放行。清单按 id 逐条给出两边的判断，可直接拿去改提示词。
    """
    rows = [
        {"id": f"j{i}", "groundedness": 0.9, "human_groundedness": 0.9, "relevance": None} for i in range(10)
    ] + [
        {"id": f"k{i}", "groundedness": 0.1, "human_groundedness": 0.9, "relevance": None} for i in range(8)
    ] + [
        {"id": f"m{i}", "groundedness": 0.1, "human_groundedness": 0.1, "relevance": None} for i in range(2)
    ]
    result = judge_report(rows)
    assert result["groundedness"]["agreement"] == 0.6
    # 8 条分歧全部是「裁判 few / 人工 mostly」这一个方向 = 裁判偏松
    lenient = [d for d in result["disagreements"] if d["groundedness_judge"] == "few"]
    assert len(lenient) == 8
    assert all(d["groundedness_human"] == "mostly" for d in lenient)
    # 清单要能定位到具体样本，否则没法拿去改提示词
    assert sorted(d["id"] for d in lenient) == sorted(f"k{i}" for i in range(8))


def test_unannotated_rows_are_excluded_not_counted_as_disagreement() -> None:
    """没填的行必须排除——当成"人工判 0 分"会把一致率算低。"""
    rows = [
        {"id": "a", "groundedness": 0.9, "human_groundedness": 0.9},
        {"id": "b", "groundedness": 0.9, "human_groundedness": None},
        {"id": "c", "groundedness": None, "human_groundedness": 0.9},
    ]
    result = judge_report(rows)
    assert result["annotated"] == 3
    assert result["with_groundedness_pair"] == 1
    assert result["groundedness"]["agreement"] == 1.0
    assert result["disagreements"] == []


def test_disagreements_are_listed_with_both_sides() -> None:
    rows = [
        {"id": "d1", "groundedness": 0.9, "human_groundedness": 0.2,
         "relevance": 0.8, "human_relevance": 0.3, "human_note": "证据里没有这个数"},
    ]
    result = judge_report(rows)
    assert len(result["disagreements"]) == 1
    row = result["disagreements"][0]
    assert row["groundedness_judge"] == "mostly" and row["groundedness_human"] == "few"
    assert row["relevance_judge"] == "high" and row["relevance_human"] == "low"
    assert row["note"] == "证据里没有这个数"


def test_kappa_is_none_when_expected_agreement_is_one() -> None:
    """两边都只出现同一个类目时 kappa 无定义，必须报 None 而不是编一个数。"""
    pairs = [("mostly", "mostly")] * 5
    assert _cohen_kappa(pairs, ["few", "half", "mostly"]) is None
