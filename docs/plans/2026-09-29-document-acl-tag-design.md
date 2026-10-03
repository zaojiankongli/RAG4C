# 契约设计：文档 acl_tag 与 FAQ 继承（Round 11 后续切片的先行设计）

> 状态：**设计稿（未实施）**。状态更新以此文件为准；实施时按「切片纪律」拆分
> 迁移 / 入库 / QA / 守卫四步，各自过门禁。
> 作者：2026-09-29，round11 会话。触发源：Round 10 §7-6 独立评审跟进项
> 「FAQ 绕过 chunk 级 acl」的长期解。

## 1. 现状勘测（本轮逐一核实，含对 Round 10 前提的一处修正）

| # | 事实 | 证据 |
|---|---|---|
| 1 | 检索侧 acl 过滤**完整**：`build_filters` 在 acl 非空且 `acl_filter_on` 时拼 `acl IN ['…']`（Milvus 表达式），图回取路径也有同款 Python 过滤 | `core/milvus_client.py:1090-1149`、`retrieval/pipeline.py:503` |
| 2 | Milvus schema 有 `acl` VARCHAR 字段，值取自 `chunk.metadata["acl"]` | `core/milvus_client.py:319/471` |
| 3 | **生产入库路径没有任何 acl 写入方**：`indexing/ingest.py`（metadata 直通 chunk）与 `server/documents.py` 都不构造 acl；只有 `eval/retrieval_eval/runner.py:134` 与 `core/retrieval_experiment_runner.py:459` 在写 | 本轮 grep 全仓核实 |
| 4 | `Document` 模型没有 acl 列；`parser_meta` 是解析诊断 JSON，不宜承载安全属性 | `models/orm.py:5981` |
| 5 | QA 旁路：FAQ 证据在 Milvus 过滤**之后**合并，天然不受 acl 过滤；现行守卫只有「全域 + acl → 整体跳过」，**具名数据集 + acl 时 FAQ 未按 acl 校验就注入** | `retrieval/qa_retrieval.py:122-128` |
| 6 | Round 10 原表述「继承 source_document_id 那份文档的 acl」的**前提需修正**：文档级 acl 目前不存在（事实 #3/#4），可继承的只有 chunk 元数据（且生产为空） | 本条修正本身是本轮产出 |

## 2. 契约提案（待裁定后实施）

**核心决定：acl 归属的产生地在「文档」，单一标签，入库时声明。**

1. **Document.acl_tag（迁移 0040_document_acl_tag）**
   - `documents.acl_tag VARCHAR(64) NULL`，NULL/空 = 无标签 = 「未声明归属」。
   - 单标签而非列表：Milvus 侧 chunk 的 acl 本就是单值（`acl IN [...]` 匹配单值字段），
     文档→chunk 一对一映射最简；多标签需求出现前不预先泛化。
   - 触点清单（实施时逐项过）：`models/orm.Document`、迁移文件、
     `core/catalog_schema`（shape/CHECK 对账）、`tests/head_catalog.py` 时代夹具、
     OpenAPI 重导出（ingest 请求体新增字段）、前端 ingest 表单（可选）。
2. **入库透传**：`create_document` / `register_document_atomically` / 文档 ingest API
   新增可选 `acl_tag`；存 Document 行；`parse_and_chunk` 把它写进每个 chunk 的
   `metadata["acl"]`（Milvus 行随 embed_and_insert 自然带上）。
   旧文档（无标签）重索引后仍无标签——行为不变。
3. **QA 继承（bundle 加载时左连 Document）**：bundle 每项携带
   `acl_tag`（源文档的标签；无源文档 = 空）。来源文档已被删除的 FAQ：跟随
   文档删除语义（文档删除 FAQ 不再有效），届时标签自然取不到 → 视为空。
4. **`apply_qa_retrieval` 的 acl 语义改为与 chunk 同构（fail-closed 按条判定）**：
   - 请求带 acl 时：`acl_tag ∈ acl` 的 FAQ 才可注入；空标签 FAQ 一律剔除
     （与「无标签 chunk 在 acl 过滤下不可见」完全同构）；
   - 删除「全域 + acl 整体跳过」的旧守卫（按条可证明后不再需要整体放弃）；
   - 新计数器 `query.qa_retrieval.acl_dropped`（被剔除条数），
     旧 `acl_scope_skip` 随守卫退役；
   - 无 acl 请求：行为完全不变（含空标签 FAQ）。
5. **语义边界（写进字段注释与台账）**：空标签在 acl 请求下不可见是**刻意的
   fail-closed**——「没声明归属的东西在限定视图里不可见」与 chunk 行为一致；
   部署方要 FAQ 在 acl 请求下可见，就在入库时声明标签。

## 3. 实施切片拆分（每片独立过门禁）

| 片 | 内容 | 门禁 |
|---|---|---|
| S1 迁移 | 0040_document_acl_tag + orm + head 夹具 + schema 对账 | catalog_schema 套件 + head_catalog |
| S2 入库 | create/register/ingest API 透传 + chunk metadata 落标签 | ingest 相关套件 + OpenAPI 重导出 + tsc |
| S3 QA | bundle 左连 + apply 按条过滤 + 计数器 + 守卫替换 | bundle/wiring 套件 + 变异反打 |
| S4 文档 | 台账 §9-6 补记、Round 10 §7-6 长期解销项、字段注释 | docs |

## 4. 需要裁定的开放问题（实施前必须回答）

1. **单标签 vs 多标签**：本设计选单标签（与 Milvus 现状一致）。若产品预期
   「一份文档同时属于 fin 和 legal」，需要把 chunk acl 字段改多值（更大迁移）。
2. **空标签 FAQ 的可见性**：fail-closed（本设计）会让存量无标签 FAQ 在 acl
   请求下全部不可见——这是否符合产品预期，还是需要「空标签跟随数据集
   acl_mode」的中间档？
3. **`acl_filter_on` 开关的定位**：它是运维逃生阀（关掉 = 不过滤），保留；
   但 enabled 语义要不要在台账里升级成安全红线（默认必须 on）？
4. **数据库级强制**：acl 标签只在应用层过滤（Milvus 表达式 + QA 按条剔除），
   数据库不强制——与现行 chunk acl 的威胁模型一致，需要确认这足够。
