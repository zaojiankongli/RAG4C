"""SQL dialect specs — the one place that states what each engine can actually do.

Before this module, the dialect was re-decided by hand in ~58 places across 11 files:
`dialect in {"mysql", "mariadb"}` retyped as a set literal fifteen-odd times, and four
sites written as `dialect != "sqlite"`. Both shapes are quiet traps: a fifth engine added
tomorrow would fall into the "not sqlite, therefore a real server" branch without anyone
having considered it, and `db_utc_expression` would silently hand back `CURRENT_TIMESTAMP`
as the UTC wall-clock authority that lease decisions depend on.

So resolution here fails closed: an unrecognized dialect raises. A spec must declare every
capability it is asked for, and `register_dialect_spec` exercises it at registration time,
so a new engine cannot half-exist.

Capabilities are added only when a call site is actually converted onto them. The
trigger-contract and upsert shapes are still branchy in `core/catalog_schema.py` and
`core/catalog.py`; they join this table in their own waves, not before.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import DateTime, func, literal_column
from sqlalchemy.sql.elements import ColumnElement

from core.providers import ProviderRegistry

__all__ = [
    "BUILTIN_DIALECT_SPECS",
    "DIALECT_SPECS",
    "DialectSpec",
    "DialectUnsupported",
    "dialect_utc_clock",
    "has_real_concurrent_locking",
    "register_dialect_spec",
    "resolve_dialect",
]


class DialectUnsupported(RuntimeError):
    """Raised for an engine nobody has declared a spec for. Never guess instead."""


@dataclass(frozen=True)
class DialectSpec:
    key: str
    label: str
    aliases: tuple[str, ...]
    # Does this engine serialise concurrent writers with real row/table locks? Only
    # embedded SQLite lacks them — which is exactly why the answer used to be written
    # as `dialect != "sqlite"`, silently including engines nobody had ever tested.
    real_concurrent_locking: bool
    utc_clock: Callable[[], ColumnElement[Any]]
    # 布尔列在 CHECK 约束里的字面量。PG 的 boolean 与 integer 不可比
    # （``col IN (0, 1)`` 会报 ``operator does not exist: boolean = integer``），
    # 必须写成 true/false；MySQL / SQLite 里 true/false 只是 1/0 的别名，
    # 因此沿用数字形态可以保持与存量库里已存的约束文本一致。
    boolean_literals: tuple[str, str] = ("1", "0")


def boolean_check_sql(column: str, dialect: str) -> str:
    """返回 ``<column> IN (...)`` 的方言正确写法（布尔列专用）。

    方言差异只允许落在这一处：``models/orm.py``、Alembic 迁移与 schema 校验
    三处都从这里取，避免哪天换个引擎又出现"PG 上建不了表"。
    """
    spec = resolve_dialect(dialect)
    true_value, false_value = spec.boolean_literals
    return f"{column} IN ({true_value}, {false_value})"


def _mysql_utc_clock() -> ColumnElement[Any]:
    return func.utc_timestamp(6)


def _postgresql_utc_clock() -> ColumnElement[Any]:
    return literal_column(
        "(clock_timestamp() AT TIME ZONE 'UTC')", type_=DateTime()
    )


def _sqlite_utc_clock() -> ColumnElement[Any]:
    return func.strftime("%Y-%m-%d %H:%M:%f", "now")


BUILTIN_DIALECT_SPECS: tuple[DialectSpec, ...] = (
    DialectSpec(
        key="sqlite",
        label="SQLite",
        aliases=(),
        real_concurrent_locking=False,
        utc_clock=_sqlite_utc_clock,
    ),
    DialectSpec(
        key="mysql",
        label="MySQL / MariaDB",
        aliases=("mariadb",),
        real_concurrent_locking=True,
        utc_clock=_mysql_utc_clock,
    ),
    DialectSpec(
        key="postgresql",
        label="PostgreSQL",
        aliases=("postgres",),
        real_concurrent_locking=True,
        utc_clock=_postgresql_utc_clock,
        boolean_literals=("true", "false"),
    ),
)

DIALECT_SPECS: ProviderRegistry[None, DialectSpec] = ProviderRegistry("sql dialect")
_ALIASES: dict[str, str] = {}


def register_dialect_spec(spec: DialectSpec, *, replace: bool = False) -> None:
    """Register one dialect under its canonical key plus each alias it reports."""
    try:
        # Prove the clock authority is constructible now, rather than at the first
        # lease decision taken on a half-declared engine.
        if spec.utc_clock() is None:
            raise ValueError("utc_clock returned nothing")
    except Exception as exc:  # noqa: BLE001 - reported as a registration failure
        raise DialectUnsupported(
            f"dialect {spec.key!r} has no usable UTC clock authority: {exc}"
        ) from exc
    DIALECT_SPECS.register(spec.key, lambda _config, _s=spec: _s, replace=replace)
    for alias in spec.aliases:
        _ALIASES[alias.strip().casefold()] = spec.key


def resolve_dialect(name: str) -> DialectSpec:
    """Map any spelling SQLAlchemy or a caller reports onto one declared spec."""
    raw = str(name or "").strip().casefold()
    if not raw:
        raise DialectUnsupported("dialect name is empty")
    key = _ALIASES.get(raw, raw)
    if key not in DIALECT_SPECS.names():
        supported = ", ".join(sorted(DIALECT_SPECS.names()))
        raise DialectUnsupported(
            f"no SQL dialect spec for {name!r}; declared dialects: {supported}. "
            "Declare a DialectSpec instead of letting an undeclared engine inherit one."
        )
    spec = DIALECT_SPECS.create(key, None)
    if spec is None:  # pragma: no cover - guarded above, kept so lookup stays total
        raise DialectUnsupported(f"dialect spec {key!r} resolved to nothing")
    return spec


def dialect_utc_clock(name: str) -> ColumnElement[Any]:
    return resolve_dialect(name).utc_clock()


def has_real_concurrent_locking(name: str) -> bool:
    return resolve_dialect(name).real_concurrent_locking


def _register_builtins() -> None:
    for spec in BUILTIN_DIALECT_SPECS:
        register_dialect_spec(spec, replace=True)


_register_builtins()
