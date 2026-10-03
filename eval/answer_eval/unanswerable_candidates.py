"""F1.1 不可答候选池：同领域、不同事实（必须先验证再入库）。

master-plan F1.1 要求：不可答样本从 3 条扩到 >=15 条，且要**看起来像可答**
（同领域、不同事实），不能写成"烤披萨几度"这种一眼无关的问题——那种用例
测不出过度拒答，因为任何检索都会直接判空。

用法（先验证，再挑进 gold）：

    python -m eval.verify_unanswerable_candidates

判定口径沿用 9/28 的实测分界：答得上 top1 余弦 min 0.576、答不上 max 0.492，
弃权阈值 0.52 落在中间。所以候选的 top1 余弦必须 **< 0.50** 才算真的没答案；
落在 0.50~0.58 的灰区一律弃用（宁可少几条，也不要掺进假不可答）。
"""
from __future__ import annotations

#: (候选 id, 问题, 为什么认为库里没有) —— 认为不算数，必须过验证
CANDIDATES: tuple[tuple[str, str, str], ...] = (
    # ---- Milvus 域（库里有 Milvus 文档，但以下事实不在文档里）----
    (
        "unans_milvus_cloud_price",
        "Milvus 托管版（Zilliz Cloud）按 CU 计费，8 CU 的集群一个月多少钱？",
        "文档讲架构与用法，不涉及商业报价",
    ),
    (
        "unans_milvus_mongodb_meta",
        "Milvus 用 MongoDB 做元存储时，需要配置哪些连接参数？",
        "文档明确元存储是 etcd；问 MongoDB 怎么配是伪前提，库里没有",
    ),
    (
        "unans_milvus_graphql",
        "Milvus 的 GraphQL 接口怎么查询集合列表？",
        "Milvus 只有 REST/gRPC/SDK，没有 GraphQL",
    ),
    (
        "unans_milvus_windows_native",
        "Milvus 在 Windows 上原生安装时，服务以什么方式注册为系统服务？",
        "文档只有 Docker/Helm/Linux 部署，无 Windows 原生",
    ),
    (
        "unans_milvus_join",
        "Milvus 里两个集合之间怎么做类似 SQL JOIN 的关联查询？",
        "向量库无 JOIN；文档不会写怎么写 JOIN",
    ),
    (
        "unans_milvus_oracle_sink",
        "把 Milvus 的数据同步到 Oracle 数据库要配哪个 connector？",
        "文档不涉及 Oracle 同步",
    ),
    # ---- LangChain / LangGraph 域 ----
    (
        "unans_langchain_java_version",
        "LangChain 的 Java 版本 langchain4j 怎么声明 ChatModel bean？",
        "库里是 Python 版文档，langchain4j 不在其中",
    ),
    (
        "unans_langgraph_cassandra_ckpt",
        "LangGraph 用 Cassandra 做 checkpointer 时表结构怎么建？",
        "checkpointer 文档只讲 Memory/SQLite/Postgres",
    ),
    (
        "unans_langchain_pricing",
        "LangSmith 团队版每个月包含多少条 trace 额度？",
        "商业套餐额度不在开源文档里",
    ),
    (
        "unans_langchain_dotnet",
        "LangChain 的 .NET SDK 怎么初始化一个 Agent？",
        "库里没有 .NET SDK 文档",
    ),
    (
        "unans_langgraph_k8s_autoscale",
        "LangGraph Platform 在 Kubernetes 上怎么配置 HPA 自动扩缩容？",
        "部署文档不涉及 K8s HPA 配置",
    ),
    # ---- Redis 域（库里 28.5% 是 Redis 文档）----
    (
        "unans_redis_json_limit",
        "RedisJSON 单个文档最大能存多少 MB？",
        "库里是命令参考，未必有这个上限值",
    ),
    (
        "unans_redis_oracle_cdc",
        "怎么把 Oracle 的变更通过 Redis CDC 同步进来？",
        "文档不涉及 Oracle CDC 连接器",
    ),
    (
        "unans_redis_cluster_k8s_operator",
        "Redis Operator 在 K8s 上创建集群时怎么配 TLS 证书轮转？",
        "库里以 OSS 命令参考为主",
    ),
    (
        "unans_redis_cloud_price",
        "Redis Cloud 的 25GB 套餐每月多少钱？",
        "商业报价不在开源文档里",
    ),
    # ---- Spring Boot 域（库里 17.8%）----
    (
        "unans_spring_kafka_exactly_once",
        "Spring Boot 整合 Kafka 时怎么配 exactly-once 语义的 isolation.level？",
        "文档未必覆盖该具体配置项",
    ),
    (
        "unans_spring_native_graal_hint",
        "Spring Boot 打成 GraalVM 原生镜像时，怎么为 MyBatis 写反射提示？",
        "库里是参考文档，MyBatis 相关内容通常不在其中",
    ),
    (
        "unans_spring_batch_partition",
        "Spring Batch 的远程分区（remote partitioning）怎么配置分区数？",
        "文档覆盖有限",
    ),
    (
        "unans_spring_security_saml",
        "Spring Boot 接 SAML 2.0 单点登录要引入哪个 starter？",
        "文档不一定覆盖 SAML",
    ),
    (
        "unans_spring_license_price",
        "Spring Boot 商业支持的年度订阅费用是多少？",
        "商业报价不在开源文档里",
    ),
)
