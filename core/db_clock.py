"""Dialect-aware database UTC wall-clock authority for lease decisions."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, func, literal_column, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement


def db_utc_expression(dialect_name: str) -> ColumnElement[Any]:
    dialect = str(dialect_name or "").casefold()
    if dialect in {"mysql", "mariadb"}:
        return func.utc_timestamp(6)
    if dialect == "postgresql":
        return literal_column(
            "(clock_timestamp() AT TIME ZONE 'UTC')", type_=DateTime()
        )
    if dialect == "sqlite":
        return func.strftime("%Y-%m-%d %H:%M:%f", "now")
    return func.current_timestamp()


def read_db_utc(session: Session) -> datetime:
    dialect = session.bind.dialect.name if session.bind is not None else ""
    value = session.scalar(select(db_utc_expression(dialect).label("db_utc_now")))
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    raise RuntimeError("database UTC clock authority is unavailable")


__all__ = ["db_utc_expression", "read_db_utc"]
