"""顺序/派发声明的 AST 守卫：不靠名字，靠**结构**（第十五轮 F1 之后重写）。

第一版按名字门控（`*_ORDER` / `*_KINDS` / `*_STATUSES`），于是 `c793f53` 提交信息里
"改名也躲不掉"是假的 —— 评审的变异 E6b 用模块级 `SOURCE_ADAPTER_SEQUENCE = (7 个 code)`
加一处消费点替换，守卫与旧的字面栅栏全部保持绿色。

现在查的是**结构**，与名字无关：
1. 每个 `*_ORDER` 必须是 `= tuple(*_REGISTRY)` 这一个形状；
2. 任何模块级字符串字面量序列（`Assign` 与 `AnnAssign` 一视同仁）**不许**与同模块某个
   `*_REGISTRY` 的键集**相等** —— 等价于"注册表之外还存在同一份顺序的第二份真源"，
   这就是 M5 与 E6b 共同的特征。合法词表（`TASK_SOURCE_KINDS` 等）是**超集或不同集**，
   不会被这条误伤；
3. 触发侧与任务侧的注册表键集必须等于各自权威词表 —— 补上 F1 指出的任务侧缺口。
"""

from __future__ import annotations

import ast
from pathlib import Path

import core.catalog_schema as catalog_schema
import core.enterprise_automation_workflows_service as trigger_svc
import core.enterprise_task_operations_service as task_svc
from core.enterprise_automation_workflows import AUTOMATION_TRIGGER_CODES

REPO = Path(__file__).resolve().parents[1]
MODULES = (
    REPO / "core/enterprise_automation_workflows_service.py",
    REPO / "core/enterprise_task_operations_service.py",
)


def _module_level_assignments(path: Path) -> list[tuple[str, ast.expr]]:
    """模块级 `NAME = <expr>`，`Assign` 与 `AnnAssign` 走同一条路径（第一版不一致）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, ast.expr]] = []
    for node in tree.body:
        pairs: list[tuple[ast.expr, ast.expr]] = []
        if isinstance(node, ast.Assign):
            pairs = [(target, node.value) for target in node.targets]
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            pairs = [(node.target, node.value)]
        for target, value in pairs:
            name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", "")
            if name:
                found.append((name, value))
    return found


def _string_set(value: ast.expr) -> frozenset[str] | None:
    """Tuple/List/Set 里全是字符串常量且 ≥3 个时返回该集合，否则 None。"""
    if not isinstance(value, (ast.Tuple, ast.List, ast.Set)):
        return None
    items = [
        item.value
        for item in value.elts
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    ]
    if len(items) != len(value.elts) or len(items) < 3:
        return None
    return frozenset(items)


def _registry_key_sets(path: Path) -> dict[str, frozenset[str]]:
    """同模块内 `*_REGISTRY = {…}` 的键集。"""
    out: dict[str, frozenset[str]] = {}
    for name, value in _module_level_assignments(path):
        if not name.endswith("_REGISTRY") or not isinstance(value, ast.Dict):
            continue
        keys = [
            key.value
            for key in value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        ]
        if len(keys) == len(value.keys):
            out[name] = frozenset(keys)
    return out


def test_every_order_declaration_is_structurally_derived() -> None:
    for path in MODULES:
        order_names = [name for name, _ in _module_level_assignments(path) if name.endswith("_ORDER")]
        assert order_names, f"{path.name} 里找不到 *_ORDER 声明，守卫要看的东西不在了"
        for name, value in _module_level_assignments(path):
            if not name.endswith("_ORDER"):
                continue
            ok = (
                isinstance(value, ast.Call)
                and getattr(value.func, "id", "") == "tuple"
                and len(value.args) == 1
                and isinstance(value.args[0], ast.Name)
                and value.args[0].id.endswith("_REGISTRY")
            )
            assert ok, (
                f"{path.name}::{name} 不是 `= tuple(*_REGISTRY)` 的形状 —— "
                "中间隔任何一层都会重演 F1/M5"
            )


def test_no_literal_sequence_duplicates_a_registry_key_set() -> None:
    """与名字无关的那条：注册表之外不许存在同一份成员的第二份真源。

    M5（`HAND_ORDER = (…)` + `X_ORDER = HAND_ORDER`）与 E6b（`SOURCE_ADAPTER_SEQUENCE`
    换个名字 + 换消费点）都满足"字面量集合 == 某注册表键集"，所以两种都拦得住。
    """
    offenders: list[str] = []
    for path in MODULES:
        registries = _registry_key_sets(path)
        assert registries, f"{path.name} 没有可查的 *_REGISTRY 字面量字典"
        for name, value in _module_level_assignments(path):
            literal = _string_set(value)
            if literal is None:
                continue
            for registry, keys in registries.items():
                if literal == keys:
                    offenders.append(
                        f"{path.name}::{name} 与 {registry} 键集完全相同（{len(literal)} 个成员）"
                    )
    assert not offenders, f"注册表外又长出一份顺序真源：{offenders}"


def test_registries_cover_their_authoritative_vocabularies() -> None:
    """F1 指出的任务侧缺口：触发侧早有这条对账，任务侧没有。"""
    assert set(trigger_svc.TRIGGER_ADAPTER_REGISTRY) == set(AUTOMATION_TRIGGER_CODES)
    assert set(task_svc.SOURCE_ADAPTER_REGISTRY) == set(catalog_schema.ENTERPRISE_TASK_SOURCE_KINDS)


def test_the_guard_is_not_name_gated() -> None:
    """栅栏的自查：把成员照抄成一个**不相关名字**的字面量元组，必须被抓到。

    这条本身就是 E6b 的最小复现 —— 第一版按名字门控时它是绿的。
    """
    probe = "RAG4C_PROBE_SEQUENCE = (\n" '    "document_ingest",\n' '    "index_operation",\n'
    probe += '    "source_sync",\n    "document_delete",\n    "audit_export",\n'
    probe += '    "release_quality_scan",\n    "release_recertification",\n)\n'
    source = MODULES[1].read_text(encoding="utf-8")
    mutated = ast.parse(source + probe, filename="probe")
    assert isinstance(mutated.body[-1], (ast.Assign, ast.AnnAssign))
    value = mutated.body[-1].value  # type: ignore[union-attr]
    keys = _registry_key_sets(MODULES[1])["SOURCE_ADAPTER_REGISTRY"]
    assert _string_set(value) == keys, "探针写法变了，这条自查要先修"
    offenders = [
        name
        for name, val in _module_level_assignments(MODULES[1]) + [("RAG4C_PROBE_SEQUENCE", value)]
        if (literal := _string_set(val)) is not None
        for registry, kset in keys.items()
        if literal == kset
    ] if False else [
        name
        for name, val in [("RAG4C_PROBE_SEQUENCE", value)]
        if _string_set(val) == keys
    ]
    assert offenders == ["RAG4C_PROBE_SEQUENCE"], (
        "一个与注册表同成员、名字无关的字面量序列没被判为第二真源"
    )
