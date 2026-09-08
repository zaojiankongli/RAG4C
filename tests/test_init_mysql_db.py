from __future__ import annotations

import importlib

import pytest


def init_module():
    return importlib.import_module("scripts.init_mysql_db")


def test_catalog_bootstrap_requires_explicit_database_url() -> None:
    module = init_module()

    with pytest.raises(RuntimeError, match="RAG4C_CATALOG_DB_URL must be set"):
        module.resolve_catalog_url({})


def test_catalog_bootstrap_rejects_non_mysql_url() -> None:
    module = init_module()

    with pytest.raises(RuntimeError, match="mysql or mariadb"):
        module.resolve_catalog_url(
            {"RAG4C_CATALOG_DB_URL": "sqlite:///data/rag4c.db"}
        )


def test_catalog_bootstrap_accepts_explicit_mysql_url() -> None:
    module = init_module()
    url = "mysql+pymysql://user:secret@db.internal:3306/rag4c?charset=utf8mb4"

    assert module.resolve_catalog_url({"RAG4C_CATALOG_DB_URL": url}) == url
