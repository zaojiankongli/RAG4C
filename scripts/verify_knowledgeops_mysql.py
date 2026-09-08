"""Run the mandatory KnowledgeOps migration drill in a disposable MySQL DB.

The command requires TEST_MYSQL_URL for connection credentials, but never opens
the database named by that URL. It connects to MySQL's administrative database,
creates a uniquely named temporary database, performs upgrade/downgrade/upgrade,
and drops the temporary database in a finally block.
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from typing import Any

from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

_ENV_NAME = "TEST_MYSQL_URL"
_DATABASE_PREFIX = "rag4c_knowledgeops_drill_"


def _safe_database_name() -> str:
    return f"{_DATABASE_PREFIX}{uuid.uuid4().hex[:16]}"


def _validate_source_url(raw_url: str) -> Any:
    url = make_url(raw_url)
    if url.get_backend_name() != "mysql":
        raise ValueError("TEST_MYSQL_URL must use a MySQL SQLAlchemy dialect")
    if not url.host:
        raise ValueError("TEST_MYSQL_URL must include a host")
    return url


def _run_drill(raw_url: str) -> dict[str, Any]:
    from core import catalog_schema

    source_url = _validate_source_url(raw_url)
    database_name = _safe_database_name()
    if database_name.lower() == "rag4c" or not database_name.startswith(_DATABASE_PREFIX):
        raise RuntimeError("refusing unsafe MySQL drill database name")
    admin_url = source_url.set(database="mysql")
    drill_url = source_url.set(database=database_name)
    rendered_drill_url = drill_url.render_as_string(hide_password=False)
    admin_engine = create_engine(admin_url)
    created = False
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f"CREATE DATABASE `{database_name}` CHARACTER SET utf8mb4"))
        created = True
        catalog_schema.upgrade_catalog(rendered_drill_url)
        current_engine = create_engine(drill_url)
        try:
            first_state = catalog_schema.inspect_catalog_schema(current_engine)
            if first_state.status != "current":
                raise RuntimeError("MySQL drill did not reach current schema")
        finally:
            current_engine.dispose()

        config = catalog_schema._alembic_config(rendered_drill_url)
        command.downgrade(config, "0006_source_id")
        downgraded_engine = create_engine(drill_url)
        try:
            downgraded_state = catalog_schema.inspect_catalog_schema(downgraded_engine)
            if downgraded_state.status != "behind":
                raise RuntimeError("MySQL drill downgrade was not observed")
            if "chunk_heads" in inspect(downgraded_engine).get_table_names():
                raise RuntimeError("chunk authority tables survived downgrade")
        finally:
            downgraded_engine.dispose()

        command.upgrade(config, "head")
        upgraded_engine = create_engine(drill_url)
        try:
            final_state = catalog_schema.verify_catalog_schema(upgraded_engine)
            if "chunk_heads" not in inspect(upgraded_engine).get_table_names():
                raise RuntimeError("chunk authority tables missing after re-upgrade")
        finally:
            upgraded_engine.dispose()
        return {
            "status": "passed",
            "database_ref": database_name,
            "revision": final_state.revision,
            "head_revision": final_state.head_revision,
        }
    finally:
        if created:
            with admin_engine.begin() as connection:
                connection.execute(text(f"DROP DATABASE IF EXISTS `{database_name}`"))
        admin_engine.dispose()


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    raw_url = os.getenv(_ENV_NAME, "")
    if not raw_url.strip():
        print(
            json.dumps(
                {"status": "blocked", "error": f"{_ENV_NAME} is required"},
                sort_keys=True,
            )
        )
        return 2
    try:
        report = _run_drill(raw_url)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)[:512]}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
