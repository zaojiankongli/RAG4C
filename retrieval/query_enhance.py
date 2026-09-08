"""查询端增强（Query Enhancement）可插拔组件。

定位（对齐官方 how_to_enhance_your_rag 的 Query Enhancement 方向，与
``config/settings.py`` 中 ``hyde_on / subqueries_on / stepback_on`` 开关对应）：

- :class:`HydeGenerator`：HyDE（Hypothetical Document Embeddings）假设文档
  嵌入。检索前用 LLM 生成"假设文档"，以假设文档做向量嵌入检索（不用于
  BM25 文本检索），弥合查询与文档之间的词汇鸿沟。
- :class:`SubQueryGenerator`：子查询拆解。把复杂多主题查询拆成多个子查询，
  分别检索后由管线合并结果，提升多主题查询的召回。
- :class:`StepbackGenerator`：Stepback 后退式提问。生成一个更抽象的后退
  问题补充检索，提升需要背景知识的问题的召回。

可插拔语义：
- 三个组件均离线可导入（不依赖 pymilvus / FlagEmbedding / openai），
  LLM 客户端通过构造参数注入（``chat_json(messages, schema_hint=None) -> dict``）。
- 所有 LLM 调用只发生在 ``generate`` 方法内部；任何失败
  （LLMError / ParseFallbackError / OSError / 非法 JSON / 模板缺失）一律
  静默降级，绝不向管线抛错：
  - ``HydeGenerator.generate`` / ``StepbackGenerator.generate`` 失败返回 None；
  - ``SubQueryGenerator.generate`` 失败返回 []，由调用方回退为原查询。
- 各组件自带异常类型（:class:`HydeGenerationError` 等，统一继承
  :class:`QueryEnhanceError`），供调用方在自行包装时区分增强失败；
  ``generate`` 内部会一并捕获，不会让它们逃逸到管线。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.llm import LLMError, ParseFallbackError


class QueryEnhanceError(Exception):
    """查询端增强组件异常基类。"""


class HydeGenerationError(QueryEnhanceError):
    """HyDE 假设文档生成失败。"""


class SubQueryGenerationError(QueryEnhanceError):
    """子查询拆解失败。"""


class StepbackGenerationError(QueryEnhanceError):
    """Stepback 后退式提问生成失败。"""


class HydeGenerator:
    """HyDE 假设文档嵌入生成器。

    Args:
        llm_client: 实现 ``chat_json(messages, schema_hint=None) -> dict`` 的客户端
            （如 :class:`core.llm.LLMClient` 或测试桩）。
        template_path: HyDE 提示词模板路径（``prompts/hyde_v1.txt``）。
        enabled: 是否启用；关闭时 ``generate`` 直接返回 None。
    """

    def __init__(
        self,
        llm_client: Any,
        template_path: str | Path,
        enabled: bool = True,
    ) -> None:
        self.llm_client = llm_client
        self.template_path = Path(template_path)
        self.enabled = enabled
        self._template: str | None = None

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _load_template(self) -> str:
        """惰性读取提示词模板。"""
        if self._template is None:
            self._template = self.template_path.read_text(encoding="utf-8")
        return self._template

    @staticmethod
    def _parse_result(data: Any) -> str | None:
        """解析 ``{"hypothetical_document": str}``，防御任意输入类型。"""
        if isinstance(data, str):
            # 个别客户端兜底解析可能直接返回文本，视作假设文档
            text = data.strip()
            return text or None
        if not isinstance(data, dict):
            return None
        document = data.get("hypothetical_document")
        if not isinstance(document, str) or not document.strip():
            return None
        return document.strip()

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    def generate(self, query: str) -> str | None:
        """为查询生成假设文档（纯文本，仅用于嵌入检索）。

        关闭、空查询、模板缺失或 LLM 失败时返回 None，静默降级。
        """
        if not self.enabled or not query or not query.strip():
            return None

        try:
            template = self._load_template()
            prompt = template.replace("{query}", query.strip())
            messages: list[dict[str, str]] = [
                {"role": "system", "content": "你是企业 RAG 系统的查询增强器，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ]
            data = self.llm_client.chat_json(
                messages,
                schema_hint='{"hypothetical_document": str}',
            )
        except (LLMError, ParseFallbackError, OSError, QueryEnhanceError):
            # 模板读取失败或 LLM 调用 / 解析失败：静默降级
            return None

        return self._parse_result(data)


class SubQueryGenerator:
    """子查询拆解生成器。

    Args:
        llm_client: 实现 ``chat_json(messages, schema_hint=None) -> dict`` 的客户端
            （如 :class:`core.llm.LLMClient` 或测试桩）。
        template_path: 子查询提示词模板路径（``prompts/subqueries_v1.txt``）。
        max_sub_queries: 子查询条数上限（去重后仍按此裁剪）。
        enabled: 是否启用；关闭时 ``generate`` 直接返回 []。
    """

    def __init__(
        self,
        llm_client: Any,
        template_path: str | Path,
        max_sub_queries: int = 4,
        enabled: bool = True,
    ) -> None:
        self.llm_client = llm_client
        self.template_path = Path(template_path)
        self.max_sub_queries = max_sub_queries
        self.enabled = enabled
        self._template: str | None = None

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _load_template(self) -> str:
        """惰性读取提示词模板。"""
        if self._template is None:
            self._template = self.template_path.read_text(encoding="utf-8")
        return self._template

    @staticmethod
    def _parse_result(data: Any) -> list[str]:
        """解析 ``{"sub_queries": [str, ...]}``，防御任意输入类型。

        清洗规则：逐条 strip、丢弃空串、按原序去重、只保留字符串元素。
        """
        if isinstance(data, list):
            # 个别客户端兜底解析可能直接返回数组
            raw: Any = data
        elif isinstance(data, dict):
            raw = data.get("sub_queries")
        else:
            return []
        if not isinstance(raw, list):
            return []
        seen: set[str] = set()
        result: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                continue
            cleaned = item.strip()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            result.append(cleaned)
        return result

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    def generate(self, query: str) -> list[str]:
        """拆解查询为子查询列表。

        关闭、空查询、模板缺失或 LLM 失败时返回 []，由调用方回退为原查询。
        """
        if not self.enabled or not query or not query.strip():
            return []

        try:
            template = self._load_template()
            prompt = template.replace("{query}", query.strip())
            prompt = prompt.replace("{max_sub_queries}", str(self.max_sub_queries))
            messages: list[dict[str, str]] = [
                {"role": "system", "content": "你是企业 RAG 系统的查询增强器，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ]
            data = self.llm_client.chat_json(
                messages,
                schema_hint='{"sub_queries": [str, ...]}',
            )
        except (LLMError, ParseFallbackError, OSError, QueryEnhanceError):
            # 模板读取失败或 LLM 调用 / 解析失败：静默降级
            return []

        return self._parse_result(data)[: self.max_sub_queries]


class StepbackGenerator:
    """Stepback 后退式提问生成器。

    Args:
        llm_client: 实现 ``chat_json(messages, schema_hint=None) -> dict`` 的客户端
            （如 :class:`core.llm.LLMClient` 或测试桩）。
        template_path: Stepback 提示词模板路径（``prompts/stepback_v1.txt``）。
        enabled: 是否启用；关闭时 ``generate`` 直接返回 None。
    """

    def __init__(
        self,
        llm_client: Any,
        template_path: str | Path,
        enabled: bool = True,
    ) -> None:
        self.llm_client = llm_client
        self.template_path = Path(template_path)
        self.enabled = enabled
        self._template: str | None = None

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _load_template(self) -> str:
        """惰性读取提示词模板。"""
        if self._template is None:
            self._template = self.template_path.read_text(encoding="utf-8")
        return self._template

    @staticmethod
    def _parse_result(data: Any) -> str | None:
        """解析 ``{"stepback_query": str}``，防御任意输入类型。

        空字符串 / 空白表示 LLM 判定无需后退，返回 None。
        """
        if isinstance(data, str):
            # 个别客户端兜底解析可能直接返回文本
            text = data.strip()
            return text or None
        if not isinstance(data, dict):
            return None
        stepback = data.get("stepback_query")
        if not isinstance(stepback, str) or not stepback.strip():
            return None
        return stepback.strip()

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    def generate(self, query: str) -> str | None:
        """为查询生成后退式问题（更抽象的背景问题）。

        关闭、空查询、模板缺失或 LLM 失败时返回 None；LLM 判定无需后退
        （stepback_query 为空）时同样返回 None，静默降级。
        """
        if not self.enabled or not query or not query.strip():
            return None

        try:
            template = self._load_template()
            prompt = template.replace("{query}", query.strip())
            messages: list[dict[str, str]] = [
                {"role": "system", "content": "你是企业 RAG 系统的查询增强器，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ]
            data = self.llm_client.chat_json(
                messages,
                schema_hint='{"stepback_query": str}',
            )
        except (LLMError, ParseFallbackError, OSError, QueryEnhanceError):
            # 模板读取失败或 LLM 调用 / 解析失败：静默降级
            return None

        return self._parse_result(data)


__all__ = [
    "HydeGenerator",
    "SubQueryGenerator",
    "StepbackGenerator",
    "QueryEnhanceError",
    "HydeGenerationError",
    "SubQueryGenerationError",
    "StepbackGenerationError",
]
