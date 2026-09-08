"""意图路由器：嵌入相似度默认 + LLM 兜底。

组成：
- :class:`EmbeddingRouter`：把查询嵌入后与各路由的示例查询（exemplar）做余弦
  相似度，取每路最大相似度；置信度 = ``max(0, top1 - top2)``（夹到 [0,1]），
  低于阈值则标记 ``degraded=True``（交由 LLM 兜底）。
- :class:`LlmRouterFallback`：LLM 分类兜底，模板为 ``prompts/router_llm_v1.txt``；
  任何失败都返回 ``RouteDecision("hybrid", 0.0, degraded=True)``，不抛错。
- :class:`RouteResolver`：组合两者；嵌入路由不降级时直接返回，降级时尝试 LLM
  兜底并取置信度更高者。LLM 失败时降级为 hybrid。

余弦相似度为 numpy-free 手写实现（dot / norm），numpy 非必需。
"""
from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Any

from core.llm import LLMError, ParseFallbackError
from models.schemas import RouteDecision

# 默认示例查询（中文）：每路 2 条，覆盖典型意图
DEFAULT_EXEMPLARS: dict[str, list[str]] = {
    "hybrid": ["什么是Milvus", "公司的报销流程是什么"],
    "vector_graph_rag": ["谁在什么时间发明了什么", "A和B是什么关系"],
    "full": ["帮我分析一下这个情况", "解释一下这个问题的背景"],
}

VALID_TARGETS: tuple[str, ...] = ("hybrid", "vector_graph_rag", "full")

# LLM 兜底提示词中候选路由的简述
_ROUTE_DESCRIPTIONS: dict[str, str] = {
    "hybrid": "简单的事实型查询，直接关键词或语义匹配即可命中",
    "vector_graph_rag": "多跳关系推理查询，需要结合图结构做多步推理",
    "full": "模棱两可的查询，检索与图推理两条分支都可能有收益",
}

_DEFAULT_THRESHOLD = 0.1


# ---------------------------------------------------------------------------
# 余弦相似度（numpy-free）
# ---------------------------------------------------------------------------

def _cosine(a: list[float], b: list[float]) -> float:
    """两个向量（列表）的余弦相似度，任一为零向量返回 0.0。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


# ---------------------------------------------------------------------------
# 嵌入相似度路由器
# ---------------------------------------------------------------------------

class EmbeddingRouter:
    """基于示例查询嵌入相似度的意图路由器。

    Args:
        embedder: 实现 :class:`core.embedding.EmbeddingService` 协议的嵌入服务
            （``embed_texts`` / ``embed_query``）。
        exemplars: 路由名 -> 示例查询列表。为 None 时使用内置默认示例。
        threshold: 置信度阈值，低于它标记 ``degraded=True``。
    """

    def __init__(
        self,
        embedder: Any,
        exemplars: dict[str, list[str]] | None = None,
        threshold: float = _DEFAULT_THRESHOLD,
    ) -> None:
        self.embedder = embedder
        self.exemplars = exemplars if exemplars is not None else dict(DEFAULT_EXEMPLARS)
        self.threshold = threshold
        self._exemplar_vectors: dict[str, list[list[float]]] | None = None
        # 示例向量只该被算一次。没有这把锁时，冷启动瞬间涌进来的 N 路并发
        # 查询会各自看到 ``_exemplar_vectors is None``，于是**每一路**都把全
        # 部示例嵌入一遍——正好发生在进程刚起来、最没有余量的时刻。
        self._exemplar_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    def _ensure_exemplar_vectors(self) -> dict[str, list[list[float]]]:
        """惰性嵌入示例查询（仅首次路由时计算并缓存）。

        全部示例合成**一次** ``embed_texts``。从前是按路由循环、每路一次，
        默认三路就是三次串行嵌入往返，而它们之间没有任何数据依赖——纯粹
        是"按 dict 遍历"这个写法带来的开销。它落在每个进程的第一次查询上，
        而且配置热更新会 ``reset_pipeline`` 重建组件，于是每改一次配置就
        重新付一遍。

        合并的代价是对齐：分路调用时某一路少返回几条只会让该路少几个示例，
        合成一批之后少一条就会让**切分点错位**，A 路由拿到 B 路由的示例
        向量——路由从此静默地指向错误的分支。所以下面那条长度校验是这次
        合并的前提，不是防御性编程。
        """
        cached = self._exemplar_vectors
        if cached is not None:
            return cached
        with self._exemplar_lock:
            if self._exemplar_vectors is not None:
                return self._exemplar_vectors
            routes = [r for r, texts in self.exemplars.items() if texts]
            flat = [t for r in routes for t in self.exemplars[r]]
            vectors: dict[str, list[list[float]]] = {r: [] for r in self.exemplars}
            if not flat:
                self._exemplar_vectors = vectors
                return vectors
            embedded = self.embedder.embed_texts(flat)
            if len(embedded) != len(flat):
                # 半成品不入缓存：错位的示例向量会把路由永久钉在错误分支上，
                # 而"这次没算成"下一次重算即可（此时各路皆为空向量，置信度
                # 为 0 -> degraded -> 交给 LLM 兜底，与嵌入不可用时同一条路）。
                return vectors
            offset = 0
            for route in routes:
                count = len(self.exemplars[route])
                vectors[route] = embedded[offset : offset + count]
                offset += count
            self._exemplar_vectors = vectors
        return vectors

    # ------------------------------------------------------------------ #
    def prewarm(self) -> bool:
        """提前把示例向量算好，让第一次查询不必替所有人付这笔钱。

        返回是否已就绪。失败不抛：预热是锦上添花，嵌入服务此刻不可用只该
        让首次查询回到惰性计算那条路，不该拖累进程启动。
        """
        try:
            return bool(self._ensure_exemplar_vectors())
        except Exception:  # noqa: BLE001 - 预热失败等于没预热
            return False

    # ------------------------------------------------------------------ #
    def route(self, query: str) -> RouteDecision:
        """路由决策：目标路由 + 置信度 + 是否降级。

        每路分数 = 查询与改路所有示例的最大余弦相似度；
        置信度 = ``max(0.0, top1 - top2)``，夹到 [0, 1]。
        """
        vectors = self._ensure_exemplar_vectors()
        query_vec = self.embedder.embed_query(query)

        scores: list[tuple[float, str]] = []
        for route, vecs in vectors.items():
            best = max((_cosine(query_vec, v) for v in vecs), default=-1.0)
            scores.append((best, route))
        # 按相似度降序（并列时保持路由声明顺序，保证确定性）
        scores.sort(key=lambda item: item[0], reverse=True)

        top1, top2 = (scores + [(-1.0, "hybrid")] * 2)[:2]
        confidence = max(0.0, min(1.0, top1[0] - top2[0]))
        degraded = confidence < self.threshold
        return RouteDecision(
            target=top1[1],
            confidence=confidence,
            degraded=degraded,
        )


# ---------------------------------------------------------------------------
# LLM 兜底路由器
# ---------------------------------------------------------------------------

class LlmRouterFallback:
    """LLM 意图分类兜底（仅在嵌入路由低置信度时被调用）。

    Args:
        llm_client: 实现 ``chat_json`` 的客户端（:class:`core.llm.LLMClient` 或桩）。
        template_path: 路由提示词模板路径（``prompts/router_llm_v1.txt``）。
    """

    def __init__(self, llm_client: Any, template_path: str | Path) -> None:
        self.llm_client = llm_client
        self.template_path = Path(template_path)
        self._template: str | None = None

    # ------------------------------------------------------------------ #
    def _load_template(self) -> str:
        if self._template is None:
            self._template = self.template_path.read_text(encoding="utf-8")
        return self._template

    @staticmethod
    def _fallback_decision() -> RouteDecision:
        """LLM 不可用时的安全默认。"""
        return RouteDecision(target="hybrid", confidence=0.0, degraded=True)

    # ------------------------------------------------------------------ #
    def route(self, query: str) -> RouteDecision:
        """LLM 路由决策；任何失败都降级为 ``hybrid``，永不抛错。"""
        try:
            template = self._load_template()
            candidate_routes = "\n".join(
                f"- {name}：{desc}" for name, desc in _ROUTE_DESCRIPTIONS.items()
            )
            prompt = (
                template.replace("{query}", query)
                .replace("{candidate_routes}", candidate_routes)
            )
            messages: list[dict[str, str]] = [
                {"role": "system", "content": "你是企业 RAG 系统的意图路由器，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ]
            data = self.llm_client.chat_json(
                messages,
                schema_hint='{"route": str, "confidence": float, "reason": str}',
            )
        except (LLMError, ParseFallbackError, OSError):
            return self._fallback_decision()

        if not isinstance(data, dict):
            return self._fallback_decision()
        route = data.get("route")
        if route not in VALID_TARGETS:
            return self._fallback_decision()
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        return RouteDecision(target=route, confidence=confidence, degraded=False)


# ---------------------------------------------------------------------------
# 路由解析器（组合）
# ---------------------------------------------------------------------------

class RouteResolver:
    """嵌入路由为主、LLM 兜底为辅的路由解析器。

    Args:
        embedding_router: :class:`EmbeddingRouter`。
        llm_fallback: :class:`LlmRouterFallback`。
        threshold: 可选置信度阈值。为 None 时沿用 embedding_router 的阈值。
    """

    def __init__(
        self,
        embedding_router: EmbeddingRouter,
        llm_fallback: LlmRouterFallback,
        threshold: float | None = None,
    ) -> None:
        self.embedding_router = embedding_router
        self.llm_fallback = llm_fallback
        self.threshold = threshold

    # ------------------------------------------------------------------ #
    def prewarm(self) -> bool:
        """转发给嵌入路由的预热（见 :meth:`EmbeddingRouter.prewarm`）。"""
        return self.embedding_router.prewarm()

    # ------------------------------------------------------------------ #
    def route(self, query: str) -> RouteDecision:
        """路由决策：优先嵌入路由，降级时尝试 LLM 兜底，返回置信度更高者。"""
        decision = self.embedding_router.route(query)
        degraded = decision.degraded
        if self.threshold is not None:
            degraded = decision.confidence < self.threshold

        if not degraded:
            return decision

        try:
            fallback = self.llm_fallback.route(query)
        except Exception:
            # LLM 兜底意外失败：降级为 hybrid，永不向上抛
            return RouteDecision(target="hybrid", confidence=0.0, degraded=True)

        if fallback.confidence >= decision.confidence:
            return fallback
        return decision


__all__ = [
    "DEFAULT_EXEMPLARS",
    "VALID_TARGETS",
    "EmbeddingRouter",
    "LlmRouterFallback",
    "RouteResolver",
]
