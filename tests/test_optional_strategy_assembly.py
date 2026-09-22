"""装配点必须与注册表同源 —— 可选策略可扩展性判据在 `rag.py` 这一侧的落点。

`retrieval.stages` 的注册表把"新增一个可选策略"从改三处（写死的装配字典、
`RetrievalPipeline.__init__` 参数表、`run()` 的内联分支）降到改零处。本文件钉住
装配点那一半：字典的键由注册表决定，开关关着就不构造，构造失败不连坐。

这里曾经真漏过一次：装配函数改成遍历注册表时忘了 import 那两个名字 —— 只有走到
装配才 NameError，而当时没有任何测试经过这条路（99 条检索测试全绿也没发现）。
"""

from __future__ import annotations

import rag
from config.settings import get_settings
from retrieval.stages import OPTIONAL_STRATEGIES

_BUILT_WHEN_ON = ("hyde", "subqueries", "stepback", "sentence_window")


def _enable(names: tuple[str, ...]) -> object:
    settings = get_settings()
    for name in names:
        strategy = OPTIONAL_STRATEGIES.create(name, None)
        section = getattr(settings, strategy.flag_section, None)
        if section is not None and hasattr(section, strategy.flag):
            setattr(section, strategy.flag, True)
    return settings


def test_assembly_keys_come_from_the_registry() -> None:
    components = rag._build_optional_components(get_settings(), embedder=None, milvus=None)

    assert sorted(components) == sorted(OPTIONAL_STRATEGIES.names())


def test_enabled_strategies_build_and_the_rest_stay_none() -> None:
    settings = _enable(_BUILT_WHEN_ON)

    components = rag._build_optional_components(settings, embedder=object(), milvus=object())

    for name in _BUILT_WHEN_ON:
        assert components[name] is not None, f"{name} 开关已开却没装配出来"
    for name, value in components.items():
        if name in _BUILT_WHEN_ON:
            continue
        strategy = OPTIONAL_STRATEGIES.create(name, None)
        section = getattr(settings, strategy.flag_section, None)
        if not getattr(section, strategy.flag, False):
            assert value is None, f"{name} 开关关着却装配了"
