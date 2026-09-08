"""Versioned prompt templates shipped with the RAG4C distribution.

本模块同时提供模板渲染的**唯一约定** :func:`render_prompt`。

为什么不用 ``str.format``
------------------------

这些模板几乎每一份都以「只输出如下 JSON」结尾，正文里带着一段字面量 JSON 骨架::

    {
      "triplets": [
        {"subject": "实体A", "predicate": "关系描述", "object": "实体B"}
      ]
    }

``str.format`` 会把这里的每一个 ``{`` 都当成占位符起点，于是渲染时抛
``KeyError: '\\n  "triplets"'``——**模板越是把输出格式讲清楚，越是渲染不出来**。
仓库里 11 份模板有 10 份含这样的字面量花括号。

历史上仓库靠两种办法各自绕开：9 个调用点用 ``.replace("{field}", value)``
（天然不碰其余花括号），另外 2 个用 ``.format`` 并在模板里手工写 ``{{``/``}}``。
两种约定并存且互不兼容——把一份为 ``.replace`` 写的模板交给 ``.format`` 就会炸，
``indexing/triplet_extractor.py`` 正是这么炸的（图索引一开就 100% 失败）。

:func:`render_prompt` 收敛为一种：只替换**明确传入**的占位符，其余花括号原样保留。
"""
from __future__ import annotations

import re

#: 占位符形状：``{identifier}``。字面量 JSON 里的 ``{"triplets"`` 以引号开头，
#: 不匹配标识符规则，因此天然被排除在替换之外。
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def render_prompt(template: str, /, **values: object) -> str:
    """把 ``{name}`` 占位符替换为给定值，模板里其余花括号原样保留。

    与 ``str.format`` 的差别正是本函数存在的理由：不认识的花括号一律不碰，
    所以模板可以自由地包含一段字面量 JSON 示例而无需转义。

    单次扫描替换（``re.sub`` 不回扫替换结果），因此**值里的花括号不会被二次
    解释**——一段恰好含 ``{max_triplets}`` 字样的用户文档不会篡改提示词。

    Args:
        template: 模板全文。
        **values: 占位符名 -> 值（非字符串按 ``str()`` 转换）。

    Returns:
        渲染后的提示词。

    Raises:
        KeyError: 某个传入的占位符在模板里根本不存在。这是**故意**要吵的：
            静默替换失败会让提示词带着字面的 ``{text}`` 发给模型，模型照常
            返回一个看似合理的结果，缺陷可以潜伏很久。模板改名 / 手滑拼错
            应当在渲染这一刻就暴露。
    """
    missing = sorted(k for k in values if "{" + k + "}" not in template)
    if missing:
        raise KeyError(
            f"模板缺少占位符 {missing}（模板是否已改名？）"
        )

    def _sub(m: re.Match[str]) -> str:
        key = m.group(1)
        # 未传入的标识符占位符原样保留：它可能是模板里的示例文本，
        # 也可能由另一层渲染负责，不该由这一层擅自清空。
        return str(values[key]) if key in values else m.group(0)

    return _PLACEHOLDER.sub(_sub, template)


__all__ = ["render_prompt"]
