"""Dialect-aware database UTC wall-clock authority for lease decisions."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from core.dialects import dialect_utc_clock


def db_utc_expression(dialect_name: str) -> ColumnElement[Any]:
    """Resolve the engine's UTC clock from the dialect spec table.

    An undeclared engine raises rather than falling back to ``CURRENT_TIMESTAMP``.
    This expression is the wall-clock authority for lease expiry, so a silently
    different clock is exactly how a lease goes wrong in production.
    """
    return dialect_utc_clock(dialect_name)


def read_db_utc(session: Session) -> datetime:
    dialect = session.bind.dialect.name if session.bind is not None else ""
    value = session.scalar(select(db_utc_expression(dialect).label("db_utc_now")))
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    raise RuntimeError("database UTC clock authority is unavailable")
