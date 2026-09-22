"""Dialect spec registry guards: behaviour equivalence, fail-closed resolution,
and the "adding an engine costs zero edits to existing branches" criterion.

The old shape decided the engine by hand in ~58 places, including four written as
`dialect != "sqlite"` — which would have quietly classified any future fifth engine
as a real concurrent server. Resolution now fails closed instead.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.sql.elements import ColumnElement

import core.db_clock as db_clock
from core.dialects import (
    DIALECT_SPECS,
    DialectSpec,
    DialectUnsupported,
    dialect_utc_clock,
    has_real_concurrent_locking,
    register_dialect_spec,
    resolve_dialect,
)


def test_clock_sql_is_unchanged_per_engine() -> None:
    mysql_sql = str(select(dialect_utc_clock("mysql")).compile(dialect=mysql.dialect()))
    postgres_raw = dialect_utc_clock("postgresql")
    postgres_sql = str(select(postgres_raw).compile(dialect=postgresql.dialect()))
    sqlite_sql = str(select(dialect_utc_clock("sqlite")).compile(dialect=sqlite.dialect()))

    assert "utc_timestamp" in mysql_sql.casefold()
    assert "clock_timestamp" in postgres_sql.casefold()
    assert "AT TIME ZONE 'UTC'" in postgres_sql
    assert "current_timestamp" not in postgres_sql.casefold()
    assert "strftime" in sqlite_sql.casefold()
    # the declared column type still travels with the expression
    assert postgres_raw.type is not None


def test_aliases_and_spelling_are_normalised_to_one_canonical_key() -> None:
    for spelling in ("MariaDB", "  mariadb  ", "mariadb"):
        assert resolve_dialect(spelling).key == "mysql"
    assert resolve_dialect("postgres").key == "postgresql"
    assert resolve_dialect("MySQL").key == "mysql"


def test_unknown_engine_fails_closed_instead_of_inheriting_a_clock() -> None:
    for bad in ("cockroachdb", "oracle", "", None):
        with pytest.raises(DialectUnsupported):
            resolve_dialect(bad)  # type: ignore[arg-type]
    with pytest.raises(DialectUnsupported):
        dialect_utc_clock("cockroachdb")


def test_concurrent_locking_is_a_declared_capability_not_a_not_sqlite_test() -> None:
    assert has_real_concurrent_locking("sqlite") is False
    assert has_real_concurrent_locking("mariadb") is True
    assert has_real_concurrent_locking("postgresql") is True


def test_a_new_engine_needs_no_edit_to_the_existing_call_sites() -> None:
    """判据：注册一个方言后它能被解析，而既有宿主文件一个字都没改。"""
    host_before = Path(inspect.getsourcefile(db_clock) or "").read_bytes()
    probe = DialectSpec(
        key="probedb",
        label="Probe",
        aliases=(),
        real_concurrent_locking=True,
        utc_clock=lambda: dialect_utc_clock("mysql"),
    )
    register_dialect_spec(probe)
    try:
        assert resolve_dialect("probedb").key == "probedb"
        assert has_real_concurrent_locking("probedb") is True
        assert "probe" not in {name for name in DIALECT_SPECS.names() if name == "sqlite"}
    finally:
        DIALECT_SPECS.unregister("probedb")
    assert Path(inspect.getsourcefile(db_clock) or "").read_bytes() == host_before
    with pytest.raises(DialectUnsupported):
        resolve_dialect("probedb")


def test_registration_rejects_a_dialect_with_an_unusable_clock() -> None:
    def explode() -> ColumnElement:
        raise RuntimeError("可选依赖缺失")

    broken = DialectSpec(
        key="brokendb",
        label="Broken",
        aliases=(),
        real_concurrent_locking=True,
        utc_clock=explode,
    )
    with pytest.raises(DialectUnsupported, match="usable UTC clock"):
        register_dialect_spec(broken)

    assert "brokendb" not in DIALECT_SPECS.names()


def test_db_clock_module_no_longer_decides_the_engine_itself() -> None:
    """宿主守卫：方言判断只能在注册表里，不许回到 if 链。"""
    source = inspect.getsource(db_clock)
    for literal in ('"mysql"', '"mariadb"', '"postgresql"', '"sqlite"'):
        assert literal not in source, f"core/db_clock.py 仍在按字面量判引擎：{literal}"
