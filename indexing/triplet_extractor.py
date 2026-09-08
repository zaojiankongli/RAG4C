"""知识图谱三元组抽取器（入库阶段）。

设计目标：
- 输入 chunk 文本，用 LLM（``llm.triplet`` 槽位，temperature=0 保证确定性）
  抽取 ``(subject, predicate, object)`` 三元组，用于构建实体 / 关系集合。
- 提示词模板：``prompts/graph_triplet_v1.txt``（懒加载，可覆盖路径）。
- **失败不中断入库**：单个 chunk 的 JSON 解析失败（:class:`ParseFallbackError`）
  仅跳过该 chunk（返回空列表）；底层 LLM 网络错误仍向上抛
  （可能为环境问题，需要显式暴露）。
- 同 chunk 内三元组做大小写不敏感去重（:class:`~models.schemas.Triplet` 自带），
  空字段三元组被丢弃，超长实体名截断到 64 字符（主键上限）。
- 提供离线确定性桩 :class:`FakeTripletExtractor` 供冒烟测试（无需网络与模型）。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Sequence

from core.llm import LLMClient, ParseFallbackError
from models.schemas import Triplet
from prompts import render_prompt

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TripletExtractorError(RuntimeError):
    """三元组抽取失败（底层 LLM 调用错误等环境性问题）。"""


class TripletExtractor:
    """LLM 三元组抽取器。

    Args:
        llm: 三元组槽位 LLM 客户端（``settings.llm.triplet``）。
        template_path: 提示词模板路径（``prompts/graph_triplet_v1.txt``）。
        max_triplets: 单 chunk 最多抽取条数（拼入提示词）。
    """

    def __init__(
        self,
        llm: LLMClient,
        template_path: str = "prompts/graph_triplet_v1.txt",
        max_triplets: int = 20,
    ) -> None:
        self.llm = llm
        self.max_triplets = max_triplets
        self._template_path = _PROJECT_ROOT / template_path
        self._template: Optional[str] = None

    @property
    def template(self) -> str:
        """提示词模板（懒加载）。"""
        if self._template is None:
            self._template = self._template_path.read_text(encoding="utf-8")
        return self._template

    def extract(self, text: str) -> list[Triplet]:
        """从单个 chunk 文本抽取三元组。

        解析失败（模型未返回合法 JSON）返回空列表并跳过该 chunk，
        不抛出异常；底层 LLM 调用错误以 :class:`TripletExtractorError` 抛出。
        """
        text = (text or "").strip()
        if not text:
            return []
        # 必须用 render_prompt 而不是 str.format：模板正文里带着一段字面量 JSON
        # 骨架（"只输出如下格式"），format 会把它的 `{` 当成占位符起点并抛
        # KeyError: '\n  "triplets"'。这条路径上没有 except 能救——extract 只
        # 捕 ParseFallbackError，KeyError 会被下面的 `except Exception` 包成
        # TripletExtractorError 抛出，于是**每一个 chunk 都抽取失败**：
        # 图索引一打开就 100% 失败，图库恒为空，图检索静默降级回 hybrid。
        prompt = render_prompt(self.template, text=text, max_triplets=self.max_triplets)
        try:
            payload = self.llm.chat_json(
                messages=[{"role": "user", "content": prompt}]
            )
        except ParseFallbackError:
            # 单 chunk 解析失败 -> 跳过，不中断入库
            return []
        except Exception as exc:
            raise TripletExtractorError(
                f"三元组抽取 LLM 调用失败: {exc}"
            ) from exc

        return self._normalize(payload)

    def extract_batch(
        self, texts: Sequence[str]
    ) -> tuple[list[list[Triplet]], int]:
        """批量抽取（逐条调用，互不影响）。

        Returns:
            (每段结果列表, 失败段数)。失败段对应的结果为空列表。
        """
        results: list[list[Triplet]] = []
        failed = 0
        for text in texts:
            try:
                results.append(self.extract(text))
            except TripletExtractorError:
                results.append([])
                failed += 1
        return results, failed

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _normalize(self, payload: dict) -> list[Triplet]:
        """LLM 返回 dict -> 合法三元组列表（过滤空字段 / 截断超长实体）。"""
        raw = payload.get("triplets")
        if not isinstance(raw, list):
            return []
        triplets: list[Triplet] = []
        seen: set[tuple[str, str, str]] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            subject = self._clean_entity(item.get("subject"))
            predicate = self._clean_predicate(item.get("predicate"))
            obj = self._clean_entity(item.get("object"))
            if not subject or not predicate or not obj:
                continue
            key = (subject.lower(), predicate.lower(), obj.lower())
            if key in seen:
                continue
            seen.add(key)
            triplets.append(Triplet(subject=subject, predicate=predicate, object=obj))
            if len(triplets) >= self.max_triplets:
                break
        return triplets

    @staticmethod
    def _clean_entity(value: object) -> str:
        """实体清洗：去空白与修饰标点，截断到 64 字符。"""
        if not isinstance(value, str):
            return ""
        cleaned = re.sub(r"\s+", " ", value.strip())
        cleaned = cleaned.strip("，。、；：\"'「」『』()（）<>《》")
        return cleaned[:64]

    @staticmethod
    def _clean_predicate(value: object) -> str:
        """谓语清洗：去空白，保留关系短语。"""
        if not isinstance(value, str):
            return ""
        return re.sub(r"\s+", " ", value.strip())[:128]


class FakeTripletExtractor:
    """离线确定性三元组桩（冒烟测试用，无需网络 / 模型）。

    规则：匹配 ``X是Y`` / ``X 是 Y``（X、Y 为中文/英文/数字串）生成三元组；
    或使用显式 ``fixed`` 列表固定返回。
    """

    _PATTERN = re.compile(
        r"([\u4e00-\u9fa5A-Za-z0-9_]{2,32})\s*是\s*([\u4e00-\u9fa5A-Za-z0-9_]{2,32})"
    )

    def __init__(self, fixed: Optional[Sequence[Triplet]] = None) -> None:
        self._fixed = [Triplet(**t.model_dump()) for t in fixed] if fixed else None

    def extract(self, text: str) -> list[Triplet]:
        """按固定列表或正则规则返回三元组（确定性）。"""
        if self._fixed is not None:
            return list(self._fixed)
        triplets: list[Triplet] = []
        seen: set[tuple[str, str, str]] = set()
        for m in self._PATTERN.finditer(text or ""):
            t = Triplet(subject=m.group(1), predicate="是", object=m.group(2))
            key = (t.subject.lower(), t.predicate.lower(), t.object.lower())
            if key not in seen:
                seen.add(key)
                triplets.append(t)
        return triplets

    def extract_batch(
        self, texts: Sequence[str]
    ) -> tuple[list[list[Triplet]], int]:
        """与 :meth:`TripletExtractor.extract_batch` 同签名。"""
        return [self.extract(t) for t in texts], 0


__all__ = [
    "TripletExtractorError",
    "TripletExtractor",
    "FakeTripletExtractor",
]
