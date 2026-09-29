/* eslint-disable @typescript-eslint/no-explicit-any -- compatibility callback types during TDesign migration */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button, Drawer, Tabs, Tag, Tooltip, Typography } from "../ui/index";
import {
  AimOutlined,
  ApartmentOutlined,
  BranchesOutlined,
  ClockCircleOutlined,
  DatabaseOutlined,
  FieldTimeOutlined,
  UnorderedListOutlined,
} from "../ui/icons";
import PageTopbar from "../components/PageTopbar";
import PageState from "../components/PageState";
import RunEventTable, { type RunEventFilters } from "../components/RunEventTable";
import RunFlowGraph from "../components/RunFlowGraph";
import RunHistorySidebar from "../components/RunHistorySidebar";
import RunKnowledgePanel from "../components/RunKnowledgePanel";
import RunOpsBanners from "../components/RunOpsBanners";
import RunTimeline from "../components/RunTimeline";
import AnswerEvidencePanel from "../answer-evidence/components/AnswerEvidencePanel";
import { routeLabel } from "../strategy/routes";
import { useRunMonitor } from "../run/RunMonitorContext";
import { parseRunLocation, runViewUrl, type RunViewTab } from "../run/runViewState";
import {
  FLOW_GROUP_LABELS,
  FLOW_TOPOLOGY_SOURCE_META,
  projectCompletedLiveRun,
  projectLivePhase,
  projectTerminalLiveRun,
  type FlowNode,
  type FlowRunProjection,
} from "../run/runProjection";
import {
  projectRunTimeline,
  projectServerFlow,
  projectServerRun,
  type RunIdleGap,
  type RunTimelineInterval,
  type RunTimelineWave,
} from "../run/serverRunProjection";
import { useRunHistory } from "../run/useRunHistory";
import type { RunExecutionSource } from "../run/runMonitorStore";
import { parsePageLocation } from "../run/appRoute";
import { commitNavigationIntent } from "../run/navigationAdapter";
import type { BackendRunEvent } from "../types/rag";
import type { RunEventsDto, RunListView, RunPersistenceStatus } from "../types/runs";

const { Text } = Typography;
const RUN_STATUS: Record<FlowRunProjection["status"], { label: string; color: string }> = {
  running: { label: "执行中", color: "processing" },
  completed: { label: "已完成", color: "success" },
  degraded: { label: "已降级完成", color: "warning" },
  failed: { label: "执行失败", color: "error" },
  cancelled: { label: "已取消", color: "default" },
};
const NODE_STATUS: Record<FlowNode["status"], { label: string; color: string }> = {
  pending: { label: "等待中", color: "default" },
  active: { label: "执行中", color: "processing" },
  completed: { label: "已完成", color: "success" },
  skipped: { label: "已跳过", color: "default" },
  degraded: { label: "已降级", color: "warning" },
  failed: { label: "失败", color: "error" },
  cancelled: { label: "已取消", color: "default" },
};
const SOURCE_LABEL: Record<RunExecutionSource, { label: string; color: string }> = {
  live_stream: { label: "实时流式", color: "blue" },
  rest_fallback: { label: "非流式回退", color: "warning" },
  demo: { label: "演示数据", color: "orange" },
};
const VIEW_LABELS: Record<RunListView, string> = {
  recent: "最近运行",
  active: "执行中",
  slow: "慢运行",
  errors: "错误",
  stuck: "卡住",
};

function formatDuration(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${Math.max(0, Math.round(ms))}ms`;
}
function useElapsed(
  startedAt: number | undefined,
  finishedAt: number | undefined,
  running: boolean,
) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running]);
  return startedAt === undefined ? 0 : Math.max(0, (finishedAt ?? now) - startedAt);
}
function eventPage(
  runId: string,
  events: readonly BackendRunEvent[],
  latestSeq: number,
  persistence: RunPersistenceStatus | null,
  terminal: boolean,
): RunEventsDto {
  const lastEvent = events[events.length - 1];
  return {
    schema_version: 1,
    run_id: runId,
    events: [...events],
    after_seq: lastEvent?.seq ?? 0,
    latest_seq: latestSeq,
    terminal,
    timed_out: false,
    history_state: "complete",
    earliest_available_seq: events[0]?.seq ?? 1,
    persistence_status: persistence ?? "pending",
    retry_after_ms: 500,
  };
}
function nodeSequences(events: readonly BackendRunEvent[]): Readonly<Record<string, string>> {
  const bounds: Record<string, [number, number]> = {};
  for (const event of events)
    if (event.node_id) {
      const found = bounds[event.node_id];
      bounds[event.node_id] = found
        ? [Math.min(found[0], event.seq), Math.max(found[1], event.seq)]
        : [event.seq, event.seq];
    }
  return Object.fromEntries(
    Object.entries(bounds).map(([nodeId, [first, last]]) => [
      nodeId,
      first === last ? `seq ${first}` : `seq ${first}–${last}`,
    ]),
  );
}

interface InspectorProps {
  node?: FlowNode;
  projection?: FlowRunProjection | null;
  follow: boolean;
  onFollow: () => void;
}
function RunInspectorContent({ node, projection, follow, onFollow }: InspectorProps) {
  if (!node)
    return (
      <PageState compact status="empty" title="选择一个节点" description="节点详情会显示在这里" />
    );
  return (
    <>
      <div className="run-inspector-head">
        <div className="run-section-sub">{FLOW_GROUP_LABELS[node.group]}</div>
        <div className="run-inspector-title">{node.label}</div>
        <div className="run-inspector-actions">
          <Tag color={NODE_STATUS[node.status].color}>{NODE_STATUS[node.status].label}</Tag>
          {!follow && projection?.status === "running" ? (
            <Button size="small" type="link" icon={<AimOutlined />} onClick={onFollow}>
              跟随当前步骤
            </Button>
          ) : null}
        </div>
      </div>
      <div className="run-inspector-body">
        <p>{node.description}</p>
        <dl className="run-inspector-facts">
          <div>
            <dt>节点 ID</dt>
            <dd className="mono">{node.id}</dd>
          </div>
          <div>
            <dt>耗时</dt>
            <dd className="tabular-nums">
              {node.durationMs === undefined ? "—" : formatDuration(node.durationMs)}
            </dd>
          </div>
          <div>
            <dt>Attempt</dt>
            <dd className="mono">{node.attempt ?? 1}</dd>
          </div>
          <div>
            <dt>状态</dt>
            <dd>{NODE_STATUS[node.status].label}</dd>
          </div>
        </dl>
        <div className="run-inspector-log-title">过程记录</div>
        {node.details.length ? (
          <ul className="run-inspector-log">
            {node.details.map((detail, index) => (
              <li key={`${index}-${detail}`}>{detail}</li>
            ))}
          </ul>
        ) : (
          <Text type="secondary">当前接口没有返回这个节点的详细记录。</Text>
        )}
      </div>
    </>
  );
}

function RunOverview({ projection, runId }: { projection: FlowRunProjection; runId: string }) {
  const topology = FLOW_TOPOLOGY_SOURCE_META[projection.topologySource];
  return (
    <div className="run-overview">
      <div className="run-overview-main">
        <Tag color={RUN_STATUS[projection.status].color}>{RUN_STATUS[projection.status].label}</Tag>
        {projection.executionSource ? (
          <Tag color={SOURCE_LABEL[projection.executionSource].color}>
            {SOURCE_LABEL[projection.executionSource].label}
          </Tag>
        ) : null}
        <Tooltip title={topology.tooltip}>
          <Tag color={topology.color}>{topology.label}</Tag>
        </Tooltip>
        <h2 className="mono">run …{runId.slice(-8)}</h2>
      </div>
      <div className="run-overview-meta">
        <span>
          <ClockCircleOutlined />
          <span className="tabular-nums">{formatDuration(projection.durationMs)}</span>
        </span>
        <span>{routeLabel(projection.route)}</span>
        <span>{projection.nodes.length} 个节点</span>
      </div>
    </div>
  );
}

export default function VisualizePage() {
  const { current } = useRunMonitor();
  const initialView = useMemo(() => parseRunLocation(window.location), []);
  const [activeTab, setActiveTab] = useState<RunViewTab>(initialView.tab);
  const [selectedNodeId, setSelectedNodeId] = useState<string | undefined>(initialView.nodeId);
  const [followActive, setFollowActive] = useState(initialView.follow);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [eventFilters, setEventFilters] = useState<RunEventFilters>({});
  const history = useRunHistory({
    liveRun: current,
    initial: { runId: initialView.runId, view: initialView.view },
  });
  const selectHistoryRun = history.selectRun;
  const historyItems = history.items;
  const historySelectedRunId = history.selectedRunId;
  const firstHistoryRunId = historyItems[0]?.run_id;
  const running = current?.status === "running";
  const elapsed = useElapsed(current?.startedAt, current?.finishedAt, running);
  const phaseElapsed = useElapsed(current?.phaseStartedAt, current?.finishedAt, running);
  const currentProjection = useMemo<FlowRunProjection | null>(() => {
    if (!current) return null;
    if (current.topologySource === "typed-events" && current.typedProjection)
      return {
        ...current.typedProjection,
        durationMs: running
          ? Math.max(current.typedProjection.durationMs, elapsed)
          : current.typedProjection.durationMs,
        executionSource: current.source,
      };
    const common = {
      query: current.query,
      elapsedMs: elapsed,
      phaseElapsedMs: phaseElapsed,
      phaseDurations: current.phaseDurations,
      route: current.route,
      chunks: current.chunks,
      retrievalAttempt: current.retrievalAttempt,
      startedAt: new Date(current.startedAt).toISOString(),
    };
    if (current.status === "completed" && current.response)
      return {
        ...projectCompletedLiveRun({
          ...common,
          response: current.response,
          source: current.source,
        }),
        id: current.id,
      };
    if (current.status === "failed" || current.status === "cancelled")
      return {
        ...projectTerminalLiveRun({
          ...common,
          phase: current.phase,
          status: current.status,
          error: current.error,
        }),
        id: current.id,
      };
    return {
      ...projectLivePhase({ ...common, phase: current.phase }),
      id: current.id,
      executionSource: current.source,
    };
  }, [current, elapsed, phaseElapsed, running]);
  const currentId = current?.id;
  const previousCurrentId = useRef<string>();
  useEffect(() => {
    if (currentId && currentId !== previousCurrentId.current) {
      previousCurrentId.current = currentId;
      if (!initialView.runId || running) selectHistoryRun(currentId);
    }
  }, [currentId, initialView.runId, running, selectHistoryRun]);
  useEffect(() => {
    if (!historySelectedRunId && firstHistoryRunId) selectHistoryRun(firstHistoryRunId);
  }, [firstHistoryRunId, historySelectedRunId, selectHistoryRun]);

  const serverFlow = useMemo<FlowRunProjection | null>(() => {
    if (activeTab !== "process" && activeTab !== "timeline") return null;
    if (
      !history.detail ||
      !history.events.length ||
      history.detail.summary.run_id !== history.selectedRunId
    )
      return null;
    try {
      return projectServerFlow(
        history.detail,
        eventPage(
          history.detail.summary.run_id,
          history.events,
          Math.max(history.detail.summary.last_seq, history.maximumContiguousSeq),
          history.persistenceStatus,
          history.detail.summary.status !== "running",
        ),
      );
    } catch {
      return null;
    }
  }, [
    activeTab,
    history.detail,
    history.events,
    history.maximumContiguousSeq,
    history.persistenceStatus,
    history.selectedRunId,
  ]);
  const retainedServerFlow = useRef<{
    runId: string;
    projection: FlowRunProjection;
  } | null>(null);
  useEffect(() => {
    if (serverFlow && history.selectedRunId) {
      retainedServerFlow.current = {
        runId: history.selectedRunId,
        projection: serverFlow,
      };
      return;
    }
    if (retainedServerFlow.current?.runId !== history.selectedRunId) {
      retainedServerFlow.current = null;
    }
  }, [history.selectedRunId, serverFlow]);
  const backgroundHistoryLoad = history.loading || history.reconnecting;
  const visibleServerFlow =
    serverFlow ??
    (backgroundHistoryLoad && retainedServerFlow.current?.runId === history.selectedRunId
      ? retainedServerFlow.current.projection
      : null);
  const timelineProjection = useMemo<{
    intervals: RunTimelineInterval[];
    waves: RunTimelineWave[];
    idleGaps: RunIdleGap[];
  } | null>(() => {
    if (activeTab !== "timeline" || !history.detail || !visibleServerFlow) return null;
    return projectRunTimeline(history.events, history.detail.summary.elapsed_ms);
  }, [activeTab, history.detail, history.events, visibleServerFlow]);
  const knowledgeProjection = useMemo(() => {
    if (
      activeTab !== "knowledge" ||
      !history.detail ||
      !history.events.length ||
      history.detail.summary.run_id !== history.selectedRunId
    )
      return null;
    try {
      return projectServerRun(
        history.detail,
        eventPage(
          history.detail.summary.run_id,
          history.events,
          Math.max(history.detail.summary.last_seq, history.maximumContiguousSeq),
          history.persistenceStatus,
          history.detail.summary.status !== "running",
        ),
      );
    } catch {
      return null;
    }
  }, [
    activeTab,
    history.detail,
    history.events,
    history.maximumContiguousSeq,
    history.persistenceStatus,
    history.selectedRunId,
  ]);
  const selectedProjection =
    currentId === history.selectedRunId ? currentProjection : visibleServerFlow;
  const hasSelectedHistoryPayload =
    history.detail?.summary.run_id === history.selectedRunId && history.events.length > 0;
  const processInitialLoading =
    history.loading && !selectedProjection && !hasSelectedHistoryPayload;
  const timelineInitialLoading =
    history.loading && !visibleServerFlow && !hasSelectedHistoryPayload;
  const activeNodeId = selectedProjection?.nodes.find((node) => node.status === "active")?.id;
  const storedNodeValid = selectedProjection?.nodes.some((node) => node.id === selectedNodeId);
  const effectiveNodeId = followActive
    ? (activeNodeId ?? selectedNodeId)
    : storedNodeValid
      ? selectedNodeId
      : (activeNodeId ?? selectedProjection?.nodes[0]?.id);
  const selectedNode = selectedProjection?.nodes.find((node) => node.id === effectiveNodeId);
  const sequences = useMemo(
    () => (activeTab === "process" ? nodeSequences(history.events) : {}),
    [activeTab, history.events],
  );
  const selectRun = useCallback(
    (runId: string) => {
      selectHistoryRun(runId);
      setSelectedNodeId(undefined);
      setFollowActive(true);
    },
    [selectHistoryRun],
  );
  const setHistoryView = history.setView;
  useEffect(() => {
    const syncFromLocation = () => {
      const direct = window.location.pathname === "/visualize";
      const hashVisualize = /^#\/?visualize(?:\?|$)/.test(window.location.hash);
      if (!direct && !hashVisualize) return;
      const next = parseRunLocation(window.location);
      setActiveTab((current) => (current === next.tab ? current : next.tab));
      setSelectedNodeId((current) => (current === next.nodeId ? current : next.nodeId));
      setFollowActive((current) => (current === next.follow ? current : next.follow));
      if ((next.runId ?? null) !== historySelectedRunId) selectHistoryRun(next.runId ?? null);
      if (next.view !== history.view) setHistoryView(next.view);
    };
    window.addEventListener("popstate", syncFromLocation);
    window.addEventListener("hashchange", syncFromLocation);
    return () => {
      window.removeEventListener("popstate", syncFromLocation);
      window.removeEventListener("hashchange", syncFromLocation);
    };
  }, [history.view, historySelectedRunId, selectHistoryRun, setHistoryView]);
  const selectNode = useCallback((nodeId: string) => {
    setSelectedNodeId(nodeId);
    setFollowActive(false);
    if (window.matchMedia("(max-width: 760px)").matches) setInspectorOpen(true);
  }, []);
  useEffect(() => {
    if (parsePageLocation(window.location) !== "visualize") return;
    const runId = history.selectedRunId ?? undefined;
    const nextUrl = runViewUrl(window.location, {
      runId,
      nodeId: effectiveNodeId,
      tab: activeTab,
      view: history.view,
      follow: followActive,
    });
    commitNavigationIntent(
      { mode: "history", url: nextUrl },
      { historyAction: "replace", dispatchPopStateAfterHistory: false },
    );
  }, [activeTab, effectiveNodeId, followActive, history.selectedRunId, history.view]);

  const processPanel = selectedProjection ? (
    <section className="run-stage" aria-label="执行流程工作区">
      <RunOverview
        projection={selectedProjection}
        runId={history.selectedRunId ?? selectedProjection.id}
      />
      <RunFlowGraph
        projection={selectedProjection}
        selectedNodeId={effectiveNodeId}
        sequenceByNode={sequences}
        onSelectNode={selectNode}
      />
    </section>
  ) : (
    <PageState
      status={processInitialLoading ? "loading" : "empty"}
      title={processInitialLoading ? "正在加载运行流程" : "拓扑尚不可用"}
      description="选择一条包含 canonical run.started 的运行记录"
    />
  );
  const timelinePanel =
    visibleServerFlow && timelineProjection ? (
      <section className="run-stage run-timeline-stage" aria-label="时间线工作区">
        <RunOverview
          projection={visibleServerFlow}
          runId={history.selectedRunId ?? visibleServerFlow.id}
        />
        <RunTimeline
          intervals={timelineProjection.intervals}
          waves={timelineProjection.waves}
          idleGaps={timelineProjection.idleGaps}
          selectedNodeId={effectiveNodeId}
          onSelectNode={selectNode}
        />
      </section>
    ) : (
      <PageState
        status={timelineInitialLoading ? "loading" : "empty"}
        title={timelineInitialLoading ? "正在加载时间线" : "时间线尚不可用"}
        description="开放区间和部分历史会在 Registry 返回事件后显示"
      />
    );
  const eventsPanel =
    history.detail && history.detail.summary.run_id === history.selectedRunId ? (
      <RunEventTable
        events={history.events}
        topology={history.detail.topology}
        filters={eventFilters}
        onFiltersChange={setEventFilters}
      />
    ) : (
      <PageState
        status={history.loading ? "loading" : "empty"}
        title={history.loading ? "正在加载事件" : "事件尚不可用"}
        description="选择一条运行记录查看脱敏事件账本"
      />
    );
  const knowledgePanel = knowledgeProjection ? (
    <RunKnowledgePanel
      knowledge={knowledgeProjection.knowledge}
      nodeRollup={knowledgeProjection.nodeRollup}
    />
  ) : (
    <PageState
      status={history.loading ? "loading" : "empty"}
      title={history.loading ? "正在加载资料关联" : "资料关联尚不可用"}
      description="选择一条包含安全 Knowledge 事实的运行记录"
    />
  );
  const tabs = [
    {
      key: "process",
      label: (
        <span>
          <ApartmentOutlined /> 执行流程
        </span>
      ),
      children: processPanel,
    },
    {
      key: "timeline",
      label: (
        <span>
          <FieldTimeOutlined /> 时间线
        </span>
      ),
      children: timelinePanel,
    },
    {
      key: "events",
      label: (
        <span>
          <UnorderedListOutlined /> 事件
        </span>
      ),
      children: eventsPanel,
    },
    {
      key: "knowledge",
      label: (
        <span>
          <DatabaseOutlined /> 资料关联
        </span>
      ),
      children: knowledgePanel,
    },
  ];
  const selectedOption =
    history.selectedRunId && !history.items.some((item) => item.run_id === history.selectedRunId)
      ? [{ value: history.selectedRunId, label: `当前 · …${history.selectedRunId.slice(-8)}` }]
      : [];
  return (
    <div className="page-slot">
      <PageTopbar
        icon={<BranchesOutlined />}
        title="回答过程"
        subtitle="按 canonical sequence ledger 检查运行、并发与节点状态"
      />
      <div className="page-shell run-page-shell">
        <div className="page-shell-inner">
          <RunOpsBanners
            capability={history.capability}
            health={history.health}
            liveTransportDesync={history.liveTransportDesync}
            eventIntegrity={history.eventIntegrity}
            persistenceStatus={history.persistenceStatus}
            reconnecting={history.reconnecting}
            expired={history.expired}
          />
          <div className="run-mobile-ledger" role="group" aria-label="移动端运行筛选">
            <label>
              <span>筛选</span>
              <select
                value={history.view}
                onChange={(event: any) => history.setView(event.currentTarget.value as RunListView)}
              >
                {Object.entries(VIEW_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>运行</span>
              <select
                value={history.selectedRunId ?? ""}
                onChange={(event: any) => selectRun(event.currentTarget.value)}
              >
                {[
                  ...selectedOption,
                  ...history.items.map((item) => ({
                    value: item.run_id,
                    label: `…${item.run_id.slice(-8)} · ${item.status} · seq ${item.last_seq}`,
                  })),
                ].map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div
            className={`run-ops-layout${selectedNode ? "" : " is-inspector-collapsed"}`}
            data-mobile-order="filters-selected-tabs-workspace-drawer"
          >
            <RunHistorySidebar
              items={history.items}
              selectedRunId={history.selectedRunId}
              liveRunId={currentId}
              view={history.view}
              loading={history.loading}
              hasMore={history.hasMore}
              retentionDays={history.retention?.days}
              sourceLabel={history.listSource ?? undefined}
              onSelect={selectRun}
              onViewChange={history.setView}
              onLoadMore={history.loadMore}
            />
            <section className="run-workspace" aria-label="运行详情工作区">
              <Tabs
                className="run-view-tabs"
                activeKey={activeTab}
                onChange={(key: any) => setActiveTab(key as RunViewTab)}
                items={tabs}
              />
            </section>
            {selectedNode ? (
              <aside className="run-inspector run-inspector-desktop" aria-label="节点详情">
                <RunInspectorContent
                  node={selectedNode}
                  projection={selectedProjection}
                  follow={followActive}
                  onFollow={() => setFollowActive(true)}
                />
              </aside>
            ) : null}
          </div>
          <section className="run-answer-evidence" aria-label="答案证据链区块">
            <AnswerEvidencePanel runId={history.selectedRunId ?? undefined} />
          </section>
          <Drawer
            className="run-inspector-drawer"
            title="节点详情"
            placement="bottom"
            height="72vh"
            open={inspectorOpen && Boolean(selectedNode)}
            onClose={() => setInspectorOpen(false)}
          >
            <RunInspectorContent
              node={selectedNode}
              projection={selectedProjection}
              follow={followActive}
              onFollow={() => setFollowActive(true)}
            />
          </Drawer>
        </div>
      </div>
    </div>
  );
}
