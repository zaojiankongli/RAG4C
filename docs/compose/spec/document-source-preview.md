---
feature: document-source-preview
status: delivered
base: 6727e1f
commits: 0a825f6..pending（后端表+端点、前端查看面各一条）
date: 2026-09-22
---

# 受控原文查看（source-preview）

## 1. 为什么做

目标里写着"借鉴 WeKnora：文档分开**可编辑**和**可查看**"。可编辑那一半已经落地（切片生命
周期 API + 合并视图 + 修订历史）。可查看这一半今天是**界面自己承认的空白**：
`frontend/src/parse-intervention/components/ParsedContextPane.tsx` 末尾那条 Alert 原文是
"当前没有原始文档预览能力……建议后端未来提供受控 source-preview 端点"。

本轮勘察（Explore 报告，逐条带 file:line）确认了三件约束这件事的事实：

1. **没有字节级存储抽象**。`core/storage_backends.py:91` 的 `StorageProviderSpec` 只是
   配置校验表；七个提供方里只有 `local` 有探针，远端六个标注 `validated_config_only` +
   "no object-store SDK wired"（`:260`）。
2. 原文位置是 `documents.file_path`（`models/orm.py:6108`，String(1024)）——**入库时由操作
   员提供的绝对本地路径**，`server/documents.py:1132` 只校验 `is_file()` 就收下。
3. 全仓**没有任何端点读过 `file_path`**；唯一的二进制响应先例是审计导出
   （`server/enterprise_compliance_api.py:346`，`export_root=Path(export_root())` 收口）。

所以这件事的难点不是"能不能显示 PDF"，而是"怎么在不新增越权面与路径穿越面的前提下显示"。

## 2. 设计（三块，各自可测）

### 2.1 `core/source_previews.py` — 可查看后缀的声明表

`SourcePreviewSpec(suffix, kind, media_type, inline_renderable, max_bytes)`：一行声明"这个
后缀能不能 inline、以什么 media_type 给、多大封顶"。**表按后缀精确建键**，因为 content type
属于后缀而不属于家族：五个图像扩展名是一个 kind、五个 media_type，把 JPEG 标成 `image/png`
在 `nosniff` 之下是浏览器不会原谅的 bug。

- 内建：`.pdf`（inline）、`.png/.jpg/.jpeg/.gif/.webp`（inline 图像，各按真实 media_type）、
  `.txt/.md/.csv/.json`（inline 文本）、`.docx/.xlsx/.pptx`（只允许下载，不 inline）。
- 注册期判死：非小写/不带点的后缀、空 kind、`type/subtype` 形状不对、`max_bytes<=0`、
  同后缀重名（要 `replace` 才改派）、`inline_renderable=True` 但 media_type 不在可 inline
  白名单（`text/html`、`image/svg+xml` 一律不许 inline —— 它们会带脚本）。
- 没登记的后缀 → `source_preview_unsupported_kind`，**不**降级成 `application/octet-stream`
  内联。这是这张表存在的理由：默认值必须是"不知道就别给"。

### 2.2 落点收口：`source_preview_roots`

- 新增配置项 `settings.source_preview_roots: list[str]`，**默认为空 = 功能关闭**，端点报
  `source_preview_disabled`（诚实的 503 语义，不假装成功）。
- 解析规则：把 `documents.file_path` 与每个 root 都 `resolve()` 之后要求
  `candidate.is_relative_to(root)`；不在任一 root 内 → `source_preview_out_of_scope`。
  两侧都 resolve 是为了挡符号链接跳出（root 内的软链指向 root 外）。
- 只跟随一次：不 `open()` 之前不 stat 之外的任何路径；`kind` 由 resolve **之后**的路径决定。

### 2.3 端点

`GET /api/knowledge-bases/{dataset_id}/documents/{document_id}/source-preview[?disposition=attachment]`

- 授权照抄同域既有读端点的形状：`require_knowledge_permission(KNOWLEDGE_READ,
  resolve_path_dataset("dataset_id"))`（`server/knowledge_chunks_api.py:38`），文档行用
  `tenant_id + dataset_id + id` 三条件取（`:102`）——**不用** `catalog.get_document`，它没有
  租户过滤（`core/catalog.py:1981`）。
- 响应头：`Content-Type` 只来自声明；`X-Content-Type-Options: nosniff`；
  `Content-Disposition` 默认 `inline` 且仅当 `inline_renderable`，否则 `attachment`；
  `?disposition=attachment` 只能把 inline 降级为下载，不能反向；`ETag: "<content_revision>-<file_hash[:12]>"`；
  `Cache-Control: private, must-revalidate`。
- 超过 `max_bytes` → 拒（不截断：截断的 PDF 会渲染成"看起来正常"的残页）。

### 2.4 前端

`ParsedContextPane` 那条 Alert 换成真的预览面：fetch（带 Authorization 头）→ blob →
`URL.createObjectURL`，和审计导出/问答导出同一套路（`enterpriseComplianceApi.ts:284`）。
**不做**带 token 的裸 URL 给 `<iframe src>`。PDF 用 `<iframe sandbox="">`，图像用 `<img alt>`，
下载型给一个下载按钮；关闭/越权/超限/类型不支持四种状态各一句人话。
切走或重取时 `revokeObjectURL` 并 abort 上一个请求（沿用 `revisionsRef` 那个形状）。

## 3. 验收判据（沿用 `backend-extensibility-standard.md` §3）

1. §3.1：注册一种新可查看来源，宿主文件逐字节不变 **且实时**照它给 media_type/disposition
   （真打一次端点，不是只查表）。
2. §3.2：形状判死 + 重名要 `replace`。
3. §3.4 反向验证，至少这些变异必须各自有名字地转红：去掉 root 收口、去掉 `is_relative_to`
   两侧 resolve、`Content-Disposition` 不看 `inline_renderable`、未知扩展名回退
   octet-stream、去掉大小上限、用 `catalog.get_document` 取行（跨租户读必须红）。
4. 安全用例：跨租户、跨数据集、`../` 穿越、root 内软链跳出 root、超限、HTML/SVG 想 inline、
   功能未配置 roots。

## 4. 明确不做

- 不做对象存储 SDK 接线（六个远端提供方仍是 `validated_config_only`），不做分片/Range 流式，
  不做在线编辑原文，不做 Office 渲染。
- 不改 `documents.file_path` 的语义（那是既有入库契约）；本期只**读**，且只在配置了 roots
  时读。
