import { useCallback, useMemo, useState } from "react";
import { Button, Input, Select } from "tdesign-react";
import { RefreshIcon, SecuredIcon, SettingIcon } from "tdesign-icons-react";

import { Alert } from "../../ui";
import AuthorityBanner from "../../ui/enterprise/AuthorityBanner";
import type {
  ContentRecoveryController,
  LegalHold,
  RecoveryApprovalHandoff,
  RecoveryEntry,
  RecoveryEntryDetail,
} from "./contentRecoveryTypes";
import ContentRecoveryHeader from "./ContentRecoveryHeader";
import ContentRecoveryMetricStrip from "./ContentRecoveryMetricStrip";
import LegalHoldDialog from "./LegalHoldDialog";
import PurgeApprovalDialog from "./PurgeApprovalDialog";
import RecoveryDetailDrawer from "./RecoveryDetailDrawer";
import RecoveryEntryTable from "./RecoveryEntryTable";
import RecoveryLifecycleRail from "./RecoveryLifecycleRail";
import ReleaseLegalHoldDialog from "./ReleaseLegalHoldDialog";
import RestoreDocumentDialog from "./RestoreDocumentDialog";
import RetentionPolicyPanel from "./RetentionPolicyPanel";
import { RecoveryStateNotice, useFocusReturn } from "./recoveryUi";
import "../content-recovery.css";

export interface ContentRecoveryCenterProps {
  controller: ContentRecoveryController;
  capabilityReady?: boolean;
  readOnly?: boolean;
  mobile?: boolean;
  tenantLabel?: string;
  onApprovalHandoff?: RecoveryApprovalHandoff;
}

type EntryFilter = "all" | RecoveryEntry["status"];

function fallbackDetail(entry: RecoveryEntry): RecoveryEntryDetail {
  return { ...entry, events: [], holds: [], purge_requests: [] };
}

export default function ContentRecoveryCenter({
  controller,
  capabilityReady = true,
  readOnly = false,
  mobile = false,
  tenantLabel = "当前租户",
  onApprovalHandoff,
}: ContentRecoveryCenterProps) {
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<EntryFilter>("all");
  const [selectedEntry, setSelectedEntry] = useState<RecoveryEntry | null>(null);
  const [detailVisible, setDetailVisible] = useState(false);
  const [restoreEntry, setRestoreEntry] = useState<RecoveryEntry | null>(null);
  const [holdEntry, setHoldEntry] = useState<RecoveryEntry | null>(null);
  const [releaseHold, setReleaseHold] = useState<LegalHold | null>(null);
  const [purgeEntry, setPurgeEntry] = useState<RecoveryEntry | null>(null);
  const [policyVisible, setPolicyVisible] = useState(false);
  const focus = useFocusReturn();

  const summary = controller.summary.value;
  const entries = controller.entries.items;
  const filteredEntries = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    return entries.filter((entry) => {
      const matchesStatus = statusFilter === "all" || entry.status === statusFilter;
      const matchesQuery =
        !normalizedQuery ||
        [entry.document_label, entry.dataset_label, entry.document_id]
          .join(" ")
          .toLocaleLowerCase()
          .includes(normalizedQuery);
      return matchesStatus && matchesQuery;
    });
  }, [entries, query, statusFilter]);

  const detailState = useMemo(() => {
    if (!selectedEntry) return controller.detail;
    if (controller.detail.value?.id === selectedEntry.id) return controller.detail;
    return {
      status: controller.detail.status === "idle" ? "ready" : controller.detail.status,
      value: fallbackDetail(selectedEntry),
      error: controller.detail.error,
    } as typeof controller.detail;
  }, [controller, selectedEntry]);

  const closeWithFocus = useCallback(
    (setter: (value: null) => void) => {
      setter(null);
      focus.restore();
    },
    [focus],
  );

  const openDetail = useCallback(
    (entry: RecoveryEntry) => {
      focus.capture();
      setSelectedEntry(entry);
      setDetailVisible(true);
      void controller.detail.load?.(entry.id);
    },
    [controller.detail, focus],
  );

  const closeDetail = useCallback(() => {
    setDetailVisible(false);
    focus.restore();
  }, [focus]);

  const openRestore = useCallback(
    (entry: RecoveryEntry) => {
      focus.capture();
      setRestoreEntry(entry);
    },
    [focus],
  );

  const openHold = useCallback(
    (entry: RecoveryEntry) => {
      focus.capture();
      setHoldEntry(entry);
    },
    [focus],
  );

  const openPurge = useCallback(
    (entry: RecoveryEntry) => {
      focus.capture();
      setPurgeEntry(entry);
    },
    [focus],
  );

  const openPolicy = useCallback(() => {
    focus.capture();
    setPolicyVisible(true);
  }, [focus]);

  const runRestore = useCallback(
    async (entry: RecoveryEntry) => {
      if (readOnly || !controller.mutation.restore) return;
      try {
        await controller.mutation.restore(entry);
        setRestoreEntry(null);
        focus.restore();
      } catch {
        // The controller owns the safe mutation error projection; keep the dialog open.
      }
    },
    [controller.mutation, focus, readOnly],
  );

  const runHold = useCallback(
    async (
      entry: RecoveryEntry,
      input: Parameters<NonNullable<ContentRecoveryController["mutation"]["applyHold"]>>[1],
    ) => {
      if (readOnly || !controller.mutation.applyHold) return;
      try {
        await controller.mutation.applyHold(entry, input);
        setHoldEntry(null);
        focus.restore();
      } catch {
        // The controller owns the safe mutation error projection; keep the dialog open.
      }
    },
    [controller.mutation, focus, readOnly],
  );

  const runRelease = useCallback(
    async (hold: LegalHold) => {
      if (readOnly || !controller.mutation.releaseHold) return;
      try {
        await controller.mutation.releaseHold(hold);
        setReleaseHold(null);
        focus.restore();
      } catch {
        // The controller owns the safe mutation error projection; keep the dialog open.
      }
    },
    [controller.mutation, focus, readOnly],
  );

  const runPurge = useCallback(
    async (entry: RecoveryEntry) => {
      if (readOnly || !controller.mutation.requestPurge) return;
      try {
        const outcome = await controller.mutation.requestPurge(entry);
        setPurgeEntry(null);
        focus.restore();
        if (outcome?.approval_request_id && onApprovalHandoff)
          onApprovalHandoff(outcome.approval_request_id);
      } catch {
        // The controller owns the safe mutation error projection; keep the dialog open.
      }
    },
    [controller.mutation, focus, onApprovalHandoff, readOnly],
  );

  const runPolicyUpdate = useCallback(
    async (
      input: Parameters<NonNullable<ContentRecoveryController["mutation"]["updatePolicy"]>>[0],
    ) => {
      if (readOnly || !controller.mutation.updatePolicy) return;
      try {
        await controller.mutation.updatePolicy(input);
        setPolicyVisible(false);
        focus.restore();
      } catch {
        // The controller owns the safe mutation error projection; keep the panel open.
      }
    },
    [controller.mutation, focus, readOnly],
  );

  const refresh = useCallback(() => {
    void Promise.all([controller.summary.reload?.(), controller.entries.reload?.()]);
  }, [controller.entries, controller.summary]);

  const summaryStatus = controller.summary.status;
  const entryStatus = controller.entries.status;
  const authorityTone =
    !capabilityReady ||
    !controller.active ||
    summaryStatus === "unavailable" ||
    entryStatus === "unavailable"
      ? "warning"
      : summaryStatus === "partial" || entryStatus === "partial"
        ? "projection"
        : "authoritative";
  const showEntries = capabilityReady && controller.active && filteredEntries.length > 0;

  return (
    <section
      className="content-recovery"
      role="region"
      aria-labelledby="content-recovery-title"
      data-testid="content-recovery-center"
    >
      <ContentRecoveryHeader
        tenantLabel={tenantLabel}
        summary={summary}
        summaryStatus={summaryStatus}
        capabilityReady={capabilityReady && controller.active}
        readOnly={readOnly}
        refreshing={summaryStatus === "loading" || entryStatus === "loading"}
        onRefresh={refresh}
      />
      <AuthorityBanner
        title="回收权威与永久清除边界"
        tone={authorityTone}
        description={
          !capabilityReady || !controller.active
            ? "恢复能力尚未就绪，所有破坏性动作均保持禁用，不会回退到永久删除。"
            : "移入回收站、恢复、法律保留和清除审批都由 Tenant 级权威事实驱动。"
        }
        badgeLabel={
          !capabilityReady || !controller.active
            ? "能力未就绪"
            : readOnly
              ? "只读投影"
              : "0033 权威"
        }
      />
      <ContentRecoveryMetricStrip summary={summary} status={summaryStatus} />
      <RecoveryLifecycleRail summary={summary} status={summaryStatus} />

      <div className="content-recovery__workspace-toolbar">
        <div>
          <span className="content-recovery__eyebrow">RECYCLED DOCUMENT REGISTER</span>
          <h2 id="content-recovery-title">回收条目</h2>
          <p>文档正文不会复制到回收快照；当前列表只展示可验证的安全摘要。</p>
        </div>
        <div className="content-recovery__workspace-actions">
          <Button
            variant="outline"
            icon={<SettingIcon />}
            disabled={!capabilityReady || !controller.active}
            onClick={openPolicy}
          >
            保留策略
          </Button>
          {readOnly ? (
            <span className="content-recovery__read-only-copy">
              <SecuredIcon aria-hidden="true" />
              只读模式
            </span>
          ) : null}
        </div>
      </div>

      {controller.mutation.error ? (
        <div className="content-recovery__mutation-error">
          <Alert
            theme="error"
            title="操作未完成"
            message="权威服务没有接受本次变更，请保留当前窗口并重试。"
          />
        </div>
      ) : null}

      <div className="content-recovery__filters" role="search" aria-label="筛选回收条目">
        <label>
          <span>搜索文档</span>
          <Input
            type="search"
            value={query}
            placeholder="按文档、知识库或 ID 搜索"
            aria-label="搜索回收文档"
            onChange={(value) => setQuery(String(value))}
          />
        </label>
        <label>
          <span>生命周期状态</span>
          <Select
            aria-label="筛选生命周期状态"
            value={statusFilter}
            options={[
              { label: "全部状态", value: "all" },
              { label: "已回收", value: "recycled" },
              { label: "恢复中", value: "restoring" },
              { label: "清除待审批", value: "purge_requested" },
              { label: "已恢复", value: "restored" },
              { label: "处理失败", value: "failed" },
            ]}
            onChange={(value) => setStatusFilter(String(value) as EntryFilter)}
          />
        </label>
        <Button
          variant="text"
          icon={<RefreshIcon />}
          onClick={() => {
            setQuery("");
            setStatusFilter("all");
          }}
          disabled={!query && statusFilter === "all"}
        >
          清除筛选
        </Button>
      </div>

      <div className="content-recovery__list-heading">
        <div>
          <strong>{showEntries ? `${filteredEntries.length} 条可验证条目` : "回收条目清单"}</strong>
          <span>{summary?.as_of ? `权威时间 ${summary.as_of}` : "权威时间未返回"}</span>
        </div>
        <span className="content-recovery__list-boundary">
          <SecuredIcon aria-hidden="true" />
          租户范围内
        </span>
      </div>

      {!capabilityReady || !controller.active ? (
        <RecoveryStateNotice status="unavailable" resourceLabel="回收条目" />
      ) : (
        <>
          <RecoveryStateNotice
            status={entryStatus}
            invalidItemCount={controller.entries.invalidItemCount}
            hasItems={filteredEntries.length > 0}
          />
          {showEntries ? (
            <RecoveryEntryTable
              entries={filteredEntries}
              mobile={mobile}
              loading={entryStatus === "loading"}
              readOnly={readOnly}
              onOpenDetail={openDetail}
              onRestore={openRestore}
              onAddHold={openHold}
              onManageHolds={openDetail}
              onRequestPurge={openPurge}
            />
          ) : entryStatus === "ready" && entries.length > 0 && filteredEntries.length === 0 ? (
            <div className="content-recovery__filtered-empty">
              <span>没有匹配的回收条目</span>
              <Button
                variant="text"
                onClick={() => {
                  setQuery("");
                  setStatusFilter("all");
                }}
              >
                显示全部
              </Button>
            </div>
          ) : null}
        </>
      )}

      <RecoveryDetailDrawer
        visible={detailVisible}
        state={detailState}
        readOnly={readOnly}
        onClose={closeDetail}
        onRestore={openRestore}
        onAddHold={openHold}
        onReleaseHold={(hold) => {
          focus.capture();
          setReleaseHold(hold);
        }}
        onRequestPurge={openPurge}
        onApprovalHandoff={onApprovalHandoff}
        returnFocusRef={focus.returnFocusRef}
      />
      <RestoreDocumentDialog
        visible={restoreEntry !== null}
        entry={restoreEntry}
        readOnly={readOnly}
        saving={controller.mutation.status === "saving"}
        onClose={() => closeWithFocus(setRestoreEntry)}
        onSubmit={runRestore}
        returnFocusRef={focus.returnFocusRef}
      />
      <LegalHoldDialog
        visible={holdEntry !== null}
        entry={holdEntry}
        readOnly={readOnly}
        saving={controller.mutation.status === "saving"}
        onClose={() => closeWithFocus(setHoldEntry)}
        onSubmit={runHold}
        returnFocusRef={focus.returnFocusRef}
      />
      <ReleaseLegalHoldDialog
        visible={releaseHold !== null}
        hold={releaseHold}
        readOnly={readOnly}
        saving={controller.mutation.status === "saving"}
        onClose={() => closeWithFocus(setReleaseHold)}
        onSubmit={runRelease}
        returnFocusRef={focus.returnFocusRef}
      />
      <PurgeApprovalDialog
        visible={purgeEntry !== null}
        entry={purgeEntry}
        readOnly={readOnly}
        saving={controller.mutation.status === "saving"}
        onClose={() => closeWithFocus(setPurgeEntry)}
        onSubmit={runPurge}
        onApprovalHandoff={onApprovalHandoff}
        returnFocusRef={focus.returnFocusRef}
      />
      <RetentionPolicyPanel
        visible={policyVisible}
        policy={controller.retentionPolicy.value}
        readOnly={readOnly}
        saving={controller.mutation.status === "saving"}
        onClose={() => {
          setPolicyVisible(false);
          focus.restore();
        }}
        onSubmit={runPolicyUpdate}
        returnFocusRef={focus.returnFocusRef}
      />
    </section>
  );
}
