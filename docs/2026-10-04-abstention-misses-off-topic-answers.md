# F1.2 基线的核心发现：弃权门拦不住「答非所问」，因为 L3 拿不到问题

日期：2026-10-04
关联：`docs/2026-10-04-f12-answer-baseline.md`（F1.2 基线）、
`docs/2026-10-04-f13-abstention-threshold-calibration.md`（F1.3 阈值）
计划：`docs/plans/2026-09-29-rag4c-master-plan.md`

## 一句话

**幻觉率 0.6364 的主因不是生成模型编造，而是弃权门缺少一个判断维度：
「这段证据是否回答了用户问的那个事实」。而负责这个判断的 L3 蕴含判定，
它的提示词里连用户的问题都没有。**

## 一、证据链

### 1. 基线数据指向"答非所问"而非"编造"

14 条不可答问题被放行，它们的裁判分数是：

| 子集 | avg_groundedness | avg_relevance |
|---|---|---|
| 可答（123 条） | 0.8411 | 0.8953 |
| **不可答但作答（14 条）** | **0.7408** | **0.2143** |

其中 **11 条（79%）的 relevance 恰好是 0.00**，而它们的 groundedness
在 0.71~1.00 之间。逐条看：

| 用例 | 有据性 | 相关性 | 问题 |
|---|---|---|---|
| `unanswerable_ondomain_03` | 1.00 | **0.00** | Milvus 的 GraphQL 接口怎么查询集合列表？ |
| `unanswerable_ondomain_05` | 1.00 | **0.00** | Milvus 里两个集合之间怎么做类似 SQL JOIN？ |
| `unanswerable_ondomain_08` | 1.00 | **0.00** | LangGraph 用 Cassandra 做 checkpointer |
| `unanswerable_ondomain_16` | 0.91 | **0.00** | Spring Boot 整合 Kafka 怎么配 exactly-once？ |

系统检索到 Milvus / Spring Boot / LangGraph 的**正确文档**，基于它生成了
**确实有据**的回答——只是那段回答没回答用户问的那个具体事实。

**所以 `avg_groundedness = 0.83` 这个漂亮的数字，完全掩盖了 0.636 的幻觉率。**
有据性与幻觉率是两个正交维度，不能互相替代。

### 2. F1.3 独立验证了同一个结论

F1.3 在**只看检索分数**的 145 条分布上算出：同域硬负样本的 top1 分数
0.78~0.89，**天然落在可答族的中位区间**（可答族 p50 = 0.975、p10 = 0.720）。
两族分布在 p10~p90 上重叠——**信号不足，不是阈值没调好**。

F1.3 当时的结论是"解法方向是让 L3 承担同域硬负样本"。本文给出它的具体形式。

### 3. 根因：L3 的提示词里没有用户的问题

`verify/verifier.py:559` 构造 L3 提示词：

```python
prompt = (
    template.replace("{claims}", claims_block).replace("{evidence}", evidence_block)
)
messages = [{"role": "user", "content": prompt}]
```

**只替换了 `{claims}` 与 `{evidence}`。** 模板
`prompts/judge_groundedness_v1.txt` 里也只有这两个占位符——
`eval/judges.py:149` 那一行注释写得很直白：

```python
del question  # 有据性模板不包含问题占位符
```

所以 L3 看到的是「这些声明 + 这些证据」，**完全不知道用户在问什么**。
它能回答"这段声明能否由这段证据推出"，但**在原理上不可能**回答
"这段证据是否回答了用户的问题"。

## 二、这解释了三件事

1. **为什么幻觉率与有据性反向**：有据性高说明"证据支持声明"，
   而幻觉的定义是"证据不支持用户的问题"。两者测的不是一回事。
2. **为什么 F1.3 调阈值只能到 0.09**：把检索阈值提到 0.85 能拦住高分段的
   同域硬负样本，但剩下的（分数与可答族重叠的那些）**仍然会答非所问**。
3. **为什么 F1.2 的相关性裁判能识别它**：相关性模板**含 `{question}`**，
   它拿得到用户问题，所以给了 0.00。

## 三、提案（需要确认后才实施）

### 改法

给 L3 一份**新的专用模板** `prompts/judge_answer_relevance_v2.txt`，
加一个维度：`answer_status ∈ {answered, topic_only, unsupported}`：

- `answered`：证据直接回答了用户问的那个事实；
- `topic_only`：证据主题正确但**不包含**用户问的那个具体事实
  （"讲的是 Milvus，但没讲 GraphQL 接口"）；
- `unsupported`：证据与问题无关。

`topic_only` 应当让弃权门拒答。`verify/verifier.py` 需要把 `question`
传进 `_judge_claims`（当前签名里没有），并把 `topic_only` 映射成
低于 `entailment_threshold` 的分数。

### 为什么这是"升版本"而不是改提示词

计划明确写着：**"提示词行为性修改必须升版本"**（F1.4 验收）。
`judge_groundedness_v1.txt` 被两个地方共用：

- `eval/judges.py` 的 `GroundednessJudge`（评测侧）
- `verify/verifier.py` 的 `CitationVerifier`（生产侧 L3）

**直接改它会同时改变评测口径与生产行为**，让 F1.2 的基线失去可比性
（基线是按 v1 模板跑的）。所以正确做法是**新增 v2 模板给 L3 用**，
v1 保持不动供评测侧继续用。

### 需要用户确认的点

1. **是否要做这个改动**。它改的是弃权门的行为，直接影响"答得多还是答得少"
   这个产品取舍——和 F1.3 的阈值决策一样，不该由我单方面定。
2. 如果做，**弃权门是否接受一个新的判定维度**（`topic_only`）。
   这会让弃权率上升（当前 0.0897），是设计上的必然结果。

### 我建议的顺序（不依赖上述确认）

1. **先压降级率**（0.1034 → ≤0.05）。9 条 `entailment_unavailable` 意味着
   引用校验根本没跑过、弃权门拿不到第二道闸——**在这个状态下讨论怎么加强
   第二道闸是没有意义的**。今天已发现 120s 超时 + 思考链会让上游调用反复
   重试（`ebdc64b`），这很可能就是 9 条 L3 失败的成因。
2. **先做 F1.4**（裁判可信度）。在裁判本身是否可信都不知道时改判定逻辑，
   是拿一个未验证的基线去换另一个。
3. **再动弃权门**。

## 四、如何验证这个提案有效

改完之后，判据很明确：**幻觉率应下降而 avg_groundedness 基本不动**。
如果两个数一起掉，说明新模板把"真的答错了"也判成了 `topic_only`——
那就是把门槛提高了而不是把判断做准了。

当前基线作为对照：

```
refusal_rate 0.0897   over_refusal_rate 0.0407   hallucination_rate 0.6364
avg_groundedness 0.8298   avg_relevance 0.8225   citation_failure_rate 0.2471
degraded_rate 0.1034
```

## 五、降级率成因的排查进展（未定位，如实记录）

F1.2 基线里 `degraded_rate = 0.1034`（15 条），其中 `entailment_unavailable`
9 条。降级率不达标的直接后果是：**这 9 条的引用校验根本没跑过，弃权门拿不到
第二道闸**，于是它们全部按"检索通过就作答"处理——这本身就推高幻觉率。

排查了两个假设，**都否掉了**：

### 假设 1：3 并发触发上游限流 —— **证伪**

基线是 `--workers 3` 跑的，而 F2.4/F1.2 都记录了硅基流动有 429 限流。
直接对照实验（`eval/.cache/probe_l3_concurrency.py`，同一请求形状）：

| 并发 | 成功率 | p50 | 墙钟（6 条） |
|---|---|---|---|
| 1 | 6/6 (100%) | 14.4s | 81.2s |
| 3 | 6/6 (100%) | **9.1s** | 19.4s |

并发不但没提高失败率，p50 还**更快**（服务端并发处理，摊薄了排队）。
限流不是成因。

### 假设 2：失败样本的输入规模特殊 —— **证伪**

降级 9 条的引用数：`[6, 4, 12, 6, 9, 11, 6, 21, 7]`（中位 7）；
正常作答 123 条：中位 11、p90 26、**最大 45**。

降级样本的规模**完全落在正常范围内**，没有任何一条超过正常样本的最大值。
"引用太多把 prompt 顶爆"（`verify/verifier.py:552` 注释里记过这个失败形态）
不成立。

### 当前的配置事实

```
verify.entailment_mode   = llm
verify.strict            = True        # L3 评全部引用，不抽样
verify.sample_ratio      = 1.0
pipeline.entailment_threshold = 0.6
```

### 还没排除的可能

1. **长答案的真实声明切分结果**。探针用的是固定的 5 条短声明，而真实失败样本
   可能有 20+ 条长声明、切分边界诡异。`verify/claims.split_claims` 的输出
   直接进 prompt——声明里如果含大量换行/引号，可能破坏 prompt 结构。
2. **JSON 解析**。`chat_json` 走 `json_mode=True`（`response_format`），
   硅基流动在 `59d6826` 修过 system 消息位置之后能正常工作，但长输出下仍可能
   截断导致解析失败——而 `ParseFallbackError` 在 L3 里被 catch 成
   `entailment_unavailable`。
3. **证据里的特殊字符**。证据块是原文直插，长文档里若含 `{}`（与模板占位符
   冲突）或超长未截断内容，也可能改变 prompt。

### 下一步（需要真跑一次带诊断的基线）

**给 L3 失败路径补上诊断信息**再跑一轮基线。当前
`verifier.py:511` 的 note 只记了 `f"L3 判定失败（降级：蕴含不可用...）: {exc}"`，
而这个 `exc` 在报告里**看不到**（F1.2 的 `degraded_kinds` 只区分到
`entailment_unavailable` 这一级）。

最小改动：在 L3 的 except 分支里把异常类型 + `repr(exc)` 前若干字符 + 当时的
声明数/证据数写进 `notes`，并让 `QueryResult.traces` 带上它（traces 已经在
`rag.py:375` 汇入结果）。这样下一轮基线能直接读出失败原因，不必再猜。

**这个诊断本身不需要用户确认**（只加可观测性、不改行为），是下一步该做的事。
