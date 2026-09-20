import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Alert } from "tdesign-react";
import { Tabs } from "../../ui/index";
import { RefreshIcon } from "tdesign-icons-react";

import type { AutomationRule, AutomationRuleRevision } from "../model/automationModel";
import AutomationAttentionBoard from "./AutomationAttentionBoard";
import AutomationHeader from "./AutomationHeader";
import AutomationLifecycleRail from "./AutomationLifecycleRail";
import { ActivitySurface, RequestsSurface, RulesSurface, RunsSurface } from "./AutomationTables";
import RuleBuilderDialog from "./RuleBuilderDialog";
import RuleDetailDrawer from "./RuleDetailDrawer";
import { useAutomationFocusReturn } from "./automationUi";
import type {
  AutomationCenterController,
  AutomationCenterTab,
  AutomationRuleBuilderInput,
  AutomationRevisionBuilderInput,
  AutomationRuleDetailValue,
} from "./types";
import "../automation-workflows.css";

export interface AutomationCenterProps {
  controller: AutomationCenterController;
  capabilityReady?: boolean;
  readOnly?: boolean;
  mobile?: boolean;
  tenantLabel?: string;
  title?: string;
  onHandoff?: (rule: AutomationRule, revision: AutomationRuleRevision) => void;
}


function fallbackDetail(
  rule: AutomationRule,
  controller: AutomationCenterController,
): AutomationRuleDetailValue {
  const revisions = controller.revisions.items.filter((item) => item.rule_id === rule.id);
  return {
    rule,
    current_revision: revisions.find((item) => item.id === rule.current_revision_id) ?? null,
    revisions,
    recent_runs: controller.runs.items.filter((item) => item.rule_id === rule.id),
    events: controller.activity.items.filter((item) => item.rule_id === rule.id),
  };
}

function mutationResourceId(value: unknown): string | null {
  if (typeof value !== "object" || value === null || !("resource_id" in value)) return null;
  const resourceId = (value as { resource_id?: unknown }).resource_id;
  return typeof resourceId === "string" && resourceId ? resourceId : null;
}

function authorityTenantId(controller: AutomationCenterController): string | null {
  return (
    controller.summary.value?.tenant_id ??
    controller.rules.items[0]?.tenant_id ??
    controller.revisions.items[0]?.tenant_id ??
    controller.runs.items[0]?.tenant_id ??
    controller.requests.items[0]?.tenant_id ??
    controller.activity.items[0]?.tenant_id ??
    null
  );
}

function isContextResetBoundary(controller: AutomationCenterController): boolean {
  const collections = [
    controller.rules,
    controller.revisions,
    controller.runs,
    controller.requests,
    controller.activity,
  ];
  return (
    controller.summary.value === null &&
    controller.detail.value === null &&
    collections.every(
      (collection) =>
        collection.items.length === 0 &&
        (collection.status === "idle" || collection.status === "loading"),
    ) &&
    (controller.summary.status === "idle" || controller.summary.status === "loading") &&
    (controller.detail.status === "idle" || controller.detail.status === "loading")
  );
}

export default function AutomationCenter({
  controller,
  capabilityReady = true,
  readOnly = false,
  mobile = false,
  tenantLabel = "当前租户",
  onHandoff,
}: AutomationCenterProps) {
  const [tab, setTab] = useState<AutomationCenterTab>("rules");
  const [selectedRule, setSelectedRule] = useState<AutomationRule | null>(null);
  const [detailVisible, setDetailVisible] = useState(false);
  const [builderVisible, setBuilderVisible] = useState(false);
  const [pendingCreatedRuleId, setPendingCreatedRuleId] = useState<string | null>(null);
  const [builderRule, setBuilderRule] = useState<AutomationRule | null>(null);
  const [builderRevision, setBuilderRevision] = useState<AutomationRuleRevision | null>(null);
  const focus = useAutomationFocusReturn();
  const currentTenantId = authorityTenantId(controller);
  const contextResetBoundary = isContextResetBoundary(controller);
  const previousContext = useRef<{
    capabilityReady: boolean;
    readOnly: boolean;
    tenantLabel: string;
    active: boolean;
    tenantId: string | null;
    resetBoundary: boolean;
  } | null>(null);

  useLayoutEffect(() => {
    const previous = previousContext.current;
    const contextChanged =
      previous !== null &&
      (previous.capabilityReady !== capabilityReady ||
        previous.readOnly !== readOnly ||
        previous.tenantLabel !== tenantLabel ||
        previous.active !== controller.active ||
        (previous.tenantId !== null &&
          currentTenantId !== null &&
          previous.tenantId !== currentTenantId) ||
        (contextResetBoundary && !previous.resetBoundary));
    if (contextChanged) {
      setSelectedRule(null);
      setDetailVisible(false);
      setBuilderVisible(false);
      setPendingCreatedRuleId(null);
      setBuilderRule(null);
      setBuilderRevision(null);
      focus.restore();
    }
    previousContext.current = {
      capabilityReady,
      readOnly,
      tenantLabel,
      active: controller.active,
      tenantId: currentTenantId,
      resetBoundary: contextResetBoundary,
    };
  }, [
    capabilityReady,
    contextResetBoundary,
    controller.active,
    currentTenantId,
    focus,
    readOnly,
    tenantLabel,
  ]);

  const detailValue = useMemo(() => {
    if (!selectedRule) return null;
    if (controller.detail.value?.rule.id === selectedRule.id) return controller.detail.value;
    if (["unavailable", "error", "partial", "loading"].includes(controller.detail.status)) {
      return controller.detail.status === "partial" &&
        controller.detail.value?.rule.id === selectedRule.id
        ? controller.detail.value
        : null;
    }
    return fallbackDetail(selectedRule, controller);
  }, [controller, selectedRule]);

  const detailStatus = selectedRule
    ? detailValue
      ? controller.detail.status === "idle"
        ? "ready"
        : controller.detail.status
      : controller.detail.status === "idle"
        ? "loading"
        : controller.detail.status
    : "idle";

  const openRule = useCallback(
    (rule: AutomationRule, trigger: HTMLElement) => {
      focus.capture();
      setSelectedRule(rule);
      setDetailVisible(true);
      if (controller.detail.value?.rule.id !== rule.id) void controller.detail.load?.(rule.id);
      void trigger;
    },
    [controller.detail, focus],
  );

  const closeDetail = useCallback(() => {
    setDetailVisible(false);
    setSelectedRule(null);
    focus.restore();
  }, [focus]);

  const openCreateRule = useCallback(() => {
    if (readOnly) return;
    focus.capture();
    setBuilderRule(null);
    setBuilderRevision(null);
    setBuilderVisible(true);
  }, [focus, readOnly]);

  const openCreateRevision = useCallback(
    (rule: AutomationRule) => {
      if (readOnly) return;
      focus.capture();
      setBuilderRule(rule);
      const detailRevisions =
        controller.detail.value?.rule.id === rule.id ? controller.detail.value.revisions : [];
      const revisions = [...detailRevisions, ...controller.revisions.items];
      setBuilderRevision(
        revisions.find((item) => item.rule_id === rule.id && item.revision === rule.revision) ??
          revisions.find((item) => item.id === rule.current_revision_id) ??
          null,
      );
      setBuilderVisible(true);
    },
    [controller.detail.value, controller.revisions.items, focus, readOnly],
  );

  const closeBuilder = useCallback(() => {
    setBuilderVisible(false);
    setBuilderRule(null);
    setBuilderRevision(null);
    focus.restore();
  }, [focus]);

  const submitBuilder = useCallback(
    async (input: AutomationRuleBuilderInput | AutomationRevisionBuilderInput) => {
      if (builderRule && "expectedRevision" in input) {
        if (!controller.mutation.createRevision) return;
        await controller.mutation.createRevision(builderRule, input);
      } else {
        if (!controller.mutation.createRule || "expectedRevision" in input) return;
        const outcome = await controller.mutation.createRule(input);
        const createdRuleId = mutationResourceId(outcome);
        if (createdRuleId) setPendingCreatedRuleId(createdRuleId);
      }
      closeBuilder();
    },
    [builderRule, closeBuilder, controller.mutation],
  );

  useEffect(() => {
    if (!pendingCreatedRuleId) return;
    const createdRule = controller.rules.items.find((item) => item.id === pendingCreatedRuleId);
    if (!createdRule) return;
    setSelectedRule(createdRule);
    setDetailVisible(true);
    setPendingCreatedRuleId(null);
    if (controller.detail.value?.rule.id !== createdRule.id) {
      void controller.detail.load?.(createdRule.id);
    }
  }, [controller.detail, controller.rules.items, pendingCreatedRuleId]);

  const refresh = useCallback(() => {
    void controller.summary.reload?.();
    void controller.rules.reload?.();
    if (tab === "runs") void controller.runs.reload?.();
    if (tab === "requests") void controller.requests.reload?.();
    if (tab === "activity") void controller.activity.reload?.();
  }, [controller, tab]);

  useEffect(() => {
    if (tab === "runs") void controller.runs.reload?.();
    if (tab === "requests") void controller.requests.reload?.();
    if (tab === "activity") void controller.activity.reload?.();
  }, [tab, controller.activity.reload, controller.requests.reload, controller.runs.reload]);

  const mutationError = controller.mutation.error;
  const summary = controller.summary.value;

  return (
    <section
      className={`automation-workflows${capabilityReady ? "" : " automation-workflows--capability-blocked"}`}
      role="region"
      aria-label="自动化中心"
      data-testid="automation-center"
    >
      <AutomationHeader
        tenantLabel={tenantLabel}
        asOf={summary?.as_of}
        readOnly={readOnly}
        onRefresh={refresh}
        onCreateRule={openCreateRule}
      />
      {!capabilityReady ? (
        <div className="automation-workflows__capability-alert" role="alert">
          <Alert
            theme="warning"
            title="自动化能力尚未就绪"
            message="当前仅展示已加载的安全事实；创建、revision、启用和暂停操作已禁用。"
          />
        </div>
      ) : null}
      {mutationError ? (
        <div className="automation-workflows__mutation-error" role="alert">
          <Alert theme="error" title="自动化变更未完成" message={mutationError} />
        </div>
      ) : null}
      <AutomationAttentionBoard summary={controller.summary} />
      <AutomationLifecycleRail summary={summary} activityCount={controller.activity.items.length} />
      <section
        className="automation-workflows__workspace"
        aria-labelledby="automation-workspace-title"
      >
        <div className="automation-workflows__workspace-heading">
          <div>
            <span className="automation-workflows__eyebrow">CONTROL REGISTER</span>
            <h2 id="automation-workspace-title">自动化控制台</h2>
            <p>规则定义、运行尝试、动作请求和事件证据分层展示。</p>
          </div>
          <span className="automation-workflows__workspace-fence">
            <RefreshIcon aria-hidden="true" />{" "}
            {controller.active ? "租户范围已锁定" : "权威未生效"}
          </span>
        </div>
        <Tabs
          className="rag-tabs automation-workflows__tabs"
          aria-label="自动化工作面"
          keepAlive
          activeKey={tab}
          onChange={(value: any) => setTab(String(value) as AutomationCenterTab)}
        >
          <Tabs.TabPanel value="rules" label="规则">
            <section className="automation-workflows__tab-panel" aria-label="自动化规则">
              <div className="automation-workflows__list-heading">
                <div>
                  <span className="automation-workflows__eyebrow">RULE REGISTER</span>
                  <h3>规则</h3>
                  <p>每条规则都绑定当前 revision 与固定触发/条件/动作 allow-list。</p>
                </div>
                <span className="automation-workflows__record-count">
                  {controller.rules.items.length} rules
                </span>
              </div>
              <RulesSurface
                rules={controller.rules}
                revisions={controller.revisions}
                mobile={mobile}
                readOnly={readOnly}
                onOpenRule={openRule}
              />
            </section>
          </Tabs.TabPanel>
          <Tabs.TabPanel value="runs" label="运行记录">
            <section className="automation-workflows__tab-panel" aria-label="自动化运行记录">
              <div className="automation-workflows__list-heading">
                <div>
                  <span className="automation-workflows__eyebrow">RUN ATTEMPTS</span>
                  <h3>运行记录</h3>
                  <p>运行只记录纯条件计算与受限请求结果，不代表下游动作已执行。</p>
                </div>
                <span className="automation-workflows__record-count">
                  {controller.runs.items.length} runs
                </span>
              </div>
              <RunsSurface runs={controller.runs} mobile={mobile} />
            </section>
          </Tabs.TabPanel>
          <Tabs.TabPanel value="requests" label="请求">
            <section className="automation-workflows__tab-panel" aria-label="动作请求">
              <div className="automation-workflows__list-heading">
                <div>
                  <span className="automation-workflows__eyebrow">ACTION REQUEST REGISTER</span>
                  <h3>请求</h3>
                  <p>动作请求是持久、有界的输出，需由既有领域权威接管。</p>
                </div>
                <span className="automation-workflows__record-count">
                  {controller.requests.items.length} requests
                </span>
              </div>
              <RequestsSurface requests={controller.requests} mobile={mobile} />
            </section>
          </Tabs.TabPanel>
          <Tabs.TabPanel value="activity" label="活动">
            <section className="automation-workflows__tab-panel" aria-label="自动化活动">
              <ActivitySurface activity={controller.activity} />
            </section>
          </Tabs.TabPanel>
        </Tabs>
      </section>
      <RuleDetailDrawer
        visible={detailVisible}
        detail={detailValue}
        status={detailStatus}
        readOnly={readOnly}
        mobile={mobile}
        onClose={closeDetail}
        onCreateRevision={openCreateRevision}
        onActivate={(rule) => {
          if (!readOnly) void controller.mutation.activateRule?.(rule);
        }}
        onPause={(rule) => {
          if (!readOnly) void controller.mutation.pauseRule?.(rule);
        }}
        onHandoff={onHandoff}
      />
      <RuleBuilderDialog
        visible={builderVisible}
        rule={builderRule}
        revision={builderRevision}
        readOnly={readOnly || !capabilityReady}
        saving={controller.mutation.status === "saving"}
        onClose={closeBuilder}
        onSubmit={submitBuilder}
      />
    </section>
  );
}
