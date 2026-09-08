"""RAG4C 评测裁判：Groundedness（有据性）与 Relevance（相关性）。

两个裁判相互独立，均基于 prompt 模板 + 严格 JSON 输出：

- 模板本身强制 Evidence-Only：裁判只依据给定证据片段判断，不依赖外部知识。
- ``chat_json`` 解析失败或模型返回结构不合法时，显式返回 ``score=None``
  （``rationale="judge_parse_failed"``），绝不静默当作 0 分。分数缺失必须可见。

评分契约：有据性裁判按声明逐条判定 supported / unsupported，
``score = supported / total``；相关性裁判按模板内置的 1-5 锚点打分，
映射为 ``(r - 1) / 4``，取值 1..5 之外一律视为解析失败。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from config.settings import Settings, get_settings
from core.llm import LLMError, ParseFallbackError, create_client
from models.schemas import Chunk, JudgeResult

# ---------------------------------------------------------------------------
# 声明切分：优先使用 verify.claims（并行建设中），不可用时回退到本地实现
# ---------------------------------------------------------------------------

try:
    from verify.claims import split_claims as _verify_split_claims

    _HAS_VERIFY_CLAIMS = True
except Exception:  # pragma: no cover - verify 模块可能尚未创建
    _verify_split_claims = None
    _HAS_VERIFY_CLAIMS = False

_CLAIM_BOUNDARY_RE = re.compile(r"[。！？!?；;\n]+")


def _split_claims_fallback(answer: str) -> list[str]:
    """本地回退声明切分：按中英文句末标点与换行切句。"""
    parts = _CLAIM_BOUNDARY_RE.split(answer)
    claims = [p.strip() for p in parts if p and p.strip()]
    return [c for c in claims if len(c) >= 2]


def split_claims(answer: str) -> list[str]:
    """把回答切分为独立声明（claim）列表。

    优先使用 ``verify.claims.split_claims``；若该模块不可用
    （例如离线评测时尚未建成），则回退到本地句级切分。
    """
    if _HAS_VERIFY_CLAIMS and _verify_split_claims is not None:
        return list(_verify_split_claims(answer))
    return _split_claims_fallback(answer)


# ---------------------------------------------------------------------------
# 公共工具
# ---------------------------------------------------------------------------

GROUNDEDNESS_SCHEMA_HINT = (
    '{"verdicts": [{"claim": str, "status": str, "cited_chunk_ids": [], "reason": str}]}'
)
RELEVANCE_SCHEMA_HINT = (
    '{"relevance": int, "rationale": str, "unsupported_claims": [], "missing_facts": []}'
)


def _load_template(template_path: str) -> str:
    """读取裁判模板；相对路径按项目根目录解析（与工作目录无关）。"""
    path = Path(template_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    if not path.is_file():
        raise FileNotFoundError(f"裁判模板不存在: {path}")
    return path.read_text(encoding="utf-8")


def _format_claims(claims: list[str]) -> str:
    return "\n".join(f"{i}. {claim}" for i, claim in enumerate(claims, start=1))


def _format_evidence(chunks: list[Chunk]) -> str:
    """证据块：每行以编号开头（[1] 到 [N]），与模板约定一致。"""
    if not chunks:
        return "（无证据片段提供）"
    return "\n".join(f"[{i + 1}] {c.text}" for i, c in enumerate(chunks))


def _parse_failure_result(exc: Exception, raw: dict[str, Any] | None = None) -> JudgeResult:
    """显式失败结果：score=None，绝不返回 0。"""
    error = raw if raw is not None else {"error": str(exc)}
    return JudgeResult(score=None, rationale="judge_parse_failed", raw=error)


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str)]


def _coerce_relevance(value: Any) -> int | None:
    """校验 relevance 必须是 1-5 的整数（容忍 4.0 这类整数值浮点）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        result = value
    elif isinstance(value, float) and value.is_integer():
        result = int(value)
    else:
        return None
    return result if 1 <= result <= 5 else None


# ---------------------------------------------------------------------------
# 裁判
# ---------------------------------------------------------------------------

class _BaseJudge:
    """裁判基类：持有 LLM 客户端与模板。"""

    def __init__(self, llm_client: Any, template_path: str) -> None:
        self.llm_client = llm_client
        self._template = _load_template(template_path)


class GroundednessJudge(_BaseJudge):
    """有据性裁判：逐条判断回答中的声明能否由证据推导得出。

    证据块按 ``[1] ...`` 编号拼接，模板强制 Evidence-Only：
    事实正确但证据不支持、或引用了不存在的编号，一律判 unsupported。
    """

    SCHEMA_HINT = GROUNDEDNESS_SCHEMA_HINT

    def __init__(
        self,
        llm_client: Any,
        template_path: str = "prompts/judge_groundedness_v1.txt",
    ) -> None:
        super().__init__(llm_client, template_path)

    def judge(self, question: str, answer: str, chunks: list[Chunk]) -> JudgeResult:
        """对回答做有据性判定。

        Returns:
            JudgeResult：``score = supported / total``（0..1）；
            无法判定（空回答 / 无声明 / 解析失败）时 ``score=None``，显式而非 0。
        """
        del question  # 有据性模板不包含问题占位符
        text = (answer or "").strip()
        if not text:
            return JudgeResult(score=None, rationale="empty_answer", raw={})
        claims = split_claims(text)
        if not claims:
            return JudgeResult(score=None, rationale="no_claims", raw={})
        prompt = (
            self._template.replace("{claims}", _format_claims(claims))
            .replace("{evidence}", _format_evidence(list(chunks)))
        )
        messages = [{"role": "user", "content": prompt}]
        try:
            raw = self.llm_client.chat_json(messages, schema_hint=self.SCHEMA_HINT)
        except ParseFallbackError as exc:
            return _parse_failure_result(exc)
        except LLMError as exc:
            return _parse_failure_result(exc)
        try:
            verdicts = raw.get("verdicts")
            if not isinstance(verdicts, list):
                return _parse_failure_result(ValueError("verdicts 不是数组"), {"error": "verdicts 不是数组", "raw": raw})
            total = len(verdicts)
            if total == 0:
                return JudgeResult(score=None, rationale="no_verdicts", raw=raw)
            supported = 0
            unsupported_claims: list[str] = []
            for verdict in verdicts:
                if not isinstance(verdict, dict):
                    continue  # 畸形条目不计入有据
                if verdict.get("status") == "supported":
                    supported += 1
                elif verdict.get("status") == "unsupported":
                    claim = verdict.get("claim")
                    if isinstance(claim, str) and claim:
                        unsupported_claims.append(claim)
            return JudgeResult(
                score=supported / total,
                rationale=f"有据声明 {supported}/{total} 条",
                unsupported_claims=unsupported_claims,
                raw=raw,
            )
        except Exception as exc:
            return _parse_failure_result(exc)


class RelevanceJudge(_BaseJudge):
    """相关性裁判：对照模板内置的 1-5 锚点评分合约打分。

    - 模板含锚点定义、扣分条件、不计分条件与 Evidence-Only 条款；
    - 1-5 分映射为 ``(r - 1) / 4`` 落入 [0, 1]；
    - relevance 非 1-5 整数视为解析失败，显式返回 ``score=None``。
    """

    SCHEMA_HINT = RELEVANCE_SCHEMA_HINT

    def __init__(
        self,
        llm_client: Any,
        template_path: str = "prompts/judge_relevance_v1.txt",
    ) -> None:
        super().__init__(llm_client, template_path)

    def judge(self, question: str, answer: str, chunks: list[Chunk]) -> JudgeResult:
        """对回答做相关性评分。

        Returns:
            JudgeResult：``score = (relevance - 1) / 4``；
            空回答或解析失败时 ``score=None``，显式而非 0。
        """
        text = (answer or "").strip()
        if not text:
            return JudgeResult(score=None, rationale="empty_answer", raw={})
        prompt = (
            self._template.replace("{question}", question)
            .replace("{answer}", text)
            .replace("{evidence}", _format_evidence(list(chunks)))
        )
        messages = [{"role": "user", "content": prompt}]
        try:
            raw = self.llm_client.chat_json(messages, schema_hint=self.SCHEMA_HINT)
        except ParseFallbackError as exc:
            return _parse_failure_result(exc)
        except LLMError as exc:
            return _parse_failure_result(exc)
        try:
            relevance = _coerce_relevance(raw.get("relevance"))
            if relevance is None:
                return _parse_failure_result(
                    ValueError("relevance 不是 1-5 的整数"),
                    {"error": "relevance 不是 1-5 的整数", "raw": raw},
                )
            return JudgeResult(
                score=(relevance - 1) / 4.0,
                rationale=raw.get("rationale") or f"relevance={relevance}",
                unsupported_claims=_str_list(raw.get("unsupported_claims")),
                missing_facts=_str_list(raw.get("missing_facts")),
                raw=raw,
            )
        except Exception as exc:
            return _parse_failure_result(exc)


def create_judges(settings: Settings | None = None) -> tuple[GroundednessJudge, RelevanceJudge]:
    """按配置创建两个独立裁判（共用 judge 槽位，各持独立客户端）。"""
    settings = settings if settings is not None else get_settings()
    cfg = settings.llm.judge
    return (
        GroundednessJudge(llm_client=create_client(cfg)),
        RelevanceJudge(llm_client=create_client(cfg)),
    )


__all__ = [
    "split_claims",
    "GroundednessJudge",
    "RelevanceJudge",
    "create_judges",
]
