"""自动元数据打标签（通道 B：入库时 LLM 按分类体系为 chunk 打标签）。

- 输入：chunk 文本 + 分类体系（dataset 的 MetadataField 中 source=auto
  的字段 + 内置语言检测）；
- 处理：classifier 槽位输出 JSON 标签对象（如 department/topic）；
- 校验：标签键必须在 schema（source=auto 声明），值类型与声明一致；
  非法键 / 类型一律丢弃（防元数据污染），合法标签写入 chunk.metadata
  与 chunk 级元数据（供 Milvus JSON 字段过滤）；
- 开关：settings.catalog.auto_tag_on；组件注入 IngestPipeline
  （parse_and_chunk 之后可选调用）；任何失败静默降级，不影响入库。

与通道 A（检索时动态表达式）互补：预打标签保证高频分类（主题/部门/
语言）稳定可过滤，动态表达式覆盖临时性条件。
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

# 内置分类键（无需在 MetadataField 声明，自动打标签恒可用）
_BUILTIN_TYPES: dict[str, str] = {
    "topic": "string",       # 主题分类（如 报销 / 考勤 / 预算）
    "department": "string",  # 部门分类
    "language": "string",    # 语言（auto 检测）
    "doc_kind": "string",    # 文档类型（制度 / 报告 / 合同 / 通知）
}

_SYSTEM_PROMPT = (
    "你是文档元数据自动分类器。根据文档片段内容，输出一个 JSON 对象，"
    "键仅限：{keys}。topic 取最贴近的主题词；department 取最可能归属的"
    "部门（无法判断时给 general）；doc_kind 取 制度/报告/合同/通知/其他；"
    "language 取 zh/en。只输出 JSON 对象本身，不要解释，不要 markdown。"
)


def _extract_json(text: str) -> Optional[dict]:
    """从 LLM 输出提取 JSON 对象（容忍代码块围栏）。"""
    text = (text or "").strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if fenced:
        try:
            obj = json.loads(fenced.group(1).strip())
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


class AutoTagger:
    """入库时元数据自动分类器（LLM 打标签 + schema 校验）。"""

    def __init__(self, llm_client: Any, user_field_types: Optional[dict[str, str]] = None):
        self.llm_client = llm_client
        # 允许的标签键 -> 值类型（内置 + dataset 声明 source=auto 的字段）
        self.field_types: dict[str, str] = dict(_BUILTIN_TYPES)
        for key, vtype in (user_field_types or {}).items():
            self.field_types[key] = vtype

    def tag(self, text: str) -> dict[str, Any]:
        """为一段文本生成合法标签（失败 / 非法输出返回空 dict，绝不抛错）。"""
        keys = ", ".join(sorted(self.field_types.keys()))
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT.replace("{keys}", keys)},
            {"role": "user", "content": text[:2000]},
        ]
        try:
            raw = self.llm_client.chat(messages=messages)
        except Exception:  # noqa: BLE001 - LLM 不可用静默降级
            return {}
        obj = _extract_json(raw)
        if not obj:
            return {}
        out: dict[str, Any] = {}
        for key, value in obj.items():
            vtype = self.field_types.get(key)
            if vtype is None:
                continue  # 非法键：丢弃
            if vtype == "number" and not isinstance(value, (int, float)):
                continue
            if vtype == "bool" and not isinstance(value, bool):
                continue
            if isinstance(value, (dict, list)):
                continue
            out[key] = value
        return out

    def tag_chunks(self, chunks: list[Any]) -> None:
        """逐 chunk 打标签（原地写入 chunk.metadata，失败静默）。"""
        for chunk in chunks:
            tags = self.tag(chunk.text)
            if tags and isinstance(chunk.metadata, dict):
                chunk.metadata.update(tags)


def create_auto_tagger(settings: Any = None, user_field_types: dict[str, str] | None = None) -> Optional[AutoTagger]:
    """按配置创建自动打标签器（auto_tag_on=False 返回 None）。"""
    from config.settings import get_settings
    from core.llm import create_client

    s = settings or get_settings()
    if not getattr(s.catalog, "auto_tag_on", False):
        return None
    try:
        return AutoTagger(create_client(s.llm.classifier), user_field_types)
    except Exception:  # noqa: BLE001 - 槽位配置缺失静默降级
        return None


__all__ = ["AutoTagger", "create_auto_tagger"]
