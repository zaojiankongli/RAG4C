"""本地向量库 + 嵌入缓存：让检索评测在没有 Milvus 的机器上也能跑。

两件事，分开说清楚：

1. ``LocalVectorStore`` 只替代 **Milvus 的检索算术**（余弦 Top-K + 可选分组），
   不替代生产检索管线的任何决策逻辑。它实现的是
   :class:`core.milvus_client.RagMilvusClient` 的 ``hybrid_search`` 端口，所以
   生产管线里"候选放大 → 重排 → 多样性"这一段可以原样被测到。
   稠密分支之外（BM25 / 过滤表达式）这里**明确不支持并直接报错**，
   而不是静默退化——静默退化会让指标看起来漂亮但不可信。

2. ``CachedEmbedder`` 把嵌入结果按内容哈希落盘。真实嵌入是要花钱的，
   同一份语料不该在每次跑评测时重新付费；缓存命中后评测完全离线可复现。
   ``HashingEmbedder`` 则是零依赖的确定性伪嵌入（词哈希 → 向量），
   给 CI / 离线自测用——它只有字面相似性，指标不能直接和真实嵌入比。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

from models.schemas import Chunk, RetrievedChunk

_TOKEN_RE = re.compile(r"[一-龥]|[A-Za-z0-9_]+")


class HashingEmbedder:
    """零依赖确定性伪嵌入：把 token 哈希投到固定维度（用于离线/CI 自测）。

    语义能力只到"字面重叠"这一层，不要拿它的指标跟真实 bge-m3 对比。
    """

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def _vector(self, text: str) -> list[float]:
        vec = np.zeros(self.dim, dtype=np.float32)
        for token in _TOKEN_RE.findall(text.lower()):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest()
            index = int.from_bytes(digest, "big") % self.dim
            vec[index] += 1.0
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec = vec / norm
        return vec.tolist()

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class CachedEmbedder:
    """给任意 embedder 套一层按内容哈希落盘的缓存。

    缓存键 = ``模型名 + 文本 sha1``；语料指纹写在 manifest 里，
    换模型或换语料都会自然分流到不同缓存文件，不会串味。
    """

    def __init__(
        self,
        inner: Any,
        cache_path: Path,
        *,
        model: str = "unknown",
        fingerprint: str = "",
    ) -> None:
        self._inner = inner
        self._path = Path(cache_path)
        self._model = model
        self._fingerprint = fingerprint
        self._cache: dict[str, list[float]] = {}
        self._hits = 0
        self._misses = 0
        self._load()

    # -- 缓存读写 ---------------------------------------------------------
    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return  # 缓存坏了就当没有，重算一遍即可
        if payload.get("model") != self._model or payload.get("fingerprint") != self._fingerprint:
            return
        self._cache = {key: list(value) for key, value in payload.get("vectors", {}).items()}

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "model": self._model,
            "fingerprint": self._fingerprint,
            "vectors": self._cache,
        }
        self._path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    @property
    def stats(self) -> dict[str, int]:
        return {"hits": self._hits, "misses": self._misses}

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha1(text.encode("utf-8")).hexdigest()

    # -- 嵌入 -------------------------------------------------------------
    def embed_texts(self, texts: Sequence[str], *, batch_size: int = 16) -> list[list[float]]:
        pending: list[int] = []
        pending_texts: list[str] = []
        out: list[list[float] | None] = []
        for text in texts:
            key = self._key(text)
            cached = self._cache.get(key)
            if cached is not None:
                self._hits += 1
                out.append(cached)
            else:
                self._misses += 1
                out.append(None)
                pending.append(len(out) - 1)
                pending_texts.append(text)
        for start in range(0, len(pending_texts), batch_size):
            batch = pending_texts[start : start + batch_size]
            vectors = self._inner.embed_texts(batch)
            for offset, vector in enumerate(vectors):
                index = pending[start + offset]
                out[index] = vector
                self._cache[self._key(batch[offset])] = vector
        return [vector for vector in out if vector is not None]

    def embed_query(self, text: str) -> list[float]:
        key = self._key(text)
        cached = self._cache.get(key)
        if cached is not None:
            self._hits += 1
            return cached
        self._misses += 1
        vector = self._inner.embed_query(text)
        self._cache[key] = vector
        return vector


class LocalVectorStore:
    """numpy 余弦检索：只做生产 Milvus 客户端的稠密分支。

    刻意不支持的两件事（不支持就报错，绝不静默）：
    - ``query_text``（BM25 稀疏分支）：本地没有 BM25 实现，假装支持会让
      "混合检索"这个被测对象失真；
    - ``filter_expr``（过滤表达式）：生产是 Milvus 表达式语法，本地实现一套
      子集等于再造一个易错点。
    """

    def __init__(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunks 与 vectors 长度必须一致")
        self._chunks = list(chunks)
        matrix = np.asarray(vectors, dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[0] == 0:
            raise ValueError("vectors 必须是非空的二维矩阵")
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self._matrix = matrix / norms

    def __len__(self) -> int:
        return len(self._chunks)

    def hybrid_search(
        self,
        query_dense: Sequence[float],
        top_k: int,
        query_text: Optional[str] = None,
        group_by_field: Optional[str] = None,
        group_size: Optional[int] = None,
        filter_expr: Optional[str] = None,
        with_cosine: bool = False,
    ) -> list[RetrievedChunk]:
        if query_text:
            raise ValueError("LocalVectorStore 不支持 BM25 稀疏分支（query_text 必须为 None）")
        if filter_expr:
            raise ValueError("LocalVectorStore 不支持过滤表达式（filter_expr 必须为 None）")

        query = np.asarray(query_dense, dtype=np.float32)
        norm = float(np.linalg.norm(query))
        if norm == 0.0:
            return []
        scores = self._matrix @ (query / norm)

        order = np.argsort(-scores)
        per_group: dict[str, int] = {}
        picked: list[int] = []
        for index in order:
            chunk = self._chunks[int(index)]
            if group_by_field:
                key = str(chunk.metadata.get(group_by_field, getattr(chunk, group_by_field, "")))
                limit = group_size or 1
                if per_group.get(key, 0) >= limit:
                    continue
                per_group[key] = per_group.get(key, 0) + 1
            picked.append(int(index))
            if len(picked) >= top_k:
                break

        results: list[RetrievedChunk] = []
        for rank, index in enumerate(picked, start=1):
            score = float(scores[index])
            results.append(
                RetrievedChunk(
                    chunk=self._chunks[index],
                    score=score,
                    rank=rank,
                    branch="hybrid",
                    dense_cosine=score if with_cosine else None,
                )
            )
        return results
