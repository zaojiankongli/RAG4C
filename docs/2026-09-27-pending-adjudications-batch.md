# 六条待裁定项批量裁定与落地（2026-09-27）

## 授权与范围

用户授权原文：「这些我觉得你选择企业级合适的做法就行了 或者 你和我分析利弊 我来决定」。
据此裁定六条交接审查 §9 待裁定项：四条需要改动的落地实现，两条核实/分析后维持现状。
第 21a 条因跨仓（Java 侧）明确只做利弊分析，不擅自开工。

## 逐条裁定

| 条目 | 裁定 | 落地 |
|------|------|------|
| §9-8 墓碑 `projection_pending` 恒真 | delete 成功后推进 head（期望态即"投影里已无此 chunk"） | `indexing/projection_handlers.py` milvus/graph 两 handler 的 mutation 分支改为 edit/delete 都 `mark_indexed`；`scripts/backfill_chunk_authority.py` 回填对齐；两处测试断言反转/新增 |
| §9-27 分面字段滚动窗口 | 方案 (B) 空值兜底（(A) 上线顺序契约无法本仓单侧执行） | `frontend/src/types/rag.ts` `chunking_reasons` 改 optional；`DocumentsPage.projectSummaryFacets` 缺键空分面；新增守卫用例 |
| §9-28 `started`/`running` | 查实为双层词表（存储 `running`/公开 `started`），无数据缺陷，保留 | 写路径查实回填 `test_enterprise_task_vocabulary_reconciliation` docstring，断言不动 |
| §9-29 `gate_state` 双拼法 | 留（宽容收参是有意契约，声明表已收口） | 核实 `core/release_quality_gate_states.py` + 对账测试 16 passed，无代码改动 |
| §9-31 `mineru.provider` Literal | 不加（出口查表必拒维持权威校验） | 无代码改动；裁定依据见下 |
| §9-21a 证据带 `dataset_id` | 只分析，留用户裁定跨仓推进 | 无代码改动，利弊见交接条目 40 |

## 关键实现事实

### §9-8（唯一行为改动）

- **为什么选"推进 head"而不是读侧排除 disabled**：读侧排除会把"删除尚未执行/执行失败"
  也报成"投影响应为当前"，worker 里 delete 操作 dead 时信号被完全吞掉——那是掩盖不是修复。
  推进 head 让"已成功停用"与"追平"同义，语义正确且改动面最小。
- **安全边界**：`mark_indexed` 的 CAS（`desired_index_revision == expected_revision`）+
  post-fence（`_assert_current` + head 签名比对，mutation=="delete" 时 `enabled` 必须仍为
  False）都在 mark 之前，并发 restore 会让 stale 先抛、不会误推进；CAS miss 静默保持
  pending 属 fail-closed（对账重排队）。
- **回填对齐**：backfill 产生的墓碑头原先写 `indexed=0/not_indexed`，同样恒报 pending；
  现与 worker delete 语义一致记 ready/追平（parent 角色除外）。前提是遗留部署没有
  `enabled=False` 向量仍驻留 milvus（生产管线不产这种元数据）。
- 消费面收口核实：`server/knowledge_chunks_api.py:198` 与 `server/documents.py:957-960`
  的 `projection_pending = indexed < desired or status != "ready"` 在新语义下对墓碑都报
  False；前端 `parseInterventionModel.projectProjectionLifecycle` 显示"投影响应为当前"，
  不再恒挂"投影待处理"。

### §9-31 裁定依据（含评审更正）

1. 运行时 `/api/config/update` 热更新链对 str 字段只透传（`server/support.py:73`），
   Literal 挡不住 API 写入、只挡启动加载——防护面不完整且带升级停机风险。
2. 回潮栅栏 `test_no_module_outside_the_declaration_compares_provider_itself` 的
   docstring 明文把「provider 是裸 str（没有字面量校验）」钉成当前形状。
3. **更正**：此前引证「测试夹具构造怪值会被 Literal 崩掉」不成立——怪值参数化用例
   （`test_an_undeclared_provider_is_refused_instead_of_falling_through_to_egress`）走
   `_NamespaceSettings` 桩绕过 pydantic，Literal 不影响它。独立评审抓出，裁定依据以 1/2 为准。

## 本轮实跑读数（本机新跑）

- `pytest tests/test_chunk_authority_api.py` → **12 passed / 35.94s**
- `pytest tests/test_projection_handlers.py tests/test_index_worker.py tests/test_chunk_count_reconciliation.py tests/test_knowledge_chunks_api.py` → **72 passed / 234.36s**
- `pytest tests/test_document_chunks_api.py tests/test_document_delete_api_v2.py` → **19 passed / 11.88s**
- `pytest tests/test_enterprise_task_vocabulary_reconciliation.py tests/test_enterprise_task_operations_core.py` → **28 passed / 1.43s**
- `pytest tests/test_release_quality_gate_states.py` → **16 passed / 1.36s**
- `pytest tests/test_knowledgeops_chunk_backfill.py` → **41 passed, 1 skipped / 251.90s**
- `pytest tests/test_mineru_provider_registry.py tests/test_knowledgeops_chunk_reconcile.py` → **55 passed / 245.45s**（§9-31 现状确认）
- `tsc --noEmit` → 退出 0；`vitest run src/pages/DocumentsPage.workspace.test.tsx src/pages/DocumentsPage.test.tsx` → **40 passed**
- `ruff check`（触及文件）→ 全部通过；`git diff --check` → 通过

环境备注：sqlite pytest，权威 MySQL 不可达；前端证据到 jsdom 为止。

## 独立评审

**PASS_WITH_NITS**。P1（交接台账未同步裁定）已处置：交接条目 8/19/27/28/29/31 就地标注
裁定结论，新增条目 40 完整记录。P2（§9-31 引证失实）已按评审更正并写明。两条 NIT
（mark_indexed 返回值忽略的语义注释、backfill ready 的物理前提）已补代码注释。

## 范围边界

行为改动只有 §9-8（墓碑头投影语义）与 §9-27（前端缺键容错）。§9-28/29/31 均为文档与
测试 docstring 层面的事实回填，无语义变更；§9-21a 未开工。无 migration、无 OpenAPI、
无后端 API 契约变更。
