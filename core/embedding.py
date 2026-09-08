"""BGE-M3 稠密嵌入服务。

实现两个实现：
- :class:`Bge3LocalEmbedder`：本地 FlagEmbedding 的 BGEM3FlagModel。
  FlagEmbedding 为**可选依赖**（惰性导入），未安装时模块仍可 import。
  且按用户决策：BGE-M3 **只产出稠密向量**，稀疏检索交给 Milvus 内置
  BM25 Function，因此这里不生成任何 sparse 向量。
- :class:`ApiEmbedder`：OpenAI 兼容 /embeddings 端点（可指向 vLLM / Ollama / 网关）。

两者都实现 :class:`EmbeddingService` 协议。
"""
from __future__ import annotations

import threading
from typing import Any, Protocol, runtime_checkable

from config.settings import EmbeddingSettings
from core.embed_cache import get_embed_cache
from core.providers import ProviderRegistry
from core.retry import RetryPolicy, retry_call

# 瞬态异常类缓存。与 core.llm._retry_policy 同样的懒探测套路：openai 未安装时
# 本模块仍要能离线导入（config 校验、单测都会 import 它）。
_RETRY_ON_CACHE: tuple[type[BaseException], ...] | None = None


def _retry_policy() -> RetryPolicy:
    """嵌入调用的重试策略。

    补这个是因为嵌入原本**零重试**——一次网络抖动就让整篇文档的入库作废，
    或者让一次问答直接失败。而它跟 LLM 打的是同一类 OpenAI 兼容端点，
    面对的瞬态失败完全一样，没有理由一个有重试一个没有。

    只重试瞬态类（连接 / 超时 / 限流 / 5xx）。400 参数错误、401 鉴权失败
    重试多少次都是同样的结果，重试只会拖长故障暴露时间。
    """
    global _RETRY_ON_CACHE
    if _RETRY_ON_CACHE is None:
        try:
            from openai import (
                APIConnectionError,
                APITimeoutError,
                InternalServerError,
                RateLimitError,
            )

            _RETRY_ON_CACHE = (
                APIConnectionError,
                APITimeoutError,
                RateLimitError,
                InternalServerError,
            )
        except ImportError:  # 未装 openai：实际调用会先抛依赖错误，这里仅防御
            _RETRY_ON_CACHE = (Exception,)
    from config.settings import get_settings

    policy = RetryPolicy.from_settings(get_settings().retry)
    policy.retry_on = _RETRY_ON_CACHE
    return policy


@runtime_checkable
class EmbeddingService(Protocol):
    """嵌入服务协议（后续索引 / 检索模块依赖此接口）。"""

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class EmbeddingError(RuntimeError):
    """嵌入调用失败。"""


class EmbeddingDependencyError(EmbeddingError):
    """缺少可选依赖（FlagEmbedding 或 openai）。"""


class Bge3LocalEmbedder:
    """本地 BGE-M3 稠密嵌入。

    ``BGEM3FlagModel`` 惰性加载，首次 ``embed_*`` 调用时才实例化（含权重下载）。
    因此未安装 FlagEmbedding / torch、或不联网时，本类可以安全地创建。

    构造参数使用 BGEM3FlagModel 调用约定：
    ``return_dense=True``、``return_sparse=False``、``return_colbert_vecs=False``，
    仅取稠密向量。
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-m3",
        device: str = "cpu",
        use_fp16: bool = False,
        max_length: int = 8192,
        batch_size: int = 32,
        normalize_embeddings: bool = False,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.use_fp16 = use_fp16
        self.max_length = max_length
        self.batch_size = batch_size
        self.normalize_embeddings = normalize_embeddings
        self._model: Any = None  # BGEM3FlagModel，惰性
        self._model_lock = threading.Lock()
        self._inference_lock = threading.Lock()

    @classmethod
    def from_settings(cls, cfg: EmbeddingSettings) -> "Bge3LocalEmbedder":
        """由 EmbeddingSettings 构造（provider=local 时使用）。"""
        return cls(
            model_name=cfg.model,
            device=cfg.device,
            use_fp16=cfg.use_fp16,
            max_length=cfg.max_length,
            batch_size=cfg.batch_size,
            normalize_embeddings=cfg.normalize_embeddings,
        )

    # ------------------------------------------------------------------ #
    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is not None:
                return self._model
            try:
                from FlagEmbedding import BGEM3FlagModel
            except ImportError as exc:
                raise EmbeddingDependencyError(
                    "FlagEmbedding 未安装。请执行 `pip install 'rag4c[embedding]'` "
                    "（会同时安装 torch / transformers）。"
                ) from exc
            kwargs: dict[str, Any] = {"use_fp16": self.use_fp16}
            try:
                self._model = BGEM3FlagModel(self.model_name, device=self.device, **kwargs)
            except TypeError:
                # 不同 FlagEmbedding 版本构造参数不一致（devices vs device），降级重试
                self._model = BGEM3FlagModel(self.model_name, devices=[self.device], **kwargs)
            return self._model

    # ------------------------------------------------------------------ #
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """批量嵌入，返回 ``[text1_vec, text2_vec, ...]``。

        仅返回稠密向量（dense）；sparse / colbert 一律关闭，
        稀疏检索由 Milvus 内置 BM25 Function 处理。
        """
        if not texts:
            return []
        model = self._ensure_model()
        try:
            # FlagEmbedding/torch model objects are not guaranteed re-entrant.
            with self._inference_lock:
                outputs = model.encode(
                    texts,
                    batch_size=self.batch_size,
                    max_length=self.max_length,
                    return_dense=True,
                    return_sparse=False,
                    return_colbert_vecs=False,
                    normalize_embeddings=self.normalize_embeddings,
                )
        except Exception as exc:
            raise EmbeddingError(
                f"BGE-M3 批量嵌入失败（model={self.model_name}）: {exc}"
            ) from exc
        dense = getattr(outputs, "dense_vecs", None)
        if dense is None:
            raise EmbeddingError("BGEM3FlagModel 未返回 dense_vecs。")
        return [[float(x) for x in vec] for vec in dense]

    def embed_query(self, text: str) -> list[float]:
        """单条查询嵌入（与 embed_texts 使用同一模型，带两级缓存）。

        本地模型没有 API 账单，但有比 API 更贵的东西：一次 BGE-M3 前向在 CPU 上
        是几十到几百毫秒，而且 :attr:`_inference_lock` 是全局串行的——并发下它
        直接就是吞吐瓶颈。省掉的每一次嵌入都是从这把锁上让出的时间。

        ``variant`` 里带上归一化开关与 max_length：它们会改变同一模型对同一段
        文本的输出，不进键的话，改配置之后拿到的就是按旧配置算出来的向量，
        既不报错也查不出来。
        """
        cache = get_embed_cache()
        variant = f"norm={int(self.normalize_embeddings)},len={self.max_length}"
        hit = cache.get(self.model_name, text, variant=variant)
        if hit is not None:
            return hit

        vec = self.embed_texts([text])[0]
        cache.put(self.model_name, text, vec, variant=variant)
        return list(vec)


class ApiEmbedder:
    """OpenAI 兼容 /embeddings 端点嵌入。

    通过 openai SDK 请求 ``{base_url}/embeddings``，适合把嵌入服务化部署
    （vLLM / TEI / 各类网关）。
    """

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str = "",
        timeout: float = 120.0,
        batch_size: int = 32,
    ) -> None:
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
        # 单次请求的文本条数上限；过大易触发服务端 token / 体积限制
        self.batch_size = max(1, batch_size)
        self._client: Any = None  # openai.OpenAI，惰性
        self._client_lock = threading.Lock()

    @classmethod
    def from_settings(cls, cfg: EmbeddingSettings) -> "ApiEmbedder":
        """由 EmbeddingSettings 构造（provider=api 时使用）。"""
        return cls(
            model=cfg.api_model,
            base_url=cfg.api_base_url,
            api_key=cfg.api_key,
            timeout=cfg.api_timeout,
            batch_size=cfg.batch_size,
        )

    # ------------------------------------------------------------------ #
    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is not None:
                return self._client
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise EmbeddingDependencyError(
                    "openai 未安装。请执行 `pip install openai>=1.40`。"
                ) from exc
            try:
                self._client = OpenAI(
                    base_url=self.base_url or None,
                    api_key=self.api_key or "not-set",
                    timeout=self.timeout,
                )
            except Exception as exc:
                raise EmbeddingError(
                    f"初始化嵌入客户端失败 base_url={self.base_url!r}: {exc}"
                ) from exc
            return self._client

    # ------------------------------------------------------------------ #
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """批量嵌入（按 ``batch_size`` 分批请求，保持输入顺序）。

        分批的必要性：入库时一篇长文档可能有上千个 chunk，且开启上下文
        增强后每条文本还会更长。一次性把全部文本塞进单个请求，极易触发
        服务端的单请求 token / 体积上限，而且一旦失败整篇文档的嵌入全部
        作废，没有任何部分进展。
        """
        if not texts:
            return []
        client = self._ensure_client()
        policy = _retry_policy()
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            try:
                resp = retry_call(
                    client.embeddings.create,
                    model=self.model,
                    input=batch,
                    policy=policy,
                )
            except Exception as exc:
                raise EmbeddingError(
                    f"API 嵌入失败（model={self.model}, base_url={self.base_url}，"
                    f"已完成 {len(vectors)}/{len(texts)} 条）: {exc}"
                ) from exc
            # OpenAI 响应按输入顺序返回
            vectors.extend([float(x) for x in item.embedding] for item in resp.data)
        return vectors

    def embed_query(self, text: str) -> list[float]:
        """单条查询嵌入（带两级缓存：进程内 LRU + Redis）。

        加缓存是因为**同一次问答里同一段文本会被嵌两遍**：路由器要嵌一次
        ``search_query`` 决定走哪条路（retrieval/router.py），主检索紧接着又把
        同一个字符串嵌一次（retrieval/pipeline.py）。两次远程往返，拿回完全
        相同的向量。默认开着复杂度门控，这条路径每次提问都会走。

        没有选择"把路由器算出的向量传给检索层"，虽然那样更省事：那要求两处
        对「嵌的到底是哪个字符串」保持一致（hyde 开启时检索层嵌的是假设文档，
        不是查询），一旦哪天有人改了其中一处，传下去的就是个**错的向量**，
        而且不会报错，只会让召回悄悄变差。缓存按文本内容命中，天然不存在
        这个问题：嵌的文本不同就是不同的键。

        缓存搬到了 :mod:`core.embed_cache`（进程内 LRU 之下再垫一层 Redis），
        于是多吃两类命中：另一个副本问过的、以及本进程重启之前问过的。键里带
        模型名，所以换模型不会串味——这也是它可以做成进程级单例、不必跟着
        embedder 一起被热更新重建的原因。
        """
        cache = get_embed_cache()
        hit = cache.get(self.model, text)
        if hit is not None:
            return hit

        vec = self.embed_texts([text])[0]
        cache.put(self.model, text, vec)
        return list(vec)


EMBEDDING_PROVIDERS: ProviderRegistry[EmbeddingSettings, EmbeddingService] = (
    ProviderRegistry("embedding")
)
EMBEDDING_PROVIDERS.register("local", Bge3LocalEmbedder.from_settings)
EMBEDDING_PROVIDERS.register("api", ApiEmbedder.from_settings)


def create_embedder(cfg: EmbeddingSettings) -> EmbeddingService:
    """按配置的 provider 创建嵌入服务。

    Args:
        cfg: EmbeddingSettings。

    Returns:
        返回 :class:`Bge3LocalEmbedder` 或 :class:`ApiEmbedder`。
        两者都符合 :class:`EmbeddingService` 协议。

    Raises:
        ValueError: provider 非法。
    """
    return EMBEDDING_PROVIDERS.create(cfg.provider, cfg)


__all__ = [
    "EmbeddingService",
    "EmbeddingError",
    "EmbeddingDependencyError",
    "Bge3LocalEmbedder",
    "ApiEmbedder",
    "EMBEDDING_PROVIDERS",
    "create_embedder",
]
