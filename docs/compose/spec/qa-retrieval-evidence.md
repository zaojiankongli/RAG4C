---
feature: qa-retrieval-evidence
status: delivered
updated: 2026-09-20
branch: integration/qa-faq-ops
commits: dc3a94b..2d6296c
---

# QA Retrieval + Evidence Chain（审核后 QA 进入检索与证据）

## Report

**What was built** — 打通 Catalog 权威 FAQ 与问答证据链：`KnowledgeContentRepository.list_qa_retrieval_bundle` 仅导出 approved+active+retrieval_enabled+生效窗口内的 QA，并附带备选问/反例问；`retrieval/qa_matcher.py` 做规范化全等/备选/词面 Jaccard 匹配与反例抑制；`retrieval/qa_retrieval.py` 把命中转为 `RetrievedChunk(branch="qa", chunk_id=qa::{id})` 并并入 hybrid 结果。`rag._answer_sequential` 与 `rag_stream` 在弃权门前接线；`qa_hit` 时检索分按绝对分对待（避免 FAQ 命中被 RRF 不可比误弃权）。证据载荷带 `source_kind/qa_id/qa_revision`，AnswerEvidence refs 以 `qa::` chunk_id 可反查。Catalog 读失败记 `query.qa_retrieval.catalog_error` + warning，不阻断问答。不投影 Milvus。

**Verification** — 主仓 `D:\program_project\python_project\RAG4C`：

| 命令 | 结果 |
|---|---|
| `pytest tests/test_qa_matcher.py tests/test_qa_retrieval_bundle.py tests/test_answer_evidence_facts.py` | **PASS** 15 |
| `pytest tests/test_qa_matcher.py tests/test_qa_retrieval_bundle.py + test_knowledge_content.py -k qa/bundle/effective` | **PASS** 22 |
| `tsc --noEmit` | **PASS** |
| `import rag, rag_stream, qa modules` | **PASS** import-ok |
| 独立评审 | **success** — 六条 AC 均 met，无 critical；非 critical「catalog 静默失败」已补 metric+log |

**Journey log**
1. `list_effective_qa` 早已存在却只被测试调用——查询主路径从未消费审核后 QA。
2. 备选问/反例问变更会 review-reset；bundle 测试必须在 alt/neg 之后重新 `review_qa` 才能进 effective 集。
3. QA 绝对分（1.0/0.95）与 RRF 分不可混尺：`qa_hit` 时将 `scores_comparable=True`，让弃权门用阈值判 QA。
4. NFKC 会把全角 `？` 折成 `?`，匹配与测试必须共用同一规范化。
5. 可插拔 QA 检索保持 Catalog 真源，刻意不做 Milvus 投影，避免本切片膨胀成 index operation。

## [S1] Problem

Catalog 已具备 QA 权威与门禁（`approved` + `lifecycle_state=active` + `retrieval_enabled` + 生效窗口；`list_effective_qa` 有测试），但**查询主路径从未消费这些 QA**：

- `rag.py` / `rag_stream.py` 只走 Milvus hybrid / graph，不读 `qa_knowledge`；
- 已审核 FAQ 的权威答案不会出现在问答证据里；
- 自动 QA 审核后「可检索」在运行时是空头支票，违背 CONTEXT 不变量 *Automatic QA generation requires review before becoming retrievable* 与 Knowledge Lifeline 中 QA → 证据链的一环。

## [S2] Design

### 2.1 权威边界

- MySQL Catalog 仍是 QA 真源；本切片**不把 QA 投影进 Milvus**（不做 index operation / projection fence）。
- 仅 `list_effective_qa` 语义内的 QA 可进入检索候选：`review_status=approved` ∧ `lifecycle_state=active` ∧ `retrieval_enabled` ∧ 生效窗口。
- 过期/驳回/未审核 QA **不得**出现在证据中；数据保留，仅检索禁用（既有不变量）。

### 2.2 检索匹配 `retrieval/qa_matcher.py`

纯函数，离线可测：

- 规范化：NFKC + casefold + 空白折叠（与 `core.knowledge_content._question_normal_form` 同则）。
- 命中来源：主问题 / 备选问（`qa_alternative_questions`）。
- 分数：规范化全等 = 1.0；备选问全等 = 0.95；否则 token Jaccard（≥ `min_score`，默认 0.55）。
- **反例问**：若查询规范化后命中某 QA 的 negative（全等或包含关系），则抑制该 QA。
- 输出：`QAMatch(qa_id, question, answer, score, matched_on, revision, ...)`。

### 2.3 证据表示

- `RetrievedChunk.branch` 增加 `"qa"`。
- 合成 `Chunk`：`chunk_id = f"qa::{qa_id}"`，`doc_id = source_document_id or f"qa::{qa_id}"`，`text = f"{question}\n{answer}"`，`metadata` 含 `qa_id/revision/origin/matched_on`、`source_kind=qa`。
- 弃权门：存在 QA 命中时 `scores_comparable=True`（QA 分是绝对分，不是 RRF 名次分）。

### 2.4 查询路径接线

在 hybrid/graph 检索结果之后、弃权门之前：

1. `settings.pipeline.qa_retrieval_on`（默认 True）时读取 QA 包；
2. 匹合并入，QA 优先、按 `chunk_id` 去重；
3. catalog 失败 → metric `query.qa_retrieval.catalog_error` + warning，静默跳过不阻断；
4. 接线：`rag._answer_sequential` 与 `rag_stream`。

### 2.5 答案证据

- `rag_common.evidence_chunks` 对 QA 项注入 `source_kind/qa_id/qa_revision`；
- `record_answer_fact` 以 `chunk_id=qa::{id}` 写入 evidence refs，Lifeline 可反查 QA 权威。

### 2.6 设置

- `qa_retrieval_on: bool = True`
- `qa_match_min_score: float = 0.55`
- `qa_match_top_k: int = 3`

### 2.7 测试

覆盖 matcher 全等/备选/反例/弱相关、bundle 仅 effective、evidence refs 含 `qa::`、门对 QA 绝对分放行。

## [S3] Out of Scope

- QA 向量投影 / Milvus index operation / Graph 投影；
- 前端治理页展示 `branch=qa` 的专门面板；
- 负向反例自动训练 / 学习排序；
- 修改审核工作流本身。

## Tasks

- [x] T1: `retrieval/qa_matcher.py` + 单测 — acceptance: matcher/negative/normalize 单测绿（covers: S2.2）
- [x] T2: catalog QA 包加载 + settings — acceptance: `list_qa_retrieval_bundle` 仅返回 effective 及附属 alt/neg（covers: S2.1; covers: S2.6）
- [x] T3: RetrievedChunk branch=qa + 查询路径接线 — acceptance: 接线测试绿，QA 证据进入 QueryResult（covers: S2.3; covers: S2.4; covers: S2.5）
- [x] T4: 门禁 + 评审 + finalize — acceptance: 相关 pytest + tsc PASS；spec delivered（depends: T3）

## Workspace

- 主仓 `D:\program_project\python_project\RAG4C` / `integration/qa-faq-ops`
- Override: compose worktree 沙箱不可用；主线已统一至 `dc3a94b`，本切片直接在 integration 提交
