"""压测脚本的弃权率统计守卫。

## 为什么要有这个字段

F3.5 门禁第一次跑出「QPS 116.9 / P50 262.6ms / 成功率 100%」，看起来很好。
但对照单请求串行实测（10~30s）才发现：**差 30 倍**。原因是并发下多数请求
走了弃权——弃权路径不调生成与 L3 裁判，延迟只有完整链路的零头。

也就是说那批读数测的是「检索 + 快速拒答」，不是完整问答链路。而原来的输出
**没有任何字段能让人看出这一点**，读数一旦被引用就会变成错误结论。

所以：``abstained_rate`` 必须跟着 QPS 一起报。

## 守卫三条

1. **只统计成功请求**——失败请求没有 answer 可言；
2. **另报 ``abstained_known_rate``**——响应形状变了要看得见，解析不出时返回
   ``None`` 而不是 ``False``（静默当成"没弃权"会把这个缺口盖掉）；
3. **非 2xx 时 ``abstained`` 是 ``None``** 而不是 False。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "bench" / "load_query.py"
_spec = importlib.util.spec_from_file_location("load_query", _SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_extract_abstained = _mod._extract_abstained


def _body(result: dict | None, **extra) -> bytes:
    payload = {"using_mock": False, "duration_ms": 1.0, **extra}
    if result is not None:
        payload["result"] = result
    return json.dumps(payload).encode()


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def test_reads_abstained_from_result() -> None:
    assert _extract_abstained(_body({"abstained": True})) is True
    assert _extract_abstained(_body({"abstained": False})) is False


def test_missing_field_is_none_not_false() -> None:
    """字段缺失必须返回 None。

    返回 False 会被读成"确认没弃权"——而真实情况是**没看见**。那个区别正是
    本测试要守的：静默的 False 会把响应契约变化盖掉。
    """
    assert _extract_abstained(_body({"answer": "x"})) is None
    assert _extract_abstained(_body(None)) is None


def test_unparsable_body_is_none() -> None:
    """响应体不是 JSON 时不能抛——压测脚本不该因为形状变了就崩掉。"""
    assert _extract_abstained(b"<html>502</html>") is None
    assert _extract_abstained(b"") is None


def test_result_not_a_dict_is_none() -> None:
    """result 不是 dict（比如契约改成字符串）时返回 None，不做属性猜测。"""
    assert _extract_abstained(json.dumps({"result": "ok"}).encode()) is None
    assert _extract_abstained(json.dumps({"result": ["a"]}).encode()) is None


# ---------------------------------------------------------------------------
# 汇总口径
# ---------------------------------------------------------------------------


def test_summary_reports_both_rates() -> None:
    """summary 里两个字段都要在，且语义不同。

    ``abstained_rate`` 是行为（多少请求拒答），
    ``abstained_known_rate`` 是**观测质量**（这个比例本身可不可信）。
    """
    import inspect

    src = inspect.getsource(_mod)
    assert '"abstained_rate"' in src
    assert '"abstained_known_rate"' in src
    # 分母必须是"字段已知的那部分"，不是全部成功数
    assert "len(known) / len(ok_rows)" in src
    # 只统计成功请求
    assert "if code == 200" in src


def test_post_returns_four_tuple() -> None:
    """_post 的返回是 4 元组——少一个就解包失败。

    从**调用点**验证而不是看注解：``load_query.py`` 里有 ``from __future__
    import annotations``，注解是字符串、``len()`` 量的是字符数（35）而不是元
    素数（4）。真正会崩的是调用点的解包，所以直接查那里。
    """
    import inspect

    src = inspect.getsource(_mod)
    assert "for code, d, _, _ in results" in src, "worker 汇总按 4 元组解包"
    assert "for _, _, tag, _ in results" in src, "错误标签按 4 元组解包"
    # 不能残留 3 元组解包（那是改动前的形态）
    assert "for code, d, _ in results" not in src
    assert "for _, _, tag in results" not in src
