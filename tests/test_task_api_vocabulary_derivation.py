"""任务 API 的请求侧词表与上限只由 `core.catalog_schema` 那一份声明决定。

普查清单原本记的是「两处 `max_length=7`，第 9 个值会被 422 静默挡掉」—— 这句话把形状读错了
（那两个 7 不是字符串宽度，是 **列表长度上限**），但读出来的病是真的，而且更难受：`SourceKind`
与 `TaskStatus` 各有 7 个成员，`max_length` 也写死 7，两边都是**碰巧相等**。加第 8 种来源后，
一个合法的「把所有来源都选上」的 reconcile 请求会在请求校验层被 422 掉，而核心侧的校验
（`catalog_schema.py:5516`）本来就是按 `len(词表)` 判的 —— 两层会各执一词，症状还不直观。

现在词表与上限都从同一份声明派生（`Literal[tuple(...)]`，沿用 `core/run_events.py:69` 的
`RagExecutor` 写法）。因为派生发生在**导入期**，下面的零改分支用例走 `importlib.reload`，
而不是假装改运行时元组就能生效。
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import get_args

import core.catalog_schema as catalog_schema
import server.enterprise_task_operations_api as task_api
from core.task_vocabulary import TASK_CATEGORY_API_VALUES

REPO = Path(__file__).resolve().parents[1]
API_FILE = REPO / "server/enterprise_task_operations_api.py"


def test_the_request_side_vocabularies_are_the_core_ones() -> None:
    assert get_args(task_api.SourceKind) == catalog_schema.ENTERPRISE_TASK_SOURCE_KINDS
    assert get_args(task_api.TaskStatus) == catalog_schema.ENTERPRISE_TASK_STATUSES
    assert get_args(task_api.TaskCategory) == TASK_CATEGORY_API_VALUES


def test_no_list_cap_is_a_copied_number() -> None:
    """`source_kinds` / `statuses` 这两个列表不许再带字面量上限。"""
    source = API_FILE.read_text(encoding="utf-8")
    # `[^)]*` 到字段右括号为止，所以单行写法和换行写法都覆盖得到（只按 `.*` 会漏掉跨行的那种）。
    offenders = re.findall(
        r"(?:source_kinds|statuses):[^)]*max_length=(\d+)", source, re.S
    )
    assert not offenders, f"列表上限又被写回成数字：{offenders}"
    assert source.count("max_length=len(ENTERPRISE_TASK_SOURCE_KINDS)") == 2
    assert source.count("max_length=len(ENTERPRISE_TASK_STATUSES)") == 1


def test_a_new_source_kind_needs_no_edit_to_the_api_layer() -> None:
    """扩展性判据：词表加一项，API 侧的字面量类型与上限自己跟上，宿主文件零改动。"""
    before = API_FILE.read_bytes()
    grown = (*catalog_schema.ENTERPRISE_TASK_SOURCE_KINDS, "knowledge_prune")
    original = catalog_schema.ENTERPRISE_TASK_SOURCE_KINDS
    catalog_schema.ENTERPRISE_TASK_SOURCE_KINDS = grown
    try:
        reloaded = importlib.reload(task_api)
        assert get_args(reloaded.SourceKind) == grown
        meta = reloaded.ReconcileRequest.model_fields["source_kinds"].metadata
        caps = [m.max_length for m in meta if hasattr(m, "max_length")]
        assert caps == [8], f"新来源没被上限接住，「全选」会 422：{caps}"
        saved = reloaded.SavedViewFilters.model_fields["source_kinds"].metadata
        assert [m.max_length for m in saved if hasattr(m, "max_length")] == [8]
    finally:
        catalog_schema.ENTERPRISE_TASK_SOURCE_KINDS = original
        importlib.reload(task_api)

    after = API_FILE.read_bytes()
    assert after == before, "派生一个词表项却要求改宿主文件，等于没收回声明"
    assert get_args(task_api.SourceKind) == original
    meta = task_api.ReconcileRequest.model_fields["source_kinds"].metadata
    assert [m.max_length for m in meta if hasattr(m, "max_length")] == [7]


def test_reconcile_and_saved_view_agree_with_the_core_predicate() -> None:
    """请求层的上限必须与核心层那条 `len(source_kinds) > len(词表)` 同步。"""
    meta = task_api.ReconcileRequest.model_fields["source_kinds"].metadata
    caps = [m.max_length for m in meta if hasattr(m, "max_length")]
    assert caps == [len(catalog_schema.ENTERPRISE_TASK_SOURCE_KINDS)]


def test_categories_keeps_its_deliberate_asymmetry() -> None:
    """别名输入词表与 canonical storage 上限保持有意不对称。

    `content` / `source` 等是兼容输入别名，数据库仍只写 canonical category。
    """
    assert len(get_args(task_api.TaskCategory)) > len(catalog_schema.ENTERPRISE_TASK_CATEGORIES)
    assert "content" in get_args(task_api.TaskCategory)
    assert "source" in get_args(task_api.TaskCategory)
    metadata = task_api.SavedViewFilters.model_fields["categories"].metadata
    assert [item.max_length for item in metadata if hasattr(item, "max_length")] == [
        len(catalog_schema.ENTERPRISE_TASK_CATEGORIES)
    ]
