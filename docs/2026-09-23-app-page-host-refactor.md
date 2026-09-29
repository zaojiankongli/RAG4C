# App 页面宿主拆分实现记录（2026-09-23）

## 变更

- 新增 `frontend/src/run/MountedPageHost.tsx`，专门负责已访问页面的 keep-alive 渲染、当前页/隐藏页 class、单页 ErrorBoundary 和 Suspense 加载状态。
- `frontend/src/App.tsx` 将原内联页面 host 替换为组件调用；页面映射、导航/身份/授权状态仍留在 App。
- `docs/plans/2026-09-23-app-page-host-design.md` 记录方案比较与不变量。本次无视觉/文案/CSS 变化。

## 为什么选这个切片

历史方案 `docs/前后端再设计与优化方案.md` 将 App 壳层拆分列为 C4b 后续；当前 App 实测 914 行。完整 AppLayout/路由器迁移会同时触及用户身份、租户权限、URL 与未保存状态保护，因此先取最清晰的展示生命周期边界，减少 App 的职责且保留受控状态归属。

## 验收

- `npm --prefix frontend exec -- vitest run src/App.initialRoute.test.tsx src/App.a11y.test.tsx src/App.mobile.test.tsx src/App.stage18.test.tsx src/App.stage19.test.tsx`：5 files / **24 tests passed**。
- `npm --prefix frontend run lint`：退出码 0，**0 errors**；输出 98 条已有 warning，均在其它现存文件（全局 ESLint 会扫描整仓）。
- `npm --prefix frontend run build`：通过（tsc + Vite，7119 modules transformed）。
- ESLint 首轮发现 App 中原属页面宿主的 `PAGE_KEYS` import 在拆出后 unused；当场移除并重跑 lint/build。

## 后续

`AppRoutes` 的 lazy 页面组成与 page map 已于 2026-09-24 单独提取，详见
`docs/2026-09-24-app-routes-separation.md`。下一片仍可评估 `AppLayout`，但不得借机搬动
workspace 身份能力、授权状态或路由状态；继续用 direct-route、dirty-navigation 与 enterprise
capability 测试守住边界。

## 独立审查

实现后的子 agent review 待完成；结论及修复/复验将在这里追加。

## 独立复核结论

第二轮子 agent 对完整工作树复核：**PASS**。未发现 blocker/high/medium/nit；确认页面顺序、Suspense/ErrorBoundary、keep-alive、隐藏态、前后端切片及文档一致。
