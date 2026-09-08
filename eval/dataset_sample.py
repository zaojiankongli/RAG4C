"""RAG4C 内置评测数据集（含脏数据）。

刻意混入 3 条不可答用例：知识库中没有对应文档，用于审计
拒答率与过度拒答率之间的平衡，以及幻觉率（无答案却作答）。
"""
from __future__ import annotations

SAMPLE_DATASET: list[dict] = [
    {
        "id": "milvus_index_types",
        "question": "Milvus 支持哪些索引类型？HNSW 和 IVF 有什么区别？",
        "unanswerable": False,
        "doc_text": (
            "Milvus 内置多种向量索引类型。HNSW 是一种基于层级小世界图"
            "（Hierarchical Navigable Small World）的图索引，检索延迟低、召回率高，"
            "适合中等规模向量集；IVF（Inverted File）系列索引通过聚类把向量分桶，"
            "检索时只扫描与查询最接近的若干个桶，适合大规模向量集与内存受限场景。"
            "Milvus 还提供 FLAT 暴力检索索引，适合小数据集与召回率校验。"
        ),
        "expected_keywords": ["HNSW", "IVF", "FLAT"],
        "notes": "可答：语料覆盖 Milvus 索引说明",
    },
    {
        "id": "hybrid_search_rrf",
        "question": "混合检索里的 RRF 是如何融合稠密与稀疏结果的？",
        "unanswerable": False,
        "doc_text": (
            "RAG4C 的混合检索同时执行稠密向量检索（BGE-M3 嵌入）与稀疏 BM25 检索，"
            "然后使用 RRF（Reciprocal Rank Fusion，倒数排名融合）合并两路结果："
            "对每条文档按其在各结果集中的排名取倒数并求和，排名越靠前权重越高，"
            "从而把两路检索的排序信号融合为单一排序。RRF 不依赖分数归一化，"
            "对稠密与稀疏分数的量纲差异不敏感。"
        ),
        "expected_keywords": ["RRF", "BM25", "倒数排名"],
        "notes": "可答：语料覆盖混合检索融合机制",
    },
    {
        "id": "milvus_lite_deploy",
        "question": "Milvus Lite 如何零部署使用？",
        "unanswerable": False,
        "doc_text": (
            "Milvus Lite 是 Milvus 的嵌入式版本，无需单独启动服务。"
            "在 RAG4C 中，只要把配置项 RAG4C_MILVUS_URI 设置为本地文件路径"
            "（例如 ./rag4c.db），pymilvus 会自动以 Milvus Lite 模式打开该文件"
            "并启动进程内向量库，适合开发调试与单机小规模场景。生产环境可将 URI "
            "改为 http(s)://host:port 指向独立的 Milvus 服务。"
        ),
        "expected_keywords": ["Milvus Lite", "rag4c.db", "RAG4C_MILVUS_URI"],
        "notes": "可答：语料覆盖 Milvus Lite 部署方式",
    },
    {
        "id": "policy_annual_leave",
        "question": "云帆科技的员工年假是怎么规定的？",
        "unanswerable": False,
        "doc_text": (
            "云帆科技员工手册第三章规定：员工入职当年即享有 5 天带薪年假；"
            "自第二年起，每满一年工龄增加 1 天，年假上限为 15 天。"
            "年假须在自然年度内使用完毕，未使用部分不跨年结转；"
            "确因工作安排无法休完的，经部门负责人审批后最多可结转 5 天，"
            "须在次年 6 月 30 日前休完。"
        ),
        "expected_keywords": ["5 天", "15 天", "结转"],
        "notes": "可答：语料覆盖公司年假政策",
    },
    {
        "id": "policy_expense_reimburse",
        "question": "云帆科技出差报销的时限和材料要求是什么？",
        "unanswerable": False,
        "doc_text": (
            "云帆科技财务报销制度规定：员工出差结束后应在 30 个自然日内提交报销申请，"
            "逾期需书面说明原因并经部门总监与财务总监双签审批。报销材料包括："
            "差旅申请单、发票原件、行程单或登机牌、以及酒店水单；"
            "发票抬头须为云帆科技全称并附统一社会信用代码。"
            "单笔金额超过 5000 元的报销需提前邮件报备。"
        ),
        "expected_keywords": ["30 个自然日", "发票", "5000 元"],
        "notes": "可答：语料覆盖公司报销制度",
    },
    {
        "id": "out_of_corpus_movie",
        "question": "2026 年春节档电影票房的冠军是哪部影片？",
        "unanswerable": True,
        "doc_text": None,
        "expected_keywords": None,
        "notes": "不可答：娱乐新闻话题，不在企业知识库范围内",
    },
    {
        "id": "out_of_corpus_canteen",
        "question": "公司食堂本周的午餐菜单是什么？",
        "unanswerable": True,
        "doc_text": None,
        "expected_keywords": None,
        "notes": "不可答：食堂菜单从未入库，知识库无相关文档",
    },
    {
        "id": "out_of_corpus_mars",
        "question": "火星土壤样本中的主要矿物成分有哪些？",
        "unanswerable": True,
        "doc_text": None,
        "expected_keywords": None,
        "notes": "不可答：火星探测话题与企业知识库无关",
    },
]

__all__ = ["SAMPLE_DATASET"]
