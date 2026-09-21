---
feature: admin-endpoint-auth
status: delivered
updated: 2026-09-20
branch: integration/qa-faq-ops
commits: a5a1d8b..working-tree
---

# Admin Endpoint Auth（管理型端点鉴权接入）

## Report

**What was built** — 在 C3 远程 operator 中间件之上补齐业务鉴权：`security.is_admin_path` 用路径模式覆盖参数化文档写（`/api/knowledge-bases/*/documents/*/delete|batch-delete`、`/api/documents/*/reindex`）；`knowledge_security.require_actor_on_admin_writes`（默认 **true**）控制 ingest / reindex / config.update / eval.run 是否强制 Knowledge Actor；ingest/reindex 将租户绑定到 actor（不一致 403；文档 `tenant_id` 为空 **fail-closed**），并在 handler 内按 body/document 的 `dataset_id` 二次调用 `require_knowledge_permission(WRITE)` 强制 dataset ACL；config/eval 在 require 或非 loopback 时要求 MANAGE。配置实现拆为 `_apply_config_update` 供热更新单测直接调用。前端 ingest/reindex 客户端附带 knowledge auth headers。

**开关语义（以代码为准）**：`knowledge_security.require_actor_on_admin_writes=false` **仅**放宽 loopback 无 token；远程仍必须先过 operator 中间件，且非 loopback 在业务层仍要求 Knowledge Bearer。

**Verification** — 主仓 `D:\program_project\python_project\RAG4C`：

| 命令 | 结果 |
|---|---|
| `pytest tests/test_admin_access_guard.py tests/test_admin_endpoint_actor_auth.py tests/test_config_hot_reload.py` | **PASS** 38 |
| `tsc --noEmit` | **PASS** |
| 覆盖说明 | is_admin_path 模式；require_actor 开关；tenant 绑定；dataset resolver 传入 body dataset；空租户 reindex 403；config 热更新行为单测。**未**跑完整 FastAPI TestClient 远程矩阵（环境限制）；远程闸门语义由 security 单测锁定。 |

**Journey log**
1. `_ADMIN_PATHS` 精确集合漏掉知识库参数化文档写路由 → 必须 `is_admin_path` 模式匹配。
2. FastAPI 路由不能把 `Request | None` 当 response 字段注解；鉴权在路由、实现拆 `_apply_config_update`。
3. 评审 critical：`dataset_resolver=None` 会跳过 dataset ACL → handler 内按目标 dataset 二次 enforce；reindex 空 `tenant_id` 不得放行。
4. 开关真实路径是 `knowledge_security.require_actor_on_admin_writes`，不是 pipeline 段。
5. 身份解析与 dataset 授权必须分两步：依赖里只有 request，body/document 上下文在 handler。

## [S1] Problem

C3 中间件路径与真实路由不一致；管理写无租户/dataset 绑定的 Knowledge Actor 门禁。

## [S2] Design

### 2.1 路径匹配

`is_admin_path`：精确白名单 + `knowledge-bases/*/documents/*(delete|batch-delete)` + `documents/*/reindex`。

### 2.2 业务鉴权

| 端点 | 权限 | 附加 |
|---|---|---|
| ingest / ingest-folder | WRITE（身份）+ WRITE（body.dataset_id） | tenant 强制 = actor |
| reindex | WRITE（doc.dataset_id） | tenant 必须相等且非空 |
| config.update / eval.run | MANAGE（require 或非 loopback） | 远程仍先 operator 中间件 |

设置：`knowledge_security.require_actor_on_admin_writes: bool = True`。

### 2.3 测试

security 路径模式；flag；dataset resolver；空租户 reindex 403；config 热更新 impl。

## [S3] Out of Scope

OIDC 登录流；把本地开发默认改成强制 token（开关控制）；全量 TestClient 远程 E2E。

## Tasks

- [x] T1: is_admin_path + 中间件/测试 — covers: S2.1
- [x] T2: ingest/reindex Actor + tenant/dataset 绑定 — covers: S2.2
- [x] T3: config.update / eval.run MANAGE 门禁 — covers: S2.2
- [x] T4: 门禁 + 评审 critical 修复 + finalize — depends: T2

## Workspace

- 主仓 `D:\program_project\python_project\RAG4C` / `integration/qa-faq-ops`
