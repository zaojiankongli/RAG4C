# RAG4C 企业目录升级运维手册

> **文档日期：2026-08-29**<br>
> **运维时区：Asia/Shanghai**<br>
> **适用环境：RAG4C 企业目录（真实 MySQL）**<br>
> **文档状态：审批前运行手册；本轮仅完成只读核对，未执行真实备份、迁移或恢复。**

## 0. 变更结论（先读）

本手册对应的真实数据库状态快照如下：

- 当前 Alembic revision：`0007_chunk_rev`
- Stage20 当前目标 head：`0030_enterprise_release_quality_certification`；Stage19 previous target：`0029_enterprise_knowledge_base_releases`
- 只读预检结果：`3` 个 `active` tenant、`0` 条 member、`0` 条 owner、`3` 个 active ownerless tenant
- 结论：**0016 必须 fail-closed；当前不得执行真实升级。**
- 本轮明确未执行：真实 `mysqldump`、真实数据库写入、Alembic migration、downgrade、生产恢复、Workspace Authorization policy backfill、Enforced 激活或 approval execution。

**本轮生产禁止项：禁止自动生产迁移；禁止自动回滚；禁止自动写库。** 本手册只允许生成计划、执行只读预检、校验离线备份文件和描述经审批后的人工步骤；本轮不得执行 `upgrade --execute`、Alembic downgrade、生产 restore、Workspace backfill、成员关系写入或 Dataset 重新绑定；不执行真实 Workspace Authorization policy backfill，不执行真实 Enforced 激活，不执行真实 Workspace Authorization approval execution。

`0016_enterprise_membership` 会在自身第一条 DDL 之前检查既有 membership 数据，至少拒绝以下情况：非法/空角色、重复 tenant-account 关系、孤儿 account/tenant 关系，以及 active tenant 没有 owner。当前三个 active tenant 都没有 owner，因此不能用“先迁移、再补数据”的方式绕过闸门。

**必须先经过审批，使用真实且可审计的 owner/member 账号显式 provision 真实 tenant membership。不得自动伪造 owner、member、account ID、邮箱或 tenant 关系；不得为了让预检变绿而写入合成数据。** 本升级工具没有 provisioning 命令，provision 必须走已经批准的身份/租户 bootstrap 流程，并保留审批单、操作者、时间、tenant ID、account ID、role 和证据。

> **重要风险边界**：Stage19 从 `0007_chunk_rev` 升级到 `head` 包含二十二个迁移步骤；Stage18 的二十一个步骤仍作为历史基线保留。`0016` 自身在 DDL 前 fail-closed，但这不等价于把此前已经执行并提交的 `0008`–`0015` 自动回滚；后续任一迁移失败也不会自动回滚更早已提交的 revision。真实执行前必须完成备份、恢复演练、membership 审批和二次预检；工具不会自动 rollback 或 restore。

## 1. 工具与不可违反的安全规则

工具文件：`scripts/enterprise_catalog_upgrade.py`。以下规则来自该工具的实际行为：

1. `preflight` 只做 URL、schema、角色/owner、Stage19 `knowledge_base_releases`、Stage20 `release_quality` 和磁盘检查，返回 `read_only: true`，不会创建备份目录、探针文件、Release Channel、Release Manifest、Release Quality authority 或修改数据库。
2. `backup-command` 只渲染安全引用的 `mysqldump` 命令，**不会执行 `mysqldump`**。
3. `verify-backup` 只读取备份文件，校验文件存在性、基本 mysqldump 标记和 SHA-256，**不会连接数据库**。
4. `plan` 只读取本地 Alembic migration graph，输出步骤和人工回滚点，**不会连接或写数据库**。
5. `upgrade` 默认是 dry-run；只有显式传入 `--execute`，并同时通过备份校验、expected revision、URL/target label、二次预检和执行前 revision 检查，才会调用 Alembic `upgrade head`。
6. `rollback` 只能生成人工回滚/恢复步骤；不会执行 `downgrade`，不会自动恢复备份，也没有可用于自动回滚的 CLI 开关。
7. 所有密码不得放在命令输出、日志或工单正文中。脚本会输出不含凭据的 URL label；生成的备份命令要求密码只存在于进程环境或受控的 MySQL defaults 文件中。
8. 任何 safety gate 失败都应停止变更。不要用 `--execute` 重试来“碰运气”，先处理 `reasons`、审批或数据问题。

### 1.1 运行位置与凭据约定

以下命令假设从工作区根目录运行：

```text
D:\program_project\python_project\RAG4C
```

`--url` 的默认值来自环境变量 `RAG4C_CATALOG_URL`；`--target-label` 的默认值来自 `RAG4C_CATALOG_TARGET_LABEL`。推荐在受控的运维进程环境中设置变量，而不是把带密码的 SQLAlchemy URL 写进 shell history、CI 日志或变更单：

```powershell
$env:RAG4C_CATALOG_URL = '<由批准的密钥系统注入的 SQLAlchemy MySQL URL>'
$env:RAG4C_CATALOG_TARGET_LABEL = 'production'
```

允许的 target label 是：`test`、`development`、`staging`、`production`。真实数据库本次按 `production` 处理。`inspect_url_safety` 要求 MySQL、host、database 和有效 target label；系统数据库（例如 `mysql`、`information_schema`、`performance_schema`、`sys`）会被拒绝。

若必须显式传递 URL，参数名只能是脚本实际支持的 `--url`；不要自行发明 `--database`、`--dsn` 或其它别名。生产环境仍优先使用环境变量以减少凭据泄露面。

### 1.2 返回码约定

脚本的 CLI 返回码按实际实现解释：

- `0`：命令成功；对 `preflight` 表示 `safe_to_upgrade: true`，对 `verify-backup` 表示备份已验证，对 `plan`/`backup-command` 表示计划或命令已生成；`upgrade` dry-run 也会返回 `0`。
- `1`：备份验证失败，或发生未被 safety gate 识别的异常。
- `2`：显式安全闸门阻断，例如预检不通过、参数缺失、URL 不安全或尝试自动 rollback。

## 2. 维护窗口与进入条件

### 2.1 推荐维护窗口

Stage18 历史基线：从 `0007_chunk_rev` 到 `0028_enterprise_knowledge_base_registry` 有 21 个迁移步骤。工具在没有显式传入窗口时按 `max(30, 15 + 8 × 步骤数)` 计算，即 `15 + 8 × 21 = 183`，本历史链路默认得到 `183` 分钟。

Stage19 当前目标：从 `0007_chunk_rev` 到 `0029_enterprise_knowledge_base_releases` 有 22 个迁移步骤，默认维护窗口为 `15 + 8 × 22 = 191` 分钟。正式变更单建议批准 **至少 360 分钟**，并预留以下阶段：

Stage20 增量目标：从 `0029_enterprise_knowledge_base_releases` 到 `0030_enterprise_release_quality_certification` 有 1 个迁移步骤。工具按 `max(30, 15 + 8 × 1)` 计算默认窗口，即 `30` 分钟；由于 Stage20 还需要逐 Tenant 做 Release Quality authority、Approval waiver action 和 high/default Channel policy coverage 审计，正式变更单仍建议保留独立审批、备份/恢复演练和足够的人工验证时间。

1. 变更前冻结写入、API、worker 和定时任务。
2. 生成并核验完整备份。
3. 在隔离实例完成恢复演练并留证。
4. 重跑只读预检和迁移计划。
5. 执行 Alembic upgrade。
6. 完成 schema、数据、应用、管理端和监控验证。
7. 作出继续恢复服务或进入人工 rollback 的决定。

维护窗口必须记录 Asia/Shanghai 的开始时间、预计结束时间、实际结束时间、值班 DBA、应用负责人和回滚决策人。脚本 `plan` 返回的 `generated_at` 使用 UTC ISO-8601（带 `+00:00`）；归档时保留原始值，并在变更记录中另记 Asia/Shanghai 时间。

### 2.2 开始前硬性条件

在以下条件全部满足前，不得使用 `upgrade --execute`：

- 三个 active ownerless tenant 已通过审批完成真实 owner/member provision；每个 active tenant 至少有一个有效、可登录、可审计的 owner。
- membership 中不存在非法/空 role；允许的 role 只有 `owner`、`admin`、`editor`、`member`。
- 不存在重复的 `(tenant_id, account_id)` membership；不存在指向不存在 account 或 tenant 的孤儿关系。
- 真实数据库仍确认是 `0007_chunk_rev`，且变更单中的 expected current 与数据库一致。
- 备份命令已由 DBA 审核，备份文件已经生成，SHA-256 已记录并通过 `verify-backup`。
- 恢复演练在隔离环境成功，恢复证据可由 DBA 和应用负责人复核。
- `preflight` 返回 `safe_to_upgrade: true`，并且 `read_only: true`；不能仅凭“schema status 是 behind”继续。
- `plan` 输出仍是从 `0007_chunk_rev` 到 `0028_enterprise_knowledge_base_registry` 的二十一步链路。
- 维护窗口、写入冻结、监控、回滚决策人和双人复核已在审批单中确认。

## 3. 阶段 A：只读预检（当前应当阻断）

备份目录的父目录应由运维人员在变更前按组织标准准备好；工具不会创建目标目录或探针文件。下面命令只读取数据库和磁盘状态：

```powershell
& .\.venv\Scripts\python.exe scripts\enterprise_catalog_upgrade.py preflight `
  --target-label production `
  --backup-dir 'D:\secure\rag4c-backups' `
  --expected-current 0007_chunk_rev
```

`--min-free-bytes` 是可选参数，默认值为 `0`；如变更单有明确磁盘余量阈值，可以追加实际批准的正整数，例如：

```powershell
& .\.venv\Scripts\python.exe scripts\enterprise_catalog_upgrade.py preflight `
  --target-label production `
  --backup-dir 'D:\secure\rag4c-backups' `
  --min-free-bytes <审批单中的最小剩余字节数> `
  --expected-current 0007_chunk_rev
```

预检重点查看以下 JSON 字段：

- `revision` 必须是 `0007_chunk_rev`。
- `head_revision` 必须是 `0029_enterprise_knowledge_base_releases`；Stage18 的 `0028_enterprise_knowledge_base_registry` 仅用于历史核对。
- `schema.status` 在当前阶段应为 `behind`，但这本身不是放行条件。
- `owners.active_tenants` 应为 `3`，在 provision 完成后 `owners.ownerless_active_tenants` 必须为 `0`。
- `roles.unknown` 必须为空；当前没有 member 行，因此 role=member 和 role=owner 均为 `0`。
- `disk.safe` 必须为 `true`；目标目录可以尚不存在，但其父目录必须存在、是目录、可写且磁盘空间可检查。
- `safe_to_upgrade` 必须为 `true`，`read_only` 必须为 `true`。

本次真实快照的关键事实是：`active_tenants=3`、`tenant_members` 总行数为 `0`、`owner=0`、`member=0`、`ownerless_active_tenants=3`。因此本阶段预期返回非零阻断结果；不要因为 `schema.status=behind` 或目标 head 正确就继续。

### 3.1 Provision 处理方式

预检阻断后，执行以下人工流程，不执行未经批准的 SQL 修补：

1. 由租户/身份负责人确认三个 active tenant 的正式 owner 人选和 account 来源。
2. 由安全/合规负责人批准真实 account 与 tenant 的绑定范围、角色和生效时间。
3. 通过组织批准的 bootstrap/身份管理流程显式创建或关联 owner/member；记录真实 `tenant_id`、`account_id`、角色、操作者和审批单号。
4. 对可选 member 逐一确认业务授权；没有批准的账号不要为了“填满目录”而加入。
5. 重新做只读数据核对：owner 数量、有效 account/tenant 关系、无重复关系、无未知角色、无 ownerless active tenant。
6. 再次运行本节的 `preflight`；只有 `safe_to_upgrade: true` 才能进入下一阶段。

**禁止**：脚本自动插入 owner/member、使用虚构邮箱或 ID、把一个共享账号伪装成多个 owner、把非 active tenant 当作合规证明、删除问题关系来让检查通过，或在真实数据库上直接执行“临时修复 SQL”而不留审批和备份证据。

## 4. 阶段 B：生成并审核备份命令

`backup-command` 只生成命令，不运行 `mysqldump`。Windows PowerShell 的实际参数如下：

```powershell
& .\.venv\Scripts\python.exe scripts\enterprise_catalog_upgrade.py backup-command `
  --output 'D:\secure\rag4c-backups\rag4c-0007-20260826.sql' `
  --shell powershell `
  --defaults-extra-file 'C:\secure\mysql-client.cnf'
```

如果不使用 defaults file，可以省略 `--defaults-extra-file`：

```powershell
& .\.venv\Scripts\python.exe scripts\enterprise_catalog_upgrade.py backup-command `
  --output 'D:\secure\rag4c-backups\rag4c-0007-20260826.sql' `
  --shell powershell
```

工具渲染出的命令包含以下实际 mysqldump 选项：

- `--single-transaction`
- `--quick`
- `--routines`
- `--triggers`
- `--events`
- `--hex-blob`
- `--set-gtid-purged=OFF`
- host、port、user 和 database（从 MySQL URL 中读取）
- PowerShell 输出目标：`| Out-File -FilePath '<output>'`

DBA 必须人工复核生成结果后再执行。密码不得出现在生成命令或日志中；工具的输出只会给出不含凭据的连接 label。若采用 `MYSQL_PWD`，只在受控进程环境中短时注入；若采用 `--defaults-extra-file`，文件权限和内容按组织的 MySQL 客户端密钥管理规范执行。

在审批前后都要明确区分：

- `backup-command`：本工具执行，**只打印**。
- `mysqldump`：DBA 在批准后人工执行，才会产生备份文件。
- 本轮状态：**真实 mysqldump 尚未执行**。

## 5. 阶段 C：备份 SHA-256 与恢复演练

### 5.1 SHA-256 核验

DBA 生成备份后，先在受控主机上取得文件摘要，再让工具校验文件和摘要：

```powershell
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath 'D:\secure\rag4c-backups\rag4c-0007-20260826.sql').Hash.ToLowerInvariant()
& .\.venv\Scripts\python.exe scripts\enterprise_catalog_upgrade.py verify-backup `
  --file 'D:\secure\rag4c-backups\rag4c-0007-20260826.sql' `
  --sha256 $hash
```

也可以把审批单中已经核对过的 64 位十六进制摘要传给 `--sha256`：

```powershell
& .\.venv\Scripts\python.exe scripts\enterprise_catalog_upgrade.py verify-backup `
  --file 'D:\secure\rag4c-backups\rag4c-0007-20260826.sql' `
  --sha256 '<64 个十六进制字符的 SHA-256>'
```

通过条件：

- `verified: true`
- `exists: true`、`is_file: true`、`size_bytes > 0`
- `sha256_match: true`
- `dump_markers: true`
- `reasons: []`

工具只检查基本标记：样本中有 `-- MySQL dump`，并且有 `CREATE TABLE`、`INSERT INTO` 或 `SET NAMES` 之一，或有 `-- Dump completed`。这不是可恢复性的证明；摘要不匹配、文件为空、不是普通文件或标记缺失都必须停止。

### 5.2 隔离环境恢复演练

恢复演练必须在与生产隔离的临时 MySQL 实例完成，不得把演练导入真实生产库，也不得把演练实例误接入生产应用。DBA 应按以下顺序留证：

1. 记录临时实例的 MySQL 版本、字符集、时区、SQL mode、存储引擎和磁盘容量。
2. 使用已核验 SHA-256 的 SQL 文件，按组织批准的 MySQL 客户端/恢复流程导入隔离实例；恢复命令不由本工具自动生成或执行。
3. 只读查询 `alembic_version`，确认备份对应的当前 revision 为 `0007_chunk_rev`。
4. 抽样核对 `tenants`、`tenant_members`、`accounts`、`datasets`、`documents` 等关键表的行数和关键关系；特别记录当前三个 active tenant 的 ownerless 状态，避免把演练数据误认为已完成 provision。
5. 在隔离环境执行应用只读启动/健康检查，验证连接、字符集、核心目录查询和备份读取能力。
6. 如需验证完整升级链路，使用明确标记的非生产 fixture 或经审批的隔离 bootstrap 数据；不得把真实生产账号复制成未标记的合成 owner/member，也不得把隔离结果写回生产。
7. 记录恢复耗时、导入错误、校验结果、抽样 SQL、操作者和证据位置；演练不通过时不得进入 `upgrade --execute`。
8. 演练结束后销毁或按保留策略隔离临时实例，清理临时凭据和未加密副本。

本节的“恢复演练成功”与 `verify-backup` 是两个独立门槛：前者验证实际可恢复性，后者验证文件和摘要的一致性。

## 6. 阶段 D：dry-run 迁移计划

在真实升级前运行：

```powershell
& .\.venv\Scripts\python.exe scripts\enterprise_catalog_upgrade.py plan `
  --from 0007_chunk_rev `
  --to head `
  --maintenance-window-minutes 360
```

`--from`、`--to` 和 `--maintenance-window-minutes` 是脚本实际支持的参数；`--to head` 会解析到当前本地 migration head。计划必须保留 JSON 输出作为变更证据，并确认：

- `from_revision` 为 `0007_chunk_rev`。
- Stage19 历史计划的 `to_revision` 为 `0029_enterprise_knowledge_base_releases`；当前 `head_revision` 为 `0030_enterprise_release_quality_certification`。
- `maintenance_window_minutes` 为审批值（本手册 Stage19 示例为 `360`；脚本未显式传值时按二十二个步骤默认计算为 `191`）。
- `automatic_rollback` 为 `false`。
- `manual_rollback_only` 为 `true`。
- 每一步都有 `rollback_point`，且没有任何“自动回滚”承诺。

当前 Stage19 链路的二十二个 revision 顺序必须是：

1. `0008_governance` — `0008_knowledge_governance.py`：knowledge governance folders、tags 和 audit facts。
2. `0009_content` — `0009_knowledge_content.py`：document versions、QA knowledge 和统一 lifecycle truth。
3. `0010_dataset_profile` — `0010_dataset_profile.py`：knowledge-base dataset profiles。
4. `0011_durable_delete` — `0011_durable_document_delete.py`：durable document deletion request authority。
5. `0012_retrieval_experiments` — `0012_retrieval_experiments.py`：retrieval experiment 和 reviewer judgment authority。
6. `0013_source_control` — `0013_source_control.py`：source control-plane idempotency 和 retry authority。
7. `0014_source_schedules` — `0014_source_schedules.py`：fixed-interval source schedules。
8. `0015_document_catalog_indexes` — `0015_document_catalog_indexes.py`：enterprise document catalog query indexes。
9. `0016_enterprise_membership` — `0016_enterprise_membership.py`：enterprise member lifecycle 和 tenant-scoped audit history。
10. `0017_enterprise_access_graph` — `0017_enterprise_access_graph.py`：organization units、user groups、invitations 与 dataset ACL read model。
11. `0018_organization_membership` — `0018_organization_membership.py`：租户安全的组织单元成员归属，为组织 ACL 匹配提供权威关系。
12. `0019_dataset_acl_control` — `0019_dataset_acl_control.py`：持久化 Dataset ACL 模式与修订；将已有 active grant 的 Dataset 安全回填为 `dataset_acl`；创建与业务变更同事务的 ACL 幂等账本。
13. `0020_tenant_invitation_lifecycle` — `0020_tenant_invitation_lifecycle.py`：补齐邀请创建、链接轮换、撤销与接受所需的生命周期字段、约束和通用租户 mutation 幂等账本。
14. `0021_enterprise_identity_federation` — `0021_enterprise_identity_federation.py`：新增可信域名、OIDC/SAML 配置与 SCIM token 控制面，并保持外部登录 runtime 为 not connected。
15. `0022_scim_provisioning_data_plane` — `0022_scim_provisioning_data_plane.py`：新增 SCIM User/Group link authority、token 使用证据与 Users/Groups provisioning data plane。核心表为 `tenant_scim_user_links` 与 `tenant_scim_group_links`。
16. `0023_enterprise_audit_compliance` — `0023_enterprise_audit_compliance.py`：新增审计留存策略、法律保全与可校验导出作业控制面；Stage 11 保持 manual execution only。
17. `0024_oidc_sso_runtime` — `0024_oidc_sso_runtime.py`：新增 OIDC 登录事务、subject link 与 SSO session 权威数据；仅持久化 state/nonce/session token digest 和加密 PKCE verifier。
18. `0025_enterprise_approval_control` — `0025_enterprise_approval_control.py`：新增审批规则、适用审批人、审批申请与不可变决策权威；执行授权票据仅保存 digest，下游执行适配器保持显式未连接。
19. `0026_enterprise_workspace_control` — `0026_enterprise_workspace_control.py`：新增 `tenant_workspaces`、`tenant_workspace_members` 与 `tenant_workspace_datasets` 权威关系；为每个既有 Tenant 创建确定性默认 Workspace `workspace-default-{tenant_id}`，并只按真实 Dataset 与 active TenantMember 事实回填 primary binding 和 Workspace member，不生成账号、owner 或成员关系。
20. `0027_enterprise_workspace_authorization` — `0027_enterprise_workspace_authorization.py`：新增 `tenant_workspace_authorization_policies`，扩展审批 action `workspace_authorization_mode_change`，将 active Workspace 确定性回填为 `shadow`、archived Workspace 回填为带迁移证据的 `disabled`；迁移不得生成 `enforced` policy，不得伪造成员、binding 或审批事实。
21. `0028_enterprise_knowledge_base_registry` — `0028_enterprise_knowledge_base_registry.py`：新增 `dataset_workspace_ownerships` 与 `app_dataset_references`，只从 active primary binding 回填 ownership，保留 shared association，扩展 `dataset_workspace_transfer` approval action；不得从 Workflow JSON 猜测 App reference。
22. `0029_enterprise_knowledge_base_releases` — `0029_enterprise_knowledge_base_releases.py`：新增 Tenant-scoped Release Channel、immutable Release Manifest/Entry/Event、Dataset Channel binding 和 Application release mode/pin compatibility projection；迁移只读现有 Tenant/Dataset/App 事实并按 Tenant 确定性生成默认 Channel，不能由本工具自动执行生产回填。

计划命令只读本地 migration 文件，不会改变真实数据库。计划不通过、revision 顺序异常或本地 head 与审批单不一致时，停止并重新审查代码版本。

## 7. 阶段 E：升级执行（仅审批后）

### 7.1 先跑 upgrade dry-run

不带 `--execute` 的命令只返回计划和 safety gates，不调用 Alembic：

```powershell
& .\.venv\Scripts\python.exe scripts\enterprise_catalog_upgrade.py upgrade `
  --expected-current 0007_chunk_rev `
  --target-label production
```

期望看到：

- `mode: "dry-run"`
- `executed: false`
- `safety_gates.execute_required: true`
- `safety_gates.backup_sha256_required: true`
- `safety_gates.expected_current_required: true`
- `safety_gates.second_preflight_required: true`

### 7.2 真实执行命令

只有审批单明确授权、owner/member provision 已完成、备份和恢复演练均通过、最近一次预检为 `safe_to_upgrade: true` 时，才允许使用以下命令：

```powershell
& .\.venv\Scripts\python.exe scripts\enterprise_catalog_upgrade.py upgrade `
  --execute `
  --backup 'D:\secure\rag4c-backups\rag4c-0007-20260826.sql' `
  --backup-sha256 '<已通过 verify-backup 的 64 位十六进制摘要>' `
  --expected-current 0007_chunk_rev `
  --target-label production
```

真实执行的硬性参数是：

- `--execute`：没有它不会调用 Alembic；有它才进入写操作路径。
- `--backup`：必须指向已经存在的普通备份文件。
- `--backup-sha256`：必须是 64 位十六进制摘要，且必须通过 `verify_backup`。
- `--expected-current 0007_chunk_rev`：必须与执行前数据库 revision 相等；变化即阻断。
- `--target-label production`：必须是允许的 target label。

工具在调用 Alembic 前会重新执行预检并再次读取当前 revision；预检不通过或 revision 在执行前发生变化，直接阻断。调用的 Alembic 目标是脚本内部的 `head`，当前 head 为 `0030_enterprise_release_quality_certification`；成功后工具要求重新读取到该 revision。工具不会在异常后自动 rollback。

> **本轮禁止执行此命令。** 由于真实数据库仍有三个 active ownerless tenant，当前不满足 `safe_to_upgrade`，而且本轮没有真实备份摘要和恢复演练证据。

## 8. 阶段 F：升级后验证

升级命令即使返回成功，也只代表脚本完成了它声明的 revision/备份/预检门槛；值班人员仍须完成以下人工验证，并把结果附在变更单：

### 8.1 版本与结构

- Stage19 历史验证只读确认 `alembic_version` 为 `0029_enterprise_knowledge_base_releases`；当前 Stage20 完成后的目标为 `0030_enterprise_release_quality_certification`。
- 确认 `tenant_members` 有 `status`、`revision`、`updated_at`、`updated_by`、`suspended_at`、`suspended_by`。
- 确认 `tenant_members` 的 role 约束只允许 `owner`、`admin`、`editor`、`member`；status 约束只允许 `active`、`suspended`；revision 为正数。
- 确认 tenant member 查询索引已存在：`ix_tenant_members_tenant_status_role_id`、`ix_tenant_members_tenant_account_status`。
- 确认 `tenant_audit_events` 已建立，包含唯一事件 ID、tenant 外键、sequence 主键以及按 tenant/time、actor、resource、target、request 的索引。
- 确认 `datasets` 已包含 `acl_mode`、`acl_revision`、`acl_enabled_at`、`acl_enabled_by`，并验证模式枚举与正修订约束。
- 确认 `dataset_acl_mutation_requests` 已建立，包含 tenant/dataset/actor 范围外键、`(tenant_id, actor_id, idempotency_key)` 唯一约束、状态约束和查询索引。
- 对 0018 升级前已有 active grant 的 Dataset 逐项核对：升级后 `acl_mode=dataset_acl`、`acl_revision=1`；没有 active grant 的 Dataset 保持 `tenant_role`。不得出现已有 ACL 关系却回退到租户角色的授权扩大。
- 确认幂等键长度约束为 1–128，并验证空键和 129 字符键被数据库层拒绝。
- 确认 `tenant_workspaces` 已建立 tenant-scoped code/name unique、active default slot、`active | archived` lifecycle、environment 和正 revision 约束。
- 确认 `tenant_workspace_members` 已建立 tenant/workspace/account 复合 FK、`owner | admin | editor | viewer` role、`active | removed` status、one-active-relation 约束和生命周期证据。
- 确认 `tenant_workspace_datasets` 已建立 tenant/workspace/dataset 复合 FK、`primary | shared` binding kind、one-active-primary-per-Dataset 约束和生命周期证据。
- 对每个升级前既有 Tenant 核对确定性默认 ID `workspace-default-{tenant_id}`；默认 Workspace、成员和 Dataset binding 数量必须可由升级前真实事实解释，不得出现合成 account、owner、member 或跨租户 binding。
- 确认 `tenant_workspace_authorization_policies` 已建立 `(tenant_id, workspace_id)` 唯一约束、tenant-safe Workspace FK、`disabled | shadow | enforced` mode、正 `permission_model_version`/revision、mode evidence exact checks 与 tenant-leading indexes。
- 记录升级前 active/archived Workspace 数量；升级后 active Workspace 必须一一对应 `shadow` policy，archived Workspace 必须一一对应带 `migration:0027` disable evidence 的 `disabled` policy；迁移产生的 `enforced` policy 数必须为 `0`。
- 确认审批 policies/requests 的 action check 已包含 `workspace_authorization_mode_change`，既有审批事实未被重写；任何 Stage17 action row 都必须带 tenant、resource、revision 和摘要证据。
- 确认升级输出中的 `before_revision` 是 `0007_chunk_rev`、`after_revision` 是 `0028_enterprise_knowledge_base_registry`、`automatic_rollback` 是 `false`。

### 8.2 数据与权限

- 三个 active tenant 均有至少一个真实 active owner；owner 数量、member 数量与审批单一致。
- 重新核对无非法角色、重复 membership、孤儿 account/tenant 关系和 ownerless active tenant。
- 抽样验证 owner/admin/editor/member 的目录访问范围；不能因为迁移默认值而扩大租户边界。
- 验证管理端成员目录、角色变更/暂停/恢复能力和 tenant-scoped audit 查询时，审计事件包含 actor、target、request ID 和时间等必需上下文。
- 验证迁移没有产生未批准的账号、角色、租户关系或跨租户可见性。
- 验证每个 Tenant 最多一个 active default Workspace，每个 Dataset 最多一个 active primary Workspace binding；默认 Workspace 拥有 active primary binding 时不得被归档。
- 按真实 TenantMember 映射抽查默认 Workspace 成员：tenant owner → workspace owner、admin → admin、editor → editor、member → viewer；缺失或 inactive membership 不得被推测或补造。
- 验证 Stage 17 采用加法授权：`disabled` 不计算 would-grant，`shadow` 仅生成证据且绝不进入 `effective_permissions`，`enforced` 才将 Workspace 权限与既有 Tenant Role/Dataset ACL 决策取并集；Workspace 角色不得被解释成 Tenant 角色。
- 抽样验证 active TenantMember + active Workspace + active WorkspaceMember + active binding + structurally valid policy 缺一不可；removed member/binding、archived Workspace、suspended TenantMember、跨租户行和缺失 policy 均不得贡献权限。
- 对同一 Dataset 的多个 Workspace 绑定验证权限仅按 eligible Workspace 的最高角色集合取并集；`shadow` 的 `would_grant_permissions` 不得污染 `effective_permissions`。
- 验证 policy/member/binding 任一 revision 或状态变化后下一请求立即反映撤权；不得存在遗漏这些 revision 的 permission decision cache。
- 验证 impact/审计/API 响应仅包含最小化安全证据；不得返回 raw approval ticket、Authorization、Cookie、secret、credential 或跨 Workspace 成员邮箱清单。
- 抽样验证 `dataset_acl` 即使没有 active grant 也保持 deny；只有显式且审计化的停用操作才能回到 `tenant_role`。
- 抽样选择一个升级前已有 active grant 的知识库，以未匹配普通成员验证其仍被拒绝，证明 0019 回填没有扩大访问范围。
- 对同一 actor、同一 `Idempotency-Key` 重放 ACL mutation，确认响应一致且不会重复创建 grant 或审计事件；同 key 不同请求体必须返回冲突。

### 8.3 应用与运行面

- API、worker、定时任务按变更单顺序解除冻结。
- 应用启动、数据库连接池、知识库目录、文档列表、分类/标签查询和企业管理页完成最小 smoke test。
- 观察错误率、数据库锁等待、慢查询、连接池耗尽、队列积压和审计写入失败。
- 在观察窗口结束前，不要删除备份、恢复演练证据或升级日志。

## 9. 回滚与恢复（只生成流程，不自动恢复）

### 9.1 生成回滚计划

工具支持生成人工步骤，但不会执行任何 downgrade 或 restore：

```powershell
& .\.venv\Scripts\python.exe scripts\enterprise_catalog_upgrade.py rollback `
  --current 0028_enterprise_knowledge_base_registry `
  --to 0007_chunk_rev `
  --backup 'D:\secure\rag4c-backups\rag4c-0007-20260826.sql' `
  --backup-sha256 '<已核验的 64 位十六进制摘要>'
```

返回结果应保持：

- `executed: false`
- `automatic_restore: false`
- `automatic_downgrade: false`
- `steps` 中每个 `operator_action` 都是 `Manual:` 人工动作。

CLI 没有 `rollback --execute`；不要添加或猜测这个参数。工具函数本身也会拒绝自动 rollback。生成的 `migration_plan` 用于展示 revision 上下文，不能替代 DBA 的恢复审批。

### 9.2 人工回滚决策与动作

发生升级异常、应用不可用、数据/权限验证失败或审批单定义的阈值被触发时：

1. 人工停止写入、API、worker 和定时任务；保留故障时间线和日志。
2. 人工核对备份文件、SHA-256 和恢复演练记录；不能使用未核验的文件。
3. 由 DBA 按批准单和标准 Alembic 流程人工执行目标 `downgrade 0007_chunk_rev`；本工具不会执行该动作。
4. 只读核验 revision、表结构、应用启动和抽样数据，再由应用负责人决定是否恢复服务。
5. 如果 downgrade 不可接受，由 DBA 按批准单人工恢复备份；本工具不会自动恢复生产数据库，也不会自动重试恢复。
6. 记录回滚开始/结束时间、执行人、命令审计、数据差异、服务影响和后续修复计划。

`0019` 的 downgrade 会删除 ACL 幂等账本并移除 Dataset 的持久 ACL 控制字段；`0016` 的 downgrade 会删除 `tenant_audit_events` 并移除 `tenant_members` 新增的生命周期字段、约束和索引。升级后已经写入的新审计事件或新字段数据可能无法在旧 schema 中保留；是否采用 downgrade 还是恢复备份必须由 DBA、应用负责人和业务 owner 明确决策。**不要在没有备份和审批的情况下直接 downgrade。**

**Stage17 语义回滚门：** 从 `enforced` 回退必须先执行经审批、revision-fenced 的 policy mutation，将目标 Workspace 改为 `shadow`（必要时再到 `disabled`），并验证权限差集和审计；这不是数据库 downgrade。存在 `workspace_authorization_mode_change` policy/request/decision、已执行授权审计或任何依赖 0027 的运行事实时，0027 downgrade 必须 fail-closed，先由 DBA、安全负责人和业务 owner 决定保留 0027、清理测试事实还是恢复备份。不得通过 downgrade 绕过一次性 ticket、revision 或撤权验证。

## 9.3 0020 邀请生命周期专项预检与验证

升级到 `0028_enterprise_knowledge_base_registry` 前必须只读检查：

- 同一 `(tenant_id, normalized_email)` 不存在两条及以上 pending invitation；出现 **duplicate pending** 必须停止，先由授权操作员清理。
- 同一租户不存在重复 `token_hash`；账本和审计中不得出现原始邀请 token 或原始 Idempotency-Key。
- accepted 历史行必须已有 `accepted_at` 与 `accepted_by`。
- pending 行升级后应有 `pending_email_key = normalized_email`；terminal 行必须为 `NULL`。
- `last_sent_at = created_at`、`send_count = 1`、`updated_by = invited_by`。
- legacy revoked 行的 `revoked_at = updated_at`、`revoked_by = invited_by` 只是兼容投影，不是历史审计重建，必须在变更记录中单独标识。
- `tenant_control_mutation_requests` 只保存 64 位 domain-separated digest 和请求 hash，不保存原始 key、Authorization、Cookie 或原始请求体。

升级后验证 `tenant_control_mutation_requests` 的 unique、FK、digest/status checks 与三组 tenant-leading indexes；空缺或约束损坏必须由 readiness 归类为 `tenant_invitation_lifecycle` 并 fail-closed。

## 10. 审批 checklist

### 10.1 数据与身份

- [ ] 已确认变更对象为真实 MySQL，当前 revision 为 `0007_chunk_rev`。
- [ ] 已记录三个 active tenant 的正式 `tenant_id`。
- [ ] 已由授权负责人批准三个 active tenant 的真实 owner；每个 tenant 至少一个 owner。
- [ ] 已通过批准的 bootstrap 流程显式 provision owner/member；没有自动伪造任何账号、ID、邮箱或关系。
- [ ] 已核对 role 只包含 `owner`、`admin`、`editor`、`member`。
- [ ] 已核对无重复 `(tenant_id, account_id)`、无孤儿 account/tenant 关系、无 active ownerless tenant。

### 10.2 备份与恢复

- [ ] 维护窗口按 Asia/Shanghai 记录，Stage19 默认计算为 `191` 分钟，正式审批建议不少于 360 分钟。
- [ ] `backup-command` 输出已由 DBA 审核；明确它只打印命令，不执行 `mysqldump`。
- [ ] 已在批准后人工执行 `mysqldump`，备份文件位于受控路径。
- [ ] 已完成 `verify-backup --file ... --sha256 ...`，`verified: true` 且 `reasons: []`。
- [ ] 已在隔离实例完成恢复演练并归档证据。
- [ ] 生产备份保留期、访问权限、加密和清理责任人已确认。

### 10.3 迁移执行

- [ ] `preflight` 返回 `safe_to_upgrade: true`、`read_only: true`，且 `ownerless_active_tenants: 0`。
- [ ] Stage19 历史计划 `plan --from 0007_chunk_rev --to 0029_enterprise_knowledge_base_releases` 输出二十二个预期 revision；Stage20 增量计划 `plan --from 0029_enterprise_knowledge_base_releases --to 0030_enterprise_release_quality_certification` 输出一个 revision；两者均为 `manual_rollback_only: true`；Stage18 历史记录仍为二十一个预期 revision。
- [ ] 已先运行不带 `--execute` 的 `upgrade` dry-run 并归档 JSON。
- [ ] 已确认没有并发 migration、写入流量或未登记的 worker/定时任务。
- [ ] 已取得 DBA、应用负责人、业务 owner 和变更审批人的执行授权。
- [ ] 执行命令中的 `--backup`、`--backup-sha256`、`--expected-current`、`--target-label` 与审批单一致。
- [ ] 已安排第二位人员在执行前复核命令和摘要；密码未进入命令或日志。

### 10.4 验证与回滚

- [ ] Stage19 历史验证使用 `after_revision=0029_enterprise_knowledge_base_releases`；Stage20 完成后验证 `after_revision=0030_enterprise_release_quality_certification` 以及七张 Release Quality 表的目标列/索引/immutable guards。
- [ ] 已验证 owner/member 权限、租户隔离、管理端和审计读取。
- [ ] 已验证 API、worker、定时任务、监控和队列恢复正常。
- [ ] 已明确回滚决策人、触发阈值和 DBA 人工动作。
- [ ] 已知悉工具不会自动 rollback、不会自动 restore，且已准备人工恢复路径。
- [ ] 已在变更单注明：**截至 2026-08-28 Asia/Shanghai，本轮未执行真实备份、迁移或恢复。**

## 11. 本轮执行记录

截至 **2026-08-28（Asia/Shanghai）**：

- 已阅读并以 `scripts/enterprise_catalog_upgrade.py`、`tests/test_enterprise_catalog_upgrade.py` 的实际 CLI 和 gate 行为编写本手册。
- 已确认真实数据库只读状态为 revision `0007_chunk_rev`、3 个 active tenant、0 member、0 owner、3 个 active ownerless tenant。
- 未执行真实 `mysqldump`。
- 未执行 `verify-backup` 针对真实备份文件的核验，因为本轮没有生成真实备份。
- 未执行真实 Alembic upgrade、downgrade 或任何生产数据库写操作。
- 未执行自动或人工生产恢复。

下一次变更只能从“审批完成、真实 owner/member provision 完成、备份和恢复演练证据齐全”开始，并重新执行本手册的只读预检；不能复用本轮 ownerless 的阻断状态作为放行依据。




## 0021 identity federation 专项验证

验证全局 domain uniqueness、每租户唯一 active primary provider、每租户唯一 active SCIM token name，以及三张表只保存 secret reference、certificate fingerprint 和 token hash。运行时必须明确展示 `runtime_not_connected` 与 `scim_data_plane_not_connected`；这两个状态表示配置控制面可用，但外部 SSO 登录和 SCIM provisioning data plane 尚未接通。工具不会自动迁移、回滚或恢复。


## 0022 SCIM data plane 数据库验证

验证 userName/externalId/account 与 displayName/externalId/group 的 tenant unique，link 到 TenantMember/TenantGroup/SCIM token 的复合 FK，以及 token use_count、last_used_at、last_used_ip_hash 的一致性检查。原始 bearer token 与原始 IP 永不持久化。工具不会自动迁移、回滚或恢复。


## 0023 audit compliance 数据库验证

验证 `tenant_audit_retention_policies` 每租户单一策略及 30–3650/1–365 边界，验证 `tenant_audit_legal_holds` active 名称唯一，验证 `tenant_audit_export_jobs` 生命周期、完整性 hash 与 tenant-scoped requester。运行面必须显示 `manual_execution_only`；Stage 11 不启用自动删除，不执行真实 retention deletion、外部存储写入、自动回滚或恢复。


## 0024 OIDC runtime 数据库验证

验证 state/nonce/session token 仅存 64 位 digest，PKCE verifier 仅存加密密文和 key version；login transaction、subject link、SSO session 的 provider/member scope FK、生命周期与唯一约束必须完整。不得持久化 raw state、nonce、verifier、authorization code、ID/access token 或 raw session token。

## 0025 enterprise approval control 数据库验证

验证 `tenant_approval_policies`、`tenant_approval_policy_approvers`、`tenant_approval_requests` 与 `tenant_approval_decisions` 的 tenant-safe FK、状态与 revision 约束、active policy 唯一性、审批阈值计数和不可变决策唯一性。执行 authorization ticket 仅允许返回一次且数据库只保存 domain-separated digest；request snapshot 不得包含 secret、token、credential、原始邀请链接或其他敏感值。Stage 13 不自动执行 catalog upgrade、retention deletion、ACL disable、身份提供商停用或外部发布。`execution authorization ticket` 仅作为一次性授权边界，未连接 consumer 时必须显示 `execution_adapter_not_connected`。

## 0026 enterprise workspace control 数据库验证

验证 `tenant_workspaces`、`tenant_workspace_members` 与 `tenant_workspace_datasets` 的 tenant-safe composite FK、active default 唯一性、active primary Dataset binding 唯一性、lifecycle/status/revision 约束和查询索引。回填必须使用确定性 ID `workspace-default-{tenant_id}`，只复制真实存在的 Tenant、Dataset 与 active TenantMember 事实，不得伪造账号、owner、成员或跨租户关系。默认 Workspace 拥有 active primary Dataset binding 时不得归档，最后一个 active Workspace owner 保护必须保留。Stage 16 仅记录 Workspace 角色证据；在授权引擎正式消费 Workspace membership 前，运行面必须显示 `workspace_authorization_not_enforced`，不得将 Workspace 角色当作 Dataset ACL 或实际权限。禁止自动生产迁移、禁止自动回滚、禁止自动写库；本轮不执行真实 Workspace backfill、成员变更、Dataset 绑定或外部发布。

## 0027 enterprise workspace authorization 数据库与 rollout 验证

验证 `tenant_workspace_authorization_policies` 的 tenant-safe FK、每 Workspace 单 policy、mode/evidence exact checks、`permission_model_version > 0`、revision fence 和查询索引；状态机必须明确为 `disabled -> shadow -> enforced`。迁移只允许 active Workspace 回填 `shadow`、archived Workspace 回填 `disabled`，禁止迁移直接生成 `enforced`。

只读预检必须记录 active/archived Workspace 数、预计 policy backfill 数、现存 policy mode 分布、缺失/孤儿 policy、重复 Workspace policy、现存 `workspace_authorization_mode_change` 审批 action 行数及 downgrade blocker。升级后逐租户核对计数守恒，并验证 `permission_model_version = 1`。任何结构缺失、未知 mode、未知 model version、policy/member/binding revision 不一致或跨租户关系都必须 fail-closed。

Shadow 观察门要求：至少完成 Permissions rollout、impact preview、existing ACL 对照、即时撤权、multiple Workspace union、archived/removed/suspended 排除和 schema-failure 演练，且 `shadow` 权限不得进入 `effective_permissions`。进入 Enforced 必须逐 Workspace 使用最新 Workspace revision、policy revision、`permission_matrix_fingerprint`、reason 和（命中规则时）一次性 approval ticket；重放不得重复 policy mutation。

运行面验收必须覆盖 direct/hash、light/dark、1440/375/280，检查 Permissions rollout、impact、mode dialog、长状态 token 换行、Drawer/Dialog 视口边界；console error、page error、unknown request 均为 `0`，水平 overflow 为 `false`。DOM、localStorage/sessionStorage、日志和截图中不得出现 raw ticket。

生产禁止：禁止自动生产迁移、禁止自动回滚、禁止自动写库；不执行真实 Workspace Authorization policy backfill，不执行真实 Enforced 激活，不执行真实 Workspace Authorization approval execution，不执行真实 downgrade/restore 或外部发布。


## Stage17 controlled QA command contract

Use the project runtime explicitly:

```text
uv run python scripts/enterprise_catalog_upgrade.py plan --from 0007_chunk_rev --to 0027_enterprise_workspace_authorization --maintenance-window-minutes 175
uv run python output/playwright/enterprise-workspace-authorization-stage17/stage17-acceptance.py
```

The controlled browser entrypoint is `run-stage17-acceptance.ps1`; it writes `stage17-browser-result.json`. The matrix covers direct/hash, light/dark and 1440/375/280. Evidence fields are `console_error_count`, `page_error_count`, `unknown_request_count`, `all_no_horizontal_overflow`, `raw_ticket_visible` and `raw_ticket_persisted`. Before the application head and Permissions Rollout Center are available the result may be `blocked` with the explicit reason `前端未就绪`; it must never fabricate a passed result.

## 0028 enterprise knowledge base registry 数据库与依赖验证

验证 `dataset_workspace_ownerships` 与 `app_dataset_references` 的 tenant-safe composite FK、每 Dataset 单一 ownership、Application reference active 唯一性、lifecycle/revision exact checks、查询索引和确定性 ownership backfill。ownership backfill 只允许来自可证明的 active primary Workspace binding；active shared binding 只保留为 association，不得被解释为 ownership。

只读预检必须记录：Dataset 总数、可证明 primary ownership 数、缺失/孤儿/重复 ownership、active shared association、App 数、active Application reference 数、跨租户或孤儿 reference、现存 `dataset_workspace_transfer` approval action 行数和 downgrade blocker。任何 ownership 缺失、未知引用状态、revision 漂移或跨租户关系都必须 fail-closed。

Workspace ownership transfer 必须使用最新 Dataset profile revision、ownership revision、目标 active Workspace、reason、Idempotency-Key 和（命中规则时）一次性 approval ticket。Application reference mutation 不产生 Dataset permission；active Application reference 是 Dataset archive 的硬 blocker，审批不能绕过该依赖事实。

生产禁止：禁止自动生产迁移、禁止自动回滚、禁止自动写库；不执行真实 ownership backfill，不执行真实 Dataset ownership transfer，不执行真实 Application reference mutation，不执行真实 Dataset archive/delete，不执行真实 approval execution、downgrade/restore 或外部发布。

## Stage18 controlled QA command contract

Use the project runtime explicitly:

```text
uv run python scripts/enterprise_catalog_upgrade.py plan --from 0007_chunk_rev --to 0028_enterprise_knowledge_base_registry --maintenance-window-minutes 183
uv run python output/playwright/enterprise-knowledge-base-registry-stage18/stage18-acceptance.py
```

The controlled browser entrypoint is `run-stage18-acceptance.ps1`; it writes `stage18-browser-result.json`. The exact matrix covers direct/hash, light/dark and 1440/375/280. It must verify Registry, Dependency Rail, Application references, ownership transfer, archive blockers, focus return, console/page/unknown/unexpected request counts, horizontal overflow and raw ticket/secret absence. Before the application head and Enterprise Knowledge Base Center are available the result remains `blocked` with `application_head_not_ready`; it must never fabricate `passed`.


## 0029 Stage19 Knowledge Base Release read-only preflight 与人工步骤

Stage19 的 `knowledge_base_releases` 预检是**只读审计面**，不是 Release 执行器。它只在已有 0029 Release authority 表时读取现状；当 `alembic_version` 仍早于 0029 且五张目标表不存在时，返回 `schema_status=not_available`，不把“目标表尚未迁移”伪装成阻断，也不创建任何默认 Channel。

### Stage19 preflight contract

执行：

```powershell
uv run python scripts/enterprise_catalog_upgrade.py preflight `
  --url $env:RAG4C_CATALOG_URL `
  --target-label production `
  --backup-dir 'D:\secure\rag4c-backups'
```

必须归档 JSON，并逐 Tenant 审核以下证据：

- `active datasets`：只统计 `status='active'` 的 Dataset，并以 `tenant_id:dataset_id` 保存引用；归档或 disabled Dataset 不得混入 Release readiness。
- `ownership/version/index/source-sync/projection readiness`：分别检查每个 active Dataset 的 authoritative Workspace ownership、active retrieval Document current Version、desired/indexed revision、active Source sync run、graph/projection revision；所有 Dataset/Document/Source/Run 关联都必须带 `tenant_id`。
- existing release/channel/binding counts：读取 `dataset_release_manifests`、`dataset_release_entries`、`dataset_release_events`、`tenant_release_channels`、`dataset_channel_releases` 和 active Application references；每个聚合先按 Tenant 分组，再计算展示总数，禁止用裸 `dataset_id` 合并租户数据。
- `malformed App release modes/pins`：active Application reference 的 `release_mode` 只能是 `follow_channel` 或 `pinned`；前者必须指向同一 Tenant 的 active Channel 且不得有 pin，后者必须指向同一 Tenant、同一 Dataset 的 Release 且不得有 Channel。缺失、交叉 Tenant、XOR 违规或非 active 目标都必须列出 scoped reference ID。
- `custom/default channels`：核对每个 active Tenant 的 `development`、`testing`、`production` 默认 Channel、默认 serving slot、重复 normalized code 和自定义 Channel；默认 Channel 缺失或租户内重复都必须进入 `blockers` 与 `reasons`。
- `blockers` 与 `reasons`：必须同时保留机器可判定的 blocker code 和 operator-readable reason。Release readiness、serving Release、binding、Channel 或 App pin 证据不足时 fail-closed。

报告中的 `checked`、`available`、`schema_status`、五类 readiness、各表 count、`tenant_summaries`、scoped issue IDs、`blockers` 和 `reasons` 只是事实投影。该命令不得执行 INSERT、UPDATE、DELETE、DDL、Channel seed、Manifest capture、binding mutation 或任何外部同步。

### Stage19 manual change sequence

1. 先执行 Stage19 `plan`：

   ```text
   uv run python scripts/enterprise_catalog_upgrade.py plan --from 0007_chunk_rev --to 0029_enterprise_knowledge_base_releases --maintenance-window-minutes 191
   ```

   预期为二十二个 revision、`manual_rollback_only=true`；计划只读本地 migration graph。

2. 在审批单中记录 preflight JSON、每个 Tenant 的 readiness 与 blocker 清单。若出现 ownerless、missing ownership、Document Version 缺失、index/projection drift、active Source sync、malformed App release modes/pins 或跨租户引用，停止并由有权限的人工操作员处理；不得通过插入合成事实让预检变绿。

3. 只有 DBA、应用负责人和业务 owner 完成审批、备份和恢复演练后，才可由授权人员人工执行迁移。0029 的默认 Channel seed 只能在迁移审计中做 **manual Channel seed verification**：逐 Tenant 核对三个默认 code、`active_default_slot`、revision 和数量守恒；本工具不创建、不补写、不修复 Channel。

4. 迁移后重新执行 preflight，并确认 0029 authority 表、composite Tenant FK、索引、App release compatibility projection 和 readiness 证据。`Release manifest capture` 必须是单独的、经过审批的业务动作；应记录 Dataset/profile、ownership/Workspace、Document Version、Source generation、Projection revision、digest、request ID 和 actor，但不得保存正文、凭据或 raw ticket。

5. 发布、Channel promotion、rollback 与 Application pin/follow-channel 变更必须分别通过现有 revision fence、幂等和一次性审批规则。preflight 只报告是否具备事实，不会代替 publish approval，也不会自动调用 Release API、Source sync、index worker 或恢复流程。

### Stage19 production prohibitions

截至本轮，明确：

- 不执行真实 migration；
- 不执行真实 default-channel backfill；
- 不执行真实 Release capture；
- 不执行真实 Release promotion；
- 不执行真实 Release rollback；
- 不执行真实 Application pin；
- 不执行真实 approval execution；
- 不执行真实 source sync、ingestion、restore、destruction 或外部发布。

任何生产操作都必须由被授权人员在独立变更单中人工执行并留存审计证据；`enterprise_catalog_upgrade.py preflight` 永远保持 `read_only: true`。


## Stage 19 offline SQL 边界

- MySQL/PostgreSQL 的 `alembic upgrade --sql` 会显式生成三类 default Release Channel 的 `INSERT ... SELECT`，并在添加 Application release binding CHECK/FK 前生成 `app_dataset_references` backfill `UPDATE`；不得删除或跳过这些数据 SQL。
- SQLite 的 0029 batch migration 必须使用在线连接执行；SQLite offline SQL 会在 Alembic 层 fail closed，并提示先完成只读 preflight 后使用 online batch migration。
- offline SQL 仍然不是自动生产执行器：生成后必须人工审核 Tenant 数量、default Channel seed、legacy Application binding backfill 和审批 action CHECK，再进入正式变更窗口。


## 0030 Stage20 Release Quality 只读预检与人工变更边界

Stage20 的目标 revision 是 `0030_enterprise_release_quality_certification`，down revision 是 `0029_enterprise_knowledge_base_releases`。本节是 0030 的审批前 runbook；它不改变 Stage19 的历史核对，也不授权任何真实生产写操作。

### Stage20 preflight contract

`preflight` 在发现 0030 authority 表后，只执行 metadata inspection 和 `SELECT` 查询，返回 `release_quality.read_only: true`、`mutations_performed: false`、`automatic_actions: []`。它不会执行 migration、backfill、INSERT、UPDATE、DELETE、DDL、Release capture、Channel promotion、rollback、Application pin/follow-channel mutation、source sync、restore 或 destruction。

预检必须核验 **七张 Release Quality 表**：

1. `tenant_release_quality_gate_policies`
2. `dataset_quality_baselines`
3. `dataset_quality_baseline_items`
4. `dataset_release_quality_certifications`
5. `dataset_release_quality_certification_evidence`
6. `dataset_release_quality_waivers`
7. `dataset_release_quality_events`

报告 `release_quality` 必须保留以下只读事实：

- 7 tables 的 `missing_tables`、`missing_columns`、`schema_status` 和 `target_revision`；
- `policy_count`、`active_policy_count`、`baseline_count`、`baseline_item_count`、`certification_count`、`certification_evidence_count`、`waiver_count`、`event_count`，以及按 Tenant 分组的 `tenant_summaries`；
- canonical active policy scope：`global:*`、`risk_tier:<low|medium|high>`、`channel:<channel_id>`，并阻断非法 scope 或 duplicate active scope；
- Baseline/Item、Certification/Evidence、Waiver/Event 的 Tenant-leading scope integrity，禁止使用裸 `dataset_id` 跨 Tenant 合并事实；
- missing/stale/expired/revoked/invalid envelope blocker：缺失质量 authority、Release/Policy revision 或 digest 漂移、过期/撤销 Waiver、非法 digest、非法 safe snapshot 或不一致 envelope 都必须 fail-closed；
- `knowledge_base_release_quality_waiver` Approval waiver action 是否同时受到 Approval policy/request schema 支持；
- high/default Channel policy coverage：高风险或 default-serving Channel 必须有唯一、可解析的 active Policy；已有 active Dataset Channel binding 还必须能解析 current Certification 或有效 Waiver。

当 `alembic_version` 仍为 `0029_enterprise_knowledge_base_releases` 且七张 0030 表尚不存在时，`release_quality.schema_status=not_available` 是“目标尚未迁移”的只读事实，不得由工具自动创建任何 authority。迁移后必须再次运行预检；若出现 `release_quality.blockers`，不得以人工补写合成 Policy、Baseline、Certification、Waiver 或 Event 的方式绕过。

### Stage20 manual change sequence

1. 先只读生成 0030 单步计划：

   ```text
   uv run python scripts/enterprise_catalog_upgrade.py plan --from 0029_enterprise_knowledge_base_releases --to 0030_enterprise_release_quality_certification
   ```

   计划必须显示 `0030_enterprise_release_quality_certification.py`、`manual_rollback_only=true`，并归档原始 JSON。工具只读本地 Alembic graph，不连接或修改数据库。

2. 在审批单中归档 0029 preflight、备份校验、恢复演练和写入冻结证据。确认 Tenant、Dataset、Release Manifest/Entry、Channel、Approval schema 的事实已经由授权人员核验；预检不得替代业务审批。

3. 由 DBA 在批准的维护窗口人工审阅 offline SQL。Stage20 migration 创建表、约束、索引、immutable guards，并同步 Approval waiver action；它不是 Policy/Baseline/Certification/Waiver/Event 数据初始化器。任何 SQL 必须先做语法、锁、执行时长和回滚影响评审，再由授权 DBA 执行。

4. 0030 执行后重新运行只读 preflight，逐 Tenant 核对七张表的 counts、Tenant scope、canonical active Policy scope、Approval waiver action、high/default Channel policy coverage 和所有 blocker。`safe_to_upgrade` 在已经到达 head 时不再代表“可以再次升级”；此时以 `release_quality` 的事实和 blocker 作为 Stage20 质量审计结果。

5. Policy 创建、Baseline capture、Certification、Waiver request/Approval execution、Release promotion、rollback 和 Application pin 都是独立的、经过授权的业务动作。必须使用各自 API 的 revision fence、幂等键、Approval 和审计链路；本升级工具永远不代执行。

### Stage20 production prohibitions

本手册和 `enterprise_catalog_upgrade.py preflight` 明确禁止：

- 不自动创建 Policy/Baseline/Certification/Waiver/Event；
- 不执行真实 0030 migration；
- 不执行真实 0030 backfill；
- 不执行真实 Release capture、promotion 或 rollback；
- 不执行真实 Application pin/follow-channel mutation；
- 不执行真实 source sync、ingestion、restore、destruction 或外部发布；
- 不通过插入伪造的质量事实、伪造 Approval execution fact 或手工改写 immutable authority 来消除 blocker。

### Stage20 offline SQL

MySQL/PostgreSQL 的 offline SQL 由人工在受控环境生成并审阅，例如：

```text
uv run alembic upgrade 0030_enterprise_release_quality_certification --sql
```

生成的 SQL 只是审阅材料，不是自动生产执行器。DBA 必须确认七张表、Tenant-leading FK、active policy canonical scope CHECK/unique、digest/lifecycle CHECK、immutable trigger、Approval action CHECK、downgrade blocker 和索引均在 SQL 中出现；不得从 SQL 中删除 fail-closed 约束。

`0030 SQLite offline upgrade is unsupported`：SQLite 必须使用在线 batch migration，并在执行前后完成只读预检；本工具不会替代 Alembic，也不会自动运行该 online migration。0030 downgrade 同样只能由 DBA 按独立审批人工执行，并且存在质量 authority facts 时必须尊重 migration 的 downgrade blocker。

### Stage20 checklist

- [ ] `plan --from 0029_enterprise_knowledge_base_releases --to 0030_enterprise_release_quality_certification` 已归档，目标 revision 与审批单一致。
- [ ] 0030 preflight 为 read-only，七张表、counts、Tenant scope 和 Approval waiver action 已核验。
- [ ] active Policy scope 均为 canonical，且 high/default Channel policy coverage 无 blocker。
- [ ] Baseline/Item、Certification/Evidence、Waiver/Event 的 missing/stale/expired/revoked/invalid envelope blocker 均为空。
- [ ] 已明确不自动创建 Policy/Baseline/Certification/Waiver/Event，不执行真实 0030 migration、backfill、promotion、rollback、pin 或 sync。
- [ ] MySQL/PostgreSQL offline SQL 已由 DBA 审阅；SQLite offline path 已按 `0030 SQLite offline upgrade is unsupported` 阻断。


## Stage21：Release Quality Operations 只读预检与人工升级边界

> **适用 revision：** `0031_enterprise_release_quality_operations`<br>
> **down revision：** `0030_enterprise_release_quality_certification`<br>
> **本节日期：2026-08-29**<br>
> **结论：** 本节只定义只读 preflight、offline review 和经审批的人工步骤；本轮不执行真实 migration、scan、certification 或任何生产写入。

Stage21 新增六张 Release Quality Operations 表：

1. `tenant_release_quality_slo_policies`
2. `tenant_release_quality_scan_schedules`
3. `tenant_release_quality_scan_runs`
4. `dataset_release_quality_observations`
5. `dataset_release_quality_alerts`
6. `dataset_release_recertification_jobs`

### Stage21 preflight contract

`scripts/enterprise_catalog_upgrade.py` 的 Stage21 报告必须同时呈现：

- 0031 六表是否存在、每表计数和缺失列；
- Dataset-scoped Schedule/Run 的 Tenant-leading composite scope、Dataset/Policy/Schedule 交叉引用和目标唯一性；
- canonical active SLO/Schedule/Alert/Job identities；
- Recertification Job 的 `cycle_key` 合法性和 Tenant 内重复；
- Scan Run / Recertification Job 的 lease/terminal consistency；
- `dataset_release_quality_observations` 的实际 `UPDATE`/`DELETE` immutable guards 及其 target table；
- orphan/cross-Tenant/duplicate blockers，以及 Observation digest/envelope 的 authority 一致性。

报告必须明确包含以下不变量：

```text
read_only=true
mutations_performed=false
automatic_actions=[]
```

`safe_to_upgrade` 只能由全部已发现 blocker 为空时为 true；任何 schema capability、composite scope、identity、cycle_key、lease/terminal、immutable guard、orphan、cross-Tenant 或 duplicate blocker 都必须使它为 false。预检不得通过写入或修复数据来改变结论。

预检严格是 SELECT/metadata-only：**不得创建 Policy/Schedule/Run/Observation/Alert/Job**，不得 enqueue Scan Run，不得自动生成 Recertification Job，不得自动 Certification、Waiver、Approval、Promotion、Rollback、Application pin、Source Sync、restore 或 delete。Stage19/20 的 Release、Manifest、Quality Gate、Baseline、Certification、Waiver 和 Approval ExecutionFact 仍是既有 authority，Stage21 只读取并校验它们。

### Stage21 人工步骤

1. **冻结变更面。** 在审批单中记录 Tenant、Dataset、Release、Channel、Stage20 Quality authority、当前 Alembic revision、备份校验、恢复演练、操作者和维护窗口；暂停 API 写入、worker 和 scheduler。不得用合成 owner、Policy、Schedule 或 Job 让预检变绿。
2. **执行 0030 → 0031 之前的只读 preflight。** 归档 JSON 原文，确认 Stage19/20 blockers 为空。若 0031 表已部分出现、revision 不一致、父级 authority 不完整或已有 blocker，停止并交由 DBA/owner 处理。
3. **生成 MySQL/PostgreSQL offline review 材料。** 只生成并审阅 SQL，不从本工具自动执行：

   ```text
   uv run alembic upgrade 0031_enterprise_release_quality_operations --sql
   ```

   DBA 必须核对六张表、Tenant-leading composite FK、target unique、canonical active identity CHECK/unique、`cycle_key`、lease/terminal CHECK、Observation immutable guards、索引、UTC/精度和 downgrade guard。**MySQL/PostgreSQL offline review** 只是审阅证据，不是生产执行授权。
4. **SQLite online only。** SQLite 不接受 0031 offline SQL；必须在隔离测试库以 online migration 运行，并启用 `PRAGMA foreign_keys=ON`，验证真实 immutable trigger target、clean upgrade 和 clean downgrade。`0031 SQLite offline upgrade is unsupported`；本工具不会自动执行 SQLite migration。
5. **经审批人工执行 migration。** 授权 DBA 按批准的 SQL/online 流程执行 0031，工具不调用 `alembic upgrade` 代替 DBA，不自动 backfill 六表，也不自动创建任何运行时 authority。
6. **迁移后再次只读核对。** 确认 `alembic_version=0031_enterprise_release_quality_operations`，复核六表 counts、Dataset-scoped Schedule/Run、active identities、`cycle_key`、lease/terminal consistency、immutable guards、orphan/cross-Tenant/duplicate blockers。只要 `safe_to_upgrade=false` 或 `reasons` 非空，就不得继续启用 worker 或对外宣称健康。
7. **运行时开通必须分离。** SLO Policy、Schedule、Scan Run、Observation、Alert、Recertification Job 的真实创建/执行/处置必须经各自 API、Tenant authorization、revision fence、幂等和审计链路；不得把 migration/preflight 当作业务初始化器。
8. **Downgrade 只允许空 authority。** 0031 → 0030 前必须重新执行只读 data/schema preflight；任何 Stage21 authority row 都触发 **nonempty downgrade blocker**。不得自动删除 Observation、Alert、Job、Run、Schedule 或 SLO Policy，不得自动 restore/rollback；人工 downgrade 必须由 DBA 按独立审批执行。
9. **跨数据库结论要有证据。** SQLite online 测试、MySQL/PostgreSQL offline review 不能替代真实数据库验证；完成 **real container smoke** 前，不得称为 **cross-DB production-ready**。容器 smoke 必须覆盖 composite FK、CHECK、UTC/DateTime 精度、lease CAS、Observation trigger target、active identity uniqueness 和 nonempty downgrade blocker。

### Stage21 preflight checklist

- [ ] 当前 revision、目标 `0031_enterprise_release_quality_operations` 和 down revision `0030_enterprise_release_quality_certification` 与审批单一致。
- [ ] 六张表全部存在，counts 已归档，缺失表/列为空。
- [ ] Dataset-scoped Schedule/Run 的 `tenant_id + dataset_id` scope 和所有 Tenant-leading composite FK/target unique 已核验。
- [ ] canonical active SLO/Schedule/Alert/Job identities、`active_alert_key`、`active_job_key` 和 `cycle_key` 无 blocker。
- [ ] Scan Run / Recertification Job lease/terminal consistency 无 blocker。
- [ ] Observation immutable guards 的实际 target table 正确，Observation digest/envelope 无 blocker。
- [ ] orphan/cross-Tenant/duplicate blockers 为空；Stage19/20 authority current、可解析且无伪造事实。
- [ ] 预检 JSON 明确 `read_only=true`、`mutations_performed=false`、`automatic_actions=[]`，且没有任何自动 action。
- [ ] MySQL/PostgreSQL offline review 已由 DBA 完成；SQLite online only 边界已记录。
- [ ] nonempty downgrade blocker、真实 container smoke 和在此之前不得称 cross-DB production-ready 的限制已写入变更单。


## 0032 Stage22 Enterprise Notification Center

Revision: `0032_enterprise_notification_center`  
Down revision: `0031_enterprise_release_quality_operations`

The Stage22 preflight is strictly read-only and reports the five authority tables:

```text
tenant_notification_subscriptions
tenant_notifications
tenant_notification_recipients
tenant_notification_receipts
tenant_notification_events
```

It verifies canonical Subscription/Notification/Recipient/Receipt/Event identities, immutable Notification/Recipient/Event guards, Receipt lifecycle, Event hash-chain, active Tenant members and safe route parameters. The report always preserves `read_only=true`, `mutations_performed=false` and `automatic_actions=[]`.

Preflight never performs Notification materialization, creates a Recipient or Receipt, marks a Notification read, or changes a Subscription. Notification materialization is a separate controlled system action.

SQLite online only: 0032 SQLite batch migration must run through an online connection after read-only preflight. SQLite offline SQL fails closed. MySQL/PostgreSQL offline SQL is review-only until real container smoke validates triggers and Tenant-leading composite FKs.

Downgrade to 0031 has a nonempty downgrade blocker: any Stage22 authority row prevents downgrade. The operator must not delete Notification history merely to satisfy downgrade. No automatic rollback is attempted.


## 0033 Enterprise Content Recovery boundary

Target revision: `0033_enterprise_content_recovery`. The read-only preflight reports exactly:

- `tenant_content_retention_policies`
- `tenant_document_recycle_entries`
- `tenant_document_legal_holds`
- `tenant_document_purge_requests`
- `tenant_document_recovery_events`

The report always records `read_only=true`, `mutations_performed=false`, and no automatic recycle, restore, legal hold, Approval Request, purge or durable delete. MySQL and PostgreSQL may use reviewed offline DDL; SQLite online only. Downgrade requires online inspection and has a nonempty authority blocker plus a recycled Document downgrade blocker. Operators must never clear recovery rows or rewrite a recycled Document merely to make downgrade pass.


## 0034 Stage24 Enterprise Task Operations boundary

Target revision: `0034_enterprise_task_operations`. Down revision: `0033_enterprise_content_recovery`.

The Stage24 preflight is strictly read-only and reports exactly five projection-authority tables:

- `tenant_task_projections`
- `tenant_task_operator_actions`
- `tenant_task_events`
- `tenant_task_saved_views`
- `tenant_task_reconciliation_runs`

The JSON evidence must retain `read_only=true`, `mutations_performed=false`, `automatic_actions=[]`, `reconcile_performed=false`, and `source_actions_dispatched=[]`. The preflight performs metadata inspection and `SELECT COUNT(*)` only. There is **no automatic reconcile**, no task projection materialization, and **no automatic retry or cancel**; it never dispatches source actions, acknowledges attention, creates Saved Views, changes queues, or mutates source-domain task tables.

Readiness validates Tenant-leading identities, current source revision/digest fences, canonical projection and action digests, immutable contiguous Event chains, bounded Saved View filters, and reconciliation lifecycle/counts. A partial 0034 schema fails closed. Operators must not treat the unified projection as a replacement for the source-domain task authority.

SQLite online only: SQLite offline SQL fails closed. MySQL/PostgreSQL offline DDL remains review evidence until real container smoke validates composite FKs, CHECK constraints, digest/event guards, idempotency, and concurrency. Downgrade to 0033 has a **nonempty downgrade blocker**: any Stage24 authority row prevents downgrade. Never delete task events, actions, Saved Views, reconciliation evidence, or projections merely to satisfy downgrade; no automatic rollback or restore is attempted.

## 0035 Stage25 Enterprise Automation & Workflow Orchestration boundary

Target revision: `0035_enterprise_automation_workflows`. Down revision: `0034_enterprise_task_operations`.

Stage25 preflight is strictly read-only and reports exactly six Tenant-scoped Automation authority tables:

- `tenant_automation_rules`
- `tenant_automation_rule_revisions`
- `tenant_automation_source_cursors`
- `tenant_automation_runs`
- `tenant_automation_action_requests`
- `tenant_automation_events`

The report includes a count for every table and the manifest-backed schema capability state/issues. Evidence must retain the following exact safety fields:

```text
read_only=true
mutations_performed=false
automatic_actions=[]
source_observation_performed=false
cursor_advanced=false
action_dispatches=[]
```

A partial 0035 schema is **fail closed**. Missing tables or columns, a revision mismatch, missing Tenant-leading constraints/indexes, missing immutable revision/Event guards, broken event-chain capability, or any other manifest capability issue makes `safe_to_upgrade=false`. The preflight never repairs a partial schema or changes the conclusion by inserting synthetic rules, revisions, cursors, Runs, Action Requests or Events.

The Stage25 preflight only inspects schema metadata, reads the Alembic revision, and runs `SELECT COUNT(*)` against the six authority tables. It does not observe source events, advance a source cursor, create or evaluate an automation Run, create an Action Request, dispatch an Action, or pause a Rule. There is **no automatic Run**, **no automatic Action**, and **no automatic Rule pause**. Source observation and all operational actions remain separate approved workflows.

SQLite is **online only** for 0035 migration execution. SQLite offline SQL generation is unsupported and must fail closed; the online Alembic batch migration remains a separate manually approved step after preflight. MySQL/Postgres (PostgreSQL) offline DDL is review evidence only: a DBA must inspect the generated SQL for all six tables, Tenant-leading composite FKs and uniques, bounded checks, immutable revision/Event guards, event-chain validation, indexes, and downgrade protection before any live execution. Offline SQL is never executed automatically by this tool.

Downgrade to `0034_enterprise_task_operations` has a **nonempty downgrade blocker**. Any Stage25 rule, revision, cursor, Run, Action Request or Event row prevents downgrade. Operators must not delete Automation authority history, clear cursors, rewrite immutable revisions/events, or pause Rules merely to make downgrade pass. No automatic rollback or restore is attempted; downgrade and recovery require an explicit reviewed maintenance procedure.

### Stage25 operator checklist

- [ ] `plan --from 0034_enterprise_task_operations --to 0035_enterprise_automation_workflows` is archived with the approved change.
- [ ] Preflight reports all six table counts and manifest-backed `schema_capability_state`/`schema_capability_issues`.
- [ ] A partial 0035 schema is blocked; no synthetic authority rows are inserted to make it green.
- [ ] The JSON evidence contains `read_only=true`, `mutations_performed=false`, `automatic_actions=[]`, `source_observation_performed=false`, `cursor_advanced=false`, and `action_dispatches=[]`.
- [ ] No automatic Run, Action dispatch, or Rule pause occurred; source observation and cursor advancement remain false.
- [ ] SQLite online-only handling is recorded; MySQL/Postgres offline DDL review is complete before live execution.
- [ ] The nonempty downgrade blocker, backup/recovery evidence, maintenance window, operator, and approval references are recorded.

For avoidance of doubt: **MySQL/PostgreSQL offline DDL review** is a review gate only, not an execution path.

## Stage 26 — Enterprise Knowledge Serving Reliability (`0036_enterprise_knowledge_serving_reliability`)

> Backfilled in R10 (2026-09-19). This section was a registered documentation gap: Stage 25
> (`0035`) jumped straight to Stage 27 (`0037`) in this ledger, so operators preparing a live
> drill had no authority shape, no downgrade blocker text and no coverage boundary for `0036`.
> Every statement below was read off the migration and its tests, not off a prior document.

### Authority shape

`revision = 0036_enterprise_knowledge_serving_reliability`,
`down_revision = 0035_enterprise_automation_workflows`. Six tenant-scoped tables, in
`TABLES` order:

`tenant_knowledge_serving_profiles`, `tenant_knowledge_serving_policy_revisions`,
`tenant_knowledge_serving_snapshots`, `tenant_knowledge_serving_stage_facts`,
`tenant_knowledge_serving_evidence_links`, `tenant_knowledge_serving_events`.

`IMMUTABLE_TABLES = TABLES[1:]` — i.e. **everything except the profile header is append-only**:
UPDATE and DELETE are blocked by `trg_<table>_no_update` / `trg_<table>_no_delete`.

Stage facts are constrained to a closed vocabulary, so a "reliability" row cannot be invented
ad hoc: `STAGE_CODES = (source, parse, chunk, index, serve)`,
`STAGE_STATES = (ready, lagging, blocked, missing, unavailable)`.

Profiles carry two mutable pointers (`current_snapshot`, `current_policy`) whose foreign keys
`fk_tenant_knowledge_serving_profiles_current_snapshot` / `_current_policy` are the only
constraints the downgrade path has to drop explicitly (non-SQLite only — SQLite rebuilds).

Six indexes, one per table:
`ix_tenant_knowledge_serving_profiles_tenant_status_updated`,
`ix_tenant_knowledge_serving_policy_revisions_profile_created`,
`ix_tenant_knowledge_serving_snapshots_profile_as_of`,
`ix_tenant_knowledge_serving_stage_facts_snapshot_sequence`,
`ix_tenant_knowledge_serving_evidence_links_snapshot_kind`,
`ix_tenant_knowledge_serving_events_profile_time`.

### Event hash chain

`tenant_knowledge_serving_events` is guarded by `trg_tenant_knowledge_serving_events_validate_insert`
(BEFORE INSERT). The chain is per `(tenant_id, profile_id, stream_key)`:

* `sequence = 1` must have `event_type = 'profile_created'` **and** `previous_event_digest IS NULL`
  (`knowledge serving first event invalid` / `knowledge serving first previous digest invalid`).
* `sequence > 1` must carry a `previous_event_digest`
  (`knowledge serving previous digest required`) **and** the predecessor row must exist with
  `sequence = NEW.sequence - 1` and `event_digest = NEW.previous_event_digest`
  (`knowledge serving event predecessor invalid`).

Dialect implementations differ and this matters for review: SQLite/MySQL/MariaDB use inline
trigger bodies, PostgreSQL uses two functions — `rag4c_knowledge_serving_immutable` (the
append-only guard) and `rag4c_knowledge_serving_event_validate` (the chain) — and
`_drop_guards()` must `DROP FUNCTION` both, not just the triggers.

### Readiness and fail-closed behaviour

* Capability key `enterprise_knowledge_serving_reliability`, label 「知识服务可靠性」,
  default state `unavailable` with reason 「知识服务可靠性权威尚未通过数据库验证」;
  the directory gate message is 「0036 知识服务可靠性数据库升级尚未就绪」.
* `SUPPORTED_DIALECTS = {sqlite, mysql, mariadb, postgresql}`. Anything else (the test uses
  `oracle`) raises rather than half-applying.
* Offline mode is refused for downgrade: `0036 downgrade requires online preflight`.
* Downgrade is blocked while **any** of the six tables is non-empty:
  `0036 downgrade blocked by Knowledge Serving authority: <table>=<count>, …`.
  Operators must not delete authority rows to make downgrade pass.

### Coverage boundary

Better than Stage 27's, and worth stating precisely so nobody over- or under-trusts it:

* All execution-path tests in `tests/test_enterprise_knowledge_serving_migration.py` run on
  **SQLite** (`sqlite_url(tmp_path / …)`): contract, guards, profile identity, evidence
  contract, clean vs blocked downgrade.
* `test_offline_ddl_supports_mysql_postgresql_and_sqlite_fails_closed` does assert the
  **rendered offline DDL** for `mysql+pymysql` (expects `DATETIME(6)`) and
  `postgresql+psycopg` (expects `TIMESTAMP`), and that SQLite offline is refused.
  So the dialect branches are *rendered* under test, but never *executed* against a live
  MySQL/MariaDB/PostgreSQL server in CI.
* Trigger bodies on MySQL/MariaDB/PostgreSQL therefore remain review-only. This is exactly the
  class of gap where Stage 27 later found a real defect (`dialect.name == "mysql"` excluding
  `mariadb`), so treat "offline DDL renders" as necessary, not sufficient.

**Gate:** the live MySQL release drill must cover `0036` and `0037` together — they share the
same trigger/function structure, and `0037`'s chain depends on `0036`'s profile authority.

### Stage 26 operator checklist

- [ ] `plan --from 0035_enterprise_automation_workflows --to 0036_enterprise_knowledge_serving_reliability` archived with the approved change.
- [ ] Preflight reports all six table counts and the manifest-backed capability state/issues.
- [ ] A partial 0036 schema (e.g. events table without its insert trigger) is blocked, not tolerated.
- [ ] The nonempty downgrade blocker text, backup/recovery evidence, maintenance window, operator and approval references are recorded.
- [ ] PostgreSQL path reviewed for both `DROP FUNCTION`s (`…_immutable`, `…_event_validate`), not just triggers.
- [ ] MySQL/MariaDB `DATETIME(6)` defaults verified to actually carry microseconds on the live engine.

## Stage 27 — Enterprise Knowledge Operations & Feedback (`0037_enterprise_knowledge_operations_feedback`)

> Ledger note: Stage 26 previously had no section in this file. It was backfilled in R10
> (2026-09-19) — see the Stage 26 section immediately above. Stage 27's coverage boundary was
> **narrower** than Stage 26's (no offline-DDL dialect test at all); R10 added
> `test_offline_ddl_renders_mysql_mariadb_postgresql_and_refuses_sqlite`, which renders the
> `0036:0037` step for mysql, **mariadb** and postgresql and asserts the seven `CREATE TABLE`s,
> both digest columns, `CREATE TRIGGER` and the `DATETIME(6)` / `TIMESTAMP` markers. Live-engine
> execution is still untested for both stages.

### Authority shape

Seven tenant-scoped tables that project **derived operational facts only** — no raw query,
answer, prompt, token, credential or document content column is permitted anywhere in this
authority (the migration asserts this and `tests/test_enterprise_knowledge_operations_migration.py`
re-checks every column name against a protected-name set):

`tenant_knowledge_operations_profiles`, `tenant_knowledge_conversation_sessions`,
`tenant_knowledge_query_facts`, `tenant_knowledge_feedback_facts`,
`tenant_knowledge_review_cases`, `tenant_knowledge_review_events`,
`tenant_knowledge_improvement_candidates`.

### Readiness and fail-closed behaviour

* Capability key `enterprise_knowledge_operations_feedback`, label 「企业知识运营与反馈」,
  appended **immediately after** `enterprise_knowledge_serving_reliability` in
  `server/enterprise_readiness_api.py::_CAPABILITIES`.
* Default state is `unavailable`; a partial schema, an unknown dialect, or an
  unrecognised revision all resolve to fail-closed, never to `ready`.
* `tenant_knowledge_query_facts`, `tenant_knowledge_feedback_facts` and
  `tenant_knowledge_review_events` are **append-only**: UPDATE/DELETE are blocked by triggers.
* `tenant_knowledge_review_events` enforces a hash chain — `sequence=1` must be
  `case_created` with no predecessor digest; later events must carry the previous event's
  digest and have their predecessor present.
* Downgrade is blocked while **any** of the seven tables is non-empty
  (`0037 downgrade blocked by Knowledge Operations authority: <table>=<count>`).
  Operators must not delete authority rows to make downgrade pass.

### Coverage boundary (read before trusting this migration on MySQL)

All 19 Stage 27 tests run on **SQLite**. The MySQL/MariaDB/PostgreSQL trigger branches have no
regression coverage, and one real defect was found by review rather than by test:
`upgrade()` decided MySQL-ness with `dialect.name == "mysql"`, which excluded `mariadb` and would
have given `DATETIME(6)` columns a second-precision `CURRENT_TIMESTAMP` default — silently
destroying the microsecond ordering the event chain depends on. Fixed to use the same casefolded
dialect set as the trigger branches.

**Gate:** run the MySQL release drill
(`docs/knowledgeops-mysql-release-drill-2026-08-24.md` procedure) against `0037` before any live
execution, and review MySQL/PostgreSQL offline DDL as a review gate only.

### Stage 27 operator checklist

- [ ] `plan --from 0036_enterprise_knowledge_serving_reliability --to 0037_enterprise_knowledge_operations_feedback` archived with the approved change.
- [ ] Preflight reports all seven table counts and manifest-backed capability state/issues.
- [ ] A partial 0037 schema is blocked; no synthetic authority rows are inserted to make it green.
- [ ] The nonempty downgrade blocker, backup/recovery evidence, maintenance window, operator and approval references are recorded.
- [ ] MySQL/MariaDB/PostgreSQL trigger + guard branches reviewed (untested by CI).
