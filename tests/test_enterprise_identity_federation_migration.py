from __future__ import annotations
import importlib
from io import StringIO
from pathlib import Path
import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from core import catalog_schema
from core.catalog_schema import _alembic_config

REVISION = "0021_enterprise_identity_federation"
DOWN = "0020_tenant_invitation_lifecycle"
TABLES = {"tenant_verified_domains", "tenant_identity_providers", "tenant_scim_tokens"}


def _module():
    try:
        return importlib.import_module(
            "catalog_migrations.versions.0021_enterprise_identity_federation"
        )
    except ModuleNotFoundError:
        pytest.fail("0021 migration missing")


def _url(p: Path):
    return f"sqlite:///{p.as_posix()}"


def _scope(tmp: Path):
    u = _url(tmp / "identity.db")
    c = _alembic_config(u)
    command.upgrade(c, "0017_enterprise_access_graph")
    from tests.test_organization_membership_migration import _seed_0017_scope

    _seed_0017_scope(u)
    command.upgrade(c, DOWN)
    return u, c


def test_0021_precedes_current_head_and_tables_match_orm_manifest():
    m = _module()
    s = ScriptDirectory.from_config(_alembic_config("sqlite://"))
    assert m.revision == REVISION and m.down_revision == DOWN
    assert s.get_current_head() == catalog_schema.HEAD_REVISION
    # 0021 已被后续 stage 超越。原先这里硬编码了当时的 head 字符串，
    # 于是每加一条迁移都会误挂——改为引用单一事实源 + 断言"本迁移不是 head"。
    assert m.revision != catalog_schema.HEAD_REVISION
    import models.orm as orm

    assert TABLES <= set(orm.Base.metadata.tables) <= set(catalog_schema.HEAD_CATALOG_TABLES)
    for name in TABLES:
        assert name in catalog_schema._HEAD_REQUIRED_COLUMNS
        assert name in catalog_schema._HEAD_REQUIRED_UNIQUES
        assert name in catalog_schema._HEAD_REQUIRED_FOREIGN_KEYS
        assert name in catalog_schema._HEAD_REQUIRED_CHECK_FRAGMENTS
        assert name in catalog_schema._HEAD_REQUIRED_INDEXES


def test_0021_unique_domain_active_provider_and_active_token(tmp_path: Path):
    u, c = _scope(tmp_path)
    command.upgrade(c, REVISION)
    e = create_engine(u)
    now = "2026-08-26 12:00:00"
    with e.begin() as x:
        x.exec_driver_sql("PRAGMA foreign_keys=ON")
        x.execute(
            text(
                "INSERT INTO tenant_verified_domains (id,tenant_id,normalized_domain,status,verification_method,challenge_token,txt_host,txt_value,revision,created_at,created_by,updated_at,updated_by) VALUES ('d1','tenant-1','example.com','pending','dns_txt','c','_rag4c-verify.example.com','rag4c-verification=c',1,:n,'account-1',:n,'account-1')"
            ),
            {"n": now},
        )
    for sql in [
        "INSERT INTO tenant_verified_domains (id,tenant_id,normalized_domain,status,verification_method,challenge_token,txt_host,txt_value,revision,created_at,created_by,updated_at,updated_by) VALUES ('d2','tenant-2','example.com','pending','dns_txt','c2','h','v',1,:n,'account-2',:n,'account-2')",
    ]:
        with pytest.raises(IntegrityError):
            with e.begin() as x:
                x.execute(text(sql), {"n": now})
    with e.begin() as x:
        x.execute(
            text(
                "INSERT INTO tenant_identity_providers (id,tenant_id,name,provider_type,status,active_slot,trusted_domain_id,issuer_url,client_id,secret_ref,scopes,validation_state,revision,created_at,created_by,updated_at,updated_by) VALUES ('p1','tenant-1','OIDC','oidc','active','primary','d1','https://id.example.com','client','vault://idp','openid','valid',1,:n,'account-1',:n,'account-1')"
            ),
            {"n": now},
        )
    with pytest.raises(IntegrityError):
        with e.begin() as x:
            x.execute(
                text(
                    "INSERT INTO tenant_identity_providers (id,tenant_id,name,provider_type,status,active_slot,trusted_domain_id,issuer_url,client_id,secret_ref,scopes,validation_state,revision,created_at,created_by,updated_at,updated_by) VALUES ('p2','tenant-1','OIDC2','oidc','active','primary','d1','https://id2.example.com','client2','vault://idp2','openid','valid',1,:n,'account-1',:n,'account-1')"
                ),
                {"n": now},
            )
    with e.begin() as x:
        x.execute(
            text(
                "INSERT INTO tenant_scim_tokens (id,tenant_id,name,active_name_key,token_hash,token_prefix,status,scopes,expires_at,revision,issued_at,issued_by,created_at,updated_at) VALUES ('t1','tenant-1','sync','sync',:h,'rag4c_scim_','active','[\"users\"]','2026-09-26',1,:n,'account-1',:n,:n)"
            ),
            {"h": "a" * 64, "n": now},
        )
    with pytest.raises(IntegrityError):
        with e.begin() as x:
            x.execute(
                text(
                    "INSERT INTO tenant_scim_tokens (id,tenant_id,name,active_name_key,token_hash,token_prefix,status,scopes,expires_at,revision,issued_at,issued_by,created_at,updated_at) VALUES ('t2','tenant-1','sync','sync',:h,'rag4c_scim_','active','[\"users\"]','2026-09-26',1,:n,'account-1',:n,:n)"
                ),
                {"h": "b" * 64, "n": now},
            )
    e.dispose()


def test_0020_0021_roundtrip(tmp_path: Path):
    u, c = _scope(tmp_path)
    command.upgrade(c, REVISION)
    command.downgrade(c, DOWN)
    e = create_engine(u)
    assert not TABLES & set(inspect(e).get_table_names())
    e.dispose()
    command.upgrade(c, REVISION)
    e = create_engine(u)
    assert TABLES <= set(inspect(e).get_table_names())
    assert catalog_schema.inspect_catalog_schema(e).status == "behind"
    e.dispose()


def test_0021_mysql_offline_ddl():
    o = StringIO()
    c = _alembic_config("mysql+pymysql://u:p@localhost/rag4c")
    c.output_buffer = o
    command.upgrade(c, f"{DOWN}:{REVISION}", sql=True)
    d = o.getvalue().upper()
    for t in TABLES:
        assert f"CREATE TABLE {t.upper()}" in d
    assert "DATETIME(6)" in d and "JSON" in d and "ACTIVE_SLOT" in d and "ACTIVE_NAME_KEY" in d
