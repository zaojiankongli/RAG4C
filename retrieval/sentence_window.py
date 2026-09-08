"""Sentence Window Retrieval：small-to-big 父块回取（Retriever Enhancement）。

**来源**：LangChain 官方《How to Enhance Your RAG》的 Retriever Enhancement 方向
（官方 notebook ``sentence_window_with_langchain``），实现 small-to-big 语义：
先以**小粒度**（句子 / 子块）检索定位最相关的上下文，再回取其所属的**大粒度**
父块作为最终证据，从而在保持检索精度的同时补全上下文完整性。

**配套关系**：与索引侧 :class:`indexing.chunker.StructureAwareChunker` 的
父子块切分配套使用——该切分器为每个子块写入 ``parent_chunk_id`` 字段，
本模块消费该字段把命中子块回卷到父块。

**工作方式**：命中项（``RetrievedChunk.chunk`` 带 ``parent_chunk_id``）视为
子块；收集父块 id 后调用 ``milvus.get_chunks_by_ids(parent_ids)`` 批量取回
父块（与 ``retrieval/pipeline.py`` 图分支的用法一致），再按 ``replace`` 开关
决定语义：

- ``replace=True``（默认）：命中子块被其父块**替换**（父块继承子块中 rank
  最小项的 rank / score / branch），最终证据条数与输入相同，全部是完整上下文；
- ``replace=False``：父块**追加**在对应子块之后（按 chunk_id 去重），
  结果条数增多，子块与父块同时保留。

**安全设计**：
- 单层回取，不做级联：返回的父块统一标记 ``parent_chunk_id=None``
  （浅拷贝，不修改 Milvus 返回对象），即使对结果再次调用 ``expand`` 也不会
  继续向上放大；
- 完全可插拔：milvus 以协议（duck-typing）注入，模块导入零副作用、
  不引入第三方依赖，离线可 import；
- 失败静默降级：milvus 回取抛错 / 返回空 / 父块缺失时，原样返回输入，
  不向调用方抛错。

模块可离线独立运行（``from retrieval.sentence_window import SentenceWindowExpander``）。
"""
from __future__ import annotations

from typing import Any, Sequence

from models.schemas import Chunk, RetrievedChunk

__all__ = ["SentenceWindowExpander"]


class SentenceWindowExpander:
    """Sentence Window Retrieval 展开器：把命中子块回卷为父块证据。

    Args:
        milvus: Milvus 客户端，需实现
            ``get_chunks_by_ids(ids: Sequence[str]) -> list[Chunk]``
            （与检索管线图分支 ``pipeline._run_graph_branch`` 的用法一致）。
        replace: True 时命中子块被父块替换（结果条数不变）；
            False 时父块追加在子块之后（结果条数增多）。默认 True。
        enabled: False 时 ``expand`` 原样返回输入（可插拔开关）。
    """

    def __init__(
        self,
        milvus: Any,
        replace: bool = True,
        enabled: bool = True,
    ) -> None:
        self.milvus = milvus
        self.replace = replace
        self.enabled = enabled

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    def expand(self, items: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """把命中子块展开为其父块，返回新的结果列表。

        语义见模块 docstring。全程不抛错：milvus 缺失 / 接口缺失 /
        回取失败 / 无父块命中时，原样返回输入（静默降级）。

        Args:
            items: 检索命中的结果列表（可为空）。

        Returns:
            展开后的结果列表；``enabled=False`` 或无法展开时原样返回 ``items``。
        """
        if not self.enabled or not items:
            return items

        # 收集命中子块（chunk 带 parent_chunk_id 的项）
        children = [
            item for item in items if item.chunk.parent_chunk_id
        ]
        if not children:
            return items

        # 父块 id 去重，且排除已在结果中的 id（其内容已作为独立命中存在，
        # 避免重复；也天然防住 parent_chunk_id 自指 / 指向自身等脏数据）
        existing_ids = {item.chunk.chunk_id for item in items}
        parent_ids: list[str] = []
        seen: set[str] = set()
        for item in children:
            pid = item.chunk.parent_chunk_id
            if pid is not None and pid not in existing_ids and pid not in seen:
                seen.add(pid)
                parent_ids.append(pid)

        # 无需要回取的父块 -> 无操作
        if not parent_ids:
            return items

        # 静默降级：milvus 缺失 / 接口缺失 / 回取抛错 / 返回空
        parents = self._fetch_parents(parent_ids)
        if not parents:
            return items

        parent_by_id = self._index_parents(parents)

        if self.replace:
            return self._expand_replace(items, children, parent_by_id)
        return self._expand_append(items, children, parent_by_id)

    # ------------------------------------------------------------------ #
    # 内部实现
    # ------------------------------------------------------------------ #
    def _fetch_parents(self, parent_ids: Sequence[str]) -> list[Chunk]:
        """调 milvus 回取父块；任何失败返回空列表（调用方静默降级）。"""
        getter = getattr(self.milvus, "get_chunks_by_ids", None)
        if getter is None:
            return []
        try:
            return list(getter(parent_ids))
        except Exception:
            # 静默降级：milvus 暂时不可用 / 接口实现异常，保留原子块
            return []

    @staticmethod
    def _index_parents(parents: Sequence[Chunk]) -> dict[str, Chunk]:
        """把回取的父块按 chunk_id 建索引；去重，未知 id 直接忽略。"""
        indexed: dict[str, Chunk] = {}
        for chunk in parents:
            if chunk.chunk_id and chunk.chunk_id not in indexed:
                indexed[chunk.chunk_id] = chunk
        return indexed

    @staticmethod
    def _to_parent_item(parent: Chunk, child: RetrievedChunk) -> RetrievedChunk:
        """构造父块证据项（继承子块的 rank / score / branch）。

        父块浅拷贝并标记 ``parent_chunk_id=None``：单层回取，避免对结果
        再次展开时发生级联放大；也不修改 milvus 返回的原始对象。
        """
        safe_parent = parent.model_copy(update={"parent_chunk_id": None})
        return RetrievedChunk(
            chunk=safe_parent,
            score=child.score,
            rank=child.rank,
            branch=child.branch,
        )

    def _expand_replace(
        self,
        items: list[RetrievedChunk],
        children: list[RetrievedChunk],
        parent_by_id: dict[str, Chunk],
    ) -> list[RetrievedChunk]:
        """replace=True：命中子块被父块替换，条数不变、顺序稳定。

        同一父块被多个子块命中时只保留一条，rank / score / branch 取
        rank 最小（最先）的子块；父块插入在最小 rank 子块的位置，
        同父块的其余子块被吸收（不再单独出现）。
        父块未回取到的子块原样保留。
        """
        # 每个可回取父块 -> 命中子块中 rank 最小者（作为锚点与分数来源）
        best: dict[str, RetrievedChunk] = {}
        for item in children:
            parent = parent_by_id.get(item.chunk.parent_chunk_id or "")
            if parent is None:
                continue
            prev = best.get(parent.chunk_id)
            if prev is None or item.rank < prev.rank:
                best[parent.chunk_id] = self._to_parent_item(parent, item)

        # 子块 chunk_id -> 该位置要顶替输出的父块项；按 rank 升序锚定，
        # 保证父块出现在最小 rank 子块的位置
        replacement_at: dict[str, RetrievedChunk] = {}
        anchored: set[str] = set()
        for item in sorted(children, key=lambda it: it.rank):
            parent = parent_by_id.get(item.chunk.parent_chunk_id or "")
            if parent is None or parent.chunk_id in anchored:
                continue
            anchored.add(parent.chunk_id)
            replacement_at[item.chunk.chunk_id] = best[parent.chunk_id]

        out: list[RetrievedChunk] = []
        for item in items:
            parent_item = replacement_at.get(item.chunk.chunk_id)
            if parent_item is not None:
                out.append(parent_item)
                continue
            parent = parent_by_id.get(item.chunk.parent_chunk_id or "")
            if parent is not None and parent.chunk_id in anchored:
                continue  # 兄弟子块：父块已在最小 rank 子块位置输出，吸收
            out.append(item)
        return out

    def _expand_append(
        self,
        items: list[RetrievedChunk],
        children: list[RetrievedChunk],
        parent_by_id: dict[str, Chunk],
    ) -> list[RetrievedChunk]:
        """replace=False：父块追加在对应子块之后，按 chunk_id 去重。"""
        out: list[RetrievedChunk] = []
        emitted: set[str] = set()
        for item in items:
            cid = item.chunk.chunk_id
            if cid not in emitted:
                emitted.add(cid)
                out.append(item)
            parent = parent_by_id.get(item.chunk.parent_chunk_id or "")
            if parent is None or parent.chunk_id in emitted:
                continue
            emitted.add(parent.chunk_id)
            out.append(self._to_parent_item(parent, item))
        return out


# 兼容可选异常约定：本模块全程静默降级、不主动抛错，因此不定义异常类型。
# 若调用方需要自行包装 milvus 错误，可在上层捕获后按其自身约定处理。
