"""方言层的守卫测试（含 PostgreSQL）。

## 为什么要有这个文件

目录库刚切到 PostgreSQL 17.8（2026-10-04），切换过程中发现两件事：

1. **方言层没有专门测试**。`core/dialects.py` 声明了三个方言
   （sqlite / mysql / postgresql），但没有测试断言「PG 注册了什么、
   跟 MySQL 差在哪」。而 `config/.env.postgres` 里那句「切换引擎不再依赖
   58 处手写法」正是在说这件事——**它值得被测，而不是只写在注释里**。
2. **`tests/test_catalog_schema.py` 测的是 MySQL 方言**（它
   `from sqlalchemy.dialects import mysql`）。那 62 条在 PG 环境下跑，
   验证的是 MySQL 建表路径，**不等于验证 PG**。

所以本文件守三件事：

- PG / MySQL / SQLite 三个方言都注册了，别把任何一个删掉；
- PG 特有的能力被正确声明（``boolean_literals``、``postgres`` 别名）——
  这些是切库时会踩的地方；
- 三个方言的**能力声明真的生效**，不是只挂在 ``DialectSpec`` 上没人读。

最后一条最关键：一个注册表若没有读者，它和死代码没区别。
"""
from __future__ import annotations

from core.dialects import (
    BUILTIN_DIALECT_SPECS,
    DialectUnsupported,
    register_dialect_spec,
    resolve_dialect,
)

_SPECS = {spec.key: spec for spec in BUILTIN_DIALECT_SPECS}


def test_all_three_dialects_are_registered() -> None:
    """sqlite / mysql / postgresql 三个都在。

    少任何一个都会让某条部署路径**静默失效**（而不是启动就报错）——
    方言解析失败通常发生在真正执行到那条语句时。
    """
    assert set(_SPECS) == {"sqlite", "mysql", "postgresql"}


def test_boolean_literals_differ_between_postgresql_and_mysql() -> None:
    """PG 与 MySQL 的 BOOLEAN 字面量不同，且 PG 显式覆盖了默认值。

    MySQL 的 ``BOOLEAN`` 就是 ``TINYINT(1)``、存 1/0；PG 存 true/false。
    ``DialectSpec.boolean_literals`` 的**默认值是 ("1","0")**，PG 显式改成
    ("true","false")——若这行被删掉，``WHERE flag = 1`` 这类按 MySQL 假设写的
    SQL 在 PG 上会类型不匹配或静默返回空集，而且只在切库后出现。
    """
    assert _SPECS["postgresql"].boolean_literals == ("true", "false")
    # MySQL 保持默认（1/0），不覆盖
    assert _SPECS["mysql"].boolean_literals == ("1", "0")
    assert _SPECS["sqlite"].boolean_literals == ("1", "0")


def test_postgres_is_an_alias_of_postgresql() -> None:
    """``postgres`` 必须是 ``postgresql`` 的别名。

    部署方写 ``postgresql://`` 还是 ``postgres://`` 是随手的事——URL 里
    少写两个字母就让方言解析失败，排查起来要翻配置才知道。
    """
    assert "postgres" in _SPECS["postgresql"].aliases
    assert "mariadb" in _SPECS["mysql"].aliases


def test_only_server_backends_claim_concurrent_locking() -> None:
    """只有 MySQL / PG 声称并发锁；SQLite 不该声称。

    SQLite 的并发写是真会 ``database is locked`` 的。给它挂上
    ``real_concurrent_locking=True`` 会让上层以为可以放心并发写——
    而那正是 SQLite 唯一不能做的事。
    """
    assert _SPECS["sqlite"].real_concurrent_locking is False
    assert _SPECS["mysql"].real_concurrent_locking is True
    assert _SPECS["postgresql"].real_concurrent_locking is True


def test_every_spec_has_a_utc_clock() -> None:
    """三个方言都必须提供 UTC 时钟。

    时间戳语义错乱是那种「测试全绿、上线后按时间查数据查错」的问题——
    有 utc_clock 且都有实现，缺一个就在真正用到时才炸。
    """
    for key, spec in _SPECS.items():
        assert callable(spec.utc_clock), f"{key} 缺 utc_clock"
        assert spec.utc_clock() is not None, f"{key} 的 utc_clock 返回 None"


def test_resolve_dialect_handles_key_alias_and_case() -> None:
    """``resolve_dialect`` 能按 key、按别名、按大小写解析。

    这一条守的是「注册表真的被读过」——若没人调 ``resolve_dialect``，
    前面那些声明就只是数据，删掉也不会有人发现。

    部署方写 ``postgresql://`` 还是 ``postgres://``、大小写如何，是随手的事；
    URL 里少写两个字母让方言解析失败，排查要翻配置才知道。
    """
    assert resolve_dialect("postgresql").key == "postgresql"
    assert resolve_dialect("postgres").key == "postgresql", "别名应解析到同一个 spec"
    assert resolve_dialect("PostgreSQL").key == "postgresql", "应大小写不敏感"
    assert resolve_dialect("mysql").key == "mysql"
    assert resolve_dialect("mariadb").key == "mysql"
    assert resolve_dialect("sqlite").key == "sqlite"


def test_registering_a_dialect_without_replace_is_rejected() -> None:
    """同 key 重复注册必须**报错**，不能静默替换。

    覆盖能力是必需的（部署方要给某个方言加特例），但它也是危险能力：不显式
    要求 ``replace=True`` 就该拒绝，否则后注册的会静默盖掉前者——
    排查时看到的是「配置写着 A、跑起来是 B」，极难定位。
    """
    import pytest
    from dataclasses import replace

    base = _SPECS["postgresql"]
    patched = replace(base, label="PG 定制版")
    register_dialect_spec(patched, replace=True)
    try:
        assert resolve_dialect("postgresql").label == "PG 定制版"
        with pytest.raises(Exception):
            register_dialect_spec(replace(base, label="又一个"))
        assert resolve_dialect("postgresql").label == "PG 定制版", (
            "失败的注册不该改动现状"
        )
    finally:
        # 还原：注册表是模块级全局，测试之间会互相影响
        register_dialect_spec(base, replace=True)
    assert resolve_dialect("postgresql").label == _SPECS["postgresql"].label


def test_dialect_without_usable_clock_cannot_be_registered() -> None:
    """utc_clock 不可用的方言**注册时就该被拒**。

    报错信息说的是「registration failure」——因为半声明的引擎会等到第一次
    租约决策（可能几小时后、某个租户请求时）才炸，那时排查成本高得多。
    """
    import pytest
    from dataclasses import replace

    bad = replace(_SPECS["sqlite"], key="bad_dialect", aliases=(), utc_clock=lambda: None)
    with pytest.raises(DialectUnsupported):
        register_dialect_spec(bad, replace=True)
