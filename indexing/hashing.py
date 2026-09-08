"""文本哈希工具。

索引模块通用的内容指纹：对文本的 utf-8 字节做 sha256 摘要。
用于：
- :class:`Chunk.text_hash` 字段（内容校验、去重、缓存失效判断）；
- chunk_id 的确定性组成部分（同输入必得同 id）。
"""
from __future__ import annotations

import hashlib


def text_hash(text: str) -> str:
    """返回 ``text`` 的 sha256 十六进制摘要（64 字符）。

    Args:
        text: 任意文本（utf-8 编码后计算）。

    Returns:
        64 位小写十六进制字符串。
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = ["text_hash"]
