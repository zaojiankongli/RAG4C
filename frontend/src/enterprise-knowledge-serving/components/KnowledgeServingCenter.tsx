import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { Button, Input, Select, Tag } from "tdesign-react";
import { FilterIcon, RefreshIcon, SearchIcon } from "tdesign-icons-react";
import { Alert } from "../../ui";
import type {
  ServingHandoff,
  ServingPolicyRevision,
  ServingProfile,
  ServingSnapshot,
} from "../model/servingModel";
import ServingPolicyDialog from "./ServingPolicyDialog";
import GovernanceEvidenceStrip from "./GovernanceEvidenceStrip";
import ServingDetailDrawer from "./ServingDetailDrawer";
import ServingHeader from "./ServingHeader";
import ServingLifecycleRail from "./ServingLifecycleRail";
import ServingTable from "./ServingTable";
import type { KnowledgeServingController, ServingHandoffCallback } from "./types";
import { LoadState, safeErrorMessage, stateLabel, StateTag } from "./servingUi";

export interface KnowledgeServingCenterProps {
  controller: KnowledgeServingController;
  tenantLabel?: string;
  datasetName?: string;
  workspaceName?: string;
  datasetId?: string;
  mobile?: boolean;
  onHandoff?: ServingHandoffCallback;
}
const stateOptions = [
  { label: "全部状态", value: "all" },
  { label: "就绪", value: "ready" },
  { label: "降级", value: "degraded" },
  { label: "阻断", value: "blocked" },
  { label: "不可用", value: "unavailable" },
];
const sourceOptions = [
  { label: "全部来源", value: "all" },
  { label: "存在延迟", value: "stale" },
  { label: "来源正常", value: "fresh" },
];
function currentProfile(
  value: KnowledgeServingController["profile"]["value"],
): ServingProfile | null {
  return value?.profile ?? null;
}
function currentPolicy(
  value: KnowledgeServingController["profile"]["value"],
): ServingPolicyRevision | null {
  return value?.current_policy ?? null;
}
export default function KnowledgeServingCenter({
  controller,
  tenantLabel = "当前租户",
  datasetName,
  workspaceName,
  datasetId,
  mobile = false,
  onHandoff,
}: KnowledgeServingCenterProps) {
  const [selectedState, setSelectedState] = useState("all");
  const [selectedSource, setSelectedSource] = useState("all");
  const [keyword, setKeyword] = useState("");
  const [selectedSnapshot, setSelectedSnapshot] = useState<ServingSnapshot | null>(null);
  const [detailVisible, setDetailVisible] = useState(false);
  const [policyVisible, setPolicyVisible] = useState(false);
  const [preview, setPreview] = useState<import("../model/servingModel").ServingPreview | null>(
    null,
  );
  const detailTriggerRef = useRef<HTMLElement | null>(null);
  const summary = controller.summary.value;
  const profile = currentProfile(controller.profile.value);
  const policy = currentPolicy(controller.profile.value);
  const effectiveDatasetId = datasetId ?? summary?.dataset_id ?? profile?.dataset_id ?? "";
  const authorityContextKey = [
    controller.active ? "active" : "inactive",
    controller.readOnly ? "readonly" : "editable",
    datasetId ?? "",
    summary?.tenant_id ?? profile?.tenant_id ?? "",
    summary?.dataset_id ?? profile?.dataset_id ?? "",
  ].join("\u0000");
  const previousAuthorityContextKeyRef = useRef(authorityContextKey);
  const authorityContextChanged = previousAuthorityContextKeyRef.current !== authorityContextKey;
  const authorityCleared =
    controller.summary.value === null &&
    controller.profile.value === null &&
    controller.snapshots.items.length === 0 &&
    controller.detail.value === null &&
    controller.activity.items.length === 0;
  const contextResetting = authorityContextChanged || !controller.active || authorityCleared;
  useLayoutEffect(() => {
    if (contextResetting) {
      setSelectedState("all");
      setSelectedSource("all");
      setKeyword("");
      setSelectedSnapshot(null);
      setDetailVisible(false);
      setPolicyVisible(false);
      setPreview(null);
      detailTriggerRef.current = null;
    }
    previousAuthorityContextKeyRef.current = authorityContextKey;
  }, [authorityContextKey, contextResetting]);
  const visibleSelectedState = contextResetting ? "all" : selectedState;
  const visibleSelectedSource = contextResetting ? "all" : selectedSource;
  const visibleKeyword = contextResetting ? "" : keyword;
  const visibleSnapshots = contextResetting ? [] : controller.snapshots.items;
  const filteredSnapshots = useMemo(
    () =>
      visibleSnapshots.filter(
        (snapshot) =>
          (visibleSelectedState === "all" || snapshot.state === visibleSelectedState) &&
          (visibleSelectedSource === "all" ||
            (visibleSelectedSource === "stale"
              ? snapshot.stale_source_count > 0
              : snapshot.stale_source_count === 0)) &&
          (!visibleKeyword.trim() ||
            [snapshot.id, snapshot.observation_key, snapshot.policy_revision_id].some((item) =>
              item.toLowerCase().includes(visibleKeyword.trim().toLowerCase()),
            )),
      ),
    [visibleKeyword, visibleSelectedSource, visibleSelectedState, visibleSnapshots],
  );
  const openDetail = (snapshot: ServingSnapshot, trigger: HTMLElement) => {
    setSelectedSnapshot(snapshot);
    detailTriggerRef.current = trigger;
    setDetailVisible(true);
    void controller.detail.load(snapshot.id);
  };
  const closeDetail = () => {
    setDetailVisible(false);
    window.setTimeout(() => detailTriggerRef.current?.focus(), 0);
  };
  const openPolicy = () => {
    if (!controller.readOnly) {
      setPreview(null);
      setPolicyVisible(true);
    }
  };
  const closePolicy = () => {
    setPolicyVisible(false);
    setPreview(null);
  };
  const policyInput = (input: {
    expectedRevision: number;
    expectedPolicyDigest: string;
    policy: import("../api/servingApi").ServingPolicyInput;
    reason: string;
  }) => controller.mutation.createPolicyRevision(input);
  const previewPolicy = async (input: {
    expectedRevision: number;
    expectedPolicyDigest: string;
    policy: import("../api/servingApi").ServingPolicyInput;
    reason: string;
  }) => {
    const result = await controller.mutation.previewPolicy(input);
    setPreview(result);
    return result;
  };
  if (!controller.active)
    return (
      <section
        className="knowledge-serving knowledge-serving--inactive"
        aria-label="知识服务可靠性中心"
      >
        <Alert
          theme="info"
          title="知识服务可靠性中心未启用"
          message="当前资源上下文未启用 serving reliability capability。"
        />
      </section>
    );
  return (
    <section className="knowledge-serving" aria-label="知识服务可靠性中心">
      <ServingHeader
        summary={summary}
        profile={profile}
        tenantLabel={tenantLabel}
        datasetName={datasetName}
        workspaceName={workspaceName}
        readOnly={controller.readOnly}
        onRefresh={() => void controller.summary.reload()}
        onOpenPolicy={openPolicy}
      />
      {controller.readOnly ? (
        <div className="knowledge-serving__readonly-banner" role="status">
          <Tag theme="default" variant="light-outline">
            只读事实
          </Tag>
          <span>当前身份只能查看权威快照、证据和历史，策略修改已禁用。</span>
        </div>
      ) : null}
      {controller.summary.status === "unavailable" || controller.summary.status === "error" ? (
        <LoadState
          status={controller.summary.status}
          title="知识服务可靠性能力不可用"
          description={safeErrorMessage(controller.summary.error) ?? "摘要权威未返回。"}
          onRetry={() => void controller.summary.reload()}
        />
      ) : null}
      {summary ? (
        <div className="knowledge-serving__state-banner" data-state={summary.state}>
          <div>
            <span className="knowledge-serving__eyebrow">CURRENT SERVING DECISION</span>
            <strong>{stateLabel(summary.state)}</strong>
            <span>
              {summary.state === "ready"
                ? "五个阶段均有可验证的最新证据。"
                : summary.state === "degraded"
                  ? "服务仍可用，但存在延迟或不完整投影。"
                  : summary.state === "blocked"
                    ? "至少一个阶段明确阻断，需先查看证据。"
                    : "当前无法证明该知识库可以可靠服务。"}
            </span>
          </div>
          <StateTag state={summary.state} />
        </div>
      ) : null}
      <GovernanceEvidenceStrip summary={summary} />
      <ServingLifecycleRail stages={summary?.stage_facts ?? []} />
      <section
        className="knowledge-serving__workspace"
        aria-labelledby="serving-snapshot-list-title"
      >
        <div className="knowledge-serving__workspace-heading">
          <div>
            <span className="knowledge-serving__eyebrow">SNAPSHOT OPERATIONS</span>
            <h2 id="serving-snapshot-list-title">服务快照与处理证据</h2>
            <p>每一行都是一次不可变观察，不会触发来源同步、索引或查询。</p>
          </div>
          <div className="knowledge-serving__workspace-actions">
            <span className="knowledge-serving__record-count">
              {contextResetting
                ? 0
                : (controller.snapshots.count ?? controller.snapshots.items.length)}{" "}
              snapshots
            </span>
            <Button
              variant="outline"
              size="small"
              icon={<RefreshIcon />}
              onClick={() => void controller.snapshots.reload()}
            >
              刷新列表
            </Button>
          </div>
        </div>
        <div className="knowledge-serving__filters" role="search" aria-label="服务快照筛选">
          <div className="knowledge-serving__filter-field">
            <span>状态</span>
            <Select
              value={visibleSelectedState}
              options={stateOptions}
              onChange={(value) => setSelectedState(String(value))}
            />
          </div>
          <div className="knowledge-serving__filter-field">
            <span>来源健康度</span>
            <Select
              value={visibleSelectedSource}
              options={sourceOptions}
              onChange={(value) => setSelectedSource(String(value))}
            />
          </div>
          <div className="knowledge-serving__filter-field knowledge-serving__filter-field--keyword">
            <span>Snapshot / Observation / Policy</span>
            <Input
              value={visibleKeyword}
              placeholder="搜索权威 ID"
              prefixIcon={<SearchIcon />}
              clearable
              onChange={(value) => setKeyword(String(value))}
            />
          </div>
          <Button
            variant="text"
            icon={<FilterIcon />}
            onClick={() => {
              setSelectedState("all");
              setSelectedSource("all");
              setKeyword("");
            }}
          >
            清除筛选
          </Button>
        </div>
        {contextResetting ? (
          <LoadState status="loading" title="正在切换知识库权威…" />
        ) : controller.snapshots.status === "unavailable" ||
          controller.snapshots.status === "error" ? (
          <LoadState
            status={controller.snapshots.status}
            title="服务快照不可用"
            onRetry={() => void controller.snapshots.reload()}
          />
        ) : controller.snapshots.status === "partial" && controller.snapshots.items.length === 0 ? (
          <LoadState status="partial" />
        ) : controller.snapshots.status === "empty" ||
          (controller.snapshots.status === "ready" && filteredSnapshots.length === 0) ? (
          <LoadState
            status="empty"
            title={controller.snapshots.status === "empty" ? "暂无服务快照" : "没有匹配的服务快照"}
            description={
              controller.snapshots.status === "empty"
                ? "当前 Dataset 尚未产生可展示的 serving snapshot。"
                : "请调整筛选条件后重试。"
            }
          />
        ) : (
          <ServingTable
            snapshots={filteredSnapshots}
            mobile={mobile}
            loading={controller.snapshots.status === "loading"}
            onOpenDetail={openDetail}
          />
        )}
      </section>
      <ServingDetailDrawer
        visible={detailVisible && !contextResetting && selectedSnapshot !== null}
        datasetId={effectiveDatasetId}
        status={controller.detail.status}
        detail={controller.detail.value}
        policy={policy}
        readOnly={controller.readOnly}
        error={controller.detail.error}
        returnFocusRef={detailTriggerRef}
        onClose={closeDetail}
        onHandoff={(handoff: ServingHandoff) => onHandoff?.(handoff)}
      />
      <ServingPolicyDialog
        visible={policyVisible && !contextResetting}
        profile={profile}
        policy={policy}
        readOnly={controller.readOnly}
        saving={controller.mutation.status === "saving"}
        preview={preview}
        error={controller.mutation.error}
        onClose={closePolicy}
        onPreview={previewPolicy}
        onSubmit={async (input) => {
          const result = await policyInput(input);
          if (result?.state === "applied" || result?.state === "replayed") closePolicy();
          return result;
        }}
      />
    </section>
  );
}
