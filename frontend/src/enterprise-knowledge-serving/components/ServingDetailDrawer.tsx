import { useEffect, useState, type RefObject } from "react";
import { Button, Drawer, Tabs, Tag } from "tdesign-react";
import {
  ArrowRightIcon,
  CheckCircleIcon,
  HistoryIcon,
  LinkIcon,
  LockOnIcon,
} from "tdesign-icons-react";
import type {
  ServingHandoff,
  ServingPolicyRevision,
  ServingSnapshot,
  ServingSnapshotDetail,
  ServingStageFact,
} from "../model/servingModel";
import type { ServingDetailValue, ServingLoadStatus } from "../hooks/useEnterpriseKnowledgeServing";
import { dateLabel, numberLabel, shortDigest, shortId, StageTag, StateTag } from "./servingUi";

export type ServingDetailTab = "overview" | "evidence" | "pipeline" | "policy" | "history";
const tabs: Array<{ value: ServingDetailTab; label: string }> = [
  { value: "overview", label: "Overview" },
  { value: "evidence", label: "Evidence" },
  { value: "pipeline", label: "Pipeline" },
  { value: "policy", label: "Serving Policy" },
  { value: "history", label: "History" },
];
function DetailTabTrigger({
  item,
  active,
  onSelect,
}: {
  item: { value: ServingDetailTab; label: string };
  active: boolean;
  onSelect: (value: ServingDetailTab) => void;
}) {
  return (
    <span
      role="tab"
      tabIndex={active ? 0 : -1}
      aria-selected={active}
      onClick={() => onSelect(item.value)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect(item.value);
        }
      }}
    >
      {item.label}
    </span>
  );
}
export interface ServingDetailDrawerProps {
  visible: boolean;
  datasetId: string;
  status: ServingLoadStatus;
  detail: ServingDetailValue | ServingSnapshotDetail | null;
  policy: ServingPolicyRevision | null;
  readOnly: boolean;
  error: Error | string | null;
  returnFocusRef?: RefObject<HTMLElement | null>;
  onClose: () => void;
  onHandoff?: (handoff: ServingHandoff) => void;
}
function detailEvents(detail: ServingDetailDrawerProps["detail"]) {
  return detail && "events" in detail ? detail.events : [];
}
function detailSnapshot(detail: ServingDetailDrawerProps["detail"]): ServingSnapshot | null {
  return detail?.snapshot ?? null;
}
function facts(detail: ServingDetailDrawerProps["detail"]): ServingStageFact[] {
  return detail?.stage_facts ?? [];
}
function DetailFacts({ snapshot }: { snapshot: ServingSnapshot }) {
  return (
    <dl className="knowledge-serving__detail-facts">
      <div>
        <dt>Snapshot</dt>
        <dd>
          <code>{snapshot.id}</code>
        </dd>
      </div>
      <div>
        <dt>Observation key</dt>
        <dd>
          <code>{snapshot.observation_key}</code>
        </dd>
      </div>
      <div>
        <dt>状态</dt>
        <dd>
          <StateTag state={snapshot.state} />
        </dd>
      </div>
      <div>
        <dt>Policy revision</dt>
        <dd>
          <code>{snapshot.policy_revision_id}</code>
        </dd>
      </div>
      <div>
        <dt>Serving generation</dt>
        <dd>
          <code>G{snapshot.observed_serving_generation}</code>
        </dd>
      </div>
      <div>
        <dt>Digest</dt>
        <dd>
          <code>{shortDigest(snapshot.snapshot_digest)}</code>
        </dd>
      </div>
      <div>
        <dt>As of</dt>
        <dd>
          <time dateTime={snapshot.as_of}>{dateLabel(snapshot.as_of)}</time>
        </dd>
      </div>
      <div>
        <dt>Created by</dt>
        <dd>{shortId(snapshot.created_by)}</dd>
      </div>
    </dl>
  );
}
function PipelinePanel({ stages }: { stages: ServingStageFact[] }) {
  return (
    <section className="knowledge-serving__detail-panel" aria-label="Pipeline evidence">
      <ol className="knowledge-serving__pipeline-list">
        {stages.map((stage) => (
          <li key={stage.stage_code}>
            <span className="knowledge-serving__pipeline-sequence">0{stage.sequence}</span>
            <div>
              <strong>{stage.stage_code.toUpperCase()}</strong>
              <span>
                {numberLabel(stage.item_count)} items · {numberLabel(stage.error_count)} errors ·{" "}
                {stage.lag_seconds}s lag
              </span>
            </div>
            <StageTag state={stage.state} />
          </li>
        ))}
      </ol>
    </section>
  );
}
function HistoryPanel({ detail }: { detail: ServingDetailDrawerProps["detail"] }) {
  const events = detailEvents(detail);
  return (
    <section className="knowledge-serving__detail-panel" aria-label="Serving history">
      {events.length ? (
        <ol className="knowledge-serving__history-list">
          {events.map((event) => (
            <li key={event.id}>
              <span className="knowledge-serving__history-dot">
                <HistoryIcon />
              </span>
              <div>
                <strong>{event.event_type}</strong>
                <span>
                  {dateLabel(event.occurred_at)} · #{event.sequence} · {shortId(event.actor_id)}
                </span>
                <code>{shortDigest(event.event_digest)}</code>
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <div className="knowledge-serving__empty knowledge-serving__empty--compact">
          <HistoryIcon aria-hidden="true" />
          <strong>暂无历史事件</strong>
          <span>服务可靠性事件会以不可变链记录。</span>
        </div>
      )}
    </section>
  );
}
export default function ServingDetailDrawer({
  visible,
  datasetId,
  status,
  detail,
  policy,
  readOnly,
  error,
  returnFocusRef,
  onClose,
  onHandoff,
}: ServingDetailDrawerProps) {
  const [tab, setTab] = useState<ServingDetailTab>("overview");
  useEffect(() => {
    if (!visible) setTab("overview");
  }, [visible, detail?.snapshot.id]);
  useEffect(() => {
    if (!visible && returnFocusRef?.current)
      window.setTimeout(() => returnFocusRef.current?.focus(), 0);
  }, [returnFocusRef, visible]);
  const snapshot = detailSnapshot(detail);
  const stages = facts(detail);
  const links = detail?.evidence_links ?? [];
  return (
    <Drawer
      visible={visible}
      placement="right"
      size="min(760px, 94vw)"
      destroyOnClose
      closeOnEscKeydown
      onClose={onClose}
      header={
        <div className="knowledge-serving__drawer-header">
          <div>
            <span className="knowledge-serving__eyebrow">IMMUTABLE SERVING EVIDENCE</span>
            <strong>服务快照详情</strong>
          </div>
          {snapshot ? <StateTag state={snapshot.state} /> : null}
        </div>
      }
      footer={false}
      aria-label="服务快照详情"
      className="knowledge-serving__detail-drawer"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="服务快照详情"
        className="knowledge-serving__detail-body"
      >
        <div className="knowledge-serving__drawer-context">
          <span>{snapshot ? "Snapshot " + snapshot.id : "Serving snapshot"}</span>
          <span>{datasetId}</span>
          <Tag theme={readOnly ? "default" : "primary"} variant="light-outline">
            {readOnly ? "只读" : "权威只读"}
          </Tag>
        </div>
        {status === "loading" ? (
          <div role="status" className="knowledge-serving__inline-state">
            <span className="knowledge-serving__spinner" aria-hidden="true" />
            正在读取快照证据…
          </div>
        ) : error ? (
          <div role="alert" className="knowledge-serving__state-wrap">
            <span>{String(error instanceof Error ? error.message : error)}</span>
          </div>
        ) : snapshot ? (
          <>
            <Tabs
              value={tab}
              onChange={(value) => setTab(String(value) as ServingDetailTab)}
              className="knowledge-serving__detail-tabs"
            >
              {tabs.map((item) => (
                <Tabs.TabPanel
                  key={item.value}
                  value={item.value}
                  label={
                    <DetailTabTrigger item={item} active={tab === item.value} onSelect={setTab} />
                  }
                  destroyOnHide
                >
                  {tab === item.value ? (
                    <div className="knowledge-serving__detail-tab-panel">
                      {item.value === "overview" ? (
                        <section aria-label="Overview">
                          <div className="knowledge-serving__detail-lead">
                            <div className="knowledge-serving__detail-state">
                              <StateTag state={snapshot.state} />
                              <strong>
                                {snapshot.state === "ready"
                                  ? "当前可以稳定服务"
                                  : snapshot.state === "degraded"
                                    ? "当前服务可用但存在延迟"
                                    : snapshot.state === "blocked"
                                      ? "当前服务被证据阻断"
                                      : "当前服务事实不可用"}
                              </strong>
                            </div>
                            <p>
                              这是一份由 Source、Parse、Chunk、Index 与 Serve 权威聚合出的只读快照。
                            </p>
                          </div>
                          <DetailFacts snapshot={snapshot} />
                        </section>
                      ) : item.value === "evidence" ? (
                        <section aria-label="Evidence">
                          <div className="knowledge-serving__detail-section-title">
                            <div>
                              <span className="knowledge-serving__eyebrow">
                                BOUNDED INTERNAL LINKS
                              </span>
                              <h3>关联权威证据</h3>
                            </div>
                            <span>{links.length} links</span>
                          </div>
                          {links.length ? (
                            <ul className="knowledge-serving__evidence-links">
                              {links.map((link) => (
                                <li key={link.id}>
                                  <div>
                                    <strong>{link.safe_label}</strong>
                                    <span>
                                      {link.evidence_kind} · {shortId(link.resource_id)} ·{" "}
                                      {shortDigest(link.evidence_digest)}
                                    </span>
                                  </div>
                                  <Button
                                    variant="text"
                                    size="small"
                                    icon={<LinkIcon />}
                                    aria-label={"查看 " + link.safe_label + " 权威来源"}
                                    onClick={() =>
                                      onHandoff?.({
                                        evidence_kind: link.evidence_kind,
                                        route_code: link.route_code,
                                        dataset_id: datasetId,
                                        resource_id: link.resource_id,
                                      })
                                    }
                                  >
                                    查看来源
                                  </Button>
                                </li>
                              ))}
                            </ul>
                          ) : (
                            <div className="knowledge-serving__empty knowledge-serving__empty--compact">
                              <LockOnIcon aria-hidden="true" />
                              <strong>暂无可交接证据</strong>
                              <span>当前快照未返回受 allow-list 约束的内部资源链接。</span>
                            </div>
                          )}
                        </section>
                      ) : item.value === "pipeline" ? (
                        <section aria-label="Pipeline">
                          <PipelinePanel stages={stages} />
                        </section>
                      ) : item.value === "policy" ? (
                        <section aria-label="Serving Policy">
                          <div className="knowledge-serving__policy-card">
                            <div className="knowledge-serving__policy-card-title">
                              <strong>当前 Serving Policy</strong>
                              <Tag theme="primary" variant="light-outline">
                                R{policy?.revision ?? "未返回"}
                              </Tag>
                            </div>
                            {policy ? (
                              <dl className="knowledge-serving__detail-facts">
                                <div>
                                  <dt>Source freshness</dt>
                                  <dd>{policy.max_source_staleness_seconds}s</dd>
                                </div>
                                <div>
                                  <dt>Parse lag</dt>
                                  <dd>{policy.max_parse_lag_seconds}s</dd>
                                </div>
                                <div>
                                  <dt>Index lag</dt>
                                  <dd>{policy.max_index_lag_seconds}s</dd>
                                </div>
                                <div>
                                  <dt>Failed documents</dt>
                                  <dd>{policy.max_failed_document_count}</dd>
                                </div>
                                <div>
                                  <dt>Pending index</dt>
                                  <dd>{policy.max_pending_index_count}</dd>
                                </div>
                                <div>
                                  <dt>Release required</dt>
                                  <dd>{policy.require_current_release ? "是" : "否"}</dd>
                                </div>
                                <div>
                                  <dt>Certification required</dt>
                                  <dd>{policy.require_passing_certification ? "是" : "否"}</dd>
                                </div>
                                <div>
                                  <dt>Digest</dt>
                                  <dd>
                                    <code>{shortDigest(policy.policy_digest)}</code>
                                  </dd>
                                </div>
                              </dl>
                            ) : (
                              <div className="knowledge-serving__empty knowledge-serving__empty--compact">
                                <LockOnIcon aria-hidden="true" />
                                <strong>策略事实未返回</strong>
                                <span>未发现可验证的当前策略 revision。</span>
                              </div>
                            )}
                          </div>
                        </section>
                      ) : (
                        <section aria-label="History">
                          <HistoryPanel detail={detail} />
                        </section>
                      )}
                    </div>
                  ) : null}
                </Tabs.TabPanel>
              ))}
            </Tabs>
            <div className="knowledge-serving__drawer-footer-note">
              <CheckCircleIcon aria-hidden="true" />
              Evidence is read-only · 不修改来源、索引、发布或查询状态
            </div>
          </>
        ) : (
          <div className="knowledge-serving__empty">
            <ArrowRightIcon aria-hidden="true" />
            <strong>等待服务快照</strong>
            <span>当前 Dataset 尚未返回可展示的 serving snapshot。</span>
          </div>
        )}
      </div>
    </Drawer>
  );
}
