"""Axis #7 guards: a quality-alert operation is one declaration, and the active-slot
fence cannot be declared away.

``_mutate_alert`` used to carry each operation's whole behaviour in an ``elif`` chain —
starting states, stamped columns, released columns, audit action and message. Two of those
were invariants wearing the clothes of implementation detail: a terminal operation must
release ``active_alert_key`` (the partial-unique "one live alert per fingerprint" slot), and
a non-terminal one must not. Both are now refused at registration.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

import core.enterprise_release_quality_alerts as alerts
from core.quality_alert_operations import (
    ACTIVE_ALERT_STATUSES,
    BUILTIN_QUALITY_ALERT_OPERATIONS,
    QualityAlertOperationSpec,
    configure_alert_model,
    declared_columns,
    quality_alert_operation_names,
    register_quality_alert_operation,
    resolve_quality_alert_operation,
    unregister_quality_alert_operation,
)
from models.orm import DatasetReleaseQualityAlert
from tests.test_enterprise_release_quality_alerts import (
    _create_alert,
    _seed_quality_authority,
)
from test_enterprise_knowledge_base_release_snapshot import _release_engine


@pytest.fixture()
def quality_engine(tmp_path: Path):
    engine, _ = _release_engine(tmp_path)
    _seed_quality_authority(engine)
    try:
        yield engine
    finally:
        engine.dispose()

HOST = Path(inspect.getsourcefile(alerts) or "")


def _defer_spec(**overrides: Any) -> QualityAlertOperationSpec:
    """A plausible new operation: park an open alert under suppression with a bound."""
    fields: dict[str, Any] = {
        "operation": "defer_quality_alert",
        "target_status": "suppressed",
        "allowed_from": frozenset({"open"}),
        "audit_action": "knowledge_base.release_quality.alert_deferred",
        "response_message": "Quality alert deferred",
        "conflict_message": "only open quality alerts can be deferred",
        "stamp_actor": "suppressed_by",
        "stamp_comment": "suppressed_comment",
        "sets_bound": "suppressed_until",
    }
    fields.update(overrides)
    return QualityAlertOperationSpec(**fields)


# --------------------------------------------------------------------------- #
# 判据 §3.1：注册一个操作，宿主逐字节不变，而且真把这条操作执行出来
# --------------------------------------------------------------------------- #


def test_a_new_operation_is_applied_live_without_editing_the_ceremony(quality_engine) -> None:
    before = HOST.read_bytes()
    alert_id = _create_alert(quality_engine)
    register_quality_alert_operation(_defer_spec())
    try:
        result = alerts._mutate_alert(  # noqa: SLF001
            quality_engine,
            operation="defer_quality_alert",
            tenant_id="tenant-a",
            actor_id="owner-a",
            dataset_id="dataset-a",
            alert_id=alert_id,
            expected_revision=1,
            comment="先挂起",
            suppressed_until=datetime.now(timezone.utc) + timedelta(days=2),
            idempotency_key="axis7-defer-probe",
            request_id="req-axis7-defer",
            request_ip="10.0.0.1",
            now=None,
        )
    finally:
        unregister_quality_alert_operation("defer_quality_alert")

    # 声明里的回执文案与目标状态，直接从仪式的返回体里读出来。
    assert result.status == 200
    assert result.body["alert"]["status"] == "suppressed"
    assert result.body["alert"]["revision"] == 2
    assert HOST.read_bytes() == before, "仪式里又长回一份操作梯"
    assert resolve_quality_alert_operation("defer_quality_alert") is None
    with Session(quality_engine) as session:
        alert = session.get(DatasetReleaseQualityAlert, alert_id)
        assert alert is not None
        assert alert.status == "suppressed"
        assert alert.suppressed_by == "owner-a"
        assert alert.suppressed_comment == "先挂起"
        assert alert.suppressed_until is not None
        # 非终态操作不许让出活跃槽位，否则同一指纹可以再开一条活告警。
        assert alert.active_alert_key
    assert alert.revision == 2, "revision 仍由仪式推进"


def test_an_unknown_operation_is_still_refused_after_the_refactor(quality_engine) -> None:
    alert_id = _create_alert(quality_engine)
    with pytest.raises(alerts.QualityAlertInvalid):
        alerts._mutate_alert(  # noqa: SLF001
            quality_engine,
            operation="escalate_quality_alert",
            tenant_id="tenant-a",
            actor_id="member-a",
            dataset_id="dataset-a",
            alert_id=alert_id,
            expected_revision=1,
            comment="",
            idempotency_key="axis7-unknown",
            request_id="req-axis7-unknown",
            request_ip="10.0.0.1",
            now=None,
        )
    with Session(quality_engine) as session:
        alert = session.get(DatasetReleaseQualityAlert, alert_id)
        assert alert is not None and alert.status == "open" and alert.revision == 1


# --------------------------------------------------------------------------- #
# 栅栏：活跃槽位的释放不是可选细节
# --------------------------------------------------------------------------- #


def test_a_terminal_operation_must_release_the_active_slot() -> None:
    with pytest.raises(ValueError, match="active_alert_key"):
        register_quality_alert_operation(
            _defer_spec(operation="bad_terminal", target_status="resolved", clears=frozenset())
        )
    assert resolve_quality_alert_operation("bad_terminal") is None


def test_a_non_terminal_operation_may_not_release_the_active_slot() -> None:
    with pytest.raises(ValueError, match="only a terminal target"):
        register_quality_alert_operation(
            _defer_spec(
                operation="bad_release", clears=frozenset({"active_alert_key"})
            )
        )
    assert resolve_quality_alert_operation("bad_release") is None


def test_a_declaration_may_only_name_columns_the_model_actually_has() -> None:
    """SQLAlchemy 上 setattr 一个不存在的列不会报错，只会在 flush 时丢掉 ——
    那会造出一次"成功了但其实什么都没改"的操作。"""
    with pytest.raises(ValueError, match="does not have"):
        register_quality_alert_operation(
            _defer_spec(operation="bad_column", stamp_actor="suppressed_by_whom")
        )
    assert resolve_quality_alert_operation("bad_column") is None
    # 已声明的内建操作也全部通过这道核（configure_alert_model 会重查一遍）
    configure_alert_model(lambda column: hasattr(DatasetReleaseQualityAlert, column))


def test_an_operation_may_not_stamp_itself_into_the_same_state() -> None:
    with pytest.raises(ValueError, match="allowed starting status"):
        register_quality_alert_operation(
            _defer_spec(operation="noop", allowed_from=frozenset({"open", "suppressed"}))
        )


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"operation": "  "}, "空操作名"),
        ({"operation": "Defer_Quality_Alert"}, "非规范拼写当键"),
        ({"target_status": "archived"}, "未知目标状态"),
        ({"allowed_from": frozenset()}, "空允许起点"),
        ({"allowed_from": frozenset({"ghostly"})}, "起点含未知状态"),
        ({"audit_action": "  "}, "空审计动作"),
        ({"response_message": ""}, "空回执文案"),
        ({"conflict_message": ""}, "空冲突文案"),
        ({"stamp_actor": ""}, "空署名列"),
        ({"stamp_comment": None}, "空批注列"),
    ],
)
def test_a_bad_declaration_dies_at_registration(overrides: dict[str, Any], reason: str) -> None:
    before = set(quality_alert_operation_names())
    with pytest.raises((ValueError, TypeError)):
        register_quality_alert_operation(_defer_spec(**overrides))
    assert set(quality_alert_operation_names()) == before, reason


def test_only_a_real_declaration_can_be_registered() -> None:
    before = set(quality_alert_operation_names())
    for junk in ({"operation": "junk"}, "junk", None):
        with pytest.raises(TypeError):
            register_quality_alert_operation(junk)  # type: ignore[arg-type]
    assert set(quality_alert_operation_names()) == before


# --------------------------------------------------------------------------- #
# 内建声明的字面值，与"宿主不再自己抄一份"
# --------------------------------------------------------------------------- #


def test_the_three_builtin_operations_keep_their_exact_contracts() -> None:
    by_operation = {spec.operation: spec for spec in BUILTIN_QUALITY_ALERT_OPERATIONS}
    assert set(by_operation) == {
        "acknowledge_quality_alert",
        "suppress_quality_alert",
        "resolve_quality_alert",
    }
    ack = by_operation["acknowledge_quality_alert"]
    assert (ack.target_status, ack.allowed_from, ack.stamp_time) == (
        "acknowledged",
        frozenset({"open"}),
        "acknowledged_at",
    )
    assert ack.audit_action == "knowledge_base.release_quality.alert_acknowledged"
    assert ack.clears == frozenset()

    suppress = by_operation["suppress_quality_alert"]
    assert suppress.allowed_from == frozenset({"open", "acknowledged"})
    assert suppress.stamp_time is None, "抑制没有 suppressed_at，只有 suppressed_until"
    assert suppress.sets_bound == "suppressed_until"

    resolve = by_operation["resolve_quality_alert"]
    assert resolve.allowed_from == ACTIVE_ALERT_STATUSES
    assert resolve.is_terminal is True
    assert resolve.terminal_conflict_message == "resolved quality alerts are terminal"
    assert resolve.clears == frozenset(
        {"active_alert_key", "suppressed_until", "suppressed_by", "suppressed_comment"}
    )
    assert "status" not in declared_columns(resolve)
    assert "revision" not in declared_columns(resolve)


def test_the_host_holds_no_copy_of_the_operation_ladder_or_the_status_set() -> None:
    source = inspect.getsource(alerts._mutate_alert)  # noqa: SLF001
    assert 'operation == "acknowledge_quality_alert"' not in source
    assert 'operation == "resolve_quality_alert"' not in source
    assert "resolve_quality_alert_operation" in source
    assert "apply_operation" in source
    module_source = HOST.read_text("utf-8")
    assert '_ACTIVE_ALERT_STATUSES = frozenset({"open"' not in module_source, "状态分区又被抄了一份"
    assert alert_source_has_one_revision_bump()


def alert_source_has_one_revision_bump() -> bool:
    """revision 只在一处推进 —— 声明表碰不到它。"""
    source = inspect.getsource(alerts._mutate_alert)  # noqa: SLF001
    assert source.count("alert.revision = int(alert.revision) + 1") == 1
    for column in ("status", "revision", "updated_at"):
        assert f'alert.{column} = None' not in source
    return True
