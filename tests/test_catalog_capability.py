"""`capability_state` 的总函数式判定：今天可达的三种状态行为逐字不变，
任何没声明过的第四种状态一律拒绝。

改动前 `core/enterprise_access_control.py` 只在 `== "unavailable"` 时 raise，
即"除了 unavailable 都可以去跑授权评估"——那是授权路径上的 fail-open。
`inspect_workspace_authorization_capability` 的真实产出集合是
{ready, not_available, unavailable}（见 core/catalog_schema.py:5201/5202/5189 等），
其中 not_available 是合法状态（capability 迁移尚未装上），必须继续放行。
"""

from __future__ import annotations

import pytest

from core.catalog_capability import (
    CAPABILITY_NOT_AVAILABLE,
    CAPABILITY_READY,
    CAPABILITY_STATES,
    CAPABILITY_UNAVAILABLE,
    require_safe_capability_state,
)


class Refused(Exception):
    pass


def _check(state: object, issues: tuple[str, ...] = ()) -> str:
    return require_safe_capability_state(
        state, issues, allow_not_available=True, error=Refused
    )


def test_declared_states_are_exactly_the_produced_ones() -> None:
    assert CAPABILITY_STATES == frozenset({"ready", "not_available", "unavailable"})
    assert (CAPABILITY_READY, CAPABILITY_NOT_AVAILABLE, CAPABILITY_UNAVAILABLE) == (
        "ready",
        "not_available",
        "unavailable",
    )


@pytest.mark.parametrize(
    ("state", "expected"),
    [(CAPABILITY_READY, CAPABILITY_READY), (CAPABILITY_NOT_AVAILABLE, CAPABILITY_NOT_AVAILABLE)],
)
def test_states_that_used_to_pass_still_pass(state: str, expected: str) -> None:
    assert _check(state) == expected


def test_unavailable_still_refuses_with_the_same_message_as_before() -> None:
    with pytest.raises(Refused) as got:
        _check(CAPABILITY_UNAVAILABLE, ("trigger missing", "column drift"))
    assert str(got.value) == (
        "Workspace 授权 capability 不可安全使用: trigger missing; column drift"
    )

    with pytest.raises(Refused) as bare:
        _check(CAPABILITY_UNAVAILABLE, ())
    assert str(bare.value).endswith("schema drift")


@pytest.mark.parametrize(
    "hostile",
    ["", None, "degraded", "pending", "READY", "unknown", "not-ready"],
)
def test_any_undeclared_state_refuses_instead_of_reaching_authorization(
    hostile: object,
) -> None:
    """第四种状态不能再"因为不等于 unavailable 所以往下走"。"""
    with pytest.raises(Refused):
        _check(hostile)


def test_case_matters_being_strict_is_intentional() -> None:
    # "READY" 不是 ready：宁可拒绝也不要在授权判定上做大小写归一，
    # 因为生产者写错大小写本身就是需要被看见的缺陷。
    with pytest.raises(Refused):
        _check("READY")


def test_undeclared_state_is_named_as_such_not_blended_into_schema_drift() -> None:
    """未知状态与 unavailable 走不同的报错——这一条才是那个分支的全部理由。

    两条路径都 raise，所以"会不会拒绝"抓不住它；只有报错文本能。
    """
    with pytest.raises(Refused) as got:
        _check("degraded")
    assert "未知的 Workspace 授权 capability 状态" in str(got.value)
    assert "degraded" in str(got.value)


def test_the_authorization_call_site_no_longer_decides_by_hand() -> None:
    """宿主守卫：改前那一行是 `if capability_state == "unavailable": raise`，

    也就是"除了 unavailable 之外一律放行"——第四种状态会直接落进
    evaluate_workspace_authorization。现在该点只调用总判定，不再自己比字面量。
    """
    import inspect
    from pathlib import Path

    from core import enterprise_access_control

    source = Path(inspect.getsourcefile(enterprise_access_control) or "").read_text(
        encoding="utf-8"
    )
    assert 'capability_state == "unavailable"' not in source
    assert "require_safe_capability_state(" in source
    assert "allow_not_available=True" in source
