"""钉住后端 trace span 名 ↔ 前端 STAGE_LABELS 键的对账（S-RS 评审 P2-3）。

spec（retrieval-stage-pipeline.md Workspace 段）承诺把「阶段名 ↔ STAGE_LABELS 键」
的一致性做成一条测试，交付时没有兑现——前端契约此前只靠"节点 id 逐字沿用"的自觉。
这条把账对上：

- 管线 19 个阶段里带 span 的 12 个（其余是次序守卫/无 duration 收口），span 名必须
  都能被前端 :data:`STAGE_LABELS` 认出，否则耗时条显示裸英文键；
- 前端还认 4 个管线外的 span（generate / verify / verify_l2 / verify_l3，分别来自
  generation 与 verify 层），这些的发射点也在本文件钉住，改名一样会红；
- 反向：前端 STAGE_LABELS 里不许有后端任何发射点都不产出的死键。

读的前端事实是 ``frontend/src/strategy/traceParse.ts`` 的源码文本（提取
``STAGE_LABELS`` 的键集合）；后端事实是 live 注册表 + 发射点 grep。
"""

from __future__ import annotations

from pathlib import Path
import re

from retrieval.stages import retrieval_stage_order

REPO = Path(__file__).resolve().parents[1]
TRACE_PARSE = REPO / "frontend" / "src" / "strategy" / "traceParse.ts"


def _frontend_stage_label_keys() -> set[str]:
    text = TRACE_PARSE.read_text(encoding="utf-8")
    block = re.search(r"const STAGE_LABELS[^=]*=\s*\{(.*?)\};", text, re.S)
    assert block, "前端 traceParse.ts 里找不到 STAGE_LABELS"
    return set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*):", block.group(1), re.M))


def _backend_pipeline_span_names() -> set[str]:
    return {
        stage.span_name for stage in retrieval_stage_order() if stage.span_name
    }


def test_every_pipeline_span_is_labelled_by_the_frontend() -> None:
    frontend_keys = _frontend_stage_label_keys()
    missing = _backend_pipeline_span_names() - frontend_keys
    assert missing == set(), f"管线 span 名前端没有标签（耗时条会露裸键）：{sorted(missing)}"


def test_the_extra_frontend_keys_have_live_backend_emitters() -> None:
    """generate/verify 家族来自管线外，发射点消失/改名时前端死键必须被这条抓出来。"""
    frontend_keys = _frontend_stage_label_keys()
    pipeline_spans = _backend_pipeline_span_names()
    extra = frontend_keys - pipeline_spans
    # 前端额外认的键应恰好是这四个管线外 span。
    assert extra == {"generate", "verify", "verify_l2", "verify_l3"}, sorted(extra)
    # 每个键在后端都有真实的 add_span 发射点。
    sources: list[Path] = []
    for folder in ("rag_stream.py", "generation", "verify", "scripts"):
        path = REPO / folder
        sources.append(path) if path.is_file() else sources.extend(sorted(path.glob("*.py")))
    for key in sorted(extra):
        hits = [
            src
            for src in sources
            if re.search(rf'add_span\(\s*[\'"]{key}[\'"]', src.read_text(encoding="utf-8"))
        ]
        assert hits, f"前端标签 {key!r} 在后端没有任何 add_span 发射点（死键）"


def test_the_two_sides_cannot_drift_by_renaming_one_side() -> None:
    """改名一侧必红：两边键集合的对称差必须恰为那四个管线外 span。"""
    frontend_keys = _frontend_stage_label_keys()
    pipeline_spans = _backend_pipeline_span_names()
    assert frontend_keys ^ pipeline_spans == {
        "generate",
        "verify",
        "verify_l2",
        "verify_l3",
    }
