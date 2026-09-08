"""RAG4C 生成模块核心实现。

负责"有据可依的答案生成"：
- 输入：用户问题 + 检索片段（:class:`~models.schemas.RetrievedChunk` 列表）
- 输出：纯文本答案，每个事实性陈述后紧跟 [N] 引用标记

引用约束（本模块唯一强制约束）：
- 引用编号必须落在本次提供的证据编号范围内（1..evidence_count）
- 越界标记（如 [9]，而证据只有 3 条）在输出前被剥离并记入 warnings，
  保证最终答案中绝不出现不存在的引用编号

:class:`CitationExtractor` 产出的 citation_ids 供后续验证模块（L1/L2/L3）使用。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from config.settings import get_settings
from core.llm import LLMClient, create_client
from core.tracing import current_trace
from models.schemas import Chunk, RetrievedChunk

# 项目根目录：本文件位于 <root>/generation/generator.py
_PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 送入 prompt 的证据片段数量上限（控制上下文规模）
MAX_EVIDENCE_CHUNKS = 12

# 输出规模提示（仅用于引导模型控制篇幅，不构成事实约束）
MAX_TOKENS_HINT = "800"

# 语言代码 -> prompt 中的人类可读标签
_LANGUAGE_LABELS: dict[str, str] = {
    "zh": "中文",
    "en": "English",
    "ja": "日本語",
}

# 引用标记正则：方括号内数字，按出现顺序解析
_CITATION_RE = re.compile(r"\[(\d+)\]")


class GenerationError(RuntimeError):
    """生成失败（模板缺失、空答案等）。"""


@dataclass
class GeneratedAnswer:
    """一次生成的结果。

    Attributes:
        answer: 最终答案文本（越界引用标记已被剥离）。
        citation_ids: 答案中保留的引用编号（按出现顺序，均落在 1..evidence_count）。
        evidence: 实际送入 prompt 的证据片段（按 chunk_id 去重后）。
        warnings: 生成过程中的警告（如被剥离的越界引用标记）。
    """

    answer: str
    citation_ids: list[int] = field(default_factory=list)
    evidence: list[Chunk] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class CitationExtractor:
    """引用标记解析与校验（静态工具类）。"""

    @staticmethod
    def parse_citation_ids(answer: str) -> list[int]:
        """按出现顺序解析答案中的全部 [N] 引用编号。

        Args:
            answer: 模型生成的答案文本。

        Returns:
            引用编号列表（保持文本中的出现顺序，不去重）。
        """
        return [int(m.group(1)) for m in _CITATION_RE.finditer(answer)]

    @staticmethod
    def validate(ids: list[int], evidence_count: int) -> tuple[list[int], list[int]]:
        """把编号分为 (valid, invalid)。

        Args:
            ids: 待校验的引用编号列表。
            evidence_count: 本次证据片段数量。

        Returns:
            (valid, invalid)：valid 为落在 1..evidence_count 的编号，
            invalid 为其余编号，两者均保持输入顺序。
        """
        valid: list[int] = []
        invalid: list[int] = []
        for cid in ids:
            if 1 <= cid <= evidence_count:
                valid.append(cid)
            else:
                invalid.append(cid)
        return valid, invalid


class Generator:
    """有据可依的答案生成器。

    Args:
        llm_client: LLM 客户端（生成槽位，见 :class:`~core.llm.LLMClient`）。
        template_path: prompt 模板路径；相对路径以项目根目录为基准，
            默认 ``prompts/generation_v1.txt``。
        language: 回答语言代码（"zh" -> "中文"），会替换模板中的 {language}。
    """

    def __init__(
        self,
        llm_client: LLMClient,
        template_path: str = "prompts/generation_v1.txt",
        language: str = "zh",
    ) -> None:
        self.llm_client = llm_client
        self.language = language
        self._template = self._load_template(template_path)

    # ------------------------------------------------------------------ #
    # 模板加载（init 时缓存，之后不再读盘）
    # ------------------------------------------------------------------ #
    def _load_template(self, template_path: str) -> str:
        path = Path(template_path)
        if not path.is_absolute():
            path = _PROJECT_ROOT / path
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise GenerationError(f"无法读取生成模板 {path}: {exc}") from exc

    # ------------------------------------------------------------------ #
    # 公开接口
    # ------------------------------------------------------------------ #
    def generate(self, query: str, chunks: list[RetrievedChunk]) -> GeneratedAnswer:
        """基于检索证据生成带 [N] 引用标记的答案。

        Args:
            query: 用户问题。
            chunks: 检索返回的片段（允许重复，内部按 chunk_id 去重）。

        Returns:
            GeneratedAnswer：答案中绝不包含越界引用标记。

        Raises:
            GenerationError: 模型未返回任何有效内容（空答案）。
        """
        t0 = time.perf_counter()
        deduped = self._dedup_chunks(chunks)
        prompt = self._build_prompt(query, deduped)
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": query},
        ]
        raw_answer = self.llm_client.chat(messages=messages)
        answer, citation_ids, warnings = self._postprocess(raw_answer, len(deduped))

        if not answer.strip():
            model_name = self.llm_client.config.model
            raise GenerationError(f"模型 {model_name} 未生成有效答案（输出为空）")

        trace = current_trace()
        if trace is not None:
            trace.add_span("generate", (time.perf_counter() - t0) * 1000.0)

        return GeneratedAnswer(
            answer=answer,
            citation_ids=citation_ids,
            evidence=[rc.chunk for rc in deduped],
            warnings=warnings,
        )

    # ------------------------------------------------------------------ #
    # 流式生成（SSE 用）
    # ------------------------------------------------------------------ #
    def generate_stream(self, query: str, chunks: list[RetrievedChunk]):
        """流式生成：逐 token 产出答案原始文本（未做越界引用剥离）。

        与 generate() 的差异：
        - 只产出原始模型文本增量（越界引用剥离等后处理必须在完整文本上
          进行，由调用方拼接后用 postprocess_stream() 完成）；
        - 内部同样按 chunk_id 去重并截断到 MAX_EVIDENCE_CHUNKS；
        - 生成耗时 span 在流结束后无法自动记录，由编排层（rag_stream）
          负责记录。

        Args:
            query: 用户问题。
            chunks: 检索返回的片段。

        Yields:
            模型产出的文本增量（str）。

        Raises:
            LLMError: 流式调用失败（调用方决定回退策略）。
        """
        deduped = self._dedup_chunks(chunks)
        prompt = self._build_prompt(query, deduped)
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": query},
        ]
        yield from self.llm_client.chat_stream(messages=messages)

    @staticmethod
    def postprocess_stream(
        raw_answer: str, evidence_count: int
    ) -> "GeneratedAnswer":
        """对拼接后的流式原始文本做后处理（复用 _postprocess）。

        Args:
            raw_answer: 拼接完成的模型原始输出。
            evidence_count: 本次证据片段数量（与流式生成时一致）。

        Returns:
            GeneratedAnswer：答案已不含越界引用标记。

        Raises:
            GenerationError: 模型未返回任何有效内容（空答案）。
        """
        answer, citation_ids, warnings = Generator._postprocess(
            raw_answer, evidence_count
        )
        if not answer.strip():
            raise GenerationError("模型未生成有效答案（输出为空）")
        return GeneratedAnswer(
            answer=answer,
            citation_ids=citation_ids,
            warnings=warnings,
        )

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    @staticmethod
    def _dedup_chunks(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """按 chunk_id 去重（保留首次出现顺序），并截断到 MAX_EVIDENCE_CHUNKS。"""
        seen: set[str] = set()
        deduped: list[RetrievedChunk] = []
        for rc in chunks:
            cid = rc.chunk.chunk_id
            if cid in seen:
                continue
            seen.add(cid)
            deduped.append(rc)
            if len(deduped) >= MAX_EVIDENCE_CHUNKS:
                break
        return deduped

    def _build_prompt(self, query: str, chunks: list[RetrievedChunk]) -> str:
        """构建证据块并填充模板占位符。"""
        evidence_lines = [
            f"[{idx}] {rc.chunk.text}" for idx, rc in enumerate(chunks, start=1)
        ]
        evidence_block = "\n".join(evidence_lines)
        language_label = _LANGUAGE_LABELS.get(self.language, self.language)
        return (
            self._template
            .replace("{question}", query)
            .replace("{evidence}", evidence_block)
            .replace("{max_tokens_hint}", MAX_TOKENS_HINT)
            .replace("{language}", language_label)
        )

    @staticmethod
    def _postprocess(
        raw_answer: str, evidence_count: int
    ) -> tuple[str, list[int], list[str]]:
        """后处理：解析引用、剥离越界标记、收集警告。

        Args:
            raw_answer: 模型原始输出。
            evidence_count: 本次证据片段数量。

        Returns:
            (answer, citation_ids, warnings)：answer 已不含越界标记。
        """
        ids = CitationExtractor.parse_citation_ids(raw_answer)
        valid, invalid = CitationExtractor.validate(ids, evidence_count)
        invalid_set = set(invalid)

        def _strip_out_of_range(match: re.Match) -> str:
            if int(match.group(1)) in invalid_set:
                return ""
            return match.group(0)

        answer = _CITATION_RE.sub(_strip_out_of_range, raw_answer)
        warnings = [
            f"越界引用标记 [{i}] 已从答案中剥离（有效范围 1..{evidence_count}）"
            for i in invalid
        ]
        return answer, valid, warnings


def create_generator(settings=None) -> Generator:
    """按配置创建生成器（生成槽位）。

    Args:
        settings: 可选的 Settings 实例；为 None 时使用 get_settings()。

    Usage:
        from generation import create_generator

        gen = create_generator()
        result = gen.generate(query, retrieved_chunks)
    """
    if settings is None:
        settings = get_settings()
    llm_client = create_client(settings.llm.generation)
    return Generator(llm_client=llm_client)


__all__ = [
    "GeneratedAnswer",
    "CitationExtractor",
    "Generator",
    "GenerationError",
    "create_generator",
    "MAX_EVIDENCE_CHUNKS",
    "MAX_TOKENS_HINT",
]
