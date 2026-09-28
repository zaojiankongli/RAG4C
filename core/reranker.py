"""BGE-Reranker-v2-M3 重排序服务。

实现两个实现：
- :class:`BgeReranker`：本地 FlagEmbedding 的 FlagReranker。
  FlagEmbedding 为**可选依赖**（惰性导入）。
- :class:`LlmReranker`：可插拔的 LLM 重排槽位——调用配置好的 LLM
  （通常是 judge 或 generation 槽位）对候选做 listwise 打分，
  返回与候选等长的分数列表。

两者都实现 :class:`Reranker` 协议：``rerank(query, candidates) -> list[float]``，
分数与 ``candidates`` 对齐（同序）。
"""
from __future__ import annotations

import threading
from typing import Any, Protocol, runtime_checkable

from models.schemas import Chunk
from core.llm import LLMClient, LLMError, ParseFallbackError
from core.providers import ProviderRegistry
from core.retry import RetryPolicy, retry_call
from core.endpoint_probe import assert_endpoint_reachable


def _retry_policy() -> RetryPolicy:
    """重排调用的重试策略（沿用全局 retry 配置）。

    这里不做异常类型细分：重排走的是裸 HTTP（httpx / requests），传输层异常
    本身就基本都是瞬态的，而业务层错误（401 / 4xx）根本不以异常形式出现——
    它们是带状态码的正常响应，由调用处按状态码分支处理，压根到不了重试逻辑。
    """
    from config.settings import get_settings

    return RetryPolicy.from_settings(get_settings().retry)


@runtime_checkable
class Reranker(Protocol):
    """重排序协议（后续检索 / 生成模块依赖此接口）。"""

    def rerank(self, query: str, candidates: list[Chunk]) -> list[float]: ...


class RerankError(RuntimeError):
    """重排序失败。"""


class RerankDependencyError(RerankError):
    """缺少可选依赖（FlagEmbedding）。"""


class BgeReranker:
    """本地 BGE-reranker-v2-m3 重排序。

    ``FlagReranker`` 惰性加载，首次 ``rerank`` 时才实例化（含权重下载）。
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        device: str = "cpu",
        use_fp16: bool = False,
        batch_size: int = 32,
        normalize_score: bool = True,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.use_fp16 = use_fp16
        self.batch_size = batch_size
        self.normalize_score = normalize_score
        self._model: Any = None  # FlagReranker，惰性
        self._model_lock = threading.Lock()
        self._inference_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is not None:
                return self._model
            try:
                from FlagEmbedding import FlagReranker
            except ImportError as exc:
                raise RerankDependencyError(
                    "FlagEmbedding 未安装。请执行 `pip install 'rag4c[embedding]'`。"
                ) from exc
            kwargs: dict[str, Any] = {"use_fp16": self.use_fp16}
            try:
                self._model = FlagReranker(self.model_name, device=self.device, **kwargs)
            except TypeError:
                self._model = FlagReranker(self.model_name, devices=[self.device], **kwargs)
            return self._model

    # ------------------------------------------------------------------ #
    def rerank(self, query: str, candidates: list[Chunk]) -> list[float]:
        """对候选列表打分，返回与 ``candidates`` 同序的分数列表。"""
        if not candidates:
            return []
        model = self._ensure_model()
        pairs: list[list[str]] = [[query, c.text] for c in candidates]
        try:
            with self._inference_lock:
                scores = model.compute_score(
                    pairs,
                    normalize=self.normalize_score,
                    batch_size=self.batch_size,
                )
        except Exception as exc:
            raise RerankError(
                f"BGE 重排序失败（model={self.model_name}）: {exc}"
            ) from exc
        if isinstance(scores, (int, float)):
            scores = [scores]  # 单候选时返回标量
        return [float(s) for s in scores]


class LlmReranker:
    """基于 LLM 的 listwise 重排序槽位。

    把 query + 候选列表一次性发给 LLM，要求其返回每个候选的 0~1 分 JSON。
    实现上**保守且显式**：解析失败默认抛错（不静默返回），
    业务方可用 ``fallback_on_error=True`` 选择退化为等分。

    这个类是"可插拔 LLM 重排槽位"的默认实现——后续可以替换为
    pairwise 两两比较或更复杂的 prompt 策略，协议不变。
    """

    DEFAULT_PROMPT = (
        "你是检索结果重排序器。请仅根据相关性对候选片段打分。\n"
        "返回严格 JSON：{{\"scores\": [0.0, 0.5, ...]}}，"
        "每个值在 0（完全不相关）到 1（高度相关）之间，"
        "顺序必须与候选列表一致，数量必须等于候选数 {n}。"
        "不要输出任何其他内容。"
    )

    def __init__(
        self,
        llm: LLMClient,
        batch_size: int = 8,
        prompt_template: str | None = None,
        fallback_on_error: bool = False,
    ) -> None:
        self.llm = llm
        self.batch_size = batch_size
        self.prompt_template = prompt_template or self.DEFAULT_PROMPT
        self.fallback_on_error = fallback_on_error

    # ------------------------------------------------------------------ #
    def rerank(self, query: str, candidates: list[Chunk]) -> list[float]:
        """分 batch 调用 LLM 打分，返回与 ``candidates`` 同序的分数列表。"""
        if not candidates:
            return []
        scores: list[float] = []
        for start in range(0, len(candidates), self.batch_size):
            batch = candidates[start : start + self.batch_size]
            scores.extend(self._rerank_batch(query, batch))
        return scores

    # ------------------------------------------------------------------ #
    def _rerank_batch(self, query: str, batch: list[Chunk]) -> list[float]:
        numbered = "\n".join(f"[{i}] {c.text[:200]}" for i, c in enumerate(batch))
        user_content = (
            f"查询：{query}\n\n候选片段（按编号）：\n{numbered}\n\n"
            f"请输出 JSON：{{\"scores\": [0~1 的 {len(batch)} 个分数]}}"
        )
        messages: list[dict] = [
            {"role": "system", "content": self.prompt_template.format(n=len(batch))},
            {"role": "user", "content": user_content},
        ]
        try:
            data = self.llm.chat_json(messages, schema_hint='{"scores": [float, ...]}')
        except (LLMError, ParseFallbackError) as exc:
            if self.fallback_on_error:
                # 显式退化：等分，不静默返回 None
                return [0.0] * len(batch)
            raise RerankError(f"LLM 重排序失败: {exc}") from exc

        raw_scores = data.get("scores", data.get("score"))
        if not isinstance(raw_scores, list) or len(raw_scores) != len(batch):
            if self.fallback_on_error:
                return [0.0] * len(batch)
            raise RerankError(
                f"LLM 返回的分数数量不符（期望 {len(batch)}，实际 "
                f"{len(raw_scores) if isinstance(raw_scores, list) else '非列表'}）。"
            )
        try:
            return [max(0.0, min(1.0, float(s))) for s in raw_scores]
        except (TypeError, ValueError) as exc:
            if self.fallback_on_error:
                return [0.0] * len(batch)
            raise RerankError(f"LLM 返回的分数不可解析: {exc}") from exc


class ApiReranker:
    """OpenAI 兼容 /rerank API 重排序（硅基流动 BAAI/bge-reranker-v2-m3）。

    - 惰性导入 httpx / requests，仅 rerank 时联网；
    - 请求体 {"model", "query", "documents": [str...], "top_n": n}；
    - 响应 results[{index, relevance_score}] 按 index 对齐，
      返回与 candidates 同序的分数列表；
    - 非 200 / 结果缺失统一抛 RerankError（401 附可操作提示）。
    """

    def __init__(
        self,
        base_url: str = "https://api.siliconflow.cn/v1",
        api_key: str = "",
        model: str = "BAAI/bge-reranker-v2-m3",
        timeout: float = 30.0,
        http_client: Any = None,
    ) -> None:
        self.base_url = (base_url or "https://api.siliconflow.cn/v1").rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._client: Any = http_client  # None 时惰性创建
        self._client_lock = threading.Lock()
        # requests 兜底路径专用：每线程一个 Session（见 _ensure_client）
        self._local = threading.local()

    def _ensure_client(self) -> Any:
        """返回可用的 HTTP 客户端。**httpx 全进程共享，requests 每线程一个。**

        这个区别不是洁癖：``httpx.Client`` 明确声明线程安全，而
        ``requests.Session`` 明确声明**不是**——它的 ``HTTPAdapter`` 连接池
        与 cookie jar 在并发下都会被写坏。而这个重排器是进程级单例
        （挂在 ``get_pipeline()`` 缓存的组件图上），最多 32 个工作线程会同时
        踩进来。共享一个 Session 的后果是偶发的连接复用错乱：报错随机、
        复现不了、还会被 pipeline 静默降级成"不重排"吃掉，只留下"检索质量
        偶尔变差"这种查不动的症状。

        没有选「加锁串行化」：那会把重排变成全局瓶颈，为一条兜底路径牺牲
        主链路并发，代价方向反了。每线程一个 Session 只多占几个连接池，
        线程池本身有上限，不会失控。
        """
        if self._client is not None:
            return self._client

        # 上一轮已判定 httpx 缺席、退到 requests：直接取本线程的 Session
        session = getattr(self._local, "session", None)
        if session is not None:
            return session

        with self._client_lock:
            if self._client is not None:
                return self._client
            try:
                import httpx

                self._client = httpx.Client(timeout=self.timeout)
                return self._client
            except ImportError:
                pass
        # httpx 不可用：走 requests，且**不**写进 self._client（那会让所有
        # 线程共用同一个 Session，正是这里要避免的事）。
        try:
            import requests
        except ImportError as exc:
            raise RerankError(
                "未安装 HTTP 客户端库（httpx 或 requests），"
                "无法调用重排序 API。请执行 pip install httpx。"
            ) from exc
        session = requests.Session()
        self._local.session = session
        return session

    def rerank(self, query: str, candidates: list[Chunk]) -> list[float]:
        """API 打分，返回与 candidates 同序的分数列表。"""
        if not candidates:
            return []
        # 端点不可达时快速失败：重排在检索关键路径上，服务没起的话每次查询
        # 都要白等一整轮重试（与嵌入同因，实测单批 113 秒）。
        assert_endpoint_reachable(self.base_url, RerankError, kind="reranker")
        documents = [c.text for c in candidates]
        payload: dict[str, Any] = {
            "model": self.model,
            "query": query,
            "documents": documents,
            "top_n": len(documents),
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            # 重排原本也是零重试。它的失败还被 retrieval/pipeline.py 静默吞掉
            # 降级为"不重排"，于是一次网络抖动的表现是「检索质量突然变差，
            # 但日志里一切正常」——最难查的那类故障。加重试是第一道，
            # 让它在熔断器里可见是第二道（见 pipeline 侧）。
            #
            # 只重试**传输层**异常。HTTP 4xx/5xx 在下面按状态码分别处理：
            # 401 是配置问题，重试无益；5xx 由 httpx 当正常响应返回而不抛异常，
            # 落不到这个 except 里，所以这里的 retry_on 保持宽泛是安全的。
            resp = retry_call(
                self._ensure_client().post,
                f"{self.base_url}/rerank",
                json=payload,
                headers=headers,
                timeout=self.timeout,
                policy=_retry_policy(),
            )
        except Exception as exc:
            raise RerankError(f"重排序 API 请求失败（{self.base_url}）: {exc}") from exc
        if getattr(resp, "status_code", 200) != 200:
            detail = getattr(resp, "text", "") or ""
            if getattr(resp, "status_code", 0) == 401:
                raise RerankError(
                    f"重排序 API 认证失败（401）：请检查 RAG4C_RERANKER_API_KEY"
                    f"（{self.base_url}）{('，响应: ' + detail[:200]) if detail else ''}"
                )
            raise RerankError(
                f"重排序 API 返回 HTTP {getattr(resp, 'status_code', '?')}"
                f"（{self.base_url}）: {detail[:200]}"
            )
        try:
            body = resp.json()
        except Exception as exc:
            raise RerankError(f"重排序 API 响应不是合法 JSON: {exc}") from exc
        results = body.get("results")
        if not isinstance(results, list):
            raise RerankError(
                f"重排序 API 响应缺少 results 列表: "
                f"{str(body)[:200]}"
            )
        scores = [0.0] * len(documents)
        for item in results:
            if not isinstance(item, dict):
                continue
            idx = int(item.get("index", -1))
            if 0 <= idx < len(scores):
                scores[idx] = float(item.get("relevance_score", 0.0))
        return scores


def _create_api_reranker(settings: Any) -> ApiReranker:
    if not getattr(settings, "api_key", ""):
        raise RerankError(
            "重排序 API 模式需要 RAG4C_RERANKER_API_KEY"
            "（硅基流动免费额度可获取：https://cloud.siliconflow.cn）"
        )
    return ApiReranker(
        base_url=getattr(settings, "api_base_url", "https://api.siliconflow.cn/v1"),
        api_key=settings.api_key,
        model=getattr(settings, "api_model", "BAAI/bge-reranker-v2-m3"),
        timeout=getattr(settings, "api_timeout", 30.0),
    )


def _create_local_reranker(settings: Any) -> BgeReranker:
    return BgeReranker(
        model_name=settings.model,
        device=settings.device,
        use_fp16=settings.use_fp16,
        batch_size=settings.batch_size,
        normalize_score=settings.normalize_score,
    )


RERANKER_PROVIDERS: ProviderRegistry[Any, Reranker] = ProviderRegistry("reranker")
RERANKER_PROVIDERS.register("api", _create_api_reranker)
RERANKER_PROVIDERS.register("local", _create_local_reranker)


def create_reranker(settings: Any) -> Reranker:
    """按 reranker.provider 创建重排序器（api=远程端点 / local=本地模型）。

    - api：需 RAG4C_RERANKER_API_KEY（硅基流动免费额度可用）；
    - local：惰性加载 FlagEmbedding（首次调用下载权重）。
    """
    return RERANKER_PROVIDERS.create(getattr(settings, "provider", "local"), settings)


__all__ = [
    "Reranker",
    "RerankError",
    "RerankDependencyError",
    "BgeReranker",
    "LlmReranker",
    "ApiReranker",
    "RERANKER_PROVIDERS",
    "create_reranker",
]
