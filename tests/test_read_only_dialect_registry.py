"""Axis #15 guards: proving a catalog engine is read-only is a per-dialect declaration.

``server/enterprise_readiness_api.py`` used to hold the whole answer in two places: a
hand-written ``backend -> mechanism`` dict inside ``prove_read_only_engine`` plus two
``if backend == ...`` ladders (one for the live probe statement, one for the driver
kwargs). Adding a dialect meant editing the file whose job is to decide whether a
production deployment may be claimed read-only, and the dangerous mistake there is
silent: a dialect missing from the dict yields ``expected_mechanism = None``, which the
old code compared with ``!=`` (fail closed) but a careless edit turns into a pass.

The table is ``core/read_only_dialects.py``. These tests pin three things:
the zero-branch criterion **behaviourally** (a registered dialect is honoured by the
host without the host changing, and an unregistered one is still refused), the built-in
spellings (a rename would change what readiness claims about a live deployment), and
registration-time shape death.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import server.enterprise_readiness_api as api
from core.read_only_dialects import (
    BUILTIN_READ_ONLY_DIALECT_SPECS,
    ReadOnlyDialectSpec,
    read_only_dialect_names,
    register_read_only_dialect,
    resolve_read_only_dialect,
    unregister_read_only_dialect,
)

PROBE_DIALECT = "probekind"
PROBE_STATEMENT = "SELECT @@session.probe_read_only"


def _probe_spec(**overrides: Any) -> ReadOnlyDialectSpec:
    fields: dict[str, Any] = {
        "dialect": PROBE_DIALECT,
        "mechanism": "probekind-session-readonly",
        "connect_args": {"init_command": "SET PROBE READ ONLY"},
        "proof_statements": (PROBE_STATEMENT,),
        "accepted_values": frozenset({"yes", "1"}),
    }
    fields.update(overrides)
    return ReadOnlyDialectSpec(**fields)


class _FakeConnection:
    def __init__(self, readings: dict[str, Any]) -> None:
        self._readings = readings
        self.seen: list[str] = []

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *_exc: Any) -> bool:
        return False

    def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        self.seen.append(sql)
        value = self._readings[sql]
        if isinstance(value, Exception):
            raise value
        return value


class _FakeEngine:
    """Just enough of an Engine for the proof: a dialect name, a connect(), a marker."""

    def __init__(self, dialect: Any, readings: dict[str, Any] | None = None) -> None:
        self.dialect = SimpleNamespace(name=dialect)
        self.connection = _FakeConnection(readings or {})

    def connect(self) -> _FakeConnection:
        return self.connection


def _proved(dialect: str, mechanism: str) -> _FakeEngine:
    engine = _FakeEngine(dialect)
    engine_connection = engine.connect()
    engine.connect = lambda: engine_connection  # type: ignore[method-assign]
    setattr(
        engine,
        api._READ_ONLY_PROOF_ATTRIBUTE,  # noqa: SLF001
        api.ReadOnlyEngineProof(dialect=dialect, mechanism=mechanism),
    )
    return engine


def _host_source() -> Path:
    return Path(inspect.getsourcefile(api) or "")


# --------------------------------------------------------------------------- #
# 判据：注册一种方言，宿主零改动且**实时**认它
# --------------------------------------------------------------------------- #


def test_a_new_dialect_is_honoured_live_without_editing_the_readiness_api() -> None:
    host = _host_source()
    before = host.read_bytes()
    register_read_only_dialect(_probe_spec())
    try:
        assert resolve_read_only_dialect(PROBE_DIALECT) is not None

        # 机制对得上 + 服务端回答 yes → 认定只读。这条走的是宿主的查表与实探测路径。
        engine = _proved(PROBE_DIALECT, "probekind-session-readonly")
        engine.connection._readings[PROBE_STATEMENT] = "YES "  # noqa: SLF001
        assert api.prove_read_only_engine(engine) is True
        assert engine.connection.seen == [PROBE_STATEMENT]  # noqa: SLF001

        # 机制写错一律不认，哪怕这条方言已经注册。（原来这条用的是没备答案的假连接，
        # 删掉宿主里那句机制比对它照样 False —— 判的是 KeyError，不是栅栏。下面
        # test_a_proof_is_refused_... 把每一项单独拆开、其余全部答对。）
        guess = _proved(PROBE_DIALECT, "guess")
        guess.connection._readings[PROBE_STATEMENT] = "YES"  # noqa: SLF001
        assert api.prove_read_only_engine(guess) is False

        # 服务端答 no 就是不认 —— 声明只决定"怎么问"，不决定"答案是什么"。
        unanswered = _proved(PROBE_DIALECT, "probekind-session-readonly")
        unanswered.connection._readings[PROBE_STATEMENT] = "no"  # noqa: SLF001
        assert api.prove_read_only_engine(unanswered) is False

        # 同一张表也喂"怎么开"这一半：connect_args 来自声明，不是宿主的 if。
        options, proof = api._non_sqlite_read_only_engine_options(  # noqa: SLF001
            SimpleNamespace(get_backend_name=lambda: PROBE_DIALECT)
        )
        assert options["connect_args"] == {"init_command": "SET PROBE READ ONLY"}
        assert proof.mechanism == "probekind-session-readonly"
        assert proof.guaranteed is True
    finally:
        unregister_read_only_dialect(PROBE_DIALECT)

    assert host.read_bytes() == before, "宿主又自己长出了一份方言清单"
    assert resolve_read_only_dialect(PROBE_DIALECT) is None
    assert api.prove_read_only_engine(_proved(PROBE_DIALECT, "probekind-session-readonly")) is False


def _proved_with(
    mechanism: str,
    *,
    guaranteed: bool = True,
    proof_dialect: str = PROBE_DIALECT,
    engine_dialect: str = PROBE_DIALECT,
) -> Any:
    """一台"其它一切都对"的引擎：实探测照答 yes，只把要判的那一项换掉。"""
    engine = _FakeEngine(engine_dialect, {PROBE_STATEMENT: "YES"})
    connection = engine.connect()
    engine.connect = lambda: connection  # type: ignore[method-assign]
    setattr(
        engine,
        api._READ_ONLY_PROOF_ATTRIBUTE,  # noqa: SLF001
        api.ReadOnlyEngineProof(
            dialect=proof_dialect, mechanism=mechanism, guaranteed=guaranteed
        ),
    )
    return engine


def test_each_condition_of_the_proof_is_alone_enough_to_refuse_it() -> None:
    """评审发现 R1/R4：删掉宿主里 ``proof.mechanism != spec.mechanism`` 或
    ``proof.guaranteed is not True`` 这两句，16 条守卫全绿 —— 因为原来那条"机制写错"的
    断言用的是没备答案的假连接，KeyError 先让它 False，判的不是栅栏。这里一次只破坏一项，
    服务端始终答 yes，所以只有那一项能让它翻。
    """
    register_read_only_dialect(_probe_spec())
    try:
        assert api.prove_read_only_engine(_proved_with("probekind-session-readonly")) is True
        assert api.prove_read_only_engine(_proved_with("probekind-session-readonly", guaranteed=False)) is False, (
            "guaranteed=False 却仍被认定只读"
        )
        assert api.prove_read_only_engine(_proved_with("some-other-mechanism")) is False, (
            "机制标签不符却仍被认定只读"
        )
        assert api.prove_read_only_engine(
            _proved_with("probekind-session-readonly", proof_dialect="mysql")
        ) is False, "凭证上的方言与引擎实际方言不符"
    finally:
        unregister_read_only_dialect(PROBE_DIALECT)


def test_an_unregistered_dialect_fails_closed_even_with_a_plausible_proof() -> None:    # 未注册的方言带着格式完全正确的凭证也必须判 False：这里一旦松手，readiness 就会把
    # "我们没法证明" 说成 "我们证明了"。
    assert api.prove_read_only_engine(_proved("oracle", "oracle-session-readonly")) is False
    assert api.prove_read_only_engine(_proved("probekind", "x")) is False
    assert api.prove_read_only_engine(_FakeEngine(None)) is False
    assert api.prove_read_only_engine(_FakeEngine(7)) is False

    options, proof = api._non_sqlite_read_only_engine_options(  # noqa: SLF001
        SimpleNamespace(get_backend_name=lambda: "oracle")
    )
    assert proof.guaranteed is False
    assert proof.mechanism == "unsupported"
    assert "connect_args" not in options


# --------------------------------------------------------------------------- #
# 内建声明的字面值：改一个拼写就会改变对现网的说辞
# --------------------------------------------------------------------------- #


def test_the_builtin_spellings_are_exactly_what_readiness_claims() -> None:
    by_name = {spec.dialect: spec for spec in BUILTIN_READ_ONLY_DIALECT_SPECS}
    assert set(by_name) == {"sqlite", "postgresql", "mysql", "mariadb"}
    assert set(read_only_dialect_names()) == set(by_name)

    assert by_name["sqlite"].mechanism == "sqlite-uri-mode-ro"
    assert by_name["sqlite"].live_probe_required is False
    assert by_name["sqlite"].connect_args == {}

    assert by_name["postgresql"].mechanism == "postgresql-default-transaction-read-only"
    assert by_name["postgresql"].connect_args == {
        "options": "-c default_transaction_read_only=on"
    }
    assert by_name["postgresql"].proof_statements == ("SHOW transaction_read_only",)

    # MySQL 把变量改过名：现代名在前、遗留名在后，顺序本身就是契约。
    assert by_name["mysql"].proof_statements == (
        "SELECT @@session.transaction_read_only",
        "SELECT @@session.tx_read_only",
    )
    assert by_name["mysql"].connect_args == {
        "init_command": "SET SESSION TRANSACTION READ ONLY"
    }
    mariadb, mysql = by_name["mariadb"], by_name["mysql"]
    assert mariadb.mechanism == mysql.mechanism
    assert mariadb.connect_args == mysql.connect_args
    assert mariadb.proof_statements == mysql.proof_statements


def test_the_live_probe_falls_back_to_the_legacy_mysql_statement_in_order() -> None:
    engine = _proved("mysql", "mysql-session-transaction-read-only")
    readings = engine.connection._readings  # noqa: SLF001
    readings["SELECT @@session.transaction_read_only"] = RuntimeError("renamed away")
    readings["SELECT @@session.tx_read_only"] = 1
    assert api.prove_read_only_engine(engine) is True
    assert engine.connection.seen == [  # noqa: SLF001
        "SELECT @@session.transaction_read_only",
        "SELECT @@session.tx_read_only",
    ]

    both_broken = _proved("mysql", "mysql-session-transaction-read-only")
    both_broken.connection._readings["SELECT @@session.transaction_read_only"] = RuntimeError()  # noqa: SLF001
    both_broken.connection._readings["SELECT @@session.tx_read_only"] = RuntimeError()  # noqa: SLF001
    assert api.prove_read_only_engine(both_broken) is False


# --------------------------------------------------------------------------- #
# 注册期把形状判死，而不是等第一次查表
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"dialect": "  "}, "空方言名"),
        ({"dialect": "ProbeKind"}, "非规范拼写当键"),
        ({"dialect": " probekind "}, "带空格的键"),
        ({"mechanism": "  "}, "空机制"),
        ({"proof_statements": ()}, "要实探测却没有探测语句"),
        ({"live_probe_required": False}, "不实探测却留着探测语句"),
        ({"accepted_values": frozenset()}, "没有可读作只读的值"),
        ({"connect_args": {"": "x"}}, "空 connect_args 键名"),
        ({"connect_args": {}, "live_probe_required": False, "proof_statements": ()},
         "既无驱动机制、又不实探测、也没声称靠 URL"),
        ({"proved_by_url": True}, "声称靠 URL 却同时留着驱动机制/实探测"),
    ],
)
def test_a_bad_declaration_dies_at_registration(overrides: dict[str, Any], reason: str) -> None:
    before = set(read_only_dialect_names())
    with pytest.raises((ValueError, TypeError)):
        register_read_only_dialect(_probe_spec(**overrides))
    assert set(read_only_dialect_names()) == before, f"{reason}：拒绝之后不该留下半条注册"


def test_only_a_real_declaration_can_be_registered() -> None:
    """形状守卫的另一半：不是 spec 的东西在注册期就死，而不是第一次查表时炸。

    少了这条，``_validate_spec`` 里那句 isinstance 检查被删掉也不会有任何用例变红 ——
    我在这一轮的变异跑里就是这么发现的（M3 逃逸）。
    """
    before = set(read_only_dialect_names())
    junk: list[Any] = [
        {"dialect": "junk", "mechanism": "m"},
        SimpleNamespace(dialect="junk", mechanism="m"),
        "junk",
        None,
    ]
    for value in junk:
        with pytest.raises(TypeError):
            register_read_only_dialect(value)  # type: ignore[arg-type]
    assert set(read_only_dialect_names()) == before


def test_replacing_an_existing_declaration_is_explicit() -> None:
    register_read_only_dialect(_probe_spec())
    try:
        with pytest.raises(ValueError):
            register_read_only_dialect(_probe_spec(mechanism="second"))
        register_read_only_dialect(_probe_spec(mechanism="second"), replace=True)
        assert resolve_read_only_dialect(PROBE_DIALECT) is not None
        assert resolve_read_only_dialect(PROBE_DIALECT).mechanism == "second"
    finally:
        unregister_read_only_dialect(PROBE_DIALECT)
    assert resolve_read_only_dialect(PROBE_DIALECT) is None


def test_withdrawal_works_for_any_spelling_of_the_key() -> None:
    """一张存储 + 归一后再弹：不给"两边不同步"留位置（轴 #2 上真翻过一次）。"""
    register_read_only_dialect(_probe_spec())
    try:
        unregister_read_only_dialect(f"  {PROBE_DIALECT.upper()} ")
        assert resolve_read_only_dialect(PROBE_DIALECT) is None
        assert PROBE_DIALECT not in read_only_dialect_names()
    finally:
        unregister_read_only_dialect(PROBE_DIALECT)


def test_the_host_holds_no_copy_of_the_dialect_lists() -> None:
    """宿主守卫：那两份手写的方言清单不许长回来。"""
    source = inspect.getsource(api.prove_read_only_engine)
    for dead in ("sqlite-uri-mode-ro", "postgresql-default-transaction-read-only", "tx_read_only"):
        assert dead not in source, f"prove_read_only_engine 又抄了一份字面量（{dead}）"
    assert "resolve_read_only_dialect" in source
    assert "expected_mechanism" not in source
