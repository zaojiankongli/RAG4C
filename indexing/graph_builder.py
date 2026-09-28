"""知识图谱索引构建器（vector-graph-rag 方案的入库端）。

职责：
1. 对每个 chunk 调用 :class:`~indexing.triplet_extractor.TripletExtractor`
   抽取三元组；
2. 组装实体节点（``GraphEntity``）与关系边（``GraphRelation``），
   写入邻接信息（``relation_ids`` / ``entity_ids`` / ``passage_ids``），
   实体按名称大小写不敏感去重，关系按三元组文本去重；
3. 与库内已有节点按 text 精确匹配合并邻接（增量更新不产生重复节点），
   主键稳定：实体 = sha256(名称小写)、关系 = sha256(三元组文本小写)；
4. 批量嵌入实体 / 关系文本，批量 upsert 到
   :class:`~core.graph_store.RagGraphStore`；
5. 按 chunk 集级联删除：先删涉及这些 chunk 的关系，再删失去所有
   关系与 passage 引用的孤儿实体。

实体与关系的向量由外部 :class:`~core.embedding.EmbeddingService` 生成
（与 chunks 集合一致，不在此处耦合具体嵌入实现）。
"""
from __future__ import annotations

import hashlib
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from core.embedding import EmbeddingService
from core.graph_projection_adapters import (
    GRAPH_BUILDER_COUNT_REQUIRED_METHODS,
    GRAPH_BUILDER_DELETE_REQUIRED_METHODS,
    GRAPH_BUILDER_WRITE_REQUIRED_METHODS,
    GraphProjectionAdapter,
    create_graph_projection_adapter,
)
from core.graph_store import RagGraphStore, RagGraphStoreError
from models.schemas import GraphEntity, GraphRelation, Triplet


class GraphBuilderError(RuntimeError):
    """图索引构建失败（嵌入 / 存储错误等环境性问题）。"""


def _stable_id(*parts: str) -> str:
    """稳定主键：对拼接文本取 sha256 前 16 位 hex（大小写不敏感）。"""
    joined = "|".join(p.lower() for p in parts if p)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _merge_unique(base: Sequence[str], extra: Sequence[str]) -> list[str]:
    """有序合并去重（保留 base 顺序，追加 extra 中新增项）。"""
    out = list(base)
    seen = set(out)
    for item in extra:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


@dataclass
class GraphBuildResult:
    """一次图构建的统计结果。"""

    chunk_count: int = 0
    triplet_count: int = 0
    entity_count: int = 0
    relation_count: int = 0
    failed_chunk_count: int = 0
    stale_skipped: bool = False


@dataclass
class GraphBuildPlan:
    """LLM/embedding output prepared before entering a projection write lock."""

    result: GraphBuildResult
    entities: list[GraphEntity]
    relations: list[GraphRelation]
    entity_vectors: list[list[float]]
    relation_vectors: list[list[float]]



class GraphBuilder:
    """知识图谱索引器。

    Args:
        store: 图存储（实体 / 关系两集合）。
        embedder: 嵌入服务（实现 :class:`~core.embedding.EmbeddingService`）。
        extractor: 三元组抽取器（:class:`TripletExtractor` 或确定性桩）。
        batch_size: 嵌入与 upsert 的批大小；None 时用 GraphSettings.batch_size。
    """

    def __init__(
        self,
        store: RagGraphStore,
        embedder: EmbeddingService,
        extractor,
        batch_size: Optional[int] = None,
        extract_concurrency: int = 8,
    ) -> None:
        self.store = store
        self.adapter: GraphProjectionAdapter = create_graph_projection_adapter(
            store,
            required_methods=(),
        )
        self.embedder = embedder
        self.extractor = extractor
        self.batch_size = batch_size or self.adapter.graph.batch_size
        # 三元组抽取的并发度：每个 chunk 一次 LLM 调用，彼此独立。
        # 上限不宜过高——远端限流与本地模型的并发能力都是瓶颈。
        self.extract_concurrency = max(1, extract_concurrency)

    def _safe_extract(self, text: str) -> tuple[list, bool]:
        """抽取单个 chunk 的三元组。

        Returns:
            ``(triplets, ok)``。``ok=False`` 表示这次抽取是**失败**的
            （LLM 超时 / 解析失败 / 模板缺失），而不是"这段文本确实没有
            三元组"——两者都返回空列表，但含义完全不同：前者需要计入
            failed_chunk_count，且全部失败时应让整次建图失败，而不是
            静默产出一个空图谱、把文档标成已完成。
        """
        try:
            return list(self.extractor.extract(text) or []), True
        except Exception:  # noqa: BLE001 - 单 chunk 失败不中断其余 chunk
            return [], False

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def prepare_build(
        self,
        chunks: Sequence[tuple[str, str]],
        progress: Any = None,
    ) -> GraphBuildPlan:
        """Run LLM extraction and embeddings without mutating graph storage."""
        result = GraphBuildResult(chunk_count=len(chunks))
        entities_by_name: dict[str, GraphEntity] = {}
        relations_by_text: dict[str, GraphRelation] = {}
        extracted: list[list] = [[] for _ in chunks]
        failed = 0
        if chunks:
            done = 0
            with ThreadPoolExecutor(
                max_workers=min(self.extract_concurrency, len(chunks)),
                thread_name_prefix="rag-triplet",
            ) as pool:
                futures = {
                    pool.submit(self._safe_extract, text): idx
                    for idx, (_, text) in enumerate(chunks)
                }
                for future in as_completed(futures):
                    triplets, ok = future.result()
                    extracted[futures[future]] = triplets
                    if not ok:
                        failed += 1
                    done += 1
                    if progress is not None:
                        try:
                            progress(done, len(chunks))
                        except Exception:  # noqa: BLE001 - progress is best effort
                            pass
        result.failed_chunk_count = failed
        if chunks and failed == len(chunks):
            raise GraphBuilderError(
                f"三元组抽取全部失败（{failed}/{len(chunks)} 个片段），"
                "请检查三元组槽位的 LLM 配置与 prompts/graph_triplet_v1.txt 是否可用"
            )
        for (chunk_id, _), triplets in zip(chunks, extracted):
            if not triplets:
                continue
            result.triplet_count += len(triplets)
            self._assemble(chunk_id, triplets, entities_by_name, relations_by_text)
        entities = list(entities_by_name.values())
        relations = list(relations_by_text.values())
        result.entity_count = len(entities)
        result.relation_count = len(relations)
        try:
            entity_vectors = (
                self._embed_texts([entity.name for entity in entities])
                if entities
                else []
            )
            relation_vectors = (
                self._embed_texts([relation.text for relation in relations])
                if relations
                else []
            )
        except Exception as exc:
            raise GraphBuilderError(f"图索引嵌入失败: {exc}") from exc
        return GraphBuildPlan(
            result=result,
            entities=entities,
            relations=relations,
            entity_vectors=entity_vectors,
            relation_vectors=relation_vectors,
        )

    def write_prepared(
        self,
        plan: GraphBuildPlan,
        *,
        tenant_id: str = "",
    ) -> GraphBuildResult:
        """Merge and mutate graph storage from a lock-protected prepared plan."""
        if not plan.relations:
            return plan.result
        self.adapter.require_methods(GRAPH_BUILDER_WRITE_REQUIRED_METHODS)
        entities = deepcopy(plan.entities)
        relations = deepcopy(plan.relations)
        self._merge_with_store(entities, relations, tenant_id)
        try:
            if entities:
                self.adapter.upsert_entities(
                    entities, plan.entity_vectors, tenant_id=tenant_id
                )
            if relations:
                self.adapter.upsert_relations(
                    relations, plan.relation_vectors, tenant_id=tenant_id
                )
        except RagGraphStoreError:
            raise
        except Exception as exc:
            raise GraphBuilderError(f"图索引写入失败: {exc}") from exc
        return plan.result

    def build(
        self,
        chunks: Sequence[tuple[str, str]],
        tenant_id: str = "",
        progress: Any = None,
        before_write: Callable[[], bool] | None = None,
    ) -> GraphBuildResult:
        """Prepare outside callers' locks, then perform the graph mutation."""
        plan = self.prepare_build(chunks, progress=progress)
        if before_write is not None and not before_write():
            plan.result.stale_skipped = True
            return plan.result
        return self.write_prepared(plan, tenant_id=tenant_id)

    def delete_by_chunk_ids(
        self, chunk_ids: Sequence[str], tenant_id: str = ""
    ) -> tuple[int, int]:
        """Remove chunk provenance without destroying facts owned by other chunks."""
        chunk_ids = list(dict.fromkeys(str(item) for item in chunk_ids if str(item)))
        if not chunk_ids:
            return 0, 0
        self.adapter.require_methods(GRAPH_BUILDER_DELETE_REQUIRED_METHODS)
        deleted_chunks = set(chunk_ids)
        try:
            relations = self.adapter.get_relations_by_passage_ids(
                chunk_ids, tenant_id=tenant_id, include_vectors=True
            )
            direct_entities = self.adapter.get_entities_by_passage_ids(
                chunk_ids, tenant_id=tenant_id, include_vectors=True
            )
        except RagGraphStoreError as exc:
            raise GraphBuilderError(f"级联删除查询图事实失败: {exc}") from exc

        delete_relation_ids: list[str] = []
        update_relations: list[dict[str, Any]] = []
        affected_entity_ids: list[str] = []
        seen_entities: set[str] = set()
        for relation in relations:
            for entity_id in relation.get("entity_ids") or []:
                if entity_id not in seen_entities:
                    seen_entities.add(entity_id)
                    affected_entity_ids.append(entity_id)
            remaining = [
                item
                for item in relation.get("passage_ids") or []
                if item not in deleted_chunks
            ]
            if remaining:
                updated = dict(relation)
                updated["passage_ids"] = remaining
                update_relations.append(updated)
            else:
                delete_relation_ids.append(str(relation["id"]))

        try:
            self.adapter.upsert_raw_relations(update_relations, tenant_id=tenant_id)
            deleted_relations = self.adapter.delete_relations_by_ids(
                delete_relation_ids,
                tenant_id=tenant_id,
            )
            related_entities = self.adapter.get_entities_by_ids(
                affected_entity_ids, tenant_id=tenant_id, include_vectors=True
            )
        except RagGraphStoreError as exc:
            raise GraphBuilderError(f"级联删除更新关系失败: {exc}") from exc

        entities_by_id: dict[str, dict[str, Any]] = {}
        for entity in [*direct_entities, *related_entities]:
            entities_by_id[str(entity["id"])] = dict(entity)
        deleted_relation_set = set(delete_relation_ids)
        referenced_relation_ids = list(
            dict.fromkeys(
                str(relation_id)
                for entity in entities_by_id.values()
                for relation_id in (entity.get("relation_ids") or [])
            )
        )
        try:
            existing_relation_ids = {
                str(row["id"])
                for row in self.adapter.get_relations_by_ids(
                    referenced_relation_ids, tenant_id=tenant_id
                )
            }
        except RagGraphStoreError as exc:
            raise GraphBuilderError(f"级联删除校验实体关系失败: {exc}") from exc
        update_entities: list[dict[str, Any]] = []
        delete_entity_ids: list[str] = []
        for entity in entities_by_id.values():
            remaining_passages = [
                item
                for item in entity.get("passage_ids") or []
                if item not in deleted_chunks
            ]
            remaining_relations = [
                item
                for item in entity.get("relation_ids") or []
                if item not in deleted_relation_set and item in existing_relation_ids
            ]
            if not remaining_passages and not remaining_relations:
                delete_entity_ids.append(str(entity["id"]))
                continue
            entity["passage_ids"] = remaining_passages
            entity["relation_ids"] = remaining_relations
            update_entities.append(entity)
        try:
            self.adapter.upsert_raw_entities(update_entities, tenant_id=tenant_id)
            deleted_entities = self.adapter.delete_entities_by_ids(
                delete_entity_ids,
                tenant_id=tenant_id,
            )
        except RagGraphStoreError as exc:
            raise GraphBuilderError(f"级联删除更新实体失败: {exc}") from exc
        return int(deleted_relations or 0), int(deleted_entities or 0)

    def count_facts_by_chunk_ids(
        self, chunk_ids: Sequence[str], tenant_id: str = ""
    ) -> int:
        """Count graph entities and relations still referencing chunk IDs."""
        unique_ids = list(dict.fromkeys(str(item) for item in chunk_ids if str(item)))
        if not unique_ids:
            return 0
        self.adapter.require_methods(GRAPH_BUILDER_COUNT_REQUIRED_METHODS)
        try:
            relations = self.adapter.get_relations_by_passage_ids(
                unique_ids, tenant_id=tenant_id
            )
            entities = self.adapter.get_entities_by_passage_ids(
                unique_ids, tenant_id=tenant_id
            )
        except RagGraphStoreError as exc:
            raise GraphBuilderError(f"图删除缺席校验失败: {exc}") from exc
        return len(relations) + len(entities)

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    @staticmethod
    def _assemble(
        chunk_id: str,
        triplets: Sequence[Triplet],
        entities_by_name: dict[str, GraphEntity],
        relations_by_text: dict[str, GraphRelation],
    ) -> None:
        """把一段三元组并入内存中的实体 / 关系邻接表。"""
        for triplet in triplets:
            rel_text = f"{triplet.subject} {triplet.predicate} {triplet.object}"
            rel_key = rel_text.lower()
            relation = relations_by_text.get(rel_key)
            if relation is None:
                relation = GraphRelation(
                    id=_stable_id(rel_text),
                    text=rel_text,
                    entity_ids=[],
                    passage_ids=[],
                    subject=triplet.subject,
                    predicate=triplet.predicate,
                    object=triplet.object,
                )
                relations_by_text[rel_key] = relation
            if chunk_id not in relation.passage_ids:
                relation.passage_ids.append(chunk_id)

            for name in (triplet.subject, triplet.object):
                ent_key = name.lower()
                entity = entities_by_name.get(ent_key)
                if entity is None:
                    entity = GraphEntity(
                        id=_stable_id(name),
                        name=name,
                        relation_ids=[],
                        passage_ids=[],
                    )
                    entities_by_name[ent_key] = entity
                if chunk_id not in entity.passage_ids:
                    entity.passage_ids.append(chunk_id)
                if relation.id not in entity.relation_ids:
                    entity.relation_ids.append(relation.id)
                if entity.id not in relation.entity_ids:
                    relation.entity_ids.append(entity.id)

    def _merge_with_store(
        self,
        entity_list: list[GraphEntity],
        relation_list: list[GraphRelation],
        tenant_id: str = "",
    ) -> None:
        """把库内同名 / 同文本节点的邻接并入本次写入的节点（增量更新）。

        查重限定在当前租户内：不同租户的同名实体 / 同文本关系
        互不合并，保证图数据租户隔离。主键保持稳定：合并时沿用
        库内已有 id，避免重复节点与孤引用。
        """
        try:
            existing_entities = self.adapter.get_entities_by_texts(
                [e.name for e in entity_list], tenant_id=tenant_id
            )
            existing_relations = self.adapter.get_relations_by_texts(
                [r.text for r in relation_list], tenant_id=tenant_id
            )
        except RagGraphStoreError as exc:
            raise GraphBuilderError(f"查重失败: {exc}") from exc

        for entity in entity_list:
            record = existing_entities.get(entity.name)
            if not record:
                continue
            entity.id = record["id"]
            entity.relation_ids = _merge_unique(
                entity.relation_ids, record.get("relation_ids") or []
            )
            entity.passage_ids = _merge_unique(
                entity.passage_ids, record.get("passage_ids") or []
            )

        for relation in relation_list:
            record = existing_relations.get(relation.text)
            if not record:
                continue
            relation.id = record["id"]
            relation.entity_ids = _merge_unique(
                relation.entity_ids, record.get("entity_ids") or []
            )
            relation.passage_ids = _merge_unique(
                relation.passage_ids, record.get("passage_ids") or []
            )

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """按批大小嵌入文本列表。"""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            vectors.extend(self.embedder.embed_texts(batch))
        return vectors


__all__ = ["GraphBuilderError", "GraphBuildPlan", "GraphBuildResult", "GraphBuilder", "_stable_id"]
