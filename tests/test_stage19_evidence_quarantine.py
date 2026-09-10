"""Stage 19 浏览器证据隔离哨兵。

`tests/test_enterprise_knowledge_base_release_playwright_evidence.py` 整文件 skip，
原因见该文件的 QUARANTINE_REASON：证据需要按**当前前端**重新生成——harness 依赖的
DOM 选择器（Release 表的「查看 Release 42」按钮、Channel 选择器、移动端卡片）已经
和现在的界面不一致，重跑会在 12 个身份上各卡 30s 超时。

这个哨兵的作用是让隔离不会被忘记：一旦有人把 harness 的定位器对齐到当前 UI 并成功
重跑（证据文件重新变成 `status == "passed"`），本测试立刻失败并提示摘掉 skip。
"""

from __future__ import annotations

import json
from pathlib import Path

EVIDENCE = (
    Path(__file__).resolve().parents[1]
    / "output"
    / "playwright"
    / "enterprise-knowledge-base-release-stage19"
)
RESULT = EVIDENCE / "stage19-browser-result.json"
QUARANTINED_TEST = "tests/test_enterprise_knowledge_base_release_playwright_evidence.py"


def test_stage19_evidence_is_still_blocked_so_the_quarantine_holds() -> None:
    if not RESULT.is_file():
        return  # 证据不在（新克隆/被清理）：skip 依然成立，无需解除
    payload = json.loads(RESULT.read_text(encoding="utf-8"))
    if payload.get("status") == "passed":
        raise AssertionError(
            "Stage 19 浏览器证据已重新生成且通过：请移除 "
            f"{QUARANTINED_TEST} 里的 pytestmark skip，并删除本哨兵测试。"
        )


def test_quarantined_evidence_test_still_carries_the_skip_mark() -> None:
    """反向保证：隔离标记不能被无声删掉（删了就会多出一条长期红的验收门）。"""
    text = (Path(__file__).resolve().parents[1] / QUARANTINED_TEST).read_text(encoding="utf-8")
    assert "pytestmark = pytest.mark.skip(" in text, QUARANTINED_TEST
