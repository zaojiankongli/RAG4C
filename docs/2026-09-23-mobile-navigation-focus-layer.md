# Mobile navigation focus layer（2026-09-23）

## 交付内容

在不改变 RAG4C 路由、侧栏宽度、页面保活、工作区身份和脏草稿确认的前提下，补齐窄屏导航的“临时覆盖层”交互：

- `frontend/src/App.tsx`：移动导航打开时聚焦主导航 landmark；主内容区设置 `inert`，关闭时侧栏设置 `inert` + `aria-hidden`；Escape、遮罩点击和侧栏关闭按钮统一回焦“打开导航”；导航选择和内部 handoff 后回焦新页面主内容。
- `frontend/src/App.tsx`：Tab/Shift+Tab 在移动导航 rail 内循环，不会穿透遮罩进入页面或跳到 skip link；移动↔桌面断点切换时把焦点交接给可见的 rail/toggle。
- `frontend/src/onboarding/OnboardingTour.tsx`：真正把引导作为模态焦点层处理，打开后聚焦关闭按钮，Tab/Shift+Tab 限制在引导内，Escape 关闭；关闭时回到触发点，若触发点因断点切换变为 inert 则回退到移动 toggle。引导内部导航显式把 return focus 目标设为主内容，避免焦点被带回旧 trigger。
- 浏览器 back/forward/hash 变化只在移动 rail 仍打开时关闭并转移焦点；rail 已关、onboarding modal 打开时不再把焦点抢到 modal 背后的 main。
- `frontend/src/styles.css`：新增仅在 `max-width: 768px` 生效的 `.mobile-nav-backdrop`，位于内容与侧栏之间（z-index 29/30），使用既有墨色 token 语义，不引入新的主题体系。
- `frontend/src/App.mobile.test.tsx`：补充关闭状态的可访问性、打开焦点落点、路由关闭焦点、遮罩关闭、Escape 关闭和 rail 内 Tab 循环测试。

## 保持的行为边界

- 桌面端侧栏仍保持 232/72 宽度和原有折叠行为；无移动遮罩。
- 移动端侧栏仍是 232px overlay；不引入底部导航、React Router 或新的状态容器。
- 业务页面、工作区 selector、通知 Drawer、权限、URL/history/hash 和 keep-alive 页面状态不迁移。
- `inert` 只隔离移动导航打开时的 `.app-content`；关闭后恢复页面交互。关闭的移动侧栏不进入 accessibility tree/focus order。

## 验证证据

- App focused regression：`npm run test:single -- src/App.mobile.test.tsx src/App.a11y.test.tsx src/App.initialRoute.test.tsx src/App.notifications.test.tsx src/App.stage18.test.tsx src/App.stage19.test.tsx src/onboarding/OnboardingTour.test.tsx` — **43 tests passed**（含 dialog/route handoff/popstate focus 回归）。
- `npm run lint` — exit 0，0 errors，98 条既有 warnings 未增加为 errors。
- `npm run build` — `tsc --noEmit` 与 Vite production build 均通过。
- 全量 Vitest `npm test`：**291/291 test files passed**，manifest
  `78f61fd7591b7da3fb3fe5eeec06aac4e9c406aabef41b9ed06be90ed1f54eab`。
- Edge 真实浏览器 375×812 检查：打开后焦点为 `#primary-navigation`，主内容 `inert=true`；连续 Tab 始终落在侧栏；Escape 后焦点回到“打开导航”，侧栏 `inert=true`、主内容恢复；page errors 0。
- 视觉产物：`frontend/output/playwright/mobile-nav-open.png`。
- 独立 subagent review 迭代找出焦点穿透、dialog 不 trap、断点切换、popstate 抢焦及 onboarding route return-focus 等问题；均已增加修复与回归。最终复审 **PASS**，确认导航/dialog 焦点循环、Escape、return-focus、响应断点与 history 变化均无剩余 finding。复审还发现 popstate 回归断言需等待原来的 0ms task；已补等待，并确认该回归先红后绿。

## 接手说明

后续若将移动导航改成真正的 Drawer 或引入 focus trap 组件，必须继续保留：`aria-controls="primary-navigation"`、关闭后回焦、主内容隔离、Escape/遮罩关闭，以及 375px 视觉和溢出验收。不要把 `inert` 逻辑下沉到页面组件，也不要在路由层复制一套移动导航状态。

## Review 记录

- 首轮独立审查：指出打开后焦点仍可能穿透内容、关闭侧栏仍留在 focus order、路由选择直接 `setMobileNavOpen(false)` 不回焦。
- 修复：增加 `useLayoutEffect` focus/inert 同步、统一 `closeMobileNavigation`、移动侧栏 closed-state `inert`/`aria-hidden`、路由 focus target、rail 内 Tab 循环；引导层新增自身 focus trap/Escape 与 return-focus 语义；桌面/移动断点交接、引导内导航和 popstate 时机均显式化。最终 review PASS。
