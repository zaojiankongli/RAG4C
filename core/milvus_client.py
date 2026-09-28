"""Milvus 客户端封装（Milvus Lite 文件模式 + Milvus server 模式）。

关键设计（pymilvus **v3.0.x** API，已对照官方文档核实）：
- **BM25 稀疏检索由 Milvus 内置 Function 完成**：在集合创建时声明一个
  ``FunctionType.BM25`` 的 Function，输入是 ``text`` 字段，输出是
  ``sparse_vector``（SPARSE_FLOAT_VECTOR）。应用层**不生成**任何 sparse 向量，
  insert 时只需要 text，Milvus 自动在写路径上计算 BM25 稀疏向量。
- **混合检索**用 ``MilvusClient.hybrid_search``，两个 ``AnnSearchRequest``：
    1. 稠密请求：``anns_field="dense_vector"``，``param={"metric_type": ...}``
    2. BM25 请求：``anns_field="sparse_vector"``（Function 输出字段），
       ``param={"metric_type": "BM25"}``，``data`` 直接放**查询文本**
  融合排序用 ``Function(function_type=FunctionType.RERANK,
  params={"reranker": "rrf"})``——3.0 把重排统一收进了 Function 体系，
  这是官方文档现在给出的写法；旧的 ``RRFRanker`` 类仍在 SDK 里，但已被
  取代。
- 运行模式由 ``uri`` 自动判断：包含 ``://`` 视为 server 模式
  （http://localhost:19530）；否则视为 Milvus Lite 文件模式（./rag4c.db）。
- ``group_by_field`` / ``group_size`` / ``strict_group_size`` 通过
  ``hybrid_search`` 的 **kwargs 透传。
- 所有 Milvus 调用错误统一包装为 :class:`RagMilvusError`。
- 对 **server 模式**的搜索 / 读取（``hybrid_search`` / ``get_chunks_by_ids``）
  做**指数退避重试**：连接错误 / 服务不可用等瞬态异常自动重试，策略来自
  ``config.settings.retry``；lite 模式（本地 .db 文件）失败多为确定性错误，
  不重试（max_attempts=1）。

pymilvus 为**可选依赖**（惰性导入），未安装时本模块可正常 import。
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import threading
from typing import Any, Iterable, Optional, Sequence

from config.settings import MilvusSettings, get_settings
from core.observability import get_logger
from core.retry import RetryPolicy, retry_call
from core.vectors import safe_cosine
from models.schemas import Chunk, RetrievedChunk


class RagMilvusError(RuntimeError):
    """Milvus 相关错误（连接 / 集合 / 读写 / 检索）。"""


#: 写入分批大小。单行含全文 + metadata + 1024 维向量，约 4-8KB；
#: 512 行一批约 2-4MB，稳妥落在 gRPC 默认消息上限内，且失败时
#: 已写入的批次是真实进展，不会整篇回退。
_WRITE_BATCH_SIZE = 512


# ---------------------------------------------------------------------------
# 时间转换助手：Milvus 无原生 datetime，统一存 INT64 毫秒时间戳（UTC）
# ---------------------------------------------------------------------------

def _dt_to_ms(dt: datetime) -> int:
    """datetime -> Unix 毫秒（缺失时区按 UTC 处理）。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _ms_to_dt(ms: int) -> datetime:
    """Unix 毫秒 -> UTC datetime。"""
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def _clean_str(value: Any) -> str:
    """Milvus VARCHAR 无法存 None，统一用空串占位；读取时还原为 None。"""
    if value is None:
        return ""
    return str(value)


def _optional_str(value: Any) -> Optional[str]:
    """读取时把空串还原为 None。"""
    s = _clean_str(value)
    return s if s else None


def _quote_expr_str(value: str) -> str:
    """把字符串值转成 Milvus 过滤表达式里的**带引号字面量**。

    引号由本函数负责，调用点不要再自己拼 ``"{...}"`` 或 ``'{...}'``。

    历史教训：上一版叫 ``_escape_expr_str``，只转义 ``\\`` 和 ``'``，
    由调用点自己加引号——而 7 个调用点里有 6 个加的是**双引号**。于是
    ``tenant_id`` 里的一个 ``"`` 就能闭合字面量并注入布尔子句：

        输入   zzz" or tenant_id != "zzz
        生成   tenant_id == "zzz" or tenant_id != "zzz"   ← 恒真，匹配所有租户

    这条路径可经 ``/api/query`` 跨租户读，也可经 ``delete_by_doc_id``
    跨租户删。把引号收进函数内部，是为了让"转义与引号不匹配"这种错误
    在结构上不可能再发生，而不是依赖每个调用点记得配对。

    与 :func:`core.graph_store._quote_string` 行为一致（同一套语义两处实现，
    改动时请同步）。
    """
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ---------------------------------------------------------------------------
# Milvus 重试策略（惰性探测 pymilvus 异常类，保持离线可导入）
# ---------------------------------------------------------------------------

_MILVUS_RETRY_ON: tuple | None = None


def _milvus_retry_policy(lite: bool) -> "RetryPolicy":
    """构造 Milvus 重试策略（惰性探测 pymilvus 异常类，保持离线可导入）。

    lite 模式（本地 .db 文件）失败多为确定性错误，不重试（max_attempts=1）；
    server 模式（网络）对连接错误 / 服务不可用做指数退避重试。

    参数校验错误（如 top_k<1）在进入 try 块前就抛 RagMilvusError，
    永远不会进入重试路径。
    """
    global _MILVUS_RETRY_ON
    if _MILVUS_RETRY_ON is None:
        try:
            from pymilvus.exceptions import (
                ConnectError,
                MilvusException,
                MilvusUnavailableException,
            )
            _MILVUS_RETRY_ON = (
                ConnectError,
                MilvusException,
                MilvusUnavailableException,
            )
        except ImportError:  # pymilvus 未安装：退化重试所有 Exception
            _MILVUS_RETRY_ON = (Exception,)
    if lite:
        return RetryPolicy(
            max_attempts=1, base_delay=0.5, max_delay=30.0,
            jitter=0.1, backoff_factor=2.0, retry_on=_MILVUS_RETRY_ON,
        )
    policy = RetryPolicy.from_settings(get_settings().retry)
    policy.retry_on = _MILVUS_RETRY_ON
    return policy


def _observe_retry_delay(attempt: int, exc: BaseException, delay: float) -> None:
    """on_retry 埋点：把退避延迟（秒）记入 metrics 直方图（惰性导入）。"""
    from core.metrics import get_metrics

    get_metrics().observe("retry.milvus.delay", delay)


class RagMilvusClient:
    """RAG 系统对 Milvus 的专用封装。

    用法:
        client = RagMilvusClient(settings.milvus)
        client.ensure_collection()
        ids = client.insert_chunks(chunks, dense_vectors=vectors)
        results = client.hybrid_search(query_dense=vec, query_text="...", top_k=8)

    Attributes:
        config: MilvusSettings。
        mode: ``"lite"`` 或 ``"server"``（由 uri 自动判定）。
    """

    # 检索时返回的标量字段
    _OUTPUT_FIELDS = [
        "chunk_id",
        "doc_id",
        "text",
        "text_hash",
        "created_at",
        "updated_at",
        "source",
        "parent_chunk_id",
        "acl",
        "tenant_id",
        # dataset_id 必须回读：它是写入时的知识库维度，若只写不读，
        # 任何"读回 Chunk -> 再 upsert"的往返都会把这个字段悄悄清空。
        "dataset_id",
        "document_revision",
        "content_revision",
        "metadata",
    ]

    def __init__(self, config: MilvusSettings) -> None:
        self.config = config
        self._client: Any = None  # pymilvus.MilvusClient，惰性连接
        self._client_lock = threading.Lock()
        self.mode: str = "server" if "://" in config.uri else "lite"

    # ------------------------------------------------------------------ #
    # 连接
    # ------------------------------------------------------------------ #
    @property
    def is_lite(self) -> bool:
        """是否为 Milvus Lite（本地 .db 文件）模式。"""
        return self.mode == "lite"

    @property
    def is_server(self) -> bool:
        """是否为 Milvus server / Zilliz Cloud 模式。"""
        return self.mode == "server"

    def connect(self) -> None:
        """建立连接（幂等）。

        注意：MilvusClient(uri=...) 本身不发起网络请求，
        连接在首次操作时懒建立，因此**不会**在此处抛连接错误。
        """
        self._ensure_client()

    def close(self) -> None:
        """关闭底层连接（如适用）。"""
        with self._client_lock:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:
                    pass
                self._client = None

    def _ensure_client(self) -> Any:
        """惰性创建底层 MilvusClient。"""
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is not None:
                return self._client
            try:
                from pymilvus import MilvusClient
            except ImportError as exc:
                raise RagMilvusError(
                    "pymilvus 未安装。请安装与 Milvus 服务端版本线对齐的客户端："
                    'Milvus 3.0 服务端对应 `pip install "pymilvus>=3.0,<4"`；'
                    "2.x 服务端则装对应的 2.x 客户端。"
                    "版本不匹配会报 “this version of sdk is incompatible with server”。"
                    "另注：[milvus-lite] 附加依赖仅本地文件模式需要，且不支持 Windows。"
                ) from exc
            kwargs: dict[str, Any] = {
                "uri": self.config.uri,
                "timeout": self.config.timeout,
            }
            if self.config.token:
                kwargs["token"] = self.config.token
            if self.config.db_name:
                kwargs["db_name"] = self.config.db_name
            try:
                self._client = MilvusClient(**kwargs)
            except Exception as exc:
                raise RagMilvusError(
                    f"创建 MilvusClient 失败（uri={self.config.uri!r}, mode={self.mode}）: {exc}"
                ) from exc
            return self._client

    # ------------------------------------------------------------------ #
    # 集合 Schema 与索引
    # ------------------------------------------------------------------ #
    def _resolve_analyzer_params(self) -> Optional[dict[str, Any]]:
        """把 ``milvus.bm25_analyzer`` 解析成 Milvus 的 ``analyzer_params``。

        Returns:
            要下发的 analyzer_params；``None`` 表示不下发（沿用 Milvus 的
            standard 分析器）。

        取值规则见 :class:`config.settings.MilvusSettings.bm25_analyzer`。
        无法识别的值一律**抛错而不是静默回退**：回退到 standard 会让中文
        语料的 BM25 悄悄失效，而这类失效不报错、只表现为检索质量下降，
        是最难被发现的一类故障，值得在建集合时就拦住。
        """
        raw = (getattr(self.config, "bm25_analyzer", "") or "").strip()
        if not raw or raw.lower() == "standard":
            return None
        if raw.lower() == "chinese":
            return {"type": "chinese"}
        # 其余按 JSON 原样下发，支持自定义分词器 / 过滤器组合
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RagMilvusError(
                f"milvus.bm25_analyzer 无法识别: {raw!r}。"
                '应为 "chinese" / "standard"，或一段 analyzer_params JSON，'
                '例如 \'{"tokenizer": "jieba", "filter": ["lowercase"]}\'。'
            ) from exc
        if not isinstance(parsed, dict):
            raise RagMilvusError(
                f"milvus.bm25_analyzer 的 JSON 必须是对象，实际是 {type(parsed).__name__}: {raw!r}"
            )
        return parsed

    def _build_schema(self, client: Any) -> Any:
        """构建集合 Schema（含 BM25 Function，pymilvus v3.0.x API）。"""
        from pymilvus import DataType, Function, FunctionType

        schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field(field_name="chunk_id", datatype=DataType.VARCHAR, is_primary=True, max_length=512)
        schema.add_field(field_name="doc_id", datatype=DataType.VARCHAR, max_length=512)
        # BM25 的输入文本字段：必须 enable_analyzer=True（分词器）
        #
        # analyzer_params 同样不可省。只写 enable_analyzer=True 会落到 Milvus
        # 的 standard 分析器（按空白 / 标点切词），中文没有词间空白，整句切不开，
        # 于是中文查询的 BM25 召回恒为 0 —— 稀疏那一路静默失效，混合检索退化
        # 成纯稠密检索，且不报任何错。分词器由 milvus.bm25_analyzer 决定。
        text_field_kwargs: dict[str, Any] = {"enable_analyzer": True}
        analyzer_params = self._resolve_analyzer_params()
        if analyzer_params is not None:
            text_field_kwargs["analyzer_params"] = analyzer_params
        schema.add_field(
            field_name="text",
            datatype=DataType.VARCHAR,
            max_length=self.config.text_max_length,
            **text_field_kwargs,
        )
        schema.add_field(field_name="text_hash", datatype=DataType.VARCHAR, max_length=64)
        # 时间存 INT64（Unix 毫秒，UTC）
        schema.add_field(field_name="created_at", datatype=DataType.INT64)
        schema.add_field(field_name="updated_at", datatype=DataType.INT64)
        schema.add_field(field_name="source", datatype=DataType.VARCHAR, max_length=512)
        schema.add_field(field_name="parent_chunk_id", datatype=DataType.VARCHAR, max_length=512)
        schema.add_field(field_name="acl", datatype=DataType.VARCHAR, max_length=4096)
        # 多租户隔离维度：单集合 + tenant_id 元数据过滤（检索 / 入库强制按租户过滤）
        schema.add_field(field_name="tenant_id", datatype=DataType.VARCHAR, max_length=128)
        # 知识库维度（tenant -> dataset -> document -> chunk）：标量字段利于过滤性能
        schema.add_field(field_name="dataset_id", datatype=DataType.VARCHAR, max_length=128)
        schema.add_field(field_name="document_revision", datatype=DataType.INT64)
        schema.add_field(field_name="content_revision", datatype=DataType.INT64)
        schema.add_field(field_name="metadata", datatype=DataType.JSON)

        # 稠密向量（BGE-M3）
        schema.add_field(
            field_name="dense_vector",
            datatype=DataType.FLOAT_VECTOR,
            dim=self.config.dim,
        )
        # BM25 Function 的输出字段：稀疏向量（应用层不写它，Milvus 自动计算）
        schema.add_field(field_name="sparse_vector", datatype=DataType.SPARSE_FLOAT_VECTOR)

        bm25_function = Function(
            name="bm25_text_search",
            function_type=FunctionType.BM25,
            input_field_names=["text"],
            output_field_names="sparse_vector",
        )
        schema.add_function(bm25_function)
        return schema

    def _build_index_params(self, client: Any) -> Any:
        """构建稠密 + 稀疏索引（pymilvus v3.0.x API）。"""
        index_params: Any = client.prepare_index_params()
        # 稠密向量索引：按 index_type 选择参数
        params: dict[str, Any]
        itype = self.config.index_type.upper()
        if itype in ("HNSW",):
            params = {"M": self.config.m, "efConstruction": self.config.ef_construction}
        elif itype in ("IVF_FLAT", "IVF_SQ8", "IVF_PQ"):
            params = {"nlist": self.config.nlist}
        else:  # AUTOINDEX / FLAT / BRUTE_FORCE
            params = {}
        index_params.add_index(
            field_name="dense_vector",
            index_type=itype,
            metric_type=self.config.metric_type,
            params=params,
        )
        # 稀疏向量：SPARSE_INVERTED_INDEX + BM25 度量
        # 参数以 Milvus 稀疏索引文档为准，仅三项：
        # inverted_index_algo / bm25_k1 / bm25_b，且都属于**索引参数**
        # （不是 Function 参数，也不是集合属性）。
        #
        # 这里不下发 drop_ratio_build：3.0 的稀疏索引参数表里没有它。
        # （能明确查证的废弃项是 SPARSE_WAND —— 自 2.5.4 起由
        # inverted_index_algo="DAAT_WAND" 取代；drop_ratio_build 本身是否
        # 被正式标记废弃我没查到一手依据，只是它已不在参数表内，留着容易
        # 让人误以为调它有用。）
        # 注意区分：drop_ratio_search 是**检索期**参数，仍然有效。
        #
        # 待实测确认：Milvus 3.0 新增的 SINDI 稀疏引擎是否为
        # inverted_index_algo 引入了新的取值 —— 目前该字段默认
        # DAAT_MAXSCORE（BM25 度量下的默认算法），跑通联调后可复核。
        index_params.add_index(
            field_name="sparse_vector",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
            params={
                "inverted_index_algo": self.config.sparse_index_algo,
                "bm25_k1": self.config.bm25_k1,
                "bm25_b": self.config.bm25_b,
            },
        )
        return index_params

    def _migrate_schema(self, client: Any) -> None:
        """幂等补齐存量集合缺失的标量字段。"""
        from pymilvus import DataType

        try:
            desc = client.describe_collection(self.config.collection_name)
        except Exception:
            return
        fields = {field.get("name"): field for field in (desc.get("fields") or [])}
        additions = (
            ("dataset_id", DataType.VARCHAR, {"max_length": 128, "default_value": ""}),
            ("document_revision", DataType.INT64, {"default_value": 0}),
            ("content_revision", DataType.INT64, {"default_value": 0}),
        )
        for field_name, data_type, options in additions:
            if field_name in fields:
                continue
            try:
                client.add_collection_field(
                    collection_name=self.config.collection_name,
                    field_name=field_name,
                    data_type=data_type,
                    nullable=True,
                    **options,
                )
            except Exception as exc:
                raise RagMilvusError(
                    f"集合 {self.config.collection_name!r} 缺少 {field_name} 字段且自动迁移失败: "
                    f"{exc}。处理方式：删除旧集合文件 / 集合后重新入库。"
                ) from exc

    def ensure_collection(self) -> None:
        """确保集合存在（不存在则创建，含 BM25 Function + 索引），并加载。

        Note:
            多租户改造后，新集合 schema 含 ``tenant_id`` 标量字段；若磁盘上
            已有**不含**该字段的旧集合（迁移前入库的 ``rag4c.db``），本方法
            不会自动迁移（保持幂等：检测到存在即跳过创建），旧集合会在写入
            含 tenant_id 的行时失败。处理方式：直接删除旧集合文件 / 集合后
            重新 ``ensure_collection`` 再入库（本项目数据可重建，无需迁移
            逻辑）。
        """
        client = self._ensure_client()
        try:
            if not client.has_collection(self.config.collection_name):
                schema = self._build_schema(client)
                index_params = self._build_index_params(client)
                client.create_collection(
                    collection_name=self.config.collection_name,
                    schema=schema,
                    index_params=index_params,
                )
            else:
                self._migrate_schema(client)
        except RagMilvusError:
            raise
        except Exception as exc:
            raise RagMilvusError(
                f"确保集合 {self.config.collection_name!r} 存在失败: {exc}"
            ) from exc
        # 加载到内存（Milvus Lite 已自动加载，重复调用会被忽略）
        try:
            client.load_collection(self.config.collection_name)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #
    def _chunk_to_row(self, chunk: Chunk, dense_vector: Sequence[float]) -> dict[str, Any]:
        """Chunk -> Milvus 行。sparse_vector 由 BM25 Function 自动填充，不提供。"""
        return {
            "chunk_id": chunk.chunk_id,
            "doc_id": chunk.doc_id,
            "text": chunk.text,
            "text_hash": chunk.text_hash,
            "created_at": _dt_to_ms(chunk.created_at),
            "updated_at": _dt_to_ms(chunk.updated_at),
            "source": _clean_str(chunk.source),
            "parent_chunk_id": _clean_str(chunk.parent_chunk_id),
            "acl": _clean_str(chunk.metadata.get("acl") if isinstance(chunk.metadata, dict) else None),
            "tenant_id": _clean_str(chunk.tenant_id),
            "dataset_id": _clean_str(chunk.dataset_id),
            "document_revision": int(chunk.document_revision),
            "content_revision": int(chunk.content_revision),
            "metadata": chunk.metadata,
            "dense_vector": list(dense_vector),
        }

    def insert_chunks(
        self,
        chunks: Sequence[Chunk],
        dense_vectors: Optional[Sequence[Sequence[float]]] = None,
    ) -> list[str]:
        """批量插入 Chunk，返回主键 chunk_id 列表。

        Args:
            chunks: 待插入的 Chunk 列表。
            dense_vectors: 与 chunks 等长的稠密向量。BGE-M3 由独立的
                EmbeddingService 生成后传入；为 None 时抛出
                :class:`RagMilvusError`（Milvus 的稀疏向量由 BM25 Function
                在写路径自动计算，无需也**不能**由应用提供）。

        Returns:
            插入成功的 chunk_id 列表。
        """
        if not chunks:
            return []
        if dense_vectors is None:
            raise RagMilvusError(
                "insert_chunks 需要 dense_vectors（由 EmbeddingService 预先计算）。"
                "稀疏向量无需提供：Milvus 内置 BM25 Function 会自动从 text 生成。"
            )
        if len(dense_vectors) != len(chunks):
            raise RagMilvusError(
                f"dense_vectors 数量({len(dense_vectors)})与 chunks 数量({len(chunks)})不一致。"
            )
        client = self._ensure_client()
        rows = [self._chunk_to_row(c, v) for c, v in zip(chunks, dense_vectors)]
        # 分批写入：每行含全文（可达数千字）、metadata JSON 与 1024 维向量，
        # 约 4-8KB。一次性提交上千行会构造出十几 MB 的 gRPC 消息，
        # 逼近服务端消息上限且失败时没有任何部分进展。
        # 与 core/graph_store.py 的 upsert 分批策略保持一致。
        inserted: list[str] = []
        try:
            for start in range(0, len(rows), _WRITE_BATCH_SIZE):
                batch = rows[start : start + _WRITE_BATCH_SIZE]
                res = client.insert(collection_name=self.config.collection_name, data=batch)
                ids = res.get("ids") or []
                inserted.extend(str(i) for i in ids)
        except RagMilvusError:
            raise
        except Exception as exc:
            raise RagMilvusError(
                f"插入 chunks 失败（已写入 {len(inserted)}/{len(rows)} 行）: {exc}"
            ) from exc
        return inserted

    def upsert_chunks(
        self,
        chunks: Sequence[Chunk],
        dense_vectors: Optional[Sequence[Sequence[float]]] = None,
    ) -> list[str]:
        """按主键 chunk_id 执行 upsert（存在则覆盖，不存在则插入）。

        参数与返回值语义同 :meth:`insert_chunks`。
        """
        if not chunks:
            return []
        if dense_vectors is None:
            raise RagMilvusError(
                "upsert_chunks 需要 dense_vectors（由 EmbeddingService 预先计算）。"
            )
        if len(dense_vectors) != len(chunks):
            raise RagMilvusError(
                f"dense_vectors 数量({len(dense_vectors)})与 chunks 数量({len(chunks)})不一致。"
            )
        client = self._ensure_client()
        rows = [self._chunk_to_row(c, v) for c, v in zip(chunks, dense_vectors)]
        upserted: list[str] = []
        try:
            for start in range(0, len(rows), _WRITE_BATCH_SIZE):
                batch = rows[start : start + _WRITE_BATCH_SIZE]
                res = client.upsert(collection_name=self.config.collection_name, data=batch)
                ids = res.get("ids") or []
                upserted.extend(str(i) for i in ids)
        except RagMilvusError:
            raise
        except Exception as exc:
            raise RagMilvusError(
                f"upsert chunks 失败（已处理 {upserted and len(upserted) or 0}/{len(rows)} 行）: {exc}"
            ) from exc
        # 部分 pymilvus 版本返回 {"upsert_count": n} 而非 ids；
        # upsert 以 chunk_id 为主键，返回请求的 chunk_id 列表始终准确。
        if upserted:
            return upserted
        return [c.chunk_id for c in chunks]

    # ------------------------------------------------------------------ #
    # 删除
    # ------------------------------------------------------------------ #
    def delete_by_doc_id(self, doc_id: str) -> int:
        """按 doc_id 删除该文档的所有 chunk，返回删除条数。"""
        expr = f"doc_id == {_quote_expr_str(doc_id)}"
        return self.delete_by_expr(expr)

    def flush(self) -> None:
        """Flush the chunk collection so delete absence checks observe durable state."""
        client = self._ensure_client()
        try:
            client.flush(collection_name=self.config.collection_name)
        except TypeError:
            # Older pymilvus clients accept the collection name positionally.
            client.flush(self.config.collection_name)
        except Exception as exc:
            raise RagMilvusError(f"flush chunk collection 失败: {exc}") from exc

    def delete_by_ids(self, ids: Sequence[str]) -> int:
        """按 chunk_id 主键列表删除，返回删除条数。"""
        if not ids:
            return 0
        client = self._ensure_client()
        try:
            res = client.delete(
                collection_name=self.config.collection_name, ids=list(ids)
            )
        except RagMilvusError:
            raise
        except Exception as exc:
            raise RagMilvusError(f"按主键删除失败（{len(ids)} 条）: {exc}") from exc
        return int(res.get("delete_count", 0) or 0)

    def delete_by_expr(self, expr: str) -> int:
        """按 Milvus 过滤表达式删除。

        **必须走 ``filter=`` 而不是 ``ids=``**。``MilvusClient.delete`` 把
        ``ids`` 收到的字符串当作**单个主键值**处理（内部走
        ``_pack_pks_expr``，表达式会被拼成 ``pk in ['doc_id == "x"']``），
        于是一条都删不掉——而且不报错，静默返回 0。
        此前这里传的是 ``ids=expr``，导致按 doc_id 删除、覆盖式更新
        （upsert_document 先删后建）实际都没有删掉旧数据。

        注：``ids`` 与 ``filter`` 互斥，同时传会抛「Ambiguous filter parameter」。

        Returns:
            删除条数。
        """
        client = self._ensure_client()
        try:
            res = client.delete(collection_name=self.config.collection_name, filter=expr)
        except RagMilvusError:
            raise
        except Exception as exc:
            raise RagMilvusError(f"按表达式删除失败（expr={expr!r}）: {exc}") from exc
        return int(res.get("delete_count", 0) or 0)

    # ------------------------------------------------------------------ #
    # 读取
    # ------------------------------------------------------------------ #
    def get_chunks_by_ids(self, ids: Sequence[str]) -> list[Chunk]:
        """按 chunk_id 批量取回 Chunk。"""
        if not ids:
            return []
        client = self._ensure_client()
        try:
            rows = retry_call(
                client.get,
                collection_name=self.config.collection_name,
                ids=list(ids),
                output_fields=self._OUTPUT_FIELDS,
                policy=_milvus_retry_policy(self.is_lite),
                on_retry=_observe_retry_delay,
            )
        except RagMilvusError:
            raise
        except Exception as exc:
            raise RagMilvusError(f"按 ids 查询 chunks 失败: {exc}") from exc
        return [self._row_to_chunk(row) for row in rows]

    def _row_to_chunk(self, row: dict[str, Any]) -> Chunk:
        return Chunk(
            chunk_id=str(row.get("chunk_id") or ""),
            doc_id=str(row.get("doc_id") or ""),
            text=str(row.get("text") or ""),
            text_hash=str(row.get("text_hash") or ""),
            created_at=_ms_to_dt(int(row.get("created_at") or 0)),
            updated_at=_ms_to_dt(int(row.get("updated_at") or 0)),
            source=_optional_str(row.get("source")),
            parent_chunk_id=_optional_str(row.get("parent_chunk_id")),
            tenant_id=str(row.get("tenant_id") or ""),
            dataset_id=str(row.get("dataset_id") or ""),
            document_revision=int(row.get("document_revision") or 0),
            content_revision=int(row.get("content_revision") or 0),
            metadata=row.get("metadata") or {},
        )

    # ------------------------------------------------------------------ #
    # 混合检索
    # ------------------------------------------------------------------ #
    def _build_ranker(self) -> Any:
        """构造 RRF 融合排序器（Milvus 3.0 写法）。

        3.0 把重排统一收进了 ``Function`` 体系：融合排序器和 BM25 一样，
        都是挂在请求上的 ``Function``，只是 ``function_type`` 是 ``RERANK``。
        这是官方文档当前给出的写法。旧的 ``RRFRanker`` 类在 pymilvus 3.0.1
        里依然存在且没有 ``@deprecated`` 标记（``hybrid_search`` 的 ranker
        形参类型是 ``Union[BaseRanker, Function]``），所以换过来是为了跟随
        文档口径、少踩后续版本的坑，而不是因为旧写法此刻已经不能用。

        两处易错点：

        - ``input_field_names`` **必须是空列表**。RRF 只看各路的名次，不读
          任何字段；传入字段名反而会被服务端当作非法输入拒绝。
        - ``params["reranker"]`` 是字符串 ``"rrf"``，用来选择融合算法；
          ``k`` 是 RRF 的平滑常数（文档给的取值范围是 (0, 16384)，默认
          60；越大越削弱头部名次的优势）。
        """
        from pymilvus import Function, FunctionType

        rerank = getattr(FunctionType, "RERANK", None)
        if rerank is None:
            # 装的是 2.x 客户端。不在这里退回 RRFRanker——版本线不匹配时
            # 后续调用迟早会以更含糊的方式失败，不如在此明确指出根因。
            raise RagMilvusError(
                "当前 pymilvus 不支持 FunctionType.RERANK（需要 3.0+ 客户端）。"
                '请执行 pip install "pymilvus>=3.0,<4" 后重试。'
            )
        return Function(
            name="rrf",
            input_field_names=[],
            function_type=rerank,
            params={"reranker": "rrf", "k": self.config.rrf_k},
        )

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
        """混合检索：稠密（BGE-M3）+ BM25（Milvus 内置 Function）。

        Args:
            query_dense: 查询的稠密向量（由 EmbeddingService.embed_query 得到）。
            top_k: 最终返回条数。
            query_text: 查询文本。**提供时才包含 BM25 稀疏分支**；
                为 None 则仅做稠密检索（降级路径）。
            group_by_field: 按该字段分组（如 doc_id），配合 group_size 控制
                同组条数（来源多样性 / group_only）。
            group_size: 每组保留条数（默认取 config 无值时为 1）。
            filter_expr: Milvus 过滤表达式（如 ACL 过滤 ``acl IN ['fin','legal']``），
                会同时应用到稠密与 BM25 两个分支。
            with_cosine: 额外取回每条命中的稠密向量，算出它与 ``query_dense``
                的真实余弦，填进 ``RetrievedChunk.dense_cosine``。

                **默认关闭是因为它要花钱。** 取回向量意味着每条命中多传
                ``dim × 4`` 字节（1024 维约 4KB），top_k=16 时一次查询多传约
                64KB。只有真正要用这个信号的调用方才该打开——目前是「rerank
                不生效时给弃权闸一把能用的尺子」这一条路。

                返回的融合分 ``distance`` 是 RRF 值，**不含相似度信息**，所以
                这个余弦不是"顺便多给一个数"，而是那种情况下唯一还能判断
                "库里到底有没有相关内容"的依据。

        Returns:
            按相关度排序的 RetrievedChunk 列表。

        Note:
            pymilvus v3.0.x 的 BM25 分支请求：``AnnSearchRequest`` 的
            ``data`` 直接传查询文本、``anns_field`` 指向 Function 输出字段
            ``sparse_vector``、``param={"metric_type": "BM25"}``。
            ``group_by_field/group_size/strict_group_size`` 经 hybrid_search
            的 **kwargs 透传。
        """
        if top_k < 1:
            raise RagMilvusError("top_k 必须 >= 1")
        client = self._ensure_client()

        # 每路候选放大，给 RRF 融合留余量
        candidate_limit = max(top_k * self.config.candidate_factor, 20)

        from pymilvus import AnnSearchRequest

        reqs: list[AnnSearchRequest] = []
        # 检索期索引参数：按索引类型下发对应的调节项。
        # HNSW 用 ef（候选宽度），IVF 系用 nprobe（探测分桶数），二者都是
        # 「增大 -> 召回精度上升、延迟上升」的调节旋钮。
        #
        # 关于嵌套层级：平铺和嵌套**两种写法都有效**，不必纠结。
        # pymilvus 的 client/utils.py::get_params() 会把 param 顶层的键
        # 合并进 params 子字典（"after 2.5.2, all parameters of search_params
        # can be written into one layer"），所以
        #   {"metric_type": "COSINE", "ef": 100}
        #   {"metric_type": "COSINE", "params": {"ef": 100}}
        # 序列化结果完全相同。这里用嵌套写法只是为了和索引调节项
        # 与 metric_type 在语义上分层，不是因为平铺会失效。
        # （注意：同一个键同时出现在两层会直接报冲突错误。）
        #
        # ef 必须 >= 本次请求的 limit（Milvus 约束），因此对配置值取下界
        # 保护：top_k 调大导致 candidate_limit 超过配置 ef 时自动抬高，
        # 避免直接报错。
        search_params: dict[str, Any] = {}
        itype = self.config.index_type.upper()
        if itype.startswith("IVF"):
            search_params["nprobe"] = self.config.nprobe
        elif itype == "HNSW":
            search_params["ef"] = max(self.config.ef, candidate_limit)
        dense_param: dict[str, Any] = {"metric_type": self.config.metric_type}
        if search_params:
            dense_param["params"] = search_params
        dense_req = AnnSearchRequest(
            data=[list(query_dense)],
            anns_field="dense_vector",
            param=dense_param,
            limit=candidate_limit,
            expr=filter_expr,
        )
        reqs.append(dense_req)

        if query_text:
            sparse_req = AnnSearchRequest(
                data=[query_text],
                anns_field="sparse_vector",
                param={"metric_type": "BM25"},
                limit=candidate_limit,
                expr=filter_expr,
            )
            reqs.append(sparse_req)

        kwargs: dict[str, Any] = {}
        if group_by_field:
            kwargs["group_by_field"] = group_by_field
            kwargs["group_size"] = max(group_size or 1, 1)
            kwargs["strict_group_size"] = False

        output_fields = self._OUTPUT_FIELDS
        if with_cosine:
            output_fields = [*self._OUTPUT_FIELDS, "dense_vector"]

        try:
            results = retry_call(
                client.hybrid_search,
                collection_name=self.config.collection_name,
                reqs=reqs,
                ranker=self._build_ranker(),
                limit=top_k,
                output_fields=output_fields,
                policy=_milvus_retry_policy(self.is_lite),
                on_retry=_observe_retry_delay,
                **kwargs,
            )
        except RagMilvusError:
            raise
        except Exception as exc:
            if self._is_empty_result_quirk(client, exc, filter_expr):
                return []
            raise RagMilvusError(
                f"混合检索失败（group_by_field={group_by_field!r}）: {exc}"
            ) from exc

        hits: list[dict[str, Any]] = results[0] if results else []
        out: list[RetrievedChunk] = []
        for rank, hit in enumerate(hits):
            entity = hit.get("entity") or hit
            chunk = self._row_to_chunk(entity)
            cosine: Optional[float] = None
            if with_cosine:
                cosine = safe_cosine(query_dense, entity.get("dense_vector"))
            out.append(
                RetrievedChunk(
                    chunk=chunk,
                    score=float(hit.get("distance", 0.0)),
                    rank=rank,
                    branch="hybrid",
                    dense_cosine=cosine,
                )
            )
        return out

    # Milvus 服务端在「融合结果整体为空」时返回的错误特征串。
    # 匹配用小写子串而不是错误码：code=5 是通用的 internal error，
    # 拿它当判据会把真实故障也一并吞掉。
    _EMPTY_RESULT_SIGNATURE = "unsupported id type"

    def _is_empty_result_quirk(
        self, client: Any, exc: Exception, filter_expr: Optional[str]
    ) -> bool:
        """判断一次 hybrid_search 失败是否只是「本来就没有任何命中」。

        **这是在绕开一个服务端缺陷，不是在容错。** Milvus 的混合检索在所有
        子路召回都为空时，无法从空并集里推断主键的 ID 类型，于是抛
        ``service internal error: unsupported ID type``——而「一条都没召回」
        是完全正常的业务状态：空知识库、新建租户、ACL 把行全过滤掉，都会
        走到这里。单路 ``search`` 遇到同样情况老老实实返回 0 条，只有
        ``hybrid_search`` 会炸。

        不修的话有两个后果，第二个比第一个严重得多：

        1. 「知识库无相关内容」被误报成「检索失败」，弃权理由是错的；
        2. 每次都记一次熔断失败——**反复查询一个空知识库就能打开检索熔断
           器，进而让所有租户的检索一起快速失败**。

        判据用两个条件而不是只看错误串，避免把真实故障误判成空结果：

        1. 错误信息含 :attr:`_EMPTY_RESULT_SIGNATURE`；
        2. 用同一个 ``filter_expr`` 做一次 ``query(limit=1)`` 复核，确认确实
           一行都没有。这一步是充分的：只要有任意一行通过过滤，稠密 ANN
           必然能召回它（ANN 返回最近邻，不设距离门槛），也就不可能落到
           空并集这个分支上。

        复核查询只在**错误路径**上跑，正常检索零额外开销。

        Returns:
            True 表示可以安全地当作空结果返回；False 表示这是真实故障，
            调用方应继续抛错。
        """
        if self._EMPTY_RESULT_SIGNATURE not in str(exc).lower():
            return False
        try:
            probe = client.query(
                collection_name=self.config.collection_name,
                filter=filter_expr or "",
                output_fields=["chunk_id"],
                limit=1,
            )
        except Exception:  # noqa: BLE001 - 复核本身失败时不敢下结论，按真实故障处理
            return False
        if probe:
            return False
        get_logger(__name__).debug(
            "混合检索无命中（filter=%s），按空结果处理", filter_expr
        )
        return True

    def count_chunks(self, expr: str = "") -> int:
        """统计满足过滤表达式的 chunk 数（``expr`` 为空表示全集合）。

        走 Milvus 的 ``count(*)`` 聚合，而不是「查出来再 len()」：后者要把
        命中的行真的传回客户端，一个几千 chunk 的知识库就是几 MB 的无用往返，
        还会撞上 ``limit`` 上限（超过就静默少算，比报错更糟）。

        Returns:
            条数；集合尚未创建时返回 0 而不是抛错——「还没建库」和「库里
            没有」对调用方是同一件事，没必要逼每个调用点都写一次 try。
        """
        client = self._ensure_client()
        try:
            res = client.query(
                collection_name=self.config.collection_name,
                filter=expr,
                output_fields=["count(*)"],
            )
        except Exception as exc:  # noqa: BLE001 - 集合不存在等同于零条
            if "collection not found" in str(exc).lower():
                return 0
            raise RagMilvusError(f"统计 chunk 数失败（expr={expr!r}）: {exc}") from exc
        if not res:
            return 0
        return int(res[0].get("count(*)", 0) or 0)

    def count_chunks_by_document(
        self,
        doc_id: str,
        tenant_id: str = "",
        *,
        consistency: str = "strong",
    ) -> int:
        """Strongly verify how many chunks remain for one scoped document."""
        expr = f"doc_id == {_quote_expr_str(doc_id)}"
        if tenant_id:
            expr += f" and tenant_id == {_quote_expr_str(tenant_id)}"
        client = self._ensure_client()
        consistency_level = "Strong" if consistency.casefold() == "strong" else consistency
        try:
            rows = client.query(
                collection_name=self.config.collection_name,
                filter=expr,
                output_fields=["count(*)"],
                consistency_level=consistency_level,
            )
        except Exception as exc:
            if "collection not found" in str(exc).casefold():
                return 0
            raise RagMilvusError(
                f"统计文档 chunk 数失败（doc_id={doc_id!r}）: {exc}"
            ) from exc
        if not rows:
            return 0
        return int(rows[0].get("count(*)", 0) or 0)

    def count_chunks_by_dataset(self, dataset_id: str) -> int:
        """统计某个知识库下的 chunk 数。"""
        return self.count_chunks(f"dataset_id == {_quote_expr_str(dataset_id)}")

    def query_chunks_by_doc(
        self,
        doc_id: str,
        tenant_id: str = "",
        *,
        dataset_id: str = "",
        include_unscoped_scope: bool = False,
    ) -> list[Chunk]:
        """按 doc_id（+ 租户 / 知识库）查询该文档的全部 chunk。

        返回全部字段（含 metadata / text_hash），上限 16384 条。
        """
        if include_unscoped_scope and (not tenant_id or not dataset_id):
            raise ValueError(
                "unscoped document query requires both tenant_id and dataset_id"
            )
        client = self._ensure_client()
        expr = f"doc_id == {_quote_expr_str(doc_id)}"
        if tenant_id:
            tenant_filter = f"tenant_id == {_quote_expr_str(tenant_id)}"
            if include_unscoped_scope:
                tenant_filter = (
                    f"({tenant_filter} or tenant_id == {_quote_expr_str('')} "
                    "or tenant_id is null)"
                )
            expr += f" and {tenant_filter}"
        if dataset_id:
            dataset_filter = f"dataset_id == {_quote_expr_str(dataset_id)}"
            if include_unscoped_scope:
                dataset_filter = (
                    f"({dataset_filter} or dataset_id == {_quote_expr_str('')} "
                    "or dataset_id is null)"
                )
            expr += f" and {dataset_filter}"
        try:
            res = client.query(
                collection_name=self.config.collection_name,
                filter=expr,
                output_fields=["*"],
                limit=16384,
            )
        except RagMilvusError:
            raise
        except Exception as exc:
            raise RagMilvusError(f"按文档查询 chunk 失败（doc_id={doc_id!r}）: {exc}") from exc
        return [self._row_to_chunk(row) for row in res]

    def query_chunk_references_by_dataset(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        limit: int = 16_384,
        include_unscoped_scope: bool = False,
    ) -> list[dict[str, str]]:
        """Enumerate only chunk/document identity fields in one dataset scope.

        This is an audit primitive, not a consistency authority. When
        ``include_unscoped_scope`` is enabled, empty/null scope rows are
        surfaced so the caller can mark the enumeration incomplete instead of
        mistaking them for absence.
        """
        if not tenant_id or not dataset_id:
            raise ValueError("tenant_id and dataset_id are required")
        if not isinstance(limit, int) or not 1 <= limit <= 16_384:
            raise ValueError("limit must be between 1 and 16384")
        if include_unscoped_scope:
            tenant_filter = (
                f'(tenant_id == {_quote_expr_str(tenant_id)} '
                f'or tenant_id == {_quote_expr_str("")} or tenant_id is null)'
            )
            dataset_filter = (
                f'(dataset_id == {_quote_expr_str(dataset_id)} '
                f'or dataset_id == {_quote_expr_str("")} or dataset_id is null)'
            )
        else:
            tenant_filter = f"tenant_id == {_quote_expr_str(tenant_id)}"
            dataset_filter = f"dataset_id == {_quote_expr_str(dataset_id)}"
        expression = f"{tenant_filter} and {dataset_filter}"
        client = self._ensure_client()
        try:
            rows = client.query(
                collection_name=self.config.collection_name,
                filter=expression,
                output_fields=["chunk_id", "doc_id", "tenant_id", "dataset_id"],
                limit=limit,
            )
        except RagMilvusError:
            raise
        except Exception as exc:
            raise RagMilvusError(
                f"按知识库枚举 chunk 引用失败（dataset_id={dataset_id!r}）: {exc}"
            ) from exc
        if not isinstance(rows, list):
            raise RagMilvusError("按知识库枚举 chunk 引用返回了不可物化结果")
        references: list[dict[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                raise RagMilvusError("按知识库枚举 chunk 引用返回了非法行")
            for field in ("chunk_id", "doc_id"):
                value = row.get(field)
                if type(value) is not str or not value:
                    raise RagMilvusError(f"按知识库枚举 chunk 引用返回了非法 {field}")
            for field in ("tenant_id", "dataset_id"):
                value = row.get(field)
                if value is not None and type(value) is not str:
                    raise RagMilvusError(f"按知识库枚举 chunk 引用返回了非法 {field}")
            references.append(
                {
                    "chunk_id": row["chunk_id"],
                    "doc_id": row["doc_id"],
                    "tenant_id": row.get("tenant_id") or "",
                    "dataset_id": row.get("dataset_id") or "",
                }
            )
        return references

    # ------------------------------------------------------------------ #
    # 工具
    # ------------------------------------------------------------------ #
    @staticmethod
    def build_acl_filter(acls: Iterable[str]) -> str:
        """构造 ACL 过滤表达式：``acl IN ['fin','legal']``。

        Usage:
            client.hybrid_search(..., filter_expr=client.build_acl_filter(["fin", "legal"]))
        """
        quoted = ", ".join(_quote_expr_str(a) for a in acls)
        return f"acl IN [{quoted}]"

    @staticmethod
    def build_tenant_filter(tenant_id: str) -> str | None:
        """构造租户过滤表达式：``tenant_id == 'x'``。

        空串 / None 视为未指定，返回 None 表示**不过滤**（enforced 关闭
        的开发 / 单租户模式）。检索 / 入库的强制隔离由调用方先把租户解析
        为有效值（:func:`config.settings.resolve_tenant`）再传入。
        """
        if not tenant_id:
            return None
        return f"tenant_id == {_quote_expr_str(tenant_id)}"

    @staticmethod
    def build_dataset_filter(dataset_id: str) -> str | None:
        """构造知识库过滤表达式：dataset_id == x；空串返回 None（不过滤）。"""
        if not dataset_id:
            return None
        return f"dataset_id == {_quote_expr_str(dataset_id)}"

    @staticmethod
    def build_filters(
        tenant_id: str,
        acl: Iterable[str] | None = None,
        acl_filter_on: bool = True,
        dataset_id: str = "",
        extra_expr: str | None = None,
    ) -> str | None:
        """组合租户 + 知识库 + ACL + 用户过滤的完整 Milvus 过滤表达式。

        - tenant 过滤无条件参与（非空租户时）；空租户跳过（不过滤）。
        - dataset_id 过滤：非空时参与（多知识库隔离）。
        - ACL 过滤仅在 acl 非空且 acl_filter_on 为 True 时参与。
        - extra_expr 必须已经 core.metadata 校验（manual/automatic 通道），
          原样 AND 拼入。
        - 全部为空时返回 None（无过滤）。

        Usage:
            client.hybrid_search(..., filter_expr=client.build_filters(...))
        """
        parts: list[str] = []
        tenant_expr = RagMilvusClient.build_tenant_filter(tenant_id)
        if tenant_expr:
            parts.append(tenant_expr)
        dataset_expr = RagMilvusClient.build_dataset_filter(dataset_id)
        if dataset_expr:
            parts.append(dataset_expr)
        if acl and acl_filter_on:
            parts.append(RagMilvusClient.build_acl_filter(acl))
        if extra_expr:
            parts.append("(" + extra_expr + ")")
        return " and ".join(parts) if parts else None


__all__ = [
    "RagMilvusError",
    "RagMilvusClient",
]
