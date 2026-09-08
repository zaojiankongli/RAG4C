"""声明（claim）切分工具。

把模型生成的整段答案切分为可逐条验证的声明列表。
切分规则是确定性纯函数，不依赖任何外部服务：

- 按中文句末标点（。！？；）与换行符切分；
- 去除首尾空白，丢弃空片段；
- 不足 4 个字符的碎片并入前一条声明（避免标点残留 / 编号残留
  造成大量无意义短句）。
"""
from __future__ import annotations

import re

# 中文句末标点 + 换行：一个或多个连续分隔符视为一次切分
_SENTENCE_SPLIT_RE = re.compile(r"[。！？；\n]+")

# 短碎片阈值：不足该长度的片段视为碎片，并入前一条声明
_MIN_FRAGMENT_LEN = 4


def split_claims(answer: str) -> list[str]:
    """把答案文本切分为声明列表。

    Args:
        answer: 模型生成的答案文本（可含 [N] 引用标记）。

    Returns:
        切分后的声明列表：按 。！？；\\n 切分，去首尾空白、丢弃空片段，
        长度不足 4 字符的碎片并入前一条声明。无内容时返回空列表。
    """
    if not answer:
        return []
    claims: list[str] = []
    for part in _SENTENCE_SPLIT_RE.split(answer):
        part = part.strip()
        if not part:
            continue
        if len(part) < _MIN_FRAGMENT_LEN and claims:
            # 碎片并入前一条声明（保持确定性：从左到右扫描）
            claims[-1] += part
        else:
            claims.append(part)
    return claims


__all__ = ["split_claims"]
