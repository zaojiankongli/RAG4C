"""Embedding / LLM / Reranker 三条 provider 轴的常驻守卫（补 §E 的判据覆盖缺口）。

普查（`docs/compose/spec/backend-extensibility-inventory.md` §A）已核实这三条轴
**本就已经**满足"加一个实现 = 0 处既有分支修改"：一切经
`core/providers.py:ProviderRegistry`，`server/` 与 `config/` 里没有残留的
`if provider ==`，`create_embedder` / `create_reranker` / `create_client` 是纯委托。
但"成立"和"有人守着"是两件事——写本文件之前 `tests/` 里没有任何一条测试引用
`EMBEDDING_PROVIDERS` / `LLM_PROVIDERS` / `RERANKER_PROVIDERS`，于是这条保证可以静默腐烂。
本文件只做一件事：把它变成**会被测红的**东西。不给这三个模块加任何间接层
（§C 明确说了不要为它们抽公共基类）。

钉住的五件事（判据见 `backend-extensibility-standard.md` §3）：

1. **零改分支**：注册一个假 provider 后，它能被本家族自己的创建路径选中，而所有
   宿主文件（三个模块 + settings + 装配点）**逐字节不变**。手法来自
   `tests/test_retrieval_stage_registry.py`（宿主字节）与
   `tests/test_storage_provider_registry.py`（注册即全通）。探针在 teardown 里注销，
   不让同进程的其他测试看见。
2. **注册期判死**：重名报错、空名报错、非 callable 报错；``replace=True`` 是唯一的显式覆盖口。
3. **未知值 fail closed**：错误文案原样钉住（不改行为），且其中的 "available:" 清单
   **由注册表现场推导**——注册表是"什么可选"的唯一真相，不是一份手抄名单。
4. **源码级守卫**：三个模块里不存在按 provider 名分派的比较/成员判定（AST 级，
   改注释躲不掉），创建函数里只有一条委托给**本家族**注册表的 ``.create(``，
   provider 名的字面量只出现在 ``register(`` 与 ``getattr(`` 默认值上。
5. **声明一致**：`config/settings.py` 里以注释形式写着的可选值清单（那是注册表集合
   唯一的重复副本）必须与注册表同名同集合，且默认值本身可选中。

探针一律不联网、不加载模型：工厂返回哨兵对象；真内置实现只测到"构造成功 + 符合
协议"为止（三者的构造都是惰性的，见各自 ``_ensure_client`` / ``_ensure_model``）。
"""

from __future__ import annotations

import ast
import inspect
import re
import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

import config.settings as settings_module
import core.embedding as embedding_module
import core.llm as llm_module
import core.reranker as reranker_module
from config.settings import (
    EmbeddingSettings,
    LlmSlotSettings,
    LlmSlotsSettings,
    RerankerSettings,
)
from core.embedding import EMBEDDING_PROVIDERS, EmbeddingService, create_embedder
from core.llm import LLM_PROVIDERS, LLMClient, create_client
from core.providers import ProviderRegistry
from core.reranker import (
    RERANKER_PROVIDERS,
    Reranker,
    RerankError,
    create_reranker,
)

_ROOT = Path(__file__).resolve().parents[1]

# 探针名：刻意用仓库里任何地方都不存在的名字，这样"它出现在拒绝文案里"只可能来自
# 注册表现场，不可能来自某份手抄名单。
_PROBE = "extprobe"


class _ProbeService:
    """探针实例：什么也不做，只用来证明"被选中的是我"。"""


# 新增任意一个 provider 时**不该需要改动**的文件集合：三个注册宿主 + 配置声明 +
# re-export 面（core/__init__.py 逐个列出了内置实现类）+ 全部生产装配点。装配点即
# ``grep -rlE "create_(embedder|reranker|client)\\(" --include=*.py`` 在 tests/ 与
# scripts/ 之外的每一处命中——谁持有调用点，谁就是候选宿主。
_HOST_FILES: tuple[tuple[str, Path], ...] = (
    ("core/embedding.py", Path(inspect.getsourcefile(embedding_module) or "")),
    ("core/llm.py", Path(inspect.getsourcefile(llm_module) or "")),
    ("core/reranker.py", Path(inspect.getsourcefile(reranker_module) or "")),
    ("core/__init__.py", _ROOT / "core" / "__init__.py"),
    ("config/settings.py", Path(inspect.getsourcefile(settings_module) or "")),
    ("rag.py", _ROOT / "rag.py"),
    ("server/app.py", _ROOT / "server" / "app.py"),
    ("server/documents.py", _ROOT / "server" / "documents.py"),
    ("server/health.py", _ROOT / "server" / "health.py"),
    ("generation/generator.py", _ROOT / "generation" / "generator.py"),
    ("eval/judges.py", _ROOT / "eval" / "judges.py"),
    ("indexing/auto_tagger.py", _ROOT / "indexing" / "auto_tagger.py"),
    ("retrieval/auto_filter.py", _ROOT / "retrieval" / "auto_filter.py"),
    ("retrieval/stages.py", _ROOT / "retrieval" / "stages.py"),
    ("verify/verifier.py", _ROOT / "verify" / "verifier.py"),
)


@dataclass(frozen=True)
class _Family:
    key: str
    registry: ProviderRegistry
    registry_var: str
    create: Callable[..., object]
    create_name: str
    module: ModuleType
    make_cfg: Callable[[str], object]
    default_cfg: Callable[[], object]
    protocol: type
    settings_class: str
    builtin: tuple[str, ...]


_FAMILIES = (
    _Family(
        key="embedding",
        registry=EMBEDDING_PROVIDERS,
        registry_var="EMBEDDING_PROVIDERS",
        create=create_embedder,
        create_name="create_embedder",
        module=embedding_module,
        make_cfg=lambda name: EmbeddingSettings(provider=name),
        default_cfg=EmbeddingSettings,
        protocol=EmbeddingService,
        settings_class="EmbeddingSettings",
        builtin=tuple(EMBEDDING_PROVIDERS.names()),
    ),
    _Family(
        key="llm",
        registry=LLM_PROVIDERS,
        registry_var="LLM_PROVIDERS",
        create=create_client,
        create_name="create_client",
        module=llm_module,
        make_cfg=lambda name: LlmSlotSettings(provider=name),
        default_cfg=LlmSlotSettings,
        protocol=LLMClient,
        settings_class="LlmSlotSettings",
        builtin=tuple(LLM_PROVIDERS.names()),
    ),
    _Family(
        key="reranker",
        registry=RERANKER_PROVIDERS,
        registry_var="RERANKER_PROVIDERS",
        create=create_reranker,
        create_name="create_reranker",
        module=reranker_module,
        make_cfg=lambda name: RerankerSettings(provider=name),
        default_cfg=RerankerSettings,
        protocol=Reranker,
        settings_class="RerankerSettings",
        builtin=tuple(RERANKER_PROVIDERS.names()),
    ),
)


@pytest.fixture(autouse=True)
def _probe_stays_out_of_the_way():
    """前置：探针不该被谁留下；后置：本文件哪条断言炸了都把它注销掉。"""
    for family in _FAMILIES:
        assert _PROBE not in family.registry.names(), (
            f"{family.registry_var} 里残留了探针 {_PROBE!r}——它会污染同进程的其他测试"
        )
    yield
    for family in _FAMILIES:
        if _PROBE in family.registry.names():
            family.registry.unregister(_PROBE)


def _snapshot_hosts() -> dict[str, bytes]:
    return {label: path.read_bytes() for label, path in _HOST_FILES}


def _drifted_hosts(before: dict[str, bytes]) -> list[str]:
    after = _snapshot_hosts()
    return sorted(label for label, data in before.items() if after[label] != data)


def _factory_of(result: object) -> Callable[[object], object]:
    return lambda _cfg: result


def _choices(names) -> str:
    return " / ".join(sorted(names))


# --------------------------------------------------------------------------- #
# 1. 零改分支
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("family", _FAMILIES, ids=[f.key for f in _FAMILIES])
def test_a_probe_provider_is_selectable_with_zero_edits_to_hosts(family: _Family) -> None:
    """判据本体：注册一条 + 一个类 = 0 处既有分支/宿主文件修改。"""
    hosts_before = _snapshot_hosts()
    marker = _ProbeService()
    family.registry.register(_PROBE, _factory_of(marker))

    # 本家族自己的创建路径就能选中它——不需要在创建路径上再加任何判据。
    assert family.create(family.make_cfg(_PROBE)) is marker
    # 名称规范化由注册表负责：大小写与首尾空白不是调用方的义务。
    assert family.create(family.make_cfg(f"  {_PROBE.upper()}  ")) is marker
    # 选中即生效，注册表的可见集合随之变化（没有第二份"可选清单"要同步）。
    assert _PROBE in family.registry.names()

    drifted = _drifted_hosts(hosts_before)
    assert drifted == [], f"注册一个 provider 竟然需要改动这些宿主文件：{drifted}"

    family.registry.unregister(_PROBE)
    assert _PROBE not in family.registry.names()


# --------------------------------------------------------------------------- #
# 2. 注册期判死
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("family", _FAMILIES, ids=[f.key for f in _FAMILIES])
def test_duplicate_registration_is_refused_and_replace_is_the_only_way_over(
    family: _Family,
) -> None:
    """重名在**注册时**就炸；静默覆盖是唯一不该发生的失败方式。"""
    family.registry.register(_PROBE, _factory_of("first"))
    try:
        with pytest.raises(ValueError, match=f"already registered: {_PROBE}"):
            family.registry.register(_PROBE, _factory_of("second"))
        # 被拒的那次注册必须没有改动原实现（否则"报错"反而是个半成品状态）。
        assert family.create(family.make_cfg(_PROBE)) == "first"

        family.registry.register(_PROBE, _factory_of("second"), replace=True)
        assert family.create(family.make_cfg(_PROBE)) == "second"
    finally:
        family.registry.unregister(_PROBE)


@pytest.mark.parametrize("family", _FAMILIES, ids=[f.key for f in _FAMILIES])
def test_registration_shape_checks_fire_at_register_time(family: _Family) -> None:
    """空名与非 callable 在注册期就被拒，而不是等到创建服务时才 AttributeError。"""
    with pytest.raises(ValueError, match="provider name must not be empty"):
        family.registry.register("   ", _factory_of(object()))
    with pytest.raises(TypeError, match="provider factory must be callable"):
        family.registry.register(_PROBE, "not-a-factory")  # type: ignore[arg-type]
    assert _PROBE not in family.registry.names()


# --------------------------------------------------------------------------- #
# 3. 未知值 fail closed —— 钉住今天的失败方式，不改动它
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("family", _FAMILIES, ids=[f.key for f in _FAMILIES])
def test_unknown_provider_is_rejected_naming_the_kind(family: _Family) -> None:
    for bad in ("nope", ""):
        with pytest.raises(ValueError) as exc:
            family.create(family.make_cfg(bad))
        assert str(exc.value) == (
            f"unknown {family.registry.kind} provider: {bad!r} "
            f"(available: {_choices(family.builtin)})"
        )


@pytest.mark.parametrize("family", _FAMILIES, ids=[f.key for f in _FAMILIES])
def test_the_available_list_is_derived_from_the_registry_not_a_hand_copy(
    family: _Family,
) -> None:
    """"哪些值可选"只有注册表一份真相：注册的探针立刻出现在拒绝文案里，注销后消失。"""
    family.registry.register(_PROBE, _factory_of(_ProbeService()))
    try:
        with pytest.raises(ValueError) as exc:
            family.create(family.make_cfg("nope"))
        assert f"(available: {_choices((*family.builtin, _PROBE))})" in str(exc.value)
    finally:
        family.registry.unregister(_PROBE)

    with pytest.raises(ValueError) as exc:
        family.create(family.make_cfg("nope"))
    assert f"(available: {_choices(family.builtin)})" in str(exc.value)
    assert _PROBE not in str(exc.value)


# --------------------------------------------------------------------------- #
# 4. 源码级守卫
# --------------------------------------------------------------------------- #

def _provider_side_names(node: ast.expr) -> set[str]:
    """表达式里"看着像 provider 字段"的名字（``cfg.provider`` / ``provider_ref`` …）。"""
    found: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute):
            found.add(sub.attr)
        elif isinstance(sub, ast.Name):
            found.add(sub.id)
    return {name for name in found if "provider" in name.lower()}


def _holds_str_constant(node: ast.expr) -> bool:
    return any(
        isinstance(sub, ast.Constant) and isinstance(sub.value, str)
        for sub in ast.walk(node)
    )


def _name_dispatch_sites(tree: ast.AST, source: str) -> list[str]:
    """一棵树里所有"按 provider 名做的等值/成员判定"（AST 级：注释与措辞骗不了它）。

    认 ``provider == "x"`` / ``"x" == cfg.provider`` / ``provider in ("api", "local")`` /
    ``match cfg.provider:`` 四种形态。``getattr(settings, "provider", "local")`` 这种
    取默认值不算分派——它不改变走哪条路，路仍然是注册表选的。
    """
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Match):
            if _provider_side_names(node.subject):
                hits.append(f"L{node.lineno} match {ast.get_source_segment(source, node.subject)}")
            continue
        if not isinstance(node, ast.Compare):
            continue
        if not isinstance(node.ops[0], (ast.Eq, ast.NotEq, ast.In, ast.NotIn)):
            continue
        operands = [node.left, *node.comparators]
        name_side = [o for o in operands if _provider_side_names(o)]
        value_side = [o for o in operands if not _provider_side_names(o)]
        if name_side and value_side and any(_holds_str_constant(o) for o in value_side):
            hits.append(f"L{node.lineno} {ast.get_source_segment(source, node)}")
    return hits


def _stray_provider_literals(module: ModuleType, names: tuple[str, ...]) -> list[str]:
    """provider 名的字面量只允许出现在 ``register(`` 与 ``getattr(`` 默认值上。"""
    stray: list[str] = []
    for lineno, line in enumerate(inspect.getsource(module).splitlines(), 1):
        if ".register(" in line or "getattr(" in line:
            continue
        for name in names:
            if re.search(r"""["']""" + re.escape(name) + r"""["']""", line):
                stray.append(f"L{lineno} {name!r}: {line.strip()}")
    return stray


@pytest.mark.parametrize("family", _FAMILIES, ids=[f.key for f in _FAMILIES])
def test_module_carries_no_per_provider_name_branch(family: _Family) -> None:
    """残留的 ``if provider ==`` 一旦回到这三个模块，这条先红。"""
    source = inspect.getsource(family.module)
    dispatch = _name_dispatch_sites(ast.parse(source), source)
    assert dispatch == [], f"{family.module.__name__} 仍在按 provider 名分派：{dispatch}"


@pytest.mark.parametrize("family", _FAMILIES, ids=[f.key for f in _FAMILIES])
def test_provider_name_literals_only_appear_at_registration(family: _Family) -> None:
    """堵住"注册表旁边再抄一份 dict/名单"的形态——那是第二份真相。"""
    stray = _stray_provider_literals(family.module, family.builtin)
    assert stray == [], f"{family.module.__name__} 里出现了裸的 provider 名清单：{stray}"


@pytest.mark.parametrize("family", _FAMILIES, ids=[f.key for f in _FAMILIES])
def test_creation_path_is_a_single_delegation_to_its_own_registry(family: _Family) -> None:
    """创建函数只允许一次 ``<本家族注册表>.create(``；多出来的都是分派点。"""
    source = textwrap.dedent(inspect.getsource(family.create))
    func = ast.parse(source).body[0]
    calls = [
        node
        for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create"
    ]
    assert len(calls) == 1, f"{family.create_name} 里有 {len(calls)} 处 .create( 调用"
    callee = calls[0].func
    assert isinstance(callee, ast.Attribute) and isinstance(callee.value, ast.Name)
    assert callee.value.id == family.registry_var, (
        f"{family.create_name} 委托到了别的注册表：{callee.value.id}"
    )
    dispatch = _name_dispatch_sites(func, source)
    assert dispatch == [], f"{family.create_name} 函数体里出现了按 provider 名的分支：{dispatch}"


# --------------------------------------------------------------------------- #
# 5. 声明一致：config/settings.py 的注释清单 vs 注册表集合
# --------------------------------------------------------------------------- #

_PROVIDER_FIELD_RE = re.compile(
    r'^\s{4}provider:\s*str\s*=\s*"(?P<default>[^"]*)"\s*#\s*(?P<declared>[^#\n]+?)\s*$'
)


def _declared_in_settings(class_name: str) -> tuple[str, list[str]]:
    """取 ``class <X>`` 体内那行 ``provider: str = "d"  # a | b``。

    只在自家类体里找：``MineruSettings`` 也有一行同样形状的
    ``provider: str = "cli"  # cli | http``，但那是解析引擎轴（§C / §D-8），不归这里管。
    """
    lines = Path(inspect.getsourcefile(settings_module) or "").read_text(
        encoding="utf-8"
    ).splitlines()
    inside = False
    for line in lines:
        if line.startswith("class "):
            inside = line.startswith(f"class {class_name}(")
            continue
        if not inside:
            continue
        match = _PROVIDER_FIELD_RE.match(line)
        if match:
            declared = [v.strip() for v in match.group("declared").split("|") if v.strip()]
            return match.group("default"), declared
    raise AssertionError(
        f"config/settings.py 的 {class_name} 里找不到形如 "
        '`provider: str = "..."  # a | b` 的声明行——注册表集合唯一的重复副本消失了，'
        "要么它被搬去了别处，要么这条轴的形状变了，两者都需要重新判断本文件守的是什么。"
    )


@pytest.mark.parametrize("family", _FAMILIES, ids=[f.key for f in _FAMILIES])
def test_settings_comment_declared_set_matches_the_registry(family: _Family) -> None:
    """注释里的可选值清单是注册表集合唯一的重复副本 —— 两边必须同步。"""
    default, declared = _declared_in_settings(family.settings_class)

    assert set(declared) == set(family.builtin), (
        f"{family.settings_class}.provider 的注释声明 {sorted(declared)} 与 "
        f"{family.registry_var}.names() = {sorted(family.builtin)} 不一致："
        f"只在注释里 = 文档承诺了一个选不中的值，"
        f"只在注册表里 = 有实现没声明。两边都得改，但由谁改要当场决定。"
    )
    assert default in family.builtin, f"{family.settings_class} 默认 provider {default!r} 未注册"


def test_builtin_registries_are_isolated_per_family() -> None:
    """一个 provider 不会被别的家族选中（``ProviderRegistry`` 的分裂是刻意的）。"""
    assert len({id(f.registry) for f in _FAMILIES}) == 3
    assert {f.registry.kind for f in _FAMILIES} == {"embedding", "llm", "reranker"}

    host, *others = _FAMILIES
    marker = _ProbeService()
    host.registry.register(_PROBE, _factory_of(marker))
    try:
        assert host.create(host.make_cfg(_PROBE)) is marker
        for other in others:
            with pytest.raises(ValueError, match=f"unknown {other.registry.kind} provider"):
                other.create(other.make_cfg(_PROBE))
    finally:
        host.registry.unregister(_PROBE)


# --------------------------------------------------------------------------- #
# 出厂配置真的走得通（顺带钉住"构造不联网"这条探针前提）
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("family", _FAMILIES, ids=[f.key for f in _FAMILIES])
def test_shipped_default_provider_is_constructible_and_conforms(family: _Family) -> None:
    """默认值必须能经本家族创建路径走通，且构造是惰性的（不联网、不加载模型）。"""
    service = family.create(family.default_cfg())
    assert isinstance(service, family.protocol), type(service).__name__


def test_every_llm_slot_default_routes_through_the_llm_registry() -> None:
    """11 个槽位的默认 provider 都要在 ``LLM_PROVIDERS`` 里——否则问答链路起手就抛。"""
    slots = LlmSlotsSettings()
    slot_fields = {
        field: slot
        for field, slot in slots.__dict__.items()
        if isinstance(slot, LlmSlotSettings)
    }
    assert len(slot_fields) == 11, sorted(slot_fields)

    unregistered = {
        field: slot.provider
        for field, slot in slot_fields.items()
        if slot.provider not in LLM_PROVIDERS.names()
    }
    assert unregistered == {}
    for slot in slot_fields.values():
        assert isinstance(create_client(slot), LLMClient)


def test_api_reranker_factory_still_fails_closed_without_a_key() -> None:
    """注册表选中不等于放行：api 重排器缺凭据仍然要拒（今天就是 RerankError）。"""
    with pytest.raises(RerankError, match="RAG4C_RERANKER_API_KEY"):
        create_reranker(RerankerSettings(provider="api", api_key=""))
