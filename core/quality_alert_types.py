"""`alert_type` 的派生：观察事实 → 告警类型，一条链一张有序规则表。

原来这里是 `enterprise_release_quality_alerts.py` 里的一串顺序 `if`（7 条字面量判断 + 2 条
位置敏感的到期启发式 + 1 个兜底）。它的问题不是长，而是**顺序就是语义**：`gate_state ==
blocked` 必须排在到期启发式之前，否则一条"认证已过期且闸口受阻"的观察会派生成
`certification_expired` 而不是 `quality_gate_blocked`。这个次序以前只存在于代码行序里，没有
任何地方声明它，也没有任何测试钉住它。

规则因此是一张显式有序的表，每条带名字；插入必须指名锚点，不能默默落到末尾。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

# 唯一一份 alert_type 词表。消费方（筛选入参、角色校验）从这里取，不再各抄一遍。
# 这七条同时也是存储侧 CHECK 的白名单，见 tests/test_quality_alert_type_derivation.py。
ALERT_TYPES = frozenset(
    {
        "certification_expiring",
        "certification_expired",
        "certification_stale",
        "waiver_expiring",
        "waiver_expired",
        "quality_gate_blocked",
        "quality_authority_unavailable",
    }
)

UNAVAILABLE_ALERT_TYPE = "quality_authority_unavailable"
FALLBACK_ALERT_TYPE = UNAVAILABLE_ALERT_TYPE

# 一条 gate_reason 派生成哪个告警类型。加一种原因 = 在这里加一行，不改任何分支。
# 三个原因共用一个类型是有意的：stale / 过期证据 / 撤销在运营眼里是同一件事。
REASON_ALERT_TYPES: dict[str, str] = {
    "certification_stale": "certification_stale",
    "stale_evidence": "certification_stale",
    "certification_revoked": "certification_stale",
    "certification_expired": "certification_expired",
    "waiver_expired": "waiver_expired",
    "waiver_expiring": "waiver_expiring",
    "certification_expiring": "certification_expiring",
}


@dataclass(frozen=True)
class ObservationFacts:
    """从 ORM 观察行抽出的纯值视图：规则只认这些，不碰 session。"""

    severity: str
    gate_state: str
    gate_reason: str
    has_certification: bool
    minutes_to_certification_expiry: int | None
    has_waiver: bool
    minutes_to_waiver_expiry: int | None


def _minutes(value: object) -> int | None:
    return None if value is None else int(value)


def facts_from_observation(observation: object) -> ObservationFacts:
    return ObservationFacts(
        severity=str(observation.severity),
        gate_state=str(observation.gate_state),
        gate_reason=str(observation.gate_reason or "").strip().casefold(),
        has_certification=observation.certification_id is not None,
        minutes_to_certification_expiry=_minutes(observation.minutes_to_certification_expiry),
        has_waiver=observation.waiver_id is not None,
        minutes_to_waiver_expiry=_minutes(observation.minutes_to_waiver_expiry),
    )


def _authority_unavailable(facts: ObservationFacts) -> str | None:
    if facts.severity == "unavailable" or facts.gate_state == "unavailable":
        return UNAVAILABLE_ALERT_TYPE
    return None


def _declared_reason(facts: ObservationFacts) -> str | None:
    return REASON_ALERT_TYPES.get(facts.gate_reason)


def _gate_blocked(facts: ObservationFacts) -> str | None:
    return "quality_gate_blocked" if facts.gate_state == "blocked" else None


def _certification_clock(facts: ObservationFacts) -> str | None:
    if facts.has_certification and facts.minutes_to_certification_expiry is not None:
        return "certification_expired" if facts.minutes_to_certification_expiry <= 0 else "certification_expiring"
    return None


def _waiver_clock(facts: ObservationFacts) -> str | None:
    if facts.has_waiver and facts.minutes_to_waiver_expiry is not None:
        return "waiver_expired" if facts.minutes_to_waiver_expiry <= 0 else "waiver_expiring"
    return None


@dataclass(frozen=True)
class AlertTypeRule:
    name: str
    resolve: Callable[[ObservationFacts], str | None]


ALERT_TYPE_RULES: list[AlertTypeRule] = [
    AlertTypeRule("authority_unavailable", _authority_unavailable),
    AlertTypeRule("declared_reason", _declared_reason),
    AlertTypeRule("gate_blocked", _gate_blocked),
    AlertTypeRule("certification_clock", _certification_clock),
    AlertTypeRule("waiver_clock", _waiver_clock),
]


def register_alert_type_rule(rule: AlertTypeRule, *, after: str) -> None:
    """把一条规则插在具名锚点**之后**。

    位置必须显式：落到末尾意味着"前面全不命中才生效"，那几乎不是新增者想要的语义，
    而静默的次序错误正是这张表要消灭的东西。
    """
    if not isinstance(rule, AlertTypeRule):
        raise ValueError("rule must be an AlertTypeRule")
    if not rule.name or not rule.name.strip():
        raise ValueError("rule.name must be a non-empty name")
    if any(existing.name == rule.name for existing in ALERT_TYPE_RULES):
        raise ValueError(f"{rule.name}: an alert type rule with this name is already registered")
    if not callable(rule.resolve):
        raise ValueError(f"{rule.name}: resolve must be callable")
    positions = [existing.name for existing in ALERT_TYPE_RULES]
    if after not in positions:
        raise ValueError(
            f"{rule.name}: after={after!r} is not a registered rule; known order is {positions}"
        )
    ALERT_TYPE_RULES.insert(positions.index(after) + 1, rule)


def _require_declared(rule_name: str, alert_type: str) -> None:
    if alert_type not in ALERT_TYPES:
        raise LookupError(
            f"{rule_name}: produced {alert_type!r}, which the stored alert_type CHECK does "
            "not accept — widen the constraint before shipping the rule"
        )


def derive_alert_type(observation: object) -> str:
    facts = facts_from_observation(observation)
    for rule in ALERT_TYPE_RULES:
        outcome = rule.resolve(facts)
        if outcome is None:
            continue
        _require_declared(rule.name, outcome)
        return outcome
    _require_declared("<fallback>", FALLBACK_ALERT_TYPE)
    return FALLBACK_ALERT_TYPE


def rule_order() -> tuple[str, ...]:
    return tuple(rule.name for rule in ALERT_TYPE_RULES)


def reason_map() -> Mapping[str, str]:
    return dict(REASON_ALERT_TYPES)
