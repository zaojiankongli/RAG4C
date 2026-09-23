"""特征化网：`ApprovalExecutionFact.__post_init__` 的每条校验规则各有一个用例（扩展轴 #5 的前置）。

按**规则**生成，不按字段。前两次尝试都按字段做（置 None / 换一个值），结果对报错文案变异全绿 ——
置 None 会让整组别名一起消失，别名一致性那类规则根本不触发。这次先把 :213-358 里 24 条
`raise ValueError` 抄成清单，再给每条造一个最小触发用例，并断言"清单里每条都被某个用例打到，
或被明确标为到不了"。报错文案观测自当前实现，所以改文案、改条件、改先后顺序都会红。

数据在 `tests/approval_execution_fact_golden.py`，重新生成的理由记在清单 §AJ。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.enterprise_approval_control import ApprovalExecutionFact
from tests.approval_execution_fact_golden import CASES, UNREACHABLE

D = "a" * 64
EXP = "2030-01-01T00:00:00.000000Z"

BASE = {
    "tenant_id": "t1", "approval_request_id": "ar1", "execution_id": "ex1",
    "request_revision": 1, "execution_revision": 2, "resource_type": "release",
    "resource_id": "rel1", "snapshot_hash": D, "reason": "r", "workspace_revision": 3,
    "policy_revision": 4, "from_mode": "off", "target_mode": "shadow",
    "permission_model_version": 5, "permission_matrix_fingerprint": D, "profile_revision": 4,
    "dataset_profile_revision": 4, "expected_dataset_profile_revision": 4,
    "ownership_revision": 3, "expected_ownership_revision": 3, "source_workspace_id": "w1",
    "target_workspace_id": "w2", "source_workspace_revision": 3, "target_workspace_revision": 3,
    "expected_source_workspace_revision": 3, "expected_target_workspace_revision": 3,
    "release_id": "rel1", "release_number": 11, "manifest_digest": D, "mutation_generation": 3,
    "channel_id": "ch1", "channel_revision": 13, "serving_generation": 3, "workspace_id": "w1",
    "policy_id": "p1", "policy_digest": D, "quality_gate_revision": 13, "quality_gate_digest": D,
    "quality_evidence_digest": D, "evidence_digest": D, "waiver_expires_at": EXP,
    "requested_expires_at": EXP, "quality_gate_state": "blocked",
    "quality_gate_reason": "gate blocked",
}

VALIDATOR = Path("core/enterprise_approval_control.py")
RULE_RANGE = (213, 358)


def _message(action_type: str, over: dict) -> str | None:
    try:
        ApprovalExecutionFact(**{**BASE, "action_type": action_type, **over})
    except ValueError as error:
        return str(error)
    return None


def _rule_literals() -> set[str]:
    lines = VALIDATOR.read_text(encoding="utf-8").splitlines()
    return {
        found
        for line in lines[RULE_RANGE[0] - 1 : RULE_RANGE[1]]
        for found in re.findall(r'raise ValueError\(f?"([^"]+)"', line)
    }


@pytest.mark.parametrize("case", CASES, ids=[c["case"] for c in CASES])
def test_observed_verdict_is_unchanged(case: dict) -> None:
    assert _message(case["action"], case["over"]) == case["msg"]


def test_every_validator_rule_is_exercised_or_declared_unreachable() -> None:
    """这张网的自查：清单里不许有没用例的规则。"""
    observed = [_message(c["action"], c["over"]) for c in CASES]
    missing = []
    for literal in _rule_literals():
        if literal in UNREACHABLE:
            continue
        pattern = re.escape(literal).replace(re.escape("{field}"), r"[a-z_]+")
        if not any(re.fullmatch(pattern, got or "") for got in observed):
            missing.append(literal)
    assert not missing, f"这些规则没有用例，网有洞：{missing}"


def test_net_has_teeth_on_the_cross_field_rules() -> None:
    """不许又做成"只看缺字段"的网：别名 / canonical / 大小写三类必须真打到。"""
    messages = [c["msg"] for c in CASES if c["msg"]]
    assert any("aliases must agree" in m for m in messages)
    assert any("canonical" in m for m in messages)
    assert any("lowercase" in m for m in messages)
    assert len(_rule_literals()) >= 20, "规则清单明显变少 —— 校验器被删过，这张网要重生成"


def test_the_declared_unreachable_rule_is_still_shadowed() -> None:
    """`waiver_expires_at is required` 到不了：两个都为 None 时先抛别名一致那条。

    这不是漏测，是死分支。哪天有人把它改到可达，这条会红 —— 那时该重新判定它是保留还是删掉。
    """
    assert (
        _message(
            "knowledge_base_release_quality_waiver",
            {"waiver_expires_at": None, "requested_expires_at": None},
        )
        == "waiver expiry aliases must agree"
    )
    assert list(UNREACHABLE) == ["waiver_expires_at is required for Quality Waiver fact"]


# ---------------------------------------------------------------------------
# 扩展性判据：新增一种审批事实，不许改任何既有分支，也不许改宿主文件。
# ---------------------------------------------------------------------------

HOST_FILE = Path("core/enterprise_approval_control.py")


def test_a_new_fact_type_needs_no_edit_to_existing_rules_or_the_host() -> None:
    from core.approval_fact_rules import (
        FACT_RULES,
        Check,
        FactRule,
        RequireInt,
        RequireStr,
        register_fact_rule,
        rule_names,
        validate_execution_fact,
    )

    before = HOST_FILE.read_bytes()
    names_before = rule_names()
    rule = FactRule(
        name="index_shrink_approval",
        action_types=frozenset({"index_shrink"}),
        steps=(
            RequireInt(("mutation_generation",), "{field} is required for Index Shrink fact"),
            RequireStr(("channel_id",), "{field} is required for Index Shrink fact"),
            Check("shrink-reason", lambda fact: None),
        ),
    )
    register_fact_rule(rule)
    try:
        assert rule_names() == (*names_before, "index_shrink_approval")
        assert len(FACT_RULES) == len(names_before) + 1
        # 新类型的要求当场生效……
        with pytest.raises(ValueError, match="is required for Index Shrink fact"):
            validate_execution_fact(
                ApprovalExecutionFact(**{**BASE, "action_type": "index_shrink", "mutation_generation": None})
            )
        # ……而既有类型完全不受影响（common 之后没有别的规则命中 index_shrink）。
        validate_execution_fact(ApprovalExecutionFact(**{**BASE, "action_type": "index_shrink"}))
    finally:
        FACT_RULES.remove(rule)
    assert HOST_FILE.read_bytes() == before, "注册一条规则却要求改宿主文件，等于没收回注册表"
    assert rule_names() == names_before


def test_the_rule_table_refuses_duplicate_names_and_unknown_anchors() -> None:
    from core.approval_fact_rules import FACT_RULES, FactRule, RequireInt, register_fact_rule

    probe = FactRule("probe", None, (RequireInt(("policy_revision"), "x"),))
    with pytest.raises(ValueError, match="not a registered rule"):
        register_fact_rule(probe, after="no_such_rule")
    with pytest.raises(ValueError, match="at least one step"):
        register_fact_rule(FactRule("empty", None, ()), after="common")
    with pytest.raises(ValueError, match="non-empty"):
        register_fact_rule(FactRule("  ", None, (RequireInt(("policy_revision"), "x"),)), after="common")
    register_fact_rule(probe, after="common")
    try:
        duplicate = FactRule("probe", None, (RequireInt(("policy_revision"), "x"),))
        with pytest.raises(ValueError, match="already registered"):
            register_fact_rule(duplicate, after="common")
    finally:
        FACT_RULES.remove(probe)


def test_step_order_is_declared_data_not_python_control_flow() -> None:
    """这张表要能被结构化查：步骤次序必须是数据，否则 §AI 的"一重组就翻"又回来了。"""
    from core.approval_fact_rules import FACT_RULES, rule_names

    dswt = next(rule for rule in FACT_RULES if rule.name == "dataset_workspace_transfer")
    waiver = next(rule for rule in FACT_RULES if rule.name == "knowledge_base_release_quality_waiver")
    kinds = [type(step).__name__ for step in dswt.steps]
    assert kinds == ["RequireInt", "Check", "Check", "RequireStr", "Check", "Check"], kinds
    assert len(waiver.steps) == 9
    registered = [name for name in rule_names() if name != "common"]
    assert "dataset_workspace_transfer" in registered
