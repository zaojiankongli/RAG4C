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

REV = "0022_scim_provisioning_data_plane"
DOWN = "0021_enterprise_identity_federation"
TABLES = {"tenant_scim_user_links", "tenant_scim_group_links"}


def mod():
    try:
        return importlib.import_module(
            "catalog_migrations.versions.0022_scim_provisioning_data_plane"
        )
    except ModuleNotFoundError:
        pytest.fail("0022 missing")


def url(p):
    return f"sqlite:///{p.as_posix()}"


def scope(tmp):
    u = url(tmp / "s.db")
    c = _alembic_config(u)
    command.upgrade(c, "0017_enterprise_access_graph")
    from tests.test_organization_membership_migration import _seed_0017_scope

    _seed_0017_scope(u)
    command.upgrade(c, DOWN)
    e = create_engine(u)
    n = "2026-08-26 12:00:00"
    with e.begin() as x:
        x.execute(
            text(
                "INSERT INTO tenant_scim_tokens (id,tenant_id,name,active_name_key,token_hash,token_prefix,status,scopes,expires_at,revision,issued_at,issued_by,created_at,updated_at) VALUES ('tok','tenant-1','sync','sync',:h,'rag4c_','active','[]','2026-09-26',1,:n,'account-1',:n,:n)"
            ),
            {"h": "a" * 64, "n": n},
        )
    return u, c


def test_0022_precedes_current_head_and_matches_orm_manifest():
    m = mod()
    assert m.revision == REV and m.down_revision == DOWN
    assert ScriptDirectory.from_config(_alembic_config("sqlite://")).get_current_head() == (
        catalog_schema.HEAD_REVISION
    )
    # 0022 已被后续 stage 超越。原先这里硬编码了当时的 head 字符串，
    # 于是每加一条迁移都会误挂——改为引用单一事实源 + 断言"本迁移不是 head"。
    assert m.revision != catalog_schema.HEAD_REVISION
    import models.orm as o

    assert TABLES <= set(o.Base.metadata.tables) <= set(catalog_schema.HEAD_CATALOG_TABLES)
    for t in TABLES:
        assert (
            t in catalog_schema._HEAD_REQUIRED_UNIQUES
            and t in catalog_schema._HEAD_REQUIRED_FOREIGN_KEYS
            and t in catalog_schema._HEAD_REQUIRED_CHECK_FRAGMENTS
            and t in catalog_schema._HEAD_REQUIRED_INDEXES
        )


def test_uniques_and_token_evidence(tmp_path: Path):
    u, c = scope(tmp_path)
    command.upgrade(c, REV)
    e = create_engine(u)
    n = "2026-08-26 12:00:00"
    with e.begin() as x:
        x.exec_driver_sql("PRAGMA foreign_keys=ON")
        x.execute(
            text(
                "INSERT INTO tenant_scim_user_links (id,tenant_id,account_id,external_id,user_name,revision,last_provisioned_at,source_token_id,created_at,updated_at) VALUES ('u1','tenant-1','account-1','ext','a@example.test',1,:n,'tok',:n,:n)"
            ),
            {"n": n},
        )
    for col, val in [
        ("external_id", "ext"),
        ("user_name", "a@example.test"),
        ("account_id", "account-1"),
    ]:
        with pytest.raises(IntegrityError):
            with e.begin() as x:
                x.execute(
                    text(
                        f"INSERT INTO tenant_scim_user_links (id,tenant_id,account_id,external_id,user_name,revision,last_provisioned_at,source_token_id,created_at,updated_at) VALUES ('u-{col}','tenant-1',:account,:external,:user,1,:n,'tok',:n,:n)"
                    ),
                    {
                        "account": val if col == "account_id" else "account-1",
                        "external": val if col == "external_id" else col,
                        "user": val if col == "user_name" else col + "@x",
                        "n": n,
                    },
                )
    with e.connect() as x:
        row = x.execute(
            text("SELECT use_count,last_used_ip_hash FROM tenant_scim_tokens WHERE id='tok'")
        ).one()
        assert tuple(row) == (0, None)
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
    assert (
        "CREATE TABLE TENANT_SCIM_USER_LINKS" in d
        and "CREATE TABLE TENANT_SCIM_GROUP_LINKS" in d
        and "LAST_USED_IP_HASH" in d
        and "USE_COUNT" in d
        and "DATETIME(6)" in d
    )
