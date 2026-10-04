# F1.2 幻觉率治理：给 L3 加「答案相关性」维度

日期：2026-10-04
关联：`docs/2026-10-04-f12-answer-baseline.md`（基线）、
`docs/2026-10-04-abstention-misses-off-topic-answers.md`（根因诊断）
改动提交：`7f7cc3d`（v2 模板）、`209c36e`（门独立判定）、`12e51d2`（诊断可观测性）

## 一句话

**F1.2 基线的幻觉率 0.6364 不是"生成模型编造"，而是"弃权门缺少一个判断维度"。
负责这个判断的 L3 蕴含判定，它的提示词里连用户的问题都没有。** 本次给 L3
加 `answer_status` 维度并让弃权门独立判定它，端到端冒烟已验证有效。

## 一、基线与病灶

| 指标 | v1 基线 | 门禁 |
|---|---|---|
| 幻觉率 | **0.6364** | FAIL（阈值 0.10） |
| 过度拒答率 | 0.0407 | PASS |
| 平均有据性 | 0.8298 | PASS |
| 降级率 | 0.1034 | FAIL |

**14 条幻觉样本的裁判分数：有据性 0.74、相关性 0.21，其中 11 条（79%）的
相关性恰好 0.00。** 逐条看：

```
unanswerable_ondomain_03  g=1.00 r=0.00  Milvus 的 GraphQL 接口怎么查询集合列表？
unanswerable_ondomain_05  g=1.00 r=0.00  Milvus 里两个集合之间怎么做类似 SQL JOIN？
unanswerable_ondomain_08  g=1.00 r=0.00  LangGraph 用 Cassandra 做 checkpointer
```

**不是编造，是答非所问**：检索到正确主题的文档、生成了确实有据的回答，
只是没回答用户问的那个具体事实。`avg_groundedness = 0.83` 这个漂亮的数字
**完全掩盖了 0.636 的幻觉率**——有据性与幻觉率是两个正交维度。

## 二、根因与改动

### 根因

`verify/verifier.py` 构造 L3 提示词时只替换 `{claims}` 与 `{evidence}`；
模板 `prompts/judge_groundedness_v1.txt` 也只有这两个占位符
（`eval/judges.py:149` 的注释直说「有据性模板不包含问题占位符」）。

**L3 在原理上不可能判定「证据是否回答了用户的问题」。** 它看到的是
"这些声明 + 这些证据"，能回答"声明能否由证据推出"，但看不到用户问什么。

### 改动一：v2 模板（`7f7cc3d`）

新增 `prompts/judge_answer_relevance_v2.txt`：

- 加 `{question}` 占位符；
- 加 `answer_status ∈ {answered, topic_only, irrelevant}` 维度；
- 模板头部写明 F1.2 数据与判定要点：「**主题相关不等于回答了问题**」。
  一个包含大量正确 Milvus 内容的文档，对"GraphQL 接口怎么查集合列表"
  仍然是 `topic_only`——而那正是 79% 的幻觉样本落入的区间。

分数映射：

| answer_status | 分数 | 引用状态 | 含义 |
|---|---|---|---|
| `answered` | 不改分 | 不改 | 证据直接回答了那个事实 |
| `topic_only` | ≤ 0.3 | `exists_only` | 主题相关但不包含所问事实——**安全，只是没用** |
| `irrelevant` | ≤ 0.0 | `exists_only` | 与问题无关——**跑题** |

`topic_only` 给 0.3 而非 0.0 是刻意的：它要和 `unsupported`(0.0) 区分开。
两者都低于默认阈值 0.6、都会导致弃权，但语义不同——前者是"没用"，
后者是"说错了"。F1.2 的价值恰恰在于这个区分。

### 改动二：门必须独立判定（`209c36e`）

**这一步是实现里最关键的坑。** 最初我把 `topic_only` 压成"某一条声明的
0.3 分"，实测发现**拦不住**：

```python
>>> gate.decide([0.9], [1.0, 1.0, 1.0, 1.0, 0.3])
(False, '')   # max = 1.0 >= 0.6，放行
```

弃权门第 4 步对逐条蕴含分数取 **`max`**，而 F1.2 那批样本的形态恰恰是
"5 条声明里 4 条 supported(1.0) + 整段答非所问"。`min` 聚合也一样
（4 条 1.0）。

**主题相关性是整段的属性，不该参与逐条聚合。** 所以：

- `VerificationResult` 增 `answer_status` 字段；
- `AbstentionGate.decide` 增 `answer_status` 形参，**作为第 3 步**
  （在蕴含分数检查之前）短路返回；
- `rag.py` 与 `rag_stream.py` 的 `gate.decide` 都传该字段。

拦截原因可区分：`topic_only` = 「证据主题相关但不包含所问事实」、
`irrelevant` = 「证据与问题无关」。

### 改动三：诊断可观测性（`12e51d2`）

- L3 失败路径的 note 现在带 `[声明 N 条 / 证据 M 条] [异常 类型]`
  （此前 9 条 `entailment_unavailable` 只能看到"失败了"、看不到原因）；
- `CaseResult.notes` 并入管线自己的诊断 trace——此前它只取评测集里标注者
  写的证据摘要，管线的失败原因全在 `qr.traces` 里，等于写了没人看；
- `CaseResult.judge_failures` 记录 `groundedness:no_claims` 这类**原因**。

### 生产用 v2、评测仍用 v1（刻意的）

`_DEFAULT_TEMPLATE` 改成 v2（生产侧），而 `eval/judges.py` 的
`GroundednessJudge` **保持 v1**——F1.2 基线是按 v1 口径测的，换了模板基线
就失去可比性。两侧分开让"生产行为"与"评测口径"各自独立演进。

## 三、端到端验证（真实上游，6 条）

```
[可答]        作答              75.1s  BIN_IVF_FLAT 索引适用于什么向量？
[可答]        作答              38.7s  cnalphanumonly 这个 filter 的作用是什么？
[可答]        作答              50.5s  删除节点或改 State key 之前为什么要先检测在途线程？
[不可答-同域]  主题相关性拦截     30.6s  Milvus 用 MongoDB 做元存储时需要配置哪些连接参数？
[不可答-同域]  主题相关性拦截     39.0s  把 Milvus 的数据同步到 Oracle 数据库要配哪个 connector？
[不可答-离域]  弃权              20.4s  烤披萨时烤箱要预热到多少度？

作答 3/6   幻觉(应拒却答) 0   正确拦截 3   L3降级 0
```

**3 条可答全部作答（无误拒）、3 条不可答全部拦下、零 L3 降级。**
这两条正是 F1.2 的病灶所在。

另单独验证 v2 模板在真实模型上的判别力（直接调 `_judge_claims`）：

| 场景 | answer_status | 耗时 |
|---|---|---|
| 证据里有答案 | `answered` | 5.7s |
| 主题相关但没答案（GraphQL） | `topic_only` | 2.5s |
| 离域（烤披萨） | `irrelevant` | 3.2s |

三类判别完全正确，且 v2 prompt 更长却没有让 judge 变慢。

## 四、验收判据

指标变好看不等于项目变好。`scripts/verify_f12_improvement.py` 按
`docs/2026-10-04-abstention-misses-off-topic-answers.md` 写明的口径判定，
必须能区分三种情况：

| 情况 | 指标形态 | 判定 |
|---|---|---|
| **真改进** | 幻觉降，过度拒答与有据性基本不动 | 通过 |
| **关门了** | 幻觉 0.64→0.10 但过度拒答 0.04→0.34 | **不通过** |
| **提门槛不是做判准** | 幻觉降但有据性/相关性同步下跌 | **不通过** |

第 2、3 种都会让幻觉率这个数字变好看而项目没变过。脚本额外报
**总作答率**（= 1 − `refusal_rate`），降下来就在输出里明说"降幅里有多拒成分"。

## 五、测试与变异验证

新增 22 条测试（`tests/test_l3_answer_relevance.py` 13 条、
`tests/test_abstention_answer_status.py` 10 条），
全部做过变异验证：

| 变异 | 结果 |
|---|---|
| `answered` 也压分 | ✅ 被抓 |
| 缺 `answer_status` 默认为 `answered`（fail-open） | ✅ **第一版测试没抓到**，补断言后抓到 |
| `topic_only` 标成 `unsupported` | ✅ **第一版断言写成"两者不等"，误标后恰好相等所以不变红**；改白名单后抓到 |
| 门的 `answer_status` 检查挪到蕴含分数之后 | ✅ 被抓（2 条变红） |
| 空 `answer_status` 也拒答（fail-closed） | ✅ 被抓（2 条变红） |

两个"第一版没抓到"值得记：它们都是**测试写法**的问题而非实现问题——
断言看起来在测某件事，实际上因为两侧行为等价而恒真。

## 六、复现

```bash
# 端到端冒烟（6 条，约 4 分钟）
python eval/.cache/e2e_v2.py

# 全量基线（约 2 小时）
python scripts/run_answer_baseline.py --workers 3 --sleep-ms 2000 \
  --out eval/baseline-v2.json

# 验收判据
python scripts/verify_f12_improvement.py

# 双提示词一致性（F1.4 的自动化旁证）
python scripts/compare_judge_templates.py
```

## 七、已知边界

1. **全量基线与验收数字待补**。端到端冒烟 6 条已验证有效，但 145 条上的
   六个数尚未产出——尤其是过度拒答率会不会因新维度上涨（本仓余量是
   0.0407 到 0.30 的阈值，容错空间大，但要实测）。
2. **`_judge_claims` 仍复用 `MAX_EVIDENCE_CHUNKS=12` 的证据截断**。答案声明
   多、证据长的样本可能仍被判不准；本次未改这个口径。
3. **judge 提示词升到 v2 后，L3 的一次调用成本可能上升**（prompt 更长）。
   F1.2 基线记的 543 次调用 / 2,499,996 tokens 是 v1 的数，v2 的用量要重新
   统计才能拿来做成本决策。
