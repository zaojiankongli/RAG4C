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
    PROTECTED_COLUMNS,
    PROTECTED_REASONS,
    PROVENANCE_COLUMNS,
    SCOPE_COLUMNS,
    RevocationKindSpec,
    register_revocation_kind,
    resolve_revocation_kind,
    revocation_kind_names,
    unregister_revocation_kind,
)
from tests.test_enterprise_identity_federation_api import (
    TENANT_A,
    TENANT_B,
    MemoryIdentityResolver,
    _client,
    _create_domain,
    _engine,
    _rows,
)

PROBE_TABLE = "tenant_verified_domains"


def _as_dict(value: Any) -> Any:
    """快照列在 sqlite 上是 JSON 文本，取法随驱动而定 —— 这里只比较内容。"""
    import json

    return json.loads(value) if isinstance(value, (str, bytes)) else value


def _probe_spec(**overrides: Any) -> RevocationKindSpec:
    fields: dict[str, Any] = {
        "kind": "probe_revoke",
        "operation": "identity.probe.revoke",
        "resource_type": "tenant_probe",
        "table_name": PROBE_TABLE,
        "payload_key": "probe",
        "audit_action": "tenant_probe.revoked",
        "project": lambda row: {
            # 投影要看得见"撤销改了什么"：只读 normalized_domain 的投影，让审计把 before
            # 写成"什么都没变"也照样全绿（评审发现 C）。
            "normalized_domain": str(row["normalized_domain"]),
            "status": str(row["status"]),
            "revision": int(row["revision"]),
        },
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
    created_row = _rows(engine, PROBE_TABLE)[0]
    revision = int(created_row["revision"])
    status_before = str(created_row["status"])

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
        # 同一个幂等键重放必须拿回同一份响应：把 response_for_replay 写成空也照样"能用"，
        # 但操作员重放时会看到一次没有结果的撤销（评审发现 C）。
        replayed = control._simple_state_mutation(  # noqa: SLF001
            engine,
            tenant_id=TENANT_A,
            actor_id="owner-a",
            actor_role="owner",
            resource_id=domain_id,
            expected_revision=revision,
            reason="探针撤销",
            idempotency_key="axis11-probe",
            request_id="req-axis11-probe-replay",
            request_ip="10.0.0.1",
            kind="probe_revoke",  # type: ignore[arg-type]
        )
    finally:
        unregister_revocation_kind("probe_revoke")

    # 响应的键名与形状都来自声明，不是宿主里写死的 domain/token；投影取的是**更新后**的行。
    assert payload == {
        "probe": {"normalized_domain": "probe.test", "status": "revoked", "revision": revision + 1}
    }
    assert replayed == payload
    # 共享仪式照旧：CAS 递增、状态落 revoked、声明让出的那一列也写进去了。
    row = _rows(engine, PROBE_TABLE)[0]
    assert row["status"] == "revoked"
    assert int(row["revision"]) == revision + 1
    assert row["revoked_by"] == "owner-a"
    # 声明让出的那一列真的被仪式写进了 UPDATE。挑一列建域名时就是占位值、
    # 只有声明能把它改成这个字符串的 —— 早先用 updated_by 时它建出来就是 owner-a，
    # 断言根本区分不出"仪式写了"和"本来就写着"，变异跑（去掉 values.update）全绿。
    assert row["txt_value"] == "released:owner-a"
    # 审计与幂等预留这两份"仪式的产物"也必须逐字取自声明 —— 评审发现 I1/I2/I3/I3b/I6：
    # 把 action / resource_type / before / after 换成字面量，原先 23 条守卫全绿，因为它们
    # 只看声明对象的字段，从没看过落库的那一行。
    events = [
        r for r in _rows(engine, "tenant_audit_events") if r["resource_type"] == "tenant_probe"
    ]
    assert len(events) == 1, "审计行的 resource_type 没照声明写（这一列没有任何约束兜着）"
    event = events[0]
    assert event["action"] == "tenant_probe.revoked"
    assert event["resource_id"] == domain_id
    assert _as_dict(event["before_snapshot"]) == {
        "normalized_domain": "probe.test",
        "status": status_before,
        "revision": revision,
    }
    assert _as_dict(event["after_snapshot"]) == {
        "normalized_domain": "probe.test",
        "status": "revoked",
        "revision": revision + 1,
        "reason": "探针撤销",
    }
    reserved = [
        r
        for r in _rows(engine, "tenant_control_mutation_requests")
        if r["operation"] == "identity.probe.revoke"
    ]
    assert len(reserved) == 1, "幂等预留的 operation 没照声明写"
    assert reserved[0]["resource_type"] == "tenant_probe"
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


def test_the_protected_column_sets_are_exactly_what_the_ceremony_owns() -> None:
    """把 FENCE_COLUMNS 里的任何一列删掉，参数化用例只会**少跑一条**而不是失败 —— 评审发现 B
    就是这样放过"少一列"的。这里把三组字面值钉死。"""
    assert FENCE_COLUMNS == frozenset(
        {"status", "revision", "revoked_at", "revoked_by", "updated_at"}
    )
    assert SCOPE_COLUMNS == frozenset({"id", "tenant_id", "created_at"})
    assert PROVENANCE_COLUMNS == frozenset(
        {"created_by", "verified_by", "issued_by", "issued_at"}
    )
    assert PROTECTED_COLUMNS == FENCE_COLUMNS | SCOPE_COLUMNS | PROVENANCE_COLUMNS


def test_updated_by_is_the_one_provenance_column_a_kind_still_owns() -> None:
    """``updated_by`` 看着像溯源列，但它是 kind **该有**的一条：仪式自己盖 ``updated_at``，
    而"是谁做的这次释放"只有声明知道 —— 内建 ``domain_revoke`` 返回的正是
    ``{"updated_by": actor_id}``（:330 那条字面值钉用例）。

    把它列进禁区会在 **import 期**就炸（``_validate_spec`` 对每个内建 kind 探一次
    ``release_values``，第九轮评审把我这句旧注释纠正了过来），所以真出问题时是响的；
    这一条钉的不是"炸不炸"，而是"这一列归谁写"。
    """
    assert "updated_by" not in PROTECTED_COLUMNS
    assert "updated_by" not in PROVENANCE_COLUMNS
    register_revocation_kind(
        _probe_spec(release_values=lambda actor: {"updated_by": f"by-{actor}"})
    )
    try:
        assert resolve_revocation_kind("probe_revoke") is not None
    finally:
        unregister_revocation_kind("probe_revoke")


def test_every_protected_column_carries_a_reason_and_the_reason_set_is_the_check() -> None:
    """注册期查的是 ``PROTECTED_REASONS`` 的键集合，不是三个成员集合各写一遍。

    少一条理由 = 那一列同时躲过注册期与写侧（写侧查 ``PROTECTED_COLUMNS``，两边就此脱钩）。
    加一个新集合忘了登记理由，这里就红，而不是安静地放行。
    """
    assert frozenset(PROTECTED_REASONS) == PROTECTED_COLUMNS
    assert set(PROTECTED_REASONS) & {"updated_by", "txt_value", "active_name_key"} == set()
    assert all(reason.strip() for reason in PROTECTED_REASONS.values())


@pytest.mark.parametrize("column", sorted(PROTECTED_COLUMNS))
def test_a_kind_may_not_write_the_columns_the_ceremony_or_the_row_identity_owns(column: str) -> None:
    before = set(revocation_kind_names())
    stolen = {
        "status": "revoked", "revision": 99, "revoked_at": None, "revoked_by": "x",
        "updated_at": None, "id": "someone-elses-row", "tenant_id": TENANT_B,
        "created_at": None,
        # 溯源四列：值随便给一个"改写历史"的形状即可，判据是注册期就得拒。
        "created_by": "someone-else", "verified_by": None,
        "issued_by": "someone-else", "issued_at": None,
    }[column]
    with pytest.raises(ValueError):
        register_revocation_kind(_probe_spec(release_values=lambda _actor: {column: stolen}))
    assert set(revocation_kind_names()) == before, "拒绝之后不该留下半条注册"


def _run_probe_ceremony(engine: Any, resource_id: str, revision: int, key: str) -> Any:
    return control._simple_state_mutation(  # noqa: SLF001
        engine,
        tenant_id=TENANT_A,
        actor_id="owner-a",
        actor_role="owner",
        resource_id=resource_id,
        expected_revision=revision,
        reason="栅栏探针",
        idempotency_key=key,
        request_id=f"req-{key}",
        request_ip="10.0.0.1",
        kind="probe_revoke",  # type: ignore[arg-type]
    )


STOLEN_PROBES = {
    # status 这一列在库里有 CHECK 兜着（写非法值会被约束层拦），所以它的红不能只算在
    # 栅栏头上；revoked_by 与 tenant_id 没有任何约束兜，绕过栅栏时它们是**只有**这道栅栏
    # 能挡的那两列 —— 后者还会把行搬到别的租户，而审计仍记请求租户，链路说的是假话。
    "status": "verified",
    "revoked_by": "fabricated-actor",
    "tenant_id": TENANT_B,
}


@pytest.mark.parametrize("column", sorted(STOLEN_PROBES))
@pytest.mark.parametrize("slip", ["by-actor", "by-call-count"])
def test_the_fence_is_held_at_write_time_not_only_at_registration(
    slip: str,
    column: str,
) -> None:
    """注册期只把 release_values 拿一个合成 actor **试调一次**，所以按参数（或按调用次数）
    分支的声明能骗过 ``_validate_spec``。写 UPDATE 之前那一侧必须由仪式自己把住，否则声明
    就能推翻仪式：``status`` 被写回撤销前的值 → 一次"撤销"落成"没撤销"（版本跳了、状态没
    变、还返回 200）；``revoked_by`` 被改写 → 审计上这次撤销记在一个假 actor 名下。
    """
    calls = []

    def release_values(_actor: str) -> dict[str, Any]:
        calls.append(_actor)
        clean_call = (_actor == "probe-actor") if slip == "by-actor" else len(calls) <= 1
        return {"txt_value": "clean"} if clean_call else {column: STOLEN_PROBES[column]}

    engine = _engine()
    domain_id = str(_create_domain(_client(engine, MemoryIdentityResolver()), "fence.test")
                    .json()["domain"]["id"])
    created = _rows(engine, PROBE_TABLE)[0]
    revision = int(created["revision"])
    status_before = str(created["status"])

    register_revocation_kind(_probe_spec(release_values=release_values))
    try:
        with pytest.raises(control.IdentityControlUnavailable) as excinfo:
            _run_probe_ceremony(engine, domain_id, revision, "axis11-fence-runtime")
        cause = excinfo.value.__cause__
        assert isinstance(cause, ValueError), f"{slip}/{column} 的声明没被写侧拦住"
        assert "release_values" in str(cause)
    finally:
        unregister_revocation_kind("probe_revoke")

    assert len(calls) == 2, "注册期 + 写期各一次"
    row = _rows(engine, PROBE_TABLE)[0]
    # 拒绝发生在 UPDATE 之前：整行还是撤销前的样子（既没被写成"没撤销"，也没把 CAS 推上去，
    # 更没有一列被声明改名）。
    assert row["status"] == status_before != "revoked"
    assert int(row["revision"]) == revision
    assert row["revoked_at"] is None and row["revoked_by"] is None
    # 这条是补刀，不是判据：上面"状态与 revision 都没动"已经证明没有任何半行写入。
    assert row["txt_value"] != "clean"


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


def test_the_builtin_declarations_survive_a_second_instantiation() -> None:
    """评审发现 G：内建注册发生在宿主模块末尾、往导入进来的表里写 —— 宿主被第二次实例化
    （``importlib.reload`` 或按别的模块名副本装载）时，第二次注册会当场炸在启动路径上。
    内建声明必须可重放。
    """
    control.register_builtin_revocation_kinds()
    control.register_builtin_revocation_kinds()
    assert {"domain_revoke", "scim_revoke"} <= set(revocation_kind_names())
