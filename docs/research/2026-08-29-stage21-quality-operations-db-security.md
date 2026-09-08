# Stage 21 Quality Operations：数据库与安全研究

> **状态：Research only / 仅设计研究，不是已执行的迁移方案。**
>
> **目标 Revision：** `0031_enterprise_release_quality_operations`
>
> **Down Revision：** `0030_enterprise_release_quality_certification`
>
> **目标文件日期：** `2026-08-29`（按任务指定；本次代码审查实际执行日期为 **2026-08-28，Asia/Shanghai**）。
>
> **本次写集：** 仅本文件。没有修改代码、Migration、ORM、测试或受保护的 Retrieval Quality 路径；没有执行生产 Migration、扫描、Certification、Waiver、Approval、Promotion、Rollback、Application Pin、Source Sync、Restore 或 Delete。

## 1. 结论摘要

Stage 21 的目标不是重建 Retrieval Experiment，也不是把浏览器里的实时计算伪装成数据库事实，而是在 Stage 20 已有的 Release Quality authority 之上增加一个**可重放、可审计、可恢复的运营控制面**：

```text
Stage 20 Quality Policy / Baseline / Certification / Waiver
    ↓
SLO Scan Schedule
    ↓
Durable Scan Run（有 lease、有幂等）
    ↓
Immutable Observation
    ↓
Alert lifecycle / Recertification Job
    ↓
只读运营面、显式人工操作、Stage 20 certify_release()
```

推荐保留六张表：

```text
tenant_release_quality_slo_policies
tenant_release_quality_scan_schedules
tenant_release_quality_scan_runs
dataset_release_quality_observations
dataset_release_quality_alerts
dataset_release_quality_recertification_jobs
```

核心安全结论：

1. **扫描 Schedule 建议以 Dataset 为边界，而不是做一个 `dataset_id=NULL` 的 Tenant-wide polymorphic row。** 这样每一条新表记录都可以使用 Tenant-leading composite FK，并能把扫描工作量、权限和锁范围限制在一个 Knowledge Base。
2. **Observation 必须 append-only；Alert、Schedule、Run、Job 是受 revision fence 保护的可变运营状态。** 不要用一个“全表 immutable”触发器覆盖所有六张表。
3. **不要依赖 partial unique index 表达“只有 active 行唯一”。** 采用 `active_scope_key` / `active_alert_key` 非活动时为 `NULL` 的模式，再配合 `(tenant_id, key)` 普通 UniqueConstraint，兼容 SQLite、MySQL/MariaDB、PostgreSQL。
4. **Job 的幂等与去重需要两层 key：** API/系统请求用 Tenant-scoped idempotency digest；同一 Release authority 周期的自动告警合并用不可变 `cycle_key`。不能只把每一次扫描的 `scan_run_id` 放进 key，否则同一 stale authority 会每次扫描产生一个新 Job。
5. **Quality Gate 仍以 Stage 20 为唯一 authority。** Stage 21 只重新读取、重新验证和记录观察；不新增 Retrieval Experiment runner，不修改 Judgment，不自动生成 Baseline，不自动执行 Certification 或 Promotion。
6. **跨数据库的 CHECK 只约束结构和状态形状，语义 authority 由 service + readiness/preflight 重算。** Digest 是否真的是 hex、JSON 是否安全、Observation 是否与当前 Gate envelope 一致，不能只靠一个跨方言 SQL CHECK。
7. **不要新增 Stage 21 Approval action。** Waiver 继续复用既有 `knowledge_base_release_quality_waiver`；SLO policy、Alert acknowledge/resolve、Job enqueue/cancel 使用既有 Tenant permission、revision fence、audit 和 idempotency ledger。

## 2. 当前代码证据与设计边界

以下结论来自当前工作树的真实代码，而不是假设中的 0031 实现。

### 2.1 Stage 20 Migration / Catalog 约束已经形成基线

证据：

- `catalog_migrations/versions/0030_enterprise_release_quality_certification.py:15-37`
  - 当前 Revision 为 `0030_enterprise_release_quality_certification`；
  - 现有 Release Quality authority 为七张表；
  - `IMMUTABLE_TABLES` 包含 Baseline、Baseline Item、Certification、Evidence、Waiver、Event。
- `catalog_migrations/versions/0030_enterprise_release_quality_certification.py:69-86`
  - Policy 的 `active_scope_key` 已有跨方言 canonical predicate。
- `catalog_migrations/versions/0030_enterprise_release_quality_certification.py:97-135`
  - SQLite 使用 `BEFORE UPDATE/DELETE ... RAISE(ABORT)`；
  - MySQL/MariaDB 使用 `SIGNAL SQLSTATE '45000'`；
  - PostgreSQL 使用 trigger function。
- `catalog_migrations/versions/0030_enterprise_release_quality_certification.py:138-154`
  - Downgrade 是 online-only；
  - 非空 Quality authority 或 Waiver action 会阻断退回 0029。
- `core/catalog_schema.py:3385-3462, 3802-3997`
  - 当前 Catalog readiness 以 ORM metadata 生成表、列、Not Null、Unique、FK、Check、Index contract；
  - 会检查 canonical Policy scope、重复 active scope、immutable trigger 的实际目标表及 Stage 20 capability。

Stage 21 应继承这套模式：Migration 提供结构性约束，Catalog capability 提供版本感知和语义 preflight，service 负责运行时 authority 重算。

### 2.2 Stage 20 Quality evidence 是唯一质量事实来源

证据：

- `core/enterprise_release_quality_evidence.py:57-125`
  - `_exact_integer()` 和 `_exact_boolean()` 拒绝隐式类型转换；
  - snapshot 使用深度、键和值类型限制；
  - `canonical_quality_digest()` 使用 domain-separated SHA-256 canonical JSON。
- `core/enterprise_release_quality_evidence.py:376-511`
  - Release evidence 校验 Tenant、Dataset、Release；
  - Experiment 的 serving generation 必须匹配 Release；
  - Result document 必须存在于 Release Manifest；
  - document revision 必须匹配 Manifest Entry 的 content revision；
  - `lock_for_update=True` 时按确定性顺序锁 Experiment 和 Judgment。
- `core/enterprise_release_quality_service.py:1115-1152`
  - Certification 会重新采集 evidence、比对 Baseline Item digest，并重算 Policy digest；
  - Baseline evidence 或 Policy authority 过期时拒绝写入 Certification。
- `core/enterprise_release_quality_service.py:1864-2019`
  - `resolve_release_quality_gate()` 只接受当前有效 Policy、Manifest、Certification 或 Approval-backed Waiver；
  - high-risk/default-serving Channel 没有 Policy 时 fail closed；
  - Gate 状态包括 `not_required`、`passed`、`blocked`、`waived`、`unavailable`。

Stage 21 Observation 不应存储 query text、result body、Judgment note 或任何第二套质量指标。它只保存 Gate 已验证的 scalar projection、authority IDs/digests 和时间事实。

### 2.3 SourceSchedule 提供可复用的调度、锁和幂等模式

证据：

- `core/source_schedules.py:147-185`
  - `_for_update()` 在 MySQL/PostgreSQL 使用 `skip_locked=True`，SQLite 使用普通 `with_for_update()`；
  - `_lock_scope()` 先锁 Dataset，再锁 Data Source。
- `core/source_schedules.py:207-215`
  - SQLite 写事务使用 `BEGIN IMMEDIATE`；
  - Dataset 或 Source 非 active 时不允许调度运行。
- `core/source_schedules.py:235-360`
  - Schedule 使用正整数 Revision、CAS 更新、数据库 UTC 时间和审计；
  - 状态 `active/paused/archived` 是显式生命周期。
- `core/source_schedules.py:428-446`
  - due slot 通过确定性时间槽计算；
  - intent key 与 schedule id、schedule revision、planned time 绑定。
- `core/source_schedules.py:448-585`
  - due candidate 读取后会重新锁 scope、Schedule 并复核 revision/status/next_run_at；
  - 同一 planned slot 使用确定性幂等 key，只创建一个 durable run；
  - Schedule advance、Run、Audit 在同一事务中提交，失败时回滚。
- `catalog_migrations/versions/0014_source_schedules.py:107-146`
  - `SourceSyncRun` 使用 `schedule_id/schedule_revision/planned_at` 绑定 Schedule；
  - scheduled 与非-scheduled metadata 具有 all-or-none CheckConstraint。

Stage 21 Scan Schedule/Run 应复用这些原则，但不能把 Source Sync 的状态或数据模型复制为另一套 runner。

### 2.4 Tenant idempotency ledger 不保存原始 Operator key

证据：

- `core/enterprise_tenant_idempotency.py:54-71`
  - `tenant_id + actor_id + normalized raw key` 通过 domain-separated SHA-256 生成 lookup digest；
  - 原始 key 不应写入数据库。
- `core/enterprise_tenant_idempotency.py:108-195`
  - reservation 使用 `(tenant_id, actor_id, idempotency_key)` 锁定；
  - 同 key 不同 request hash/operation/resource type 会冲突；
  - pending reservation 会返回 in-progress；
  - completed reservation 可安全 replay。
- `core/enterprise_tenant_idempotency.py:198-239`
  - replay response 会检查敏感 marker；
  - 只有 pending reservation 才能完成为 completed。

因此 Stage 21 API mutation 应继续调用该 ledger，而不是在六张新表中存储原始 `Idempotency-Key`。System scheduler 也应使用固定 system actor + deterministic request hash；表内再保留 digest 作为数据库级去重证据。

### 2.5 Approval 的 opaque ExecutionFact 已是 Waiver authority

证据：

- `core/enterprise_approval_control.py:153-224`
  - `ApprovalExecutionFact` 要求 Tenant、Request、Execution、Revision、Action、Resource、Snapshot hash 等必需字段；
  - request revision 与 execution revision 必须连续；
  - snapshot hash 必须为 SHA-256 hex。
- `core/enterprise_approval_control.py:276-348`
  - Release Publish/Rollback/Waiver action 会检查 Release、Manifest、Channel、Workspace、Generation 等绑定；
  - Quality Waiver 额外检查 Policy digest、Quality Gate digest、Evidence digest、expiry 和 `quality_gate_state='blocked'`。
- `models/orm.py:1522-1675`
  - Approval Request 有 Tenant-leading Policy FK、requester/actor scope、status evidence、`ticket_consumed_at`、`execution_ticket_hash`、revision 和 expiry 字段；
  - `knowledge_base_release_quality_waiver` 已在 ORM action Check 中存在。
- `server/enterprise_approval_consumers.py` 和 `core/enterprise_approval_control.py` 当前已有稳定 Waiver consumer/fact 构造路径。

Stage 21 不应在 SLO Policy、Alert 或 Job 中增加一套 `approved_by/approval_status` 的影子状态。只有真正的 Waiver 继续走现有 Approval Request → consumed ExecutionFact → immutable Stage 20 Waiver receipt 链路。

## 3. 六张表的推荐 authority contract

下文是 0031 Migration/ORM 的建议 contract。当前工作树尚无 0031 表；下面的列名、状态和约束必须在实现阶段同步到 Migration、ORM、Catalog readiness、preflight、service 和测试。

### 3.1 `tenant_release_quality_slo_policies`

#### 3.1.1 作用

Tenant 管理的 SLO policy revision。它描述“多久前开始告警、何时进入 critical、是否允许自动排队再认证”等运营规则，不替代 Stage 20 的 Retrieval quality gate policy。

#### 3.1.2 推荐列

```text
id                              VARCHAR(64)       PK, NOT NULL
tenant_id                       VARCHAR(64)       NOT NULL
name                            VARCHAR(128)      NOT NULL
scope_type                      VARCHAR(16)       NOT NULL  # global|risk_tier|channel
scope_value                     VARCHAR(128)      NOT NULL
channel_id                      VARCHAR(128)      NULL
active_scope_key                VARCHAR(192)      NULL
status                          VARCHAR(16)       NOT NULL  # active|disabled
revision                        INTEGER           NOT NULL
certification_warning_minutes  INTEGER           NOT NULL
certification_critical_minutes INTEGER           NOT NULL
waiver_warning_minutes         INTEGER           NOT NULL
waiver_critical_minutes        INTEGER           NOT NULL
max_open_alerts                 INTEGER           NOT NULL
auto_queue_recertification      BOOLEAN           NOT NULL
require_passing_certification   BOOLEAN           NOT NULL
allow_active_waiver             BOOLEAN           NOT NULL
slo_policy_digest               VARCHAR(64)       NOT NULL
created_at                      DATETIME(6)       NOT NULL
created_by                      VARCHAR(64)       NOT NULL
updated_at                      DATETIME(6)       NOT NULL
updated_by                      VARCHAR(64)       NOT NULL
disabled_at                     DATETIME(6)       NULL
disabled_by                     VARCHAR(64)       NULL
```

#### 3.1.3 数据库约束

```text
PK(id)
UNIQUE(tenant_id, id)
UNIQUE(tenant_id, active_scope_key)
FK(tenant_id) -> tenants(id)
FK(tenant_id, channel_id) -> tenant_release_channels(tenant_id, id)
```

推荐 Check：

```text
scope_type IN ('global', 'risk_tier', 'channel')
status IN ('active', 'disabled')
revision > 0

(
  scope_type='global'
  AND scope_value='*'
  AND channel_id IS NULL
)
OR (
  scope_type='risk_tier'
  AND scope_value IN ('low','medium','high')
  AND channel_id IS NULL
)
OR (
  scope_type='channel'
  AND channel_id IS NOT NULL
  AND scope_value=channel_id
)

(
  status='active'
  AND active_scope_key IS NOT NULL
  AND disabled_at IS NULL
  AND disabled_by IS NULL
)
OR (
  status='disabled'
  AND active_scope_key IS NULL
  AND disabled_at IS NOT NULL
  AND disabled_by IS NOT NULL
)

certification_warning_minutes > certification_critical_minutes
AND certification_critical_minutes >= 0
AND waiver_warning_minutes > waiver_critical_minutes
AND waiver_critical_minutes >= 0
AND max_open_alerts > 0

auto_queue_recertification IN (0,1)
AND require_passing_certification IN (0,1)
AND allow_active_waiver IN (0,1)

length(slo_policy_digest)=64
AND lower(slo_policy_digest)=slo_policy_digest
```

`active_scope_key` 的 canonical contract：

```text
global:*                       when scope_type='global'
risk_tier:<low|medium|high>   when scope_type='risk_tier'
channel:<channel_id>           when scope_type='channel'
NULL                           when status='disabled'
```

不要只在 service 中填 key；必须增加与 Stage 20 相同形式的 exact canonical Check，并在 readiness 中重算。SQLite/PostgreSQL 可以使用 `||`，MySQL/MariaDB 使用 SQLAlchemy dialect 编译到 `CONCAT(...)`。不要使用无法在三种数据库保持同语义的 `REGEXP` CHECK。

`slo_policy_digest` 建议使用：

```python
canonical_quality_digest(
    "slo_policy",
    {
        "scope_type": scope_type,
        "scope_value": scope_value,
        "channel_id": channel_id,
        "revision": revision,
        "certification_warning_minutes": certification_warning_minutes,
        "certification_critical_minutes": certification_critical_minutes,
        "waiver_warning_minutes": waiver_warning_minutes,
        "waiver_critical_minutes": waiver_critical_minutes,
        "max_open_alerts": max_open_alerts,
        "auto_queue_recertification": auto_queue_recertification,
        "require_passing_certification": require_passing_certification,
        "allow_active_waiver": allow_active_waiver,
    },
)
```

`name` 是显示属性，不作为 SLO 行为 authority；若未来将 name 作为外部协议的一部分，再把它加入 digest 并提升 schema version。Revision、scope、所有影响告警/Job 的字段必须进入 digest。

#### 3.1.4 生命周期与权限

- 更新只能使用 `expected_revision` CAS；成功时 revision 增加 1。
- 不物理删除；停用必须写 `disabled_at/disabled_by`，并将 active key 置为 `NULL`。
- SLO Policy 不需要 immutable trigger，但所有变更必须 Tenant audit + generic idempotency。
- Policy 解析顺序固定为：`exact channel -> risk_tier -> global`。
- 不同 Tenant 的相同 `active_scope_key` 可以共存；同一 Tenant 不允许两个 active row。

### 3.2 `tenant_release_quality_scan_schedules`

#### 3.2.1 作用与范围决策

推荐一条 Schedule 对应一个 active Dataset，扫描该 Knowledge Base 的 active/default-serving Release Channels。不要用 `dataset_id` 可空来同时表达 Tenant-wide 和 Dataset-wide 两种语义：可空 composite FK 在 SQLite/MySQL/PostgreSQL 都可能跳过部分 referential check，容易形成跨 Tenant 或未绑定 Dataset 的“合法”伪 authority。

如果未来确实需要 Tenant-wide scan，增加显式的 Tenant scheduler aggregate 或由 service 为每个 Dataset 生成 schedule；不要在本表里加入 polymorphic `scope_type`。

#### 3.2.2 推荐列

```text
id                          VARCHAR(64)       PK, NOT NULL
tenant_id                   VARCHAR(64)       NOT NULL
dataset_id                  VARCHAR(64)       NOT NULL
revision                    INTEGER           NOT NULL
status                      VARCHAR(16)       NOT NULL  # active|paused|archived
interval_seconds            INTEGER           NOT NULL
next_run_at                 DATETIME(6)       NOT NULL
last_enqueued_at            DATETIME(6)       NULL
last_run_id                 VARCHAR(64)       NULL
created_by                  VARCHAR(64)       NOT NULL
updated_by                  VARCHAR(64)       NOT NULL
created_at                  DATETIME(6)       NOT NULL
updated_at                  DATETIME(6)       NOT NULL
```

#### 3.2.3 数据库约束与索引

```text
PK(id)
UNIQUE(tenant_id, id)
UNIQUE(tenant_id, dataset_id)
FK(tenant_id) -> tenants(id)
FK(tenant_id, dataset_id) -> datasets(tenant_id, id)
FK(tenant_id, dataset_id, last_run_id)
  -> tenant_release_quality_scan_runs(tenant_id, dataset_id, id)

revision > 0
status IN ('active','paused','archived')
interval_seconds BETWEEN 300 AND 604800
```

推荐索引：

```text
(status, next_run_at, id)
(tenant_id, dataset_id, status, id)
(tenant_id, dataset_id, next_run_at, id)
```

`last_run_id` 与 Run 的 `schedule_id` 会形成一个与当前 `SourceSchedule/SourceSyncRun` 相似的 nullable circular FK。该关系可以保留，但 Migration downgrade 必须显式先删除两个 cyclic FK，再删表；SQLite 需要 online batch rebuild。若实现团队无法在三种数据库对 cyclic FK 做可靠的 downgrade，则宁可删除 `last_run_id`，用 `(tenant_id,dataset_id,schedule_id,planned_at)` 查询最新 Run，也不能退化成无 Tenant scope 的 `FOREIGN KEY(last_run_id)`。

#### 3.2.4 运行规则

- `active` 才能被 due scanner 选中；`paused/archived` 不得推进 `next_run_at`。
- `next_run_at` 的 due 判断必须使用数据库 UTC clock：复用 `core/db_clock.py` 的 `db_utc_expression()` / `read_db_utc()` 语义。
- miss 多个 interval 时合并为下一个 future slot，不为每个 missed slot 伪造 Run。
- schedule 更新与 API audit 在同一事务提交。
- 不在表中存储原始 Cron expression；固定 interval 更容易跨方言验证，也符合当前 SourceSchedule 的能力边界。

### 3.3 `tenant_release_quality_scan_runs`

#### 3.3.1 作用

Durable SLO scan execution record。它记录“某个 schedule planned slot 是否被领取、是否完成、出现了哪些安全摘要”，不记录 query/result body，也不承担实际 Retrieval Experiment execution。

#### 3.3.2 推荐列

```text
id                          VARCHAR(64)       PK, NOT NULL
tenant_id                   VARCHAR(64)       NOT NULL
dataset_id                  VARCHAR(64)       NOT NULL
schedule_id                 VARCHAR(64)       NOT NULL
schedule_revision           INTEGER           NOT NULL
planned_at                  DATETIME(6)       NOT NULL

status                      VARCHAR(24)       NOT NULL
# pending|claimed|running|completed|failed|cancelled
revision                    INTEGER           NOT NULL

idempotency_key_digest      VARCHAR(64)       NOT NULL
request_hash                VARCHAR(64)       NOT NULL

execution_owner             VARCHAR(128)      NOT NULL DEFAULT ''
execution_lease_until       DATETIME(6)       NULL
execution_heartbeat_at      DATETIME(6)       NULL
claimed_at                  DATETIME(6)       NULL
claimed_by                  VARCHAR(128)      NULL
started_at                  DATETIME(6)       NULL
finished_at                 DATETIME(6)       NULL
attempt_count               INTEGER           NOT NULL DEFAULT 0
max_attempts                INTEGER           NOT NULL DEFAULT 3
next_attempt_at             DATETIME(6)       NULL

observation_count           INTEGER           NOT NULL DEFAULT 0
alert_opened_count          INTEGER           NOT NULL DEFAULT 0
alert_updated_count         INTEGER           NOT NULL DEFAULT 0
recertification_job_count   INTEGER           NOT NULL DEFAULT 0
scan_summary_digest         VARCHAR(64)       NULL
safe_summary_json           JSON              NULL
error_code                  VARCHAR(64)       NULL
safe_error                  VARCHAR(512)      NULL

created_at                  DATETIME(6)       NOT NULL
created_by                  VARCHAR(64)       NOT NULL
updated_at                  DATETIME(6)       NOT NULL
updated_by                  VARCHAR(64)       NOT NULL
request_id                  VARCHAR(128)      NOT NULL
```

`revision` 是 Run 状态 CAS，不要把 `schedule_revision` 当成 Run revision。`schedule_revision` 是该 planned slot 读取到的 Schedule authority；Run 自身每次 claim/heartbeat/complete/cancel 都应该递增 `revision`。

#### 3.3.3 数据库约束与索引

```text
PK(id)
UNIQUE(tenant_id, id)
UNIQUE(tenant_id, idempotency_key_digest)
UNIQUE(tenant_id, dataset_id, schedule_id, schedule_revision, planned_at)

FK(tenant_id) -> tenants(id)
FK(tenant_id, dataset_id) -> datasets(tenant_id, id)
FK(tenant_id, dataset_id, schedule_id)
  -> tenant_release_quality_scan_schedules(tenant_id, dataset_id, id)

revision > 0
schedule_revision > 0
attempt_count BETWEEN 0 AND max_attempts
max_attempts BETWEEN 1 AND 10
observation_count >= 0
alert_opened_count >= 0
alert_updated_count >= 0
recertification_job_count >= 0

status IN ('pending','claimed','running','completed','failed','cancelled')
length(idempotency_key_digest)=64
AND lower(idempotency_key_digest)=idempotency_key_digest
length(request_hash)=64
AND lower(request_hash)=request_hash
```

推荐 Lease shape Check：

```text
(
  status IN ('claimed','running')
  AND execution_owner <> ''
  AND execution_lease_until IS NOT NULL
  AND execution_heartbeat_at IS NOT NULL
  AND claimed_at IS NOT NULL
)
OR (
  status IN ('pending','completed','failed','cancelled')
  AND execution_owner = ''
  AND execution_lease_until IS NULL
  AND execution_heartbeat_at IS NULL
)
```

推荐完成/失败证据 Check：

```text
status <> 'completed'
OR (
  finished_at IS NOT NULL
  AND scan_summary_digest IS NOT NULL
  AND error_code IS NULL
  AND safe_error IS NULL
)

status <> 'failed'
OR (
  finished_at IS NOT NULL
  AND error_code IS NOT NULL
  AND safe_error IS NOT NULL
)

status <> 'cancelled'
OR finished_at IS NOT NULL
```

不在数据库 Check 中比较 `execution_lease_until > NOW()`：三种数据库的 clock expression、事务时间和函数语义不同，且会让静态/离线 DDL 不稳定。Claim/heartbeat/reclaim 必须用 `read_db_utc()` 得到同一个数据库时间基准，再用 CAS 更新。

推荐索引：

```text
(status, next_attempt_at, planned_at, id)
(tenant_id, dataset_id, status, planned_at, id)
(tenant_id, schedule_id, schedule_revision, planned_at, id)
(tenant_id, status, execution_lease_until, id)
```

#### 3.3.4 Lease、幂等和恢复

1. due scanner 读取 active Schedule candidates。
2. 事务内按 `Tenant -> Dataset -> Schedule -> Run` 顺序锁 scope。
3. 重新验证 Schedule revision/status/next_run_at 后生成：

```text
idempotency_key_digest = domain-separated SHA-256 of:
  tenant_id, dataset_id, schedule_id, schedule_revision, planned_at
request_hash = canonical request hash of the same intent
```

4. 依靠 DB UniqueConstraint + nested transaction 处理并发插入；已存在 Run 只返回 existing，不创建第二行。
5. Claim 时把 `pending` 变为 `claimed`，写 owner/lease/heartbeat/revision，提交后才执行扫描。
6. 长任务不得持有数据库事务；worker 在外部执行，按 lease/heartbeat CAS 更新。
7. lease 过期时，新的 worker 只能在锁定后把 `claimed/running` 重新置为 `pending` 或 `failed`，并递增 `attempt_count`；不得直接抢写其他 owner 的 Run。
8. `completed/failed/cancelled` 是终态；若允许人工 retry，必须用显式 `failed -> pending` revision-fenced mutation，并检查 `attempt_count < max_attempts`。
9. `scan_summary_digest` 只对安全的整数计数、Gate state 数量和 authority digest 生成；不把原始扫描输出放入 `safe_summary_json`。

### 3.4 `dataset_release_quality_observations`

#### 3.4.1 作用

一条 Observation 代表一次 SLO scan 对一个 Dataset/Release/Channel 的完整质量 authority 观察。它是 Stage 21 最重要的 append-only evidence，不是当前 Gate 的 mutable projection。

#### 3.4.2 推荐列

```text
id                              VARCHAR(64)       PK, NOT NULL
tenant_id                       VARCHAR(64)       NOT NULL
dataset_id                      VARCHAR(64)       NOT NULL
release_id                      VARCHAR(64)       NOT NULL
channel_id                      VARCHAR(128)      NOT NULL
scan_run_id                     VARCHAR(64)       NOT NULL

slo_policy_id                   VARCHAR(64)       NULL
slo_policy_revision             INTEGER           NULL
slo_policy_digest               VARCHAR(64)       NULL

gate_state                      VARCHAR(16)       NOT NULL
# not_required|passed|blocked|waived|unavailable
gate_reason                     VARCHAR(64)       NOT NULL
quality_gate_digest             VARCHAR(64)       NOT NULL

certification_id                VARCHAR(64)       NULL
certification_digest            VARCHAR(64)       NULL
quality_evidence_digest         VARCHAR(64)       NULL
certification_valid_until       DATETIME(6)       NULL

waiver_id                       VARCHAR(64)       NULL
waiver_digest                   VARCHAR(64)       NULL
waiver_expires_at               DATETIME(6)       NULL

minutes_to_certification_expiry BIGINT            NULL
minutes_to_waiver_expiry       BIGINT            NULL
severity                        VARCHAR(16)       NOT NULL
# healthy|warning|critical|unavailable
observation_digest              VARCHAR(64)       NOT NULL
observed_at                     DATETIME(6)       NOT NULL
observed_by                     VARCHAR(64)       NOT NULL
request_id                      VARCHAR(128)      NOT NULL
```

建议额外保存 `channel_revision`、`release_mutation_generation`、`release_serving_generation` 三个整数 authority。它们能让“该次观察看到的 Channel/Release 版本”脱离后续 mutable row 重建；如果实现保持六表的最小列集，也至少把它们放入 `quality_gate_digest` 的 canonical envelope，并由 preflight 重算。

#### 3.4.3 Tenant-leading FK 与 Unique

```text
PK(id)
UNIQUE(tenant_id, id)
UNIQUE(tenant_id, dataset_id, scan_run_id, release_id, channel_id)

FK(tenant_id) -> tenants(id)
FK(tenant_id, dataset_id) -> datasets(tenant_id, id)
FK(tenant_id, dataset_id, release_id)
  -> dataset_release_manifests(tenant_id, dataset_id, id)
FK(tenant_id, channel_id)
  -> tenant_release_channels(tenant_id, id)
FK(tenant_id, dataset_id, scan_run_id)
  -> tenant_release_quality_scan_runs(tenant_id, dataset_id, id)
FK(tenant_id, slo_policy_id)
  -> tenant_release_quality_slo_policies(tenant_id, id)
FK(tenant_id, dataset_id, certification_id)
  -> dataset_release_quality_certifications(tenant_id, dataset_id, id)
FK(tenant_id, dataset_id, waiver_id)
  -> dataset_release_quality_waivers(tenant_id, dataset_id, id)
```

所有可空的复合引用必须遵循 all-or-none：例如 `certification_id` 为 `NULL` 时，`certification_digest`、`quality_evidence_digest`、`certification_valid_until` 和 `minutes_to_certification_expiry` 都必须为 `NULL`；如果 certification id 非空，则这些 authority 字段必须全部存在。

#### 3.4.4 推荐 Check

```text
gate_state IN ('not_required','passed','blocked','waived','unavailable')
severity IN ('healthy','warning','critical','unavailable')

(slo_policy_id IS NULL
 AND slo_policy_revision IS NULL
 AND slo_policy_digest IS NULL)
OR
(slo_policy_id IS NOT NULL
 AND slo_policy_revision > 0
 AND slo_policy_digest IS NOT NULL)

(certification_id IS NULL
 AND certification_digest IS NULL
 AND quality_evidence_digest IS NULL
 AND certification_valid_until IS NULL
 AND minutes_to_certification_expiry IS NULL)
OR
(certification_id IS NOT NULL
 AND certification_digest IS NOT NULL
 AND quality_evidence_digest IS NOT NULL
 AND certification_valid_until IS NOT NULL
 AND minutes_to_certification_expiry IS NOT NULL)

(waiver_id IS NULL
 AND waiver_digest IS NULL
 AND waiver_expires_at IS NULL
 AND minutes_to_waiver_expiry IS NULL)
OR
(waiver_id IS NOT NULL
 AND waiver_digest IS NOT NULL
 AND waiver_expires_at IS NOT NULL
 AND minutes_to_waiver_expiry IS NOT NULL)

(gate_state='passed'  -> certification_id IS NOT NULL)
(gate_state='waived'  -> waiver_id IS NOT NULL)
(gate_state='not_required' -> slo_policy_id IS NULL)
(severity='unavailable' -> gate_state='unavailable')

length(quality_gate_digest)=64
AND lower(quality_gate_digest)=quality_gate_digest
length(observation_digest)=64
AND lower(observation_digest)=observation_digest
```

上面的 `->` 是设计语义，不能直接作为所有数据库的 SQL；实际 Migration 应展开为 `(gate_state <> 'passed' OR certification_id IS NOT NULL)` 形式。Digest 的 strict hex 仍由 service/preflight 的 `re.fullmatch(r'[0-9a-f]{64}', ...)` 补强，因为仅 `length + lower` 无法在三种数据库一致拒绝全部非 hex 字符。

#### 3.4.5 Immutable trigger

Observation 必须有两条 immutable guard：

```text
trg_dataset_release_quality_observations_no_update
trg_dataset_release_quality_observations_no_delete
```

实现方式沿用 Stage 20，但建议为 0031 使用独立 function/name，避免 downgrade 误删 Stage 20 function：

- SQLite：`BEFORE UPDATE/DELETE ON dataset_release_quality_observations`，`RAISE(ABORT, ...)`。
- MySQL/MariaDB：`BEFORE UPDATE/DELETE ... FOR EACH ROW SIGNAL SQLSTATE '45000'`。
- PostgreSQL：独立 `rag4c_quality_operations_immutable()` function + `EXECUTE FUNCTION`。

Catalog readiness 必须同时验证：

1. trigger name；
2. trigger timing/event；
3. **真实 target table**；
4. abort/signal/exception body；
5. PostgreSQL function 存在且被实际 trigger 调用。

这不是可选项。Stage 20 曾经需要专门补强 trigger target 检查，0031 不应只通过 trigger name 判断安全。

#### 3.4.6 Observation digest envelope

推荐：

```python
observation_digest = canonical_quality_digest(
    "quality_observation",
    {
        "tenant_id": tenant_id,
        "dataset_id": dataset_id,
        "release_id": release_id,
        "channel_id": channel_id,
        "scan_run_id": scan_run_id,
        "slo_policy_id": slo_policy_id,
        "slo_policy_revision": slo_policy_revision,
        "slo_policy_digest": slo_policy_digest,
        "gate_state": gate_state,
        "gate_reason": gate_reason,
        "quality_gate_digest": quality_gate_digest,
        "certification_id": certification_id,
        "certification_digest": certification_digest,
        "quality_evidence_digest": quality_evidence_digest,
        "certification_valid_until": canonical_utc(certification_valid_until),
        "waiver_id": waiver_id,
        "waiver_digest": waiver_digest,
        "waiver_expires_at": canonical_utc(waiver_expires_at),
        "minutes_to_certification_expiry": minutes_to_certification_expiry,
        "minutes_to_waiver_expiry": minutes_to_waiver_expiry,
        "severity": severity,
        "observed_at": canonical_utc(observed_at),
    },
)
```

`observed_by`、`request_id` 是否进入 digest 要固定成 schema contract；推荐将 actor/request 作为 audit metadata，不进入“业务观察事实” digest，避免相同 authority 因不同 worker 重新观察而产生不可比较的 digest。

### 3.5 `dataset_release_quality_alerts`

#### 3.5.1 作用

Alert 是告警生命周期，不是 Observation 的替代品。Observation 记录每次看到的事实；Alert 记录一个告警周期是否 open、acknowledged、suppressed 或 resolved。

#### 3.5.2 推荐列

```text
id                          VARCHAR(64)       PK, NOT NULL
tenant_id                   VARCHAR(64)       NOT NULL
dataset_id                  VARCHAR(64)       NOT NULL
release_id                  VARCHAR(64)       NOT NULL
channel_id                  VARCHAR(128)      NOT NULL
alert_type                  VARCHAR(64)       NOT NULL
# certification_expiring|certification_expired|certification_stale
# waiver_expiring|waiver_expired|quality_gate_blocked
# quality_authority_unavailable
severity                    VARCHAR(16)       NOT NULL  # warning|critical
status                      VARCHAR(16)       NOT NULL
# open|acknowledged|suppressed|resolved
active_alert_key             VARCHAR(64)       NULL
revision                    INTEGER           NOT NULL

source_observation_id       VARCHAR(64)       NOT NULL
source_observation_digest   VARCHAR(64)       NOT NULL
first_observed_at            DATETIME(6)       NOT NULL
last_observed_at             DATETIME(6)       NOT NULL
opened_at                    DATETIME(6)       NOT NULL

acknowledged_at              DATETIME(6)       NULL
acknowledged_by              VARCHAR(64)       NULL
acknowledgement_comment      VARCHAR(512)      NULL

suppressed_at                DATETIME(6)       NULL
suppressed_by                VARCHAR(64)       NULL
suppression_reason           VARCHAR(512)      NULL
suppressed_until              DATETIME(6)       NULL

resolved_at                  DATETIME(6)       NULL
resolved_by                  VARCHAR(64)       NULL
resolution_comment           VARCHAR(512)      NULL

created_at                  DATETIME(6)       NOT NULL
created_by                  VARCHAR(64)       NOT NULL
updated_at                  DATETIME(6)       NOT NULL
updated_by                  VARCHAR(64)       NOT NULL
request_id                  VARCHAR(128)      NOT NULL
```

#### 3.5.3 Active identity：用 digest + NULL，而不是 partial index

`active_alert_key` 应由以下 identity 计算，而不是由用户输入：

```text
quality_alert_active:v1
  tenant_id
  dataset_id
  release_id
  channel_id
  alert_type
```

推荐 `active_alert_key` 为 64 位 lowercase SHA-256 digest，并加：

```text
UNIQUE(tenant_id, active_alert_key)
```

生命周期语义：

```text
open / acknowledged / suppressed -> active_alert_key 非 NULL
resolved                         -> active_alert_key 为 NULL
```

这样同一 Tenant/Release/Channel/alert_type 在 active cycle 中只能有一行；resolved 后可以开启新 cycle。`suppressed` 仍占有 active identity，直到 scanner 通过 revision-fenced transition 将它恢复为 open 或结束为 resolved；不能在 suppression 期间创建第二个同类 Alert。

该模式利用三种数据库对普通 UniqueConstraint 的共同 NULL 行为，避免：

```sql
UNIQUE (...) WHERE status IN (...)
```

这种 partial index 在 SQLAlchemy metadata、SQLite 迁移、MySQL/MariaDB 版本和 Catalog reflection 上都更难保持同一 contract。

#### 3.5.4 Tenant-leading FK 与约束

```text
PK(id)
UNIQUE(tenant_id, id)
UNIQUE(tenant_id, active_alert_key)

FK(tenant_id) -> tenants(id)
FK(tenant_id, dataset_id) -> datasets(tenant_id, id)
FK(tenant_id, dataset_id, release_id)
  -> dataset_release_manifests(tenant_id, dataset_id, id)
FK(tenant_id, channel_id)
  -> tenant_release_channels(tenant_id, id)
FK(tenant_id, dataset_id, source_observation_id)
  -> dataset_release_quality_observations(tenant_id, dataset_id, id)
```

推荐 Check：

```text
alert_type IN (
  'certification_expiring',
  'certification_expired',
  'certification_stale',
  'waiver_expiring',
  'waiver_expired',
  'quality_gate_blocked',
  'quality_authority_unavailable'
)
severity IN ('warning','critical')
status IN ('open','acknowledged','suppressed','resolved')
revision > 0

(
  status IN ('open','acknowledged','suppressed')
  AND active_alert_key IS NOT NULL
)
OR (
  status='resolved'
  AND active_alert_key IS NULL
)

acknowledged_at IS NULL AND acknowledged_by IS NULL
OR acknowledged_at IS NOT NULL AND acknowledged_by IS NOT NULL

suppressed_at IS NULL AND suppressed_by IS NULL AND suppressed_until IS NULL
OR suppressed_at IS NOT NULL
   AND suppressed_by IS NOT NULL
   AND suppressed_until IS NOT NULL

resolved_at IS NULL AND resolved_by IS NULL
OR resolved_at IS NOT NULL AND resolved_by IS NOT NULL

length(active_alert_key)=64 OR active_alert_key IS NULL
length(source_observation_digest)=64
AND lower(source_observation_digest)=source_observation_digest
```

`resolved` 记录可以保留历史 acknowledgement/suppression fields；数据库只要求每个 evidence pair all-or-none。是否允许 `resolved_at < suppressed_until`、是否允许 `last_observed_at < first_observed_at` 等时间关系，应由 service 使用同一 DB clock 校验，不能硬编码成跨方言 `NOW()` Check。

推荐索引：

```text
(tenant_id, dataset_id, status, severity, updated_at, id)
(tenant_id, dataset_id, release_id, channel_id, alert_type, status, id)
(tenant_id, dataset_id, source_observation_id, id)
(tenant_id, status, suppressed_until, id)
```

#### 3.5.5 Lifecycle 规则

```text
open -> acknowledged
open -> suppressed
open -> resolved
acknowledged -> suppressed
acknowledged -> resolved
suppressed -> open       # suppression 到期且问题仍存在
suppressed -> resolved   # 问题消失
```

- `resolved` 是告警周期终态；新周期插入新 row。
- `acknowledge/resolve/suppress` 需要 `expected_revision`，每次成功递增 revision，并写 Tenant Audit。
- `source_observation_id`、`source_observation_digest`、target identity、alert_type 不允许在普通 lifecycle update 中更改。若需要更严格的 DB 保护，可增加“禁止 identity columns update”的专用 trigger；不要把 Alert 做成全表 immutable，否则无法推进生命周期。
- Alert 不能因为 UI 重新加载而自动 resolve；只有 scanner 依据新的 Observation 或有明确 operator action 才能 resolve。
- `max_open_alerts` 超限时不能静默丢弃 Alert；应把 Scan Run 置为 failed/degraded，并记录安全 `error_code`，否则运营面会产生未审计的告警缺失。

### 3.6 `dataset_release_quality_recertification_jobs`

#### 3.6.1 作用

持久化“需要重新准备质量 Certification”的工作项。Job 不是 Retrieval Experiment runner，也不是 Approval Request；它只把可验证的 authority context 带到 `ready_to_certify`，最终由显式 operator action 调用 Stage 20 `certify_release()`。

#### 3.6.2 推荐列

```text
id                          VARCHAR(64)       PK, NOT NULL
tenant_id                   VARCHAR(64)       NOT NULL
dataset_id                  VARCHAR(64)       NOT NULL
release_id                  VARCHAR(64)       NOT NULL
channel_id                  VARCHAR(128)      NOT NULL

baseline_id                 VARCHAR(64)       NOT NULL
quality_policy_id           VARCHAR(64)       NOT NULL
quality_policy_revision     INTEGER           NOT NULL
quality_policy_digest       VARCHAR(64)       NOT NULL
slo_policy_id               VARCHAR(64)       NOT NULL
slo_policy_revision         INTEGER           NOT NULL
slo_policy_digest           VARCHAR(64)       NOT NULL

source_observation_id       VARCHAR(64)       NULL
source_alert_id             VARCHAR(64)       NULL
cycle_key                   VARCHAR(64)       NOT NULL
idempotency_key_digest      VARCHAR(64)       NOT NULL
request_hash                VARCHAR(64)       NOT NULL

trigger                     VARCHAR(32)       NOT NULL
# manual|certification_warning|certification_expired
# stale_evidence|alert_escalation
status                      VARCHAR(24)       NOT NULL
# pending|claimed|awaiting_evidence|ready_to_certify
# completed|failed|cancelled
revision                    INTEGER           NOT NULL

expected_manifest_digest    VARCHAR(64)       NOT NULL
expected_evidence_digest    VARCHAR(64)       NULL
expected_channel_revision   INTEGER           NOT NULL
expected_baseline_digest    VARCHAR(64)       NOT NULL

execution_owner             VARCHAR(128)      NOT NULL DEFAULT ''
execution_lease_until       DATETIME(6)       NULL
execution_heartbeat_at      DATETIME(6)       NULL
claimed_at                  DATETIME(6)       NULL
claimed_by                  VARCHAR(128)      NULL
attempt_count               INTEGER           NOT NULL DEFAULT 0
max_attempts                INTEGER           NOT NULL DEFAULT 3
next_attempt_at             DATETIME(6)       NULL

result_certification_id     VARCHAR(64)       NULL
result_certification_digest VARCHAR(64)       NULL
error_code                  VARCHAR(64)       NULL
safe_error                  VARCHAR(512)      NULL
created_at                  DATETIME(6)       NOT NULL
created_by                  VARCHAR(64)       NOT NULL
updated_at                  DATETIME(6)       NOT NULL
updated_by                  VARCHAR(64)       NOT NULL
completed_at                DATETIME(6)       NULL
cancelled_at                DATETIME(6)       NULL
cancelled_by                VARCHAR(64)       NULL
request_id                  VARCHAR(128)      NOT NULL
```

#### 3.6.3 为什么要有 `cycle_key` 和 `idempotency_key_digest`

两者语义不同：

- `idempotency_key_digest`：一次 API/system request 的 replay identity，受 actor + raw header 归一化规则控制；适合防止重复请求。
- `cycle_key`：同一 Release/Channel/Policy/Baseline/Manifest/Evidence authority 周期的业务去重 identity，适合合并多个 Scan Run、Alert 和手工入口。

推荐：

```text
cycle_key = canonical_quality_digest(
  "recertification_cycle",
  {
    "tenant_id": tenant_id,
    "dataset_id": dataset_id,
    "release_id": release_id,
    "channel_id": channel_id,
    "baseline_id": baseline_id,
    "baseline_digest": baseline_digest,
    "quality_policy_id": quality_policy_id,
    "quality_policy_revision": quality_policy_revision,
    "quality_policy_digest": quality_policy_digest,
    "expected_manifest_digest": expected_manifest_digest,
    "expected_evidence_digest": expected_evidence_digest,
    "expected_channel_revision": expected_channel_revision,
  },
)
```

数据库约束：

```text
UNIQUE(tenant_id, cycle_key)
UNIQUE(tenant_id, idempotency_key_digest)
```

如果同一 cycle 已经有 pending/claimed/awaiting/ready Job，后续自动触发必须返回 existing/coalesced；不要插入第二行。若 cycle 的 Manifest/Evidence digest 改变，则自然形成新 cycle。若之前 Job failed，显式 retry 可以在原 row 上 revision-fenced 重置为 pending，或创建新的 cycle version；不能通过修改 cycle_key 伪造历史。

#### 3.6.4 Tenant-leading FK

```text
PK(id)
UNIQUE(tenant_id, id)
UNIQUE(tenant_id, cycle_key)
UNIQUE(tenant_id, idempotency_key_digest)

FK(tenant_id) -> tenants(id)
FK(tenant_id, dataset_id) -> datasets(tenant_id, id)
FK(tenant_id, dataset_id, release_id)
  -> dataset_release_manifests(tenant_id, dataset_id, id)
FK(tenant_id, channel_id)
  -> tenant_release_channels(tenant_id, id)
FK(tenant_id, dataset_id, baseline_id)
  -> dataset_quality_baselines(tenant_id, dataset_id, id)
FK(tenant_id, quality_policy_id)
  -> tenant_release_quality_gate_policies(tenant_id, id)
FK(tenant_id, slo_policy_id)
  -> tenant_release_quality_slo_policies(tenant_id, id)
FK(tenant_id, dataset_id, source_observation_id)
  -> dataset_release_quality_observations(tenant_id, dataset_id, id)
FK(tenant_id, dataset_id, source_alert_id)
  -> dataset_release_quality_alerts(tenant_id, dataset_id, id)
FK(tenant_id, dataset_id, result_certification_id)
  -> dataset_release_quality_certifications(tenant_id, dataset_id, id)
```

所有可空 FK 必须 all-or-none：`source_alert_id` 为空时不要求 alert；非空时 Tenant/Dataset 必须完全匹配。`result_certification_id` 只有 completed 才允许非空。

#### 3.6.5 状态与 Lease Check

推荐状态图：

```text
pending
  -> claimed
  -> cancelled

claimed
  -> awaiting_evidence
  -> ready_to_certify
  -> failed
  -> cancelled

awaiting_evidence
  -> ready_to_certify
  -> failed
  -> cancelled

ready_to_certify
  -> claimed       # 显式执行前再次 claim
  -> cancelled

failed
  -> pending       # 仅显式 retry 且 attempt_count < max_attempts
  -> cancelled

completed       terminal
cancelled       terminal
```

Database Check 约束状态 shape，而不是尝试在 CHECK 中表达完整图：

```text
status IN (
  'pending','claimed','awaiting_evidence','ready_to_certify',
  'completed','failed','cancelled'
)
trigger IN (
  'manual','certification_warning','certification_expired',
  'stale_evidence','alert_escalation'
)
revision > 0
attempt_count BETWEEN 0 AND max_attempts
max_attempts BETWEEN 1 AND 10
expected_channel_revision > 0

(status='claimed'
 AND execution_owner <> ''
 AND execution_lease_until IS NOT NULL
 AND execution_heartbeat_at IS NOT NULL
 AND claimed_at IS NOT NULL)
OR
(status<>'claimed'
 AND execution_owner = ''
 AND execution_lease_until IS NULL
 AND execution_heartbeat_at IS NULL)

(status IN ('ready_to_certify','completed')
 -> expected_evidence_digest IS NOT NULL)
(status='completed'
 -> result_certification_id IS NOT NULL
    AND result_certification_digest IS NOT NULL
    AND completed_at IS NOT NULL)
(status='failed'
 -> error_code IS NOT NULL AND safe_error IS NOT NULL)
(status='cancelled'
 -> cancelled_at IS NOT NULL AND cancelled_by IS NOT NULL)
```

`awaiting_evidence` 不代表系统会自动改 Judgment；它只代表当前 evidence 尚未满足 Job 的 expected authority。只有当 service 重新读取 Experiment/Judgment 并得到新的完整 digest，才允许进入 `ready_to_certify`。

#### 3.6.6 Job 执行边界

- Claim transaction 提交后才能进行长时间工作；不持有 Job row lock 调用外部服务。
- 不在 Job worker 内直接创建 Waiver、Approval Request、Release Manifest 或 Source Sync。
- `ready_to_certify -> completed` 必须验证：
  - 当前 Manifest digest == expected；
  - Channel revision == expected；
  - Baseline digest == expected；
  - Stage 20 Quality Policy revision/digest 未变；
  - 当前 Experiment/Judgment evidence digest == expected；
  - 调用 Stage 20 `certify_release()` 的结果是新的或幂等 unchanged Certification。
- `certify_release()` 返回 `unavailable/conflict` 时，Job 只能进入安全 failed/awaiting_evidence，不得展示 completed。
- Job 完成后由下一次只读 SLO scan 生成新的 Observation；不要在 Job 内手工修改 Alert 为 resolved，除非同一事务中依据新 Observation 完成完整的 Alert CAS 流程。

## 4. 跨 SQLite / MySQL(MariaDB) / PostgreSQL 的通用约束原则

### 4.1 类型与时间

| 领域 | 推荐 | 不推荐 |
|---|---|---|
| IDs | `VARCHAR(64/128)`，与 Stage 20/SourceSchedule/Approval 保持一致 | 各表自定义 UUID 二进制格式 |
| revision/count | `INTEGER`，非负/正数 Check | Python float 或字符串数字 |
| sequence/large counter | `BIGINT` | 在 SQLite 依赖无界 unsigned 类型 |
| datetime | SQLAlchemy `DateTime`，MySQL/MariaDB variant `DATETIME(fsp=6)` | 只在 MySQL 使用 `TIMESTAMP` |
| UTC | 持久化 naive UTC + DB clock helper contract | 混用 local time、浏览器 time、server time |
| JSON | SQLAlchemy `JSON`，写入前 canonical/safe validation | 依赖 JSON path CHECK 禁止敏感字段 |
| boolean | SQLAlchemy `Boolean` + service exact `type(value) is bool` | `bool(1)`、`bool('false')` 之类隐式转换 |

当前 `core/db_clock.py` 已定义：

```text
MySQL/MariaDB -> UTC_TIMESTAMP(6)
PostgreSQL     -> clock_timestamp() AT TIME ZONE 'UTC'
SQLite         -> strftime('%Y-%m-%d %H:%M:%f', 'now')
```

Stage 21 的 due、lease、expiry、alert suppression 都应以该 DB clock 为 authority。数据库插入 timestamp default 可以存在，但涉及 gate/lease 的比较必须显式取 DB now；不要让数据库 server default、Python `datetime.utcnow()` 和 browser clock 互相竞争。

### 4.2 FK 设计

- 每一张 Stage 21 child table 都有显式 `FK(tenant_id) -> tenants(id)`，即使其他 composite FK 已包含 Tenant。
- 每一条跨 Tenant/Dataset 的 FK 第一列都必须是 `tenant_id`。
- 父表必须先有精确匹配的 UniqueConstraint：

```text
(tenant_id, id)
(tenant_id, dataset_id, id)
(tenant_id, dataset_id, schedule_id)
```

- 不允许使用只包含 `id` 的 unscoped FK 连接 Tenant-owned resource。
- 不使用 `ON DELETE CASCADE`。Tenant、Dataset、Release、Channel、Stage 20 authority 应通过 archive/retire/disable 管理，不能因父记录删除而静默删除 Observations、Alerts、Jobs。
- 如果父表必须物理删除，删除前 preflight 必须证明所有引用为空；默认 `RESTRICT/NO ACTION`，不能自动级联清理审计事实。
- 对 `last_run_id`、`source_observation_id`、`result_certification_id` 等可空引用，Migration/ORM/service 三层都必须保持 all-or-none 和 Tenant/Dataset scope。

### 4.3 CheckConstraint 的可移植边界

三种数据库共同稳定的表达式：

```text
IN (...)、BETWEEN、>、>=、IS NULL、IS NOT NULL、AND、OR
length(column)=N
lower(column)=column
```

应谨慎处理：

- 字符串拼接：Policy canonical scope 使用 dialect-specific expression；Migration 必须测试 SQLite/PostgreSQL `||` 与 MySQL/MariaDB `CONCAT(...)`。
- Boolean：MySQL reflection 可能表现为 `TINYINT(1)`，Catalog validator 需要 normalization；不要把 `TRUE`/`FALSE` 的方言字符串写死在 readiness。
- Regex：不要用 `REGEXP` 作为 digest hex 的唯一数据库约束；SQLite 默认没有同样的 regex function，MySQL/PostgreSQL 行为和 collation 也不同。
- 时间函数：不要在 Check 中调用 `NOW()`/`CURRENT_TIMESTAMP` 作为 lease/expiry authority。
- JSON path：不要用三种数据库不同的 JSON operators 作为安全合约；由 service/preflight canonicalize 和禁止字段扫描。

### 4.4 Digest contract

Stage 20 已有两种相关能力：

```text
_snapshot_quality_digest(namespace, value)
canonical_quality_digest(namespace, value)
```

0031 应保持 domain separation：

```text
slo_policy
scan_intent
scan_summary
quality_observation
quality_alert_active
recertification_cycle
```

所有保存的 digest：

```text
VARCHAR(64) NOT NULL
length(digest)=64
lower(digest)=digest
```

同时在 service/readiness/preflight 使用：

```python
re.fullmatch(r"[0-9a-f]{64}", value)
```

读取已存 authority 时不能只检查格式；必须按当前行字段重算 digest，再比较 stored digest。特别是：

- SLO policy digest：重算 scope、revision、horizon、flags；
- Observation digest：重算 gate envelope、expiry、severity、source authority；
- Alert active key：重算 target identity；
- Job cycle key：重算 Release/Policy/Baseline/Evidence expected authority；
- Scan Run summary digest：重算安全 summary JSON。

Digest 版本必须进入 namespace 或 payload schema version；算法演进时不能静默改变同一 namespace 的含义。

### 4.5 Safe JSON

仅允许保存用于重放/审计的安全投影：

```text
null、exact bool、exact int、有限 float、bounded string、list、mapping
```

写入和读取都应检查：

- 最大深度；
- 最大数组/对象元素数；
- key 必须是非空 string 且有长度上限；
- 拒绝 NaN/Infinity；
- 拒绝未经限制的任意 Python object；
- 禁止 key 或 path 包含：

```text
body、content、raw_result、query、query_text、raw_query、note
password、secret、token、authorization、cookie、ticket
credential、api_key、client_secret、connection_string、database_url
```

- `safe_summary_json` 不得存 Experiment query、document body、judgment note、Approval ticket 或 raw Idempotency-Key。
- API replay payload 继续交给 `complete_tenant_mutation()` 检查；不能绕过 generic ledger 的敏感信息防护。

## 5. 推荐锁顺序与事务边界

### 5.1 通用前缀

所有 API mutation 复用 Stage 20 当前 `_mutation_scope()` 的原则（证据：`core/enterprise_release_quality_service.py:197-251`）：

```text
1. normalize raw idempotency key
2. acquire tenant/actor idempotency lock
3. acquire engine serialization lock
4. open Session + transaction
5. ensure Catalog capability
6. lock Tenant FOR UPDATE
7. validate actor/membership/permission
8. reserve generic tenant mutation
```

Idempotency lock 是进程/分布式 synchronization；Tenant row lock 是数据库 authority。两者不要互换顺序。

### 5.2 0031 canonical database row lock order

对任何同时操作多个 Stage 20/21 authority 的事务，统一采用：

```text
Tenant
→ Dataset
→ Workspace/ownership（如果调用链涉及）
→ Release Manifest
→ Release Entries（按 resource_id/id 排序）
→ Channel（按 channel_id 排序）
→ Stage 20 Quality Policy
→ Stage 20 Baseline / Baseline Items
→ Retrieval Experiments（按 sequence/id）
→ Retrieval Judgments（按 result_rank/created_by/id）
→ Stage 21 SLO Policy
→ Stage 21 Schedule
→ Stage 21 Scan Run
→ Stage 21 Observation/Alert/Recertification Job（按 id 排序）
→ Audit / idempotency completion
```

说明：

- Stage 20 evidence collector 在 `lock_for_update=True` 时已经按确定性顺序锁 Experiment/Judgment；Stage 21 authoritative scan 必须传该参数。
- 现有 `resolve_release_quality_gate()` 具有 `lock_evidence` 参数；Stage 21 scan、Job readiness 和涉及发布决策的重新验证必须使用 `lock_evidence=True`。
- Schedule-only mutation 只需要锁 `Tenant -> Dataset -> Schedule`；不能为了改 interval 锁整个 Tenant 下所有 Release。
- Alert ack/resolve 只需要 `Tenant -> Dataset -> Alert`；它不应额外锁 Retrieval evidence。
- Scan Run claim 使用 `Tenant -> Dataset -> Schedule -> Run`；claim 后提交并在事务外执行长任务。
- Scan finalization 若要同时写 Observation/Alert/Job，先按上述顺序重新锁目标 authority，不能先锁 Job 再回头锁 Release，否则容易与 Promotion/Certification 形成死锁。
- Job 执行不得在持有 Job row lock 时直接调用会创建独立 transaction 的 `certify_release()`。推荐：

```text
claim Job transaction -> commit
    ↓
调用 Stage 20 certify_release()（独立 authority transaction）
    ↓
完成 Job transaction：Tenant → Dataset → Release/Channel → Quality → Job
```

### 5.3 方言差异

- MySQL/MariaDB/PostgreSQL：锁查询可使用 `with_for_update(skip_locked=True)` 领取队列；必须在结果为空时正常提交，不把 skip locked 当成失败。
- SQLite：没有真正的 row-level `FOR UPDATE`；使用 `BEGIN IMMEDIATE` + `engine_serialization_lock`，并用短事务减少 writer contention。
- 所有数据库都应按同样的 `(tenant_id, dataset_id, id)` 排序；不要让 PostgreSQL/MySQL 按不同默认计划锁行。
- 不持有数据库锁调用外部 HTTP、通知、文件下载、模型服务或浏览器；这些都不在 0031 authority transaction 内。

## 6. Readiness / Preflight contract

### 6.1 Revision 与 capability

0031 应新增：

```text
ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REVISION
  = "0031_enterprise_release_quality_operations"

ENTERPRISE_RELEASE_QUALITY_OPERATIONS_TABLES
  = six table names

inspect_enterprise_release_quality_operations_capability(bind)
```

Head 逻辑应同时保留 Stage 20 contract：

```text
0030 -> Stage 21 not_available（不是数据 blocker）
0031 + six tables complete -> ready
0031 + any table/column/FK/check/trigger missing -> unavailable
partial tables at 0030 -> unavailable / schema incomplete
multiple alembic revisions -> unavailable
unknown/future revision -> unavailable
```

不能在表不存在时把“尚未迁移”误报成 active quality data blocker；但部分存在、版本已标记 0031 而 contract 不完整，必须 fail closed。

### 6.2 Schema preflight

只读检查至少包含：

1. 0031 六张表与全部 required columns；
2. Not Null、类型长度、MySQL `DATETIME(6)`；
3. Tenant-leading FK 的 constrained/referred columns；
4. 精确 UniqueConstraint / index；
5. Policy canonical scope Check；
6. Alert active identity/lifecycle Check；
7. Run/Job lease shape Check；
8. Observation immutable triggers 的 name/event/timing/target/body/function；
9. PostgreSQL function 的真实存在性和 trigger binding；
10. Approval action contract 未被 0031 破坏；
11. Stage 20 capability 仍为 ready。

Readiness 不应只检查表存在；应像当前 `core/catalog_schema.py:3883-3962` 一样反射并比对 exact contract。对于 SQLite/MySQL/PostgreSQL，分别做 dialect-aware normalization 后再比较语义。

### 6.3 Data preflight

只读数据检查至少包含：

```text
SLO Policy
- 非 canonical active_scope_key
- 同 Tenant/scope 重复 active policy
- active/disabled lifecycle evidence 错误
- digest 格式或重算不一致
- horizon / max_open_alerts 越界

Schedule
- orphan Tenant/Dataset
- duplicate tenant+dataset schedule
- active schedule next_run_at 无效
- archived/paused schedule 被错误视为 due
- last_run_id 跨 Tenant/Dataset

Scan Run
- orphan schedule
- schedule_revision 不匹配
- duplicate idempotency/cycle slot
- status 与 lease fields 矛盾
- 过期 lease 未被标记可回收
- completed/failed/cancelled 缺 evidence
- unsafe summary/error

Observation
- orphan run/release/channel/SLO policy/Certification/Waiver
- cross Tenant/Dataset/Release/Channel binding
- stored digest 与 live envelope 不一致
- duplicate run+target observation
- Certification/Waiver pair fields 不完整
- safe JSON 包含 query/body/note/ticket/secret

Alert
- duplicate active_alert_key
- resolved row 仍占 active key
- active row 缺 active key
- source Observation 不存在或 digest 不匹配
- lifecycle evidence 缺失
- suppression window malformed

Recertification Job
- orphan parent references
- cycle_key 重复或重算不一致
- expected Manifest/Baseline/Policy/Evidence digest 不合法
- status/lease/result/error shape 不一致
- ready_to_certify 没有 expected evidence
- completed 没有 result Certification
- failed/cancelled 缺安全 evidence
```

### 6.4 Runtime authority revalidation

对 high-risk/default-serving active Channel，preflight 应在只读 Session 中重新调用等价于：

```text
resolve_release_quality_gate(..., lock_evidence=False)
```

的安全读取，并在需要证明 currentness 的地方重算：

- Release Manifest/Entry authority；
- Stage 20 Certification envelope；
- Waiver Approval/consumed ticket/event chain；
- current evidence digest。

Preflight 本身是只读的，不能使用 `lock_evidence=True` 造成长时间写锁，也不能为了修复数据自动写 Observation、Alert 或 Job。它应输出：

```json
{
  "read_only": true,
  "mutations_performed": false,
  "automatic_actions": [],
  "schema_status": "ready|not_available|unavailable",
  "blockers": []
}
```

如果扫描/Job 需要执行，则由后续明确批准的 worker/操作 API 完成；Migration upgrade 预检绝不能顺便执行它。

### 6.5 Upgrade preflight 与 runbook

`enterprise_catalog_upgrade.py` 应新增 `release_quality_operations` section，保持当前 Stage 20 runbook 的边界：

- 0030 → 0031 是单步 upgrade；
- SQLite offline upgrade fail closed；
- MySQL/PostgreSQL 可生成 offline SQL 供 DBA 审阅；
- 只有 schema/read-only checks；
- 不自动创建 SLO Policy；
- 不自动创建 Schedule；
- 不自动执行 Scan Run；
- 不自动创建 Observation/Alert/Job；
- 不自动触发 Certification、Waiver、Approval、Promotion、Rollback、Pin、Source Sync、Restore、Delete。

## 7. Downgrade blocker 与删除顺序

### 7.1 Blocker 规则

`0031` downgrade 只能 online 执行，并且必须先进行只读 preflight。只要以下任一项非空，就阻断退回 0030：

```text
tenant_release_quality_slo_policies
 tenant_release_quality_scan_schedules
 tenant_release_quality_scan_runs
 dataset_release_quality_observations
 dataset_release_quality_alerts
 dataset_release_quality_recertification_jobs
```

最安全策略是六张表有任意历史 row 都阻断，因为 Observation/Alert/Job 是运营审计事实，删除后无法恢复“何时开始过期、谁 acknowledge、何时排队再认证”。

Stage 21 若使用共享 `tenant_control_mutation_requests`，不能因为共享 ledger 的其他业务 row 阻断 downgrade；但必须按精确 operation/resource_type 列表检查 0031 自己的 reservation：

```text
quality_slo_policy.*
quality_scan_schedule.*
quality_scan_run.*
quality_alert.*
quality_recertification.*
```

只要这些 0031-specific ledger rows 存在，也应阻断，因为 replay response 可能指向已删除的 0031 resource。不要使用一个宽泛的 `LIKE 'quality%'` 把未来无关业务误判为 blocker。

### 7.2 Downgrade 顺序

在 blocker 为零且通过 DBA/Approval 后：

1. 停止 0031 worker/queue consumer；
2. 再次执行 schema/data preflight；
3. 删除 0031 Observation immutable triggers；
4. 删除 PostgreSQL 0031 immutable function；
5. 删除 cyclic FK：Schedule.last_run_id 与 Run.schedule_id；
6. 删除 Job；
7. 删除 Alert；
8. 删除 Observation；
9. 删除 Scan Run；
10. 删除 Scan Schedule；
11. 删除 SLO Policy；
12. 清理 0031 Catalog capability/manifest contract；
13. 确认 `alembic_version=0030_enterprise_release_quality_certification`；
14. 重新检查 Stage 20 capability 为 ready。

Migration 不得为了完成 downgrade 而自动 delete rows、清理共享 idempotency ledger、解除父级 Release/Channel FK 或伪造历史。SQLite 的表重建必须 online，且失败时整个事务应 rollback。

## 8. 失败关闭与威胁模型

| 风险 | 失败关闭要求 |
|---|---|
| Cross-Tenant FK | 所有 child FK 以 `tenant_id` 开头；service 再验证 Dataset/Release/Channel 组合 |
| Fake active policy | DB canonical key + tenant unique + readiness 重算 |
| Duplicate alert | `active_alert_key` digest + NULL for resolved + unique `(tenant_id,key)` |
| Stale Certification/Waiver | Observation/Job 写入前重新调用 Stage 20 currentness；digest 不匹配即 unavailable/failed |
| Judgment 在扫描期间变化 | authoritative scan 使用 `lock_evidence=True`；完成前再比较 evidence digest |
| Run/Job lease theft | DB UTC + owner + lease + heartbeat + revision CAS；过期只能 reclaim，不得直接覆盖 |
| Double enqueue | deterministic slot key + DB unique + generic idempotency ledger |
| Same cycle across triggers | separate `cycle_key` coalescing; no new row for same authority cycle |
| Forged Alert active key | key 由 service 计算；preflight 重算；不信任 client field |
| Malformed digest | DB length/lower + service strict lowercase hex + full root recomputation |
| Unsafe JSON | safe projector reject forbidden keys/types/depth; read path validates again |
| Secret/Body leak | no query/body/note/ticket/raw key in six tables or replay response |
| Misbound immutable trigger | readiness reads actual target table and trigger definition/function |
| SQLite FK off | online connection must enable foreign keys; preflight verifies enforcement before accepting authority |
| MySQL CHECK not enforced | deployment minimum version/DB capability must be part of readiness; unsupported server returns unavailable |
| Partial migration | revision/table capability mismatch is unavailable, never ready |
| Silent alert loss | max-open limit breach becomes safe Run failure/degraded result, not dropped alert |
| UI fail-open | UI only renders server-validated gate/observation; unknown/unavailable never enables waiver/certify action |

## 9. Approval boundary

Stage 21 不新增 Approval action，原因是当前 Approval Control 已有足够的 opaque execution contract，而新增 action 会扩大 Migration、ORM、consumer、readiness 和 downgrade surface。

推荐边界：

- **SLO Policy create/update/disable：** Tenant owner/admin + Dataset/Tenant scope + generic idempotency + audit；若企业未来要求双人控制，再复用 Approval Control，但不在本六表中复制 Approval lifecycle。
- **Schedule pause/resume/update：** Dataset manage + revision fence + audit；不触发生产 Source Sync。
- **Scan Run claim/heartbeat：** system actor/worker lease；不需要人工 Approval。
- **Alert acknowledge/resolve/suppress：** Dataset manage + revision fence + audit；不能自动修改 Release 或 Quality authority。
- **Recertification Job enqueue/cancel/retry：** Dataset manage/system actor + idempotency；Job 不直接改变 Judgment、Waiver 或 Release。
- **Waiver：** 继续使用既有 `knowledge_base_release_quality_waiver` action、Approval Request、ExecutionFact 和 immutable Stage 20 Waiver。
- **Promotion/Rollback/Pin：** 继续使用 Stage 20/Stage 19 的 Quality Gate + Publish/Rollback Approval 顺序；Stage 21 只能提供 Observation/Job 信息，不能成为新的发布 authority。

## 10. 实现阶段验收清单

### Migration / ORM

- [ ] `0031` 只从 `0030` 进入，无分叉 head。
- [ ] 六张表所有 child FK Tenant-leading。
- [ ] 所有 composite FK parent 有精确 UniqueConstraint。
- [ ] SLO canonical scope Check 与 Stage 20 语义一致。
- [ ] Scan Schedule Dataset scope 唯一。
- [ ] Run idempotency/slot unique 和 lease shape Check 存在。
- [ ] Observation immutable trigger 绑定真实目标表。
- [ ] Alert active identity 普通 UniqueConstraint + NULL inactive key。
- [ ] Job cycle key 与 idempotency digest 分离且唯一。
- [ ] 不使用 `ON DELETE CASCADE` 清理历史 authority。
- [ ] MySQL DATETIME(6)、PostgreSQL、SQLite online schema 均通过 contract。

### Service / API

- [ ] API mutation 使用 Tenant idempotency ledger，不保存 raw key。
- [ ] 所有 mutation 有 revision fence、audit、安全错误 projection。
- [ ] Scan worker 使用 DB UTC、lease、heartbeat、reclaim。
- [ ] Stage 20 gate 是唯一 quality authority；scan 传 `lock_evidence=True`。
- [ ] Observation 写入前后重算 Manifest/Certification/Waiver/evidence digest。
- [ ] Alert transition 仅按允许状态图 CAS。
- [ ] Job 不修改 Judgment，不自动 Certification/Waiver/Promotion。
- [ ] `ready_to_certify` 之后仍需显式 operator action。

### Readiness / Preflight

- [ ] `0030` 返回 Stage 21 `not_available`，不是 blocker。
- [ ] `0031` partial schema 返回 `unavailable`。
- [ ] 检查 columns/Not Null/types/unique/FK/check/index/triggers。
- [ ] 检查实际 trigger target 和 PostgreSQL function binding。
- [ ] 重算 policy/observation/alert/job digests。
- [ ] 检查孤儿、跨 Tenant、重复 active identity、状态/lease contradiction。
- [ ] 检查高风险/default-serving Channel 的 current Gate coverage。
- [ ] preflight 输出 `read_only=true`、`mutations_performed=false`、`automatic_actions=[]`。

### Tests / Dialect smoke

至少增加以下测试：

```text
test_stage21_upgrade_from_0030_creates_exact_six_tables
test_stage21_all_foreign_keys_are_tenant_leading
test_slo_policy_scope_is_canonical_and_active_unique
test_schedule_is_dataset_scoped_and_revision_fenced
test_scan_run_slot_and_idempotency_are_deduplicated
test_scan_run_lease_shape_and_reclaim_are_safe
test_observation_update_delete_are_rejected_on_sqlite
test_observation_trigger_target_is_real_table
test_alert_active_key_allows_only_one_active_cycle
test_resolved_alert_can_start_new_cycle
test_alert_lifecycle_requires_revision_cas
test_recertification_cycle_coalesces_multiple_triggers
test_job_state_machine_rejects_invalid_transition
test_job_completed_requires_certification
test_stage21_downgrade_blocks_nonempty_authority
test_stage21_downgrade_is_online_only
test_preflight_is_read_only_and_does_not_enqueue_jobs
test_quality_operation_digests_are_recomputed
test_safe_json_rejects_body_query_note_ticket_secret
test_unknown_or_stale_gate_fails_closed
```

- SQLite：online Migration + SQLite `PRAGMA foreign_keys=ON` + trigger target/immutable tests。
- PostgreSQL：真实 container smoke，验证 `FOR UPDATE SKIP LOCKED`、function/trigger、JSON、composite FK、DateTime equivalent、lease CAS。
- MySQL/MariaDB：真实 container smoke，验证 `DATETIME(6)`、`CONCAT` canonical scope、CHECK enforcement、`SIGNAL SQLSTATE`、composite FK、lease CAS。
- Offline MySQL/PostgreSQL：只生成并审阅 SQL，不执行生产数据库。
- Offline SQLite：按 Stage 20 policy fail closed，不生成可误执行的 SQL。

## 11. 生产禁令

0031 实现、Migration、preflight、Playwright 或测试阶段都必须保持以下禁令：

```text
禁止真实生产 Migration
禁止真实生产 backfill
禁止真实 SLO scan / Scan Run
禁止真实 Observation / Alert / Recertification Job 写入
禁止真实 Certification
禁止真实 Waiver / Approval ticket / ticket consumption
禁止真实 Promotion / Rollback / Application Pin
禁止 Source Sync / ingestion / restore / delete
禁止外部通知、Webhook、邮件、短信或外部服务发布
```

受保护路径继续保持不动：

```text
frontend/src/retrieval-quality/**
core/retrieval_experiment_runner.py
server/retrieval_experiments_api.py
tests/test_retrieval_experiment_runner.py
tests/test_retrieval_experiments_api.py
```

## 12. 最终推荐

0031 不应只是“加几张 UI 表”。推荐把它实现为一个小型但严格的运营 authority：

```text
Dataset-scoped Schedule
→ leased Scan Run
→ immutable Observation
→ active-identity Alert lifecycle
→ cycle-deduplicated Recertification Job
```

其中：

- Stage 20 Quality Gate、Release Manifest、Approval ExecutionFact 是上游不可替代的 authority；
- 六张新表只保存安全的运营事实和生命周期；
- 结构由 Migration/ORM 保证，语义由 service/readiness/preflight 重算；
- SQLite 作为本地/测试数据库必须 online + `BEGIN IMMEDIATE`，不伪装成拥有 row-level `FOR UPDATE`；
- MySQL/PostgreSQL 需要真实 container smoke 后才能声称跨数据库 production-ready；
- Downgrade 在任何 0031 authority 非空时 fail closed；
- 没有真实生产动作、没有自动修复、没有自动认证、没有自动发布。

**关键建议优先级：**

1. 先固定 Dataset-scoped Schedule 和六表 Tenant-leading FK contract；
2. 再实现 Run/Job lease + deterministic idempotency/cycle key；
3. 然后实现 Observation immutable guard 与 Alert active identity；
4. 把 Stage 20 currentness/digest 重算接入 0031 scan/preflight；
5. 最后才建设 TDesign Quality Operations UI，并让 UI 只显示服务器已验证的 authority，不在浏览器推断状态。
