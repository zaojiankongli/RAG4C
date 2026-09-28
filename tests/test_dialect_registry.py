"""Dialect spec registry guards: behaviour equivalence, fail-closed resolution,
and the "adding an engine costs zero edits to existing branches" criterion.

The old shape decided the engine by hand in ~58 places, including four written as
`dialect != "sqlite"` — which would have quietly classified any future fifth engine
as a real concurrent server. Resolution now fails closed instead.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest
from sqlalchemy import Boolean
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
REPO_ROOT = Path(__file__).resolve().parents[1]
_MIGRATION_DIR = REPO_ROOT / "catalog_migrations" / "versions"


def _dotted(node: ast.AST) -> str:
    """把 `sa.Column(...)`、`sa.Boolean()`、`sa.text("0")` 还原成点分名字。

    调用节点先剥到 `func`：不剥的话 `sa.Boolean()` 这种带括号的写法会被认成空名字，
    整条栅栏就只是装饰（变异反打实测到过一次）。
    """
    parts: list[str] = []
    cursor = node
    while isinstance(cursor, ast.Call):
        cursor = cursor.func
    while isinstance(cursor, ast.Attribute):
        parts.append(cursor.attr)
        cursor = cursor.value
    if isinstance(cursor, ast.Name):
        parts.append(cursor.id)
    return ".".join(reversed(parts))


def _integer_boolean_default(value: ast.AST) -> bool:
    """默认值是不是写成了整数（含 `sa.text("0")` / `"0"` / `0` 三种写法）。

    判定按节点语义走，不按源码字面串比对：`ast.unparse` 会把双引号规范成单引号，
    按字符串匹配的栅栏会被自己漏掉（同类的"只守得住它看得见的形状"在本仓已犯过三次）。
    """
    if isinstance(value, ast.Constant):
        return value.value in {"0", "1", 0, 1}
    if (
        isinstance(value, ast.Call)
        and _dotted(value.func) == "sa.text"
        and len(value.args) == 1
        and isinstance(value.args[0], ast.Constant)
    ):
        return value.args[0].value in {"0", "1", 0, 1}
    return False


def _integer_boolean_defaults(tree: ast.AST) -> list[str]:
    """返回把布尔列默认值写成整数的站点（跨行也认，按 Column 节点判）。"""
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _dotted(node.func) == "sa.Column"):
            continue
        if len(node.args) < 2 or _dotted(node.args[1]) != "sa.Boolean":
            continue
        for kw in node.keywords:
            if kw.arg == "server_default" and _integer_boolean_default(kw.value):
                offenders.append(f"line {node.lineno}: server_default={ast.unparse(kw.value)}")
    return offenders


def test_boolean_columns_never_default_themselves_to_an_integer():
    """Every Boolean column must default via sa.false()/sa.true(), not an integer.

    MySQL silently accepts `BOOLEAN DEFAULT 0`; PostgreSQL rejects the same DDL with
    `DatatypeMismatch: column ... is of type boolean but default expression is of type
    integer`, which stops the whole migration chain on the way up. The repository already
    renders dozens of boolean defaults through sa.false(), so the integer spelling is an
    accident rather than a convention - and the only dialect where it "works" is the one
    the migration suite never runs against.
    """
    offenders: list[str] = []
    for path in sorted(_MIGRATION_DIR.glob("*.py")):
        hits = _integer_boolean_defaults(ast.parse(path.read_text(encoding="utf-8")))
        offenders.extend(f"{path.name} {hit}" for hit in hits)
    assert offenders == []


def test_boolean_default_renders_as_a_real_boolean_literal_per_dialect():
    """The replacement must compile into something each dialect actually accepts.

    Also pins the counter-example: `sa.text("0")` still compiles to `DEFAULT 0` on
    PostgreSQL, i.e. the guard is not defending an imaginary failure.
    """
    import sqlalchemy as sa
    from sqlalchemy.schema import CreateTable

    good = sa.Table(
        "probe",
        sa.MetaData(),
        sa.Column("off_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("on_flag", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    rendered = {
        name: " ".join(CreateTable(good).compile(dialect=dialect()).string.split())
        for name, dialect in (("postgresql", postgresql.dialect), ("mysql", mysql.dialect),
                              ("sqlite", sqlite.dialect))
    }
    assert "DEFAULT false" in rendered["postgresql"] and "DEFAULT true" in rendered["postgresql"]
    assert "DEFAULT 0" in rendered["sqlite"] and "DEFAULT 1" in rendered["sqlite"]

    bad = sa.Table(
        "bad",
        sa.MetaData(),
        sa.Column("flag", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    bad_ddl = " ".join(CreateTable(bad).compile(dialect=postgresql.dialect()).string.split())
    assert "is of type boolean" not in bad_ddl  # 编译期不报错，是 PG 执行期才拒
    assert "DEFAULT 0" in bad_ddl
_INSERT_BOOL_RE = re.compile(
    r"INSERT\s+INTO\s+(?P<table>[\w.\"]+)\s*\((?P<cols>[^()]*)\)\s*"
    r"VALUES\s*\((?P<vals>[^()]*)\)",
    re.IGNORECASE | re.DOTALL,
)


def _boolean_columns_by_table() -> dict[str, set[str]]:
    from models.orm import Base

    out: dict[str, set[str]] = {}
    for table in Base.metadata.tables.values():
        names = {c.name for c in table.columns if isinstance(c.type, Boolean)}
        if names:
            out[table.name] = names
    return out


def _non_boolean_columns_by_table() -> dict[str, set[str]]:
    """同一张表里非布尔的列名，用来挡住"同名列在别的表是整数"的误报。"""
    from models.orm import Base

    return {
        table.name: {c.name for c in table.columns if not isinstance(c.type, Boolean)}
        for table in Base.metadata.tables.values()
    }


def _string_literals(tree: ast.AST) -> list[str]:
    """AST 里所有字符串常量（多行拼接的 SQL 也会被合成一个常量）。"""
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)]


def test_raw_sql_never_writes_a_boolean_column_as_an_integer():
    """裸 SQL 往布尔列写 0/1 时 MySQL 收、PG 拒 —— 整类扫一遍。

    覆盖"值进入数据库的通道"而不只是 DDL：迁移里的回填 INSERT、脚本里的种子
    INSERT 都算（`seed_workspace_channel_ownership.py` 就是实机连 PG 才现形的
    一例：`is_default` 写成 1，报 DatatypeMismatch，注册权威补不上）。
    """
    boolean_columns = _boolean_columns_by_table()
    sources = list((REPO_ROOT / "catalog_migrations" / "versions").glob("*.py"))
    sources += list((REPO_ROOT / "scripts").glob("*.py"))
    offenders: list[str] = []
    for path in sources:
        for literal in _string_literals(ast.parse(path.read_text(encoding="utf-8"))):
            match = _INSERT_BOOL_RE.search(literal)
            if not match:
                continue
            table = match.group("table").strip('"').split(".")[-1]
            columns = [c.strip().strip('"').casefold() for c in match.group("cols").split(",")]
            values = [v.strip() for v in match.group("vals").split(",")]
            if len(columns) != len(values):
                # 不齐就**算问题**：跳过等于给"改一下列清单让栅栏失明"留后门
                # （变异反打实测到：多写一个列名，整条 INSERT 就不再被检查）。
                offenders.append(
                    f"{path.name}: {table} 列数 {len(columns)} 与值数 {len(values)} 不符，无法核对"
                )
                continue
            for column, value in zip(columns, values):
                if column in boolean_columns.get(table, set()) and value in {"0", "1"}:
                    offenders.append(f"{path.name}: {table}.{column} <- {value}")
    assert offenders == []


_COMPARE_BOOL_RE = re.compile(
    r"\b(?P<col>[a-z_][a-z0-9_]*)\s*(?:=\s*(?P<val>[01])\b|<>|!=)"
    r"|\b(?P<col_in>[a-z_][a-z0-9_]*)\s+IN\s*\(\s*0\s*,\s*1\s*\)",
    re.IGNORECASE,
)
_TABLE_REF_RE = re.compile(
    r"\b(?:FROM|UPDATE|INTO|JOIN)\s+([a-z_][a-z0-9_]*)", re.IGNORECASE
)


def test_raw_sql_never_compares_a_boolean_column_to_an_integer():
    """读写之外的第三条通道：**比较**。布尔列 `= 1` 在 PG 直接报
    `operator does not exist: boolean = integer`，MySQL/sqlite 却收。

    实机例证（本轮在 PG 上跑企业体检脚本才现形）：
    `scripts/enterprise_catalog_upgrade.py` 的 `WHERE ... AND retrieval_enabled=1`，
    而同一份仓库在 CHECK 里本来就会写 `NOT retrieval_enabled`（`models/orm.py:6034`）——
    方言无关的写法一直在那里，只是这条 SQL 没用它。
    """
    boolean_columns = _boolean_columns_by_table()
    # 同名列在不同表里类型不同时不能武断判红：按"这条 SQL 涉及的表"取交集。
    non_boolean_columns = _non_boolean_columns_by_table()

    sources = list((REPO_ROOT / "catalog_migrations" / "versions").glob("*.py"))
    sources += list((REPO_ROOT / "scripts").glob("*.py"))
    sources += list((REPO_ROOT / "core").glob("*.py"))
    sources += list((REPO_ROOT / "server").glob("*.py"))
    offenders: list[str] = []
    for path in sources:
        for literal in _string_literals(ast.parse(path.read_text(encoding="utf-8"))):
            tables = {t.casefold() for t in _TABLE_REF_RE.findall(literal)}
            if not tables:
                continue
            for match in _COMPARE_BOOL_RE.finditer(literal):
                column = (match.group("col") or match.group("col_in") or "").casefold()
                if not column:
                    continue
                in_boolean_table = any(column in boolean_columns.get(t, set()) for t in tables)
                ambiguous = any(
                    column in non_boolean_columns.get(t, set()) for t in tables
                )
                if in_boolean_table and not ambiguous:
                    offenders.append(f"{path.name}: {sorted(tables)} 上 {column} 与整数比较")
    assert offenders == []
