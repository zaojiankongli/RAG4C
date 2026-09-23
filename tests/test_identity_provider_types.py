"""扩展轴 #10：身份 provider 的字段形状收成声明，未知类型仍然拒绝。

两种内置类型的投影是行为（HTTPS、scope 排序、可选 metadata），不是两行数据。
宿主只查表。没登记的类型继续抛原来的 `provider_type is invalid`，不许落到另一种类型上。

登记一种类型不会加宽 CHECK。字段组合 CHECK 在迁移 0021 和 API 测试的建表语句里，
ORM 模型目前只有 `provider_type` 的 IN 列表，本套不去假装模型里也有那条组合约束。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.enterprise_identity_control import IdentityValidationError, _provider_values
from core.identity_provider_types import (
    STORAGE_COLUMNS,
    IdentityProviderTypeSpec,
    builtin_identity_provider_types,
    identity_provider_type,
    register_identity_provider_type,
    unregister_identity_provider_type,
)
import core.enterprise_identity_control as control

REPO = Path(__file__).resolve().parents[1]
HOST = REPO / "core" / "enterprise_identity_control.py"
ORM = REPO / "models" / "orm.py"
MIGRATION = REPO / "catalog_migrations" / "versions" / "0021_enterprise_identity_federation.py"
API_DDL = REPO / "tests" / "test_enterprise_identity_federation_api.py"

_IN_LIST_RE = re.compile(r"provider_type IN \(([^)]*)\)")
_BRANCH_RE = re.compile(
    r"provider_type='([^']+)'((?: AND [a-z_]+ IS NOT NULL)*)",
)


class _NoDns:
    def resolve_txt(self, name: str) -> tuple[str, ...]:
        raise AssertionError(name)

    def resolve_host_addresses(self, host: str) -> tuple[str, ...]:
        raise AssertionError(host)


def _ldap(**overrides: object) -> IdentityProviderTypeSpec:
    def project(data: dict, resolver: object) -> dict[str, object]:
        del data, resolver
        return {column: None for column in STORAGE_COLUMNS} | {"issuer_url": "https://1.1.1.1/ldap"}

    spec = IdentityProviderTypeSpec(
        provider_type="ldap",
        required_not_null=("issuer_url",),
        project=project,
    )
    if not overrides:
        return spec
    return IdentityProviderTypeSpec(**{**spec.__dict__, **overrides})


def test_builtin_projection_keeps_the_observed_field_split() -> None:
    oidc = _provider_values(
        "oidc",
        {
            "issuer_url": "https://1.1.1.1/issuer",
            "client_id": "client-a",
            "secret_ref": "vault://identity/oidc/client-secret",
            "scopes": ["email", "openid", "email"],
        },
        _NoDns(),
    )
    assert oidc == {
        "issuer_url": "https://1.1.1.1/issuer",
        "client_id": "client-a",
        "secret_ref": "vault://identity/oidc/client-secret",
        "scopes": "email openid",
        "entity_id": None,
        "sso_url": None,
        "metadata_url": None,
        "certificate_fingerprint": None,
    }
    omitted = _provider_values(
        "oidc",
        {
            "issuer_url": "https://1.1.1.1/issuer",
            "client_id": "client-a",
            "secret_ref": "vault://identity/oidc/client-secret",
            "scopes": [],
        },
        _NoDns(),
    )
    assert omitted["scopes"] == "openid"

    saml = _provider_values(
        "saml",
        {
            "entity_id": "sp-entity",
            "sso_url": "https://1.1.1.1/sso",
            "certificate_fingerprint": "ab",
        },
        _NoDns(),
    )
    assert saml == {
        "issuer_url": None,
        "client_id": None,
        "secret_ref": None,
        "scopes": None,
        "entity_id": "sp-entity",
        "sso_url": "https://1.1.1.1/sso",
        "metadata_url": None,
        "certificate_fingerprint": "ab",
    }

    with pytest.raises(IdentityValidationError, match="scopes must be a list"):
        _provider_values(
            "oidc",
            {
                "issuer_url": "https://1.1.1.1/issuer",
                "client_id": "client-a",
                "secret_ref": "vault://identity/oidc/client-secret",
                "scopes": "openid",
            },
            _NoDns(),
        )
    with pytest.raises(IdentityValidationError, match="provider_type is invalid"):
        _provider_values("ldap", {}, _NoDns())


def test_registering_a_type_projects_it_without_editing_the_host_or_the_builtins() -> None:
    before = HOST.read_bytes()
    builtins = {spec.provider_type for spec in builtin_identity_provider_types()}
    register_identity_provider_type(_ldap())
    try:
        projected = _provider_values("ldap", {}, _NoDns())
        assert projected["issuer_url"] == "https://1.1.1.1/ldap"
        assert set(projected) == set(STORAGE_COLUMNS)
        assert {spec.provider_type for spec in builtin_identity_provider_types()} == builtins
        assert identity_provider_type("oidc") is not None
    finally:
        unregister_identity_provider_type("ldap")
    assert HOST.read_bytes() == before
    with pytest.raises(IdentityValidationError, match="provider_type is invalid"):
        _provider_values("ldap", {}, _NoDns())


def test_registration_rejects_a_shape_that_would_silently_change_projection() -> None:
    with pytest.raises(ValueError, match="already registered"):
        register_identity_provider_type(_ldap(provider_type="oidc"))
    with pytest.raises(ValueError, match="required_not_null"):
        register_identity_provider_type(_ldap(required_not_null=()))
    with pytest.raises(ValueError, match="required_not_null"):
        register_identity_provider_type(_ldap(required_not_null=("not_a_column",)))


def _constraint_sql(text: str, name: str) -> str | None:
    marker = f'name="{name}"'
    at = text.find(marker)
    if at != -1:
        start = text.rfind("CheckConstraint(", 0, at)
        parts = re.findall(r'"([^"]*)"', text[start:at])
        return "".join(parts) if parts else None
    match = re.search(rf"CONSTRAINT {name}\s+CHECK \((.*?)\)\s*,", text, re.S)
    if match is None:
        return None
    return " ".join(match.group(1).split())


def _branches(sql: str) -> dict[str, frozenset[str]]:
    found: dict[str, frozenset[str]] = {}
    for provider_type, tail in _BRANCH_RE.findall(sql):
        columns = frozenset(re.findall(r"([a-z_]+) IS NOT NULL", tail))
        found[provider_type] = columns
    return found


def test_builtin_types_match_every_stored_in_list_and_the_schema_probe() -> None:
    expected = {spec.provider_type for spec in builtin_identity_provider_types()}
    assert expected == {"oidc", "saml"}
    for path in (ORM, MIGRATION, API_DDL):
        lists = [
            frozenset(part.strip().strip("'") for part in match.group(1).split(","))
            for match in _IN_LIST_RE.finditer(path.read_text(encoding="utf-8"))
        ]
        assert lists, path
        assert all(item == expected for item in lists), path
    probe = set(control._CHECKS["tenant_identity_providers"]["ck_tenant_identity_providers_type"])
    assert probe == expected


def test_field_combination_checks_match_builtin_required_columns() -> None:
    expected = {
        spec.provider_type: frozenset(spec.required_not_null)
        for spec in builtin_identity_provider_types()
    }
    seen = []
    for path in (MIGRATION, API_DDL):
        sql = _constraint_sql(path.read_text(encoding="utf-8"), "ck_tenant_identity_providers_type_fields")
        assert sql, path
        assert _branches(sql) == expected
        seen.append(path)
    assert seen
