"""MinerU 文档解析器（``indexing.parsers``）。

对外暴露统一接口，供入库管线消费：

- :class:`ParsedDocument`：解析结果载体（全文 Markdown + 元数据）；
- :class:`DocumentParser`：解析器抽象基类（``supports()`` / ``parse()``）；
- :class:`MineruCliParser` / :class:`MineruHttpParser`：两种具体实现；
- :class:`MineruParserError`：解析失败异常（继承 ``RuntimeError``）；
- :func:`create_parser`：按 ``MineruSettings.provider`` 创建解析器的工厂。

本包完全离线可导入：网络请求与子进程调用只发生在各解析器的 ``parse()`` 内，
导入阶段不依赖 httpx / requests / MinerU CLI。
"""
from __future__ import annotations

from indexing.parsers.base import (
    DocumentParser,
    MineruParserError,
    ParsedDocument,
    create_parser,
)
from indexing.parsers.mineru_cli import MineruCliParser
from indexing.parsers.mineru_http import MineruHttpParser

__all__ = [
    "ParsedDocument",
    "DocumentParser",
    "MineruCliParser",
    "MineruHttpParser",
    "MineruParserError",
    "create_parser",
]
