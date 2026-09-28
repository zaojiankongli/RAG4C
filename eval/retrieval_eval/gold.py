"""检索评测集（人工标注）：问题 → 应当命中的语料切片。

标注口径（写清楚，免得下一轮有人误用）：

- 一条用例的 gold 是**能独立回答该问题的切片集合**，不是"相关"切片。
  宁可少标一条，也不要把"提到了同一个词"的段落算进来——那会让 Recall 虚高。
- 每条 gold 都带 ``notes`` 说明为什么算命中，便于后续复核与补标。
- ``unanswerable`` 用例的 gold 为空列表，它们**不参与** Recall/MRR/NDCG，
  只用来看检索层在"语料里没答案"时会捞回多少噪声（误召回）。

版本：改动语料切片口径（``corpus.py``）会连带让这里的 chunk_id 失效，
所以 :func:`validate_against_corpus` 必须在跑评测前过一遍。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence, Mapping

# 表意文字基本区（U+4E00-U+9FFF）；语料只有中英两份文档，二值判定够用
CJK_START, CJK_END = "一", "鿿"
CJK_RATIO_FOR_ZH = 0.08
CORPUS_LANGUAGES: tuple[str, ...] = ("en", "zh")
LANGUAGE_UNDECLARED = "undeclared"

DATASET_VERSION = "2026-09-27.1"


@dataclass(frozen=True)
class GoldCase:
    """一条评测用例。"""

    case_id: str
    question: str
    gold_chunk_ids: tuple[str, ...] = ()
    unanswerable: bool = False
    notes: str = ""
    corpus_language: str = LANGUAGE_UNDECLARED

    @property
    def gold(self) -> tuple[str, ...]:
        return self.gold_chunk_ids


def cjk_ratio(text: str) -> float:
    """文本里中日韩表意文字的字符占比（空文本为 0.0）。

    只按字符数算，不分词——这一层只需要把"中文切片"和"英文切片"分开，
    不需要知道哪一段是中文。
    """
    if not text:
        return 0.0
    hits = sum(1 for ch in text if CJK_START <= ch <= CJK_END)
    return hits / len(text)


def detect_corpus_language(text: str) -> str:
    """按 :data:`CJK_RATIO_FOR_ZH` 把切片原文判成 ``zh`` / ``en``。

    只有两个桶：这份语料就是中英两份文档，二值判定够用了。
    """
    return "zh" if cjk_ratio(text) > CJK_RATIO_FOR_ZH else "en"


def validate_corpus_languages(
    cases: Sequence[GoldCase], texts: Mapping[str, str]
) -> list[str]:
    """核对"用例声明的语料语言"与"gold 切片原文的实际语言"。

    返回问题清单（空列表 = 声明可信）。四种红法，每种都点名是哪条用例：
    没声明、声明的词不在 :data:`CORPUS_LANGUAGES`、声明与原文不符、
    一条用例内部的 gold 切片语言就不一致（该拆用例，不该归到某一组）。
    原文查不到也算红 —— 拿不到原文就没法核对声明，不能默认它没错。
    不可答用例没有 gold 切片，语言维度对它无意义（不进召回统计），跳过。
    """
    problems: list[str] = []
    for case in cases:
        if case.unanswerable:
            continue
        declared = case.corpus_language
        if declared == LANGUAGE_UNDECLARED:
            problems.append(f"{case.case_id}: 未声明语料语言")
            continue
        if declared not in CORPUS_LANGUAGES:
            problems.append(f"{case.case_id}: 语料语言 {declared!r} 不在词表 {CORPUS_LANGUAGES}")
            continue
        detected = {
            detect_corpus_language(texts[gid])
            for gid in case.gold_chunk_ids
            if gid in texts
        }
        missing = [gid for gid in case.gold_chunk_ids if gid not in texts]
        if missing:
            problems.append(f"{case.case_id}: 拿不到原文，无法核对语言声明 {missing}")
        if len(detected) > 1:
            problems.append(
                f"{case.case_id}: gold 切片语言不一致 {sorted(detected)}，声明 {declared!r} 盖不住"
            )
        elif detected and next(iter(detected)) != declared:
            problems.append(
                f"{case.case_id}: 声明 {declared!r}，但语料原文是 {next(iter(detected))!r}"
            )
    return problems


GOLD_SET: tuple[GoldCase, ...] = (
    GoldCase(
        case_id="hybrid_fusion",
        question="RAG4C 的混合检索是怎么把稠密向量和 BM25 稀疏结果融合起来的？",
        gold_chunk_ids=("README.md#22", "docs/数据管道说明.md#142"),
        notes="README 检索段与数据管道 2.5 都写了 BGE-M3 稠密 + Milvus BM25 + RRF",
    ),
    GoldCase(
        case_id="citation_three_layers",
        question="引用安全的三层防线分别检查什么，失败语义是什么？",
        gold_chunk_ids=("README.md#23", "docs/模块实现说明.md#78"),
        notes="L1 存在性 / L2 / L3 的分层与失败语义",
    ),
    GoldCase(
        case_id="abstention_double_threshold",
        question="RAG4C 在什么情况下会弃权不回答？",
        gold_chunk_ids=("README.md#24", "docs/模块实现说明.md#80"),
        notes="双重阈值弃权：无检索结果 / 最高分低于阈值 / 蕴含分不足",
    ),
    GoldCase(
        case_id="document_state_machine",
        question="文档从登记到可检索要经过哪些状态？",
        gold_chunk_ids=(
            "README.md#25",
            "docs/模块实现说明.md#98",
            "docs/数据管道说明.md#138",
        ),
        notes="waiting -> parsing -> splitting -> indexing -> completed / error",
    ),
    GoldCase(
        case_id="single_source_of_truth",
        question="单一事实源原则里，MySQL Catalog 和 Milvus 各自拥有什么？",
        gold_chunk_ids=("CONTEXT.md#4", "README.md#13"),
        notes="Catalog 拥有身份/生命周期/审计，Milvus 只是检索投影",
    ),
    GoldCase(
        case_id="knowledge_lifeline",
        question="RAG4C 的 Knowledge Lifeline 指的是什么？",
        gold_chunk_ids=("CONTEXT.md#0",),
        notes="产品命题段：答案可回溯到引用/检索/投影/版本/来源",
    ),
    GoldCase(
        case_id="projection_fence",
        question="Projection Fence 是什么，它防的是什么问题？",
        gold_chunk_ids=("CONTEXT.md#2",),
        notes="领域术语表：期望修订号校验，防止 stale 写入覆盖新数据",
    ),
    GoldCase(
        case_id="chunking_mode_auto",
        question="切分路由的 auto 模式怎么处理 csv/excel 和小文档？",
        gold_chunk_ids=("docs/模块实现说明.md#109",),
        notes="csv/excel 走 qa 切分；全文小于阈值走 simple",
    ),
    GoldCase(
        case_id="sentence_window",
        question="句子窗口（SentenceWindow）检索是怎么做的？",
        gold_chunk_ids=("docs/模块实现说明.md#73",),
        notes="small-to-big：命中子块回取父 chunk",
    ),
    GoldCase(
        case_id="mmr_diversity",
        question="来源多样性的 MMR 是怎么打分的？",
        gold_chunk_ids=("docs/模块实现说明.md#70", "docs/数据管道说明.md#143"),
        notes="λ·相关 + (1-λ)·多样性，同文档惩罚",
    ),
    GoldCase(
        case_id="deepdoc_pdf_route",
        question="DeepDoc 双引擎是怎么给 PDF 分类并选择解析路径的？",
        gold_chunk_ids=("docs/模块实现说明.md#100", "docs/数据管道说明.md#131"),
        notes="pdf-inspector 采样分类，text_based 走 Parser，否则 Vision/OCR",
    ),
    GoldCase(
        case_id="query_pipeline_order",
        question="查询管道从问题到有据回答的编排顺序是什么？",
        gold_chunk_ids=("docs/数据管道说明.md#140",),
        notes="gate -> rewrite -> route -> retrieve -> rerank -> generate -> verify",
    ),
    GoldCase(
        case_id="llm_slots",
        question="RAG4C 一共有多少个 LLM 槽位，哪些有真实调用？",
        gold_chunk_ids=("docs/RAG策略矩阵.md#180",),
        notes="11 个槽位与实际消费情况",
    ),
    GoldCase(
        case_id="complexity_gate",
        question="复杂度门控是做什么的，命中时省掉了哪些步骤？",
        gold_chunk_ids=("docs/模块实现说明.md#68", "docs/数据管道说明.md#141"),
        notes="具体查询跳过改写与路由，零 LLM 成本",
    ),
    GoldCase(
        case_id="add_new_source",
        question="要给 RAG4C 新增一种文档来源，需要实现什么？",
        gold_chunk_ids=("docs/文档源接入层.md#198",),
        notes="实现 DocumentSource 契约并注册",
    ),
    GoldCase(
        case_id="incremental_ingest",
        question="文档源的增量是怎么实现的，为什么要做增量？",
        gold_chunk_ids=("docs/文档源接入层.md#195",),
        notes="状态文件记录游标，避免全量重跑白花嵌入调用",
    ),
    GoldCase(
        case_id="extension_axis_patterns",
        question="后端扩展轴改造只允许用哪几种模式？",
        gold_chunk_ids=("docs/compose/spec/backend-extensibility-standard.md#220",),
        notes="Strategy+Registry / 声明式规格表 / Adapter / Template Method",
    ),
    GoldCase(
        case_id="extension_axis_done",
        question="一个扩展轴做到什么程度才算改完了？",
        gold_chunk_ids=("docs/compose/spec/backend-extensibility-standard.md#222",),
        notes="零改分支 + 常驻守卫 + 反向验证",
    ),
    GoldCase(
        case_id="query_cache_key",
        question="查询缓存的键由哪些部分组成，TTL 是多长？",
        gold_chunk_ids=("docs/后端可插拔与高并发改造.md#208",),
        notes="tenant_id + query + ACL 等，10 分钟 TTL",
    ),
    GoldCase(
        case_id="ingest_concurrency",
        question="文档入库的并发默认同时执行几个任务，排队上限是多少？",
        gold_chunk_ids=("docs/后端可插拔与高并发改造.md#210",),
        notes="有界线程池：默认 2 个执行 + 8 个排队",
    ),
    GoldCase(
        case_id="sparse_vector_source",
        question="写入 Milvus 时的稀疏向量是怎么来的？",
        gold_chunk_ids=("docs/数据管道说明.md#136",),
        notes="Milvus 内置 BM25 Function，应用层不生成稀疏向量",
    ),
    GoldCase(
        case_id="trace_spans_metrics",
        question="trace spans 最终会变成什么可观测指标？",
        gold_chunk_ids=("docs/数据管道说明.md#149",),
        notes="span 耗时进进程内指标（P50/P95/P99 + 错误率）",
    ),
    GoldCase(
        case_id="filter_expression_syskeys",
        question="过滤表达式的安全校验保留了哪些系统键？",
        gold_chunk_ids=("docs/数据管道说明.md#148",),
        notes="SYS_KEYS：tenant_id/dataset_id/doc_id/doc_type/page/chunk_level",
    ),
    # ---- 不可答用例：语料里没有答案，只看检索噪声 ----
    GoldCase(
        case_id="unanswerable_k8s",
        question="如何用 Kubernetes 部署一套 Redis 哨兵集群？",
        unanswerable=True,
        notes="语料不涉及 K8s 部署",
    ),
    GoldCase(
        case_id="unanswerable_pizza",
        question="烤披萨时烤箱要预热到多少度？",
        unanswerable=True,
        notes="语料不涉及烹饪",
    ),
    GoldCase(
        case_id="unanswerable_worldcup",
        question="2026 年世界杯的冠军是哪支球队？",
        unanswerable=True,
        notes="语料不涉及体育赛事",
    ),
)


def validate_against_corpus(chunk_ids: Sequence[str]) -> list[str]:
    """返回 gold 里**不存在于当前语料**的 chunk_id（空列表表示标注仍然有效）。

    切片口径或语料文件一变，这里就会报出失效标注，避免"指标悄悄变差"却
    被误读成检索退化。
    """
    known = set(chunk_ids)
    broken: list[str] = []
    for case in GOLD_SET:
        for gold_id in case.gold_chunk_ids:
            if gold_id not in known:
                broken.append(f"{case.case_id}:{gold_id}")
    return broken


def answerable_cases() -> tuple[GoldCase, ...]:
    """只返回可答用例（算 Recall/MRR/NDCG 的那部分）。"""
    return tuple(case for case in GOLD_SET if not case.unanswerable)


@dataclass(frozen=True)
class GoldSet:
    """评测集快照（供报告落盘时留档，标明用的是哪一版标注）。"""

    version: str = DATASET_VERSION
    cases: tuple[GoldCase, ...] = field(default_factory=lambda: GOLD_SET)
