"""顺序声明的AST守卫：注册表之后不许再有第二份手写顺序，也不许用间接方式绕开派生。

第十三轮评审的 M5 变异证明扫文本的栅栏看不见这一种：另写一个 `HAND_ORDER = ("a","b",…)`，
再把派生式改成 `X_ORDER = HAND_ORDER`，「派生那一句还在不在」这类检查全绿 —— 它扫的是字符串，
不是赋值结构。本文件按结构查，覆盖两个服务模块里形状相同的两处收口。
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MODULES = (
    REPO / "core/enterprise_automation_workflows_service.py",
    REPO / "core/enterprise_task_operations_service.py",
)


def _assignments(path: Path) -> dict[str, ast.expr]:
    """模块级 `X_ORDER = <expr>` 的赋值，键是名字、值是右侧表达式。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: dict[str, ast.expr] = {}
    for node in tree.body:  # 只看模块级：作用域内的同名局部变量不算这份契约
        pairs: list[tuple[ast.expr, ast.expr]] = []
        if isinstance(node, ast.Assign):
            pairs = [(target, node.value) for target in node.targets]
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            pairs = [(node.target, node.value)]
        for target, value in pairs:
            name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", "")
            if name.endswith("_ORDER"):
                found[name] = value
    return found


def _is_derived_from_registry(value: ast.expr) -> bool:
    """只认 `X_ORDER = tuple(X_REGISTRY)` 这一种形状。

    `= HAND_ORDER`（M5）、`= [k for k in …]`、`= sorted(...)` 都不算：派生的意义是**顺序与成员
    同时**由注册表决定，换成任何一层间接就把可追溯性丢了。
    """
    if not isinstance(value, ast.Call):
        return False
    func = value.func
    fname = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    if fname != "tuple" or len(value.args) != 1:
        return False
    arg = value.args[0]
    return isinstance(arg, ast.Name) and arg.id.endswith("_REGISTRY")


def _string_literals(value: ast.expr) -> tuple[str, ...]:
    if not isinstance(value, (ast.Tuple, ast.List)):
        return ()
    return tuple(
        node.value
        for node in value.elts
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def test_every_order_declaration_is_structurally_derived() -> None:
    for path in MODULES:
        for name, value in _assignments(path).items():
            assert _is_derived_from_registry(value), (
                f"{path.name}::{name} 不是 `= tuple(*_REGISTRY)` 的形状 —— "
                "顺序必须由注册表直接派生，中间隔一层（哪怕是另一个模块级 tuple）就会重演 F1/M5"
            )


def test_the_explicit_trigger_list_in_tests_is_reconciled_to_the_authority() -> None:
    """第十三轮 F10：测试里那份手抄 `TRIGGERS` 不是「第三份没人管的副本」。

    它**不该删** —— 删了之后 `tuple(ORDER) == TRIGGERS` 就变成拿注册表核对注册表，自证。
    该管的是它跟权威词表还不同不同：这条把测试文件里那个元组按 AST 取出来，与
    `AUTOMATION_TRIGGER_CODES` 比集合，漂移到一起就红。
    """
    from core.enterprise_automation_workflows import AUTOMATION_TRIGGER_CODES

    test_file = REPO / "tests/test_enterprise_automation_workflows_service.py"
    tree = ast.parse(test_file.read_text(encoding="utf-8"), filename=str(test_file))
    literals: tuple[str, ...] = ()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            (isinstance(t, ast.Name) and t.id == "TRIGGERS") for t in node.targets
        ):
            literals = _string_literals(node.value)
    assert literals, "测试里那份 TRIGGERS 不见了 —— 先确认这是有意的，再删掉本条"
    assert set(literals) == set(AUTOMATION_TRIGGER_CODES), (
        f"测试侧期望与权威词表漂移：{set(literals) ^ set(AUTOMATION_TRIGGER_CODES)}"
    )


def test_no_module_level_hand_written_order_tuple_survives() -> None:
    """M5 的正面拦截：模块级不许出现**看起来像顺序表**的字符串字面量元组。

    查「元组里全是字符串且长度 >2」而不是查具体变量名，所以改名绕不过去。
    """
    offenders: list[str] = []
    for path in MODULES:
        for name, value in _assignments(path).items():
            literals = _string_literals(value)
            if len(literals) > 2:
                offenders.append(f"{path.name}::{name}={literals}")
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            literals = _string_literals(node.value)
            if len(literals) <= 2:
                continue
            for target in node.targets:
                tname = target.id if isinstance(target, ast.Name) else ""
                if tname.endswith("_ORDER") or tname.endswith("_KINDS") or tname.endswith("_STATUSES"):
                    offenders.append(f"{path.name}::{tname} 手写了 {len(literals)} 个字面量")
    assert not offenders, f"这些顺序/词表还是手写的：{offenders}"
