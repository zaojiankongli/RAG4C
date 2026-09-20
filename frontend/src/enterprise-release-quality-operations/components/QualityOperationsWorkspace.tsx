import { useCallback, useMemo, useRef, useState } from "react";
import { Tabs } from "tdesign-react";

import KnowledgeBaseReleaseCenter from "../../enterprise-knowledge-base-release/components/KnowledgeBaseReleaseCenter";
import type { ReleaseApi } from "../../enterprise-knowledge-base-release/hooks/useKnowledgeBaseReleases";
import type { ReleaseQualityApi } from "../../enterprise-release-quality/api/qualityApi";
import { createOperationsIdempotencyKey, type OperationsApiScope } from "../api/operationsApi";
import {
  useReleaseQualityOperations,
  type OperationsApi,
  type ReleaseQualityOperationsHook,
} from "../hooks/useReleaseQualityOperations";
import type {
  OperationsHorizonBand,
  QualityAuthorityProjection,
  QualityOperationsAlert,
  RecertificationJob,
} from "../model/operationsModel";
import QualityOperationsCenter, {
  type QualityOperationsCenterProps,
} from "./QualityOperationsCenter";
import QualityOperationsDetailDrawer from "./QualityOperationsDetailDrawer";
import QualityOperationsMutationDialogs, {
  type QualityOperationsMutationMode,
} from "./QualityOperationsMutationDialogs";

export interface QualityOperationsWorkspaceProps {
  active: boolean;
  scope: OperationsApiScope;
  datasetName?: string;
  workspaceName?: string;
  readOnly?: boolean;
  scopeVerified?: boolean;
  releaseApi?: ReleaseApi;
  qualityApi?: ReleaseQualityApi;
  operationsApi?: OperationsApi;
  onOpenAuthority?: QualityOperationsCenterProps["onOpenAuthority"];
  onQueueRecertification?: (
    authority: QualityAuthorityProjection,
    options: { idempotencyKey: string },
  ) => void | Promise<void>;
}

type QualityOperationsTab = "release-center" | "quality-operations";

const QUALITY_TABS: QualityOperationsTab[] = ["release-center", "quality-operations"];

type QualityOperationsPanelProps = Pick<
  QualityOperationsWorkspaceProps,
  "datasetName" | "workspaceName" | "readOnly" | "onOpenAuthority" | "onQueueRecertification"
> & {
  onAcknowledgeRequest?: (alert: QualityOperationsAlert) => void;
  selectedHorizon: OperationsHorizonBand | "all";
  onHorizonChange: (band: OperationsHorizonBand) => void;
};

function qualityOperationsError(operations: ReleaseQualityOperationsHook): string | null {
  return (
    operations.load.error?.message ??
    operations.summary.error?.message ??
    operations.alerts.error?.message ??
    operations.jobs.error?.message ??
    null
  );
}

function QualityOperationsPanel({
  scopeVerified,
  operations,
  datasetName,
  workspaceName,
  readOnly,
  onOpenAuthority,
  onQueueRecertification,
  onAcknowledgeRequest,
  selectedHorizon,
  onHorizonChange,
}: QualityOperationsPanelProps & {
  scopeVerified: boolean;
  operations: ReleaseQualityOperationsHook;
}) {
  const qualityError = scopeVerified
    ? qualityOperationsError(operations)
    : "等待已验证的工作区作用域";
  const canManageAlerts = Boolean(scopeVerified && operations.active && !readOnly);
  const acknowledgeAlert = canManageAlerts ? onAcknowledgeRequest : undefined;
  const queueRecertification =
    canManageAlerts && onQueueRecertification
      ? (authority: QualityAuthorityProjection) => {
          void onQueueRecertification(authority, {
            idempotencyKey: createOperationsIdempotencyKey(),
          });
        }
      : undefined;

  return (
    <QualityOperationsCenter
      datasetName={datasetName ?? operations.summary.value?.dataset_id ?? "未返回 Dataset"}
      workspaceName={workspaceName}
      summary={scopeVerified ? operations.summary.value : null}
      alerts={operations.alerts.items}
      jobs={operations.jobs.items}
      selectedHorizon={selectedHorizon}
      loading={scopeVerified && operations.load.status === "loading" && !operations.summary.value}
      error={qualityError}
      readOnly={Boolean(readOnly || !canManageAlerts)}
      onHorizonChange={onHorizonChange}
      onRefresh={() => void operations.load.reload()}
      onOpenAuthority={scopeVerified && operations.active ? onOpenAuthority : undefined}
      onAcknowledgeAlert={acknowledgeAlert}
      onQueueRecertification={queueRecertification}
    />
  );
}

export default function QualityOperationsWorkspace({
  active,
  scope,
  datasetName,
  workspaceName,
  readOnly = false,
  scopeVerified = true,
  releaseApi,
  qualityApi,
  operationsApi,
  onOpenAuthority,
  onQueueRecertification,
}: QualityOperationsWorkspaceProps) {
  const [tab, setTab] = useState<QualityOperationsTab>("release-center");
  const [selectedHorizon, setSelectedHorizon] = useState<OperationsHorizonBand | "all">("all");
  const [selectedAuthority, setSelectedAuthority] = useState<QualityAuthorityProjection | null>(
    null,
  );
  const [mutationDialog, setMutationDialog] = useState<{
    mode: QualityOperationsMutationMode;
    alert?: QualityOperationsAlert;
    job?: RecertificationJob;
  } | null>(null);
  const focusEntryRef = useRef<HTMLElement | null>(null);
  const operations = useReleaseQualityOperations(scope, {
    enabled: active && scopeVerified,
    readOnly,
    ...(operationsApi ? { api: operationsApi } : {}),
  });

  const handleHorizonChange = useCallback((band: OperationsHorizonBand) => {
    setSelectedHorizon(band);
  }, []);

  const handleOpenAuthority = useCallback(
    (authority: QualityAuthorityProjection) => {
      focusEntryRef.current =
        document.activeElement instanceof HTMLElement ? document.activeElement : null;
      setSelectedAuthority(authority);
      onOpenAuthority?.(authority);
    },
    [onOpenAuthority],
  );

  const selectedAlert = useMemo(
    () =>
      selectedAuthority
        ? (operations.alerts.items.find(
            (item) =>
              item.release_id === selectedAuthority.release_id &&
              item.channel_id === selectedAuthority.channel_id &&
              item.release_role === selectedAuthority.release_role,
          ) ?? null)
        : null,
    [operations.alerts.items, selectedAuthority],
  );
  const selectedJob = useMemo(
    () =>
      selectedAuthority
        ? (operations.jobs.items.find(
            (item) =>
              item.release_id === selectedAuthority.release_id &&
              item.channel_id === selectedAuthority.channel_id &&
              item.release_role === selectedAuthority.release_role,
          ) ?? null)
        : null,
    [operations.jobs.items, selectedAuthority],
  );

  const selectTab = useCallback((nextTab: QualityOperationsTab) => {
    setTab(nextTab);
  }, []);
  const handleTabKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLButtonElement>, currentTab: QualityOperationsTab) => {
      const currentIndex = QUALITY_TABS.indexOf(currentTab);
      const nextIndex =
        event.key === "ArrowRight"
          ? (currentIndex + 1) % QUALITY_TABS.length
          : event.key === "ArrowLeft"
            ? (currentIndex + QUALITY_TABS.length - 1) % QUALITY_TABS.length
            : event.key === "Home"
              ? 0
              : event.key === "End"
                ? QUALITY_TABS.length - 1
                : -1;
      if (nextIndex < 0) return;
      event.preventDefault();
      const nextTab = QUALITY_TABS[nextIndex]!;
      selectTab(nextTab);
      window.setTimeout(
        () => document.getElementById(`release-workspace-tab-${nextTab}`)?.focus(),
        0,
      );
    },
    [selectTab],
  );

  if (!active) return null;

  const tabLabel = (value: QualityOperationsTab, label: string) => (
    <button
      id={`release-workspace-tab-${value}`}
      type="button"
      role="tab"
      aria-selected={tab === value}
      tabIndex={tab === value ? 0 : -1}
      onClick={() => {
        selectTab(value);
      }}
      onKeyDown={(event) => handleTabKeyDown(event, value)}
    >
      {label}
    </button>
  );

  return (
    <section aria-label="Knowledge Base Releases workspace">
      <div role="tablist" aria-label="Releases workspace views">
        <Tabs
          className="knowledge-base-release-workspace-tabs"
          value={tab}
          theme="normal"
          onChange={(value) => selectTab(String(value) as QualityOperationsTab)}
        >
          <Tabs.TabPanel
            value="release-center"
            label={tabLabel("release-center", "Release Center")}
          >
            <KnowledgeBaseReleaseCenter
              active={active}
              scope={scope}
              datasetName={datasetName}
              workspaceName={workspaceName}
              readOnly={readOnly}
              scopeVerified={scopeVerified}
              api={releaseApi}
              qualityApi={qualityApi}
            />
          </Tabs.TabPanel>
          <Tabs.TabPanel
            value="quality-operations"
            label={tabLabel("quality-operations", "Quality Operations")}
          >
            <QualityOperationsPanel
              scopeVerified={scopeVerified}
              operations={operations}
              datasetName={datasetName}
              workspaceName={workspaceName}
              readOnly={readOnly}
              onOpenAuthority={handleOpenAuthority}
              onQueueRecertification={onQueueRecertification}
              onAcknowledgeRequest={(alert) => setMutationDialog({ mode: "acknowledge", alert })}
              selectedHorizon={selectedHorizon}
              onHorizonChange={handleHorizonChange}
            />
          </Tabs.TabPanel>
        </Tabs>
      </div>
      <QualityOperationsDetailDrawer
        visible={selectedAuthority !== null}
        authority={selectedAuthority}
        datasetName={datasetName}
        workspaceName={workspaceName}
        alert={selectedAlert}
        job={selectedJob}
        observations={[]}
        auditFacts={[]}
        timelineStatus={operations.timeline.status}
        timelineError={operations.timeline.error?.message ?? null}
        error={qualityOperationsError(operations)}
        readOnly={readOnly || !scopeVerified}
        focusEntry={focusEntryRef.current}
        onClose={() => setSelectedAuthority(null)}
        onAcknowledgeAlert={
          !readOnly && scopeVerified
            ? (alert) => setMutationDialog({ mode: "acknowledge", alert })
            : undefined
        }
        onResolveAlert={
          !readOnly && scopeVerified
            ? (alert) => setMutationDialog({ mode: "resolve", alert })
            : undefined
        }
        onSuppressAlert={
          !readOnly && scopeVerified
            ? (alert) => setMutationDialog({ mode: "suppress", alert })
            : undefined
        }
        onQueueRecertification={
          !readOnly && scopeVerified && onQueueRecertification
            ? (authority) => {
                void onQueueRecertification(authority, {
                  idempotencyKey: createOperationsIdempotencyKey(),
                });
              }
            : undefined
        }
        onCancelRecertification={
          !readOnly && scopeVerified
            ? (job) => setMutationDialog({ mode: "cancel", job })
            : undefined
        }
      />
      <QualityOperationsMutationDialogs
        visible={mutationDialog !== null}
        mode={mutationDialog?.mode ?? "acknowledge"}
        resourceLabel={
          mutationDialog?.alert
            ? `${mutationDialog.alert.channel_id} / ${mutationDialog.alert.release_id}`
            : mutationDialog?.job
              ? `${mutationDialog.job.channel_id} / ${mutationDialog.job.release_id}`
              : null
        }
        revision={mutationDialog?.alert?.revision ?? null}
        status={mutationDialog?.alert?.status ?? mutationDialog?.job?.status ?? null}
        readOnly={readOnly || !scopeVerified}
        loading={operations.mutation.status === "saving"}
        error={operations.mutation.error?.message ?? null}
        onClose={() => setMutationDialog(null)}
        onAcknowledge={
          mutationDialog?.mode === "acknowledge" && mutationDialog.alert
            ? async (payload) => {
                const outcome = await operations.mutation.acknowledgeAlert(
                  mutationDialog.alert!.id,
                  {
                    expectedRevision: payload.expectedRevision,
                    reason: payload.reason,
                    comment: payload.comment,
                  },
                  { idempotencyKey: createOperationsIdempotencyKey() },
                );
                if (outcome) {
                  setMutationDialog(null);
                  await operations.load.reload();
                }
              }
            : undefined
        }
        onResolve={
          mutationDialog?.mode === "resolve" && mutationDialog.alert
            ? async (payload) => {
                const outcome = await operations.mutation.resolveAlert(
                  mutationDialog.alert!.id,
                  {
                    expectedRevision: payload.expectedRevision,
                    reason: payload.reason,
                    comment: payload.comment,
                  },
                  { idempotencyKey: createOperationsIdempotencyKey() },
                );
                if (outcome) {
                  setMutationDialog(null);
                  await operations.load.reload();
                }
              }
            : undefined
        }
        onSuppress={
          mutationDialog?.mode === "suppress" && mutationDialog.alert
            ? async (payload) => {
                const outcome = await operations.mutation.suppressAlert(
                  mutationDialog.alert!.id,
                  {
                    expectedRevision: payload.expectedRevision,
                    suppressedUntil: payload.suppressedUntil,
                    reason: payload.reason,
                    comment: payload.comment,
                  },
                  { idempotencyKey: createOperationsIdempotencyKey() },
                );
                if (outcome) {
                  setMutationDialog(null);
                  await operations.load.reload();
                }
              }
            : undefined
        }
        onCancel={
          mutationDialog?.mode === "cancel" && mutationDialog.job
            ? async (payload) => {
                const outcome = await operations.mutation.cancelRecertification(
                  mutationDialog.job!.id,
                  {
                    expectedStatus: mutationDialog.job!.status,
                    reason: payload.reason,
                  },
                  { idempotencyKey: createOperationsIdempotencyKey() },
                );
                if (outcome) {
                  setMutationDialog(null);
                  await operations.load.reload();
                }
              }
            : undefined
        }
      />
    </section>
  );
}
