import { useMemo } from "react";
import KnowledgeServingCenter from "./components/KnowledgeServingCenter";
import type { KnowledgeServingController, ServingHandoffCallback } from "./components/types";
import { useEnterpriseKnowledgeServing } from "./hooks/useEnterpriseKnowledgeServing";
import type { ServingApi } from "./api/servingApi";
import { safeServingDisplayText, type ServingHandoff } from "./model/servingModel";
import "./knowledge-serving.css";

export interface KnowledgeServingPageProps {
  active: boolean;
  tenantId: string;
  accountId?: string;
  datasetId: string;
  actorToken: string;
  capabilityReady: boolean;
  tenantLabel: string;
  datasetName?: string;
  workspaceName?: string;
  readOnly: boolean;
  mobile?: boolean;
  api?: ServingApi;
  onHandoff?: ServingHandoffCallback;
}
function safeError(error: Error | string | null): string | null {
  if (!error) return null;
  return (
    safeServingDisplayText(error instanceof Error ? error.message : error) ??
    "知识服务可靠性权威不可用"
  );
}
function controllerFromHook(
  hook: ReturnType<typeof useEnterpriseKnowledgeServing>,
): KnowledgeServingController {
  return {
    active: hook.active,
    readOnly: hook.readOnly,
    summary: {
      status: hook.summary.status,
      value: hook.summary.value,
      error: safeError(hook.summary.error),
      reload: hook.load.reload,
    },
    profile: {
      status: hook.profile.status,
      value: hook.profile.value,
      error: safeError(hook.profile.error),
      reload: hook.load.reload,
    },
    snapshots: {
      status: hook.snapshots.status,
      items: hook.snapshots.items,
      count: hook.snapshots.count,
      nextCursor: hook.snapshots.nextCursor,
      invalidItemCount: hook.snapshots.invalidItemCount,
      error: safeError(hook.snapshots.error),
      reload: () => hook.snapshots.reload(),
    },
    detail: {
      status: hook.detail.status,
      value: hook.detail.value,
      error: safeError(hook.detail.error),
      load: hook.detail.load,
    },
    activity: {
      status: hook.activity.status,
      items: hook.activity.items,
      count: hook.activity.count,
      nextCursor: hook.activity.nextCursor,
      invalidItemCount: hook.activity.invalidItemCount,
      error: safeError(hook.activity.error),
      load: () => hook.activity.load(),
    },
    mutation: {
      status: hook.mutation.status,
      outcome: hook.mutation.outcome,
      error: safeError(hook.mutation.error),
      createProfile: hook.mutation.createProfile,
      createPolicyRevision: hook.mutation.createPolicyRevision,
      activateProfile: hook.mutation.activateProfile,
      previewPolicy: (input) =>
        hook.mutation.previewPolicy({
          ...input,
          profileId: input.profileId ?? hook.profile.value?.profile.id,
        }),
    },
  };
}
export default function KnowledgeServingPage({
  active,
  tenantId,
  accountId,
  datasetId,
  actorToken,
  capabilityReady,
  tenantLabel,
  datasetName,
  workspaceName,
  readOnly,
  mobile = false,
  api,
  onHandoff,
}: KnowledgeServingPageProps) {
  const hook = useEnterpriseKnowledgeServing(
    { tenantId, accountId, datasetId, actorToken },
    { enabled: active && capabilityReady, readOnly, api },
  );
  const controller = useMemo(() => controllerFromHook(hook), [hook]);
  if (!capabilityReady)
    return (
      <section
        className="knowledge-serving knowledge-serving--unavailable"
        aria-label="知识服务可靠性中心"
      >
        <div role="alert" className="knowledge-serving__capability-alert">
          <strong>知识服务可靠性能力不可用</strong>
          <span>
            当前 Catalog readiness 未证明 Stage26 capability 可用，页面不会读取或写入服务事实。
          </span>
        </div>
      </section>
    );
  const safeHandoff = (handoff: ServingHandoff) => {
    if (handoff.dataset_id !== datasetId) return;
    onHandoff?.(handoff);
  };
  return (
    <KnowledgeServingCenter
      controller={controller}
      tenantLabel={tenantLabel}
      datasetName={datasetName}
      workspaceName={workspaceName}
      datasetId={datasetId}
      mobile={mobile}
      onHandoff={safeHandoff}
    />
  );
}
