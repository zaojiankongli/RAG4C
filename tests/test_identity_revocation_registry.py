"""Axis #11 guards: a revocation kind is one declaration, and the fence is not negotiable.

``_simple_state_mutation`` in ``core/enterprise_identity_control.py`` used to answer every
per-kind question inline with ``kind == "domain_revoke"`` ternaries (8 of them), which had
two consequences: adding a kind meant editing the shared ceremony, and **any value the
ternaries didn't recognise silently became a SCIM token revoke** — the wrong table, the
wrong audit action, a real mutation. The declaration in
``core/identity_revocations.py`` closes both: unknown kinds are refused, and the columns
that *are* the revoke (``status`` / ``revision`` / ``revoked_*``) cannot be claimed by a
kind at all.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

import core.enterprise_identity_control as control
from core.identity_revocations import (
    FENCE_COLUMNS,
    RevocationKindSpec,
    register_revocation_kind,
    resolve_revocation_kind,
    revocation_kind_names,
    unregister_revocation_kind,
)
from tests.test_enterprise_identity_federation_api import (
    TENANT_A,
    MemoryIdentityResolver,
    _client,
    _create_domain,
    _engine,
    _rows,
)

PROBE_TABLE = "tenant_verified_domains"


def _probe_spec(**overrides: Any) -> RevocationKindSpec:
    fields: dict[str, Any] = {
        "kind": "probe_revoke",
        "operation": "identity.probe.revoke",
        "resource_type": "tenant_probe",
        "table_name": PROBE_TABLE,
        "payload_key": "probe",
        "audit_action": "tenant_probe.revoked",
        "project": lambda row: {"normalized_domain": str(row["normalized_domain"])},
        "release_values": lambda actor_id: {"txt_value": f"released:{actor_id}"},
    }
    fields.update(overrides)
    return RevocationKindSpec(**fields)


# --------------------------------------------------------------------------- #
# 判据 §3.1：注册一种 kind，宿主逐字节不变，而且**实时**照它办事（真走一遍仪式）
# --------------------------------------------------------------------------- #


def test_a_new_kind_is_honoured_live_by_the_shared_ceremony() -> None:
    host = Path(inspect.getsourcefile(control) or "")
    before = host.read_bytes()
    engine = _engine()
    domain_id = str(_create_domain(_client(engine, MemoryIdentityResolver()), "probe.test")
                    .json()["domain"]["id"])
    revision = int(_rows(engine, PROBE_TABLE)[0]["revision"])

    register_revocation_kind(_probe_spec())
    try:
        payload = control._simple_state_mutation(  # noqa: SLF001
            engine,
            tenant_id=TENANT_A,
            actor_id="owner-a",
            actor_role="owner",
            resource_id=domain_id,
            expected_revision=revision,
            reason="探针撤销",
            idempotency_key="axis11-probe",
            request_id="req-axis11-probe",
            request_ip="10.0.0.1",
            kind="probe_revoke",  # type: ignore[arg-type]
        )
    finally:
        unregister_revocation_kind("probe_revoke")

    # 响应的键名与形状都来自声明，不是宿主里写死的 domain/token。
    assert payload == {"probe": {"normalized_domain": "probe.test"}}
    # 共享仪式照旧：CAS 递增、状态落 revoked、声明让出的那一列也写进去了。
    row = _rows(engine, PROBE_TABLE)[0]
    assert row["status"] == "revoked"
    assert int(row["revision"]) == revision + 1
    assert row["revoked_by"] == "owner-a"
    # 声明让出的那一列真的被仪式写进了 UPDATE。挑一列建域名时就是占位值、
    # 只有声明能把它改成这个字符串的 —— 早先用 updated_by 时它建出来就是 owner-a，
    # 断言根本区分不出"仪式写了"和"本来就写着"，变异跑（去掉 values.update）全绿。
    assert row["txt_value"] == "released:owner-a"
    assert host.read_bytes() == before, "宿主又被按 kind 点了名"
    assert resolve_revocation_kind("probe_revoke") is None


def test_the_table_a_revocation_locks_comes_from_the_declaration() -> None:
    """把声明里的 table_name 换掉，仪式就换表 —— 证明它不是从 kind 猜出来的。"""
    engine = _engine()
    domain_id = str(_create_domain(_client(engine, MemoryIdentityResolver()), "cross.test")
                    .json()["domain"]["id"])
    revision = int(_rows(engine, PROBE_TABLE)[0]["revision"])

    register_revocation_kind(_probe_spec(table_name="tenant_scim_tokens"))
    try:
        # 同一个 id 在另一张表里不存在：换表是真的换了表。
        with pytest.raises(control.IdentityNotFound):
            control._simple_state_mutation(  # noqa: SLF001
                engine,
                tenant_id=TENANT_A,
                actor_id="owner-a",
                actor_role="owner",
                resource_id=domain_id,
                expected_revision=revision,
                reason="换表探针",
                idempotency_key="axis11-cross-table",
                request_id="req-axis11-cross",
                request_ip="10.0.0.1",
                kind="probe_revoke",  # type: ignore[arg-type]
            )
    finally:
        unregister_revocation_kind("probe_revoke")
    assert _rows(engine, PROBE_TABLE)[0]["status"] != "revoked"


def test_an_undeclared_kind_is_refused_instead_of_becoming_a_scim_revoke() -> None:
    """旧写法最危险的地方：三元链的 else 会把任何没预期的 kind 当成 SCIM token 撤销。"""
    engine = _engine()
    with pytest.raises(ValueError):
        control._simple_state_mutation(  # noqa: SLF001
            engine,
            tenant_id=TENANT_A,
            actor_id="owner-a",
            actor_role="owner",
            resource_id="whatever",
            expected_revision=1,
            reason="没声明的 kind",
            idempotency_key="axis11-unknown",
            request_id="req-axis11-unknown",
            request_ip="10.0.0.1",
            kind="totally_new_kind",  # type: ignore[arg-type]
        )


# --------------------------------------------------------------------------- #
# 栅栏：表不能绕过共享仪式写下的那五列
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("column", sorted(FENCE_COLUMNS))
def test_a_kind_may_not_write_the_columns_that_are_the_revoke(column: str) -> None:
    before = set(revocation_kind_names())
    stolen = {"status": "revoked", "revision": 99, "revoked_at": None,
              "revoked_by": "x", "updated_at": None}[column]
    with pytest.raises(ValueError):
        register_revocation_kind(_probe_spec(release_values=lambda _actor: {column: stolen}))
    assert set(revocation_kind_names()) == before, "拒绝之后不该留下半条注册"


# --------------------------------------------------------------------------- #
# 内建声明的字面值 + 注册期形状判死
# --------------------------------------------------------------------------- #


def test_the_two_builtin_kinds_keep_their_exact_contracts() -> None:
    domain = resolve_revocation_kind("domain_revoke")
    scim = resolve_revocation_kind("scim_revoke")
    assert domain is not None and scim is not None
    assert (domain.operation, domain.resource_type, domain.table_name) == (
        "identity.domain.revoke",
        "tenant_verified_domain",
        "tenant_verified_domains",
    )
    assert (domain.payload_key, domain.audit_action) == ("domain", "tenant_domain.revoked")
    assert domain.release_values("actor-9") == {"updated_by": "actor-9"}

    assert (scim.operation, scim.resource_type, scim.table_name) == (
        "identity.scim.revoke",
        "tenant_scim_token",
        "tenant_scim_tokens",
    )
    assert (scim.payload_key, scim.audit_action) == ("token", "tenant_scim_token.revoked")
    # SCIM 必须让出"同名只允许一个生效"的槽位，否则轮换出来的新 token 建不出来。
    assert scim.release_values("actor-9") == {"active_name_key": None}


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"kind": "  "}, "空 kind"),
        ({"kind": "Probe_Revoke"}, "非规范拼写当键"),
        ({"operation": "  "}, "空 operation"),
        ({"resource_type": ""}, "空 resource_type"),
        ({"table_name": "   "}, "空表名"),
        ({"payload_key": ""}, "空响应键名"),
        ({"audit_action": ""}, "空审计动作"),
        ({"project": "not-callable"}, "projector 不可调用"),
        ({"release_values": None}, "release_values 不可调用"),
        ({"release_values": lambda _a: ["status"]}, "release_values 不返回 mapping"),
        ({"release_values": lambda _a: {"": "x"}}, "空列名"),
    ],
)
def test_a_bad_declaration_dies_at_registration(overrides: dict[str, Any], reason: str) -> None:
    before = set(revocation_kind_names())
    with pytest.raises((ValueError, TypeError)):
        register_revocation_kind(_probe_spec(**overrides))
    assert set(revocation_kind_names()) == before, reason


def test_only_a_real_declaration_can_be_registered() -> None:
    before = set(revocation_kind_names())
    for junk in ({"kind": "junk"}, "junk", None):
        with pytest.raises(TypeError):
            register_revocation_kind(junk)  # type: ignore[arg-type]
    assert set(revocation_kind_names()) == before


def test_replacing_an_existing_kind_is_explicit() -> None:
    register_revocation_kind(_probe_spec())
    try:
        with pytest.raises(ValueError):
            register_revocation_kind(_probe_spec(audit_action="second"))
        register_revocation_kind(_probe_spec(audit_action="second"), replace=True)
        assert resolve_revocation_kind("probe_revoke").audit_action == "second"  # type: ignore[union-attr]
    finally:
        unregister_revocation_kind("  PROBE_REVOKE ")
    assert resolve_revocation_kind("probe_revoke") is None


def test_the_ceremony_holds_no_copy_of_the_kind_branches() -> None:
    """宿主守卫：那八处 ``kind == "..."`` 不许长回来。"""
    source = inspect.getsource(control._simple_state_mutation)  # noqa: SLF001
    assert 'kind == "domain_revoke"' not in source
    assert "resolve_revocation_kind" in source
