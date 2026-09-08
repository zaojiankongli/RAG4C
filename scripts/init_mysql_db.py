"""Explicit MySQL catalog bootstrap.

Usage::

    RAG4C_CATALOG_DB_URL=mysql+pymysql://USER:PASSWORD@HOST:3306/rag4c \
        python scripts/init_mysql_db.py

The URL is mandatory. This script never falls back to embedded credentials.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import unquote, urlsplit

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


def resolve_catalog_url(environ: Mapping[str, str]) -> str:
    url = str(environ.get("RAG4C_CATALOG_DB_URL") or "").strip()
    if not url:
        raise RuntimeError("RAG4C_CATALOG_DB_URL must be set explicitly")
    parts = urlsplit(url)
    backend = parts.scheme.split("+", 1)[0]
    if backend not in {"mysql", "mariadb"}:
        raise RuntimeError("catalog bootstrap URL must use mysql or mariadb")
    if not parts.hostname or not parts.path.lstrip("/"):
        raise RuntimeError("catalog bootstrap URL must include host and database name")
    return url


def main() -> int:
    try:
        url = resolve_catalog_url(os.environ)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    parts = urlsplit(url)
    db_name = unquote(parts.path.lstrip("/"))
    user = unquote(parts.username or "")
    password = unquote(parts.password or "")
    host = parts.hostname or ""
    port = parts.port or 3306

    import pymysql

    conn = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        charset="utf8mb4",
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS \u0060{db_name}\u0060 "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        conn.commit()
    finally:
        conn.close()
    print(f"[1/2] database {db_name!r} ready ({host}:{port})")

    from sqlalchemy import create_engine

    from core.catalog_schema import upgrade_catalog

    engine = create_engine(url, pool_pre_ping=True)
    try:
        engine.connect().close()
    finally:
        engine.dispose()
    upgrade_catalog(url)
    print("[2/2] catalog migrations upgraded to head")
    return 0


if __name__ == "__main__":
    sys.exit(main())
