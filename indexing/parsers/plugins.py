"""内置解析引擎插件注册（mineru / docling）。

import 本模块即完成注册（幂等）；工厂惰性 import 引擎实现，
disabled 引擎的重依赖（torch / docling 等）绝不加载。
"""
from __future__ import annotations

from typing import Any

from indexing.parsers.registry import ParserPlugin, register_parser_plugin


def _mineru_factory(engine_cfg: Any, settings: Any) -> Any:
    """mineru 插件工厂：按 mode（free / paid）+ mineru.provider（cli / http）装配。"""
    mode = str(getattr(engine_cfg, "mode", "free"))
    if settings.mineru.provider == "cli":
        from indexing.parsers.mineru_cli import MineruCliParser

        return MineruCliParser(settings.mineru, mode=mode)
    from indexing.parsers.mineru_http import MineruHttpParser

    return MineruHttpParser(settings.mineru, mode=mode)


def _docling_factory(engine_cfg: Any, settings: Any) -> Any:
    """docling 插件工厂（惰性 import 重依赖，仅 parse 时触发）。"""
    from indexing.parsers.docling_parser import DoclingParser

    return DoclingParser(engine_cfg, settings.docling)


def register_builtin_plugins() -> None:
    """注册内置引擎插件（幂等，可重复调用）。"""
    register_parser_plugin(
        ParserPlugin(
            name="mineru",
            factory=_mineru_factory,
            describe=lambda: "MinerU OCR / 版面解析（free=flash-extract 免 token；paid=v4 精准，需 API Token）",
            modes=("free", "paid"),
        )
    )
    register_parser_plugin(
        ParserPlugin(
            name="docling",
            factory=_docling_factory,
            describe=lambda: "IBM Docling（local=本地 AI 模型免费；api=云服务付费，预留）",
            modes=("local", "api"),
        )
    )


register_builtin_plugins()

__all__ = ["register_builtin_plugins"]
