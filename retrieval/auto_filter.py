"""自动元数据过滤（通道 A：检索时 LLM 生成 Milvus 过滤表达式）。

- 输入：用户查询 + 元数据 schema（系统保留键 + 用户注册字段）；
- 处理：metadata_filter 槽位按 schema 提示生成单行过滤表达式；
- 校验：结果必须经 core.metadata.validate_filter_expr（键白名单 + 值类型
  + 语法），任何失败静默降级为「不过滤」并记录 trace——过滤是优化而非
  正确性约束，注入尝试不可能到达 Milvus；
- 开关：settings.catalog.auto_filter_on；组件由 rag.get_pipeline 注入
  检索管线（RetrievalPipeline 的 auto_filter 参数）。

用法::

    af = AutoFilter(llm_client=create_client(settings.llm.metadata_filter, slot="metadata_filter"))
    expr = af.generate(query, user_fields)   # None = 不生成/失败
    # expr 已通过 validate_filter_expr 校验，可直接拼入 build_filters
"""
from __future__ import annotations

from typing import Any, Optional

from core.metadata import (
    FilterValidationError,
    schema_to_prompt_text,
    validate_filter_expr,
)

_SYSTEM_PROMPT = (
    "你是元数据过滤条件生成器。根据用户查询与可用过滤键，输出一行 "
    "Milvus 过滤表达式。仅使用提供的键；若查询不涉及任何过滤，输出 NONE。"
    "不要输出解释，不要使用 markdown 代码块。"
)


class AutoFilter:
    """检索时自动过滤条件生成器（LLM 生成 + 白名单校验）。"""

    def __init__(self, llm_client: Any):
        self.llm_client = llm_client

    def generate(
        self, query: str, user_fields: list[dict[str, Any]]
    ) -> Optional[str]:
        """生成并校验过滤表达式。

        Returns:
            已校验表达式；LLM 失败 / 输出 NONE / 校验失败返回 None。
        """
        schema_text = schema_to_prompt_text(user_fields)
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT + "\n\n" + schema_text},
            {"role": "user", "content": query},
        ]
        try:
            raw = self.llm_client.chat(messages=messages)
        except Exception:  # noqa: BLE001 - LLM 不可用静默降级
            return None
        raw = (raw or "").strip()
        if not raw or raw.upper() == "NONE":
            return None
        try:
            return validate_filter_expr(raw, user_fields)
        except FilterValidationError:
            # 生成结果非法（键/类型/语法）：宁可不过滤，绝不放行注入
            return None


def create_auto_filter(settings: Any = None) -> Optional[AutoFilter]:
    """按配置创建自动过滤器（auto_filter_on=False 或槽位未配置返回 None）。"""
    from config.settings import get_settings
    from core.llm import create_client

    s = settings or get_settings()
    if not getattr(s.catalog, "auto_filter_on", False):
        return None
    try:
        return AutoFilter(llm_client=create_client(s.llm.metadata_filter, slot="metadata_filter"))
    except Exception:  # noqa: BLE001 - 槽位配置缺失静默降级
        return None


__all__ = ["AutoFilter", "create_auto_filter"]
