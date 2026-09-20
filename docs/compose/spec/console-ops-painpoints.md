---
feature: console-ops-painpoints
status: designed
updated: 2026-09-20
branch: compose/qa-faq-ops
commits:  # filled at delivery
---

# Console Ops Painpoints（空态 / 鉴权门禁 / 触控）

## Report

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
- 可与 `PageState` 的 `extra` 组合。

### 2.2 ConsistencyPage

- 预检：无 token → `PageState error` + `AuthRecoveryHint`（不发请求或失败后同样提示）。
- 错误投影：401/403 → 「身份凭据无效或无权限」+ CTA；其它错误保留「重新连接并重试」。
- 与 sources/governance 的 `canRetry:false` 鉴权错误语气一致。

### 2.3 Config 触控

`styles.css`（或 config 专用段）：

- `.config-page` / 高级配置 `.adv-nav-item`、`.adv-row-control` 的 Input/Select/Button：`min-height: var(--control-min-h)`（44px）；
- 不缩字号、不改信息架构。

### 2.4 空态 CTA

| 页面 | 空态/门禁 | CTA |
|---|---|---|
| EnterpriseKnowledgeBasePage | 权威不可用 | AuthRecoveryHint / 刷新 |
| KnowledgeGovernancePage | scope/offline | 说明 + 重试/配置身份 |
| DocumentsPage | 筛选无结果 | 清除筛选按钮 |
| MonitorPage | 已较好 | 保持 |

### 2.5 测试

- ConsistencyPage：401/无 token 显示恢复提示；清除筛选按钮行为；
- AuthRecoveryHint：有/无 token；
- CSS 类存在性测试（若有既有 css test 模式则沿用）。

## [S3] Out of Scope

后端放松鉴权；dev-only 本地身份通道；全站 ARIA tabs 迁移；WeKnora RuntimeQueues 整页移植。

## Tasks

- [ ] T1: AuthRecoveryHint + ConsistencyPage 401/无 token — acceptance: 单测绿（covers: S2.1; covers: S2.2）
- [ ] T2: Config 触控 CSS — acceptance: 关键控件 min-height 44，相关测试/目测（covers: S2.3）
- [ ] T3: 企业/治理/文档空态 CTA — acceptance: 相关页测试绿（covers: S2.4）
- [ ] T4: 门禁 + 评审 — acceptance: 相关 vitest + tsc PASS（depends: T3）

## Workspace
- compose clone `RAG4C-compose-qa-faq-ops` / `compose/qa-faq-ops`
