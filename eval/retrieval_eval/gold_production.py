"""生产语料评测集（rag4c_chunks 真实切片，2026-09-28 标注）。

⚠️ 语料构成（2026-10-04 实测修正，此前这段描述是错的）：

    9723 切片，按来源聚合——
        langchain/langgraph  3709 (38.1%)
        redis/docs           2767 (28.5%)
        spring-boot          1729 (17.8%)
        milvus（含中文 MinerU 切片）1514 (15.6%)
        其他                    4

    旧描述写的是「Milvus 1356 + LangChain 644」，与实际相差一个数量级，
    **Milvus 只占 15.6%**，而 Redis / Spring Boot 两个入库时被忽略的大头
    合计 46.3%。写新的不可答用例时务必按上面的真实分布判断"库里有没有"，
    照旧描述推会推出一堆其实有答案的用例（9/28 已经踩过一次：原以为
    "年假流程"无答案，实测库里就有写着年假制度的中文演示切片）。

标注方式与文档代理语料一致：**先定位候选、再读原文确认**，
只把"能独立回答该问题"的切片算 gold。

chunk_id 是生产集合里的真实主键；换库 / 重建索引后 id 会变，
跑之前请先执行校验（``validate_against_ids``），失效会明确报出来。
"""
from __future__ import annotations

from .gold import GoldCase

DATASET_VERSION = "2026-09-28.production.2"

PRODUCTION_GOLD: tuple[GoldCase, ...] = (
    GoldCase(
        case_id="bin_ivf_flat",
        corpus_language="en",
        question="BIN_IVF_FLAT 索引适用于什么向量，它是怎么加速查询的？",
        gold_chunk_ids=(
            "doc-67dd31e84e70::0000::d8f245777aaa",
        ),
        notes="原文：IVF_FLAT 的变体，专用于二值向量；先按 nlist 分簇再比对簇心",
    ),
    GoldCase(
        case_id="langchain_rag_pipeline",
        corpus_language="en",
        question="用 LangChain 搭的检索增强流水线分哪两个阶段，各自做什么？",
        gold_chunk_ids=(
            "doc-608bf00072bb::0010::b51a13a30481",
        ),
        notes="原文：Vectorstore（Milvus）检索 + LLM 生成两阶段",
    ),
    GoldCase(
        case_id="langchain_rag_chain",
        corpus_language="en",
        question="LangChain 的 RAG chain 里 RunnablePassthrough 和 PromptTemplate 是怎么串起来的？",
        gold_chunk_ids=(
            "doc-608bf00072bb::0012::311f1a75fe31",
        ),
        notes="原文含 StrOutputParser / PromptTemplate / RunnablePassthrough / langchain_milvus 的链式代码",
    ),
    GoldCase(
        case_id="create_collection_params",
        corpus_language="en",
        question="创建集合时 metric_type 与 consistency_level 分别可以设成什么（示例值）？",
        gold_chunk_ids=(
            "doc-29290878c348::0006::11a7a550de3d",
            "doc-580200058f87::0006::11a7a550de3d",
            "doc-df6cab337b74::0006::11a7a550de3d",
            "doc-eca9f75805bc::0006::11a7a550de3d",
            "doc-f3373366c766::0006::11a7a550de3d",
        ),
        notes="原文示例：metric_type='IP'、consistency_level='Bounded'",
    ),
    GoldCase(
        case_id="milvus_client_uri",
        corpus_language="en",
        question="MilvusClient 的 uri 写成本地文件和写成服务端地址有什么区别？",
        gold_chunk_ids=(
            "doc-29290878c348::0004::7760d38fb460",
            "doc-082e222f5489::0006::4093fc3b79b9",
            "doc-580200058f87::0004::7760d38fb460",
            "doc-580200058f87::0005::8afb6740772b",
            "doc-b1c65d6b7321::0010::88a19ee567cf",
            "doc-df6cab337b74::0004::7760d38fb460",
            "doc-df6cab337b74::0005::8afb6740772b",
            "doc-eca9f75805bc::0004::7760d38fb460",
            "doc-eca9f75805bc::0005::8afb6740772b",
            "doc-f3373366c766::0004::7760d38fb460",
            "doc-f3373366c766::0005::8afb6740772b",
        ),
        notes="原文：本地文件用 Milvus Lite，服务端地址连 Milvus server / Zilliz（同义切片较多，两片均可）",
    ),
    GoldCase(
        case_id="add_collection_field",
        corpus_language="en",
        question="如何给已存在的集合新增一个用户自定义的标量字段？",
        gold_chunk_ids=(
            "doc-165f6cdaeb63::0005::a860369c4b86",
        ),
        notes="原文：add_collection_field()，与动态字段的区别（Limits 同义切片一并算命中）",
    ),
    GoldCase(
        case_id="bm25_function",
        corpus_language="en",
        question="怎么在 schema 里定义 BM25 function，它的输入输出字段是什么？",
        gold_chunk_ids=(
            "doc-d331c7609f1f::0012::dbf952c868fa",
        ),
        notes="原文：Function(name/function_type/input_field_names/output_field_names)",
    ),
    GoldCase(
        case_id="knowhere_aisaq",
        corpus_language="en",
        question="milvus.yaml 里 knowhere AISAQ 的 build 参数有哪些，各自什么含义？",
        gold_chunk_ids=(
            "doc-04eb2fd88296::0006::44219bc4680d",
        ),
        notes="原文：max_degree（每个点最大边数）与 search_list_size（构建期候选池）",
    ),
    GoldCase(
        case_id="langchain_milvus_install",
        corpus_language="en",
        question="用 langchain-milvus 需要安装哪些包？",
        gold_chunk_ids=(
            "doc-21428cbc94a9::0001::98916ca78fd5",
        ),
        notes="原文：pip install -qU langchain-milvus milvus-lite langchain…",
    ),
    GoldCase(
        case_id="milvus_lite_collection",
        corpus_language="en",
        question="用 Milvus Lite 建一个 RAG 集合的完整代码示例是什么？",
        gold_chunk_ids=(
            "doc-082e222f5489::0006::4093fc3b79b9",
            "doc-580200058f87::0004::7760d38fb460",
            "doc-580200058f87::0005::8afb6740772b",
            "doc-b1c65d6b7321::0010::88a19ee567cf",
            "doc-df6cab337b74::0004::7760d38fb460",
            "doc-df6cab337b74::0005::8afb6740772b",
            "doc-eca9f75805bc::0004::7760d38fb460",
            "doc-eca9f75805bc::0005::8afb6740772b",
            "doc-f3373366c766::0004::7760d38fb460",
            "doc-f3373366c766::0005::8afb6740772b",
        ),
        notes="原文：MilvusClient(uri='./milvus_demo.db') + create_collection（同义切片较多）",
    ),
    # ---- 第二批（2026-09-28 扩标，样本从 10 条扩到 32 条）----
    GoldCase(
        case_id="cnalphanumonly_filter",
        corpus_language="en",
        question="cnalphanumonly 这个 filter 的作用是什么？",
        gold_chunk_ids=(
            "doc-81c4dda02604::0000::f140320e0f2f",
            "doc-719c4dc9d2f3::0000::e5bf1660c7f1",
            "doc-c57ad512c5ed::0001::8e84b15c1f86",
        ),
        notes="原文：移除含非中文字符的 token",
    ),
    GoldCase(
        case_id="analyzer_on_varchar",
        corpus_language="en",
        question="怎么给集合 schema 里的 VARCHAR 字段配置 analyzer？",
        gold_chunk_ids=(
            "doc-4e6bac3eb194::0021::3dc9a434e5d6",
        ),
        notes="原文：schema.add_field(field_name='text', … analyzer_params …)",
    ),
    GoldCase(
        case_id="partition_key_drop_old",
        corpus_language="en",
        question="创建带分区键的集合时 drop_old 和 partition_key_field 怎么设？",
        gold_chunk_ids=(
            "doc-21428cbc94a9::0011::9337c60dae6b",
        ),
        notes="原文：drop_old=True, partition_key_field='namespace'",
    ),
    GoldCase(
        case_id="rag_private_knowledge",
        corpus_language="en",
        question="RAG 示例里作为私有知识库的数据来自哪里？",
        gold_chunk_ids=(
            "doc-580200058f87::0002::a3b844f3d35d",
            "doc-082e222f5489::0002::a3b844f3d35d",
            "doc-29290878c348::0002::18214789f5d5",
            "doc-761548917930::0003::a3b844f3d35d",
            "doc-9d1c0381b5e1::0005::a3b844f3d35d",
            "doc-df6cab337b74::0002::a3b844f3d35d",
            "doc-eca9f75805bc::0002::f6fb94d3a4d9",
            "doc-f3373366c766::0002::a3b844f3d35d",
        ),
        notes="原文：Milvus Documentation 2.4.x 的 FAQ 页面",
    ),
    GoldCase(
        case_id="rag_llm_response",
        corpus_language="en",
        question="检索到文档之后，怎么把它们交给 LLM 生成 RAG 回答？",
        gold_chunk_ids=(
            "doc-580200058f87::0012::e41b3b7bfe7a",
            "doc-29290878c348::0013::aae95f21b3bd",
            "doc-9d1c0381b5e1::0018::1e2026024b5c",
            "doc-df6cab337b74::0012::a456a485e2c0",
            "doc-f3373366c766::0012::efd6a769d9b9",
        ),
        notes="原文：把检索结果拼成 context 字符串再交给 LLM",
    ),
    GoldCase(
        case_id="init_clients",
        corpus_language="en",
        question="示例里初始化了哪些客户端，用的什么嵌入模型？",
        gold_chunk_ids=(
            "doc-2e6a983755a8::0002::1e9bf10c8498",
        ),
        notes="原文：Exa / OpenAI / Milvus，嵌入用 text-embedding-3-small",
    ),
    GoldCase(
        case_id="embed_in_one_batch",
        corpus_language="en",
        question="示例是怎么把全部文档一次性嵌入并写入的？",
        gold_chunk_ids=(
            "doc-2e6a983755a8::0011::60a84b214e89",
        ),
        notes="原文：embed_text([...]) 一次批量调用 + milvus.insert（相邻重复切片）",
    ),
    GoldCase(
        case_id="deploy_grafana",
        corpus_language="en",
        question="怎么用 kubectl 和 helm 部署 Grafana？",
        gold_chunk_ids=(
            "doc-e838da572048::0007::8c00d816413f",
        ),
        notes="原文：kubectl create ns monitoring + helm install",
    ),
    GoldCase(
        case_id="retry_middleware_on_failure",
        corpus_language="en",
        question="ModelRetryMiddleware 的 on_failure 设成 error 会发生什么？",
        gold_chunk_ids=(
            "langchain-built-in-7b9dc737993d608a::0035::837dd7bec281",
        ),
        notes="原文：on_failure='error' 直接抛出（re-raise）",
    ),
    GoldCase(
        case_id="handoff_tool",
        corpus_language="en",
        question="LangChain 里怎么创建一个把手柄转交给其他 agent 的 handoff 工具？",
        gold_chunk_ids=(
            "langchain-handoffs-16f53a26529f35fe::0023::3fdb90f68587",
        ),
        notes="原文：@tool 定义 transfer_to_sales 并返回 Command",
    ),
    GoldCase(
        case_id="override_system_prompt",
        corpus_language="en",
        question="怎么在一次请求里覆盖 system prompt 和本步可用的工具？",
        gold_chunk_ids=(
            "langchain-handoffs-customer-support-8d80752939837533::0027::51660989fcf7",
            "langchain-handoffs-customer-support-8d80752939837533::0065::9f970b649657",
        ),
        notes="原文：request.override(system_prompt=…, tools=…)",
    ),
    GoldCase(
        case_id="guardrail_layer4",
        corpus_language="en",
        question="LangChain 护栏的第四层（Model-based safety check）是什么？",
        gold_chunk_ids=(
            "langchain-guardrails-9f0ab2ac44cf5f81::0041::da00d27269b5",
        ),
        notes="原文：SafetyGuardrailMiddleware 放在 agent 之后",
    ),
    GoldCase(
        case_id="store_read_preferences",
        corpus_language="en",
        question="怎么从 LangChain 的 store 里读取已有的用户偏好？",
        gold_chunk_ids=(
            "langchain-context-engineering-3a2410aed5839a15::0089::a990d4ed06f0",
        ),
        notes="原文：runtime.store.get(('preferences',) …)",
    ),
    GoldCase(
        case_id="format_for_synthesis",
        corpus_language="en",
        question="多路检索结果在交给汇总步骤之前怎么格式化？",
        gold_chunk_ids=(
            "langchain-router-knowledge-base-ab66531b2bc5c479::0024::e0b8b04bbee4",
        ),
        notes="原文：拼成 **From {source}:**\n{result} 形式（相邻重复切片）",
    ),
    GoldCase(
        case_id="connect_to_agent_stream",
        corpus_language="en",
        question="前端怎么连到 agent 的流式输出？",
        gold_chunk_ids=(
            "langchain-declarative-generative-ui-5d346256168e51cc::0009::ce2ffaae32c7",
        ),
        notes="原文：用 useStream 连 agent 的结构化输出",
    ),
    GoldCase(
        case_id="openui_followup",
        corpus_language="en",
        question="OpenUI 的 Button 组件怎么做追问（follow-up）？",
        gold_chunk_ids=(
            "langchain-openui-0fc7f4f1aa545aca::0016::0afad06eff3e",
        ),
        notes="原文：continue_conversation action 类型",
    ),
    GoldCase(
        case_id="structured_output_fields",
        corpus_language="en",
        question="结构化输出的示例 ProductReview 里有哪些字段？",
        gold_chunk_ids=(
            "langchain-structured-output-61971813b49081b6::0021::e9294673a988",
        ),
        notes="原文：rating / sentiment / key_points",
    ),
    GoldCase(
        case_id="thinking_bubble",
        corpus_language="en",
        question="ThinkingBubble 组件用来展示什么内容？",
        gold_chunk_ids=(
            "langchain-reasoning-tokens-1ff51708954da3aa::0007::12434fef1991",
        ),
        notes="原文：以可折叠的独立样式展示 reasoning tokens",
    ),
    GoldCase(
        case_id="langchain_history",
        corpus_language="en",
        question="LangChain 的演进历史大致是怎样的？",
        gold_chunk_ids=(
            "langchain-philosophy-f09c8568f408e1dd::0003::138df37c4d36",
        ),
        notes="原文：History 小节讲版本演进时间线",
    ),
    GoldCase(
        case_id="cache_ttl_zero",
        corpus_language="en",
        question="cacheTtl 设成 0 是什么意思，为什么还要预热？",
        gold_chunk_ids=(
            "doc-9a5d0edf9c2e::0009::8d4625110274",
        ),
        notes="原文：0 = 不过期，避免频繁重载；预热消除首次命中抖动",
    ),
    GoldCase(
        case_id="cli_option_uri",
        corpus_language="en",
        question="命令行选项里 -uri 是什么含义？",
        gold_chunk_ids=(
            "doc-de158425cec6::0005::612ad44c26a2",
        ),
        notes="原文：Options 表里的 -uri / –uri",
    ),
    GoldCase(
        case_id="cli_option_collection",
        corpus_language="en",
        question="命令行选项里 -c 代表什么？",
        gold_chunk_ids=(
            "doc-de158425cec6::0026::6e277da97970",
            "doc-de158425cec6::0023::6373c3979df9",
            "doc-de158425cec6::0029::0f9f4053942a",
        ),
        notes="原文：-c / –collection-name 集合名（Options 表在多个切片重复出现）",
    ),
    # ---- 第三批（扩到 50+ 条，2026-09-28）----
    GoldCase(
        case_id="insert_binary_vectors",
        corpus_language="en",
        question="创建集合之后，怎么插入包含二值向量的数据？",
        gold_chunk_ids=(
            "doc-8b90a3417a8e::0016::05dc4a02736c",
        ),
        notes="原文：用 insert 方法加入含 binary vector 的数据",
    ),
    GoldCase(
        case_id="rag_fireworks_ai",
        corpus_language="en",
        question="用 Milvus 和 Fireworks AI 搭 RAG 的示例讲的是什么？",
        gold_chunk_ids=(
            "doc-f3373366c766::0000::ea98011f1f56",
        ),
        notes="原文开头：Fireworks AI 是生成式推理平台 + 与 Milvus 搭 RAG",
    ),
    GoldCase(
        case_id="boost_ranker",
        corpus_language="en",
        question="Boost Ranker 是什么，它解决什么问题？",
        gold_chunk_ids=(
            "doc-fc3978761cd1::0000::3dae4b6719cf",
        ),
        notes="原文：不只依赖语义相似度做排序（2.6.2+）",
    ),
    GoldCase(
        case_id="verify_deployment_ip",
        corpus_language="en",
        question="部署完成后怎么拿到服务的外部 IP？",
        gold_chunk_ids=(
            "doc-034dd66c30f3::0010::6d0462430bf1",
        ),
        notes="原文：等所有 pod 起来后取 external IP",
    ),
    GoldCase(
        case_id="struct_field_schema",
        corpus_language="en",
        question="怎么为 caption 这个 struct 字段创建 schema？",
        gold_chunk_ids=(
            "doc-7e0d69d345fe::0015::e498e0fd74e0",
        ),
        notes="原文：create_struct_field_schema() 后 add_field",
    ),
    GoldCase(
        case_id="memory_overview",
        corpus_language="en",
        question="LangChain 里的 memory 是什么，用来解决什么问题？",
        gold_chunk_ids=(
            "langchain-short-term-memory-32f4eccb3934bb78::0001::90696bae0e92",
        ),
        notes="原文 Overview：记住历史交互信息的系统",
    ),
    GoldCase(
        case_id="add_short_term_memory",
        corpus_language="en",
        question="怎么给 agent 加短期记忆（thread 级持久化）？",
        gold_chunk_ids=(
            "langgraph-add-memory-2ba78eb4ab1a5fdc::0001::23ddcf0596d8",
        ),
        notes="原文：短期记忆 = thread 级持久化，跟踪多轮会话",
    ),
    GoldCase(
        case_id="parent_graph_subgraph",
        corpus_language="en",
        question="怎么把一个 subgraph 作为节点挂到父图上？",
        gold_chunk_ids=(
            "langgraph-add-memory-2ba78eb4ab1a5fdc::0029::466e95f580a6",
        ),
        notes="原文：builder.add_node('node_1', subgraph)",
    ),
    GoldCase(
        case_id="inflight_threads",
        corpus_language="en",
        question="删除节点或改 State key 之前，为什么要先检测在途线程？",
        gold_chunk_ids=(
            "langgraph-backward-compatibility-1dfa88683f3658dd::0008::a3188e171c84",
        ),
        notes="原文：Detecting in-flight threads 小节",
    ),
    GoldCase(
        case_id="release_cycles",
        corpus_language="en",
        question="LangChain 的大版本发布可能包含哪些变更？",
        gold_chunk_ids=(
            "langchain-versioning-d6ca742f3bf1e2e1::0005::fec173826b7a",
        ),
        notes="原文：Major releases 可能含破坏性 API 变更",
    ),
    GoldCase(
        case_id="mcp_load_resources",
        corpus_language="en",
        question="怎么按 URI 加载 MCP 的指定资源？",
        gold_chunk_ids=(
            "langchain-mcp-d3f73471540679d2::0026::43a55c737717",
        ),
        notes="原文：client.get_resources(server_name, uris=[...])",
    ),
    GoldCase(
        case_id="concurrent_tool_calls",
        corpus_language="en",
        question="agent 并发调用多个工具时，结果是怎么组织的？",
        gold_chunk_ids=(
            "langchain-tool-calling-afc9e747c263ada3::0010::6c938b30e393",
        ),
        notes="原文：toolCalls 数组承载并行调用",
    ),
    GoldCase(
        case_id="append_conversation_history",
        corpus_language="en",
        question="怎么往对话历史里追加消息？",
        gold_chunk_ids=(
            "langchain-messages-705df0325d95ae84::0012::4c2fdafd3211",
        ),
        notes="原文：SystemMessage / HumanMessage 组成 messages 列表",
    ),
    GoldCase(
        case_id="router_basic",
        corpus_language="en",
        question="路由器的基本实现是怎么分类并分发查询的？",
        gold_chunk_ids=(
            "langchain-router-e98aade76acab0ce::0003::54250ee3c22f",
        ),
        notes="原文：classify 后用 Command 分发到对应 agent",
    ),
    GoldCase(
        case_id="safety_model",
        corpus_language="en",
        question="怎么用另一个模型来做输出的安全性评估？",
        gold_chunk_ids=(
            "langchain-streaming-d9d5d3e90f735f15::0028::9125d2fe3cac",
        ),
        notes="原文：safety_model.bind_tools([ResponseSafety])",
    ),
    GoldCase(
        case_id="runtime_context_user_role",
        corpus_language="en",
        question="怎么从 runtime context 里读取用户角色和运行环境？",
        gold_chunk_ids=(
            "langchain-context-engineering-3a2410aed5839a15::0014::fb07361a0a24",
        ),
        notes="原文：request.runtime.context.user_role",
    ),
    GoldCase(
        case_id="non_reducer_last_wins",
        corpus_language="en",
        question="非 reducer 字段在两个 middleware 都写时，哪个值生效？",
        gold_chunk_ids=(
            "langchain-custom-68476a1536475eb2::0018::818cfd7e578f",
        ),
        notes="原文标题即结论：outermost value wins（最外层胜出）",
    ),
    GoldCase(
        case_id="subagent_input_state",
        corpus_language="en",
        question="子 agent 的输入 State 是怎么定义的？",
        gold_chunk_ids=(
            "langchain-router-knowledge-base-ab66531b2bc5c479::0036::1dcbad39d40f",
        ),
        notes="原文：class AgentInput(TypedDict)，含 query 字段",
    ),
    GoldCase(
        case_id="structure_output_typed_dict",
        corpus_language="en",
        question="结构化输出示例里用 TypedDict 定义了哪些个人信息字段？",
        gold_chunk_ids=(
            "langchain-structured-output-61971813b49081b6::0008::faeb66bc0c1f",
            "langchain-structured-output-61971813b49081b6::0006::590dc921e1d5",
        ),
        notes="原文：name / email / phone",
    ),
    GoldCase(
        case_id="inspect_raw_data",
        corpus_language="en",
        question="示例里演示用的原始特征数据存放在哪里？",
        gold_chunk_ids=(
            "doc-8b1201f96401::0010::054154564526",
        ),
        notes="原文：本地 parquet 文件",
    ),

    # ---- 第四批（补到 80+，覆盖 Milvus 文档那一半，2026-09-28）----
    GoldCase(
        case_id="pattern_matching_operators",
        corpus_language="en",
        question="Milvus 的模式匹配运算符用来做什么？",
        gold_chunk_ids=("doc-a28c5afbc666::0004::bc012640090c",),
        notes="原文：按通配符过滤字符串值",
    ),
    GoldCase(
        case_id="add_vector_field",
        corpus_language="en",
        question="外部数据源已经包含向量时，怎么再加一个向量字段？",
        gold_chunk_ids=("doc-4e284f892558::0004::ab99b1c3f7ae",),
        notes="原文：Add a vector field 小节",
    ),
    GoldCase(
        case_id="embed_pdf_file",
        corpus_language="en",
        question="示例里是怎么嵌入一个 PDF 文件的？",
        gold_chunk_ids=("doc-66e39b1d6b00::0013::253acbcaae3d",),
        notes="原文：embed_anything.embed_file(...)",
    ),
    GoldCase(
        case_id="client_and_connection_rule",
        corpus_language="en",
        question="连接 Milvus 时文档强调的那条硬性规则是什么？",
        gold_chunk_ids=("doc-7aeb8c57c902::0003::3ddc874e8e4b",),
        notes="原文：务必用 MilvusClient，不要用旧 ORM API",
    ),
    GoldCase(
        case_id="localstorage_config",
        corpus_language="en",
        question="localStorage.path 这个配置项是什么含义？",
        gold_chunk_ids=("doc-7c257c9e5339::0000::8251b02a6c90",),
        notes="原文：localStorage 配置表",
    ),
    GoldCase(
        case_id="filter_by_permission_bits",
        corpus_language="en",
        question="怎么按权限位（permission bits）过滤数据？",
        gold_chunk_ids=("doc-a28c5afbc666::0008::57c322306dad",),
        notes="原文：整数字段 permissions 的位运算过滤示例",
    ),
    GoldCase(
        case_id="cohere_ranker",
        corpus_language="en",
        question="Cohere Ranker 是什么，基于什么能力？",
        gold_chunk_ids=("doc-fb7e526a4cb6::0000::bc3dc3d206b9",),
        notes="原文：借助 Cohere 的 rerank 模型（2.6.x）",
    ),
    GoldCase(
        case_id="milvus_insight_tools",
        corpus_language="zh",
        question="Milvus 有哪些图形化工具和周边支持？",
        gold_chunk_ids=("milvus-overview-0e41733538565481::0007::6a4c9e975e44",),
        notes="原文（中文语料）：Milvus Insight 图形化管理工具，含集群状态可视化",
    ),
    GoldCase(
        case_id="zh_consistency_levels",
        corpus_language="zh",
        question="Milvus 提供哪几种一致性级别？",
        gold_chunk_ids=("milvus-release_notes-558b36d4a6590077::0005::d65ff92cd0a5",),
        notes="原文（中文语料）：强一致 / 有界一致 / 会话一致 / 前缀一致",
    ),
    GoldCase(
        case_id="zh_multi_consistency",
        corpus_language="zh",
        question="Milvus 2.0 为什么提供多种一致性，依据是什么？",
        gold_chunk_ids=("milvus-comparison-98fd1ad4323a3153::0006::5179055d5084",),
        notes="原文（中文语料）：基于消息存储的分布式库，遵循 PACELC 取舍",
    ),
    GoldCase(
        case_id="zh_access_layer",
        corpus_language="zh",
        question="Milvus 的接入层负责什么？",
        gold_chunk_ids=("milvus-architecture_overview-79b109857ee711dc::0002::57e1b74f0b6d",),
        notes="原文（中文语料）：暴露 endpoint、处理连接与请求校验",
    ),
    GoldCase(
        case_id="config_sections",
        corpus_language="en",
        question="Milvus 的配置为什么要分 sections？",
        gold_chunk_ids=("doc-f589cad4d47e::0001::15840c317749",),
        notes="原文：便于维护，按 section 归类配置",
    ),
    GoldCase(
        case_id="stop_nodes",
        corpus_language="en",
        question="不再需要 Milvus 集群时怎么停掉节点？",
        gold_chunk_ids=("doc-f1708a43c217::0025::6a3eff9cccd0",),
        notes="原文：Stop nodes 小节",
    ),
    GoldCase(
        case_id="crawl_website_to_milvus",
        corpus_language="en",
        question="示例是用什么工具抓取 Milvus.io 网页内容再存进向量库的？",
        gold_chunk_ids=("doc-608bf00072bb::0004::7b99192e3d9b",),
        notes="原文：Website Content Crawler",
    ),
    GoldCase(
        case_id="segment_overview",
        corpus_language="en",
        question="Milvus 是怎么存放写入的实体的（segment 相关）？",
        gold_chunk_ids=("doc-b0318649cee6::0001::6ee01b1ab11e",),
        notes="原文：实体存在 segment 里，segment 会被 seal",
    ),
    GoldCase(
        case_id="milvus_cli_reference",
        corpus_language="en",
        question="Milvus_CLI 是什么？",
        gold_chunk_ids=("doc-de158425cec6::0000::4c9a052bed23",),
        notes="原文：命令行工具 Command Reference",
    ),
    GoldCase(
        case_id="alter_varchar_field",
        corpus_language="en",
        question="VarChar 字段的哪个属性可以改，它约束什么？",
        gold_chunk_ids=("doc-ee0ad9d1781b::0001::3b1bd9de84ca",),
        notes="原文：max_length 约束最大长度",
    ),
    GoldCase(
        case_id="insert_data_into_milvus",
        corpus_language="en",
        question="怎么把数据插入 Milvus 集合（示例代码）？",
        gold_chunk_ids=("doc-b1c65d6b7321::0012::67892ef7482c",),
        notes="原文：milvus_client.insert(collection_name=..., data=data)",
    ),
    GoldCase(
        case_id="indexcoord_config",
        corpus_language="en",
        question="indexCoord 相关配置项在哪里查？",
        gold_chunk_ids=("doc-05a7e7b4a6d6::0000::c8ac7a9d89d7",),
        notes="原文：indexCoord-related Configurations",
    ),
    GoldCase(
        case_id="switchover_usage",
        corpus_language="en",
        question="什么时候该用 Switchover？",
        gold_chunk_ids=("doc-05e5f888e976::0001::2062e5b0199b",),
        notes="原文：When to Use Switchover 小节",
    ),
    GoldCase(
        case_id="aisaq_how_it_works",
        corpus_language="en",
        question="AISAQ 是怎么工作的？",
        gold_chunk_ids=("doc-04eb2fd88296::0001::65ef95dbe649",),
        notes="原文：How AISAQ works 小节",
    ),
    GoldCase(
        case_id="aisaq_modes",
        corpus_language="en",
        question="AISAQ 有哪两种运行模式？",
        gold_chunk_ids=("doc-04eb2fd88296::0003::8f41b2761bd1",),
        notes="原文：performance mode 与 scale mode",
    ),
    GoldCase(
        case_id="azure_openai_credentials",
        corpus_language="en",
        question="让 Milvus 请求 Azure OpenAI 嵌入前要做什么？",
        gold_chunk_ids=("doc-22201fc63b96::0002::c9fb2b5ce0c3",),
        notes="原文：Configure credentials，配置 API key",
    ),
    GoldCase(
        case_id="minio_formatter_config",
        corpus_language="en",
        question="配置 MinIO 参数时要注意什么？",
        gold_chunk_ids=("doc-fc5e2c8c4b28::0005::27e5e1706f26",),
        notes="原文：Additional formatter configurations 小节",
    ),
    GoldCase(
        case_id="llm_output_quality_eval",
        corpus_language="en",
        question="AIMon 提供了哪些用于评估 LLM 输出质量的判定模型？",
        gold_chunk_ids=("doc-f01cb12959ab::0003::8ba2d56273b9",),
        notes="原文：Hallucination、Context 等 Judge 模型",
    ),
    GoldCase(
        case_id="blob_storage_workload_identity",
        corpus_language="en",
        question="怎么用 Workload Identity 配置 Blob 存储访问？",
        gold_chunk_ids=("doc-02af712d1cca::0000::b8736ee04ba3",),
        notes="原文：Configure Blob Storage Access by Workload Identity",
    ),
    GoldCase(
        case_id="provision_k8s_cluster",
        corpus_language="en",
        question="部署 Milvus 前，怎么准备 Kubernetes 集群？",
        gold_chunk_ids=("doc-034dd66c30f3::0003::28231172e12b",),
        notes="原文：Provision a Kubernetes cluster 小节",
    ),
    GoldCase(
        case_id="structarray_doc_map",
        corpus_language="en",
        question="StructArray 的文档是怎么组织的？",
        gold_chunk_ids=("doc-a175754bab82::0008::972b47d3b5d9",),
        notes="原文：分 modeling 页与 search 页",
    ),
    GoldCase(
        case_id="insert_csv_cli",
        corpus_language="en",
        question="怎么用 Milvus CLI 从 CSV 文件导入数据？",
        gold_chunk_ids=("doc-de158425cec6::0033::e52063c3aabf",),
        notes="原文：insert file -c car 'examples/import_csv/vectors.csv'",
    ),
    GoldCase(
        case_id="retriever_namespace_filter",
        corpus_language="en",
        question="怎么用 namespace 表达式让 retriever 只取某个用户的文档？",
        gold_chunk_ids=("doc-21428cbc94a9::0012::e6f1218bf013",),
        notes="原文：search_kwargs={'expr': 'namespace == ...'}",
    ),
    GoldCase(
        case_id="diskann_recap",
        corpus_language="en",
        question="AISAQ 的基础算法 DISKANN 是什么？",
        gold_chunk_ids=("doc-04eb2fd88296::0002::a9a36c581250",),
        notes="原文：Foundation: DISKANN recap",
    ),

    GoldCase(
        case_id="zh_arch_layers_principle",
        question="Milvus 整体架构遵循什么分离原则，分成哪四个层次？",
        gold_chunk_ids=("milvus-architecture_overview-79b109857ee711dc::0001::001e3a17391c",),
        corpus_language="zh",
        notes="原文（中文语料）：遵循数据流和控制流分离的原则，分接入层/协调服务/执行层/存储层，各层独立扩展和容灾",
    ),
    GoldCase(
        case_id="zh_root_coord_tso",
        question="Root coordinator 除了 DDL/DCL 还要维护什么？",
        gold_chunk_ids=("milvus-architecture_overview-79b109857ee711dc::0003::a0df17e0c50d",),
        corpus_language="zh",
        notes="原文（中文语料）：负责创建删除 collection/partition/index，并维护中心授时服务 TSO 和时间窗口推进",
    ),
    GoldCase(
        case_id="zh_worker_stateless_scaling",
        question="执行节点为什么是无状态的，靠什么做扩缩容和故障恢复？",
        gold_chunk_ids=("milvus-architecture_overview-79b109857ee711dc::0004::7dcdf4334039",),
        corpus_language="zh",
        notes="原文（中文语料）：采取存储计算分离所以执行节点无状态，可配合 Kubernetes 快速实现扩缩容和故障恢复",
    ),
    GoldCase(
        case_id="zh_meta_store_etcd_choice",
        question="元数据存储为什么选 etcd，它还顺带承担了什么？",
        gold_chunk_ids=("milvus-architecture_overview-79b109857ee711dc::0005::13499cda898c",),
        corpus_language="zh",
        notes="原文（中文语料）：需要极高可用、强一致和事务支持，etcd 是不二选择；还承担服务注册和健康检查",
    ),
    GoldCase(
        case_id="zh_standalone_three_components",
        question="单机版 Milvus 由哪三个组件构成？",
        gold_chunk_ids=("milvus-architecture_overview-79b109857ee711dc::0006::de447f0f69d0",),
        corpus_language="zh",
        notes="原文（中文语料）：Milvus + etcd（元数据引擎）+ MinIO（存储引擎）；分布式版是 8 个微服务 + 3 个第三方组件",
    ),
    GoldCase(
        case_id="zh_log_as_data_no_physical_table",
        question="Milvus 2.0 为什么没有维护物理上的表？",
        gold_chunk_ids=("milvus-architecture_overview-79b109857ee711dc::0007::47bd57aa1394",),
        corpus_language="zh",
        notes="原文（中文语料）：围绕日志为核心设计，日志即数据，靠日志持久化与快照保证可靠性",
    ),
    GoldCase(
        case_id="zh_shard_routing_by_pk_hash",
        question="增删请求进入哪个 shard 是由谁、按什么决定的？",
        gold_chunk_ids=(
            "milvus-architecture_overview-79b109857ee711dc::0008::b5d191a16135",
            "milvus-glossary-40fbbadefba6dabe::0001::c5f21844ad52",
        ),
        corpus_language="zh",
        notes="原文（中文语料）：由 proxy 决定，目前基于主键哈希；术语表：默认单 collection 2 个分片，采用基于主键哈希的分片",
    ),
    GoldCase(
        case_id="zh_segment_index_reason",
        question="为什么要把 collection 切成 segment 来建索引？",
        gold_chunk_ids=("milvus-architecture_overview-79b109857ee711dc::0009::82589f76f6dc",),
        corpus_language="zh",
        notes="原文（中文语料）：为了避免数据更新导致的索引频繁重复构建，把 collection 分成更小粒度，每个 segment 独立索引",
    ),
    GoldCase(
        case_id="zh_growing_sealed_handoff",
        question="growing segment 什么时候会被 seal，之后走什么流程？",
        gold_chunk_ids=("milvus-architecture_overview-79b109857ee711dc::0010::728b9e26dff6",),
        corpus_language="zh",
        notes="原文（中文语料）：数据量到固定阈值由 data coord 发起 seal，sealed 后建索引并触发 query coord 的 handoff",
    ),
    GoldCase(
        case_id="zh_batch_stream_watermark",
        question="Milvus 的批流一体用什么架构，watermark 起什么作用？",
        gold_chunk_ids=("milvus-comparison-98fd1ad4323a3153::0002::3b0fc957a6f6",),
        corpus_language="zh",
        notes="原文（中文语料）：unified Lambda 流式处理架构；用 watermark 把无界流按写入时间切成 message pack 并保留时间轴",
    ),
    GoldCase(
        case_id="zh_fail_cheap_small_often",
        question="Fail cheap / fail small / fail often 分别指什么？",
        gold_chunk_ids=("milvus-comparison-98fd1ad4323a3153::0004::13d44f04fa2e",),
        corpus_language="zh",
        notes="原文（中文语料）：存算分离使节点失败恢复代价低；每个协调服务只管一部分；混沌测试做故障注入",
    ),
    GoldCase(
        case_id="zh_orm_api_poc_gap",
        question="Milvus ORM API 是为了补上哪一段缺口？",
        gold_chunk_ids=("milvus-comparison-98fd1ad4323a3153::0008::cb0cd17ed8df",),
        corpus_language="zh",
        notes="原文（中文语料）：弥补 AI 概念验证到实际生产部署之间的缺口，背后实现可以是库/单机/集群/云服务",
    ),
    GoldCase(
        case_id="zh_20_vs_1x_node_counts",
        question="Milvus 2.0 与 1.x 在可扩展性上分别是多少个节点？",
        gold_chunk_ids=("milvus-comparison-98fd1ad4323a3153::0010::2623ab56075d",),
        corpus_language="zh",
        notes="原文（中文语料）：原文对比表：2.0 为 500+ 个节点；1.x 为 1-32 个读节点、1 个写节点",
    ),
    GoldCase(
        case_id="zh_annns_four_categories",
        question="ANNS 向量索引按实现方式分成哪四大类？",
        gold_chunk_ids=("milvus-index-7126bd4add2e698d::0001::6680b8c236fc",),
        corpus_language="zh",
        notes="原文（中文语料）：基于树/基于图/基于哈希/基于量化四类，ANNS 以可接受的精度损失换检索效率",
    ),
    GoldCase(
        case_id="zh_index_paused_by_query",
        question="查询和后台建索引撞在一起时 Milvus 怎么排优先级？",
        gold_chunk_ids=("milvus-index-7126bd4add2e698d::0001::6680b8c236fc",),
        corpus_language="zh",
        notes="原文（中文语料）：计算资源优先给查询，查询会打断后台建索引，持续 5 秒无查询才恢复；未建索引的段直接全量搜",
    ),
    GoldCase(
        case_id="zh_flat_effective_when_few",
        question="FLAT 索引提供多少召回率，什么时候它反而最有效？",
        gold_chunk_ids=("milvus-index-7126bd4add2e698d::0002::193da0eafc85",),
        corpus_language="zh",
        notes="原文（中文语料）：FLAT 提供 100% 检索召回率，查询数量较少时是最有效的索引方法",
    ),
    GoldCase(
        case_id="zh_ivf_flat_probe_mechanism",
        question="IVF_FLAT 查询时 nlist 与 nprobe 各自起什么作用？",
        gold_chunk_ids=(
            "milvus-index-7126bd4add2e698d::0003::c33bae6fee40",
            "milvus-product_faq-09632a9c1b4ee6d6::0008::dd7bba677af2",
            "milvus-product_faq-09632a9c1b4ee6d6::0010::42f23e0f60d8",
        ),
        corpus_language="zh",
        notes="原文（中文语料）：三片各自独立回答：先比 nlist 个单元中心，取最近 nprobe 个单元再比单元内向量（两两重合度均低于 0.25，不是重复切片）",
    ),
    GoldCase(
        case_id="zh_hnsw_param_ranges",
        question="HNSW 的 M、efConstruction、ef 三个参数的取值范围分别是多少？",
        gold_chunk_ids=("milvus-index-7126bd4add2e698d::0006::aaa675916f69",),
        corpus_language="zh",
        notes="原文（中文语料）：M [4,64]、efConstruction [8,512]、ef [top_k, 32768]",
    ),
    GoldCase(
        case_id="zh_annoy_search_k_minus_one",
        question="ANNOY 的 search_k 设成 -1 是什么含义？",
        gold_chunk_ids=("milvus-index-7126bd4add2e698d::0007::dd3f8a68dcd0",),
        corpus_language="zh",
        notes="原文（中文语料）：search_k 为 -1 表示用全数据量的 5%，取值范围 {-1} 并上 [top_k, n × n_trees]",
    ),
    GoldCase(
        case_id="zh_cpu_simd_requirement_check",
        question="跑 Milvus 的 CPU 必须支持哪种指令集，怎么检查？",
        gold_chunk_ids=(
            "milvus-install_cluster-docker-1a69b586957ea00f::0002::02feedf94cfe",
            "milvus-install_standalone-docker-650f4716c1dc4ef2::0002::c134e7ffcc53",
        ),
        corpus_language="zh",
        notes="原文（中文语料）：真重复对（4-gram 重合度 0.824）：SSE4.2/AVX/AVX2/AVX512 至少一种，用 lscpu grep 检查",
    ),
    GoldCase(
        case_id="zh_helm_min_versions",
        question="用 Helm 装 Milvus 对 Kubernetes 和 Helm 的最低版本要求是多少？",
        gold_chunk_ids=("milvus-install_cluster-helm-2e6e96b0e5f5691a::0002::550cbc8702f2",),
        corpus_language="zh",
        notes="原文（中文语料）：Kubernetes 1.14.0 或以上、Helm 3.0.0 或以上，另需 minikube",
    ),
    GoldCase(
        case_id="zh_binary_vector_distances",
        question="二值型向量可以用哪些距离计算方式？",
        gold_chunk_ids=("milvus-metric-523644bd47a1ee48::0001::b1221092dd37",),
        corpus_language="zh",
        notes="原文（中文语料）：原文表格：杰卡德/谷本/汉明配 FLAT、IVF_FLAT；超结构/子结构只配 FLAT",
    ),
    GoldCase(
        case_id="zh_recall_main_factors",
        question="召回率主要受哪些因素影响？",
        gold_chunk_ids=("milvus-operational_faq-d41df130d639bd11::0002::7bc987f9e38b",),
        corpus_language="zh",
        notes="原文（中文语料）：主要受索引类型与查询参数影响，IVF 看 nprobe、HNSW 看 ef，FLAT 全量搜为 100%",
    ),
    GoldCase(
        case_id="zh_container_counts_ready",
        question="怎么从 Docker 容器数量判断 Milvus 起没起，单机和分布式分别要几个？",
        gold_chunk_ids=("milvus-operational_faq-d41df130d639bd11::0003::a7697b746b8a",),
        corpus_language="zh",
        notes="原文（中文语料）：单机版至少 3 个容器（1 Milvus + 2 etcd/存储），分布式至少 12 个（9 服务 + 3 基础）",
    ),
    GoldCase(
        case_id="zh_illegal_instruction_cause",
        question="Milvus 启动时返回 Illegal instruction 说明什么？",
        gold_chunk_ids=("milvus-operational_faq-d41df130d639bd11::0004::2cea03841628",),
        corpus_language="zh",
        notes="原文（中文语料）：说明当前 CPU 不支持 SSE4.2/AVX/AVX2/AVX512 中任何一种指令集",
    ),
    GoldCase(
        case_id="zh_source_build_supported_os",
        question="哪些系统能从源码编译部署 Milvus，Windows 行不行？",
        gold_chunk_ids=("milvus-operational_faq-d41df130d639bd11::0005::ab44ac877188",),
        corpus_language="zh",
        notes="原文（中文语料）：仅支持 Ubuntu 18.04 或以上或 CentOS 7 或以上，暂不支持 Windows/macOS 源码编译",
    ),
    GoldCase(
        case_id="zh_hybrid_query_types",
        question="Milvus 的混合查询除了向量还支持哪些数据类型？",
        gold_chunk_ids=("milvus-overview-0e41733538565481::0004::48caec8d041c",),
        corpus_language="zh",
        notes="原文（中文语料）：支持布尔值、整型、浮点等类型，一个 collection 可有多个字段，并支持标量字段过滤",
    ),
    GoldCase(
        case_id="zh_ecommerce_scenarios",
        question="电商场景里 Milvus 具体能做什么？",
        gold_chunk_ids=("milvus-overview-0e41733538565481::0005::3b1e0aff706b",),
        corpus_language="zh",
        notes="原文（中文语料）：以图搜图、以商品搜商品、个性化推荐、内容推荐、商品去重",
    ),
    GoldCase(
        case_id="zh_unstructured_data_ratio",
        question="非结构化数据占全部数据的比例是多少，怎么进入数据库？",
        gold_chunk_ids=("milvus-overview-0e41733538565481::0006::e7a6f5cc096d",),
        corpus_language="zh",
        notes="原文（中文语料）：图片/视频/音频/自然语言等占所有数据总量的 80%，通过 AI 或 ML 模型转化为向量",
    ),
    GoldCase(
        case_id="zh_query_cpu_utilization",
        question="向量查询时 CPU 利用率受哪些参数影响？",
        gold_chunk_ids=("milvus-performance_faq-f258c4feec35f8ec::0004::ac1c064c7170",),
        corpus_language="zh",
        notes="原文（中文语料）：受 nq 与 nprobe 影响，两者都小时并发度小、CPU 利用率不高；ANNOY 是单线程",
    ),
    GoldCase(
        case_id="zh_milvus_licensing",
        question="Milvus 会收费吗，遵循什么协议？",
        gold_chunk_ids=("milvus-product_faq-09632a9c1b4ee6d6::0001::010f2323ba9d",),
        corpus_language="zh",
        notes="原文（中文语料）：坚持开源路线，软件本身不收费用，遵循 Apache 2.0；不想自维护可用 Zilliz Cloud 按服务付费",
    ),
    GoldCase(
        case_id="zh_user_data_vs_metadata_store",
        question="用户插入的数据和元数据分别存在哪里？",
        gold_chunk_ids=("milvus-product_faq-09632a9c1b4ee6d6::0002::0f4fef5cf164",),
        corpus_language="zh",
        notes="原文（中文语料）：用户数据以增量日志写入持久化存储（当前仅支持 MinIO），各模块元数据存 etcd",
    ),
    GoldCase(
        case_id="zh_insert_while_query_supported",
        question="Milvus 支持边插入边查询吗，靠什么做到？",
        gold_chunk_ids=("milvus-product_faq-09632a9c1b4ee6d6::0003::b9d8c0b63801",),
        corpus_language="zh",
        notes="原文（中文语料）：插入与查询由两个相互独立的模块分开执行；数据进消息队列即算插入结束，只有加载到 query node 才可查",
    ),
    GoldCase(
        case_id="zh_duplicate_id_not_update",
        question="插入重复 ID 的向量，Milvus 会当成数据更新吗？",
        gold_chunk_ids=("milvus-product_faq-09632a9c1b4ee6d6::0004::026b3c154bd6",),
        corpus_language="zh",
        notes="原文（中文语料）：不会，暂不支持向量数据更新且不检查 entity ID 唯一性，可能多条 entity 共用一个 ID",
    ),
    GoldCase(
        case_id="zh_flush_not_persisted",
        question="插入返回成功时数据已经落盘了吗？",
        gold_chunk_ids=("milvus-product_faq-09632a9c1b4ee6d6::0009::1ce3ca49364a",),
        corpus_language="zh",
        notes="原文（中文语料）：写入消息队列即返回插入成功但并未落盘，由 data node 写持久化存储，flush 可强制立刻落盘",
    ),
    GoldCase(
        case_id="zh_normalization_definition",
        question="什么是归一化，为什么用点积算相似度时必须归一化？",
        gold_chunk_ids=(
            "milvus-glossary-40fbbadefba6dabe::0001::c5f21844ad52",
            "milvus-metric-523644bd47a1ee48::0001::b1221092dd37",
            "milvus-product_faq-09632a9c1b4ee6d6::0009::1ce3ca49364a",
        ),
        corpus_language="zh",
        notes="原文（中文语料）：三片各自独立回答：模长变为 1；用点积必须归一化，处理后点积与余弦等价（两两重合度低于 0.25）",
    ),
    GoldCase(
        case_id="zh_topk_shortfall_fix",
        question="搜 topk 条却召回不足 k 条，原因和解决办法是什么？",
        gold_chunk_ids=("milvus-product_faq-09632a9c1b4ee6d6::0010::42f23e0f60d8",),
        corpus_language="zh",
        notes="原文（中文语料）：nlist 与 topk 大而 nprobe 过小时，选中单元内向量总数可能不足 k；调大 nprobe 或调小 nlist/topk",
    ),
    GoldCase(
        case_id="zh_no_upgrade_from_1x",
        question="能从 Milvus 1.x 直接升级到 2.0 吗，为什么？",
        gold_chunk_ids=("milvus-release_notes-558b36d4a6590077::0006::275dae36bacf",),
        corpus_language="zh",
        notes="原文（中文语料）：编程语言、数据格式、分布式架构都完全不同，不能升级到 2.x；1.x 为 LTS，迁移工具后续上线",
    ),
    GoldCase(
        case_id="zh_roadmap_21_features",
        question="Milvus 2.1 计划支持哪些数据操纵能力？",
        gold_chunk_ids=("milvus-roadmap-41ea1a09363029d1::0001::ed646be81086",),
        corpus_language="zh",
        notes="原文（中文语料）：支持向量删除与更新、字符串与 varbinary 类型、基于距离的向量搜索、segment 整理、集群负载均衡",
    ),
    GoldCase(
        case_id="zh_troubleshooting_categories",
        question="故障诊断问题分成哪几类？",
        gold_chunk_ids=("milvus-troubleshooting-6d58c67352d14bad::0001::0fe1dfb70f95",),
        corpus_language="zh",
        notes="原文（中文语料）：服务启动问题 / 服务运行问题 / API 问题三类，启动故障可看 docker logs",
    ),
    # ---- 不可答用例：语料里没有答案，用来看检索噪声 ----
    GoldCase(
        case_id="unanswerable_pizza",
        question="烤披萨时烤箱要预热到多少度？",
        unanswerable=True,
        notes="语料是 Milvus / LangChain 文档，不涉及烹饪",
    ),
    GoldCase(
        case_id="unanswerable_worldcup",
        question="2026 年世界杯的冠军是哪支球队？",
        unanswerable=True,
        notes="语料不涉及体育赛事",
    ),
    GoldCase(
        case_id="unanswerable_leave",
        question="公司公积金的缴纳比例是多少，由谁承担？",
        unanswerable=True,
        notes="语料不涉及社保公积金（实测 text like 查询命中 0 条）。原问题是「公司年假的申请流程…谁审批」，而库里就有写着年假制度的中文演示切片，那条问题其实有答案，不能当不可答用例用",
    ),
)


def validate_against_ids(known: set[str]) -> list[str]:
    """返回当前集合里**找不到**的 gold chunk_id（空列表 = 标注仍有效）。"""
    missing: list[str] = []
    for case in PRODUCTION_GOLD:
        for gold_id in case.gold_chunk_ids:
            if gold_id not in known:
                missing.append(f"{case.case_id}:{gold_id}")
    return missing
