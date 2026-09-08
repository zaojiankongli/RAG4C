"""阶段 6：让图检索这条分支真正跑起来。

## 这些测试在防什么

图检索的三个缺陷有个共同点：**它们都不会让任何测试变红，也不会让任何日志报
错**。链路自始至终"成功"，只是每一次都成功地返回空手，然后静默降级回 hybrid，
trace 里留下一行"graph 无命中，降级 hybrid"——看起来就像"这个查询确实用不上
图谱"。于是一个花了大成本（入库时每个片段一次大模型调用）建起来的图库，检索
侧从来没被用上过，而没有任何信号提示这件事。

四个回归条件：

1. **实体阈值**（``entity_similarity_threshold``）：卡的是"整句查询 vs 实体
   名"，尺度远低于句对句。实测 0.9 只有查询逐字包含实体名时才命中；实体命
   不中 = 子图扩展无从谈起 = 整条分支形同虚设。
2. **扩展跳数**（``expansion_degree``）：1 跳拿到的关系和"按关系向量检索"拿
   到的是同一批，多跳推理要的桥接实体在第 2 跳。
3. **扩展关系的先验分**（``expansion_prior_decay``）：从前一律 0.0 且合并后
   不排序，于是扩展结果——这个特性的全部理由——恒排最后。
4. **重排契约**：从前要求模型给出全部候选的完整排列，少一条整份作废。模型做
   对的那部分（挑出最相关的几条）恰恰是唯一需要的，却被"全有全无"一起扔掉。

3 和 4 都是"顺序"缺陷，不是"结果"缺陷：候选集合一模一样，只是排序不同，而
``final_top_k`` 一截断，排序就决定了哪些 passage 能活到最后。所以下面的断言
盯的是 ``passage_ids`` 的**内容与次序**，不是它的长度。
"""
from __future__ import annotations

import pytest

from config.settings import GraphSettings
from retrieval.graph_retriever import GraphRetriever


# --------------------------------------------------------------------------- #
# 桩：一个小图
#
#   张三 --r1--> 财务部 --r2--> 报销制度        （r2 是桥接关系，只在第 2 跳）
#   张三 --r3--> 李四
#   无关的 r9（向量检索会把它捞出来，但和查询没关系）
# --------------------------------------------------------------------------- #
_ENTITIES = {
    "e_zhang": {"id": "e_zhang", "text": "张三", "relation_ids": ["r1", "r3"],
                "passage_ids": ["p_zhang"]},
    "e_fin": {"id": "e_fin", "text": "财务部", "relation_ids": ["r1", "r2"],
              "passage_ids": ["p_fin"]},
    "e_rule": {"id": "e_rule", "text": "报销制度", "relation_ids": ["r2"],
               "passage_ids": ["p_rule"]},
    "e_li": {"id": "e_li", "text": "李四", "relation_ids": ["r3"],
             "passage_ids": ["p_li"]},
}

_RELATIONS = {
    "r1": {"id": "r1", "text": "张三 任职于 财务部", "entity_ids": ["e_zhang", "e_fin"],
           "passage_ids": ["p_r1"]},
    "r2": {"id": "r2", "text": "财务部 负责 报销制度", "entity_ids": ["e_fin", "e_rule"],
           "passage_ids": ["p_r2"]},
    "r3": {"id": "r3", "text": "张三 同事 李四", "entity_ids": ["e_zhang", "e_li"],
           "passage_ids": ["p_r3"]},
    "r9": {"id": "r9", "text": "Kubernetes 用于 容器编排", "entity_ids": [],
           "passage_ids": ["p_r9"]},
}


class FakeStore:
    """按预设的相似度回答检索，按 id 回答邻接查询。

    ``entity_sims`` / ``relation_sims`` 是"如果不设阈值，检索会返回什么"；
    阈值过滤在这里如实实现，因为**被测的正是阈值**。
    """

    def __init__(self, graph: GraphSettings, entity_sims=None, relation_sims=None):
        self.graph = graph
        self.entity_sims = entity_sims or {}
        self.relation_sims = relation_sims or {}
        self.entity_lookups: list[list[str]] = []
        self.relation_lookups: list[list[str]] = []

    def search_entities(self, vec, top_k, threshold, tenant_id=""):
        hits = [
            dict(_ENTITIES[eid], distance=sim)
            for eid, sim in sorted(self.entity_sims.items(), key=lambda kv: -kv[1])
            if sim >= threshold
        ]
        return hits[:top_k]

    def search_relations(self, vec, top_k, threshold, tenant_id=""):
        hits = [
            dict(_RELATIONS[rid], distance=sim)
            for rid, sim in sorted(self.relation_sims.items(), key=lambda kv: -kv[1])
            if sim >= threshold
        ]
        return hits[:top_k]

    def get_entities_by_ids(self, ids, tenant_id=""):
        self.entity_lookups.append(list(ids))
        return [dict(_ENTITIES[i]) for i in ids if i in _ENTITIES]

    def get_relations_by_ids(self, ids, tenant_id=""):
        self.relation_lookups.append(list(ids))
        return [dict(_RELATIONS[i]) for i in ids if i in _RELATIONS]


class FakeEmbedder:
    def embed_query(self, text):
        return [1.0, 0.0]

    def embed_texts(self, texts):
        return [[1.0, 0.0] for _ in texts]


class ScriptedLlm:
    """按脚本返回 ``chat_json`` 的结果（或抛错）。"""

    def __init__(self, payload=None, boom: bool = False):
        self.payload = payload
        self.boom = boom
        self.prompts: list[str] = []

    def chat_json(self, messages, **kw):
        self.prompts.append(messages[0]["content"])
        if self.boom:
            raise RuntimeError("桩：重排模型不可用")
        return self.payload


def _retriever(graph: GraphSettings, llm=None, **store_kw) -> GraphRetriever:
    return GraphRetriever(
        store=FakeStore(graph, **store_kw), embedder=FakeEmbedder(), llm=llm
    )


# --------------------------------------------------------------------------- #
# 1. 默认阈值：0.9 把整条分支变成摆设
# --------------------------------------------------------------------------- #
def test_default_entity_threshold_admits_a_realistic_match() -> None:
    """0.72 是实测里"查询问到了这个实体"的典型分数（正例区间 0.70~0.91）。

    阈值 0.9 时它被挡在门外，于是没有实体命中 -> 没有子图扩展 -> 图分支退化
    成"只有关系向量检索"，而那条路径不需要图也能走。
    """
    graph = GraphSettings()
    assert graph.entity_similarity_threshold <= 0.72, (
        f"实体阈值 {graph.entity_similarity_threshold} 高于实测正例的典型分数，"
        "等于要求查询里逐字出现实体名"
    )
    result = _retriever(graph, entity_sims={"e_zhang": 0.72}).retrieve("张三是谁")
    assert result.detail.entity_texts == ["张三"]
    assert result.detail.expanded_relation_ids, "实体命中了却没做子图扩展"


def test_default_relation_threshold_filters_noise() -> None:
    """-1.0 等于不过滤：relation_top_k 会把末位那些（实测低至 0.12）也倒出来，
    它们照样进重排候选、照样回取 passage。"""
    graph = GraphSettings()
    assert graph.relation_similarity_threshold > 0.0, "关系侧不该完全不过滤"
    retriever = _retriever(
        graph,
        entity_sims={},
        relation_sims={"r2": 0.75, "r9": 0.12},
    )
    result = retriever.retrieve("报销制度归谁管")
    assert result.detail.relation_texts == ["财务部 负责 报销制度"], (
        f"噪音关系没被挡住：{result.detail.relation_texts}"
    )


def test_default_expansion_reaches_the_bridging_entity() -> None:
    """多跳推理要的桥接关系 r2 在第 2 跳上：
    张三 --r1--> 财务部 --r2--> 报销制度。degree=1 时永远够不到。"""
    graph = GraphSettings()
    assert graph.expansion_degree >= 2, f"扩展跳数 {graph.expansion_degree} 够不到桥接实体"
    result = _retriever(graph, entity_sims={"e_zhang": 0.8}).retrieve("张三管报销吗")
    assert "r2" in result.detail.expanded_relation_ids, (
        f"没够到桥接关系：{result.detail.expanded_relation_ids}"
    )


def test_default_final_top_k_matches_the_pipeline_top_k() -> None:
    """截断发生在租户 / ACL 过滤**之前**（见 pipeline._run_graph_branch）：
    取 3 时被过滤掉两条就只剩一条了。"""
    from config.settings import PipelineSettings

    assert GraphSettings().final_top_k >= PipelineSettings().top_k


# --------------------------------------------------------------------------- #
# 2. 扩展关系的先验分：不能恒为 0 并恒排最后
# --------------------------------------------------------------------------- #
def test_expanded_relations_carry_a_hop_decayed_prior() -> None:
    graph = GraphSettings(expansion_degree=2, expansion_prior_decay=0.6)
    retriever = _retriever(graph, entity_sims={"e_zhang": 1.0})
    priors = retriever._expand_relations(  # noqa: SLF001
        [dict(_ENTITIES["e_zhang"], distance=1.0)], degree=2, decay=0.6
    )
    # 一跳：1.0 * 0.6；二跳：够到它的那条关系的先验 * 0.6
    assert priors["r1"] == pytest.approx(0.6)
    assert priors["r3"] == pytest.approx(0.6)
    assert priors["r2"] == pytest.approx(0.36), (
        f"二跳的先验没有继续衰减：{priors}"
    )


def test_a_strongly_anchored_expansion_outranks_a_weak_vector_hit() -> None:
    """这条是整个 29 项的要害。

    r1 / r2 是顺着"张三 -> 财务部 -> 报销制度"够到的，正是图检索存在的理由；
    r9 是一条相似度 0.30 的边角向量命中。从前扩展所得一律拿 0.0 分、合并后
    又不排序（原样追加在向量命中之后），于是 r9 排在它们前面——
    ``final_top_k`` 一截断，活下来的是噪音。

    这里连跳距也一并钉住：一跳的 r1（0.6）该排在二跳的 r2（0.36）前面，
    "离锚点越远越不可信"必须体现在顺序上，而不只是"有个非零分"。
    """
    graph = GraphSettings(
        expansion_degree=2,
        expansion_prior_decay=0.6,
        final_top_k=9,
        # 这条测的是排序，不是过滤：显式放开关系阈值，免得 r9 还没排上号就
        # 先被阈值挡掉，把"顺序对了"和"它压根没进来"混为一谈。
        relation_similarity_threshold=0.1,
    )
    retriever = _retriever(
        graph,
        entity_sims={"e_zhang": 1.0},
        relation_sims={"r9": 0.30},
    )
    result = retriever.retrieve("张三管报销吗")
    assert result.detail.relation_texts[0] == "张三 任职于 财务部", (
        f"扩展所得排在了边角向量命中之后：{result.detail.relation_texts}"
    )
    order = [_RELATIONS[r]["text"] for r in ("r1", "r2", "r9")]
    got = [t for t in result.detail.relation_texts if t in order]
    assert got == order, f"跳距没有体现在顺序上：{got}"
    assert result.passage_ids[0] == "p_r1"


def test_merged_relations_are_sorted_by_score() -> None:
    """``_collect_passages`` 是按列表顺序取 passage 的，所以"合并后按分数降序"
    不是锦上添花，它就是优先级本身。

    这里的分数是**故意**安排成"不排序就一定不对"的：向量命中 r9 只有 0.4，
    低于扩展所得 r1 / r3 的先验 0.6，而未排序时向量命中恒在前。第一版把 r9
    写成 0.9，于是插入顺序碰巧就是降序——那样的断言拿掉排序也照样绿，是一条
    读起来像覆盖、实际上永远不会红的测试。
    """
    graph = GraphSettings(
        expansion_degree=1, expansion_prior_decay=0.6, relation_similarity_threshold=0.1
    )
    retriever = _retriever(
        graph,
        entity_sims={"e_zhang": 1.0},      # -> r1 / r3 先验 0.6
        relation_sims={"r9": 0.4, "r2": 0.2},
    )
    result = retriever.retrieve("张三")
    scores = result.detail.relation_scores
    assert scores == sorted(scores, reverse=True), f"合并后没有按分数排序：{scores}"
    assert scores[0] == pytest.approx(0.6), (
        f"未排序时向量命中恒在前，这条断言就失去意义了：{scores}"
    )


def test_zero_decay_restores_the_previous_ordering() -> None:
    """``expansion_prior_decay=0.0`` 必须是一条干净的退路：全部扩展关系同分
    0.0，稳定排序下仍旧排在检索命中之后——和从前逐字一致。"""
    graph = GraphSettings(expansion_degree=1, expansion_prior_decay=0.0)
    retriever = _retriever(
        graph,
        entity_sims={"e_zhang": 1.0},
        relation_sims={"r9": 0.5},   # 高于默认关系阈值，确保它真进得来
    )
    result = retriever.retrieve("张三")
    assert result.detail.relation_texts[0] == "Kubernetes 用于 容器编排"
    assert result.detail.relation_scores[1:] == [0.0, 0.0]


def test_expansion_does_not_revisit_a_relation_at_a_farther_hop() -> None:
    """r1 同时挂在张三和财务部名下。第 2 跳重新遇到它时不能用更低的先验覆盖
    掉第 1 跳算出来的——"最短的那条路径说了算"。"""
    graph = GraphSettings()
    retriever = _retriever(graph)
    priors = retriever._expand_relations(  # noqa: SLF001
        [dict(_ENTITIES["e_zhang"], distance=1.0)], degree=2, decay=0.6
    )
    assert priors["r1"] == pytest.approx(0.6), f"一跳的先验被二跳覆盖了：{priors}"


def test_the_second_hop_costs_exactly_two_extra_store_round_trips() -> None:
    """把第 27 项的代价钉在明面上。

    `expansion_degree` 从 1 改到 2 不是免费的：每多一跳就要多一次
    ``get_relations_by_ids``（拿到这一跳关系另一端的实体）和一次
    ``get_entities_by_ids``（拿这些实体的 relation_ids）。这两次都是打在
    Milvus 上的同步查询，落在用户等待里。

    值不值得是另一回事（此前这条分支的产出接近于零，多两次往返换它真的能用，
    显然值），但**代价必须是可见的**：写成断言，将来有人把跳数调到 3 时会先
    在这里看见往返数跟着涨，而不是在生产的 p99 上看见。
    """
    graph = GraphSettings()
    retriever = _retriever(graph, entity_sims={"e_zhang": 0.8})
    store = retriever.store
    retriever.retrieve("张三管报销吗")

    # 第 1 跳直接用 entity_hits，不查库；第 2 跳各一次
    assert len(store.entity_lookups) == 1, (
        f"实体补读次数与跳数不符：{store.entity_lookups}"
    )
    # 一次是第 2 跳的桥接查询，一次是 _merge_relations 补读扩展关系的完整记录
    assert len(store.relation_lookups) == 2, (
        f"关系补读次数与跳数不符：{store.relation_lookups}"
    )


# --------------------------------------------------------------------------- #
# 3. 重排契约：挑一部分也算数
# --------------------------------------------------------------------------- #
def _rerank_setup(payload=None, boom=False):
    graph = GraphSettings(expansion_degree=1, expansion_prior_decay=0.6, final_top_k=9)
    llm = ScriptedLlm(payload=payload, boom=boom)
    retriever = _retriever(
        graph,
        llm=llm,
        entity_sims={"e_zhang": 1.0},
        relation_sims={"r9": 0.9, "r2": 0.5},
    )
    return retriever, llm


def test_a_partial_pick_is_honoured_not_discarded() -> None:
    """从前：``len(indices) != len(candidates)`` -> 整份作废退回分数序。

    模型能可靠做到的是"把最相关的两三条挑到前面"，做不到的是"给 50 条候选排
    出一个完整全排列"。为了后者把前者一起扔掉，是拿全有全无去赌一件模型本来
    就做不好的事。
    """
    retriever, _ = _rerank_setup(payload={"ranked_indices": [2]})
    result = retriever.retrieve("张三管报销吗")
    assert result.detail.reranked_relation_ids, "只挑了一条就被当成非法结果丢了"
    # 挑中的那条排到最前，其余按分数接在后面（不丢数据）
    assert result.passage_ids[0] == _RELATIONS[
        result.detail.reranked_relation_ids[0]
    ]["passage_ids"][0]
    assert len(result.passage_ids) > 1, "没被挑中的候选被丢掉了"


def test_a_hallucinated_index_is_skipped_not_fatal() -> None:
    """越界 / 重复 / 非整数逐条跳过即可：剩下的编号照样是有效信息，而没被挑
    中的候选本来就会按分数接在后面，跳过不会丢数据。"""
    retriever, _ = _rerank_setup(
        payload={"ranked_indices": [1, 999, 1, "二", None, 0]}
    )
    result = retriever.retrieve("张三管报销吗")
    ranked = result.detail.reranked_relation_ids
    assert len(ranked) == 2 and len(set(ranked)) == 2, f"非法编号没被清理：{ranked}"


def test_an_empty_pick_falls_back_to_score_order() -> None:
    """"一条都没用"是合法输出，此时应当安静地退回分数序，而不是空手而归。"""
    retriever, _ = _rerank_setup(payload={"ranked_indices": []})
    result = retriever.retrieve("张三管报销吗")
    assert result.detail.reranked_relation_ids == []
    assert result.passage_ids, "重排没挑中任何一条，不该把候选一起清空"


def test_a_non_list_payload_still_degrades_silently() -> None:
    """模型返回的压根不是数组时才谈得上"解析失败"——这条降级路径要保住。"""
    for payload in ({"ranked_indices": "0,1,2"}, {}, None):
        retriever, _ = _rerank_setup(payload=payload)
        result = retriever.retrieve("张三管报销吗")
        assert result.detail.reranked_relation_ids == []
        assert result.passage_ids, f"payload={payload!r} 时结果被清空了"


def test_rerank_failure_never_breaks_retrieval() -> None:
    retriever, _ = _rerank_setup(boom=True)
    result = retriever.retrieve("张三管报销吗")
    assert result.passage_ids, "重排模型不可用不该让图检索一起失败"


def test_rerank_prompt_states_the_pick_budget() -> None:
    """提示词得把"可以少挑"讲明白，否则模型仍会照旧凑够全排列。"""
    retriever, llm = _rerank_setup(payload={"ranked_indices": [0]})
    retriever.retrieve("张三管报销吗")
    prompt = llm.prompts[0]
    assert "最多挑" in prompt
    assert "空数组" in prompt, "没告诉模型可以一条都不挑"
