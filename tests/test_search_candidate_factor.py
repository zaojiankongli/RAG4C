"""检索候选放大系数（``pipeline.search_candidate_factor``）的守卫测试。

背景：``retrieval/stages.py`` 里请求的候选数原本是写死的 ``top_k * 2``。
2026-09-27 用新建的检索评测（``eval/run_retrieval_eval.py``，真实 bge-m3 +
bge-reranker-v2-m3）实测了 2 / 3 / 5 三个系数，排序质量在 3 附近最好、5 反而
变差，所以这个旋钮必须能配，不能写死。

三条守卫：
1. 默认值 2 → 与改造前的硬编码行为逐字一致（行为等价）；
2. 配置成 3 → 请求条数真的跟着变（旋钮有效，不是摆设）；
3. 非法值 / 缺字段 → 回退默认而不是把检索搞挂（fail-safe）。
外加一条源码守卫：stages.py 里不许再出现写死的 ``top_k * 2``。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from retrieval.stages import SearchStage, candidate_request_count

_STAGES_PATH = Path("retrieval/stages.py")


def _settings(**overrides) -> SimpleNamespace:
    base = {"top_k": 8, "search_candidate_factor": 2, "hybrid_search_on": True}
    base.update(overrides)
    return SimpleNamespace(**base)


def test_default_factor_matches_pre_refactor_hardcoded_value():
    """默认 2 必须等价于改造前的 ``top_k * 2``——这是行为等价的验收点。"""
    assert candidate_request_count(_settings()) == 16
    assert candidate_request_count(_settings(top_k=10)) == 20


def test_factor_is_configurable():
    assert candidate_request_count(_settings(search_candidate_factor=3)) == 24
    assert candidate_request_count(_settings(top_k=10, search_candidate_factor=5)) == 50


def test_factor_one_is_allowed_but_zero_falls_back():
    assert candidate_request_count(_settings(search_candidate_factor=1)) == 8
    assert candidate_request_count(_settings(search_candidate_factor=0)) == 16
    assert candidate_request_count(_settings(search_candidate_factor=-3)) == 16


def test_missing_or_broken_factor_falls_back_to_default():
    assert candidate_request_count(SimpleNamespace(top_k=8)) == 16  # 字段缺失
    assert candidate_request_count(_settings(search_candidate_factor="abc")) == 16
    assert candidate_request_count(_settings(search_candidate_factor=None)) == 16


def test_stage_reports_requested_count_through_the_knob():
    """阶段对外宣告的 requested_count 也走同一个系数（两处不能各算各的）。"""
    stage = SearchStage()
    gate = stage.eligible(None, SimpleNamespace(p=_settings(top_k=10, search_candidate_factor=3)))
    assert gate.start_attrs["requested_count"] == 30


def test_no_hardcoded_search_candidate_count_left_in_stages():
    """源码守卫：请求候选数不许再写死，必须走 :func:`candidate_request_count`。

    只盯检索请求那两处（``requested_count=`` / ``top_k=``），多样性阶段自己的
    ``mmr_select(k=...)`` 是另一个语义（选多少条进入下一阶段），不在本轮范围内，
    见 docs/2026-09-27-retrieval-quality-baseline.md 的遗留项。
    """
    source = _STAGES_PATH.read_text(encoding="utf-8")
    assert "requested_count=ctx.p.top_k * 2" not in source
    assert "top_k=p.top_k * 2" not in source


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
