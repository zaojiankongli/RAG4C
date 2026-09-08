"""RAG4C 核心数据模型（pydantic v2）。

这些模型是全系统共享的"数据契约"：
- :class:`Chunk`           —— 检索单元（索引入库 / 检索返回的最小粒度）
- :class:`RetrievedChunk`  —— 检索结果（带分数 / 排名 / 来源分支）
- :class:`Citation`        —— 引用（答案中某句 claim 的证据定位）
- :class:`QueryResult`     —— 完整问答结果（含引用、弃权、路由、轨迹）
- :class:`RouteDecision`   —— 路由决策
- :class:`JudgeResult`     —— 裁判（Groundedness / Relevance）输出

所有模型均使用 pydantic v2（``model_config = ConfigDict(...)``）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# 业务枚举（保持为 Literal 字符串，方便与 JSON / 数据库字段直接互转）


class Chunk(BaseModel):
    """检索单元。

    注意：``metadata`` 必须是 JSON 可序列化的 dict，
    因为它在 Milvus 中存放于 JSON 字段。

    ``tenant_id`` 为多租户隔离维度：检索 / 入库强制按租户过滤，
    空串表示未指定（由强制层解析为默认租户或按开关忽略）。
    """

    model_config = ConfigDict(extra="ignore")

    chunk_id: str
    doc_id: str
    text: str
    text_hash: str
    created_at: datetime
    updated_at: datetime
    tenant_id: str = ""
    # 知识库维度（多租户 SaaS：tenant -> dataset -> document -> chunk）。
    # 空串表示未指定（由强制层解析为默认数据集 default）
    dataset_id: str = ""
    document_revision: int = 0
    content_revision: int = 0
    source: str | None = None
    parent_chunk_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievedChunk(BaseModel):
    """一条检索结果：Chunk + 检索得分 + 排名 + 来源分支。

    branch 用于区分该条来自：
    - ``hybrid``：Milvus 混合检索（稠密 + BM25）
    - ``graph`` ：图谱检索（后续 vector_graph_rag 路由使用）

    ``dense_cosine`` 与 ``score`` 是**两把不同的尺子**，不可互换、更不可共用
    阈值：``score`` 在 rerank 生效时是重排器归一化后的相关性分，未生效时是
    Milvus 的 RRF 融合分（只由名次决定，不含相似度信息）；``dense_cosine``
    始终是查询向量与该 chunk 存储向量的真实余弦。实测这两把尺子的量级差得
    很远——见 :class:`verify.abstention.AbstentionGate` 的说明。

    向量本身刻意**不出现在任何模型字段上**：算余弦需要的两个向量（查询的和
    命中的）在 ``RagMilvusClient.hybrid_search`` 里同时在手，那里算完只带一个
    float 出来即可。把 1024 维原始向量挂到 Chunk 或 RetrievedChunk 上，既会让
    "读回再写回"的往返把检索期数据当成存储数据写进库，也会让每一份缓存、每一
    次序列化凭空胖上几 KB——而下游没有任何一处真正需要那串数。
    """

    model_config = ConfigDict(extra="ignore")

    chunk: Chunk
    score: float
    rank: int
    branch: Literal["hybrid", "graph"] = "hybrid"
    #: 查询向量与本 chunk 稠密向量的余弦（仅在检索时显式索取向量才有值）。
    dense_cosine: float | None = None


class Citation(BaseModel):
    """答案中的一条引用。

    status 语义（三层引用验证后的结论）：
    - ``ok``          ：该 claim 被 chunk 证据支撑
    - ``exists_only`` ：证据存在但仅部分/弱支撑
    - ``stale``       ：证据在生成后发生变更
    - ``unsupported`` ：证据不足，claim 无支撑
    """

    model_config = ConfigDict(extra="ignore")

    claim: str
    chunk_id: str
    status: Literal["ok", "exists_only", "stale", "unsupported"]
    reason: str = ""


class QueryResult(BaseModel):
    """一次问答的完整结果。

    - ``verdict``：裁判整体结论（Groundedness / Relevance），key 由后续模块约定
    - ``abstained``：是否触发双重阈值弃权
    - ``route``：实际执行的路由（见 RouteDecision.target）
    - ``traces``：流水追踪信息（配合 core.tracing）
    """

    model_config = ConfigDict(extra="ignore")

    query: str
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    verdict: dict[str, Any] = Field(default_factory=dict)
    abstained: bool = False
    route: str = "hybrid"
    traces: list[str] = Field(default_factory=list)


class RouteDecision(BaseModel):
    """路由决策（由 router_llm 槽位输出）。

    target 语义：
    - ``hybrid``          ：默认，纯混合检索
    - ``vector_graph_rag``：向量 + 图谱增强
    - ``full``            ：全链路（含生成 / 验证 / 裁判）
    """

    model_config = ConfigDict(extra="ignore")

    target: Literal["hybrid", "vector_graph_rag", "full"]
    confidence: float = 0.0
    degraded: bool = False


class JudgeResult(BaseModel):
    """裁判（judge 槽位）输出。

    - ``score``：0~1 的支撑度 / 相关性得分（None 表示无法判定）
    - ``unsupported_claims``：答案中被判定为无支撑的 claim
    - ``missing_facts``：答案需要但检索证据中缺失的事实
    - ``raw``：LLM 返回的原始 JSON，供排查
    """

    model_config = ConfigDict(extra="ignore")

    score: float | None = None
    rationale: str = ""
    unsupported_claims: list[str] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class Triplet(BaseModel):
    """知识图谱三元组：主体 - 谓词 - 客体（vector-graph-rag 方案）。

    大小写不敏感去重：同名三元组（忽略大小写）视为同一关系。
    """

    model_config = ConfigDict(extra="ignore")

    subject: str
    predicate: str
    object: str

    def to_relation_text(self) -> str:
        """三元组 -> 关系全文（用于向量化 / 展示）。"""
        return f"{self.subject} {self.predicate} {self.object}"

    def __hash__(self) -> int:
        return hash((self.subject.lower(), self.predicate.lower(), self.object.lower()))

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Triplet):
            return False
        return (
            self.subject.lower() == other.subject.lower()
            and self.predicate.lower() == other.predicate.lower()
            and self.object.lower() == other.object.lower()
        )


class GraphEntity(BaseModel):
    """知识图谱实体节点。

    - ``relation_ids``：直接相连的关系 ID（子图扩展的邻接信息）
    - ``passage_ids``  ：该实体出现过的 chunk（passage）ID
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    relation_ids: list[str] = Field(default_factory=list)
    passage_ids: list[str] = Field(default_factory=list)


class GraphRelation(BaseModel):
    """知识图谱关系边（三元组全文）。

    - ``entity_ids``  ：主语与宾语实体 ID（head / tail）
    - ``passage_ids`` ：该关系被抽取自的 chunk（passage）ID
    - ``subject``/``predicate``/``object``：结构化三元组，便于过滤与展示
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    text: str
    entity_ids: list[str] = Field(default_factory=list)
    passage_ids: list[str] = Field(default_factory=list)
    subject: str = ""
    predicate: str = ""
    object: str = ""


class GraphRetrievalDetail(BaseModel):
    """一次图检索的中间结果明细（供追踪与评测）。

    - ``query_entities``：从查询中提取的实体
    - ``entity_texts`` / ``entity_scores``：实体向量检索命中的实体与相似度
    - ``relation_texts`` / ``relation_scores``：关系向量检索命中的关系与相似度
    - ``expanded_relation_ids``：子图扩展后收集的关系 ID
    - ``reranked_relation_ids``：LLM 重排后保留的关系 ID
    """

    model_config = ConfigDict(extra="ignore")

    query_entities: list[str] = Field(default_factory=list)
    entity_texts: list[str] = Field(default_factory=list)
    entity_scores: list[float] = Field(default_factory=list)
    relation_texts: list[str] = Field(default_factory=list)
    relation_scores: list[float] = Field(default_factory=list)
    expanded_relation_ids: list[str] = Field(default_factory=list)
    reranked_relation_ids: list[str] = Field(default_factory=list)


__all__ = [
    "Chunk",
    "RetrievedChunk",
    "Citation",
    "QueryResult",
    "RouteDecision",
    "JudgeResult",
    "Triplet",
    "GraphEntity",
    "GraphRelation",
    "GraphRetrievalDetail",
]
