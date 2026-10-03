"""F1.1 答案层黄金集（生产语料）——答案层 EvalReport v2 的输入。

设计取舍：**不复制那 126 条问题**。答案层复用检索层已逐条核对过原文的
``PRODUCTION_GOLD``，只在上面补答案层需要的字段（不可答标记、语言、gold
chunk 用于溯源）。两份清单各写一份迟早对不上——9/28 的教训就是"同义切片"
口径在两处不一致，把 Recall 分母撑大了。

## 不可答样本：3 → 22 条（F1.1 要求 >=15）

保留 3 条原有**离域**不可答（披萨/世界杯/公积金），新增 19 条**同域但事实不在
库里**的"硬负样本"。新增的每一条都跑过
``python -m eval.verify_unanswerable_candidates``，用真实嵌入 + 真实 Milvus
取 top-5，再让裁判判断"那个具体事实在不在片段里"。

**为什么用裁判而不是余弦阈值**（这条是本轮最重要的发现之一）：

    同域但无答案的问题，top1 余弦实测落在 0.56~0.73，
    而弃权阈值只有 0.52 —— 全部在闸门上方。

也就是说：余弦阈值只能拦住"披萨几度"这种离域问题（实测 top-score 均值
0.406~0.470），**拦不住同域问错**。而后者才是幻觉的主要来源。这批硬负样本
就是专门拿来暴露这件事的。判定时不能看"像不像"，只能看"片段里有没有写出
那个具体事实"，所以走裁判。

剔除记录：候选 20 条里 ``unans_spring_security_saml`` 被判掉——
Spring Boot 文档里确实有 SAML 2.0 一章并写明了要引入的依赖，那条其实有答案。

## 为什么没有 expected_keywords

F1.2 的六个指标（拒答率 / 过度拒答率 / 幻觉率 / 有据性 / 相关性 / 引用失败率）
全部由**裁判对照检索到的证据**判定，不需要标准答案关键词；硬造一批关键词
反而会污染 F1.4 的裁判可信度抽检。需要人工参考时，用 ``gold_chunk_ids``
回生产集合取原文。
"""
from __future__ import annotations

from eval.retrieval_eval.gold_production import PRODUCTION_GOLD

#: 新增的同域硬负样本，全部经 verify_unanswerable_candidates 判定通过
#: (问题, 语言, 为什么库里没有 —— 由裁判给出)
UNANSWERABLE_EXTRA: tuple[tuple[str, str, str], ...] = (
    (
        "Milvus 托管版（Zilliz Cloud）按 CU 计费，8 CU 的集群一个月多少钱？",
        "zh",
        "片段仅提及按实际使用付费，未提供 8 CU 集群的具体月费报价",
    ),
    (
        "Milvus 用 MongoDB 做元存储时，需要配置哪些连接参数？",
        "zh",
        "片段仅说明元存储用 etcd，未提及 MongoDB；属伪前提",
    ),
    (
        "Milvus 的 GraphQL 接口怎么查询集合列表？",
        "zh",
        "片段仅提及 PyMilvus SDK 与命令行，无 GraphQL 接口",
    ),
    (
        "Milvus 在 Windows 上原生安装时，服务以什么方式注册为系统服务？",
        "zh",
        "片段未提及 Windows 原生安装与系统服务注册",
    ),
    (
        "Milvus 里两个集合之间怎么做类似 SQL JOIN 的关联查询？",
        "zh",
        "片段只讲单集合内混合查询，未提集合间 JOIN",
    ),
    (
        "把 Milvus 的数据同步到 Oracle 数据库要配哪个 connector？",
        "zh",
        "片段未提及 Oracle 同步 connector",
    ),
    (
        "LangChain 的 Java 版本 langchain4j 怎么声明 ChatModel bean？",
        "zh",
        "片段均为 Python 版代码，无 langchain4j",
    ),
    (
        "LangGraph 用 Cassandra 做 checkpointer 时表结构怎么建？",
        "zh",
        "片段未提及 Cassandra 作为 checkpointer",
    ),
    (
        "LangSmith 团队版每个月包含多少条 trace 额度？",
        "zh",
        "片段只讲功能与环境变量，无套餐额度",
    ),
    (
        "LangChain 的 .NET SDK 怎么初始化一个 Agent？",
        "zh",
        "片段只有 Python 与 JS/TS 示例，无 .NET SDK",
    ),
    (
        "LangGraph Platform 在 Kubernetes 上怎么配置 HPA 自动扩缩容？",
        "zh",
        "片段只涉及版本升级与自定义端点，无 K8s/HPA",
    ),
    (
        "RedisJSON 单个文档最大能存多少 MB？",
        "zh",
        "片段只讨论内存消耗与 RedisJSON 基本特性，无容量上限",
    ),
    (
        "怎么把 Oracle 的变更通过 Redis CDC 同步进来？",
        "zh",
        "片段未提及 Oracle 或 Redis CDC 同步",
    ),
    (
        "Redis Operator 在 K8s 上创建集群时怎么配 TLS 证书轮转？",
        "zh",
        "片段只涉及 Redis 原生 TLS 配置，无 Operator 证书轮转",
    ),
    (
        "Redis Cloud 的 25GB 套餐每月多少钱？",
        "zh",
        "片段未提及 25GB 套餐及价格",
    ),
    (
        "Spring Boot 整合 Kafka 时怎么配 exactly-once 语义的 isolation.level？",
        "zh",
        "片段只讲通用 Kafka 属性，未提 exactly-once / isolation.level",
    ),
    (
        "Spring Boot 打成 GraalVM 原生镜像时，怎么为 MyBatis 写反射提示？",
        "zh",
        "片段只讲 GraalVM 与 Tracing Agent 通用概念，无 MyBatis 反射提示",
    ),
    (
        "Spring Batch 的远程分区（remote partitioning）怎么配置分区数？",
        "zh",
        "片段未提及 Spring Batch 远程分区",
    ),
    (
        "Spring Boot 商业支持的年度订阅费用是多少？",
        "zh",
        "片段只提社区支持与开源，无商业订阅费用",
    ),
)


def _build() -> list[dict]:
    """装配答案层数据集：检索层的 126 条 + 不可答样本。"""
    out: list[dict] = []

    # 1) 检索层已核对过的可答题：沿用问题与 gold chunk，补答案层字段
    for case in PRODUCTION_GOLD:
        if case.unanswerable:
            # 原有 3 条离域不可答，原样保留（case_id 带 unanswerable_ 前缀）
            out.append(
                {
                    "id": case.case_id,
                    "question": case.question,
                    "unanswerable": True,
                    "corpus_language": case.corpus_language,
                    "gold_chunk_ids": list(case.gold_chunk_ids),
                    "notes": case.notes or "",
                    "kind": "off_domain",
                }
            )
            continue
        out.append(
            {
                "id": case.case_id,
                "question": case.question,
                "unanswerable": False,
                "corpus_language": case.corpus_language,
                "gold_chunk_ids": list(case.gold_chunk_ids),
                "notes": case.notes or "",
                "kind": "answerable",
            }
        )

    # 2) 新增的同域硬负样本
    for idx, (question, lang, why) in enumerate(UNANSWERABLE_EXTRA, start=1):
        out.append(
            {
                "id": f"unanswerable_ondomain_{idx:02d}",
                "question": question,
                "unanswerable": True,
                "corpus_language": lang,
                "gold_chunk_ids": [],
                "notes": why,
                "kind": "on_domain",
            }
        )

    return out


#: run_eval 的加载目标：``eval/answer_eval/gold_answer_production.py:ANSWER_GOLD``
ANSWER_GOLD: list[dict] = _build()


def summary() -> dict[str, int]:
    """数据集构成速览（供文档与自检用）。"""
    total = len(ANSWER_GOLD)
    unans = [c for c in ANSWER_GOLD if c["unanswerable"]]
    return {
        "total": total,
        "answerable": total - len(unans),
        "unanswerable": len(unans),
        "unanswerable_off_domain": sum(1 for c in unans if c["kind"] == "off_domain"),
        "unanswerable_on_domain": sum(1 for c in unans if c["kind"] == "on_domain"),
    }


if __name__ == "__main__":
    print(summary())
