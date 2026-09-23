"""审批执行事实的字段要求：一张**有序**规则表（扩展轴 #5）。

原来是 `ApprovalExecutionFact.__post_init__` 里 145 行的顺序 `if`：五条规则按 action_type 决定
命中与否，命中即**叠加**要求，规则内部再按"整数字段 → 字符串字段 → 交叉校验"的固定次序检查。

形状为什么是"有序步骤"而不是 dict-per-action-type：
- `knowledge_base_release_quality_waiver` 同时命中「KB Release 那一组」与「Waiver 那一组」，
  要求是两条规则的和（清单 §AI / §AJ 有量化证据：25 ⊃ 20）；
- 组内次序是行为的一部分。`dataset_workspace_transfer` 把 `source_workspace_id` 这类字符串要求
  排在两个别名检查**之后**，而 `source and target Workspace must differ` 又排在 workspace 别名
  检查**之前**。按"int→str→交叉"的整齐模板一重组，这两条的先后就翻了 —— 特征化网里
  `prec-*` 那几条正是为了钉住它。

所以新增一种审批事实 = 追加一条 `FactRule`（含自己的步骤序列），不改任何既有分支。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# 步骤类型：每种都只管一件事，次序由所在规则的步骤元组决定
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RequireInt:
    """按声明顺序逐个要求正整数字段。报错模板里的 `{field}` 由字段名填入。"""

    fields: tuple[str, ...]
    template: str
    minimums: Mapping[str, int] = field(default_factory=dict)

    def apply(self, fact: Any) -> None:
        for name in self.fields:
            value = getattr(fact, name)
            minimum = self.minimums.get(name, 1)
            if type(value) is not int or value < minimum:
                raise ValueError(self.template.format(field=name))


@dataclass(frozen=True)
class RequireStr:
    """按声明顺序逐个要求非空字符串字段。"""

    fields: tuple[str, ...]
    template: str

    def apply(self, fact: Any) -> None:
        for name in self.fields:
            value = getattr(fact, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(self.template.format(field=name))


@dataclass(frozen=True)
class Check:
    """一条交叉校验。`name` 只是给人看的标识，不参与判定。"""

    name: str
    run: Callable[[Any], None]

    def apply(self, fact: Any) -> None:
        self.run(fact)


@dataclass(frozen=True)
class FactRule:
    name: str
    action_types: frozenset[str] | None  # None = 对任何 action_type 都命中
    steps: tuple[RequireInt | RequireStr | Check, ...]


# ---------------------------------------------------------------------------
# 交叉校验的实现。次序语义都写在下面的 FACT_RULES 里，这里只管单条判定。
# ---------------------------------------------------------------------------


def _check_revision_follows(fact: Any) -> None:
    if fact.execution_revision != fact.request_revision + 1:
        raise ValueError("execution_revision must immediately follow request_revision")


def _check_snapshot_hash(fact: Any) -> None:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", fact.snapshot_hash):
        raise ValueError("snapshot_hash must be a SHA-256 hex digest")


def _check_dataset_profile_aliases(fact: Any) -> None:
    if (
        len(
            {
                fact.profile_revision,
                fact.dataset_profile_revision,
                fact.expected_dataset_profile_revision,
            }
        )
        != 1
    ):
        raise ValueError("Dataset profile revision aliases must agree")


def _check_ownership_aliases(fact: Any) -> None:
    if len({fact.ownership_revision, fact.expected_ownership_revision}) != 1:
        raise ValueError("Ownership revision aliases must agree")


def _check_workspaces_differ(fact: Any) -> None:
    if fact.source_workspace_id == fact.target_workspace_id:
        raise ValueError("source and target Workspace must differ")


def _check_workspace_revision_aliases(fact: Any) -> None:
    if (
        fact.expected_source_workspace_revision != fact.source_workspace_revision
        or fact.expected_target_workspace_revision != fact.target_workspace_revision
    ):
        raise ValueError("Workspace revision aliases must agree")


def _lowercase_digest_check(attribute: str, message: str) -> Check:
    def run(fact: Any) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", str(getattr(fact, attribute))):
            raise ValueError(message)

    return Check(f"{attribute}-lowercase", run)


def _check_gate_revision_matches_channel(fact: Any) -> None:
    if fact.quality_gate_revision != fact.channel_revision:
        raise ValueError("quality_gate_revision must match channel_revision")


def _check_evidence_digest_aliases(fact: Any) -> None:
    values = [
        value
        for value in (fact.quality_evidence_digest, fact.evidence_digest)
        if value is not None
    ]
    if len(set(values)) > 1:
        raise ValueError("quality evidence digest aliases must agree")
    if values and not re.fullmatch(r"[0-9a-f]{64}", str(values[0])):
        raise ValueError("quality_evidence_digest must be a lowercase SHA-256 digest")


def _check_waiver_expiry(fact: Any) -> None:
    # 时刻格式化/解析是宿主模块的既有工具。函数内 import 是为了避开模块级循环
    # （宿主导入这张表，这张表在调用时才回头取用宿主）。
    from core.enterprise_approval_control import _datetime, _iso

    values = [
        value
        for value in (fact.waiver_expires_at, fact.requested_expires_at)
        if value is not None
    ]
    if len(set(values)) != 1:
        raise ValueError("waiver expiry aliases must agree")
    if not values or not isinstance(values[0], str):
        # 到不了：上面那条别名检查在两者皆空时已经先抛（len(set()) != 1）。
        # 原样保留，特征化网里 UNREACHABLE 那条钉着它 —— 要改它得先过那条测试。
        raise ValueError("waiver_expires_at is required for Quality Waiver fact")
    parsed = _datetime(values[0])
    if parsed is None:
        raise ValueError("waiver_expires_at must be an ISO datetime")
    canonical = _iso(parsed)
    if canonical != values[0]:
        raise ValueError("waiver_expires_at must be canonical")


def _check_gate_state_blocked(fact: Any) -> None:
    if fact.quality_gate_state is not None and fact.quality_gate_state != "blocked":
        raise ValueError("quality_gate_state must be blocked for Quality Waiver fact")


def _check_gate_reason(fact: Any) -> None:
    if fact.quality_gate_reason is not None and not fact.quality_gate_reason.strip():
        raise ValueError("quality_gate_reason is invalid")


# ---------------------------------------------------------------------------
# 规则表。顺序即行为：命中判定按列表次序，规则内部按步骤次序。
# ---------------------------------------------------------------------------

_BASE_STR = "{field} is required for ApprovalExecutionFact"
_WS_STR = "{field} is required for Workspace authorization fact"
_DT_STR = "{field} is required for Dataset Workspace transfer fact"
_KB_STR = "{field} is required for Knowledge Base Release fact"
_WV_STR = "{field} is required for Quality Waiver fact"

FACT_RULES: list[FactRule] = [
    FactRule(
        name="common",
        action_types=None,
        steps=(
            RequireStr(
                (
                    "tenant_id",
                    "approval_request_id",
                    "execution_id",
                    "action_type",
                    "resource_type",
                    "resource_id",
                    "snapshot_hash",
                    "reason",
                ),
                _BASE_STR,
            ),
            RequireInt(("request_revision", "execution_revision"), "{field} must be a positive integer"),
            Check("revision-follows", _check_revision_follows),
            Check("snapshot-hash-hex", _check_snapshot_hash),
        ),
    ),
    FactRule(
        name="workspace_authorization_mode_change",
        action_types=frozenset({"workspace_authorization_mode_change"}),
        steps=(
            RequireInt(
                ("workspace_revision", "policy_revision", "permission_model_version"), _WS_STR
            ),
            RequireStr(("from_mode", "target_mode", "permission_matrix_fingerprint"), _WS_STR),
        ),
    ),
    FactRule(
        name="dataset_workspace_transfer",
        action_types=frozenset({"dataset_workspace_transfer"}),
        steps=(
            RequireInt(
                (
                    "profile_revision",
                    "dataset_profile_revision",
                    "expected_dataset_profile_revision",
                    "ownership_revision",
                    "expected_ownership_revision",
                    "source_workspace_revision",
                    "target_workspace_revision",
                    "expected_source_workspace_revision",
                    "expected_target_workspace_revision",
                ),
                _DT_STR,
            ),
            Check("dataset-profile-aliases", _check_dataset_profile_aliases),
            Check("ownership-aliases", _check_ownership_aliases),
            # 这两个字符串要求在别名检查**之后**，不是漏写 —— 见 prec-dswt-missing-id-and-alias。
            RequireStr(("source_workspace_id", "target_workspace_id"), _DT_STR),
            Check("workspaces-differ", _check_workspaces_differ),
            Check("workspace-revision-aliases", _check_workspace_revision_aliases),
        ),
    ),
    FactRule(
        name="knowledge_base_release",
        action_types=frozenset(
            {
                "knowledge_base_release_publish",
                "knowledge_base_release_rollback",
                "knowledge_base_release_quality_waiver",
            }
        ),
        steps=(
            RequireInt(
                (
                    "release_number",
                    "channel_revision",
                    "profile_revision",
                    "mutation_generation",
                    "ownership_revision",
                    "workspace_revision",
                    "serving_generation",
                ),
                _KB_STR,
                minimums={"serving_generation": 0},
            ),
            RequireStr(("release_id", "manifest_digest", "channel_id", "workspace_id"), _KB_STR),
            _lowercase_digest_check("manifest_digest", "manifest_digest must be a lowercase SHA-256 digest"),
        ),
    ),
    FactRule(
        name="knowledge_base_release_quality_waiver",
        action_types=frozenset({"knowledge_base_release_quality_waiver"}),
        steps=(
            RequireInt(("policy_revision", "quality_gate_revision"), _WV_STR),
            RequireStr(("policy_id", "policy_digest", "quality_gate_digest"), _WV_STR),
            _lowercase_digest_check("policy_digest", "policy_digest must be a lowercase SHA-256 digest"),
            _lowercase_digest_check(
                "quality_gate_digest", "quality_gate_digest must be a lowercase SHA-256 digest"
            ),
            Check("gate-revision-matches-channel", _check_gate_revision_matches_channel),
            Check("evidence-digest-aliases", _check_evidence_digest_aliases),
            Check("waiver-expiry", _check_waiver_expiry),
            Check("gate-state-blocked", _check_gate_state_blocked),
            Check("gate-reason", _check_gate_reason),
        ),
    ),
]


def register_fact_rule(rule: FactRule, *, after: str | None = None) -> None:
    """追加一条规则。`after` 指名插到某条之后；不给就追加到末尾。

    与 §AB 那张表不同，这里允许默认追加：一条新事实类型的规则不该抢在 `common` 之前，
    而末尾恰是"先过通用校验，再谈这一类型自己的要求"的正确位置。
    """
    if not isinstance(rule, FactRule):
        raise ValueError("rule must be a FactRule")
    if not rule.name or not rule.name.strip():
        raise ValueError("rule.name must be a non-empty name")
    if any(existing.name == rule.name for existing in FACT_RULES):
        raise ValueError(f"{rule.name}: a fact rule with this name is already registered")
    if not rule.steps:
        raise ValueError(f"{rule.name}: a fact rule needs at least one step")
    positions = [existing.name for existing in FACT_RULES]
    if after is None:
        FACT_RULES.append(rule)
        return
    if after not in positions:
        raise ValueError(f"{rule.name}: after={after!r} is not a registered rule; known {positions}")
    FACT_RULES.insert(positions.index(after) + 1, rule)


def rule_names() -> tuple[str, ...]:
    return tuple(rule.name for rule in FACT_RULES)


def validate_execution_fact(fact: Any) -> None:
    for rule in FACT_RULES:
        if rule.action_types is not None and fact.action_type not in rule.action_types:
            continue
        for step in rule.steps:
            step.apply(fact)
