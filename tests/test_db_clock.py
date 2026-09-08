from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects import mysql, postgresql, sqlite

from core.db_clock import db_utc_expression


def test_db_utc_expression_is_dialect_aware_and_non_transaction_start() -> None:
    mysql_sql = str(select(db_utc_expression("mysql")).compile(dialect=mysql.dialect()))
    postgres_sql = str(
        select(db_utc_expression("postgresql")).compile(dialect=postgresql.dialect())
    )
    sqlite_sql = str(select(db_utc_expression("sqlite")).compile(dialect=sqlite.dialect()))
    assert "utc_timestamp" in mysql_sql.casefold()
    assert "clock_timestamp" in postgres_sql.casefold()
    assert "current_timestamp" not in postgres_sql.casefold()
    assert "strftime" in sqlite_sql.casefold()



def test_postgresql_clock_expression_can_be_labeled_and_read_without_not_implemented() -> None:
    from datetime import datetime
    from types import SimpleNamespace

    from core.db_clock import read_db_utc

    class FakeSession:
        bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        @staticmethod
        def scalar(statement):
            sql = str(statement.compile(dialect=postgresql.dialect()))
            assert "clock_timestamp" in sql.casefold()
            assert "AT TIME ZONE 'UTC'" in sql
            assert "db_utc_now" in sql
            return datetime(2026, 8, 25, 12, 0, 0, 123456)

    assert read_db_utc(FakeSession()) == datetime(2026, 8, 25, 12, 0, 0, 123456)
