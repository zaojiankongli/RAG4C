# Stage 24 腾讯任务运营 UI 参考

日期：2026-08-29

## 官方证据

- 腾讯云智能体开发平台召回测试：https://cloud.tencent.com/document/product/1759/135538
- 腾讯云智能体开发平台产品动态：https://cloud.tencent.com/document/product/1759/104191
- 腾讯云 MySQL 创建导入任务：https://cloud.tencent.com/document/api/236/15858
- 腾讯云 DTS 任务状态说明：https://cloud.tencent.com/document/product/571/71335
- COS 管理批量处理任务：https://intl.cloud.tencent.com/zh/document/product/436/32961

## 腾讯式任务页面模式

腾讯产品对长时间任务普遍采用：

- 独立任务列表；
- 状态筛选和搜索；
- 明确区分排队、校验、运行、完成、失败、中断、恢复；
- 详情展示阶段、进度、错误和时间；
- 只在状态允许时提供重试、取消、恢复等操作；
- HTTP 接受不等于任务完成；
- 任务历史保留并支持按 ID 深链；
- 危险操作使用二次确认和权限边界。

知识库召回测试还强调：历史记录、知识库范围、并行对比、来源和结果详情。对 RAG4C 的启示是统一任务中心不能只展示一个 spinner，而应保留来源、队列、attempt、结果与安全业务入口。

## Stage 24 推荐 UI

```text
Enterprise Task Operations Center
├─ Attention Board
│  ├─ Running
│  ├─ Queued
│  ├─ Failed
│  └─ Stale / Lease risk
├─ SOURCE -> QUEUE -> ATTEMPT -> OUTCOME
├─ All / Running / Failed / Completed / Activity
├─ Desktop PrimaryTable / Mobile Cards
├─ Task Detail Drawer
├─ Safe Retry / Cancel Dialog
├─ Saved Views
└─ Reconciliation Status
```

使用 TDesign React 与 TDesign Icons。主视觉延续腾讯云控制台的蓝灰工作面板，失败使用克制红色，排队/等待使用琥珀色，完成使用绿色；不采用营销式大渐变或装饰性动画。
