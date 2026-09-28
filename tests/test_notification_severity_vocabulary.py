"""Axis: the notification severity vocabulary is written in five places; fence them together.

§D-8/§D-9/§D-10 of the extensibility inventory all said "one vocabulary, several copies". This
is the same shape, and it is also the place where the census's own claim was **wrong**: §D-7
asserted ``info`` 是死的 because no *producer* emits it. It is not dead —
``_SEVERITY_RANK`` puts ``info`` at 0, so ``minimum_severity='info'`` is the weakest,
"everything" subscription tier, and two ``CHECK`` constraints store it. Acting on §D-7 as
written would have deleted a working user-facing tier.

What is actually missing is reconciliation. These must all describe one ordered vocabulary:

1. ``_ALLOWED_SEVERITIES`` in ``core/enterprise_notification_center.py`` — accepted on write;
2. ``_SEVERITY_RANK`` in ``core/enterprise_notification_materializer.py`` — the ordering the
   subscription filter compares with;
3/4. ``ck_tenant_notifications_severity`` and ``ck_notification_subscriptions_severity``
   (``models/orm.py``, plus the migration that installs them);
5. ``_ALERT_SEVERITIES`` in ``core/enterprise_release_quality_alerts.py`` — a deliberate
   **subset**: the producer side may only mint warning/critical. That is a real subset, so it
   is asserted as a subset, not as equality.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from core import enterprise_notification_center as center
from core import enterprise_notification_materializer as materializer
from core import enterprise_release_quality_alerts as alerts

REPO = Path(__file__).resolve().parents[1]
ORM = REPO / "models" / "orm.py"
MIGRATION = REPO / "catalog_migrations" / "versions" / "0032_enterprise_notification_center.py"

# 约束名一起匹配：`severity IN (...)` 这个形状在别的表上也有（质量观测的 severity
# 词表是 healthy/warning/critical/unavailable），只按值列表抓会抓到隔壁表。
_CHECK_RE = re.compile(
    r'"(?:minimum_)?severity IN \(([^)]*)\)",\s*name="(ck_[a-z_]+)"'
)
_WANTED = ("ck_tenant_notifications_severity", "ck_notification_subscriptions_severity")


def _check_states(text: str) -> list[frozenset[str]]:
    found = [
        frozenset(part.strip().strip("'") for part in group.split(","))
        for group, name in _CHECK_RE.findall(text)
        if name in _WANTED
    ]
    assert len(found) == len(_WANTED), (
        f"通知侧两个 severity 约束应当都能找到，实际 {len(found)}/{len(_WANTED)} —— "
        "找不到就说明约束被改名或挪走，这条守卫已经对着空气"
    )
    return found


def test_the_accepted_set_is_the_ranked_set() -> None:
    """接受写入的集合与参与排序比较的集合必须是同一个。

    `materializer.py:264` 直接 `_SEVERITY_RANK[severity]` 取值，而 :262 只守了
    `minimum_severity` 那一侧 —— 所以「能存进 notifications.severity 的值」与
    「能进 rank 表的值」一旦脱钩，表现是装配通知时 KeyError，而不是一个可操作的错误。
    """
    assert center._ALLOWED_SEVERITIES == frozenset(materializer._SEVERITY_RANK)  # noqa: SLF001


@pytest.mark.parametrize("path", [ORM, MIGRATION], ids=["orm", "migration-0032"])
def test_both_stored_severity_check_lists_match_the_declaration(path: Path) -> None:
    """两个 severity 约束（通知本体的、订阅的 minimum_severity）都得等于声明侧集合。"""
    for states in _check_states(path.read_text(encoding="utf-8")):
        assert states == center._ALLOWED_SEVERITIES  # noqa: SLF001


def test_the_producer_side_is_a_deliberate_subset_not_an_accidental_one() -> None:
    """质量告警只会铸 warning/critical，这是有意的窄集；但它必须是声明集的子集。

    反过来若哪天告警侧想铸 `info`，这条会红，逼着人同时确认排序与两条 CHECK。
    """
    assert alerts._ALERT_SEVERITIES < center._ALLOWED_SEVERITIES  # noqa: SLF001


def test_info_is_a_live_tier_rather_than_a_dead_value() -> None:
    """§D-7 的更正钉在这里：`info` 没有生产者，但它是**订阅侧最弱一档**（rank 0）。

    删掉它不是清理死代码，而是取消一个用户能选、库里能存、比较时会用到的档位。
    这条断言存在的意义就是说清这件事，别让下一个人在 §D-7 的旧措辞上动手。
    """
    rank = materializer._SEVERITY_RANK  # noqa: SLF001
    assert rank["info"] == min(rank.values())
    assert rank["info"] < rank["warning"] < rank["critical"]
    assert "info" in center._ALLOWED_SEVERITIES  # noqa: SLF001


def test_an_undeclared_severity_at_the_comparison_site_is_fail_closed() -> None:
    """§9 第 32 条裁定：订阅过滤的比较点不再裸取 `_SEVERITY_RANK[severity]`。

    `minimum_severity` 那一侧一直有守；这一侧原来裸取，词表一旦与存储脱钩，
    表现是 KeyError——完整性故障被压成一个看不懂的栈。现在显式抛
    `NotificationMaterializationUnavailable`；压成 `False` 返回等于静默丢
    投递，那是更坏的出口，这条同时防住两种回潮。
    """
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from core.enterprise_notification_materializer import (
        NotificationMaterializationUnavailable,
        _subscription_allows,  # noqa: SLF001
    )

    row = SimpleNamespace(
        status="active",
        minimum_severity="warning",
        preference="subscribed",
        muted_until=None,
    )
    now = datetime.now(timezone.utc)

    assert _subscription_allows(row, "critical", False, now) is True
    assert _subscription_allows(row, "info", False, now) is False
    try:
        _subscription_allows(row, "fatal", False, now)
    except NotificationMaterializationUnavailable:
        pass
    else:  # pragma: no cover
        raise AssertionError("未声明 severity 必须显式报完整性故障，而不是静默放行或丢投递")
