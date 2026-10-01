"""Milvus 向量库实现（pymilvus 官方 SDK，替换 Round 1 的 numpy 本地库）。

为什么要有这份代码：Round 1 的 ``store.LocalVectorStore`` 是**在没有 Milvus 实例时
的权宜之计**——它只替掉了"余弦 Top-K"这段算术，BM25、过滤表达式、分组去重、
一致性级别这些真正影响检索结果的东西全都测不到。现在虚拟机上的 Milvus 3.0 可用，
检索评测就该跑在**真实索引**上，指标才有说服力。

设计口径与仓库既有实现（``core/milvus_client.py``、``config.settings.MilvusSettings``）
保持一致：字段同名同类型（chunk_id / dense_vector / sparse_vector / tenant_id /
dataset_id …）、索引默认 HNSW+COSINE、BM25 用内置 Function（应用层不生成稀疏向量）、
中文分词器 ``{"type":"chinese"}``。这样评测集合与生产集合是同一套物理契约，
评测结论可以直接外推到生产。

用法::

    from eval.retrieval_eval.milvus_store import MilvusStoreConfig, MilvusVectorStore

    cfg = MilvusStoreConfig.from_env()          # 全部从环境变量读
    with MilvusVectorStore(cfg) as store:       # 进上下文自动建集合/加载，退出自动 close
        store.upsert(rows)                      # 批量写入（按 batch_size 切批）
        hits = store.search(query_vec, top_k=10, filter_expr='tenant_id == "eval"')

    # 命令行自检（不连服务端）/ 探活 / 端到端演示
    python -m eval.retrieval_eval.milvus_store --check
    python -m eval.retrieval_eval.milvus_store --probe
    python -m eval.retrieval_eval.milvus_store --demo --keep
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional, Sequence

_LOG = logging.getLogger(__name__)


class MilvusStoreError(RuntimeError):
    """本模块统一的异常出口。

    为什么要把 pymilvus 的异常再包一层：调用方（评测 / 脚本）需要的是
    "下一步该干什么"，而不是 gRPC 的原始错误码。所以这里把常见原因翻成
    人话，同时把原始异常挂在 ``__cause__`` 上，排查时仍能追到底。
    """


# ---------------------------------------------------------------------------
# 配置：全部来自环境变量，不硬编码
# ---------------------------------------------------------------------------

#: 环境变量前缀。优先读 RAG4C_*（本仓约定），读不到再退回通用 MILVUS_*。
_ENV_PREFIX = "RAG4C_MILVUS"
_FALLBACK_PREFIX = "MILVUS"


def _env(name: str) -> Optional[str]:
    """读环境变量：先 RAG4C_MILVUS_<NAME>，再 MILVUS_<NAME>，都没有返回 None。"""
    for prefix in (_ENV_PREFIX, _FALLBACK_PREFIX):
        raw = os.environ.get(f"{prefix}_{name}")
        if raw is not None and raw.strip():
            return raw.strip()
    return None


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        _LOG.warning("环境变量 %s=%r 不是整数，回退默认值 %s", name, raw, default)
        return default


@dataclass
class MilvusStoreConfig:
    """连接与集合参数（**无一处硬编码**，缺失一律走有文档说明的默认值）。"""

    # ---- 连接 ----
    uri: str = "http://127.0.0.1:19530"
    """Milvus 地址。

    取值形态：
    - ``http(s)://host:port``：Milvus Standalone / Distributed / Zilliz Cloud；
    - ``./xxx.db``            ：Milvus Lite（**仅 Linux/macOS**，Windows 上会报
      ``ModuleNotFoundError: milvus_lite``）。

    缺失处理：先退回 ``MILVUS_URI``；再没有就用 ``host/port`` 拼；若 host/port
    也没配，落到 ``http://127.0.0.1:19530``（本机默认端口）并打一条 warning，
    由连接失败时的异常给出明确指引。
    """

    token: str = ""
    """认证凭据。本机无认证实例留空即可；Zilliz Cloud 用 API key；
    开启认证的自建实例用 ``"用户名:密码"``。**留空不会报错**，只是对需要认证
    的服务端会拿到 401/鉴权失败——那时再配。"""

    db_name: str = ""
    """数据库名。留空 = 使用服务端默认库（``default``），此时**不会**调用
    ``use_database``，避免多一次无谓往返。"""

    host: str = ""
    port: int = 19530
    """host/port 仅在未配置 uri 时用于拼装地址（老部署习惯分开写）。"""

    # ---- 集合 ----
    collection: str = "rag4c_eval_chunks"
    """集合名。默认刻意**不带生产集合** ``rag4c_chunks``：评测要写数据、
    要建索引、还可能删集合，绝不能顺手改到生产数据上。"""

    dim: int = 1024
    """向量维度，必须与嵌入模型输出一致（BGE-M3 = 1024）。不一致会在写入时报错。"""

    # ---- 索引 ----
    index_type: str = "HNSW"
    """向量索引类型。

    - ``HNSW``（默认）：图索引，召回率高、延迟低，内存开销大。生产同款。
      关键参数 ``M``（每层连接数，16~64）与 ``efConstruction``（建索引宽度，
      200~500；越大越准、建得越慢）。
    - ``IVF_FLAT``：倒排 + 原始向量，内存比 HNSW 小、构建快，召回靠 ``nprobe``
      调（通常 nlist/16 ~ nlist/4）。数据量 < 百万级且要省内存时选它。
    - ``AUTOINDEX``：Milvus 按数据规模自动挑，省心但不可调，适合懒得调参的
      场景与 Zilliz Serverless。
    """

    metric_type: str = "COSINE"
    """度量方式。

    - ``COSINE``（默认）：余弦相似度，**归一化后**等价于 IP；文本嵌入的通行
      选择，只比方向不比长度。Milvus 会自动归一化，返回值即 [-1,1] 的相似度，
      越大越近。
    - ``IP``：内积，向量**已归一化**时与 COSINE 等价但更快一点；未归一化会被
      长向量"带偏"。
    - ``L2``：欧氏距离，越小越近，适合图像/坐标类向量。

    改这个字段必须重建索引（度量写死在索引里）。
    """

    m: int = 16
    ef_construction: int = 200
    nlist: int = 1024
    # ---- 检索 ----
    ef: int = 64
    """HNSW 检索期候选宽度。必须 ``>= limit``（Milvus 硬约束），代码里会
    自动取 ``max(ef, limit)`` 兜底。调大 -> 召回更准、延迟更高。"""

    nprobe: int = 16
    """IVF 系检索期探测分桶数。调大 -> 召回更准、延迟更高。"""

    consistency_level: str = "Bounded"
    """读一致性级别：``Strong`` / ``Session`` / ``Bounded``（默认）/ ``Eventually``。

    - ``Bounded``：默认，读到"稍旧但可控"的数据，延迟与新鲜度平衡；
    - ``Strong``：写入后立即可见，最贵（评测刚写完就查，想要确定性就用它）；
    - ``Eventually``：最便宜，可能读到旧数据。
    注：Milvus Lite 只支持 ``Strong``。
    """

    # ---- 写入 ----
    batch_size: int = 256
    """单批写入条数。

    经验值：每条 1024 维 float32 约 4KB，256 条 ≈ 1MB，接近 Milvus 官方建议的
    "单批 1~10MB 以内"。太小 -> 往返次数多；太大 -> 单次请求超时/内存峰值高。
    """

    timeout: float = 30.0
    """单次 RPC 超时（秒）。建索引/大批量写入建议临时调大。"""

    # ---- BM25（可选，与生产一致）----
    enable_bm25: bool = True
    """是否为 text 字段挂 BM25 Function + 稀疏索引。

    与生产集合一致（应用层不写稀疏向量，由 Milvus 内置 Function 计算）。
    中文语料**必须**带 ``analyzer_params={"type":"chinese"}``，否则默认 standard
    分析器按空白切词，中文查询的 BM25 召回恒为 0（不报错，静默失效）。
    """

    bm25_analyzer: str = "chinese"
    rrf_k: int = 60
    """混合检索 RRF 融合常数（与生产配置一致；越大 -> 排名靠后的结果权重衰减越慢）。"""

    text_max_length: int = 65535
    """text 字段 VARCHAR 上限（**按字节计**，中文一个字 3 字节，别按字符数估）。"""

    output_fields: tuple[str, ...] = field(
        default_factory=lambda: (
            "chunk_id",
            "doc_id",
            "text",
            "source",
            "tenant_id",
            "dataset_id",
            "metadata",
        )
    )
    """检索默认回带字段。只回带需要的字段：向量字段本身**不要**放进 output_fields
    （1024 维 × top_k 会白白放大网络与序列化开销）。"""

    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls) -> "MilvusStoreConfig":
        """从环境变量构造；缺失项用上面的默认值并留 warning 痕迹。"""
        uri = _env("URI")
        host = _env("HOST") or ""
        port = _env_int("PORT", 19530)
        if not uri and host:
            uri = f"http://{host}:{port}"
        if not uri:
            # 环境变量没配时，退回项目配置（config.settings.milvus.uri）——
            # 用 RAG4C_ENV_FILE 指定配置文件的部署方式下，URI 只在文件里，
            # 不进进程环境。这里兜住，免得评测莫名连到本机默认端口。
            try:
                from config.settings import get_settings

                uri = str(get_settings().milvus.uri or "").strip() or None
            except Exception:  # noqa: BLE001
                uri = None
        if not uri:
            _LOG.warning(
                "未配置 RAG4C_MILVUS_URI / MILVUS_URI，回退默认地址 %s；"
                "连不上时请显式配置（虚拟机部署通常是 http://<vm-ip>:19530）",
                cls.uri,
            )
        return cls(
            uri=uri or cls.uri,
            token=_env("TOKEN") or "",
            db_name=_env("DB") or _env("DB_NAME") or "",
            host=host,
            port=port,
            collection=_env("COLLECTION") or cls.collection,
            dim=_env_int("DIM", cls.dim),
            index_type=(_env("INDEX_TYPE") or cls.index_type).upper(),
            metric_type=(_env("METRIC_TYPE") or cls.metric_type).upper(),
            m=_env_int("M", cls.m),
            ef_construction=_env_int("EF_CONSTRUCTION", cls.ef_construction),
            nlist=_env_int("NLIST", cls.nlist),
            ef=_env_int("EF", cls.ef),
            nprobe=_env_int("NPROBE", cls.nprobe),
            consistency_level=_env("CONSISTENCY") or cls.consistency_level,
            batch_size=_env_int("BATCH_SIZE", cls.batch_size),
            timeout=float(_env("TIMEOUT") or cls.timeout),
            enable_bm25=(_env("ENABLE_BM25") or "1").strip().lower() not in {"0", "false", "no"},
            bm25_analyzer=_env("BM25_ANALYZER") or cls.bm25_analyzer,
            text_max_length=_env_int("TEXT_MAX_LENGTH", cls.text_max_length),
        )

    def as_dict(self) -> dict[str, Any]:
        """脱敏后的配置快照（token 只报"有没有"，不报值）。"""
        data = {k: getattr(self, k) for k in self.__dataclass_fields__}  # type: ignore[attr-defined]
        data["token"] = "***" if self.token else ""
        return data


# ---------------------------------------------------------------------------
# 集合与索引定义（与生产集合同构）
# ---------------------------------------------------------------------------


def build_schema(cfg: MilvusStoreConfig, *, client: Any = None) -> Any:
    """构建集合 Schema。**不需要连服务端**，因此可离线单测。"""
    from pymilvus import DataType, Function, FunctionType, MilvusClient

    factory = client if client is not None else MilvusClient
    schema = factory.create_schema(auto_id=False, enable_dynamic_field=False)
    # 主键：用业务 chunk_id（字符串）而不是自增 ID，去重与 upsert 才有意义
    schema.add_field(field_name="chunk_id", datatype=DataType.VARCHAR, is_primary=True, max_length=512)
    schema.add_field(field_name="doc_id", datatype=DataType.VARCHAR, max_length=512)
    text_kwargs: dict[str, Any] = {}
    if cfg.enable_bm25:
        # BM25 的输入字段必须开 analyzer；中文语料必须指定 chinese 分析器
        text_kwargs["enable_analyzer"] = True
        text_kwargs["analyzer_params"] = {"type": cfg.bm25_analyzer}
    schema.add_field(
        field_name="text",
        datatype=DataType.VARCHAR,
        max_length=cfg.text_max_length,
        **text_kwargs,
    )
    schema.add_field(field_name="text_hash", datatype=DataType.VARCHAR, max_length=64)
    schema.add_field(field_name="created_at", datatype=DataType.INT64)
    schema.add_field(field_name="updated_at", datatype=DataType.INT64)
    schema.add_field(field_name="source", datatype=DataType.VARCHAR, max_length=512)
    schema.add_field(field_name="parent_chunk_id", datatype=DataType.VARCHAR, max_length=512)
    schema.add_field(field_name="acl", datatype=DataType.VARCHAR, max_length=4096)
    # 多租户 / 知识库维度：检索期过滤的主战场，做成标量字段（可加索引加速）
    schema.add_field(field_name="tenant_id", datatype=DataType.VARCHAR, max_length=128)
    schema.add_field(field_name="dataset_id", datatype=DataType.VARCHAR, max_length=128)
    schema.add_field(field_name="document_revision", datatype=DataType.INT64)
    schema.add_field(field_name="content_revision", datatype=DataType.INT64)
    schema.add_field(field_name="metadata", datatype=DataType.JSON)
    schema.add_field(field_name="dense_vector", datatype=DataType.FLOAT_VECTOR, dim=cfg.dim)
    if cfg.enable_bm25:
        schema.add_field(field_name="sparse_vector", datatype=DataType.SPARSE_FLOAT_VECTOR)
        schema.add_function(
            Function(
                name="bm25_text_search",
                function_type=FunctionType.BM25,
                input_field_names=["text"],
                output_field_names="sparse_vector",
            )
        )
    return schema


def build_index_params(cfg: MilvusStoreConfig, *, client: Any = None) -> Any:
    """按 index_type 组装索引参数（HNSW / IVF_* / AUTOINDEX）。

    选型的取舍写在 :data:`MilvusStoreConfig.index_type` 的注释里；这里只负责
    把"不同类型该带哪些参数"收在一处，避免调用点各拼各的。
    """
    from pymilvus import MilvusClient

    factory = client if client is not None else MilvusClient
    index_params = factory.prepare_index_params()
    itype = cfg.index_type.upper()
    if itype == "HNSW":
        params: dict[str, Any] = {"M": cfg.m, "efConstruction": cfg.ef_construction}
    elif itype.startswith("IVF"):
        params = {"nlist": cfg.nlist}
    else:  # AUTOINDEX / FLAT 等：Milvus 自行决定，不要塞无关参数（会被拒）
        params = {}
    index_params.add_index(
        field_name="dense_vector",
        index_type=itype,
        metric_type=cfg.metric_type,
        params=params,
    )
    if cfg.enable_bm25:
        index_params.add_index(
            field_name="sparse_vector",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
            params={"inverted_index_algo": "DAAT_MAXSCORE", "bm25_k1": 1.2, "bm25_b": 0.75},
        )
    return index_params


def build_search_params(cfg: MilvusStoreConfig, limit: int) -> dict[str, Any]:
    """检索期索引参数。``ef`` 必须 >= limit（Milvus 硬约束，否则直接报错）。"""
    params: dict[str, Any] = {}
    itype = cfg.index_type.upper()
    if itype == "HNSW":
        params["ef"] = max(cfg.ef, limit)
    elif itype.startswith("IVF"):
        params["nprobe"] = max(cfg.nprobe, 1)
    return {"metric_type": cfg.metric_type, "params": params}


# ---------------------------------------------------------------------------
# 客户端封装
# ---------------------------------------------------------------------------


class MilvusVectorStore:
    """Milvus 向量库的薄封装：连接 / 建集合 / 写入 / 检索 / 释放。

    生命周期：``MilvusVectorStore(cfg)`` 只建对象不连服务端；
    :meth:`connect` 显式连接（也可由 :meth:`ensure_collection` 惰性触发）；
    :meth:`close` 释放连接。**推荐用 ``with`` 语句**，退出即释放。
    """

    def __init__(self, cfg: MilvusStoreConfig) -> None:
        self.cfg = cfg
        self._client: Any = None

    # -- 生命周期 ---------------------------------------------------------
    def __enter__(self) -> "MilvusVectorStore":
        self.connect()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    @property
    def client(self) -> Any:
        if self._client is None:
            self.connect()
        return self._client

    def connect(self) -> Any:
        """建立连接（幂等）。失败抛 :class:`MilvusStoreError` 并给出排查方向。"""
        if self._client is not None:
            return self._client
        try:
            from pymilvus import MilvusClient
        except ImportError as exc:  # pragma: no cover - 依赖缺失路径
            raise MilvusStoreError("未安装 pymilvus：pip install 'pymilvus>=2.5'") from exc
        try:
            kwargs: dict[str, Any] = {
                "uri": self.cfg.uri,
                "timeout": self.cfg.timeout,
            }
            if self.cfg.token:
                kwargs["token"] = self.cfg.token
            if self.cfg.db_name:
                # 只有显式配了库名才切库：默认库多调一次 use_database 是白花往返
                kwargs["db_name"] = self.cfg.db_name
            self._client = MilvusClient(**kwargs)
        except Exception as exc:  # noqa: BLE001 - 统一翻译成可操作提示
            raise MilvusStoreError(
                f"连接 Milvus 失败（uri={self.cfg.uri}）：{exc}。"
                "检查：① 服务端是否启动（docker ps / systemctl status milvus）；"
                "② 地址端口是否可达（本机 19530、虚拟机填 VM 的 IP）；"
                "③ 需要认证时是否配了 RAG4C_MILVUS_TOKEN；"
                "④ Windows 上不要指望 Milvus Lite（*.db 只在 Linux/macOS 可用）。"
            ) from exc
        return self._client

    def close(self) -> None:
        """释放连接（幂等；close 失败只告警，不影响主流程）。"""
        if self._client is None:
            return
        try:
            self._client.close()
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("关闭 Milvus 连接失败: %s", exc)
        finally:
            self._client = None

    # -- 集合 -------------------------------------------------------------
    def has_collection(self) -> bool:
        return bool(self.client.has_collection(self.cfg.collection))

    def ensure_collection(self, *, drop_if_exists: bool = False) -> bool:
        """保证集合存在并已加载。返回 True 表示本次新建。

        顺序严格是 ``has_collection -> create_collection(带 index_params) ->
        load_collection``：Milvus 的约束是**先有索引才能 load，先 load 才能查**，
        跳任何一步都会在检索时报"index not found / collection not loaded"。
        """
        client = self.client
        try:
            exists = client.has_collection(self.cfg.collection)
        except Exception as exc:  # noqa: BLE001
            raise MilvusStoreError(f"判断集合是否存在失败: {exc}") from exc
        if exists and drop_if_exists:
            client.drop_collection(self.cfg.collection)
            exists = False
        if not exists:
            try:
                client.create_collection(
                    collection_name=self.cfg.collection,
                    schema=build_schema(self.cfg, client=client),
                    index_params=build_index_params(self.cfg, client=client),
                    consistency_level=self.cfg.consistency_level,
                )
            except Exception as exc:  # noqa: BLE001
                raise MilvusStoreError(
                    f"创建集合失败（{self.cfg.collection}）：{exc}。"
                    "常见原因：dim 与向量不一致、BM25 Function 不被当前版本支持、"
                    "同名集合已存在但 schema 冲突。"
                ) from exc
            created = True
        else:
            created = False
        self.load()
        return created

    def load(self) -> None:
        """把集合加载进内存（检索前置条件）。未建索引会在这里暴露。"""
        try:
            self.client.load_collection(self.cfg.collection)
        except Exception as exc:  # noqa: BLE001
            raise MilvusStoreError(
                f"加载集合失败（{self.cfg.collection}）：{exc}。"
                "最常见的原因是集合没有索引：先建索引再 load。"
            ) from exc

    def release(self) -> None:
        """从内存卸载（省常驻内存，下次检索会重新 load）。"""
        try:
            self.client.release_collection(self.cfg.collection)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("释放集合失败: %s", exc)

    def drop(self) -> None:
        try:
            self.client.drop_collection(self.cfg.collection)
        except Exception as exc:  # noqa: BLE001
            raise MilvusStoreError(f"删除集合失败: {exc}") from exc

    def count(self) -> int:
        stats = self.client.get_collection_stats(self.cfg.collection)
        return int(stats.get("row_count", 0) or 0)

    # -- 写入 -------------------------------------------------------------
    def upsert(self, rows: Sequence[dict[str, Any]], *, batch_size: int | None = None) -> int:
        """批量写入（同主键即覆盖）。返回写入条数。

        为什么要分批：Milvus 单次请求有体积上限，一次塞几万条会超时或被拒；
        分批还能让失败范围可控（一批失败不必整批重来）。
        """
        size = batch_size or self.cfg.batch_size
        written = 0
        for batch in _batched(rows, size):
            try:
                self.client.upsert(collection_name=self.cfg.collection, data=list(batch))
            except Exception as exc:  # noqa: BLE001
                raise MilvusStoreError(
                    f"写入失败（{self.cfg.collection}，本批 {len(batch)} 条）：{exc}。"
                    "常见原因：向量维度 != dim、text 超过 max_length（注意按字节算）、"
                    "字段类型不匹配。"
                ) from exc
            written += len(batch)
        return written

    def insert(self, rows: Sequence[dict[str, Any]], *, batch_size: int | None = None) -> int:
        """批量插入（主键冲突会报错；需要覆盖请用 :meth:`upsert`）。"""
        size = batch_size or self.cfg.batch_size
        written = 0
        for batch in _batched(rows, size):
            try:
                self.client.insert(collection_name=self.cfg.collection, data=list(batch))
            except Exception as exc:  # noqa: BLE001
                raise MilvusStoreError(f"插入失败（本批 {len(batch)} 条）：{exc}") from exc
            written += len(batch)
        return written

    def delete(self, *, expr: str = "", ids: Sequence[Any] = ()) -> int:
        """按过滤表达式或主键删除，返回影响条数（Milvus 返回受影响的 id 列表长度）。"""
        if not expr and not ids:
            raise MilvusStoreError("delete 必须给 expr 或 ids，否则等于全表删除（已显式拒绝）")
        try:
            kwargs: dict[str, Any] = {"collection_name": self.cfg.collection}
            if expr:
                kwargs["filter"] = expr
            else:
                kwargs["ids"] = list(ids)
            res = self.client.delete(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise MilvusStoreError(f"删除失败: {exc}") from exc
        # pymilvus 的 delete 返回一个 "省略 0 值" 的字典（delete_count 可能根本不在里面），
        # 直接 int(res) 会抛 TypeError。计数缺失就当 0，真实条数由调用方复查。
        if isinstance(res, (list, tuple)):
            return len(res)
        try:
            return int((res or {}).get("delete_count", 0) or 0)
        except (AttributeError, TypeError, ValueError):
            return 0

    def query_ids(self, ids: Sequence[str]) -> list[str]:
        """按主键批量取回**存在**的 id（用于校验标注是否失效）。

        生产库很大，评测前只核对待用的那些主键，不做全量比对。
        """
        if not ids:
            return []
        try:
            res = self.client.query(
                collection_name=self.cfg.collection,
                ids=list(ids),
                output_fields=["chunk_id"],
            )
        except Exception as exc:  # noqa: BLE001
            raise MilvusStoreError(f"按主键查询失败: {exc}") from exc
        return [row.get("chunk_id") for row in (res or []) if row.get("chunk_id")]

    def query_texts(self, ids: Sequence[str]) -> dict[str, str]:
        """按主键取回 ``{chunk_id: text}``（用于核对标注声明与原文是否一致）。

        同样只对待用的那些主键做批量查询，不全量扫库。查不到的 id 不会出现在
        返回值里 —— 调用方据此判断"拿不到原文"，不能当作"原文没内容"。
        """
        if not ids:
            return {}
        try:
            res = self.client.query(
                collection_name=self.cfg.collection,
                ids=list(ids),
                output_fields=["text"],
            )
        except Exception as exc:  # noqa: BLE001
            raise MilvusStoreError(f"按主键取原文失败: {exc}") from exc
        return {row["chunk_id"]: row.get("text") or "" for row in (res or []) if row.get("chunk_id")}

    def flush(self) -> None:
        """把内存中的写入刷成可持久段（写入后立即可见的兜底动作）。"""
        try:
            self.client.flush(self.cfg.collection)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("flush 失败（不阻断流程）: %s", exc)

    # -- 检索 -------------------------------------------------------------
    def search(
        self,
        query_vector: Sequence[float],
        *,
        top_k: int = 10,
        filter_expr: str = "",
        output_fields: Optional[Sequence[str]] = None,
        consistency_level: str = "",
    ) -> list[dict[str, Any]]:
        """稠密向量检索。返回按相似度降序的命中列表。

        返回结构（与 :meth:`hybrid_search` 一致）：
        ``[{"chunk_id", "score", "distance", "entity": {...}}, ...]``
        """
        if top_k <= 0:
            raise MilvusStoreError(f"top_k 必须为正整数，实际 {top_k}")
        if len(query_vector) != self.cfg.dim:
            raise MilvusStoreError(
                f"查询向量维度 {len(query_vector)} 与配置 dim={self.cfg.dim} 不一致"
            )
        try:
            res = self.client.search(
                collection_name=self.cfg.collection,
                data=[list(query_vector)],
                anns_field="dense_vector",
                limit=top_k,
                search_params=build_search_params(self.cfg, top_k),
                output_fields=list(output_fields or self.cfg.output_fields),
                filter=filter_expr or None,
                consistency_level=consistency_level or self.cfg.consistency_level,
            )
        except Exception as exc:  # noqa: BLE001
            raise MilvusStoreError(
                f"检索失败（{self.cfg.collection}）：{exc}。"
                "常见原因：集合未 load、索引未建、过滤表达式语法错、ef < top_k。"
            ) from exc
        return _normalize(res, metric=self.cfg.metric_type)

    def search_text(
        self,
        query_text: str,
        *,
        top_k: int = 10,
        filter_expr: str = "",
        output_fields: Optional[Sequence[str]] = None,
    ) -> list[dict[str, Any]]:
        """BM25 全文检索分支（需要 ``enable_bm25=True`` 且集合带稀疏索引）。"""
        if not self.cfg.enable_bm25:
            raise MilvusStoreError("未启用 BM25（enable_bm25=False），无法走稀疏分支")
        try:
            res = self.client.search(
                collection_name=self.cfg.collection,
                data=[query_text],
                anns_field="sparse_vector",
                limit=top_k,
                search_params={"metric_type": "BM25"},
                output_fields=list(output_fields or self.cfg.output_fields),
                filter=filter_expr or None,
            )
        except Exception as exc:  # noqa: BLE001
            raise MilvusStoreError(f"BM25 检索失败: {exc}") from exc
        return _normalize(res, metric="BM25")

    def hybrid_search(
        self,
        query_dense: Sequence[float],
        top_k: int = 10,
        query_text: Optional[str] = None,
        group_by_field: Optional[str] = None,
        group_size: Optional[int] = None,
        filter_expr: Optional[str] = None,
        with_cosine: bool = False,
    ) -> list[Any]:
        """与 :class:`eval.retrieval_eval.store.LocalVectorStore` **同签名**的适配器，
        返回 ``list[RetrievedChunk]``，因此评测管线可以整条换成真 Milvus。

        ``query_text`` 给值时走稠密 + BM25 双路 RRF 融合（与生产一致）；
        为 None 只走稠密。
        """
        from models.schemas import Chunk, RetrievedChunk

        if query_text and self.cfg.enable_bm25:
            from pymilvus import AnnSearchRequest, RRFRanker

            reqs = [
                AnnSearchRequest(
                    data=[list(query_dense)],
                    anns_field="dense_vector",
                    param=build_search_params(self.cfg, top_k),
                    limit=top_k,
                    expr=filter_expr,
                ),
                AnnSearchRequest(
                    data=[query_text],
                    anns_field="sparse_vector",
                    param={"metric_type": "BM25"},
                    limit=top_k,
                    expr=filter_expr,
                ),
            ]
            kwargs: dict[str, Any] = {}
            if group_by_field:
                kwargs.update(
                    group_by_field=group_by_field,
                    group_size=max(group_size or 1, 1),
                    strict_group_size=False,
                )
            try:
                res = self.client.hybrid_search(
                    collection_name=self.cfg.collection,
                    reqs=reqs,
                    ranker=RRFRanker(self.cfg.rrf_k),
                    limit=top_k,
                    output_fields=list(self.cfg.output_fields),
                    consistency_level=self.cfg.consistency_level,
                    **kwargs,
                )
            except Exception as exc:  # noqa: BLE001
                raise MilvusStoreError(f"混合检索失败: {exc}") from exc
            hits = _normalize(res, metric="RRF")
        else:
            hits = self.search(
                query_dense,
                top_k=top_k,
                filter_expr=filter_expr or "",
                output_fields=self.cfg.output_fields,
            )
            if group_by_field:
                hits = _group_limit(hits, group_by_field, group_size or 1)[:top_k]

        out: list[Any] = []
        for rank, hit in enumerate(hits, start=1):
            entity = hit.get("entity") or {}
            out.append(
                RetrievedChunk(
                    chunk=Chunk(
                        chunk_id=entity.get("chunk_id", hit.get("chunk_id", "")),
                        doc_id=entity.get("doc_id", ""),
                        text=entity.get("text", ""),
                        text_hash=entity.get("text_hash", ""),
                        created_at=_ms_to_datetime(entity.get("created_at")),
                        updated_at=_ms_to_datetime(entity.get("updated_at")),
                        tenant_id=entity.get("tenant_id", "") or "",
                        dataset_id=entity.get("dataset_id", "") or "",
                        source=entity.get("source"),
                        metadata=entity.get("metadata") or {},
                    ),
                    score=float(hit.get("score", 0.0)),
                    rank=rank,
                    branch="hybrid",
                    # COSINE 度量下 Milvus 返回的 distance 就是余弦相似度；
                    # IP/L2/RRF 不是同一把尺子，不冒充余弦
                    dense_cosine=(
                        float(hit.get("distance", 0.0))
                        if (with_cosine and self.cfg.metric_type == "COSINE")
                        else None
                    ),
                )
            )
        return out


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _batched(items: Sequence[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    """按 size 切片（生成器，避免为大语料额外复制一份列表）。"""
    size = max(1, int(size))
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def _normalize(res: Any, *, metric: str) -> list[dict[str, Any]]:
    """把 pymilvus 的 ``[[hit, ...]]`` 摊平成一层，并给出统一的 score 语义。

    - COSINE / IP：distance 越大越相似 -> score = distance
    - L2：distance 越小越近 -> 转成 ``1/(1+d)``，保证"越大越好"的方向一致
    - BM25 / RRF：distance 本身就是相关性分 -> 直接用
    """
    hits: list[dict[str, Any]] = []
    for group in res or []:
        for hit in group:
            distance = float(hit.get("distance", 0.0))
            if metric.upper() == "L2":
                score = 1.0 / (1.0 + distance)
            else:
                score = distance
            hits.append(
                {
                    "id": hit.get("id"),
                    "distance": distance,
                    "score": score,
                    "entity": hit.get("entity") or {},
                }
            )
    hits.sort(key=lambda item: item["score"], reverse=True)
    return hits


def _group_limit(
    hits: Sequence[dict[str, Any]], field_name: str, group_size: int
) -> list[dict[str, Any]]:
    """按字段分组限流（来源多样性：同一分组最多保留 group_size 条）。"""
    used: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    for hit in hits:
        key = str((hit.get("entity") or {}).get(field_name, ""))
        if used.get(key, 0) >= max(1, group_size):
            continue
        used[key] = used.get(key, 0) + 1
        out.append(hit)
    return out


def _ms_to_datetime(value: Any) -> Any:  # noqa: ANN401 - 兼容 datetime 与毫秒时间戳
    if isinstance(value, (int, float)) and value > 0:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
    if value:
        return value
    from datetime import datetime, timezone

    return datetime(2026, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 命令行：--check（离线）/ --probe（探活）/ --demo（端到端）
# ---------------------------------------------------------------------------


def _demo(cfg: MilvusStoreConfig, *, keep: bool) -> int:
    """端到端演示：建集合 -> 批量写入 -> 检索（含过滤）-> 清理。"""
    import random

    # 固定种子：演示数据必须可复现，这里要的恰恰是「可预测」。
    rng = random.Random(20260928)
    now_ms = int(time.time() * 1000)
    rows = [
        {
            "chunk_id": f"demo#{i}",
            "doc_id": f"doc-{i % 3}",
            "text": text,
            "text_hash": f"h{i}",
            "created_at": now_ms,
            "updated_at": now_ms,
            "source": "demo",
            "parent_chunk_id": "",
            "acl": "",
            "tenant_id": "eval",
            "dataset_id": "demo",
            "document_revision": 1,
            "content_revision": 1,
            "metadata": {"heading": "演示"},
            "dense_vector": _unit_vector([rng.uniform(-1, 1) for _ in range(cfg.dim)], base=i),
        }
        for i, text in enumerate(
            [
                "RAG4C 的混合检索由 BGE-M3 稠密向量与 Milvus 内置 BM25 稀疏检索组成。",
                "候选放大系数决定重排能看到多少候选，实测 3 倍是甜点。",
                "MMR 来源多样性在本评测集上是负收益，因为正确段落常集中在同一篇文档。",
                "向量维度必须与嵌入模型输出一致，BGE-M3 是 1024 维。",
            ]
        )
    ]

    with MilvusVectorStore(cfg) as store:
        created = store.ensure_collection(drop_if_exists=True)
        print(f"集合 {cfg.collection} 新建={created}")
        print("写入条数:", store.upsert(rows))
        store.flush()
        print("当前行数:", store.count())

        # 用第一条的向量当查询，验证"能把自己找回来"
        hits = store.search(rows[0]["dense_vector"], top_k=3, filter_expr='tenant_id == "eval"')
        for hit in hits:
            entity = hit["entity"]
            print(f"  score={hit['score']:.4f} {entity.get('chunk_id')} :: {entity.get('text', '')[:40]}")
        if cfg.enable_bm25:
            text_hits = store.search_text("MMR 多样性", top_k=2)
            print("BM25 命中:", [h["entity"].get("chunk_id") for h in text_hits])
        if not keep:
            store.drop()
            print("已清理演示集合")
    return 0


def _unit_vector(values: Sequence[float], *, base: int) -> list[float]:
    """演示用：造一个归一化向量（真实场景换成 embedder.embed_query 的输出）。"""
    import math

    vec = [v + 0.01 * base for v in values]
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Milvus 向量库（pymilvus 官方 SDK）自检与演示")
    parser.add_argument("--check", action="store_true", help="只打印配置/Schema/索引（不连服务端）")
    parser.add_argument("--probe", action="store_true", help="连接并列出集合")
    parser.add_argument("--demo", action="store_true", help="建集合 -> 写入 -> 检索 -> 清理")
    parser.add_argument("--keep", action="store_true", help="演示后保留集合")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = MilvusStoreConfig.from_env()
    print(json.dumps(cfg.as_dict(), ensure_ascii=False, indent=2))

    if args.check:
        schema = build_schema(cfg)
        print("字段:", [f.get("name") for f in schema.fields] if hasattr(schema, "fields") else schema)
        print("索引参数:", json.dumps(build_index_params(cfg)._indexes if hasattr(build_index_params(cfg), "_indexes") else [], ensure_ascii=False, default=str))
        print("检索参数:", json.dumps(build_search_params(cfg, 10), ensure_ascii=False))
        return 0
    if args.probe:
        with MilvusVectorStore(cfg) as store:
            print("集合列表:", store.client.list_collections())
            print("目标集合存在:", store.has_collection())
        return 0
    if args.demo:
        return _demo(cfg, keep=args.keep)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
