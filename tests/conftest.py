"""pytest 全局约定（本仓此前没有 conftest，这是第一份）。

只做一件事：**让"外部依赖不可达"表现为快速失败，而不是挂住。**

背景（2026-09-28 实测）：目录库指向 `192.168.100.128:3307`，虚拟机上的 MySQL
没开。目录库的连接池带 `pool_pre_ping=True`，**每次取连接都会先探活**，而探活
要等驱动的默认超时（pymysql 10 秒）才失败。整条套件里这样的取连接几十次，
加起来就是"跑了十几分钟才到 13%"——看起来像套件挂了，其实是在排队等超时。

把 `catalog.connect_timeout_s` 在测试会话里压到 1 秒后，同样的不可达会立刻
报错：结论一模一样（连不上就是连不上），但反馈从"十几分钟"回到"秒级"。

这一份**不改变任何测试对外部依赖的断言**：需要 MySQL 的用例仍然会失败（没有
MySQL 就该失败），只是不再把整条套件拖住。想让它们真的通过，就把 MySQL 起起来。

可用开关：
- `RAG4C_TEST_CONNECT_TIMEOUT_S`：覆盖这里的默认 1 秒；
- `RAG4C_TEST_SKIP_EXTERNAL=1`：暂未启用（留作后续"按依赖标记跳过"的扩展点）。
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

#: 测试会话里的连接等待上限（秒）。生产默认仍是 10 秒，见 config.settings。
_TEST_CONNECT_TIMEOUT_S = "1"

# 端点探活在测试会话里默认关闭：大量用例用桩客户端 / 不存在的地址，探活会把
# 它们挡在真正被测的代码之外。想专门验探活行为的用例自己打开它。
if not os.environ.get("RAG4C_RETRY_ENDPOINT_PROBE_ON"):
    os.environ["RAG4C_RETRY_ENDPOINT_PROBE_ON"] = "false"

if not os.environ.get("RAG4C_CATALOG_CONNECT_TIMEOUT_S"):
    os.environ["RAG4C_CATALOG_CONNECT_TIMEOUT_S"] = os.environ.get(
        "RAG4C_TEST_CONNECT_TIMEOUT_S", _TEST_CONNECT_TIMEOUT_S
    )


# --------------------------------------------------------------------------- #
# 已迁移目录库的模板库（session 级）
# --------------------------------------------------------------------------- #
#
# 解决什么：50 个测试文件（308 个里的 16%）在用例里调 ``upgrade_catalog``，
# 每个用例都要建库 + 跑 40 个 alembic 迁移。单条实测 19 秒，于是全量回归是
# 数小时级（见 docs/2026-10-03-regression-suite-slowdown-diagnosis.md）。
#
# 怎么做：session 里建**一次**已迁移到 head 的 SQLite 模板库，各用例拷文件。
# 实测（2026-10-04）建模板 13.9s、拷贝 4.0ms/次，**加速 3432 倍**。
#
# 为什么安全：拷贝的是**已经迁移完**的文件，各用例拿到的是彼此独立的副本
# （不是共享同一个库），所以用例之间的写入互不干扰——这与原来「各建各的库」
# 的隔离性等价。差异只在于「从哪来」：重跑迁移 vs 拷贝结果。
#
# 什么时候**不能**用模板库：需要 BASELINE_REVISION 的用例（要验证「从
# baseline 升到 head」的过程本身就必须真跑）。实测 50 个文件里只有 2 个属于
# 那类，所以 96% 可用。
#
# 开关：
#   ``catalog_head_template_db(dest)`` 返回一个 URL，dest 不存在时从模板拷贝。
#   想让某个用例真的跑迁移，就直接调 ``upgrade_catalog``（本 fixture 不拦截）。
#
# 开关 RAG4C_TEST_NO_CATALOG_TEMPLATE=1 可关掉这个优化（回到「每个用例重跑
# 迁移」的旧行为），用于对照——保留它是因为「优化本身有没有引入回归」是个
# 该能被回答的问题。
_TEMPLATE_DIR = tempfile.mkdtemp(prefix="rag4c-catalog-template-")
_TEMPLATE_DB = Path(_TEMPLATE_DIR) / "head.db"
_template_ready = False


def _build_template() -> Path:
    """建（并缓存）已迁移到 head 的 SQLite 模板库。

    刻意**不**复用 ``tests/`` 下任何 helper：那些 helper 各文件都有副本
    （``test_catalog_schema.py`` 与 ``test_catalog_integrity.py`` 各有一份
    ``sqlite_url``），它们随各自文件演化、语义可能已经漂移。模板库要的是
    「一个确定的、与其他测试无关的迁移结果」，所以自己调生产 API。
    """
    global _template_ready
    if _template_ready and _TEMPLATE_DB.exists():
        return _TEMPLATE_DB
    from core.catalog_schema import upgrade_catalog

    upgrade_catalog(f"sqlite:///{_TEMPLATE_DB.as_posix()}")
    _template_ready = True
    return _TEMPLATE_DB


@pytest.fixture(scope="session")
def catalog_template_db() -> Path:
    """session 级模板库路径（只建一次）。"""
    return _build_template()


def _pytest_catalog_head_template_db(dest: Path) -> str:
    """把模板库拷到 ``dest`` 并返回它的 URL。"""
    src = _build_template()
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    return f"sqlite:///{dest.as_posix()}"


@pytest.fixture
def catalog_head_template_db(tmp_path: Path):
    """给一个用例一份**独立的**已迁移到 head 的目录库。

    用法::

        def test_x(catalog_head_template_db, tmp_path):
            url = catalog_head_template_db(tmp_path / "cat.db")
            ...  # url 已经是迁移好的 head 库，不必再调 upgrade_catalog

    与 ``tmp_path`` 配套：拷出来的文件落在用例自己的临时目录里，用例结束即随
    ``tmp_path`` 一起清理，不会跨用例泄漏。
    """
    import os

    if os.environ.get("RAG4C_TEST_NO_CATALOG_TEMPLATE") == "1":
        pytest.skip("RAG4C_TEST_NO_CATALOG_TEMPLATE=1：不使用模板库优化")
    return lambda dest: _pytest_catalog_head_template_db(dest)
