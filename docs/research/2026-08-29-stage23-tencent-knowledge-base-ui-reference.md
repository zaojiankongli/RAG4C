# Stage 23 腾讯知识库与企业内容生命周期 UI 参考

日期：2026-08-29

## 调研范围

本轮使用 Playwright/agent-browser 对腾讯云官方知识库、知识管理、文档中心和知识库设置文档进行只读调研。浏览证据保存在：

```text
output/playwright/tencent-stage23-research/
```

主要官方入口：

- https://cloud.tencent.com/document/product/1759/123551
- https://cloud.tencent.com/document/product/1759/123552
- https://cloud.tencent.com/document/product/1759/112702
- https://cloud.tencent.com/document/product/1759/112704
- https://cloud.tencent.com/document/product/1759/112707

补充参考：

- 腾讯云仪表盘回收站说明：https://cloud.tencent.com/document/product/224/21985
- 腾讯云备份回收站：https://cloud.tencent.com/document/product/1662/102275
- COS 生命周期管理：https://cloud.tencent.com/document/product/436/17031

## 可复核发现

### 1. 腾讯知识库采用“功能树 + 工作区内容面板”

官方文档目录把知识库拆为：

```text
知识库
├─ 什么是知识库
├─ 什么是知识管理
├─ 文档
├─ 问答
├─ 数据库
└─ 知识库设置
```

这与 RAG4C 当前的知识工作台、文档、知识组织、来源和设置方向一致。后续企业功能应继续进入清晰的一级/二级导航，而不是隐藏在单个文档操作菜单里。

### 2. 腾讯式页面强调状态和操作边界

文档中心与知识管理页面使用：

- 左侧目录与当前能力选中态；
- 顶部搜索；
- 主体表格/列表；
- 明确状态字段；
- 批量操作；
- 设置页面集中管理切分、索引和生命周期规则；
- 窄屏时导航收敛、主体纵向排列。

对 RAG4C 的启示是：回收与恢复不应只是一个确认弹窗，而应有独立 Center、状态筛选、批量选择、详情抽屉和策略面板。

### 3. 腾讯产品族普遍使用回收站和保留期

腾讯云仪表盘和云备份均把“删除后进入回收站、保留一定时间、允许恢复、到期再清理”作为安全边界。COS 生命周期则把保留与自动删除视为显式策略，而非界面本地逻辑。

这些模式共同说明企业知识资产应采用：

```text
ACTIVE -> RECYCLED -> RETAINED -> RESTORED / PURGE-ELIGIBLE
```

而不是从文档列表直接进入不可逆物理删除。

### 4. 企业级危险操作必须与普通恢复操作分层

参考腾讯云控制台惯例：

- 恢复是回收站内的显式操作；
- 永久清理需要更强确认；
- 保留期/自动清理由策略控制；
- 批量任务需展示结果与失败项；
- 高风险动作需要审计和审批衔接。

## Stage 23 推荐

推荐：

```text
Enterprise Content Recovery Center v1.0
```

首期范围：Document recycle/restore、Tenant retention policy、legal hold、approval-gated purge request 和 immutable recovery event chain。

不推荐本期优先做：

- Enterprise Search & Discovery：当前已有问答、文档搜索和全局搜索入口，数据库缺口不如恢复治理紧急；
- Unified Task Center：当前已有运行监控、同步、删除、扫描和再认证任务事实，可作为后续聚合层；
- 仅做前端回收站空壳：无法解决现有永久删除直达链路的治理风险。

## 推荐 UI 结构

```text
回收站
├─ Summary Strip
│  ├─ 已回收
│  ├─ 即将到期
│  ├─ 法律保留
│  └─ 待审批清除
├─ Lifecycle Rail
│  RECYCLED -> RETAINED -> HELD / ELIGIBLE -> RESTORE / PURGE REQUEST
├─ Entries Table / Mobile Cards
├─ Detail Drawer
├─ Restore Dialog
├─ Legal Hold Dialog
├─ Purge Approval Dialog
└─ Retention Policy Panel
```

UI 继续使用 TDesign React 与 TDesign Icons；仅在缺少非核心视觉时使用 Morphicons/Uiverse，避免把企业控制台做成营销页。
