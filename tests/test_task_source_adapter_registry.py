"""扩展轴 #6 的服务层那一半：`SOURCE_ADAPTER_ORDER` 由适配器注册表派生。

第十三轮独立评审的 blocker F1：`1a05899` 把任务 API 的词表与上限收成了派生，但**同一形状的缺陷
就在隔壁文件** —— `core/enterprise_task_operations_service.py` 里 `SOURCE_ADAPTER_ORDER`（:65-73
手写 tuple）与 `SOURCE_ADAPTER_REGISTRY`（:902）平行维护，而 `_validate_source_kinds` 既用它当上限
（:441）又用它做过滤器（:447）。结果：加第 8 种来源时 API 层放行、服务层回一句
`source_kinds is invalid` 的 422；就算过了上限，那第 8 种也会在 :447 被静默滤掉。

修法与 `dfeecbf`（触发器那一半）同形状，判据也相同：注册表是唯一声明，加一个适配器不需要改任何
既有分支，且**宿主文件字节不变**。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import core.enterprise_task_operations_service as svc

REPO = Path(__file__).resolve().parents[1]
HOST = REPO / "core/enterprise_task_operations_service.py"

ALL_KINDS = (
    "document_ingest",
    "index_operation",
    "source_sync",
    "document_delete",
    "audit_export",
    "release_quality_scan",
    "release_recertification",
)


def test_the_order_is_the_registry_not_a_parallel_copy() -> None:
    assert svc.SOURCE_ADAPTER_ORDER == tuple(svc.SOURCE_ADAPTER_REGISTRY)
    source = HOST.read_text(encoding="utf-8")
    # 查声明形状而不是查字面量：适配器函数体本来就会写自己那个 code，按 code 扫必然误报
    # （轴 #12 已经踩过一次）。
    assert re.search(
        r"SOURCE_ADAPTER_ORDER: tuple\[str, \.\.\.\] = tuple\(SOURCE_ADAPTER_REGISTRY\)", source
    )
    assert source.count("SOURCE_ADAPTER_ORDER: tuple[str, ...] =") == 1, (
        "又多出一份并行的顺序声明 —— 正是本轴要消掉的东西"
    )


def test_validation_reads_the_registry_live_not_a_second_declaration() -> None:
    """上限与过滤都必须跟着注册表走：两处都读手写 tuple 就是 F1 的两个发作点。"""
    source = HOST.read_text(encoding="utf-8")
    body = source.split("def _validate_source_kinds", 1)[1].split("\n\n\ndef ", 1)[0]
    code = "\n".join(ln for ln in body.splitlines() if not ln.strip().startswith("#"))
    assert "SOURCE_ADAPTER_ORDER" not in code, (
        "校验还在读那份手写顺序：注册了新适配器也会先被上限拒、再被过滤器丢掉"
    )


def test_registering_an_eighth_kind_needs_no_branch_and_edits_no_host_file() -> None:
    """零改分支判据：挂第 8 个适配器，服务层当场接住并原样返回，宿主文件一字节不动。"""
    before = HOST.read_bytes()
    original_kinds = svc.TASK_SOURCE_KINDS
    try:
        svc.TASK_SOURCE_KINDS = frozenset(original_kinds | {"knowledge_prune"})  # type: ignore[attr-defined]
        svc.SOURCE_ADAPTER_REGISTRY["knowledge_prune"] = _probe_adapter
        selected = (*ALL_KINDS, "knowledge_prune")
        assert svc._validate_source_kinds(list(selected)) == selected, (
            "8 选没有被原样接住：要么被上限拒（:441），要么被过滤器丢（:447）"
        )
    finally:
        svc.SOURCE_ADAPTER_REGISTRY.pop("knowledge_prune", None)
        svc.TASK_SOURCE_KINDS = original_kinds  # type: ignore[attr-defined]

    assert HOST.read_bytes() == before, "注册即生效却要求改宿主文件，等于没收回注册表"


def _probe_adapter(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    return []


def test_validation_still_refuses_garbage_after_the_change() -> None:
    """收成派生不能顺手把校验放松：未知值、重复值、空列表都得照旧拒。"""
    for bad in ([], ["nope"], ["task_failed", "task_failed"]):
        try:
            svc._validate_source_kinds(bad)
        except svc.EnterpriseTaskOperationsInvalid:
            continue
        raise AssertionError(f"该拒的没拒：{bad}")
