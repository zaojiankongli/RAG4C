"""Manage the RAG4C relational catalog schema without exposing credentials."""
from __future__ import annotations

import argparse

from config.settings import get_settings
from core.catalog_schema import (
    inspect_catalog_schema,
    safe_database_label,
    stamp_existing_catalog,
    upgrade_catalog,
)
from sqlalchemy import create_engine


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "upgrade", "stamp-existing"))
    parser.add_argument("--revision", default="head")
    args = parser.parse_args()
    settings = get_settings().catalog
    database_url = settings.db_url
    if not database_url:
        from core.catalog import _resolve_db_url

        database_url, _ = _resolve_db_url()
    label = safe_database_label(database_url)
    if args.command == "upgrade":
        upgrade_catalog(database_url, args.revision)
        print(f"catalog upgraded: {label} -> {args.revision}")
        return 0
    if args.command == "stamp-existing":
        stamp_existing_catalog(database_url)
        print(f"catalog stamped: {label}")
        return 0
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        state = inspect_catalog_schema(engine)
    finally:
        engine.dispose()
    print(
        f"catalog status: database={label} status={state.status} "
        f"revision={state.revision or '-'} head={state.head_revision}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
