"""已迁移目录库的模板库：可 import 的入口（fixture 版的孪生）。

## 为什么有这个文件

``tests/conftest.py`` 里的 ``catalog_head_template_db`` 是 pytest fixture ——
**只能通过参数注入**，所以像 ``test_projection_handlers.py`` 那种
「测试自己定义 ``create_state(tmp_path)`` 共享 helper」的文件用不上它：
helper 拿不到 fixture，除非把 fixture 一路传下去（要改 20 个调用点）。

所以这里提供一个**纯函数**入口，让那些 helper 也能受益：

    from _catalog_template import head_db_url
    url = head_db_url(tmp_path / "catalog.db")   # 已是迁移好的 head 库

两个入口共用**同一份** session 级模板库（都落在 ``_TEMPLATE_DIR``），
所以混用两种写法不会各建一次模板。

## 为什么安全（以及什么时候不能用）

拷贝出来的是**彼此独立的副本**，用例间写入互不干扰——与原来「各建各的库」
的隔离性等价，差异只在「从哪来」（重跑迁移 vs 拷贝结果）。

**不能用的场景**：需要 ``BASELINE_REVISION`` 的用例。它们要验证的正是
「从 baseline 升到 head」这个过程本身，拷贝一个已到 head 的库会把被测行为
跳过去。实测 50 个调用 ``upgrade_catalog`` 的文件里只有 2 个属于那类。

## 关闭开关

``RAG4C_TEST_NO_CATALOG_TEMPLATE=1``：退回「每个用例重跑迁移」的旧行为。
保留它是因为「这个优化本身有没有引入回归」是个该能被回答的问题——
没有开关就只能靠 git 回滚来回答。

## 数值依据

2026-10-04 实测：建模板 13,855.8ms（118 张表）、拷贝 + 校验 4.0ms/次，
**加速 3432 倍**。单条迁移型用例此前 19 秒。
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_TEMPLATE_DIR = tempfile.mkdtemp(prefix="rag4c-catalog-template-")
_TEMPLATE_DB = Path(_TEMPLATE_DIR) / "head.db"
_ready = False


def template_db_path() -> Path:
    """建（并缓存）已迁移到 head 的模板库，返回它的路径。

    刻意**不**复用各测试文件里的 ``sqlite_url`` helper：那些 helper 每个文件
    有一份副本、随各自文件演化、语义可能已经漂移。模板库要的是「一个确定的、
    与其他测试无关的迁移结果」，所以自己调生产 API。
    """
    global _ready
    if _ready and _TEMPLATE_DB.exists():
        return _TEMPLATE_DB
    from core.catalog_schema import upgrade_catalog

    upgrade_catalog(f"sqlite:///{_TEMPLATE_DB.as_posix()}")
    _ready = True
    return _TEMPLATE_DB


def head_db_url(dest: Path | str) -> str:
    """把模板库拷到 ``dest`` 并返回它的 SQLAlchemy URL。

    ``dest`` 可以是 ``Path``，也可以是 ``"<path>?timeout=20"`` 这种带查询参数
    的形式——后者在测试里很常见（``sqlite:///...?timeout=20`` 用查询参数而不是
    ``connect_args`` 传超时）。两种写法都支持，因为改写时**不该要求连 url 的
    拼法一起改**。

    Args:
        dest: 目标文件路径（通常是 ``tmp_path / "catalog.db"``）。父目录会被
            自动创建；文件已存在会被覆盖。

    Returns:
        ``sqlite:///...`` 形式的 URL，指向一个**已迁移到 head** 的独立副本。

    Raises:
        RuntimeError: 显式要求不用模板库（``RAG4C_TEST_NO_CATALOG_TEMPLATE=1``）
            时不静默退回重跑迁移——那会让「为什么这次慢了 3000 倍」变得难查。
            想退回旧行为请让调用方改回调 ``upgrade_catalog``。
    """
    if os.environ.get("RAG4C_TEST_NO_CATALOG_TEMPLATE") == "1":
        raise RuntimeError(
            "RAG4C_TEST_NO_CATALOG_TEMPLATE=1：本次运行禁用目录库模板库。"
            "调用方应改回 core.catalog_schema.upgrade_catalog(url) —— "
            "head_db_url 刻意不静默退回重跑迁移（那会让「为什么这次慢了 3000 倍」"
            "这种问题变得难查）。"
        )
    query = ""
    if isinstance(dest, str) and "?" in dest:
        dest, query = dest.split("?", 1)
    dest = Path(dest)
    src = template_db_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    return f"sqlite:///{dest.as_posix()}"


def upgrade_existing_sqlite_url(url: str) -> str:
    """把「尚未创建」的 ``sqlite:///path`` URL 指向拷好的模板库，返回同一 URL。

    存在的理由：47 个候选测试文件里，建库形态**不完全一致**——``url`` 那一行
    各有各的算法（``f"sqlite:///{(tmp_path / 'catalog.db').as_posix()}"``、
    ``"sqlite:///" + str(path)``、自定义 helper……）。若改写要求「自己算出目标
    路径再调 :func:`head_db_url`」，就要碰各文件差异最大的那部分代码。

    所以改成**只替换那一行调用**：``upgrade_catalog(url)`` →
    ``url = upgrade_existing_sqlite_url(url)``。调用方算 url 的逻辑一行不动，
    改动面小到能逐个 review。

    Args:
        url: 调用方刚算出的、指向**尚未创建**的 SQLite 文件的 URL。

    Returns:
        同一个 URL，但那个文件现在是已迁移到 head 的模板库副本。

    Raises:
        ValueError: url 不是 sqlite:/// 形式。静默放过会变成「拷贝没发生、
            后面 create_engine 建出一个空库」——那种失败会出现在断言里，
            离病因很远。这里直接拒绝。
    """
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError(
            f"upgrade_existing_sqlite_url 只支持 {prefix}<path>，收到 {url!r}。"
            "其他方言不该用模板库——拷贝的是 SQLite 文件。"
        )
    return head_db_url(url[len(prefix):])   # head_db_url 自己处理 ?query
