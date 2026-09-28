# Stage25 automation condition/action strategy registry handoff（2026-09-25）

## 这次完成了什么

Axis #12 的 condition/action 实现层已收成 Strategy + Registry：

- 新增 `core/automation_rule_strategies.py`，复用唯一的
  `core.providers.ProviderRegistry`。
- `AutomationConditionStrategy` 同时声明参数规格和同步 evaluator。
- `AutomationActionStrategy` 同时声明参数规格和无副作用的 target adapter。
- `core/enterprise_automation_workflows.py` 的 condition 参数规范化、条件求值、action 参数规范化与 target 选择均通过 registry dispatch。
- `core/enterprise_automation_workflows_service.py` 保留原有 durable action-request 事务，仅把 target 选择委托给 authority adapter。
- `server/enterprise_automation_workflows_api.py` 使用同一套参数声明做字段、类型、枚举、文本长度、identifier 和整数范围校验。

## 合同边界

本片没有增加任何 trigger/condition/action code：

- `AUTOMATION_CONDITION_CONTRACT_CODES` 与
  `AUTOMATION_ACTION_CONTRACT_CODES` 是冻结的持久化合同集合。
- registry 内置项在 import 期必须与冻结合同逐项、逐序一致；否则直接 fail-fast。
- API `Literal`、ORM CHECK 和历史 migration 保持闭合；测试独立对账 core、API Literal 与 ORM CHECK。
- runtime-only strategy 可以供受信任的纯函数/内部路径使用，但不能通过 canonical revision/action plan 持久化。
- 若将来要增加可持久化 code，必须另开合同切片，同步 OpenAPI、DB CHECK、migration、前端类型与兼容测试。

## 注册期守卫

以下情况在注册期拒绝：

- 重名、内置保留名；
- code/参数名不符合规范；
- 参数重复、类型/范围/allowed values 不合法或规范化后重复；
- evaluator/target resolver 缺失、签名不匹配、async function、async callable；
- built-in registry 被 raw replacement/unregister 后，resolver fail-closed。

运行时还会拒绝非 bool evaluator 返回、awaitable/deferred 返回和非法 target reference；action adapter 总是在收到 canonical、safe trigger event 后才执行。

## 行为保持

- 现有六种 condition 和四种 action 的 code、参数、错误顺序、canonical digest、target 优先级、租户安全字段与 durable transaction 语义保持不变。
- service 仍只创建 action request，不在本片 dispatch notification/approval/task 等外部副作用。
- 未知或未注册的持久化 code 仍 fail-closed。

## 验证

- 新增 registry/contract/动态路径测试：**60 passed**。
- Stage25 registry + core + service + API + readiness 合并回归：**120 passed**，保留既有 **565** 条 deprecation warnings。
- Ruff：通过。
- `.venv` `py_compile`：通过。
- `git diff --check`：通过。
- 反向验证：临时在进程内禁用 condition resolver，动态 condition 测试按预期失败；resolver 在 finally 中恢复，未修改源码。
- 独立 sub-agent 首轮发现并修复 4 类问题：持久化合同未独立冻结、连字符 code lookup、raw event target adapter、API 参数声明不完整；第二轮发现并修复 identifier 校验与规范化重复值；第三轮发现并修复无显式 minimum 的整数默认值不一致。最终 follow-up review：**PASS，无剩余 findings**。

## 后续边界

- 这片只完成 Python 实现层的扩展 seam，不宣称新增持久化 code 已可直接上线。
- trigger adapter registry 仍是同一 Axis #12 的既有部分；本片没有重复建立第二套 registry kernel。
- 与投影 Graph comparator、Catalog-deleted enumeration 等 Axis #3 未完成边界无关。

## 接手入口

1. 先读设计：`docs/plans/2026-09-25-automation-condition-action-strategy-registry-design.md`。
2. 修改现有实现时，先新增/调整 `AutomationParameterSpec` 和 strategy，再保持冻结 contract parity guard 通过。
3. 若 code 要落库，不要只改 registry；按合同边界单独完成 API/OpenAPI/ORM CHECK/migration 与兼容矩阵。
4. 重点回归：`tests/test_automation_rule_strategy_registry.py`、Stage25 core/service/API/readiness 四套测试。
