import { useMemo, useState } from "react";
import { AutomationCenter, type AutomationCenterController } from "./components";
import { useEnterpriseAutomation } from "./hooks/useEnterpriseAutomation";
import {
  projectAutomationTriggerRoute,
  type AutomationRoute,
  type AutomationRule,
  type AutomationRuleRevision,
} from "./model/automationModel";
export interface AutomationPageProps {
  tenantId: string;
  accountId?: string;
  actorToken: string;
  capabilityReady: boolean;
  tenantLabel: string;
  readOnly: boolean;
  mobile?: boolean;
  onAutomationHandoff?: (route: AutomationRoute) => void;
}
const safeError = (e: Error | null) =>
  e ? "企业自动化权威不可用" : null;
const SAFE_HANDOFF_KEYS = ["task_id", "source_id", "approval_request_id"] as const;
const SAFE_HANDOFF_ID = /^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}$/;
function safeHandoffFacts(revision: AutomationRuleRevision): Record<string, string> {
  const facts: Record<string, string> = {};
  for (const step of revision.action_plan) {
    for (const key of SAFE_HANDOFF_KEYS) {
      const value = step.params[key];
      if (
        typeof value === "string" &&
        SAFE_HANDOFF_ID.test(value) &&
        !value.includes("..") &&
        facts[key] === undefined
      )
        facts[key] = value;
    }
  }
  return facts;
}
export default function AutomationPage({
  tenantId,
  accountId,
  actorToken,
  capabilityReady,
  tenantLabel,
  readOnly,
  mobile = false,
  onAutomationHandoff,
}: AutomationPageProps) {
  const [ruleStatusFilter, setRuleStatusFilter] = useState<
    "all" | "draft" | "active" | "paused"
  >("all");
  const automation = useEnterpriseAutomation(
    { tenantId, accountId, actorToken },
    ruleStatusFilter === "all"
      ? { enabled: capabilityReady, readOnly }
      : { enabled: capabilityReady, readOnly, ruleQuery: { status: ruleStatusFilter } },
  );
  const controller = useMemo<AutomationCenterController>(
    () => ({
      active: automation.active,
      summary: {
        status: automation.summary.status,
        value: automation.summary.value,
        error: safeError(automation.summary.error),
        reload: automation.load.reload,
      },
      rules: {
        status: automation.rules.status,
        items: automation.rules.items,
        invalidItemCount: automation.rules.invalidItemCount,
        nextCursor: automation.rules.nextCursor,
        error: safeError(automation.rules.error),
        reload: automation.rules.reload,
      },
      revisions: {
        status: automation.revisions.status,
        items: automation.revisions.items,
        invalidItemCount: automation.revisions.invalidItemCount,
        nextCursor: automation.revisions.nextCursor,
        error: safeError(automation.revisions.error),
        reload: automation.revisions.reload,
      },
      runs: {
        status: automation.runs.status,
        items: automation.runs.items,
        invalidItemCount: automation.runs.invalidItemCount,
        nextCursor: automation.runs.nextCursor,
        error: safeError(automation.runs.error),
        reload: automation.runs.reload,
      },
      requests: {
        status: automation.requests.status,
        items: automation.requests.items,
        invalidItemCount: automation.requests.invalidItemCount,
        nextCursor: automation.requests.nextCursor,
        error: safeError(automation.requests.error),
        reload: automation.requests.reload,
      },
      activity: {
        status: automation.activity.status,
        items: automation.activity.items,
        invalidItemCount: automation.activity.invalidItemCount,
        nextCursor: automation.activity.nextCursor,
        error: safeError(automation.activity.error),
        reload: automation.activity.reload,
      },
      detail: {
        status: automation.detail.status,
        value: automation.detail.value,
        error: safeError(automation.detail.error),
        load: (id?: string) => (id ? automation.detail.load(id) : Promise.resolve(false)),
      },
      mutation: {
        status: automation.mutation.status,
        error: safeError(automation.mutation.error),
        createRule: automation.mutation.createRule,
        createRevision: automation.mutation.createRevision,
        activateRule: automation.mutation.activate,
        pauseRule: automation.mutation.pause,
      },
    }),
    [automation],
  );
  const handoff = (rule: AutomationRule, revision: AutomationRuleRevision) => {
    void rule;
    onAutomationHandoff?.(
      projectAutomationTriggerRoute({
        trigger_code: revision.trigger_code,
        safe_facts_json: safeHandoffFacts(revision),
      }),
    );
  };
  return (
    <div className="automation-workflows-page">
      <div className="automation-workflows-page__filters">
        <label>
          <span>规则状态</span>
          <select
            aria-label="规则状态筛选"
            value={ruleStatusFilter}
            onChange={(event) =>
              setRuleStatusFilter(
                event.target.value as "all" | "draft" | "active" | "paused",
              )
            }
          >
            <option value="all">全部非归档</option>
            <option value="draft">草稿</option>
            <option value="active">已启用</option>
            <option value="paused">已暂停</option>
          </select>
        </label>
      </div>
      <AutomationCenter
        controller={controller}
        capabilityReady={capabilityReady}
        readOnly={readOnly}
        mobile={mobile}
        tenantLabel={tenantLabel}
        onHandoff={onAutomationHandoff ? handoff : undefined}
      />
    </div>
  );
}
