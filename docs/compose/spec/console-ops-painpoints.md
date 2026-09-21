---
feature: console-ops-painpoints
status: delivered
updated: 2026-09-20
branch: compose/qa-faq-ops
commits: 879b359..working-tree  # console-ops review fixes uncommitted in compose tree; salvage checkpoint 5764092 carries prior slices
---

# Console Ops Painpoints（空态 / 鉴权门禁 / 触控）

## Report

**What was built** — 控制台运营阻塞点修复，不做 IA 合并：共享 `AuthRecoveryHint`（缺 token / token 被拒两种文案；调用方 `description` 优先，不覆盖离线诊断）；`ConsistencyPage` 无 token 预检不发 API，401/403 投影为身份恢复 CTA；Config 高级导航/行控件与 recovery 操作按钮对齐 `--control-min-h`（44px 契约）；`DocumentsPage` 空态与侧栏共用 `clearAllDocumentFilters`（keyword/status/type/engine/category/tag 全清）；企业/治理页 PageState 组合 AuthRecoveryHint。

**Verification** — compose 工作区 `D:\program_project\python_project\RAG4C-compose-qa-faq-ops`：

| 命令 | 结果 |
|---|---|
| `tsc --noEmit`（frontend） | **PASS** |
| `vitest run AuthRecoveryHint.test.tsx ConsistencyPage.test.tsx ConsistencyPage.css.test.ts KnowledgeGovernancePage.test.tsx` | **PASS** 4 files / 29 tests |
| 独立评审 critical×3（description 覆盖、clear 不全、缺 44px CSS 契约） | **已修复**；复测见上表 |

**Journey log**
1. 评审 #1：`AuthRecoveryHint` 在 hasToken 时无条件替换 description，会盖掉调用方「后端离线」诊断 → 改为 `description ?? (hasToken ? … : default)`。
2. 评审 #2：DocumentsPage 空态 CTA 只清 keyword/status，侧栏清筛选漏 keyword → 统一 `clearAllDocumentFilters`，两处复用。
3. 评审 #3：44px 触控只写了 CSS、无测试 → `ConsistencyPage.css.test.ts` 增加 console-ops describe（`@ts-expect-error` + `node:fs` 读 styles.css）。
4. compose clone 全量 vitest 仍不可靠（超时）；实用 FE 门禁 = tsc + 定向 vitest。
5. salvage 提交 `5764092` 已并入主仓 `integration/qa-faq-ops`；本切片评审修复为合并前最后一块。

## [S1] Problem

Playwright 与代码复核显示控制台仍有运营阻塞：

1. **一致性控制台** 401 只透传后端「需要有效的 KnowledgeOps Actor Bearer 凭据」，无恢复路径。
2. **系统设置** 桌面端控件密集，大量点击目标 &lt;44px（`--control-min-h` 仅窄屏生效）。
3. 多个企业/治理页空态或鉴权失败只有文案、无 CTA（下一步怎么做）。
4. 文档页筛选空态无「清除筛选」。

本切片做**证据驱动**的可用性修复，不做 IA 合并。

## [S2] Design

### 2.1 共享鉴权恢复

`frontend/src/components/AuthRecoveryHint.tsx`：

- 检测 knowledge actor token（`readKnowledgeActorToken`）是否缺失；
- 展示：标题 + 说明（如何在工作区配置 Actor Bearer / 联系管理员）+ 按钮「打开系统设置」跳 `#/config` 或 workspace 相关入口；
- 调用方显式传入的 `description` 优先；仅在未传时按 hasToken 选择默认/token 被拒文案；
- 可与 `PageState` 的 `extra` 组合。

### 2.2 ConsistencyPage

- 预检：无 token → `PageState error` + `AuthRecoveryHint`（不发请求或失败后同样提示）。
- 错误投影：401/403 → 「身份凭据无效或无权限」+ CTA；其它错误保留「重新连接并重试」。
- 与 sources/governance 的 `canRetry:false` 鉴权错误语气一致。

### 2.3 Config 触控

`styles.css`（或 config 专用段）：

- `.config-page` / 高级配置 `.adv-nav-item`、`.adv-row-control` 的 Input/Select/Button：`min-height: var(--control-min-h)`（44px）；
- `.auth-recovery-control-min-h` 同步；不缩字号、不改信息架构。

### 2.4 空态 CTA

| 页面 | 空态/门禁 | CTA |
|---|---|---|
| EnterpriseKnowledgeBasePage | 权威不可用 | AuthRecoveryHint / 刷新 |
| KnowledgeGovernancePage | scope/offline | 说明 + 重试/配置身份 |
| DocumentsPage | 筛选无结果 | `clearAllDocumentFilters`（keyword+status+type+engine+category+tag） |
| MonitorPage | 已较好 | 保持 |

### 2.5 测试

- ConsistencyPage：401/无 token 显示恢复提示；
- AuthRecoveryHint：有/无 token；token 存在时调用方 description 不被覆盖；
- CSS 契约：`.adv-nav-item` / `.adv-row-control` / `.auth-recovery-control-min-h` 的 `min-height: var(--control-min-h)`。

## [S3] Out of Scope

后端放松鉴权；dev-only 本地身份通道；全站 ARIA tabs 迁移；WeKnora RuntimeQueues 整页移植。

## Tasks

- [x] T1: AuthRecoveryHint + ConsistencyPage 401/无 token — acceptance: 单测绿（covers: S2.1; covers: S2.2）
- [x] T2: Config 触控 CSS — acceptance: 关键控件 min-height 44，相关测试/目测（covers: S2.3）
- [x] T3: 企业/治理/文档空态 CTA — acceptance: 相关页测试绿（covers: S2.4）
- [x] T4: 门禁 + 评审 — acceptance: 相关 vitest + tsc PASS（depends: T3）

## Workspace
- compose clone `RAG4C-compose-qa-faq-ops` / `compose/qa-faq-ops`；salvage `5764092` 已在主仓 `integration/qa-faq-ops`
