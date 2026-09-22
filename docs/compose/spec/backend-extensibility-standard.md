---
feature: backend-extensibility-standard
status: delivered
updated: 2026-09-22
branch: main
commits: —
---

# 后端扩展轴改造规范（"每个部分都可扩展"到底指什么）

目标口径在 2026-09-22 收紧了一次：从"把后端的设计改成可扩展式"变成"把后端**每个部分**
的设计改成可扩展式"。这句话如果按字面执行——把所有 `==` 都塞进注册表——会让代码变差。
所以本文件先把判据钉死，再给出允许的四种模式、每种的使用条件、以及"改造完成"的可测定义。
之后所有子系统的转换都必须引用本文件，不各自另立标准。

## 1. 什么算扩展轴（三问全过才算）

1. **这个值集预期会长吗？** 有人会在下个季度给它加一个成员吗（新解析器、新对象存储、
   新通知来源、新检索阶段）？还是它由模式/协议闭合（数据库 CHECK 只有两值的谓词、
   Alembic 时代目录、状态机上刻意穷举的转移）？
2. **每个值需要"行为"还是只需要"数据"？** 需要行为 → Strategy；只需要数据 → 声明式规格表。
3. **今天加一个成员，要改几处既有分支？** 记为 N。N = 1 且那一处本身就是新增位置
   （比如往一个 dict 里加一行）→ 已经是开的，不动。N ≥ 2 → 真问题。

三问里任何一问不过，结论就是 **不转**，并且要把理由写进清单——"这里故意保持硬编码"
是一个需要留痕的决定，不是偷懒。

## 2. 允许用的四种模式（只用这四种）

| 模式 | 用在哪 | 本仓参照实现 |
|------|--------|--------------|
| **Strategy + Registry** | 每个值要执行自己的逻辑 | `server/chunk_operations.py` 的 `ChunkVerb` / `CHUNK_WRITERS` |
| **声明式规格表**（Registry 装数据不是行为） | 每个值只带字段清单、要求、探针 | `core/storage_backends.py` 的 `StorageProviderSpec` |
| **Adapter** | 值选中的是一种外部形状，需要翻译成内部形状 | `retrieval/stages.py` 的 `RetrievalServices`；`indexing/parsers/` 若成立也走这条 |
| **Template Method** | 各值差异很小，但共享一段仪式（事务/outbox/栅栏） | `server/chunk_operations.py` 写路径与 `retrieval/stages.py` 的 `StageRunner` |

内核只有一个：`core/providers.py:ProviderRegistry`。**禁止**另写第二套注册内核、
插件自动发现、entry points、以及超过一层的 ABC 继承。

## 3. "改完了"的可测定义

每个被判定为 OPEN AXIS 的轴，必须同时满足：

1. **零改分支**：加一个实现只需注册一条，不碰任何既有分支。这一条要有**常驻测试**，
   不能只靠一次性探针。两种可接受写法：
   - 源码守卫：对被改文件做字面量扫描，断言不再出现对该轴的等值分支
     （参照 `tests/test_chunk_writers.py`、`tests/test_storage_provider_registry.py`）；
   - 宿主文件逐字节不变：注册一个假实现，断言宿主文件字节不变
     （参照 `tests/test_retrieval_stage_registry.py`）。
2. **注册期判死**：重名必须报错；实现形状不全必须**在注册时**报错，而不是等到跑到它时
   才 `AttributeError`（这条是 `retrieval/stages.py` 补过的教训）。
3. **行为等价**：既有测试零修改通过。**只有当契约本身被有意改变时**才允许改测试，
   且必须在 spec 里写清"旧断言为什么错"。改断言让新代码通过 = 不合格。
4. **反向验证**：亲手把新机制破坏一次（摘掉栅栏、塞进重复 order、让注册项缺失），
   确认它会以预期的方式变红，然后再恢复。没做过反向验证的"绿"不算证据。

## 4. 红线（这些让改造变危险，不是让改造更好）

- **入库值**：轴的值若存在数据库列里，加成员可能要迁移或改 CHECK。默认**不做迁移**；
  需要时必须单独成切片、单独评审，不允许夹在"顺手重构"里。
- **公开契约**：轴的值若出现在 OpenAPI / `frontend/src/types/rag.ts`，改值集是跨栈变更。
  本仓有 `npm run check:contract` 与 24 个 CSS/契约级源测试，值变了要一起重生成并说明。
- **鉴权面**：分支若决定权限或租户作用域，重构必须保持 **fail closed**——
  未知值一律拒绝，而不是落到默认放行。改造前后都要有"未知值被拒"的测试。
- **投影栅栏**：MySQL catalog 是唯一真相，Milvus 与图谱只是投影，投影不拥有业务真相。
  任何重构不得让投影写回成为权威，也不得削弱 `desired_index_revision` /
  `indexed_revision` 这道栅栏。
- **域不变量**：人工改切片必须先写不可变 Revision 再动 head；投影写受版本栅栏；
  删除是持久操作；生产失败必须可见；变更动作要租户作用域 + 留审计。

## 5. 基线（本轮起算点，均为实跑读数）

已完成的四个轴：切片写模式、切分模式、对象存储 provider、检索阶段 + 可选策略。
常驻守卫合跑 **33 passed**；`retrieval/pipeline.py` 在注册新阶段后**逐字节不变**已被测住。

普查基线：后端约 14.2 万行；候选分派点 **247**；`if/elif` 长链文件仅 **1** 个
（`core/catalog_schema.py`，属 Alembic 时代目录，非扩展轴）；大写 dict 表 **203**；
`Literal[...]` 联合 **89**（跨层重复值集的可疑信号）。

比较字面量密度 top（文件 / 处数）：`catalog_schema` 111、`enterprise_approval_control` 86、
`source_sync_ledger` 53、`enterprise_release_quality_scheduler` 53、
`enterprise_workspace_control` 49、`run_registry` 31、`document_deletion` 31。

## 6. 状态

本文件是规范，不是清单。逐轴清单与改造次序由四路并行普查汇总后落在
`docs/compose/spec/backend-extensibility-inventory.md`（待写）。
