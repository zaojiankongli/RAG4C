# Stage 25 Enterprise Automation & Workflow Orchestration Center：数据库安全架构研究

> **Research only / no implementation**  
> **文档版本：** 2026-08-30  
> **当前基线：** `0034_enterprise_task_operations`  
> **推荐新增表：** 恰好 6 张  
> **范围：** 规则治理、受限自动化请求、预览/演练、执行尝试、不可变执行证据、租约与并发安全。  
> **明确边界：** 本文不要求修改任何业务源表、源任务状态机、Task Projection schema 或现有业务执行器。

## 1. 结论

Stage 25 不应把“工作流”实现成可以解释任意代码的通用脚本系统。企业级安全架构应把自动化拆成四个不可混淆的事实层：

```text
trusted event / Task Event / Approval decision
        -> immutable rule revision
        -> restricted action request
        -> bounded execution attempt
        -> immutable execution event ledger
```

规则引擎只负责：

1. 读取 Tenant 范围内、经过 allowlist 筛选的事件事实；
2. 对不可变规则 revision 中的有限条件 schema 做纯计算；
3. 生成一个可校验的、可幂等的、带 source revision/digest fence 的 action request；
4. 在需要时进入既有 Approval Control；
5. 把执行交给显式注册的内置 adapter。

**规则引擎绝不能动态导入函数、反射执行函数、执行任意 SQL、访问任意 URL、发送任意 webhook、运行 shell/脚本/代码或把原始文档内容交给执行器。** 规则中的 JSON 只是受限数据快照，不是 DSL 代码，也不是模板、查询、连接器配置或可执行计划。

推荐的 Stage 25 future migration 以 `0034_enterprise_task_operations` 为 `down_revision`，新增以下恰好 6 张表：

```text
tenant_automation_rules
tenant_automation_rule_revisions
tenant_automation_execution_requests
tenant_automation_execution_attempts
tenant_automation_execution_events
tenant_automation_execution_leases
```

不新增独立的 allowlist 表、outbox 表、preflight 表、脚本表或 webhook 表。allowlist 以不可变 revision 中的 canonical JSON snapshot 保存；execution request 本身承担 durable request/outbox 事实；Readiness/preflight 只读计算，不落表。

## 2. 现有 0034 基线与必须复用的事实

### 2.1 当前 head

当前应用 head 是 `0034_enterprise_task_operations`，其下游仍包含 `0033_enterprise_content_recovery`。Stage 24 已经建立：

- `tenant_task_projections`：按 `(tenant_id, source_kind, source_id)` 唯一的统一 Task Projection；它是源任务的可验证读模型，不是源任务真账；
- `tenant_task_operator_actions`：`retry/cancel/acknowledge` 的 revision-fenced、idempotency-fenced action request；
- `tenant_task_events`：按 Task stream 组织的 append-only hash chain；
- `tenant_task_saved_views`：Tenant + account 范围的安全过滤视图；
- `tenant_task_reconciliation_runs`：reconciliation 证据。

Stage 25 应直接复用这些事实，而不是复制一套“自动化任务真账”。

### 2.2 Task Projection 与 Task Event 的边界

自动化触发 Task 相关 action 时：

1. `tenant_task_projections` 提供目标任务的当前 `source_kind/source_id/source_revision/source_digest` 和 `projection_digest`；
2. 规则引擎把这些值固化到 execution request；
3. 执行 adapter 再次读取并校验同一 Tenant、同一 Task、同一 source revision/digest；
4. 如果 action 是 Task Center 已支持的操作，adapter 调用既有 Task action service，形成 `tenant_task_operator_actions`；
5. source service 仍是最终业务状态机 authority；
6. Task 状态变化继续由既有 Task projection/reconciliation 路径反映，Task Event chain 继续由 `tenant_task_events` 记录。

自动化执行 ledger 与 `tenant_task_events` 是两个不同的证据链：

- `tenant_task_events` 证明 Task Projection 的 materialization、status change 和 operator action 事实；
- `tenant_automation_execution_events` 证明某条 rule revision 为什么产生 request、request 如何经过 approval/lease/attempt、以及 adapter 为什么成功或拒绝。

两条链通过 Tenant-scoped IDs、source revision/digest、Task Event digest 和 request fingerprint 交叉引用，但不互相冒充业务真账。

### 2.3 Approval Control 的边界

现有 Approval Control 已有：

- `tenant_approval_policies`；
- `tenant_approval_policy_approvers`；
- `tenant_approval_requests`；
- `tenant_approval_decisions`；
- 一次性 execution ticket 的签发与消费事实。

Stage 25 只允许把 execution request 关联到现有 Approval Request。自动化表不保存明文 ticket，不接受客户端提交的 ticket，不自行签发 ticket，也不能用自定义 action type 绕过 `tenant_approval_policies` 的固定 allowlist。

如果某种自动化 action 需要审批，而现有 Approval Control 尚未有对应的受控 action type，则该 request 必须进入 `blocked` 或 `pending_approval`，不能通过增加任意字符串、任意 JSON 或任意 adapter 名称来放行。

## 3. 安全目标与信任边界

### 3.1 保护对象

Stage 25 需要保护的不是单纯的执行队列，还包括：

- Tenant 隔离与跨 Tenant 引用；
- 规则 revision 的完整性、发布状态和 allowlist 快照；
- Task Projection 的 source revision/digest fence；
- Approval policy/request/decision/ticket 的绑定关系；
- execution request 的幂等性和不可重复执行语义；
- execution attempt 的 lease/fence token；
- execution event ledger 的时间顺序和 hash chain；
- 不泄露文档正文、chunk、query、credential、token、URL 和原始异常；
- worker 重试、进程崩溃、网络超时和重复投递下的可恢复性。

### 3.2 信任边界

```text
Tenant member / admin
    -> authenticated API + authorization
    -> rule head / immutable rule revision
    -> pure rule evaluator
    -> restricted request validator
    -> approval gate when required
    -> lease/fence guarded executor
    -> fixed adapter registry
    -> existing Task/source/Approval service
```

以下内容均视为不可信输入：Tenant 管理员提交的规则条件、外部事件字段、Task safe snapshot、队列重复投递、worker 自报状态、adapter 返回的错误文本以及任何从文档/网页/连接器抽取的字符串。所有输入都必须经过结构、范围、allowlist、Tenant scope、digest 和敏感信息扫描。

## 4. 恰好 6 张新表的推荐方案

### 4.1 `tenant_automation_rules`：逻辑规则 head

该表保存可变的规则逻辑身份和当前 head，不保存可执行规则正文。

建议字段：

```text
id                         String(64) primary key
tenant_id                  String(64) not null
rule_key                   String(128) not null
display_name               String(128) not null
scope_kind                 tenant | workspace | dataset
workspace_id               String(64) nullable
dataset_id                String(64) nullable
status                     draft | active | paused | retired
active_revision_no         Integer nullable
active_revision_digest     String(64) nullable
revision                   Integer not null, > 0
created_at / created_by
updated_at / updated_by
```

canonical identity：

```text
RuleIdentity = tenant_id + ":" + normalized(rule_key)
UNIQUE(tenant_id, rule_key)
UNIQUE(tenant_id, id)
```

约束：

- `rule_key` 使用小写 ASCII slug，禁止 URL、路径、SQL、函数名和自由文本；
- `active`/`paused` 必须同时有 `active_revision_no` 与 `active_revision_digest`；`draft`/`retired` 不得指向可执行 head；
- `active_revision_digest` 只作为 head proof，实际 revision row 由 service/preflight 通过 `(tenant_id, rule_id, revision_no, digest)` 再查证；
- `scope_kind=workspace` 时必须有 `(tenant_id, workspace_id)`；`scope_kind=dataset` 时必须有 `(tenant_id, dataset_id)`；`tenant` scope 不允许伪造 scope ID；
- 所有 scope FK 使用 Tenant-leading composite FK；
- head 更新必须带 optimistic revision fence，不能静默覆盖另一个管理员刚发布的 revision。

规则 head 可以暂停或退休，但不得通过 head 操作改写已经发布的 revision 内容。暂停只是禁止生成新的 execute request，不删除历史 request/attempt/event。

### 4.2 `tenant_automation_rule_revisions`：不可变规则版本

该表是 Stage 25 的核心安全边界。每个 revision 是可审计、可 hash、可重放验证的策略快照。发布后禁止 UPDATE/DELETE；需要修改时新增 revision。

建议字段：

```text
id                              String(64) primary key
tenant_id                       String(64) not null
rule_id                         String(64) not null
revision_no                     Integer not null, > 0
schema_version                  Integer not null
trigger_allowlist_json          JSON not null
trigger_allowlist_digest        String(64) not null
event_allowlist_json            JSON not null
event_allowlist_digest           String(64) not null
action_allowlist_json           JSON not null
action_allowlist_digest         String(64) not null
condition_spec_json             JSON not null
condition_spec_digest           String(64) not null
target_contract_json            JSON not null
target_contract_digest          String(64) not null
approval_mode                   none | existing_policy | always_existing_policy
safe_execution_mode             preview | dry_run | execute
max_attempts                    Integer not null, bounded
lease_ttl_seconds               Integer not null, bounded
concurrency_key_spec_json       JSON not null
rule_digest                     String(64) not null
created_at / created_by
published_at                    DateTime(6) nullable
```

canonical identity：

```text
RuleRevisionIdentity = tenant_id + rule_id + revision_no
UNIQUE(tenant_id, id)
UNIQUE(tenant_id, rule_id, revision_no)
```

revision digest 的输入必须是 canonicalized 的有限字段集合，至少包括：

```text
schema_version
trigger_allowlist
event_allowlist
action_allowlist
condition_spec
target_contract
approval_mode
safe_execution_mode
max_attempts
lease_ttl_seconds
concurrency_key_spec
```

不把 `created_at`、worker 名称或随机 nonce 作为规则语义的一部分。所有 JSON 使用排序后的 object key、排序/去重后的 allowlist、UTC microsecond timestamp、有限字符串长度和固定数值类型进行 canonicalization。

#### 条件 schema 的安全范围

`condition_spec_json` 不是脚本语言，只允许有限的 typed comparison AST，例如：

```text
all / any
exists
_eq_ / _in_ / _not_in_
_gte_ / _lte_
```

操作对象只能是平台注册的 safe fact path，例如：

```text
task.normalized_status
task.source_current
task.source_kind
task.source_revision
task.action_required
approval.decision
release.quality_state
```

禁止：

- `eval`、`exec`、模板插值或任意表达式解析；
- 反射、动态 import、模块名、函数名、类名、可调用对象；
- 任意字段路径、任意对象遍历、任意递归查询；
- SQL、SQL fragment、正则脚本、shell、Python/JavaScript/Lua/WASM；
- 文档正文、chunk、query、answer、prompt、cookie、header、credential、token；
- URL、webhook、callback、redirect、外部 topic 或任意连接器配置。

#### 三层 allowlist

每个 revision 固化三类 allowlist：

1. `trigger_allowlist_json`：允许进入规则评估的受信 trigger code，例如 `task_event`、`approval_decision`、`schedule_tick`；`schedule_tick` 只能来自平台 scheduler，不能由规则提交 cron 脚本或 URL；
2. `event_allowlist_json`：允许的 namespaced event type，例如 `task.status_changed`、`task.source_stale`、`approval.approved`；事件必须由已注册 source adapter 提供 typed safe facts；
3. `action_allowlist_json`：允许生成的 action code，例如 `task.retry`、`task.cancel`、`task.acknowledge`。

allowlist 的值只允许出现在平台代码维护的 registry 中。数据库保存的是 revision 时的选择和 digest；runtime 仍需验证当前 registry 版本、adapter availability、approval mapping 和 target contract。registry 中不存在的值、值的大小写变体、拼接值或相似名称均 fail closed。

### 4.3 `tenant_automation_execution_requests`：受限 action request 与幂等边界

该表保存“某个已允许事件导致的某个受限 action 意图”，是自动化系统的 durable request/outbox 事实。它不是执行脚本，也不保存原始事件 payload。

建议字段：

```text
id                              String(64) primary key
tenant_id                       String(64) not null
rule_id                         String(64) not null
rule_revision_id                String(64) not null
trigger_code                    String(64) not null
event_type                     String(128) not null
trigger_ref_kind                String(64) not null
trigger_ref_id                  String(128) not null
trigger_event_digest            String(64) not null
trigger_task_event_id           String(64) nullable
safe_trigger_facts_json         JSON not null
safe_trigger_facts_digest       String(64) not null
target_kind                     String(64) not null
target_id                       String(128) not null
task_id                         String(64) nullable
expected_source_revision        Integer nullable
expected_source_digest          String(64) nullable
expected_projection_digest      String(64) nullable
action_code                    String(64) not null
safe_action_args_json           JSON not null
safe_action_args_digest         String(64) not null
mode                            preview | dry_run | execute
status                          previewed | dry_run_succeeded | pending_approval |
                                ready | blocked | dispatched | succeeded |
                                failed | rejected | expired | cancelled
request_fingerprint             String(64) not null
idempotency_scope               String(128) not null
idempotency_key_digest          String(64) not null
concurrency_key                 String(192) not null
approval_request_id             String(64) nullable
task_operator_action_id         String(64) nullable
safe_reason_code                String(64) nullable
expires_at                      DateTime(6) nullable
requested_at / requested_by
created_at / updated_at
```

canonical identities：

```text
TriggerIdentity = tenant_id + trigger_ref_kind + trigger_ref_id + trigger_event_digest
TargetIdentity  = tenant_id + target_kind + target_id
                 + expected_source_revision + expected_source_digest

RequestFingerprint = SHA256(
  "automation-request:v1" |
  tenant_id |
  trigger_event_digest |
  target_kind |
  target_id |
  action_code |
  safe_action_args_digest |
  mode
)

UNIQUE(tenant_id, request_fingerprint)
UNIQUE(tenant_id, idempotency_scope, idempotency_key_digest)
UNIQUE(tenant_id, id)
```

`rule_revision_id` 是 request 的 policy proof；request fingerprint 不应只依赖 rule revision，否则同一事件在规则 revision 变更后可能再次产生相同 action。相同 Tenant、相同 trigger event、相同 target、相同 action、相同 safe args 和相同 mode 必须命中同一 request。规则 revision 变化可以改变是否允许新 action，但不能让 replay 绕过已经成功的幂等事实。

对 Task target，必须保存并验证：

```text
task_id
expected_source_revision
expected_source_digest
expected_projection_digest
```

如果 Task Projection 已 stale/unavailable、source digest 变化、source revision 变化或 projection digest 不匹配，request 进入 `blocked`/`rejected`，不能自动“猜测最新值”后继续执行。

建议的 composite FK：

```text
(tenant_id, rule_id)                 -> tenant_automation_rules
(tenant_id, rule_revision_id)        -> tenant_automation_rule_revisions
(tenant_id, task_id)                 -> tenant_task_projections
(tenant_id, trigger_task_event_id)   -> tenant_task_events
(tenant_id, approval_request_id)     -> tenant_approval_requests
(tenant_id, task_operator_action_id) -> tenant_task_operator_actions
```

其中可选 FK 只在对应 `target_kind` 或 `trigger_code` 下启用；service/preflight 必须验证“可选 ID 的存在、类型、Tenant、revision 和 digest”之间的一致性。通用 `trigger_ref_id` 不是任意数据库表名，也不是 SQL/URL 引用，只是已经注册的事件 source 的 bounded identity。

### 4.4 `tenant_automation_execution_attempts`：执行尝试事实

一个 execution request 可以有多次 attempt，但每次 attempt 都必须有确定序号、固定 adapter code 和受控 lease proof。attempt 只保存 safe outcome，不保存 adapter 的原始返回体。

建议字段：

```text
id                              String(64) primary key
tenant_id                       String(64) not null
execution_request_id            String(64) not null
attempt_number                  Integer not null, > 0
status                          created | leased | running | succeeded |
                                failed | rejected | expired | cancelled
adapter_code                    String(64) not null
adapter_version                 String(32) not null
lease_id                        String(64) nullable
lease_epoch                    BigInteger nullable
observed_source_revision        Integer nullable
observed_source_digest          String(64) nullable
observed_projection_digest      String(64) nullable
attempt_digest                  String(64) not null
safe_result_code                String(64) nullable
safe_error_code                 String(64) nullable
safe_error                      String(512) nullable
started_at / finished_at        DateTime(6) nullable
created_at                      DateTime(6) not null
```

canonical identity：

```text
AttemptIdentity = tenant_id + execution_request_id + attempt_number
UNIQUE(tenant_id, execution_request_id, attempt_number)
UNIQUE(tenant_id, id)
```

约束：

- `attempt_number <= rule_revision.max_attempts`；
- adapter code 必须来自静态 registry，adapter version 必须是已注册的版本；
- `succeeded`/`failed`/`rejected`/`expired` 只能由匹配的 lease owner + lease epoch 写入；
- `safe_error_code` 与 `safe_error` 必须成对出现，并经过 URL/secret/credential/raw payload 扫描；
- attempt 不能直接修改 `tenant_task_projections` 或任何源任务表；
- 失败重试是创建下一条 attempt，不更新已完成 attempt 的事实。

### 4.5 `tenant_automation_execution_events`：不可变 execution ledger

该表记录一次 request 的完整执行证据链，不能替代 `tenant_task_events`，也不能被用来承载原始日志。

建议字段：

```text
id                              String(64) primary key
tenant_id                       String(64) not null
execution_request_id            String(64) not null
attempt_id                      String(64) nullable
sequence                       BigInteger not null, > 0
event_type                     String(64) not null
previous_event_digest            String(64) nullable
event_digest                   String(64) not null
actor_id                       String(128) not null
request_id                     String(128) not null
safe_snapshot_json             JSON not null
occurred_at                    DateTime(6) not null
```

canonical identity：

```text
ExecutionEventIdentity = tenant_id + execution_request_id + sequence
UNIQUE(tenant_id, execution_request_id, sequence)
UNIQUE(tenant_id, id)
```

建议的 event type allowlist：

```text
request_created
preview_created
dry_run_completed
approval_required
approval_verified
approval_rejected
request_blocked
lease_acquired
lease_renewed
lease_expired
attempt_started
adapter_rejected
action_request_dispatched
action_applied
action_failed
late_completion_rejected
request_expired
request_cancelled
```

第一条 event 必须是 `request_created` 或 `preview_created`，且 `previous_event_digest IS NULL`；后续 event 必须引用同一 Tenant、同一 request、`sequence - 1` 的实际 `event_digest`。数据库 trigger/function 负责验证存在性、顺序和 immutable UPDATE/DELETE guard；应用 Readiness 负责按 canonical snapshot 重算 `event_digest`，因为三种数据库不应依赖相同的自定义 hash SQL 函数。

`safe_snapshot_json` 只允许 ID、code、status、revision、digest、bounded counts、时间和 safe reason code。禁止文档正文、chunk、query、answer、prompt、URL、header、cookie、token、ticket、credential、原始 exception、任意 result body。

### 4.6 `tenant_automation_execution_leases`：并发槽与 fencing lease

该表把并发安全从内存锁提升为可审计的 Tenant-scoped 数据库事实。Stage 25 v1 采用“每个 canonical concurrency key 同时最多一个 live lease”；不依赖 Redis 作为业务正确性来源。

建议字段：

```text
id                              String(64) primary key
tenant_id                       String(64) not null
execution_request_id            String(64) not null
active_slot_key                String(256) nullable
concurrency_key                 String(192) not null
status                          held | released | expired
owner_id                        String(128) not null
fence_epoch                    BigInteger not null, > 0
lease_until                    DateTime(6) not null
heartbeat_at                   DateTime(6) not null
acquired_at                    DateTime(6) not null
released_at                    DateTime(6) nullable
expired_at                     DateTime(6) nullable
safe_error_code                String(64) nullable
```

canonical identity：

```text
LeaseSlotIdentity = tenant_id + active_slot_key
UNIQUE(tenant_id, active_slot_key)
UNIQUE(tenant_id, id)
```

`active_slot_key` 在 `held` 时必须存在，在 `released`/`expired` 时清空。沿用 Stage 24 `active_view_key` 的跨数据库 NULL uniqueness 方案：已结束历史 lease 保留，但不占用 active slot。`concurrency_key` 来自 revision 的 `concurrency_key_spec_json`，只能由注册的 safe fact path 组合而成，不允许客户端直接指定任意全局锁名。

租约协议：

1. 事务内锁定 execution request 和该 Tenant + active slot；
2. 若 live lease 未过期，request 进入 `blocked`，不创建 live attempt；
3. 若 lease 已过期，先写 `lease_expired` event，再以更高 `fence_epoch` 建立新 lease；
4. worker heartbeat、attempt completion、request finalization 必须同时提交 `lease_id + fence_epoch + owner_id`；
5. 过期 worker 的晚到成功必须被拒绝，并记录 `late_completion_rejected`，不能覆盖新 attempt；
6. `lease_ttl_seconds` 有数据库与 service 双重上限，不能由规则或客户端设为无限期。

## 5. 规则引擎只能生成受限 action request

### 5.1 唯一允许的输出

规则引擎的输出类型应当是不可执行的 typed value object：

```text
RestrictedActionRequest {
  tenant_id
  rule_id
  rule_revision_id
  trigger_code
  event_type
  trigger_event_digest
  target_kind
  target_id
  expected_source_revision
  expected_source_digest
  action_code
  safe_action_args
  mode
  approval_mode
  request_fingerprint
  idempotency_key_digest
  concurrency_key
}
```

在落入 `tenant_automation_execution_requests` 之前，service 必须再次执行：

- Tenant scope 校验；
- rule head/revision digest 校验；
- trigger/event/action registry allowlist 校验；
- target contract 校验；
- Task Projection source revision/digest 校验；
- Approval policy mapping 校验；
- idempotency fingerprint 校验；
- safe fact 和 safe args 的大小、类型、敏感信息和 URL scheme 校验。

### 5.2 明确禁止的机制

以下机制在设计和实现中均不可存在：

```text
importlib.import_module(user_value)
getattr(module, user_value)(...)
eval(user_value)
exec(user_value)
connection.execute(user_sql)
requests.request(user_url, ...)
webhook(user_supplied_url, ...)
shell(user_code)
```

也不允许以 JSON 间接表达上述行为，例如：

```json
{
  "module": "...",
  "callable": "...",
  "sql": "...",
  "url": "...",
  "webhook": "...",
  "code": "...",
  "shell": "...",
  "template": "..."
}
```

字段名、嵌套 key 和 value 都要做拒绝检查；只检查 key 不足以防止把 URL、Bearer token 或 SQL 片段藏在普通字符串中。

任何未来的外部通知、HTTP callback、webhook、邮件或第三方 API 调用都不属于本 Stage 的 action。若未来需要，应由独立的、固定 endpoint/provider、独立审批、独立 secret 管理和独立 egress policy 设计，不能由本规则引擎直接提供 URL。

### 5.3 adapter registry

adapter registry 是平台代码中的显式静态映射，例如：

```text
("task.retry", "task_projection")      -> built-in Task action service
("task.cancel", "task_projection")     -> built-in Task action service
("task.acknowledge", "task_projection") -> Task Center local service
```

每个 `(action_code, target_kind)` pair 都必须有：

- 固定输入 schema；
- 固定输出 safe result code；
- 固定权限要求；
- 固定 Approval requirement；
- 固定 source revision/digest fence；
- 固定是否允许 dry-run；
- 固定最大 attempt/lease policy。

未注册 pair、版本不兼容、adapter 被禁用、registry digest 与 rule revision 不一致、或者 action allowlist 没有该 pair 时，均 `rejected`/`blocked`，不允许 fallback 到通用 executor。

## 6. Preview、dry-run 与 execute 三种语义

### 6.1 Preview

`preview` 用于回答“在给定事件 snapshot 和指定 rule revision 下，会生成什么 request”。它可以把 `preview_created` 事实写入 automation request/event ledger，但只能产生 Stage 25 自己的记录：

- 不创建 Approval Request；
- 不签发或消费 execution ticket；
- 不创建 Task operator action；
- 不 acquire live lease；
- 不调用 source adapter；
- 不更新 Task Projection、源任务或业务状态；
- 不发送通知、URL、webhook 或任何外部网络请求。

Preview 必须绑定固定的 `trigger_event_digest`、rule revision digest 和当前 target snapshot，避免“预览一个对象、执行另一个对象”。

### 6.2 Dry-run

`dry_run` 是更强的可执行性演练：它可以创建一个 `execution_request` 和一个 `execution_attempt`，执行纯校验与模拟 adapter，但不能产生业务副作用。dry-run 可以验证：

- allowlist pair 是否存在；
- target contract 是否满足；
- Task source revision/digest 是否仍然匹配；
- Approval policy 是否存在且可满足；
- 当前 concurrency slot 是否会被阻塞；
- attempt budget、lease TTL、safe args 和 idempotency 是否有效。

dry-run 不创建 live lease；若需要演练并发，只能做“would acquire / would block”判断，不能占用生产 execution slot。dry-run 不创建 Approval Request、不消费 ticket、不产生 Task action。

### 6.3 Execute

只有 `mode=execute` 才允许进入 live lease/attempt。进入 execute 前必须完成：

```text
rule head active
rule revision digest current
trigger/event/action allowlists valid
Tenant authorization valid
target currentness valid
Approval state valid when required
idempotency request unique
concurrency slot available
```

任一条件无法证明，request 不得降级成“尽力执行”，只能进入 `blocked`/`rejected`，并记录 safe code。

## 7. 端到端执行流程

1. **收集受信事件。** 事件必须来自静态注册的 source adapter；Task 事件优先使用 `tenant_task_events` 的已有 digest/sequence proof。原始内容只在源系统内部处理，不进入 automation safe facts。
2. **读取 active rule head。** 按 Tenant + rule key 查找 active/paused 状态，锁定 head revision，读取不可变 rule revision 与所有 digest。
3. **验证三类 allowlist。** 当前 trigger code、namespaced event type 和 `(action_code, target_kind)` pair 必须同时在 revision snapshot 与代码 registry 中存在。
4. **纯计算条件。** 只对有限 safe fact path 执行 typed comparisons；未知字段、深度超限、项数超限、敏感字段或 URL-like value 直接拒绝。
5. **生成 request fingerprint。** 用 trigger event digest、target identity、action code、safe args digest 和 mode 生成确定性 request fingerprint。
6. **幂等插入 request。** 同 fingerprint 返回已有 request；同 idempotency scope + digest 但语义不同则返回 conflict，不能覆盖原 request。
7. **固化 source fence。** Task 目标必须保存 source revision/digest/projection digest，后续每一步重新验证，不从缓存猜测当前状态。
8. **进入 Approval。** 若 revision 要求既有 policy，创建或复用 Tenant-scoped `tenant_approval_requests`，只传 bounded safe snapshot；不得把 ticket 写入自动化表。
9. **获得批准后再次校验。** Approval Request、Approval Decision、policy revision、target revision 和 rule revision 必须仍然匹配；过期、拒绝、取消或已消费 ticket 都 fail closed。
10. **获取 lease。** 按 canonical concurrency key 事务化占用 active slot，生成递增 fence epoch。
11. **创建 attempt。** 记录 adapter code/version、lease proof、observed source digest 和 attempt number。
12. **调用固定 adapter。** adapter 只能把 request 转换为已有受控 service call，例如 Task action service；不直接改业务源表。
13. **记录结果。** 成功、拒绝、失败、过期、晚到 completion 都写 automation event；若 Task service 产生 Task Event，则保存交叉 digest。
14. **释放 lease。** 只有匹配 owner + fence epoch 的 worker 可以释放；异常退出由 TTL 和下一次 acquire 处理。
15. **投影可见性。** Task Center 仍然通过 `tenant_task_projections`、`tenant_task_operator_actions` 和 `tenant_task_events` 展示结果；automation ledger 负责解释“为什么产生 request”和“为什么执行/拒绝”。

## 8. Tenant 隔离、FK 与身份约束

### 8.1 Tenant-leading 原则

六张新表均必须有 `tenant_id NOT NULL`，所有业务索引、唯一键和 composite FK 以 `tenant_id` 开始。最低要求：

```text
(tenant_id, rule_id)                 -> tenant_automation_rules
(tenant_id, rule_revision_id)        -> tenant_automation_rule_revisions
(tenant_id, workspace_id)            -> tenant_workspaces
(tenant_id, dataset_id)              -> datasets
(tenant_id, task_id)                 -> tenant_task_projections
(tenant_id, trigger_task_event_id)   -> tenant_task_events
(tenant_id, task_operator_action_id) -> tenant_task_operator_actions
(tenant_id, approval_request_id)     -> tenant_approval_requests
(tenant_id, execution_request_id)    -> tenant_automation_execution_requests
(tenant_id, attempt_id)              -> tenant_automation_execution_attempts
(tenant_id, lease_id)                -> tenant_automation_execution_leases
```

所有被引用的现有表必须使用现有的 `(tenant_id, id)` unique proof，不使用单列 ID 作为跨 Tenant 的充分条件。

### 8.2 actor 与 service identity

用户 actor 必须通过 `(tenant_id, account_id)` 查到 active `tenant_members`，并经过既有 authorization。系统 actor 不接受客户端任意字符串；必须使用平台固定的 service principal identity，并在 service 层验证其 action capability。自动化 event 的 `actor_id` 只保存 bounded subject，不保存 token、credential 或请求头。

### 8.3 safe snapshot

所有 safe JSON 都必须通过统一 canonicalizer：

- maximum depth、items、字符串长度和总字节数有硬上限；
- object key 仅来自 schema；
- reject `password`、`secret`、`token`、`ticket`、`credential`、`authorization`、`cookie`、`header`、`payload`、`body`、`content`、`query`、`prompt`、`answer`、`url`、`uri`、`webhook` 等 key；
- value 扫描 database URL、Bearer/JWT-like token、secret scheme、URL scheme、邮件/凭据模式和 SQL-like fragment；
- 不保存原始事件 body、原始异常、HTTP response body、连接器 config 或 document/chunk text；
- digest 使用 lower-case SHA-256，并在数据库中做长度与大小写 check。

数据库的 JSON `CHECK` 只做跨数据库都可用的结构性最低保障；深层 schema、敏感信息扫描和 digest 重算必须由 service/readiness 再验证。

## 9. 幂等、重放与故障恢复

### 9.1 幂等层次

Stage 25 至少需要四层幂等：

1. **规则触发幂等：** 同一 `trigger_event_digest` + target/action/safe args 只能产生一个 request fingerprint；
2. **API/调度幂等：** 同一 `idempotency_scope` + `idempotency_key_digest` 只能对应一个语义 request；
3. **尝试幂等：** 同一 request 的同一 `attempt_number` 只能有一行；重试只能使用下一序号；
4. **adapter side-effect fence：** action adapter 必须把 `request_id`、source revision/digest 和 approval proof 传给已有 service，并拒绝旧 request/replayed ticket。

hash digest 不是幂等锁本身。真正的保证来自 unique constraint + transaction + state transition + source fence。

### 9.2 崩溃窗口

- request 已插入、worker 尚未运行：可由 dispatcher 重试；
- lease 已插入、worker 崩溃：TTL 后由下一 worker 以更高 fence epoch 接管；
- adapter 已提交但 response 丢失：adapter 必须按 request identity 查询/返回已应用事实，不能盲目重复；如果无法证明，request 进入 `blocked`，由人工/既有业务 recovery 处理；
- Task operator action 已建立、automation response 丢失：通过 `task_operator_action_id` 和 idempotency digest 重新关联，不重复创建 action；
- event append 失败：不把 attempt 标为最终成功；Readiness 将发现 request/attempt 与 ledger 不一致。

## 10. Approval 与 Task Projection 的安全接线

### 10.1 Approval 请求

自动化创建 Approval Request 时，snapshot 只包含：

```text
tenant_id
rule_id / rule_revision_id / rule_digest
trigger_event_digest
target_kind / target_id
task_id / expected source revision / expected source digest
requested action_code
safe_action_args_digest
request_fingerprint
policy_id / policy revision
requested expiry
```

禁止：

- raw Idempotency-Key；
- execution ticket 明文或其可逆编码；
- document/chunk/query/prompt；
- provider credential、URL、webhook；
- 任意 adapter 名称、模块路径或代码片段。

Approval 成功不等于自动化已经成功。通过后仍需校验 request 是否过期、rule revision 是否 active、Task source fence 是否 current、ticket 是否精确绑定且未消费、lease 是否仍可获得。

### 10.2 Task action

Task 相关 action 的唯一安全路径是：

```text
automation request
    -> fixed Task adapter
    -> tenant_task_operator_actions
    -> existing source/task service
    -> tenant_task_projections + tenant_task_events
```

自动化不能直接写 `tenant_task_projections.normalized_status`、`source_current`、`attempt_number` 或任何源任务状态。Task Projection 的变化必须由原有 reconciliation/source service 产生，防止 automation ledger 和 Task Center 显示出两个互相矛盾的状态。

## 11. MySQL / PostgreSQL / SQLite 边界

### 11.1 共同不变量

三种数据库都必须支持以下 invariant：

- Tenant-leading composite FK 和 `(tenant_id, id)` unique proof；
- bounded string、lower-case SHA-256 digest、revision > 0、TTL/attempt 上限；
- request fingerprint/idempotency unique；
- `(tenant_id, request_id, attempt_number)` unique；
- `(tenant_id, request_id, sequence)` unique；
- live lease 的 active slot unique；
- immutable revision/event 的 UPDATE/DELETE guard；
- event first-link/previous-link 验证；
- nonempty downgrade blocker；
- Readiness fail closed。

不把 advisory lock、partial unique index、generated column、JSON Schema database extension、`SKIP LOCKED` 或 RLS 当作唯一正确性来源。

### 11.2 MySQL / MariaDB

- 使用 InnoDB，所有 FK 目标列必须有可用的 Tenant-leading unique index；
- 延续现有 `DATETIME(6)` 变体；
- 使用 `JSON` 存 safe snapshot/allowlist，但不依赖 MySQL JSON Schema 检查，深层校验由 service/readiness 完成；
- 继续使用 `CHECK` 做状态、长度、digest pairing、lease pairing 和 scope pairing；部署环境如果对旧版本 CHECK enforcement 不一致，Readiness 必须把不确定视为 malformed；
- immutable/event guard 可以使用 `BEFORE UPDATE/DELETE` 与 `SIGNAL SQLSTATE '45000'`；
- request/lease 状态转换在短事务内使用行锁，禁止用数据库外的 Redis lock 代替唯一约束；
- index key 使用固定长度 ID/digest，避免把 JSON/text 放入 unique index。

### 11.3 PostgreSQL

- 可把 JSON 实际落为 `jsonb`，但 canonical digest 仍由应用按明确 schema 计算；
- 使用 PL/pgSQL trigger/function 保护 immutable revision/event 和 event chain；
- 可用 `SELECT ... FOR UPDATE`，`SKIP LOCKED` 只能是吞吐优化，不能改变安全语义；
- 不要求启用 RLS 才能成立，但若启用 RLS，仍要保留 service-level Tenant predicates 和 composite FK；
- 所有 timestamp 以 UTC 处理，避免 session timezone 改变 digest/expiry 语义。

### 11.4 SQLite

- SQLite 只支持 online migration；offline SQL generation 必须 fail closed，与现有 0034 一致；
- 每个连接必须打开 `PRAGMA foreign_keys=ON`，否则不能宣称 Tenant isolation contract 已成立；
- immutable revision/event 使用 SQLite trigger + `RAISE(ABORT, ...)`；
- lease acquire/renew/release 使用 `BEGIN IMMEDIATE` 或等价短事务，接受 single-writer 限制；
- 不把 SQLite 当作高并发生产 executor；它适合开发、验收和小规模单节点部署；
- JSON 深层约束和 SHA-256 重算由 Python service/readiness 完成；
- SQLite 测试必须覆盖并发模拟、过期 worker、外键关闭时 readiness 失败和 trigger 被删除时 readiness 失败。
