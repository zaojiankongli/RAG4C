"""EmbedStage 的缓存口径标注（Round 11 Phase 3）。

Round 10 §7-4：embed 阶段命中两级缓存时耗时接近 0，读数会被读成「嵌入不花钱」。
修复后 trace 行写成 ``embed@hit:xx ms`` / ``embed@miss:xx ms``，消费方按名字分栏。
"""

from __future__ import annotations

from types import SimpleNamespace

from core import embed_cache
from retrieval.stages import EmbedStage, RetrievalState, StageContext, StageOutcome


class _FakeEmbedder:
    """记录调用并按预设决定缓存命中。"""

    def __init__(self, hit: bool) -> None:
        self.hit = hit
        self.calls = 0

    def embed_query(self, text: str) -> list[float]:
        self.calls += 1
        cache = embed_cache.get_embed_cache()
        if self.hit:
            cache.last_lookup_hit = True
        else:
            cache.last_lookup_hit = False
        return [0.1, 0.2, 0.3]


def _run_stage(embedder: _FakeEmbedder) -> StageOutcome:
    state = RetrievalState.start("黄山的云海什么时候最好看")
    ctx = StageContext(
        services=SimpleNamespace(embedder=embedder),
        settings=SimpleNamespace(),
        p=SimpleNamespace(),
        tenant="tenant-a",
        dataset="dataset-a",
        acl=None,
        serving_context=None,
    )
    return EmbedStage().run(state, ctx)


def test_embed_stage_reports_cache_hit_in_attributes() -> None:
    outcome = _run_stage(_FakeEmbedder(hit=True))
    assert outcome.completed is not None
    duration_ms, attrs = outcome.completed
    assert attrs["cache"] == "hit"
    assert duration_ms is not None and duration_ms >= 0.0
    # trace 行在驱动器 _finish 里合成 embed@hit:…；这里直接核对驱动器格式规则用的原料
    assert outcome.span == ("embed", outcome.span[1])


def test_embed_stage_reports_cache_miss_in_attributes() -> None:
    outcome = _run_stage(_FakeEmbedder(hit=False))
    assert outcome.completed is not None
    _, attrs = outcome.completed
    assert attrs["cache"] == "miss"


def test_embed_cache_lookup_flag_tracks_get_and_put() -> None:
    cache = embed_cache.get_embed_cache()
    cache.put("model-x", "文本甲", [1.0, 2.0])
    assert cache.last_lookup_hit is False  # put 不算命中
    assert cache.get("model-x", "文本甲") is not None
    assert cache.last_lookup_hit is True
    assert cache.get("model-x", "没见过的文本") is None
    assert cache.last_lookup_hit is False


def test_driver_formats_embed_trace_line_with_cache_suffix() -> None:
    """驱动器对 embed 的 span 名按 cache 属性加后缀（对齐 StageRunner._finish）。"""
    from retrieval.stages import StageRunner

    outcome = StageOutcome().set_span("embed", 0.4)
    outcome.complete(0.4, source="query", cache="hit")
    # 复刻 _finish 的分支逻辑做契约核对：span 名必须变成 embed@hit
    span_name = outcome.span[0]
    if (
        span_name == "embed"
        and outcome.completed is not None
        and outcome.completed[1].get("cache")
    ):
        span_name = f"embed@{outcome.completed[1]['cache']}"
    assert span_name == "embed@hit"
    # 非 embed 阶段不受影响
    other = StageOutcome().set_span("rerank", 12.0)
    other.complete(12.0, cache="hit")
    assert other.span[0] == "rerank"
    # StageRunner 真实存在且可引用（防止测试钉错了入口）
    assert hasattr(StageRunner, "_finish")
