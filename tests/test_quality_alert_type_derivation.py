"""扩展轴 #9：`gate_reason`/观察事实 → `alert_type` 只由一张有序规则表决定。

判据是"新增实现零改分支"，不是行数。所以这里最重要的一条是
`test_adding_a_reason_needs_no_branch_and_touches_no_host_file`：它往声明表里塞一条全新的
映射，断言派生结果跟着变，**并且**断言生产侧宿主文件的字节与注册前逐字节相同 —— 后半句才
是"零改分支"的硬证据。

行为等价靠 `_reference`：那是重构前那条 if 链的原样冻结副本（本仓 `_KNOWLEDGE_SERVING_ORIGINAL_*`
的老规矩），在枚举网格上与规则表逐点比对。次序不是实现细节 —— `gate_blocked` 一旦排到两条
clock 启发式之后，"认证过期且闸口受阻"就会派生成 `certification_expired`，所以次序单独钉一条。
"""

from __future__ import annotations

import itertools
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.quality_alert_types as att
from core.release_quality_gate_states import gate_states_storage

REPO = Path(__file__).resolve().parents[1]
HOST = REPO / "core/enterprise_release_quality_alerts.py"
ORM = REPO / "models/orm.py"
MIGRATION = REPO / "catalog_migrations/versions/0031_enterprise_release_quality_operations.py"

SEVERITIES = ("healthy", "warning", "critical", "unavailable")
GATE_REASONS = (
    "",
    "certification_stale",
    "stale_evidence",
    "certification_revoked",
    "certification_expired",
    "waiver_expired",
    "waiver_expiring",
    "certification_expiring",
    "  CERTIFICATION_STALE  ",
    "unrelated_reason",
)
MINUTES = (None, -5, 0, 5)


def _reference(observation) -> str:
    """重构前 `_derive_alert_type` 的原样副本 —— 行为等价的裁判，不要"顺手更新"它。"""
    reason = str(observation.gate_reason or "").strip().casefold()
    if str(observation.severity) == "unavailable" or str(observation.gate_state) == "unavailable":
        return "quality_authority_unavailable"
    if reason in {"certification_stale", "stale_evidence", "certification_revoked"}:
        return "certification_stale"
    if reason == "certification_expired":
        return "certification_expired"
    if reason == "waiver_expired":
        return "waiver_expired"
    if reason == "waiver_expiring":
        return "waiver_expiring"
    if reason == "certification_expiring":
        return "certification_expiring"
    if str(observation.gate_state) == "blocked":
        return "quality_gate_blocked"
    if (
        observation.certification_id is not None
        and observation.minutes_to_certification_expiry is not None
    ):
        return (
            "certification_expired"
            if int(observation.minutes_to_certification_expiry) <= 0
            else "certification_expiring"
        )
    if observation.waiver_id is not None and observation.minutes_to_waiver_expiry is not None:
        return (
            "waiver_expired" if int(observation.minutes_to_waiver_expiry) <= 0 else "waiver_expiring"
        )
    return "quality_authority_unavailable"


def _observation(severity, gate_state, gate_reason, has_cert, cert_minutes, has_waiver, waiver_minutes):
    return SimpleNamespace(
        severity=severity,
        gate_state=gate_state,
        gate_reason=gate_reason,
        certification_id="cert-1" if has_cert else None,
        minutes_to_certification_expiry=cert_minutes,
        waiver_id="waiver-1" if has_waiver else None,
        minutes_to_waiver_expiry=waiver_minutes,
    )


def _grid():
    for severity, gate_state, reason, hc, cm, hw, wm in itertools.product(
        SEVERITIES, sorted(gate_states_storage()), GATE_REASONS, (True, False), MINUTES, (True, False), MINUTES
    ):
        yield severity, gate_state, reason, hc, cm, hw, wm


def test_the_rule_table_reproduces_the_frozen_chain_on_the_whole_grid() -> None:
    """整网格行为等价：一条都不许差。"""
    mismatches = []
    seen = set()
    for severity, gate_state, reason, hc, cm, hw, wm in _grid():
        observation = _observation(severity, gate_state, reason, hc, cm, hw, wm)
        got = att.derive_alert_type(observation)
        seen.add(got)
        if got != _reference(observation):
            mismatches.append((severity, gate_state, reason, hc, cm, hw, wm, got, _reference(observation)))
    assert len(mismatches) == 0, f"{len(mismatches)} 处不等价，前 5 条：{mismatches[:5]}"
    # 反向自查：网格得真的把七种类型都走到位，否则"等价"只是两边同样窄。
    assert seen == set(att.ALERT_TYPES), f"网格没覆盖到的类型：{set(att.ALERT_TYPES) - seen}"


def test_blocked_precedes_the_expiry_heuristics() -> None:
    """次序即语义：闸口受阻的观察不能因为顺带过期就被派生成 certification_expired。"""
    blocked_and_expired = _observation("critical", "blocked", "", True, -30, True, -30)
    assert att.derive_alert_type(blocked_and_expired) == "quality_gate_blocked"
    order = att.rule_order()
    assert order.index("gate_blocked") < order.index("certification_clock")
    assert order.index("gate_blocked") < order.index("waiver_clock")


def test_adding_a_reason_needs_no_branch_and_touches_no_host_file() -> None:
    """扩展性判据：一行声明就接一种新的 gate_reason，宿主文件不许动一个字节。"""
    before = HOST.read_bytes()
    assert "index_lagging" not in att.REASON_ALERT_TYPES
    att.REASON_ALERT_TYPES["index_lagging"] = "quality_gate_blocked"
    try:
        observation = _observation("warning", "passing", "index_lagging", False, None, False, None)
        assert att.derive_alert_type(observation) == "quality_gate_blocked"
        assert att.reason_map()["index_lagging"] == "quality_gate_blocked"
    finally:
        del att.REASON_ALERT_TYPES["index_lagging"]
    assert HOST.read_bytes() == before, "新增一种 reason 却要求改宿主文件，等于没收回注册表"
    assert att.derive_alert_type(
        _observation("warning", "passing", "index_lagging", False, None, False, None)
    ) == _reference(_observation("warning", "passing", "index_lagging", False, None, False, None))


def test_a_rule_can_be_inserted_at_a_named_anchor_only() -> None:
    """插入必须指名位置；不许默默落到链尾去改变优先级。"""
    rule = att.AlertTypeRule("index_lagging", lambda facts: "quality_gate_blocked" if facts.gate_reason == "index_lagging" else None)
    before = HOST.read_bytes()
    with pytest.raises(ValueError, match="not a registered rule"):
        att.register_alert_type_rule(rule, after="no_such_rule")
    with pytest.raises(ValueError, match="already registered"):
        att.register_alert_type_rule(att.ALERT_TYPE_RULES[0], after="gate_blocked")
    with pytest.raises(ValueError, match="non-empty"):
        att.register_alert_type_rule(att.AlertTypeRule("  ", lambda facts: None), after="gate_blocked")
    att.register_alert_type_rule(rule, after="authority_unavailable")
    try:
        order = att.rule_order()
        assert order.index("index_lagging") == order.index("authority_unavailable") + 1
        assert order.index("index_lagging") < order.index("declared_reason")
        assert len(order) == 6
    finally:
        att.ALERT_TYPE_RULES.remove(rule)
    assert att.rule_order() == (
        "authority_unavailable",
        "declared_reason",
        "gate_blocked",
        "certification_clock",
        "waiver_clock",
    )
    assert HOST.read_bytes() == before


_CHECK_RE = re.compile(r"alert_type IN \(([^)]*)\)")


def _check_vocabulary(source: str) -> set[str]:
    match = _CHECK_RE.search(source)
    assert match, "没找到 alert_type 的 CHECK —— 存储侧词表换了位置，栅栏得跟着换，不能当通过"
    return {part.strip().strip("'\"") for part in match.group(1).split(",")}


@pytest.mark.parametrize("label", ("orm", "migration-0031"))
def test_the_derived_vocabulary_is_the_stored_vocabulary(label: str) -> None:
    source = (ORM if label == "orm" else MIGRATION).read_text(encoding="utf-8")
    assert _check_vocabulary(source) == set(att.ALERT_TYPES)


def test_every_rule_can_only_yield_a_declared_type() -> None:
    """认不出的产出当场炸，而不是把一行违反 CHECK 的写入留给 flush。"""
    rogue = att.AlertTypeRule("rogue", lambda facts: "certification_nearly_stale")
    att.register_alert_type_rule(rogue, after="authority_unavailable")
    try:
        with pytest.raises(LookupError, match="does not accept"):
            att.derive_alert_type(_observation("warning", "passing", "", False, None, False, None))
    finally:
        att.ALERT_TYPE_RULES.remove(rogue)


def test_the_host_module_no_longer_dispatches_on_literals() -> None:
    """回潮栅栏：派生只准走那张表。

    第一版按 `(reason|gate_state|severity) ==` 的形状匹配，被自己的变异测试打穿了 —— AM5 往
    链里塞 `if str(observation.gate_state) == "blocked"` 照样通过，因为 `str(...)` 的右括号
    插在了变量和运算符之间。所以改按**字面量**查：重新引入一条分支就必然要写一个告警类型或
    reason 的字符串，那个藏不掉。同时正向要求宿主函数确实引用 `derive_alert_type`，防止有人
    靠删掉整个函数"通过"。
    """
    source = HOST.read_text(encoding="utf-8")
    body = source.split("def _derive_alert_type", 1)
    assert len(body) == 2, "_derive_alert_type 不在了 —— 查清楚是被删还是被改名"
    window = body[1].split("\n\n\n", 1)[0]
    literals = re.findall(r"[\"'][^\"'\n]+[\"']", window)
    assert not literals, f"链上又长出字面量：{literals}"
    assert "derive_alert_type(observation)" in window
    assert "quality_alert_types" in source
