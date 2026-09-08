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

REV = "0023_enterprise_audit_compliance"
DOWN = "0022_scim_provisioning_data_plane"
TABLES = {"tenant_audit_retention_policies", "tenant_audit_legal_holds", "tenant_audit_export_jobs"}


def mod():
    try:
        return importlib.import_module(
            "catalog_migrations.versions.0023_enterprise_audit_compliance"
        )
    except ModuleNotFoundError:
        pytest.fail("0023 missing")


def scope(tmp):
    u = f"sqlite:///{(tmp / 'a.db').as_posix()}"
    c = _alembic_config(u)
    command.upgrade(c, "0017_enterprise_access_graph")
    from tests.test_organization_membership_migration import _seed_0017_scope

    _seed_0017_scope(u)
    command.upgrade(c, DOWN)
    return u, c


def test_0023_precedes_current_head_and_matches_orm_manifest():
    m = mod()
    assert m.revision == REV and m.down_revision == DOWN
    assert (
        ScriptDirectory.from_config(_alembic_config("sqlite://")).get_current_head()
        == catalog_schema.HEAD_REVISION
        == "0029_enterprise_knowledge_base_releases"
    )
    import models.orm as o

    assert TABLES <= set(o.Base.metadata.tables) <= set(catalog_schema.HEAD_CATALOG_TABLES)
    for t in TABLES:
        assert (
            t in catalog_schema._HEAD_REQUIRED_UNIQUES
            and t in catalog_schema._HEAD_REQUIRED_FOREIGN_KEYS
            and t in catalog_schema._HEAD_REQUIRED_CHECK_FRAGMENTS
            and t in catalog_schema._HEAD_REQUIRED_INDEXES
        )


def test_bounds_active_hold_and_export_lifecycle(tmp_path: Path):
    u, c = scope(tmp_path)
    command.upgrade(c, REV)
    e = create_engine(u)
    n = "2026-08-26 12:00:00"
    with e.begin() as x:
        x.execute(
            text(
                "INSERT INTO tenant_audit_retention_policies (id,tenant_id,audit_retention_days,export_retention_days,status,revision,created_at,created_by,updated_at,updated_by) VALUES ('r','tenant-1',365,30,'active',1,:n,'account-1',:n,'account-1')"
            ),
            {"n": n},
        )
        x.execute(
            text(
                "INSERT INTO tenant_audit_legal_holds (id,tenant_id,name,active_name_key,reason,status,revision,created_at,created_by,updated_at,updated_by) VALUES ('h1','tenant-1','investigation','investigation','case','active',1,:n,'account-1',:n,'account-1')"
            ),
            {"n": n},
        )
    with pytest.raises(IntegrityError):
        with e.begin() as x:
            x.execute(
                text(
                    "INSERT INTO tenant_audit_legal_holds (id,tenant_id,name,active_name_key,reason,status,revision,created_at,created_by,updated_at,updated_by) VALUES ('h2','tenant-1','investigation','investigation','case2','active',1,:n,'account-1',:n,'account-1')"
                ),
                {"n": n},
            )
    with pytest.raises(IntegrityError):
        with e.begin() as x:
            x.execute(
                text(
                    "INSERT INTO tenant_audit_retention_policies (id,tenant_id,audit_retention_days,export_retention_days,status,revision,created_at,created_by,updated_at,updated_by) VALUES ('bad','tenant-2',29,0,'active',1,:n,'account-2',:n,'account-2')"
                ),
                {"n": n},
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
    assert "DATETIME(6)" in d and "JSON" in d and "SHA256" in d
