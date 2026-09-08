"""知识图谱向量存储（vector-graph-rag 方案的 Milvus 落地）。

参照 zilliztech/vector-graph-rag 官方存储设计，管理两张图专用集合：

- **Entity 集合**（默认 ``rag4c_entities``）：实体节点。
  统一 schema：``id``(VARCHAR 主键) + ``vector``(FLOAT_VECTOR) + ``text``(VARCHAR)，
  开启动态字段，邻接信息存入动态字段：
  - ``relation_ids``：直接相连的关系 ID（子图扩展的跳转依据）
  - ``passage_ids``  ：该实体出现过的 chunk（passage）ID
- **Relation 集合**（默认 ``rag4c_relations``）：关系边（三元组全文）。
  动态字段：
  - ``entity_ids``  ：主语与宾语实体 ID
  - ``passage_ids`` ：该关系被抽取自的 chunk（passage）ID
  - ``subject`` / ``predicate`` / ``object``：结构化三元组

passage 角色由现有 ``rag4c_chunks``（:class:`~core.milvus_client.RagMilvusClient`）
承担，图检索命中后按 chunk_id 回查文本，避免文本双写。

pymilvus 为**可选依赖**（惰性导入），未安装时本模块可正常 import。
所有 Milvus 调用错误统一包装为 :class:`RagGraphStoreError`。
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from config.settings import GraphSettings, MilvusSettings
from core.observability import get_logger
from models.schemas import GraphEntity, GraphRelation

_logger = get_logger("core.graph_store")

# Milvus 对 query 的默认返回条数上限。超过这个数服务端会**静默截断**，
# 既不报错也没有"还有更多"的标记，因此凡是可能返回大结果集的 query
# 都显式带上 limit，并在结果刚好顶到上限时告警——否则级联删除会漏删、
# 子图扩展会莫名其妙地缺边，而且很难查。
_QUERY_LIMIT = 16384


class RagGraphStoreError(RuntimeError):
    """知识图谱存储错误（连接 / 集合 / 读写 / 检索）。"""


def _quote_string(value: str) -> str:
    """Milvus 过滤表达式中字符串值的引号转义。"""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _and_filters(*clauses: Optional[str]) -> Optional[str]:
    """把多个可选过滤子句 AND 拼接；全部为空返回 None（= 不过滤）。"""
    parts = [c for c in clauses if c]
    return " and ".join(parts) if parts else None


class RagGraphStore:
    """知识图谱向量存储（实体 / 关系两张集合）。

    用法:
        store = RagGraphStore(settings.milvus, settings.graph)
        store.ensure_collections()
        store.upsert_entities(entities, vectors)
        hits = store.search_entities(query_vec, top_k=20, threshold=0.9)

    Attributes:
        config: MilvusSettings。
        graph: GraphSettings。
        mode: ``"lite"`` 或 ``"server"``（由 uri 自动判定）。
    """

    # 检索时返回的字段（实体 / 关系通用）；tenant_id 用于租户维度过滤
    _ENTITY_OUTPUT_FIELDS = ["id", "text", "relation_ids", "passage_ids", "tenant_id"]
    _RELATION_OUTPUT_FIELDS = [
        "id",
        "text",
        "entity_ids",
        "passage_ids",
        "subject",
        "predicate",
        "object",
        "tenant_id",
    ]

    def __init__(self, config: MilvusSettings, graph: GraphSettings) -> None:
        self.config = config
        self.graph = graph
        self._client: Any = None  # pymilvus.MilvusClient，惰性连接
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

    def _ensure_client(self) -> Any:
        """惰性创建底层 MilvusClient。"""
        if self._client is not None:
            return self._client
        try:
            from pymilvus import MilvusClient
        except ImportError as exc:
            raise RagGraphStoreError(
                "pymilvus 未安装。请执行 `pip install 'pymilvus[milvus-lite]>=3.0.0,<4'`。"
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
            raise RagGraphStoreError(
                f"创建 MilvusClient 失败（uri={self.config.uri!r}, mode={self.mode}）: {exc}"
            ) from exc
        return self._client

    def close(self) -> None:
        """关闭底层连接（如适用）。"""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    # ------------------------------------------------------------------ #
    # 集合 Schema 与索引
    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_schema(client: Any, dim: int) -> Any:
        """构建图集合 Schema（官方设计：id + vector + text + 动态字段）。"""
        from pymilvus import DataType

        schema = client.create_schema(auto_id=False, enable_dynamic_field=True)
        schema.add_field(
            field_name="id", datatype=DataType.VARCHAR, max_length=64, is_primary=True
        )
        schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=dim)
        schema.add_field(field_name="text", datatype=DataType.VARCHAR, max_length=65535)
        # 租户维度：与 chunks 集合一致，显式字段 + 过滤表达式（动态字段亦可存，
        # 但显式字段便于建立过滤索引与 schema 校验）
        schema.add_field(
            field_name="tenant_id", datatype=DataType.VARCHAR, max_length=128
        )
        return schema

    @staticmethod
    def _tenant_clause(tenant_id: Optional[str]) -> Optional[str]:
        """构造租户过滤子句；空串 / None 返回 None（= 不过滤）。"""
        if not tenant_id:
            return None
        return f"tenant_id == {_quote_string(tenant_id)}"

    def _build_index_params(self, client: Any) -> Any:
        """构建稠密向量索引（与 chunks 集合一致：HNSW + COSINE）。"""
        index_params: Any = client.prepare_index_params()
        params: dict[str, Any]
        itype = self.config.index_type.upper()
        if itype in ("HNSW",):
            params = {"M": self.config.m, "efConstruction": self.config.ef_construction}
        elif itype in ("IVF_FLAT", "IVF_SQ8", "IVF_PQ"):
            params = {"nlist": self.config.nlist}
        else:  # AUTOINDEX / FLAT / BRUTE_FORCE
            params = {}
        index_params.add_index(
            field_name="vector",
            index_type=itype,
            metric_type=self.config.metric_type,
            params=params,
        )
        return index_params

    def ensure_collections(self, dim: Optional[int] = None, drop_existing: bool = False) -> None:
        """确保实体 / 关系两张集合存在（不存在则创建），幂等。

        Args:
            dim: 向量维度；None 时使用配置（默认 1024 = BGE-M3）。
            drop_existing: 为 True 时先删除已存在的同名集合再重建。
        """
        dim = dim or self.config.dim
        client = self._ensure_client()
        for collection_name in (self.config.entity_collection, self.config.relation_collection):
            try:
                if client.has_collection(collection_name):
                    if drop_existing:
                        client.drop_collection(collection_name)
                    else:
                        continue
                schema = self._build_schema(client, dim)
                index_params = self._build_index_params(client)
                client.create_collection(
                    collection_name=collection_name,
                    schema=schema,
                    index_params=index_params,
                )
            except RagGraphStoreError:
                raise
            except Exception as exc:
                raise RagGraphStoreError(
                    f"确保图集合 {collection_name!r} 存在失败: {exc}"
                ) from exc
            # 加载到内存（Milvus Lite 已自动加载，重复调用会被忽略）
            try:
                client.load_collection(collection_name)
            except Exception:
                pass

    def drop_collections(self) -> None:
        """删除实体 / 关系两张集合。"""
        client = self._ensure_client()
        for collection_name in (self.config.entity_collection, self.config.relation_collection):
            try:
                if client.has_collection(collection_name):
                    client.drop_collection(collection_name)
            except Exception as exc:
                raise RagGraphStoreError(
                    f"删除图集合 {collection_name!r} 失败: {exc}"
                ) from exc

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #
    def upsert_entities(
        self,
        entities: Sequence[GraphEntity],
        vectors: Optional[Sequence[Sequence[float]]] = None,
        tenant_id: str = "",
    ) -> list[str]:
        """批量 upsert 实体节点。

        Args:
            entities: 实体列表（``id`` 为主键，存在则覆盖）。
            vectors: 与 entities 等长的向量；None 时返回空列表且不写入
                （调用方必须预计算向量——与 chunks 集合一致，向量由独立的
                EmbeddingService 生成）。
            tenant_id: 租户标识；写入每条记录的 ``tenant_id`` 字段
                （入库侧已解析为有效租户，空串保留原样）。

        Returns:
            写入的实体 ID 列表。
        """
        return self._upsert_records(
            collection_name=self.config.entity_collection,
            records=[
                {
                    "id": e.id,
                    "text": e.name,
                    "vector": list(vec),
                    "relation_ids": list(e.relation_ids),
                    "passage_ids": list(e.passage_ids),
                    "tenant_id": tenant_id,
                }
                for e, vec in zip(entities, vectors or [])
            ],
            expected_count=len(entities) if vectors is not None else 0,
        )

    def upsert_relations(
        self,
        relations: Sequence[GraphRelation],
        vectors: Optional[Sequence[Sequence[float]]] = None,
        tenant_id: str = "",
    ) -> list[str]:
        """批量 upsert 关系边。

        Args:
            relations: 关系列表（``id`` 为主键，存在则覆盖）。
            vectors: 与 relations 等长的向量；None 时返回空列表且不写入。
            tenant_id: 租户标识；写入每条记录的 ``tenant_id`` 字段。

        Returns:
            写入的关系 ID 列表。
        """
        return self._upsert_records(
            collection_name=self.config.relation_collection,
            records=[
                {
                    "id": r.id,
                    "text": r.text,
                    "vector": list(vec),
                    "entity_ids": list(r.entity_ids),
                    "passage_ids": list(r.passage_ids),
                    "subject": r.subject,
                    "predicate": r.predicate,
                    "object": r.object,
                    "tenant_id": tenant_id,
                }
                for r, vec in zip(relations, vectors or [])
            ],
            expected_count=len(relations) if vectors is not None else 0,
        )

    def _upsert_records(
        self,
        collection_name: str,
        records: list[dict[str, Any]],
        expected_count: int,
    ) -> list[str]:
        """按批大小 upsert 记录，返回主键 ID 列表。"""
        if not records:
            return []
        if expected_count != len(records):
            raise RagGraphStoreError(
                f"向量数量({expected_count})与记录数量({len(records)})不一致。"
            )
        client = self._ensure_client()
        batch_size = max(self.graph.batch_size, 1)
        ids: list[str] = []
        try:
            for start in range(0, len(records), batch_size):
                batch = records[start : start + batch_size]
                res = client.upsert(collection_name=collection_name, data=batch)
                returned = res.get("ids")
                if returned:
                    ids.extend(str(i) for i in returned)
                else:
                    ids.extend(str(r["id"]) for r in batch)
        except RagGraphStoreError:
            raise
        except Exception as exc:
            raise RagGraphStoreError(f"upsert 图记录失败（{collection_name}）: {exc}") from exc
        return ids

    # ------------------------------------------------------------------ #
    # 读取
    # ------------------------------------------------------------------ #
    def get_entities_by_ids(
        self,
        entity_ids: Sequence[str],
        tenant_id: str = "",
        *,
        include_vectors: bool = False,
    ) -> list[dict[str, Any]]:
        """按 ID 取回实体（含邻接信息）。

        Args:
            entity_ids: 实体 ID 列表。
            tenant_id: 租户标识；非空时过滤表达式 AND 租户条件，
                避免跨租户读到同名实体（entity_id 为全局稳定哈希，
                租户过滤属防御性收口）。
        """
        return self._get_by_ids(
            self.config.entity_collection,
            entity_ids,
            self._ENTITY_OUTPUT_FIELDS + (["vector"] if include_vectors else []),
            tenant_id,
        )

    def get_relations_by_ids(
        self, relation_ids: Sequence[str], tenant_id: str = ""
    ) -> list[dict[str, Any]]:
        """按 ID 取回关系（含结构化三元组与邻接信息）。"""
        return self._get_by_ids(
            self.config.relation_collection,
            relation_ids,
            self._RELATION_OUTPUT_FIELDS,
            tenant_id,
        )

    def _get_by_ids(
        self,
        collection_name: str,
        ids: Sequence[str],
        output_fields: list[str],
        tenant_id: str = "",
    ) -> list[dict[str, Any]]:
        if not ids:
            return []
        client = self._ensure_client()
        ids_str = ", ".join(_quote_string(str(i)) for i in ids)
        expr = _and_filters(f"id in [{ids_str}]", self._tenant_clause(tenant_id))
        try:
            results = client.query(
                collection_name=collection_name,
                filter=expr,
                output_fields=output_fields,
            )
        except Exception as exc:
            raise RagGraphStoreError(f"按 id 查询失败（{collection_name}）: {exc}") from exc
        return list(results)

    def get_entities_by_texts(
        self, texts: Sequence[str], tenant_id: str = ""
    ) -> dict[str, dict[str, Any]]:
        """按 text 精确查重实体（增量更新用），返回 ``text -> 记录`` 映射。

        记录包含 id / relation_ids / passage_ids，供去重后合并邻接信息。
        查重范围受租户约束：不同租户的同名实体互不合并。

        Args:
            texts: 待查重的文本列表。
            tenant_id: 租户标识；非空时仅在当前租户内查重。
        """
        return self._query_by_texts(
            self.config.entity_collection,
            texts,
            self._ENTITY_OUTPUT_FIELDS,
            tenant_id,
        )

    def get_relations_by_texts(
        self, texts: Sequence[str], tenant_id: str = ""
    ) -> dict[str, dict[str, Any]]:
        """按 text 精确查重关系（增量更新用），返回 ``text -> 记录`` 映射。"""
        return self._query_by_texts(
            self.config.relation_collection,
            texts,
            self._RELATION_OUTPUT_FIELDS,
            tenant_id,
        )

    def _query_by_texts(
        self,
        collection_name: str,
        texts: Sequence[str],
        output_fields: list[str],
        tenant_id: str = "",
    ) -> dict[str, dict[str, Any]]:
        """按 text 批量精确查询（去重、保序）；非空租户限定查询范围。"""
        if not texts:
            return {}
        client = self._ensure_client()
        unique: list[str] = []
        seen: set[str] = set()
        for t in texts:
            if t not in seen:
                seen.add(t)
                unique.append(t)

        records_by_text: dict[str, dict[str, Any]] = {}
        batch_size = max(self.graph.batch_size, 1)
        try:
            for start in range(0, len(unique), batch_size):
                batch = unique[start : start + batch_size]
                quoted = ", ".join(_quote_string(t) for t in batch)
                expr = _and_filters(
                    f"text in [{quoted}]", self._tenant_clause(tenant_id)
                )
                results = client.query(
                    collection_name=collection_name,
                    filter=expr,
                    output_fields=output_fields,
                )
                for record in results:
                    text = record.get("text")
                    if isinstance(text, str) and text not in records_by_text:
                        records_by_text[text] = record
        except Exception as exc:
            raise RagGraphStoreError(f"按 text 查询失败（{collection_name}）: {exc}") from exc
        return records_by_text

    def get_relations_by_passage_ids(
        self,
        passage_ids: Sequence[str],
        tenant_id: str = "",
        *,
        include_vectors: bool = False,
    ) -> list[dict[str, Any]]:
        """按 passage_ids 取回关系（子图扩展 / 级联删除用）。

        返回 ``passage_ids`` 包含任一给定 chunk_id 的关系记录；
        非空租户限定查询范围（级联删除不跨租户）。

        Args:
            passage_ids: chunk（passage）ID 列表。
            tenant_id: 租户标识；非空时 AND 租户条件。
        """
        if not passage_ids:
            return []
        return self._query_by_json_contains(
            collection_name=self.config.relation_collection,
            json_field="passage_ids",
            values=passage_ids,
            output_fields=self._RELATION_OUTPUT_FIELDS
            + (["vector"] if include_vectors else []),
            tenant_id=tenant_id,
            error_label="按 passage_ids 查询关系失败",
        )

    def get_entities_by_passage_ids(
        self,
        passage_ids: Sequence[str],
        tenant_id: str = "",
        *,
        include_vectors: bool = False,
    ) -> list[dict[str, Any]]:
        """Return entities whose provenance references any requested chunk ID."""
        if not passage_ids:
            return []
        return self._query_by_json_contains(
            collection_name=self.config.entity_collection,
            json_field="passage_ids",
            values=passage_ids,
            output_fields=self._ENTITY_OUTPUT_FIELDS
            + (["vector"] if include_vectors else []),
            tenant_id=tenant_id,
            error_label="按 passage_ids 查询实体失败",
        )

    def get_relations_by_entity_ids(
        self, entity_ids: Sequence[str], tenant_id: str = ""
    ) -> list[dict[str, Any]]:
        """按 entity_ids 取回关系（级联删除判定用）。

        返回 ``entity_ids`` 包含任一给定实体 ID 的关系记录；
        非空租户限定查询范围。

        Args:
            entity_ids: 实体 ID 列表。
            tenant_id: 租户标识；非空时 AND 租户条件。
        """
        if not entity_ids:
            return []
        return self._query_by_json_contains(
            collection_name=self.config.relation_collection,
            json_field="entity_ids",
            values=entity_ids,
            output_fields=self._RELATION_OUTPUT_FIELDS,
            tenant_id=tenant_id,
            error_label="按 entity_ids 查询关系失败",
        )

    def _query_by_json_contains(
        self,
        collection_name: str,
        json_field: str,
        values: Sequence[str],
        output_fields: list[str],
        tenant_id: str,
        error_label: str,
    ) -> list[dict[str, Any]]:
        """``JSON_CONTAINS(field, v)`` 的 OR 查询，分批 + 去重 + 上限告警。

        这类查询有两条容易踩空的边：

        1. **表达式长度**：把上千个 ID 拼成一条 OR 表达式，请求体会很大，
           且服务端解析代价随子句数增长。按 ``graph.batch_size`` 分批。
        2. **静默截断**：query 有服务端条数上限（见 ``_QUERY_LIMIT``），
           顶到上限时**不报错**。这两个调用点同时服务于级联删除，漏一条
           就会留下孤儿关系，所以这里显式下发 limit 并在顶到上限时告警，
           把"悄悄漏数据"变成"日志里能看见"。

        跨批可能返回同一条关系（一条关系可以同时命中多个 ID），按主键
        ``id`` 去重，并保持首次出现的顺序。
        """
        if not values:
            return []
        client = self._ensure_client()
        batch_size = max(self.graph.batch_size, 1)
        tenant_clause = self._tenant_clause(tenant_id)
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        unique_values = list(dict.fromkeys(str(v) for v in values))
        try:
            for start in range(0, len(unique_values), batch_size):
                batch = unique_values[start : start + batch_size]
                clauses = " or ".join(
                    f"JSON_CONTAINS({json_field}, {_quote_string(v)})" for v in batch
                )
                expr = _and_filters(clauses, tenant_clause)
                results = client.query(
                    collection_name=collection_name,
                    filter=expr,
                    output_fields=output_fields,
                    limit=_QUERY_LIMIT,
                )
                results = list(results or [])
                if len(results) >= _QUERY_LIMIT:
                    _logger.warning(
                        "图关系查询顶到条数上限 %d（collection=%s field=%s 批次大小=%d），"
                        "结果可能不完整；如用于级联删除请调小 graph.batch_size 后重试",
                        _QUERY_LIMIT,
                        collection_name,
                        json_field,
                        len(batch),
                    )
                for record in results:
                    rid = str(record.get("id") or "")
                    if rid and rid in seen:
                        continue
                    if rid:
                        seen.add(rid)
                    out.append(record)
        except Exception as exc:
            raise RagGraphStoreError(f"{error_label}: {exc}") from exc
        return out

    # ------------------------------------------------------------------ #
    # 检索
    # ------------------------------------------------------------------ #
    def search_entities(
        self,
        query_vec: Sequence[float],
        top_k: Optional[int] = None,
        threshold: Optional[float] = None,
        tenant_id: str = "",
    ) -> list[dict[str, Any]]:
        """实体向量检索，返回按相似度降序的命中列表。

        每个命中：``id`` / ``text`` / ``relation_ids`` / ``passage_ids`` / ``distance``。
        相似度（COSINE）低于 ``threshold`` 的命中被过滤。

        Args:
            query_vec: 查询向量。
            top_k: 候选条数；None 时用 GraphSettings.entity_top_k。
            threshold: 相似度阈值；None 时用
                GraphSettings.entity_similarity_threshold（0.9）。
            tenant_id: 租户标识；非空时 search 带过滤表达式，只返回
                当前租户的实体（跨租户实体零可见）。
        """
        return self._search(
            collection_name=self.config.entity_collection,
            query_vec=query_vec,
            top_k=top_k if top_k is not None else self.graph.entity_top_k,
            threshold=(
                threshold
                if threshold is not None
                else self.graph.entity_similarity_threshold
            ),
            output_fields=self._ENTITY_OUTPUT_FIELDS,
            tenant_id=tenant_id,
        )

    def search_relations(
        self,
        query_vec: Sequence[float],
        top_k: Optional[int] = None,
        threshold: Optional[float] = None,
        tenant_id: str = "",
    ) -> list[dict[str, Any]]:
        """关系向量检索，返回按相似度降序的命中列表。

        每个命中：``id`` / ``text`` / ``entity_ids`` / ``passage_ids`` /
        ``subject`` / ``predicate`` / ``object`` / ``distance``。

        Args:
            query_vec: 查询向量。
            top_k: 候选条数；None 时用 GraphSettings.relation_top_k。
            threshold: 相似度阈值；None 时用
                GraphSettings.relation_similarity_threshold（-1.0 即不过滤）。
            tenant_id: 租户标识；非空时 search 带过滤表达式，只返回
                当前租户的关系。
        """
        return self._search(
            collection_name=self.config.relation_collection,
            query_vec=query_vec,
            top_k=top_k if top_k is not None else self.graph.relation_top_k,
            threshold=(
                threshold
                if threshold is not None
                else self.graph.relation_similarity_threshold
            ),
            output_fields=self._RELATION_OUTPUT_FIELDS,
            tenant_id=tenant_id,
        )

    def _search_params(self) -> dict[str, Any]:
        """检索期参数：度量方式 + 按索引类型的调节旋钮。

        必须显式下发。``MilvusClient.search`` 的 ``search_params`` 默认是
        ``None``，会被当成空字典——此前这里根本没传，导致 ``metric_type``
        与 ``ef`` / ``nprobe`` **一次都没发出去**，服务端一路用索引默认值。
        图检索的相似度阈值（``entity_similarity_threshold`` 等）是拿
        distance 直接比的，度量方式不对会让阈值筛选的语义整个错位，
        所以这不只是"调不动性能"，而是会影响结果正确性。

        调节项放进 ``params`` 子字典，与 ``RagMilvusClient.hybrid_search``
        保持同一种写法。平铺写法在 ``AnnSearchRequest.param`` 这条路径上
        已确认等价（pymilvus 会把顶层键合并进 params），但 ``search`` 的
        ``search_params`` 是否走同一段合并逻辑没有查证过——既然没验证，
        就统一用嵌套这种两边都成立的写法，别赌。

        取值逻辑与 hybrid_search 一致：HNSW 用 ef，IVF 系用 nprobe，
        其它索引类型（AUTOINDEX / FLAT）不带调节项。
        """
        params: dict[str, Any] = {"metric_type": self.config.metric_type}
        tune: dict[str, Any] = {}
        itype = self.config.index_type.upper()
        if itype == "HNSW":
            tune["ef"] = self.config.ef
        elif itype.startswith("IVF"):
            tune["nprobe"] = self.config.nprobe
        if tune:
            params["params"] = tune
        return params

    def _search(
        self,
        collection_name: str,
        query_vec: Sequence[float],
        top_k: int,
        threshold: float,
        output_fields: list[str],
        tenant_id: str = "",
    ) -> list[dict[str, Any]]:
        if top_k < 1:
            raise RagGraphStoreError("top_k 必须 >= 1")
        client = self._ensure_client()
        search_params = self._search_params()
        # ef 必须 >= limit（Milvus 约束），top_k 大于配置 ef 时自动抬高
        tune = search_params.get("params")
        if isinstance(tune, dict) and "ef" in tune:
            tune["ef"] = max(int(tune["ef"]), top_k)
        try:
            results = client.search(
                collection_name=collection_name,
                data=[list(query_vec)],
                limit=top_k,
                output_fields=output_fields,
                # 无租户约束时传空串而非 None：形参签名是 ``filter: str = ""``
                filter=self._tenant_clause(tenant_id) or "",
                search_params=search_params,
            )
        except Exception as exc:
            raise RagGraphStoreError(f"图向量检索失败（{collection_name}）: {exc}") from exc
        hits = results[0] if results else []
        out: list[dict[str, Any]] = []
        for hit in hits:
            entity = hit.get("entity") or hit
            distance = float(hit.get("distance", 0.0))
            # COSINE 度量下 distance 即相似度（越高越相似）
            if distance < threshold:
                continue
            record = dict(entity)
            record["distance"] = distance
            out.append(record)
        return out

    def _upsert_raw_records(
        self, collection_name: str, records: Sequence[dict[str, Any]]
    ) -> int:
        if not records:
            return 0
        client = self._ensure_client()
        rows = [dict(record) for record in records]
        batch_size = max(1, int(self.graph.batch_size))
        try:
            for start in range(0, len(rows), batch_size):
                client.upsert(
                    collection_name=collection_name,
                    data=rows[start : start + batch_size],
                )
        except Exception as exc:
            raise RagGraphStoreError(
                f"更新图记录失败（{collection_name}）: {exc}"
            ) from exc
        return len(rows)

    def upsert_raw_entities(self, records: Sequence[dict[str, Any]]) -> int:
        """Rewrite queried entity rows while preserving their stored vectors."""
        return self._upsert_raw_records(self.config.entity_collection, records)

    def upsert_raw_relations(self, records: Sequence[dict[str, Any]]) -> int:
        """Rewrite queried relation rows while preserving their stored vectors."""
        return self._upsert_raw_records(self.config.relation_collection, records)

    # ------------------------------------------------------------------ #
    # 删除
    # ------------------------------------------------------------------ #
    def delete_entities_by_ids(self, entity_ids: Sequence[str]) -> int:
        """按 ID 删除实体，返回删除条数。"""
        return self._delete_by_ids(self.config.entity_collection, entity_ids)

    def delete_relations_by_ids(self, relation_ids: Sequence[str]) -> int:
        """按 ID 删除关系，返回删除条数。"""
        return self._delete_by_ids(self.config.relation_collection, relation_ids)

    def _delete_by_ids(self, collection_name: str, ids: Sequence[str]) -> int:
        if not ids:
            return 0
        client = self._ensure_client()
        try:
            res = client.delete(collection_name=collection_name, ids=list(ids))
        except Exception as exc:
            raise RagGraphStoreError(f"按 id 删除失败（{collection_name}）: {exc}") from exc
        return int(res.get("delete_count", 0) or 0)

    # ------------------------------------------------------------------ #
    # 统计
    # ------------------------------------------------------------------ #
    def count_entities(self, tenant_id: str = "") -> int:
        """实体总数（非空租户时只统计该租户）。"""
        return self._count(self.config.entity_collection, tenant_id)

    def count_relations(self, tenant_id: str = "") -> int:
        """关系总数（非空租户时只统计该租户）。"""
        return self._count(self.config.relation_collection, tenant_id)

    def _count(self, collection_name: str, tenant_id: str = "") -> int:
        """按 count(*) 聚合统计，而不是把行拉回来数长度。

        此前这里 query 出所有 id 再取 len()，有两个问题：
        一是把整张集合的主键搬到客户端，白白吃网络和内存；二是 query 有
        服务端默认条数上限（16384），超过就被静默截断——图谱一大，统计
        数字会停在上限不动，而且不报错，比慢更难发现。

        ``output_fields=["count(*)"]`` 让 Milvus 在服务端聚合，返回单行
        ``[{"count(*)": N}]``。
        """
        client = self._ensure_client()
        try:
            res = client.query(
                collection_name=collection_name,
                filter=_and_filters("id != ''", self._tenant_clause(tenant_id)),
                output_fields=["count(*)"],
            )
        except Exception as exc:
            raise RagGraphStoreError(f"统计失败（{collection_name}）: {exc}") from exc
        if not res:
            return 0
        row = res[0]
        if isinstance(row, dict):
            return int(row.get("count(*)", 0))
        return 0


__all__ = ["RagGraphStoreError", "RagGraphStore"]
