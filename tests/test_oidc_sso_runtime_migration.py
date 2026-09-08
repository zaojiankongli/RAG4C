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

REV = "0024_oidc_sso_runtime"
DOWN = "0023_enterprise_audit_compliance"
TABLES = {"tenant_oidc_login_transactions", "tenant_oidc_subject_links", "tenant_sso_sessions"}


def mod():
    try:
        return importlib.import_module("catalog_migrations.versions.0024_oidc_sso_runtime")
    except ModuleNotFoundError:
        pytest.fail("0024 missing")


def scope(tmp):
    u = f"sqlite:///{(tmp / 'o.db').as_posix()}"
    c = _alembic_config(u)
    command.upgrade(c, "0017_enterprise_access_graph")
    from tests.test_organization_membership_migration import _seed_0017_scope

    _seed_0017_scope(u)
    command.upgrade(c, DOWN)
    return u, c


def test_0024_precedes_current_head_and_matches_orm_manifest():
    m = mod()
    assert m.revision == REV and m.down_revision == DOWN
    assert (
        ScriptDirectory.from_config(_alembic_config("sqlite://")).get_current_head()
        == "0029_enterprise_knowledge_base_releases"
    )
    assert catalog_schema.HEAD_REVISION == "0029_enterprise_knowledge_base_releases"
    import models.orm as o

    assert TABLES <= set(o.Base.metadata.tables) <= set(catalog_schema.HEAD_CATALOG_TABLES)
    for t in TABLES:
        assert (
            t in catalog_schema._HEAD_REQUIRED_UNIQUES
            and t in catalog_schema._HEAD_REQUIRED_FOREIGN_KEYS
            and t in catalog_schema._HEAD_REQUIRED_CHECK_FRAGMENTS
            and t in catalog_schema._HEAD_REQUIRED_INDEXES
        )


def test_digest_and_lifecycle_constraints(tmp_path: Path):
    u, c = scope(tmp_path)
    command.upgrade(c, REV)
    e = create_engine(u)
    n = "2026-08-26 12:00:00"
    with pytest.raises(IntegrityError):
        with e.begin() as x:
            x.exec_driver_sql("PRAGMA foreign_keys=ON")
            x.execute(
                text(
                    "INSERT INTO tenant_oidc_login_transactions (id,tenant_id,provider_id,state_digest,nonce_digest,pkce_verifier_ciphertext,key_version,redirect_uri,expires_at,status,revision,created_at,updated_at) VALUES ('l','tenant-1','p-missing',:s,:q,'enc',1,'https://app.example/cb','2026-08-27','pending',1,:n,:n)"
                ),
                {"s": "a" * 64, "q": "b" * 64, "n": n},
            )
    e.dispose()


def test_roundtrip(tmp_path: Path):
    u, c = scope(tmp_path)
    command.upgrade(c, REV)
    command.downgrade(c, DOWN)
    e = create_engine(u)
    assert not TABLES & set(inspect(e).get_table_names())
    e.dispose()
    command.upgrade(c, REV)
    e = create_engine(u)
    assert TABLES <= set(inspect(e).get_table_names())
    e.dispose()


def test_mysql_offline():
    o = StringIO()
    c = _alembic_config("mysql+pymysql://u:p@localhost/r")
    c.output_buffer = o
    command.upgrade(c, f"{DOWN}:{REV}", sql=True)
    d = o.getvalue().upper()
    assert all(f"CREATE TABLE {t.upper()}" in d for t in TABLES)
    assert (
        "DATETIME(6)" in d
        and "STATE_DIGEST" in d
        and "PKCE_VERIFIER_CIPHERTEXT" in d
        and "SESSION_TOKEN_HASH" in d
    )
