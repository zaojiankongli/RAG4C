"""查询改写槽位（LLM 改写 + 复杂度门控成本门）。

设计（任务书 5.1 / 5.2）：
- 复杂度门控：对已经"具体明确"的查询（短于 40 字符且不含疑问标记）跳过 LLM，
  直接原样返回，省一次 LLM 调用（成本门）。
- 对复杂查询调用 LLM 改写，模板为 ``prompts/query_rewrite_v1.txt``，
  解析 ``{"rewritten_query", "changed", "reason"}``。
- LLM 失败（LLMError / ParseFallbackError）或改写结果为空时静默降级：
  返回 ``(query, False)``，永不抛错。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.llm import LLMError, ParseFallbackError

# 疑问 / 抽象标记：命中任一即认为查询可能需要改写（非具体查询）
_QUESTION_MARKERS: tuple[str, ...] = (
    "？",
    "?",
    "how",
    "如何",
    "什么",
    "为什么",
    "是否",
)

_GATE_LENGTH_LIMIT = 40


class QueryRewriter:
    """查询改写器。

    Args:
        llm_client: 实现 ``chat_json(messages, schema_hint=None) -> dict`` 的客户端
            （如 :class:`core.llm.LLMClient` 或测试桩）。
        template_path: 改写提示词模板路径（``prompts/query_rewrite_v1.txt``）。
        gate_enabled: 是否启用复杂度门控（简单查询跳过 LLM）。
    """

    def __init__(
        self,
        llm_client: Any,
        template_path: str | Path,
        gate_enabled: bool = True,
    ) -> None:
        self.llm_client = llm_client
        self.template_path = Path(template_path)
        self.gate_enabled = gate_enabled
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
    def _is_concrete(query: str) -> bool:
        """启发式判断查询是否已足够具体明确（可直接检索，无需 LLM 改写）。

        规则：长度小于 40 字符，且不包含任何疑问 / 抽象标记。
        """
        text = query.strip()
        if len(text) >= _GATE_LENGTH_LIMIT:
            return False
        lowered = text.lower()
        return not any(marker in lowered for marker in _QUESTION_MARKERS)

    @staticmethod
    def _parse_result(data: Any, query: str) -> tuple[str, bool]:
        """解析 LLM 返回的 JSON，非法时降级为 ``(query, False)``。"""
        if not isinstance(data, dict):
            return query, False
        rewritten = data.get("rewritten_query")
        if not isinstance(rewritten, str) or not rewritten.strip():
            return query, False
        rewritten = rewritten.strip()
        changed = bool(data.get("changed", rewritten != query))
        return rewritten, changed

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    def rewrite(self, query: str) -> tuple[str, bool]:
        """改写查询，返回 ``(rewritten_query, changed)``。

        简单查询（复杂度门控命中）或 LLM 失败时返回 ``(query, False)``，
        不修改原文，由管线继续走默认路径。
        """
        if not query or not query.strip():
            return query, False

        # 成本门：具体查询不调用 LLM
        if self.gate_enabled and self._is_concrete(query):
            return query, False

        try:
            template = self._load_template()
            prompt = template.replace("{query}", query)
            messages: list[dict[str, str]] = [
                {"role": "system", "content": "你是企业 RAG 系统的查询改写器，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ]
            data = self.llm_client.chat_json(
                messages,
                schema_hint='{"rewritten_query": str, "changed": bool, "reason": str}',
            )
        except (LLMError, ParseFallbackError, OSError):
            # 模板读取失败或 LLM 调用 / 解析失败：静默降级
            return query, False

        return self._parse_result(data, query)


__all__ = ["QueryRewriter"]
