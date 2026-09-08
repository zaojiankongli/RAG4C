"""Export a content-free catalog rollback/preflight snapshot."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Engine, inspect, text

from core.catalog_schema import inspect_catalog_schema, safe_database_label


def export_catalog_snapshot(engine: Engine, output_path: str | Path) -> Path:
    output = Path(output_path)
    inspector = inspect(engine)
    state = inspect_catalog_schema(engine)
    tables: dict[str, dict[str, object]] = {}
    with engine.connect() as connection:
        for table in sorted(inspector.get_table_names()):
            if table == "alembic_version":
                continue
            row_count = int(
                connection.execute(text(f"SELECT COUNT(*) FROM {engine.dialect.identifier_preparer.quote(table)}")).scalar_one()
            )
            tables[table] = {
                "row_count": row_count,
                "columns": [column["name"] for column in inspector.get_columns(table)],
                "unique_constraints": [
                    {
                        "name": item.get("name"),
                        "columns": item.get("column_names") or [],
                    }
                    for item in inspector.get_unique_constraints(table)
                ],
            }
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": safe_database_label(str(engine.url)),
        "schema": {
            "status": state.status,
            "revision": state.revision,
            "head_revision": state.head_revision,
        },
        "tables": tables,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    from sqlalchemy import create_engine
    from config.settings import get_settings

    engine = create_engine(get_settings().catalog.db_url, pool_pre_ping=True)
    try:
        path = export_catalog_snapshot(engine, args.output)
    finally:
        engine.dispose()
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
