# Evidence 带 dataset_id（交接 §9 第 21a 条更正后落地）

Date: 2026-09-27

## 前提更正（重要）

交接条目 21a 原称：答案 payload 里的 `result.evidence` 由
`langchain4j-sister-project`（Java 侧）产出、Python 只透传，因此前端拿不到
`dataset_id`，需要跨仓改动。**该前提经用户质疑后复核为错误**：

- `langchain4j-sister-project/` 只是物理放在仓库里的独立 Java 应用（AI 伴侣系统，
  Java 21 + Spring Boot 3 + LangChain4j），Python 侧没有任何调用点，
  `docs/RAG策略矩阵.md` 也把它列为排除目录。它与 RAG4C 的答案/证据链路无关。
- 证据链是**纯 Python 闭环**：检索管线产出 `RetrievedChunk`
  （其 `Chunk` 模型本就带 `dataset_id`，`models/schemas.py:44`）→
  `rag_common.evidence_chunks()` 转 dict → `rag_stream._result_payload()`
  组装 `result.evidence`。真实缺口只是 `_result_payload` 挑字段时把
  `dataset_id` 落下了（只挑了 chunk_id/doc_id/text/score/rank/source 六个）。
- 因此**不存在跨仓门槛，也不存在"伪造出处"问题**——`chunk.dataset_id` 就是
  这条 chunk 真实所属的库，由检索层逐条带出，跨库检索的每条命中各带各的库。

## Outcome

- `rag_stream.py::_result_payload` 的 evidence 项补 `dataset_id` 透传：
  dict 形态取 `chunk["dataset_id"]`，对象形态取 `chunk.dataset_id`，
  缺失时为 `None`。前端据此明确降级，不用查询作用域冒充出处。
- 前端无需改动：`EvidenceChunk.dataset_id?` 可选字段、`RetrievalTrace` 的
  stale 深链「文档+库归属齐备才生成直达链接，否则明示无法直达」、
  `AnswerEvidencePanel` 的同型消费此前已就位（交接条目 14 的处置）。
- OpenAPI 无需重导：`/api/query` 响应在生成契约里是自由 dict
  （`{[key: string]: unknown}`），evidence 字段不在生成 schema 内，
  前端类型由 `types/rag.ts` 手工声明并已含 `dataset_id?`。
- 新增 `tests/test_result_payload_dataset_id.py` 两条：
  ① chunk 自带的 `dataset_id` 必须原样透传；
  ② 缺库归属的 legacy chunk dict 输出 `None`，绝不借用查询作用域。

## Verification

- `pytest tests/test_result_payload_dataset_id.py` → **2 passed**
- `pytest tests/test_answer_evidence_facts.py tests/test_stream_contract.py` →
  **13 passed**（证据事实与流式契约回归）
- `vitest run src/components/RetrievalTrace.test.tsx src/answer-evidence` →
  **4 files / 22 tests passed**（含 stale 深链带库直达与缺归属降级用例）
- `tsc --noEmit` → 退出 0
- `ruff check rag_stream.py tests/test_result_payload_dataset_id.py` → 通过
- `git diff --check` → 通过

## 行尾事实（记录，供下一位核对 diff 时校准）

`rag_stream.py` 在 **HEAD 上就是混合行尾**（503 行 CRLF + 48 行裸 LF，散布在
`_RetrievalAttemptObserver`、`answer_query_stream` docstring 等区块）。
本轮编辑后把全文件归一成 CRLF（与文件声明一致），因此普通
`git diff --numstat` 显示 52/48，而 `--ignore-cr-at-eol` 显示 **4/0——
那 4 行就是本切片全部语义改动**（dsid 取值 + 注释 + 字段写入）。
差异全部来自 HEAD 存量混合行尾的归一，无内容丢失（LF 规范化后逐行 diff
恰为 4 行已验证）。

## Scope boundary

本片只给 evidence 项补 `dataset_id` 透传及其钉住测试。不改检索、不改鉴权、
不改其他 evidence 消费面（`_maybe_record_answer_fact` 传原始 list，
不受字段增加影响）；不动 `langchain4j-sister-project/`。
