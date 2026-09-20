---
feature: storage-backends
status: delivered
updated: 2026-09-20
branch: compose/qa-faq-ops
commits: 879b359..working-tree  # uncommitted compose tree (qa-faq-ops + storage-backends)
---

# Storage Backend Registry（对象存储后端注册表）

## Report

**What was built** — 参考 WeKnora 0.8.0 storage-backends，为 RAG4C MySQL Catalog 增加租户级对象存储控制面：Alembic `0038_storage_backends` 新建 `storage_backends`（provider/config/status/source/软删），并为 `tenants.default_storage_backend_id`、`datasets.storage_backend_id` 增加绑定列；`HEAD_REVISION` 推进到 `0038_storage_backends`。API `/api/storage-backends` 提供 types/list/create/patch/delete/test/default/bind-dataset；密钥响应脱敏（`ak_***`/`***`）；local 做真实可写探测，minio/s3 等无 SDK 时返回 `validated_config_only`；默认后端不可删/禁用；已有 `doc_count>0` 的知识库冻结换绑。ConfigPage 懒加载「对象存储后端」面板（卡片、创建/编辑、测试连接、设默认、删除、KB 绑定），请求统一带 knowledge actor Bearer + `X-RAG4C-Tenant`。

**Verification**（compose 工作区）：

| 命令 | 结果 |
|---|---|
| `from server.storage_backends_api import router` + path 列表 | **PASS**（10 条路由） |
| `pytest tests/test_storage_backends_api.py tests/test_catalog_schema.py::test_upgrade_catalog_creates_current_versioned_schema` | **PASS** 3 passed（0038 升级 + 生命周期/绑定/脱敏/403） |
| `pytest tests/test_storage_backends_api.py tests/test_knowledge_content_qa_ops.py` | **PASS** 6 passed（QA 回归） |
| frontend `vitest` storageBackendsApi + StorageBackendsPanel | **PASS** 9/9 |
| frontend `tsc --noEmit` | **PASS** |
| `from server.app import app` | **PRE-EXISTING** FileNotFoundError `data/rag4c.db`（compose clone 无本地 catalog，与本切片无关） |

独立评审：首轮 critical（DELETE `204 + -> None` 破坏 FastAPI import；前端未带鉴权头）→ 已修复；复审 **success**，无剩余 critical。

**Journey log**
1. FastAPI 0.116 + `from __future__ import annotations` 下，`status_code=204` + `-> None` 会在注册期 assert 失败；本仓契约是 `-> Response` + `Response(status_code=204)`。
2. knowledge 鉴权 API 的前端必须 `resolveKnowledgeWorkspaceScope` + `knowledgeAuthHeaders`；裸 `request()` 会静默 401。
3. PATCH 回写密钥时按 **值** 跳过 mask（`***` / `ak_***`），不能按 key 名。
4. catalog HEAD 前进时必须同步 capability revision 白名单与 `_REVISION_ORDER_FOR_CAPABILITY`。
5. 控制面存储注册 ≠ ingest resolver：本切片不改写入库路径（S3）。

## [S1] Problem
RAG4C 缺少可注册的多实例对象存储后端：无法登记连接参数、测试连通性、设置租户默认或按知识库绑定。WeKnora 通过 `storage_backends` 解决同类运维问题。

## [S2] Design
交付与设计一致处：表/绑定列、API 面、脱敏、local 探测、remote `validated_config_only`、默认/绑定守卫、Config 面板 + knowledge 鉴权头。

偏差/澄清：
- 迁移号 **0038**（在 QA 0037 之后）；绑定列无独立 DB FK（仅 `fk_storage_backends_tenant`）；name 唯一为应用层查重。
- 权限实现为 `KNOWLEDGE_READ` / `KNOWLEDGE_MANAGE`（非 config loopback）。
- 远程存储不安装对象存储 SDK，测试诚实标注 `validated_config_only`。

## [S3] Out of Scope
ingest 路径消费 `storage_backend_id`、legacy env 自动折算、向量库注册表、完整云厂商上传/迁移工具。

## Tasks
- [x] T1: ORM + Alembic 0038 + catalog_schema HEAD/白名单 — acceptance: upgrade_catalog 到 0038，表/绑定列存在（covers: S2.2）
- [x] T2: repository + storage_backends_api + 脱敏/test/default/bind — acceptance: API 单测绿（covers: S2.3; covers: S2.4; depends: T1）
- [x] T3: 前端 Config 分区 + API client（含鉴权头）— acceptance: 单测绿；无密钥明文展示（covers: S2.5; depends: T2）
- [x] T4: 门禁 — acceptance: storage/catalog/QA pytest + storage vitest + tsc PASS；app import 缺 db 记 PRE-EXISTING（covers: S2.6; depends: T3）
- [x] T5: 独立评审 + critical 复审 — acceptance: 204 与鉴权 critical 已修，复审 success（depends: T4）

## Workspace

- `D:\program_project\python_project\RAG4C-compose-qa-faq-ops` / `compose/qa-faq-ops`
- 叠加未提交 qa-faq-ops；迁移序 0036 → 0037（QA）→ 0038（storage）
- 工作区 override：`git worktree add` 被沙箱拒绝 → 独立 clone
