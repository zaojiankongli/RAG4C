"""知识图谱检索器（vector-graph-rag 方案的检索端）。

流程（参照 zilliztech/vector-graph-rag 官方 python-api）：
1. 查询向量化（:meth:`~core.embedding.EmbeddingService.embed_query`）；
2. 实体向量检索（``entity_top_k`` / ``entity_similarity_threshold``）；
3. 子图扩展（``expansion_degree`` 跳）：收集命中实体的 ``relation_ids``
   （一跳 = 收集直接相连关系，二跳再取这些关系的实体，依此类推），
   每条扩展所得关系按跳距衰减得到一个先验分
   （``expansion_prior_decay``）；
4. 关系向量检索（``relation_top_k`` / ``relation_similarity_threshold``）；
5. 合并关系候选（扩展所得 + 检索命中，按 id 去重）并**按分数降序排列**：
   检索命中用自己的距离，扩展所得用先验分，两者排在同一把尺子上；
6. LLM 重排（``use_llm_rerank`` 且提供了 llm 客户端时）：从候选里**挑出**
   最有用的若干条（``max_rerank_picks``），挑不满不算失败；LLM 不可用或返
   回的不是数组时**静默降级**为按分数排序（不中断检索）；
7. 按"挑中的在前、其余按分数在后"的顺序收集 ``passage_ids``（回查 chunks
   用），去重并截断到 ``final_top_k``。

检索失败（存储层错误）抛出 :class:`GraphRetrieverError`；
检索**成功但无命中**不抛错，返回 ``degraded=True`` 的空结果。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence

from core.embedding import EmbeddingService
from core.graph_projection_adapters import (
    GRAPH_RETRIEVER_REQUIRED_METHODS,
    GraphProjectionAdapter,
    create_graph_projection_adapter,
)
from core.graph_store import RagGraphStore, RagGraphStoreError
from core.llm import LLMClient
from models.schemas import GraphRetrievalDetail
from prompts import render_prompt

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


class GraphRetrieverError(RuntimeError):
    """图检索失败（存储 / 嵌入错误等环境性问题）。"""


@dataclass
class GraphRetrievalResult:
    """一次图检索的结果。

    Attributes:
        detail: 中间结果明细（实体 / 关系命中、扩展与重排记录）。
        passage_ids: 最终回查的 chunk（passage）ID，按优先级有序去重。
        degraded: 无任何实体 / 关系命中时为 True（下游应降级处理）。
    """

    detail: GraphRetrievalDetail = field(default_factory=GraphRetrievalDetail)
    passage_ids: list[str] = field(default_factory=list)
    degraded: bool = False


class GraphRetriever:
    """知识图谱检索器。

    Args:
        store: 图存储（实体 / 关系两集合）。
        embedder: 嵌入服务（实现 :class:`~core.embedding.EmbeddingService`）。
        llm: 可选 LLM 客户端（重排用）。None 或未开启 ``use_llm_rerank``
            时跳过 LLM 重排，直接按检索分数排序。
        template_path: LLM 重排提示词模板（``prompts/graph_rerank_v1.txt``）。
        max_rerank_candidates: 送入 LLM 重排的候选关系数量上限。
        max_rerank_picks: 允许 LLM 挑出的关系数量上限（提示词里的建议值）。
            挑不满不算失败——没被挑中的候选照样按检索分排在后面。
    """

    def __init__(
        self,
        store: RagGraphStore,
        embedder: EmbeddingService,
        llm: Optional[LLMClient] = None,
        template_path: str = "prompts/graph_rerank_v1.txt",
        max_rerank_candidates: int = 50,
        max_rerank_picks: int = 10,
    ) -> None:
        self.store = store
        self.adapter: GraphProjectionAdapter = create_graph_projection_adapter(
            store,
            required_methods=GRAPH_RETRIEVER_REQUIRED_METHODS,
        )
        self.embedder = embedder
        self.llm = llm
        self.max_rerank_candidates = max_rerank_candidates
        self.max_rerank_picks = max_rerank_picks
        self._template_path = _PROJECT_ROOT / template_path
        self._template: Optional[str] = None

    @property
    def template(self) -> str:
        """LLM 重排提示词模板（懒加载）。"""
        if self._template is None:
            self._template = self._template_path.read_text(encoding="utf-8")
        return self._template

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    def retrieve(self, query: str, tenant_id: str | None = None) -> GraphRetrievalResult:
        """对查询执行图检索，返回最终 passage 列表与过程明细。

        Args:
            query: 用户查询。
            tenant_id: 租户标识（由检索管线透传；空串 / None = 不过滤）。
                存储层的实体 / 关系检索与子图扩展均限定在该租户内，
                跨租户图数据零可见。
        """
        tenant = tenant_id or ""
        graph = self.adapter.graph
        try:
            query_vec = self.embedder.embed_query(query)

            # 2. 实体向量检索（限定租户）
            entity_hits = self.adapter.search_entities(
                query_vec,
                top_k=graph.entity_top_k,
                threshold=graph.entity_similarity_threshold,
                tenant_id=tenant,
            )

            # 3. 子图扩展（收集命中实体的直接相连关系，限定租户）
            expanded = self._expand_relations(
                entity_hits,
                degree=graph.expansion_degree,
                tenant_id=tenant,
                decay=float(getattr(graph, "expansion_prior_decay", 0.0) or 0.0),
            )

            # 4. 关系向量检索（限定租户）
            relation_hits = self.adapter.search_relations(
                query_vec,
                top_k=graph.relation_top_k,
                threshold=graph.relation_similarity_threshold,
                tenant_id=tenant,
            )
        except RagGraphStoreError as exc:
            raise GraphRetrieverError(f"图检索存储层失败: {exc}") from exc
        except Exception as exc:
            raise GraphRetrieverError(f"图检索失败: {exc}") from exc

        # 5. 合并关系候选（检索命中优先带分数，扩展所得按 id 去重，限定租户）
        merged = self._merge_relations(relation_hits, expanded, tenant_id=tenant)

        # 6. LLM 重排（可用时）
        reranked_ids: list[str] = []
        if (
            self.llm is not None
            and graph.use_llm_rerank
            and len(merged) > 1
        ):
            reranked_ids = self._rerank(query, merged) or []

        # 7. 按优先级收集 passage_ids（去重 + 截断 final_top_k）
        passage_ids = self._collect_passages(
            merged, reranked_ids, entity_hits, graph.final_top_k
        )

        detail = GraphRetrievalDetail(
            query_entities=[h.get("text", "") for h in entity_hits],
            entity_texts=[h.get("text", "") for h in entity_hits],
            entity_scores=[float(h.get("distance", 0.0)) for h in entity_hits],
            relation_texts=[r["text"] for r in merged],
            relation_scores=[float(r.get("distance", 0.0)) for r in merged],
            # detail 里保持 list[str]（下游 / smoke 脚本按列表消费），
            # 先验分本身已经写进 merged 的 distance，无需再重复一份
            expanded_relation_ids=list(expanded),
            reranked_relation_ids=reranked_ids,
        )

        degraded = not entity_hits and not relation_hits
        return GraphRetrievalResult(
            detail=detail, passage_ids=passage_ids, degraded=degraded
        )

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _expand_relations(
        self,
        entity_hits: Sequence[dict],
        degree: int,
        tenant_id: str = "",
        decay: float = 0.0,
    ) -> dict[str, float]:
        """子图扩展：收集与命中实体相连的关系 ID，并给每条一个先验分。

        一跳：命中实体的 ``relation_ids``；二跳：这些关系另一端实体的
        ``relation_ids``（需要额外读取实体邻接，此处对二跳及以上通过
        :meth:`~core.graph_store.RagGraphStore.get_entities_by_ids` 取回）。
        所有补读均限定在指定租户内（跨租户邻接不可见）。

        **先验分**：扩展关系是顺着边够到的，不是搜到的，因此没有自己的向量
        检索分。但它们最终要和检索命中排在同一个列表里，没有分数就只能恒排
        最后。这里给的是::

            prior(第 h 跳的关系) = 锚点实体相似度 * decay ** (h + 1)

        并且逐跳串联——第 h+1 跳实体的"锚点相似度"取的是够到它的那条关系的
        先验分，于是分数沿着路径衰减，"离锚点越远越不可信"被如实表达出来。
        同一条关系被多条路径够到时取**最高**的那个先验（最短 / 最强的那条
        路径说了算）。``decay=0.0`` 时全部先验为 0.0，退回从前的行为。

        Args:
            entity_hits: 实体向量检索命中列表。
            degree: 扩展跳数。
            tenant_id: 租户标识（空串 / None = 不过滤）。
            decay: 每跳的先验衰减系数。

        Returns:
            关系 ID -> 先验分，按发现顺序（即跳距由近及远）排列。
        """
        if degree < 1:
            return {}
        priors: dict[str, float] = {}

        # 当前层：实体 ID -> 该实体的锚定分（第 0 层就是向量检索的相似度）
        current: dict[str, float] = {}
        for hit in entity_hits:
            eid = hit["id"]
            score = float(hit.get("distance", 0.0) or 0.0)
            if score > current.get(eid, float("-inf")):
                current[eid] = score

        for hop in range(degree):
            if not current:
                break
            entities = entity_hits if hop == 0 else self.adapter.get_entities_by_ids(
                list(current), tenant_id=tenant_id
            )
            new_rels: dict[str, float] = {}
            for entity in entities:
                # 每跳只乘一次 decay：``current`` 里的锚定分是从上一跳继承下来
                # 的（已经含了前面每一跳的衰减），所以这里再按 hop 取幂会把衰
                # 减重复计一遍。闭式 ``anchor * decay ** (h + 1)`` 正是这样逐
                # 跳串联出来的。
                anchor = current.get(entity["id"], 0.0)
                prior = anchor * decay
                for rid in entity.get("relation_ids") or []:
                    if rid in priors:
                        # 更近的一跳已经收过它了，近的那次分更高，不覆盖
                        continue
                    if prior > new_rels.get(rid, float("-inf")):
                        new_rels[rid] = prior
            priors.update(new_rels)

            # 下一跳：由这些关系读取另一端实体，锚定分继承自够到它的那条关系
            if hop + 1 >= degree:
                break
            next_entities: dict[str, float] = {}
            for rel in self.adapter.get_relations_by_ids(
                list(new_rels), tenant_id=tenant_id
            ):
                prior = new_rels.get(rel["id"], 0.0)
                for eid in rel.get("entity_ids") or []:
                    if prior > next_entities.get(eid, float("-inf")):
                        next_entities[eid] = prior
            current = next_entities
        return priors

    def _merge_relations(
        self,
        relation_hits: Sequence[dict],
        expanded_priors: Mapping[str, float],
        tenant_id: str = "",
    ) -> list[dict]:
        """合并关系候选并**按分数降序排列**。

        检索命中带自己的 ``distance``；扩展所得带
        :meth:`_expand_relations` 算出的先验分。两者进同一个列表后按
        ``distance`` 降序稳定排序——稳定这一点是必须的：同分（比如
        ``expansion_prior_decay=0.0`` 时扩展关系全为 0.0）时保持"检索命中在
        前、扩展在后"的插入顺序，正好是从前的行为。

        从前这里没有排序：向量命中原样在前，扩展所得一律 ``distance=0.0``
        追加在后。而 :meth:`_collect_passages` 是按列表顺序取 passage 的，
        于是扩展结果永远排在最后，只有 LLM 重排把它们提上来才能活到最后。

        扩展中缺失的完整记录补读时限定在指定租户内。
        """
        merged: list[dict] = []
        seen: set[str] = set()
        for hit in relation_hits:
            rid = hit["id"]
            if rid not in seen:
                seen.add(rid)
                merged.append(dict(hit))
        # 扩展中尚未出现的关系，补读其完整记录
        missing = [rid for rid in expanded_priors if rid not in seen]
        if missing:
            for record in self.adapter.get_relations_by_ids(missing, tenant_id=tenant_id):
                rid = record["id"]
                if rid not in seen:
                    seen.add(rid)
                    record = dict(record)
                    record["distance"] = float(expanded_priors.get(rid, 0.0))
                    merged.append(record)
        merged.sort(key=lambda r: float(r.get("distance", 0.0) or 0.0), reverse=True)
        return merged


    def _rerank(self, query: str, merged: Sequence[dict]) -> Optional[list[str]]:
        """LLM 重排合并后的关系，返回**被挑中的**关系 ID（按优先级）。

        候选超过 ``max_rerank_candidates`` 时截断（保留检索分数最高的部分）。

        这里要的是"挑出有用的"，不是"给出一个完整排列"。从前的契约是后者：
        模型必须把全部候选（最多 50 条）一个不落地重新排一遍，少一条、多一
        条、重一条，整份结果就作废退回分数序。50 条的全排列对模型来说本就
        是道苦差，而**它做对的那部分（把最相关的几条挑到前面）恰恰是我们唯
        一需要的**——为了它没做好的那部分（把 40 条无关的也排出个先后）把
        前者一起扔掉，是拿全有全无去赌一件模型做不好的事。

        因此校验放宽为：非法编号（越界 / 重复 / 非整数）逐条跳过，剩下的照
        用。这不会丢数据——没被挑中的候选由
        :meth:`_collect_passages` 按分数接在后面。只有在
        ``ranked_indices`` 压根不是数组、或 LLM 调用失败时才返回 None（调用
        方降级为分数排序）。
        """
        candidates = list(merged)[: self.max_rerank_candidates]
        lines = "\n".join(f"{i}. {r['text']}" for i, r in enumerate(candidates))
        prompt = render_prompt(
            self.template,
            query=query,
            candidates=lines,
            count=len(candidates),
            count_minus_one=len(candidates) - 1,
            max_pick=min(len(candidates), self.max_rerank_picks),
        )
        try:
            payload = self.llm.chat_json(
                messages=[{"role": "user", "content": prompt}]
            )
        except Exception:
            return None  # LLM 不可用 / 解析失败 -> 降级

        indices = payload.get("ranked_indices") if isinstance(payload, dict) else None
        if not isinstance(indices, list):
            return None
        ids: list[str] = []
        seen: set[int] = set()
        for idx in indices:
            if not isinstance(idx, int) or isinstance(idx, bool):
                continue
            if idx < 0 or idx >= len(candidates) or idx in seen:
                continue
            seen.add(idx)
            ids.append(candidates[idx]["id"])
        return ids

    @staticmethod
    def _collect_passages(
        merged: Sequence[dict],
        reranked_ids: Sequence[str],
        entity_hits: Sequence[dict],
        final_top_k: int,
    ) -> list[str]:
        """按优先级收集 passage_ids。

        优先级：LLM 重排挑中的关系（若重排成功）> 其余候选按合并顺序（也就
        是分数降序）；随后补充命中实体的 ``passage_ids``；去重并截断到
        ``final_top_k``。

        重排现在允许只挑一部分（见 :meth:`_rerank`），所以"其余候选"不是可
        有可无的兜底，而是常规路径：没被挑中不等于不相关，只等于模型没把它
        排进前列，它们依旧按分数排在挑中的那批后面。
        """
        if reranked_ids:
            by_id = {r["id"]: r for r in merged}
            picked = set(reranked_ids)
            ordered = [by_id[rid] for rid in reranked_ids if rid in by_id]
            ordered += [r for r in merged if r["id"] not in picked]
        else:
            ordered = list(merged)

        passage_ids: list[str] = []
        seen: set[str] = set()
        for record in ordered:
            for pid in record.get("passage_ids") or []:
                if pid not in seen:
                    seen.add(pid)
                    passage_ids.append(pid)
            if len(passage_ids) >= final_top_k:
                return passage_ids[:final_top_k]
        for entity in entity_hits:
            for pid in entity.get("passage_ids") or []:
                if pid not in seen:
                    seen.add(pid)
                    passage_ids.append(pid)
            if len(passage_ids) >= final_top_k:
                break
        return passage_ids[:final_top_k]


__all__ = ["GraphRetrieverError", "GraphRetrievalResult", "GraphRetriever"]
