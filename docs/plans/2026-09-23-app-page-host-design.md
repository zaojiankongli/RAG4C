# RAG4C 前端 App 页面宿主拆分设计（2026-09-23）

## 目标与证据

前端近期重设计文档 `docs/前后端再设计与优化方案.md` 的 C4b 明确登记了 `App.tsx` 职责拆分。当前 `frontend/src/App.tsx` 实测 914 行，承载导航/身份/通知等 shell 状态，并直接内联所有访问页面的 keep-alive 挂载、隐藏页展示、错误边界和 Suspense fallback。首切片从纯展示生命周期边界入手，避免先搬动认证和路由状态。

## 候选方案

1. **提取页面宿主（推荐）**：单独组件接收 `activePage`、`mountedPages` 和 `pageNodes`，负责按稳定页面顺序仅渲染已访问页面，并对每页包 ErrorBoundary/Suspense。低风险、依赖关系单向，直接缩小 App 的渲染职责。
2. 提取完整 AppLayout：需迁移 Sider、WorkspaceScopeBar、通知控制器、身份与回调，边界跨状态和授权逻辑，首片风险过高。
3. 改用 React Router：改变 URL/导航栈、keep-alive 与 direct-route 合约，超过本轮必要范围。

## 决策与不变量

采取 1。仅机械迁移已有 JSX，不更换样式类、页面顺序、路由解析、加载文案、错误隔离、懒加载或 keep-alive 行为。页面状态仍由 App 持有；pageNodes 仍在 App 构造，以免把企业授权上下文误下沉到纯布局层。

## 验收

- `App.initialRoute`、`App.a11y`、mobile navigation 和 `App.stage18/19` 测试通过；
- TypeScript build 与 ESLint 通过；
- 页面视觉/DOM 合约不变，因此以行为测试/build/lint 为验收，不做无依据的样式调整。
