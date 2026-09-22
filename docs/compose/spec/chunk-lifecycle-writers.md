---
feature: chunk-lifecycle-writers
status: delivered
updated: 2026-09-22
branch: main
commits: 426e0fa..（本地待提交，不 push）
---

# 切片 S-CL：chunk 生命周期 API + 写模式策略注册表

> 派生自 `C:\Users\饶策\Desktop\me\WeKnora-0.8.0` 的对齐勘察（见 §S2.6 借鉴边界）。
> 本切片是"后端可扩展化 + 前端细化"两件事的交点：它既消掉 chunk 写路径上的模式分支，
> 又是前端唯一还没能露出的那 4 件生命周期能力的解阻塞项。

## [S1] Problem

### S1.1 前端看得见但用不了的 4 件事

`frontend/src/parse-intervention/` 已是一个可用的三栏切片工作区（解析上下文｜切片列表｜编辑器），
带虚拟化列表、行级 diff、`expected_revision` 乐观并发与投影回执。但 `ChunkPatch` 只接受
`{text, expected_revision}`（`server/knowledge_chunks_api.py:50-52`），导致：

| 缺的能力 | 现状证据 | WeKnora 对应物 |
|---|---|---|
| 切片启用/禁用 | `ChunkListPane.tsx:10` 的 `enabled` 只是 Tag；无 Switch | `BatchUpdateChunkEnabledStatus` |
| 墓碑恢复 | `parse-intervention/` 全目录 grep `restore` 零命中；`PATCH` 在 `:263` 无条件 409 | `PUT /chunks/:kid/:id` + `is_enabled` |
| 切片 Revision 历史 | `ChunkEditorPane.tsx:18` 明文 Alert「当前没有切片 Revision 历史接口」 | `GET /chunks/:kid/:id/revisions` |
| 修改原因持久化 | `ChunkEditorPane.tsx:14` 明文 Alert「当前 API 不持久化修改原因」 | `ChunkRevision.edit_source` + 审计 |

**关键发现**：这四件事的**领域层代码已经全部写完并带测试**，只是没有生产调用方 ——
`core/chunk_catalog.py` 的 `edit_chunk(:285)` 已带 `enabled: bool | None` 形参、
`list_revisions(:202)`、`tombstone_chunk(:334)`、`revert_chunk(:368)` 全在，
调用方只有 `tests/test_chunk_revisions.py:83,102,111` 与 `tests/test_chunk_authority_api.py` 四处。
所以本切片的实现风险面在**接线**，不在领域语义。

### S1.2 加一个动词要在 4 处各加一刀

`authority_mode`（`off` / `shadow` / `active`）× 两个动词（update / delete）是内联条件写的：
`update_document_chunk_artifacts` 4 处（`server/chunk_operations.py:443,447,484,502`）、
`delete_document_chunk_artifacts` 4 处（`:606,615,650,670,683`）、
`_mutate_authority_with_outbox` 3 处（`:213,234,263`）——共 11 处 `authority_mode ==` 判断。
投影操作又是二元三元式 `operation = "upsert" if mutation == "edit" else "delete"`
（`core/index_operations.py:206`），而 `_mutate_authority_with_outbox:245` 无条件
`if not current.enabled: raise ChunkRevisionConflict("chunk is tombstoned")`
——所以"恢复一个墓碑切片"今天必须同时改分支顺序、三元映射、请求模型和三条 mode 分支。

**这是本切片引入策略/注册表的唯一理由**：不是"为了用上模式"，而是加动词的成本已经可量化地是 4 处。

### S1.3 一个必须先修的安全缺陷

`ChunkCatalog.list_revisions(chunk_id)` 的 `where` 只有 `chunk_id`
（`core/chunk_catalog.py:202-210`），**不带 tenant / dataset / document 过滤**。
今天无生产调用方所以未暴露；一旦按原样接 `GET /revisions` 就是跨租户读，
直接违反 `CONTEXT.md` invariant #5（变更型操作员动作必须 tenant scoped + audited）。
本切片把它一并修掉，并留一条反向验证。

## [S2] Design

### S2.1 值对象与端口（新文件 `server/chunk_writers.py`）

```python
ChunkOp = Literal["edit", "tombstone", "restore", "revert"]

@dataclass(frozen=True)
class ChunkMutation:
    op: ChunkOp
    doc_id: str
    chunk_id: str
    expected_revision: int
    text: str | None = None            # op=edit
    target_revision: int | None = None # op=revert
    reason: str | None = None
    editor_id: str = _MANUAL_ACTOR
    # 构造期校验：edit 必须带非空 text；revert 必须带 target_revision>=0；
    # tombstone/restore 两者都不带。非法组合在值对象层就抛，不进 mode 分支。

class WriterContext(NamedTuple):
    doc: dict; authority: ChunkCatalog; queue: IndexOperationQueue
    permit: DocumentWritePermit; pipeline: Any

class ChunkProjectionWriter(Protocol):
    mode: str
    def projection_operation(self, m: ChunkMutation, head_after: Any) -> str: ...
    def apply(self, m: ChunkMutation, ctx: WriterContext) -> ChunkUpdateResult: ...
```

三个实现精确对应今天的三种语义，**不改语义**：

| writer | mode | 今天的等价代码 | 行为 |
|---|---|---|---|
| `LegacyWriter` | `off` | `:447-468` | 无 ChunkHead 权威时只写 legacy 投影 |
| `ShadowWriter` | `shadow` | `:484-522` | authority 变更 + 同请求内 legacy/Milvus 同步 |
| `ActiveWriter` | `active` | `:488-499,523-529` | authority 变更 + 只落 durable op，追赶交给 worker |

`projection_operation` 从"`mutation == "edit"` 吗"改成**按变更后的 `enabled` 推导**：
结果 head 不 enabled → `delete`，否则 → `upsert`。这条规则对四个动词都成立
（edit→upsert、tombstone→delete、restore→upsert、revert→取决于那版的 enabled），
比原来的二元三元式更正确，也正是"恢复"以前无法走通的根因。

**durable op 的载荷词汇不变**：写回 `enqueue_chunk_projection_operations(mutation=...)`
时仍映射成 `"edit"` / `"delete"`，所以 `indexing/projection_handlers.py`、reconciler、
队列去重键 `chunk-mutation:{doc}:{chunk}:...`（`core/index_operations.py:225`）全部零改动。
决策搬走了，协议没动 —— 这是本切片"行为等价"的硬边界。

### S2.2 共享仪式留在 outbox，差异下沉到 writer

`_mutate_authority_with_outbox` 保留事务/`session.begin()`/`assert_document_write_permit(for_update)`/
attempt/operations/document_generation 回填/`cache_epoch.bump` 这些**每个 mode 都一样**的仪式，
把它做不到的那部分（调哪个 `ChunkCatalog` 方法、跳过哪条守卫）收成一个 `authority_step` 回调：

```python
def _mutate_authority_with_outbox(..., authority_step: Callable[[Session, Any], Any],
                                  projection_operation: str) -> _AuthorityMutation
```

`:226` 的"对已墓碑的 chunk 再 tombstone 是幂等 no-op"和 `:245` 的 tombstoned 守卫
**原样保留**，只是从"写在共享函数里"变成"由对应 writer 决定要不要过这道守卫"：
`tombstone` 过（因此仍幂等）、`edit`/`revert` 过（因此仍拒绝改墓碑）、
`restore` 不过（这正是新增的能力）。

编排入口收敛为一个：`apply_chunk_mutation(mode, mutation, *, catalog_api, pipeline, ...) -> ChunkUpdateResult`，
内部 `CHUNK_WRITERS.create(mode, cfg)`。`update_document_chunk_artifacts` /
`delete_document_chunk_artifacts` 保留为**签名兼容的薄壳**（它们有外部调用方与测试直接调用），
内部构造 `ChunkMutation` 后转给 `apply_chunk_mutation`。

注册表**复用 `core/providers.py` 的泛型 `ProviderRegistry`**（`:12-59`，已提供
register/unregister/names/create + 未知名可操作错误 + RLock），不新写注册表代码：

```python
CHUNK_WRITERS: ProviderRegistry[WriterConfig, ChunkProjectionWriter] = ProviderRegistry("chunk writer")
```

`off/shadow/active` 的合法集从 `_AUTHORITY_MODES` 常量改为注册表派生
（`set(CHUNK_WRITERS.names())`），保持与 `settings.catalog.chunk_authority_mode` 的校验语义一致。

### S2.3 端点

全部挂在既有 router 前缀 `/api/knowledge-bases/{dataset_id}/documents/{doc_id}/chunks` 下，
沿用既有 `ReadActor`/`WriteActor`/`DeleteActor`（`KNOWLEDGE_READ/WRITE/DELETE`）
与 `server/security.py` 的 admin 路径闸门 —— **不新增鉴权模型**。

| 端点 | 变化 |
|---|---|
| `PATCH /{chunk_id}` | body → `{text?, enabled?, reason?, expected_revision}`（`text`/`enabled` 至少给一个）；墓碑只允许被 `enabled: true` 打开，替换 `:263` 那条死板 409 |
| `GET /{chunk_id}/revisions` | 新 → `list_revisions_scoped(tenant_id, dataset_id, doc_id, chunk_id)`；返回 `[{revision, content, content_hash, enabled, editor_id, edit_source, edited_at}]`，**不回正文之外的 tenant/dataset 标识** |
| `POST /{chunk_id}/revert` | 新 → `{target_revision, expected_revision}` → `revert_chunk`（`:368` 已会连同那版的 `enabled` 一起回滚，所以 revert 天然是"回滚内容"，"恢复可见"走 `enabled: true`） |
| `DELETE /{chunk_id}` | 不变，语义即 `op=tombstone` |

`_projection()`（`server/knowledge_chunks_api.py:144-181`）补 4 个字段：
`source_content`（解析器原始输出，用于"原始 vs 当前"对比）、`editor_id`、`edit_source`、`edit_reason`。
`source_content` 只在单切片 `GET`/`PATCH` 回执里回，**不进列表页**（列表仍只回 `text`），避免列表响应体膨胀。

### S2.4 修改原因的落库选择

存 `chunk_metadata["edit_reason"]`（+ `edit_reason_at`、`edit_reason_by`），**零迁移、零 head 漂移**。

明确代价：原因只保留在 head 上，`chunk_revisions` 行没有逐版原因 —— 历史抽屉能看到"每一版的内容是谁改的、
启停状态、时间戳"（`ChunkRevision` 已有 `editor_id/edit_source/edited_at/enabled`），但看不到"那一版填的原因"。
升级到逐版原因需要 Alembic 0040 加可空列，连带 `HEAD_REVISION` 推进 + capability 白名单 +
`_REVISION_ORDER_FOR_CAPABILITY` + `tests/head_catalog.py` 时代夹具声明，
R7 §5.2 实测这类推进会带来 20 条测试漂移。**本切片不做**，登记为在册候选。

### S2.5 验收判据：可扩展性必须是可测的，不是形容词

守卫测试 `tests/test_chunk_writers.py`：

1. **零分支守卫**：扫 `server/chunk_operations.py` 源码，断言 `authority_mode ==` 出现次数为 0
   （仓库已有 `read_text()`/`ast.parse` 源码级守卫先例：`tests/test_phase8_dense_cosine.py`、
   `tests/test_enterprise_*_core.py`）。
2. **新增实现零改既有代码**：测试内 `CHUNK_WRITERS.register("test_writer", ...)` 一个假 writer，
   断言 `apply_chunk_mutation("test_writer", m)` 到达它，且**未修改任何既有 writer 或分支**；
   未知 mode 的报错串包含 `names()` 列表。
3. **新增动词只改一处**：断言四个 op 的 `projection_operation` 真值表
   （tombstone→delete / restore→upsert / edit→upsert / revert→按目标版 enabled）。
4. **反向验证**（invariant #5）：`GET /revisions` 跨租户读必须 404 —— 测试造两个租户各自的 chunk，
   A 的 actor 读 B 的 chunk revisions 得 404；把 `list_revisions_scoped` 的 scope 条件去掉时该测试必须变红。

### S2.6 借鉴 WeKnora 的边界（明确不抄的部分）

抄的：chunk 级 `enabled` 一等操作 + `expected_revision` 乐观锁、revisions 列表端点、revert 端点、
`source_content` 与当前 `content` 分离可对比、投影失败可由再次写触发重试（这里靠 `index_status` 可见化）。

**不抄**（WeKnora 在这几点上严格弱于本仓）：
- 它的 `ContentRevision` 只在写时快照旧版，**没有"当前 revision 是否已被索引"的栅栏**
  （`internal/types/chunk.go:112-179` + `application/service/chunk.go:644-694` 的同步 delete+reindex，
  崩在中途只留 `index_status=failed`，无 outbox/对账）。本仓有
  `desired_index_revision`/`indexed_revision`/`index_status` + durable op + `IndexOperationQueue`。
  **不得为对齐它而拆栅栏。**
- 它的 `rebuildParentContent` 用字符偏移回写父块、冲突时 append 尾部正文
  （`application/service/chunk.go:580-642`）。本仓父块走 `parent_chunk_id` 结构关系，不用偏移补丁。
- 它的 `CompositeRetrieveEngine` 对多引擎广播式双写 `BatchUpdateChunkEnabledStatus`
  （`retriever/composite.go:104-127`，失败只返首个 error）。Milvus 在本仓是投影不是真源，不能这么写。

## [S3] Out of Scope

- 检索管线阶段化（`retrieval/pipeline.py:508-1294` 的 787 行 `run()` → stage 策略）。
  这是后端下一个独立切片，本切片不碰检索路径。
- `chunking_router.chunk_with_mode`（`indexing/chunking_router.py:162-172`）三路 if、
  `GraphStoreFactory.create`（`core/graph_store_registry.py:121-134`）、
  `storage_backends.validate_provider_config` 的 provider 链 —— 同属"扩展点内核统一"候选，不在本切片。
- 逐版修改原因（Alembic 0040）。
- 合并/拆分/新增切片、按文档调切分参数、整篇原文文件预览（需要文件伺服，与 per-chunk `source_content` 不是一回事）。
- 全量契约迁移（`types/rag.ts` 745 行 → `components["schemas"]`）。本切片只让**新增字段**走生成类型。
- `chunk_revisions` 的保留策略/retention。

## Tasks

- [ ] T1: 改动前基线 —— acceptance: 定向 5 个套件在改动前全绿，读数写进 Report（covers: S1.1）
- [ ] T2: `server/chunk_writers.py` 值对象 + 端口 + 三 writer + `CHUNK_WRITERS` 注册表 —— acceptance: 三 writer 行为与 `:447-529`/`:606-683` 逐支等价；durable op 载荷词汇不变（covers: S2.1; covers: S2.2; depends: T1）
- [ ] T3: `apply_chunk_mutation` 编排入口 + 两个旧函数改薄壳 + 11 处 `authority_mode ==` 清零 —— acceptance: 既有 chunk 权威/修订/删除套件零修改通过（covers: S2.2; depends: T2）
- [ ] T4: `list_revisions_scoped` 与跨租户反向验证 —— acceptance: 去掉 scope 条件则测试变红；invariant #5（covers: S1.3; covers: S2.5.4; depends: T1）
- [ ] T5: 四个端点面（PATCH enabled/reason、GET revisions、POST revert）+ `_projection` 补 4 字段 —— acceptance: admin 闸门与 tenant 绑定测试同步通过（covers: S2.3; covers: S2.4; depends: T3; depends: T4）
- [ ] T6: 可扩展性守卫测试 —— acceptance: T2.5.1/2/3 三条绿，且人为在 chunk_operations.py 里加回一处 `authority_mode ==` 会让守卫变红（covers: S2.5; depends: T5）
- [x] T7: 门禁 —— acceptance: 定向 pytest + ruff 实跑读数写入 Report（covers: S2.5; depends: T6）
- [ ] T8: 独立 review 子 agent 复审后端 —— acceptance: 无 critical 残留才进前端（depends: T7）

## Workspace

- 仓库 `D:\program_project\python_project\RAG4C`，分支 `main`，tip `426e0fa`（起点）
- 后端解释器必须用 `.venv/Scripts/python.exe` + `PYTHONPATH=.`；不并发跑重负载测试（性能预算套件对负载敏感）
- 已知环境敏感红：1 条硬件预算 + 3 条负载敏感（见项目记忆 2026-09-21 基线），不属本切片回归
- 不 `git push`、不碰凭证、不删分支

## 实现偏差（执行期就地记录，保留上方原计划文字不涂改）

1. **不新建 `server/chunk_writers.py`，全部落在 `server/chunk_operations.py`。**
   writer 需要该模块里 10 个 helper（`_legacy_update` / `_legacy_delete` /
   `_bootstrap_authority_head` / `_authority_dependencies` / `_legacy_writer_permit` /
   `_head_to_chunk` / `_legacy_chunk` / `_document` / `_legacy_projection_lock` /
   `_update_document_after_delete`）。拆文件只有三条路：跨模块导 10 个私有符号、把这批
   helper 整体搬家、或函数级懒导入绕循环 —— 三条都比"同一模块内分区"差。扩展点在文件内
   仍是一等可发现的（`CHUNK_WRITERS` / `AUTHORITY_STEPS`），且 `authority_mode ==` 出现次数
   被源码级守卫钉成 0。
2. **`AUTHORITY_STEPS` 的值是 `ChunkVerb(decide, write)` 两段，不是单个回调。**
   首轮按"一个 callback 注入动词"实现时，enabled-head 完整性计数校验落在了写之后，
   active 模式下 tombstone 自己把 enabled 计数减了 1、校验随即判定权威不完整，
   `tests/test_chunk_authority_api.py` 3 条转红。拆开 decide/write 后共享函数得以保持
   原顺序 **判定 → 完整性栅栏 → 守卫 → 写**；附带好处是 `decide` 纯函数，不连库就能测。
3. **幂等捷径必须自己先过 CAS。** `test_repeated_tombstone_is_conflict_then_idempotent:407`
   要求"已墓碑 + 过期 `expected_revision`"仍冲突：原实现靠 `:226` 短路里调用
   `tombstone_chunk` 顺带做栅栏，我在调用前就返回 no-op，等于跳过乐观锁。
   现由 `_decide_tombstone` / `_decide_restore` 显式先比 `content_revision`。
   **推广为规则**：任何"状态已达成所以不做事"的分支都要先过并发栅栏，否则 stale 写会被读成无害重复。
4. `list_revisions` 直接收紧成 4 参 scoped 版，而非并存 `list_revisions_scoped`：
   它零生产调用方（只有 5 个测试调用点），并存等于留着那条未 scoped 的路。
5. `_write_revert` 按 `current` 头上的 tenant/dataset/document 过滤快照，不依赖调用方先校验作用域；
   `ChunkCatalog.revert_chunk` 同步收口。

## 判据的确切边界（不要把"零改分支"说过头）

守卫测试 `test_chunk_write_path_carries_no_authority_mode_branches` 只覆盖
`server/chunk_operations.py`，它证明的是**chunk 写路径内部不再有模式分支**。
新增第四种 authority mode 今天仍然需要改两处，都在这条守卫之外，如实登记：

- `config/settings.py:1002` 的 `chunk_authority_mode: Literal["off", "shadow", "active"]`
  —— 配置面用枚举收窄，加 mode 必须同步扩这个 Literal。
- `server/documents.py:350` 的 `if _configured_chunk_authority_mode() == "off"`
  —— 这是**文档级**路径（legacy 摄入/删除是否仍以非权威投影为准），不在这轮重构范围内。

真正被证明的是：新增**动词**只改 `AUTHORITY_STEPS` 一处；新增**模式**在 chunk 写路径内
只需 `register_chunk_writer()` 一处（守卫用探针 writer 实测到达，未碰任何既有 writer）。
配置 Literal 与文档级 off 判定属于"要扩就一起改"的同源声明，不是分支扩散。
若下一轮要把它们也吸收进来，做法是把 settings 的 Literal 与注册表 names() 做单测比对
（新增 mode 忘登记配置 → 红），而不是让配置面重新长出一套字符串校验。

## Report（只写本轮新跑的读数）

**T1 改动前基线**（未动代码）：`test_chunk_authority_api` + `test_chunk_revisions` +
`test_knowledge_chunks_api` + `test_chunk_diagnostics` + `test_admin_access_guard`
= **42 passed / 126.94s**。

**中间态读数（定位用，不作验收）**：签名收紧后 16 passed / 2 failed（两条正是被故意破坏的调用点）；
首轮重构后 83 passed / **3 failed**（全部归因到偏差 2）；守卫套件单跑 11 passed / ≈1s，不连库。

**反向验证（invariant #5）**：临时摘掉 `list_revisions` 的 tenant/dataset/document 三个过滤条件后，
`test_list_revisions_is_scoped_to_its_tenant_dataset_and_document` 在 `tests/test_chunk_revisions.py:180`
变红；还原后 `git diff` 只剩预期改动，`ruff check` 对受改 5 文件全绿。
