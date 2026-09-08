import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Dialog, Form, Input, Select, Tag, Textarea } from "tdesign-react";
import { AddIcon, BrowseIcon, SecuredIcon } from "tdesign-icons-react";

import { validateAutomationRuleDefinition } from "../api/automationApi";
import type {
  AutomationActionCode,
  AutomationConditionCode,
  AutomationRule,
  AutomationRuleDefinition,
  AutomationRuleRevision,
  AutomationSafeValue,
  AutomationSeverity,
  AutomationTriggerCode,
} from "../model/automationModel";
import { actionLabel, conditionLabel, triggerLabel } from "./automationUi";
import AutomationPreviewPanel from "./PreviewPanel";
import type {
  AutomationRuleBuilderInput,
  AutomationRuleDefinitionInput,
  AutomationRevisionBuilderInput,
} from "./types";

export interface RuleBuilderDialogProps {
  visible: boolean;
  rule?: AutomationRule | null;
  revision?: AutomationRuleRevision | null;
  readOnly?: boolean;
  saving?: boolean;
  onClose: () => void;
  onSubmit: (
    input: AutomationRuleBuilderInput | AutomationRevisionBuilderInput,
  ) => void | Promise<unknown>;
}

const TRIGGER_OPTIONS: Array<{ label: string; value: AutomationTriggerCode }> = [
  { label: "任务失败", value: "task_failed" },
  { label: "任务来源过期", value: "task_source_stale" },
  { label: "来源同步失败", value: "source_sync_failed" },
  { label: "质量告警打开", value: "release_quality_alert_opened" },
  { label: "复认证阻塞", value: "release_recertification_blocked" },
  { label: "审批请求终态", value: "approval_request_terminal" },
];
const CONDITION_OPTIONS: Array<{ label: string; value: AutomationConditionCode }> = [
  { label: "始终满足", value: "always" },
  { label: "状态等于", value: "status_is" },
  { label: "需要操作", value: "action_required" },
  { label: "严重级别至少", value: "severity_at_least" },
  { label: "尝试次数耗尽", value: "attempt_exhausted" },
  { label: "来源已过期", value: "source_is_stale" },
];
const ACTION_OPTIONS: Array<{ label: string; value: AutomationActionCode }> = [
  { label: "通知操作员", value: "notify_operator" },
  { label: "请求审批", value: "request_approval" },
  { label: "打开任务关注", value: "open_task_attention" },
  { label: "暂停规则", value: "pause_rule" },
];
const STATUS_OPTIONS = [
  "queued",
  "running",
  "succeeded",
  "failed",
  "cancelled",
  "blocked",
  "unavailable",
  "approved",
  "rejected",
  "expired",
  "completed",
].map((value) => ({ label: value, value }));
const SEVERITY_OPTIONS: Array<{ label: string; value: AutomationSeverity }> = [
  { label: "信息", value: "info" },
  { label: "警告", value: "warning" },
  { label: "错误", value: "error" },
  { label: "严重", value: "critical" },
];
const BOOLEAN_OPTIONS = [
  { label: "是", value: "true" },
  { label: "否", value: "false" },
];
const CONDITION_DEFAULTS: Record<AutomationConditionCode, Record<string, AutomationSafeValue>> = {
  always: {},
  status_is: { status: "failed" },
  action_required: { value: true },
  severity_at_least: { severity: "error" },
  attempt_exhausted: { minimum_attempts: 3 },
  source_is_stale: { value: true },
};
const ACTION_DEFAULTS: Record<AutomationActionCode, Record<string, AutomationSafeValue>> = {
  notify_operator: { category: "task_operations", severity: "error", title: "自动化规则通知" },
  request_approval: {
    action_type: "review",
    resource_type: "knowledge_base",
    reason_code: "automation_request",
  },
  open_task_attention: { task_id: "task-id", reason_code: "automation_attention" },
  pause_rule: { reason_code: "automation_pause" },
};

function stringValue(value: AutomationSafeValue | undefined, fallback: string): string {
  return typeof value === "string" && value.trim() ? value : fallback;
}
function severityValue(value: AutomationSafeValue | undefined): AutomationSeverity {
  return typeof value === "string" && SEVERITY_OPTIONS.some((option) => option.value === value)
    ? (value as AutomationSeverity)
    : "error";
}
function statusValue(value: AutomationSafeValue | undefined): string {
  return typeof value === "string" && STATUS_OPTIONS.some((option) => option.value === value)
    ? value
    : "failed";
}

export function createConditionParams(
  conditionCode: AutomationConditionCode,
  previous: Readonly<Record<string, AutomationSafeValue>> = {},
): Record<string, AutomationSafeValue> {
  const defaults = CONDITION_DEFAULTS[conditionCode];
  if (conditionCode === "status_is")
    return { status: statusValue(previous.status ?? defaults.status) };
  if (conditionCode === "action_required" || conditionCode === "source_is_stale")
    return { value: typeof previous.value === "boolean" ? previous.value : true };
  if (conditionCode === "severity_at_least")
    return { severity: severityValue(previous.severity ?? defaults.severity) };
  if (conditionCode === "attempt_exhausted")
    return {
      minimum_attempts:
        typeof previous.minimum_attempts === "number" &&
        Number.isInteger(previous.minimum_attempts) &&
        previous.minimum_attempts >= 1 &&
        previous.minimum_attempts <= 100
          ? previous.minimum_attempts
          : 3,
    };
  return {};
}

export function createActionParams(
  actionCode: AutomationActionCode,
  previous: Readonly<Record<string, AutomationSafeValue>> = {},
): Record<string, AutomationSafeValue> {
  const defaults = ACTION_DEFAULTS[actionCode];
  if (actionCode === "notify_operator")
    return {
      category: stringValue(previous.category ?? defaults.category, "task_operations"),
      severity: severityValue(previous.severity ?? defaults.severity),
      title: stringValue(previous.title ?? defaults.title, "自动化规则通知"),
    };
  if (actionCode === "request_approval")
    return {
      action_type: stringValue(previous.action_type ?? defaults.action_type, "review"),
      resource_type: stringValue(
        previous.resource_type ?? defaults.resource_type,
        "knowledge_base",
      ),
      reason_code: stringValue(previous.reason_code ?? defaults.reason_code, "automation_request"),
    };
  if (actionCode === "open_task_attention")
    return {
      task_id: stringValue(previous.task_id ?? defaults.task_id, "task-id"),
      reason_code: stringValue(
        previous.reason_code ?? defaults.reason_code,
        "automation_attention",
      ),
    };
  return {
    reason_code: stringValue(previous.reason_code ?? defaults.reason_code, "automation_pause"),
  };
}

export function createDefaultAutomationDefinition(): AutomationRuleDefinition {
  return {
    trigger_code: "task_failed",
    condition_code: "attempt_exhausted",
    condition_params: createConditionParams("attempt_exhausted"),
    action_plan: [
      {
        step_index: 0,
        action_code: "notify_operator",
        params: createActionParams("notify_operator"),
      },
    ],
  };
}

function displayValidationError(value: unknown): string {
  return value instanceof Error ? value.message : "规则定义不满足严格 schema";
}

export default function RuleBuilderDialog({
  visible,
  rule = null,
  revision = null,
  readOnly = false,
  saving = false,
  onClose,
  onSubmit,
}: RuleBuilderDialogProps) {
  const [name, setName] = useState("");
  const [priority, setPriority] = useState("50");
  const [reason, setReason] = useState("");
  const [definition, setDefinition] = useState<AutomationRuleDefinitionInput>(
    createDefaultAutomationDefinition(),
  );
  const [previewVisible, setPreviewVisible] = useState(false);
  const [previewDefinition, setPreviewDefinition] = useState<AutomationRuleDefinitionInput | null>(
    null,
  );
  const [validationError, setValidationError] = useState<string | null>(null);

  useEffect(() => {
    if (!visible) return;
    setName(rule?.name ?? "");
    setPriority(String(rule?.priority ?? 50));
    setReason("");
    setDefinition(
      revision
        ? {
            trigger_code: revision.trigger_code,
            condition_code: revision.condition_code,
            condition_params: { ...revision.condition_params },
            action_plan: revision.action_plan.map((step) => ({
              ...step,
              params: { ...step.params },
            })),
          }
        : createDefaultAutomationDefinition(),
    );
    setPreviewVisible(false);
    setPreviewDefinition(null);
    setValidationError(null);
  }, [revision, rule, visible]);

  const title = rule ? `创建自动化规则 revision · ${rule.name}` : "新建自动化规则";
  const valid = Boolean(name.trim() && reason.trim() && definition.action_plan.length);
  const actionOptions = useMemo(() => ACTION_OPTIONS, []);

  function updateDefinition(patch: Partial<AutomationRuleDefinitionInput>) {
    setDefinition((current) => ({ ...current, ...patch }));
  }
  function updateConditionParam(key: string, value: AutomationSafeValue) {
    setDefinition((current) => ({
      ...current,
      condition_params: { ...current.condition_params, [key]: value },
    }));
  }
  function updateActionParam(key: string, value: AutomationSafeValue) {
    setDefinition((current) => {
      const first = current.action_plan[0] ?? {
        step_index: 0,
        action_code: "notify_operator" as AutomationActionCode,
        params: createActionParams("notify_operator"),
      };
      return {
        ...current,
        action_plan: [
          { ...first, step_index: 0, params: { ...first.params, [key]: value } },
          ...current.action_plan.slice(1),
        ],
      };
    });
  }
  function changeCondition(value: unknown) {
    const conditionCode = String(value) as AutomationConditionCode;
    setDefinition((current) => ({
      ...current,
      condition_code: conditionCode,
      condition_params: createConditionParams(conditionCode, current.condition_params),
    }));
    setValidationError(null);
    setPreviewVisible(false);
    setPreviewDefinition(null);
  }
  function changeAction(value: unknown) {
    const actionCode = String(value) as AutomationActionCode;
    setDefinition((current) => {
      const first = current.action_plan[0];
      return {
        ...current,
        action_plan: [
          {
            step_index: 0,
            action_code: actionCode,
            params: createActionParams(actionCode, first?.params),
          },
          ...current.action_plan.slice(1),
        ],
      };
    });
    setValidationError(null);
    setPreviewVisible(false);
    setPreviewDefinition(null);
  }
  function strictDefinition(): AutomationRuleDefinition | null {
    try {
      const canonical = validateAutomationRuleDefinition(definition);
      setDefinition(canonical);
      setValidationError(null);
      return canonical;
    } catch (error) {
      setValidationError(displayValidationError(error));
      return null;
    }
  }
  function togglePreview() {
    if (previewVisible) {
      setPreviewVisible(false);
      setPreviewDefinition(null);
      return;
    }
    const canonical = strictDefinition();
    if (!canonical) return;
    setPreviewDefinition(canonical);
    setPreviewVisible(true);
  }
  function submit() {
    if (!valid || readOnly) return;
    const canonical = strictDefinition();
    if (!canonical) return;
    const parsedPriority = Number.parseInt(priority, 10);
    const safePriority = Number.isFinite(parsedPriority)
      ? Math.max(0, Math.min(1000, parsedPriority))
      : 50;
    if (rule) {
      void onSubmit({
        expectedRevision: rule.revision,
        definition: canonical,
        reason: reason.trim(),
      });
      return;
    }
    void onSubmit({
      name: name.trim(),
      priority: safePriority,
      definition: canonical,
      reason: reason.trim(),
    });
  }

  const conditionParams = definition.condition_params;
  const actionParams = definition.action_plan[0]?.params ?? {};
  const actionCode = definition.action_plan[0]?.action_code ?? "notify_operator";

  if (!visible) return null;
  return (
    <Dialog
      visible
      header={title}
      width={620}
      confirmBtn={{
        content: rule ? "创建下一版" : "保存为草稿",
        theme: "primary",
        disabled: !valid || readOnly,
      }}
      confirmLoading={saving}
      cancelBtn={{ content: "取消" }}
      destroyOnClose
      closeOnEscKeydown
      onClose={onClose}
      onConfirm={submit}
      {...({ role: "dialog", "aria-label": title } as Record<string, unknown>)}
    >
      <Alert
        theme="info"
        icon={<SecuredIcon aria-hidden="true" />}
        title="受限规则构建器"
        message="仅允许固定触发器、条件和动作；预览会先经过同一 strict API schema 校验。"
      />
      {validationError ? (
        <Alert
          theme="error"
          title="规则定义校验失败"
          message={validationError}
          data-testid="automation-rule-builder-validation-error"
        />
      ) : null}
      <Form labelAlign="top" layout="vertical">
        {!rule ? (
          <Form.FormItem>
            <label className="automation-workflows__field">
              <span>规则名称</span>
              <Input
                value={name}
                disabled={readOnly}
                onChange={(value) => setName(String(value))}
                placeholder="例如：失败任务通知"
              />
            </label>
          </Form.FormItem>
        ) : null}
        {!rule ? (
          <Form.FormItem>
            <label className="automation-workflows__field">
              <span>优先级</span>
              <Input
                aria-label="规则优先级"
                value={priority}
                disabled={readOnly}
                onChange={(value) => setPriority(String(value))}
              />
            </label>
          </Form.FormItem>
        ) : null}
        <div className="automation-workflows__builder-grid">
          <Form.FormItem>
            <label className="automation-workflows__field">
              <span>WHEN · 触发器</span>
              <Select
                aria-label="触发器"
                value={definition.trigger_code}
                options={TRIGGER_OPTIONS}
                disabled={readOnly}
                onChange={(value) =>
                  updateDefinition({ trigger_code: String(value) as AutomationTriggerCode })
                }
              />
            </label>
          </Form.FormItem>
          <Form.FormItem>
            <label className="automation-workflows__field">
              <span>IF · 条件</span>
              <Select
                aria-label="条件"
                value={definition.condition_code}
                options={CONDITION_OPTIONS}
                disabled={readOnly}
                onChange={changeCondition}
              />
            </label>
          </Form.FormItem>
        </div>
        <Form.FormItem>
          <div className="automation-workflows__field">
            <span>条件参数</span>
            {definition.condition_code === "always" ? (
              <Tag theme="default" variant="light-outline">
                无参数
              </Tag>
            ) : null}
            {definition.condition_code === "status_is" ? (
              <Select
                aria-label="条件状态"
                value={String(conditionParams.status ?? "failed")}
                options={STATUS_OPTIONS}
                disabled={readOnly}
                onChange={(value) => updateConditionParam("status", String(value))}
              />
            ) : null}
            {definition.condition_code === "action_required" ||
            definition.condition_code === "source_is_stale" ? (
              <Select
                aria-label="条件布尔值"
                value={String(conditionParams.value === true)}
                options={BOOLEAN_OPTIONS}
                disabled={readOnly}
                onChange={(value) => updateConditionParam("value", String(value) === "true")}
              />
            ) : null}
            {definition.condition_code === "severity_at_least" ? (
              <Select
                aria-label="条件严重级别"
                value={String(conditionParams.severity ?? "error")}
                options={SEVERITY_OPTIONS}
                disabled={readOnly}
                onChange={(value) => updateConditionParam("severity", String(value))}
              />
            ) : null}
            {definition.condition_code === "attempt_exhausted" ? (
              <Input
                aria-label="最小尝试次数"
                value={String(conditionParams.minimum_attempts ?? "")}
                disabled={readOnly}
                onChange={(value) =>
                  updateConditionParam(
                    "minimum_attempts",
                    String(value).trim() ? Number(value) || 0 : null,
                  )
                }
              />
            ) : null}
          </div>
        </Form.FormItem>
        <Form.FormItem>
          <div className="automation-workflows__field">
            <span>REQUEST · 动作</span>
            <Select
              aria-label="动作"
              value={actionCode}
              options={actionOptions}
              disabled={readOnly}
              onChange={changeAction}
            />
            {actionCode === "notify_operator" ? (
              <>
                <Input
                  aria-label="通知分类"
                  value={String(actionParams.category ?? "")}
                  disabled={readOnly}
                  onChange={(value) => updateActionParam("category", String(value))}
                />
                <Select
                  aria-label="通知严重级别"
                  value={String(actionParams.severity ?? "error")}
                  options={SEVERITY_OPTIONS}
                  disabled={readOnly}
                  onChange={(value) => updateActionParam("severity", String(value))}
                />
                <Input
                  aria-label="通知标题"
                  value={String(actionParams.title ?? "")}
                  disabled={readOnly}
                  onChange={(value) => updateActionParam("title", String(value))}
                />
              </>
            ) : null}
            {actionCode === "request_approval" ? (
              <>
                <Input
                  aria-label="审批动作类型"
                  value={String(actionParams.action_type ?? "")}
                  disabled={readOnly}
                  onChange={(value) => updateActionParam("action_type", String(value))}
                />
                <Input
                  aria-label="审批资源类型"
                  value={String(actionParams.resource_type ?? "")}
                  disabled={readOnly}
                  onChange={(value) => updateActionParam("resource_type", String(value))}
                />
                <Input
                  aria-label="审批原因代码"
                  value={String(actionParams.reason_code ?? "")}
                  disabled={readOnly}
                  onChange={(value) => updateActionParam("reason_code", String(value))}
                />
              </>
            ) : null}
            {actionCode === "open_task_attention" ? (
              <>
                <Input
                  aria-label="任务目标 ID"
                  value={String(actionParams.task_id ?? "")}
                  disabled={readOnly}
                  onChange={(value) => updateActionParam("task_id", String(value))}
                />
                <Input
                  aria-label="任务关注原因代码"
                  value={String(actionParams.reason_code ?? "")}
                  disabled={readOnly}
                  onChange={(value) => updateActionParam("reason_code", String(value))}
                />
              </>
            ) : null}
            {actionCode === "pause_rule" ? (
              <Input
                aria-label="暂停原因代码"
                value={String(actionParams.reason_code ?? "")}
                disabled={readOnly}
                onChange={(value) => updateActionParam("reason_code", String(value))}
              />
            ) : null}
          </div>
        </Form.FormItem>
        <Form.FormItem>
          <label className="automation-workflows__field">
            <span>变更原因</span>
            <Textarea
              aria-label="变更原因"
              value={reason}
              disabled={readOnly}
              placeholder="说明这条规则的业务边界与安全目的"
              onChange={(value) => setReason(String(value))}
            />
          </label>
        </Form.FormItem>
      </Form>
      <div className="automation-workflows__builder-actions">
        <Button
          variant="outline"
          icon={<BrowseIcon />}
          aria-label="查看预览"
          onClick={togglePreview}
        >
          {previewVisible ? "收起预览" : "查看预览"}
        </Button>
        <span>
          {triggerLabel(definition.trigger_code)} → {conditionLabel(definition.condition_code)} →{" "}
          {actionLabel(actionCode)}
        </span>
        <AddIcon aria-hidden="true" />
      </div>
      {previewVisible ? (
        <AutomationPreviewPanel
          definition={previewDefinition ?? definition}
          name={name}
          reason={reason}
        />
      ) : null}
    </Dialog>
  );
}
