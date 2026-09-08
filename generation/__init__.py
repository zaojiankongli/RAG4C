"""RAG4C 生成模块入口。

对外暴露：
- :class:`~generation.generator.Generator`      生成器
- :class:`~generation.generator.CitationExtractor` 引用解析与校验工具
- :class:`~generation.generator.GeneratedAnswer`   生成结果
- :func:`~generation.generator.create_generator`   工厂函数
"""
from generation.generator import (
    GeneratedAnswer,
    CitationExtractor,
    Generator,
    GenerationError,
    create_generator,
    MAX_EVIDENCE_CHUNKS,
    MAX_TOKENS_HINT,
)

__all__ = [
    "GeneratedAnswer",
    "CitationExtractor",
    "Generator",
    "GenerationError",
    "create_generator",
    "MAX_EVIDENCE_CHUNKS",
    "MAX_TOKENS_HINT",
]
