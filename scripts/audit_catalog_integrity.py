"""Report catalog identity duplicates without printing identity values."""
from __future__ import annotations

import json

from sqlalchemy import create_engine

from config.settings import get_settings
from core.catalog import _resolve_db_url
from core.catalog_integrity import audit_catalog_duplicates
from core.catalog_schema import safe_database_label


def main() -> int:
    settings = get_settings().catalog
    database_url = settings.db_url or _resolve_db_url()[0]
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        report = audit_catalog_duplicates(engine)
    finally:
        engine.dispose()
    print(f"catalog audit: database={safe_database_label(database_url)}")
    print(json.dumps(report.to_safe_dict(), ensure_ascii=False, indent=2))
    return 1 if report.unresolved_groups else 0


if __name__ == "__main__":
    raise SystemExit(main())
