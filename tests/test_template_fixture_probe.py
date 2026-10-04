"""验证 catalog_head_template_db fixture（路线 B 的核心机制）。"""
from __future__ import annotations

import time
from pathlib import Path

from sqlalchemy import create_engine, inspect, text


def test_template_gives_a_migrated_head_db(catalog_head_template_db, tmp_path: Path) -> None:
    url = catalog_head_template_db(tmp_path / "cat.db")
    eng = create_engine(url)
    try:
        tables = set(inspect(eng).get_table_names())
        with eng.connect() as c:
            rev = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        eng.dispose()
    assert "accounts" in tables and "alembic_version" in tables
    assert rev and rev.startswith("00"), rev


def test_copies_are_isolated_from_each_other(catalog_head_template_db, tmp_path: Path) -> None:
    """两份拷贝互不影响——这与「各建各的库」的隔离性等价，是安全的依据。"""
    a = catalog_head_template_db(tmp_path / "a.db")
    b = catalog_head_template_db(tmp_path / "b.db")
    assert a != b
    ea, eb = create_engine(a), create_engine(b)
    try:
        with ea.begin() as c:
            c.execute(text("INSERT INTO accounts (id,name,email,created_at) "
                           "VALUES ('x','n','e','2026-01-01')"))
        with eb.connect() as c:
            n = c.execute(text("SELECT count(*) FROM accounts")).scalar()
        assert n == 0, "写进 a 的数据不该出现在 b"
    finally:
        ea.dispose(); eb.dispose()


def test_second_copy_is_fast(catalog_head_template_db, tmp_path: Path) -> None:
    """第二次拷贝必须是毫秒级（第一次含建模板的 14s）。"""
    catalog_head_template_db(tmp_path / "warm.db")
    t0 = time.perf_counter()
    catalog_head_template_db(tmp_path / "t.db")
    assert (time.perf_counter() - t0) < 0.5
