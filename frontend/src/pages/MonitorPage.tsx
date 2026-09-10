import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Col, Row, Select, Tag, Typography } from "tdesign-react";
import {
  ChartBarIcon,
  ChartLineIcon,
  CertificateIcon,
  CheckCircleIcon,
  DataBaseIcon,
  PauseCircleIcon,
  PlayCircleIcon,
  RefreshIcon,
  StopCircleIcon,
  ThunderIcon,
  TimeIcon,
  UsergroupIcon,
} from "tdesign-icons-react";
import PageTopbar from "../components/PageTopbar";
import PageState from "../components/PageState";
import StatCard from "../components/StatCard";
import EChart from "../charts/EChart";
import type { EChartsOption } from "../charts/EChart";
import { CHART_FONT, useChartPalette } from "../charts/chartTheme";
import { fetchMetrics, fetchMetricsHistory } from "../api/client";
import { fetchRunHealth, fetchRuns } from "../api/runs";
import type { MetricsHistoryItem } from "../api/client";
import { useConnection } from "../context/ConnectionContext";
import {
  ATTENTION_REQUESTS,
  abstentionPercent,
  cacheHitPercent,
  classifyRunsHealth,
  loadMonitorAttention,
  projectProtectionSignals,
  type MonitorAttentionGroups,
  type MonitorRunsCapability,
} from "../monitor/monitorProjection";
import { FONT_SIZE } from "../theme/tokens";
import type { MetricStat, MetricsSnapshot, RecentQuery } from "../types/rag";
import DocumentIngestMonitor from "../monitor/DocumentIngestMonitor";
import MonitorMetricsTable, { LegacyRecentQueriesTable } from "../monitor/MonitorMetricsTable";

const { Text } = Typography;

/** 熔断器名 -> 中文（名称来自 rag.py 里创建熔断器时传入的标识） */
const CIRCUIT_LABELS: Record<string, string> = {
  retrieval: "资料检索",
  "llm.generation": "答案生成",
};

const CIRCUIT_STATE_LABELS: Record<string, string> = {
  closed: "正常",
  open: "熔断中",
  half_open: "试探恢复",
};

/** 统一时间展示：zh-CN 24 小时制日期 + 时分秒（无法解析的输入原样回退）
 *
 * 必须**显式列出** year/month/day/hour/minute/second。`Intl.DateTimeFormat`
 * 在没有任何组件选项时只输出日期，`hour12: false` 会被静默忽略——原来这里
 * 就只写了 `{ hour12: false }`，于是函数名叫 formatTime 却从不输出时间，
 * 五个调用点全都只显示 “2026/8/22”。最刺眼的是趋势图 X 轴：同一天的采样点
 * 渲染出一整排一模一样的日期，一个刻度信息量为零的坐标轴。
 */
const formatTime = (ts: string) => {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return new Intl.DateTimeFormat("zh-CN", {
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(d);
};

/** 图表 X 轴专用：只到时分。
 *
 * 轴标签空间有限，塞完整日期会互相挤掉；而趋势图看的是「这段时间内的起伏」，
 * 哪一天由卡片标题交代即可，轴上只需要能区分先后。跨天的点由 tooltip
 * 给出完整时间戳，不会因为省略日期而产生歧义。
 */
const formatClock = (ts: string) => {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return new Intl.DateTimeFormat("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
  }).format(d);
};

/** 阶段耗时分组柱状图（P50 / P95 / P99），ECharts 渲染 */
function StageBars({ snapshot }: { snapshot: MetricsSnapshot }) {
  const pal = useChartPalette();
  // 读 span.* —— 这些键由 core/observability 的 span observer 在每次问答时
  // 自动写入，是真实存在的数据。此前这里读的是 retrieval.total /
  // generate.llm / verify.total，那三个键全仓从未被写入过（只存在于
  // 注释与演示数据里），所以对着真实后端永远是空图。
  const stages = [
    "span.search",
    "span.rerank",
    "span.generate",
    "span.verify",
    "span.verify_l2",
    "span.verify_l3",
  ];
  const labels: Record<string, string> = {
    "span.search": "混合检索",
    "span.rerank": "结果精排",
    "span.generate": "答案生成",
    "span.verify": "引用验证总计",
    "span.verify_l2": "L2 文本校验",
    "span.verify_l3": "L3 蕴含判定",
  };
  const data = stages
    .map((key) => ({ key, label: labels[key], stat: snapshot.metrics[key] }))
    .filter((d) => d.stat);

  if (data.length === 0) {
    return <Text theme="secondary">暂时没有处理记录。先完成一次提问后，这里会显示各步骤用时。</Text>;
  }

  const seriesColors = {
    p50: pal.primary,
    p95: pal.graph,
    p99: pal.warning,
  };

  const makeSeries = (key: "p50" | "p95" | "p99") => ({
    name: key.toUpperCase(),
    type: "bar" as const,
    barMaxWidth: 34,
    data: data.map((d) => d.stat![key]),
    itemStyle: { color: seriesColors[key], borderRadius: [4, 4, 0, 0] },
    label: { show: true, position: "top" as const, fontSize: CHART_FONT.dataLabel, color: pal.axis },
  });

  const option: EChartsOption = {
    backgroundColor: "transparent",
    grid: { left: 8, right: 8, top: 34, bottom: 4, containLabel: true },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      valueFormatter: (value: unknown) => String(value) + " ms",
    },
    legend: {
      data: ["P50", "P95", "P99"],
      top: 0,
      icon: "roundRect",
      itemWidth: 10,
      itemHeight: 10,
      textStyle: { fontSize: CHART_FONT.axis, color: pal.axis },
    },
    xAxis: {
      type: "category",
      data: data.map((d) => d.label),
      axisLine: { lineStyle: { color: pal.splitLine } },
      axisTick: { show: false },
      axisLabel: { color: pal.axis, fontSize: CHART_FONT.caption },
    },
    yAxis: {
      type: "value",
      name: "毫秒",
      nameTextStyle: { color: pal.axis, fontSize: CHART_FONT.axis },
      axisLabel: { color: pal.axis, fontSize: CHART_FONT.axis },
      splitLine: { lineStyle: { color: pal.splitLine, type: "dashed" } },
    },
    series: [makeSeries("p50"), makeSeries("p95"), makeSeries("p99")],
  };

  return (
    <EChart
      option={option}
      height={260}
      ariaLabel={`阶段耗时进程累计分位数柱状图，包含 ${data.map((entry) => entry.label).join("、")}`}
    />
  );
}

/** 指标历史趋势（预置视图），ECharts line。
 *
 * 全部改读真实存在的键：`query.total` 由 HTTP 边界打点（流式与非流式
 * 两条路径都覆盖），`span.*` 由 span observer 自动写入。
 *
 * `cumulative` 标记计数类指标：后端快照里的 count 是**进程累计值**，
 * 直接画出来是一条只涨不跌的阶梯（横平代表没流量，而非流量平稳）。
 * 对这类指标改画相邻快照的差值，才是真正的「每段时间发生了多少次」。
 */
const TREND_VIEWS = [
  {
    key: "invoke",
    label: "完成的实际计算（每次采样新增）",
    metric: "query.total",
    stat: "count" as const,
    cumulative: true,
  },
  {
    key: "latency",
    label: "问答总耗时（进程累计 P95）",
    metric: "query.total",
    stat: "p95" as const,
    cumulative: false,
  },
  {
    key: "retr",
    label: "混合检索耗时（进程累计 P95）",
    metric: "span.search",
    stat: "p95" as const,
    cumulative: false,
  },
  {
    key: "gen",
    label: "答案生成耗时（进程累计 P95）",
    metric: "span.generate",
    stat: "p95" as const,
    cumulative: false,
  },
  {
    key: "ver",
    label: "引用验证耗时（进程累计 P95）",
    metric: "span.verify",
    stat: "p95" as const,
    cumulative: false,
  },
  {
    key: "err",
    label: "记录到的计算错误（每次采样新增）",
    metric: "query.total.errors",
    stat: "count" as const,
    cumulative: true,
  },
];

function labelTrendSelectInput(node: HTMLDivElement | null): void {
  // TDesign Select 1.18 does not forward aria-label to its native readonly input.
  // Label the actual focus target so screen readers announce the control.
  node?.querySelector("input")?.setAttribute("aria-label", "选择趋势指标");
}

function HistoryTrend({ items }: { items: MetricsHistoryItem[] }) {
  const pal = useChartPalette();
  const [viewKey, setViewKey] = useState("invoke");
  const view = TREND_VIEWS.find((viewOption) => viewOption.key === viewKey) ?? TREND_VIEWS[0];

  const points = items
    .map((it) => {
      const stat = it.metrics[view.metric];
      if (!stat) return null;
      const value = stat[view.stat];
      if (value === undefined) return null;
      // 存原始时间戳，格式化推迟到渲染点：X 轴要短（时分），tooltip 要完整
      // （带日期）。若在这里就格式化成字符串，tooltip 想补回日期就没处取了。
      return { ts: it.ts, value };
    })
    .filter((p): p is { ts: string; value: number } => p !== null);

  // 累计计数器改画相邻差值：后端快照存的是进程累计 count，
  // 直接画会得到一条永远上升的阶梯，看不出流量的起伏。
  // 进程重启会让计数归零，此时差值为负，钳到 0 而不是画出一个负尖峰。
  const series = view.cumulative
    ? points.slice(1).map((p, i) => ({
        ts: p.ts,
        value: Math.max(0, p.value - points[i].value),
      }))
    : points;

  if (series.length < 2) {
    return (
      <PageState
        compact
        status="empty"
        title="趋势还在积累中"
        description="历史记录还不够。系统运行约 1 分钟后会逐步积累趋势数据。"
      />
    );
  }

  const option: EChartsOption = {
    backgroundColor: "transparent",
    grid: { left: 8, right: 16, top: 12, bottom: 4, containLabel: true },
    tooltip: {
      trigger: "axis",
      // 轴上只有时分，tooltip 补回完整时间戳——跨天时不至于分不清是哪天。
      formatter: (params) => {
        const arr = Array.isArray(params) ? params : [params];
        const first = arr[0] as { dataIndex?: number; value?: unknown };
        const idx = first?.dataIndex ?? 0;
        const raw = series[idx]?.ts;
        const head = raw ? formatTime(raw) : "";
        const v = first?.value;
        const text = String(v);
        return `${head}<br/>${view.label}：${text}`;
      },
    },
    xAxis: {
      type: "category",
      data: series.map((p) => formatClock(p.ts)),
      boundaryGap: false,
      axisLine: { lineStyle: { color: pal.splitLine } },
      axisTick: { show: false },
      axisLabel: { color: pal.axis, fontSize: CHART_FONT.axis },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: pal.axis, fontSize: CHART_FONT.axis },
      splitLine: { lineStyle: { color: pal.splitLine, type: "dashed" } },
    },
    series: [
      {
        name: view.label,
        type: "line",
        smooth: true,
        symbol: "circle",
        symbolSize: 4,
        data: series.map((p) => p.value),
        lineStyle: { width: 2, color: pal.primary },
        itemStyle: { color: pal.primary },
        areaStyle: {
          opacity: 0.12,
          color: pal.primary,
        },
      },
    ],
  };

  return (
    <>
      <div className="monitor-trend-select" ref={labelTrendSelectInput}>
        <Select
          size="small"
          value={viewKey}
          onChange={(value) => setViewKey(String(value))}
          options={TREND_VIEWS.map((viewOption) => ({ value: viewOption.key, label: viewOption.label }))}
        />
      </div>
      <EChart
        option={option}
        height={220}
        ariaLabel={`监控历史图：${view.label}`}
      />
      <Text theme="secondary" style={{ fontSize: FONT_SIZE.xs, display: "block", marginTop: 4 }}>
        计数类视图展示相邻采样新增次数；P50/P95/P99 是进程启动以来的累计分位数快照，并非每个采样窗口的独立分位数。
      </Text>
    </>
  );
}

const EMPTY_ATTENTION: MonitorAttentionGroups = { active: [], stuck: [], slow: [], errors: [], cancelled: [] };
const ATTENTION_LABELS = { active: "执行中", stuck: "卡住", slow: "慢运行", errors: "错误", cancelled: "已取消" } as const;
const RUN_STATUS_LABELS: Record<string, string> = { running: "执行中", completed: "已完成", failed: "失败", interrupted: "已中断", cancelled: "已取消" };
function formatAttentionDuration(ms: number): string {
  return ms >= 1000
    ? `${(ms / 1000).toFixed(ms >= 10000 ? 1 : 2)}s`
    : `${Math.max(0, Math.round(ms))}ms`;
}
export function MonitorAttentionBoard({ groups, capability, reconnecting, loading, onRetry }: { groups: MonitorAttentionGroups; capability: MonitorRunsCapability; reconnecting: boolean; loading: boolean; onRetry?: () => void }) {
  const title = <h2 id="monitor-attention-heading" className="monitor-attention-heading">需要关注的运行</h2>;
  const hasItems = Object.values(groups).some((items) => items.length > 0);
  const retry = onRetry ? <Button tag="button" size="small" variant="outline" loading={loading} aria-label="重试运行状态" onClick={onRetry}><RefreshIcon className={loading ? "tdesign-icon-spin" : undefined} /> 重试</Button> : undefined;
  if (capability === "loading" && !hasItems)
    return <section role="region" aria-labelledby="monitor-attention-heading"><Card size="small" title={title} className="monitor-attention-card"><span role="status"><Text>正在加载运行状态…</Text></span></Card></section>;
  if (capability === "legacy")
    return <Alert {...({ role: "alert", "aria-label": "Runs API 未提供" } as Record<string, unknown>)} className="monitor-attention-state" theme="info" title="Runs API 未提供，正在使用 legacy 最近提问记录" message={reconnecting ? "legacy 数据已过期，正在重新连接" : loading ? "正在刷新 legacy 数据" : undefined} operation={reconnecting ? retry : undefined} />;
  if (capability === "disabled")
    return <Alert {...({ role: "status", "aria-label": "运行 Registry 已禁用" } as Record<string, unknown>)} className="monitor-attention-state" theme="warning" title="运行 Registry 已禁用" message="指标仍可查看，但当前后端不会提供运行级排障深链。" />;
  if (capability === "unauthorized")
    return <Alert {...({ role: "alert", "aria-label": "没有权限查看运行 Registry" } as Record<string, unknown>)} className="monitor-attention-state" theme="error" title="没有权限查看运行 Registry" />;
  if (capability === "unavailable" && !hasItems)
    return <Alert {...({ role: "alert", "aria-label": "运行 Registry 暂时不可用" } as Record<string, unknown>)} className="monitor-attention-state" theme="error" title="运行 Registry 暂时不可用" message="未注入演示运行；请恢复连接后重试。" operation={retry} />;
  const refreshText = reconnecting ? "数据已过期，正在重新连接" : loading ? "正在刷新，保留现有运行" : "按 run_id 深链";
  return <section role="region" aria-labelledby="monitor-attention-heading"><Card size="small" title={title} className="monitor-attention-card" actions={<span role={reconnecting || loading ? "status" : undefined}><Text theme="secondary">{refreshText}</Text></span>}>
    <div className="monitor-attention-groups">{(Object.keys(ATTENTION_LABELS) as Array<keyof typeof ATTENTION_LABELS>).map((group) => {
      const headingId = `attention-${group}`;
      return <section key={group} className={`monitor-attention-group is-${group}`} aria-labelledby={headingId}>
        <h3 id={headingId}>{ATTENTION_LABELS[group]}</h3>
        {groups[group].length ? <ul>{groups[group].map((item) => {
          const status = RUN_STATUS_LABELS[item.status] ?? item.status;
          const elapsed = formatAttentionDuration(item.elapsedMs);
          const accessibleName = `${item.runId} · ${status} · ${elapsed}`;
          return <li key={item.runId}><a href={item.href} className="monitor-attention-link" aria-label={accessibleName}><span className="mono">{item.runId}</span><span>{status}</span><span className="tabular-nums">{elapsed}</span></a></li>;
        })}</ul> : <Text theme="secondary">当前没有记录</Text>}
      </section>;
    })}</div>
  </Card></section>;
}

/** 监控页：进程内指标（count / 分位数 / 错误率）+ 阶段耗时 + 最近查询 */
export default function MonitorPage({ active = true }: { active?: boolean }) {
  const { online, refresh } = useConnection();
  const [snapshot, setSnapshot] = useState<MetricsSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastAt, setLastAt] = useState<number | null>(null);
  const [history, setHistory] = useState<MetricsHistoryItem[]>([]);
  const metricsRequestRef = useRef(0);
  const historyRequestRef = useRef(0);
  const attentionRequestRef = useRef(0);
  const [runsCapability, setRunsCapability] = useState<MonitorRunsCapability>("loading");
  const [attention, setAttention] = useState<MonitorAttentionGroups>(EMPTY_ATTENTION);
  const [attentionLoading, setAttentionLoading] = useState(false);
  const [attentionReconnecting, setAttentionReconnecting] = useState(false);
  const [legacyRecent, setLegacyRecent] = useState<RecentQuery[]>([]);

  /** 指标失败时保留最后一次真实快照；首次失败显示显式错误态，不注入演示读数。 */
  const load = useCallback(
    async ({ silent = false, force = false }: { silent?: boolean; force?: boolean } = {}) => {
      const requestId = ++metricsRequestRef.current;
      if (online === false && !force) {
        setError("后端服务连接已断开");
        setLastAt(Date.now());
        if (!silent) setLoading(false);
        return;
      }
      if (!silent) setLoading(true);
      try {
        const next = await fetchMetrics();
        if (requestId !== metricsRequestRef.current) return;
        setSnapshot(next);
        setError(null);
      } catch (loadError) {
        if (requestId !== metricsRequestRef.current) return;
        setError(loadError instanceof Error ? loadError.message : String(loadError));
      } finally {
        if (requestId === metricsRequestRef.current) {
          setLastAt(Date.now());
          if (!silent) setLoading(false);
        }
      }
    },
    [online],
  );

  useEffect(() => {
    if (!active) return;
    void load();
    return () => {
      metricsRequestRef.current += 1;
    };
  }, [active, load]);

  // 历史趋势（60s 快照；与指标刷新一并更新）
  const loadHistory = useCallback(async () => {
    const requestId = ++historyRequestRef.current;
    if (online === false) return;
    try {
      const next = await fetchMetricsHistory(120);
      if (requestId === historyRequestRef.current) setHistory(next.items ?? []);
    } catch {
      // 历史是辅助信息；失败时保留已有曲线，且旧请求不能覆盖新结果。
    }
  }, [online]);

  useEffect(() => {
    if (!active) return;
    void loadHistory();
    return () => {
      historyRequestRef.current += 1;
    };
  }, [active, loadHistory]);

  const loadAttention = useCallback(async () => {
    const requestId = ++attentionRequestRef.current;
    setAttentionLoading(true);
    try {
      let health;
      try {
        health = await fetchRunHealth();
      } catch (healthError) {
        if (requestId !== attentionRequestRef.current) return;
        const failure = classifyRunsHealth(undefined, healthError);
        if (failure === "legacy") {
          setRunsCapability("legacy");
          try {
            const metrics = await fetchMetrics();
            if (requestId !== attentionRequestRef.current) return;
            setLegacyRecent(metrics.recent_queries ?? []);
            setAttentionReconnecting(false);
          } catch {
            if (requestId === attentionRequestRef.current) setAttentionReconnecting(true);
          }
        } else if (failure === "unauthorized") {
          setRunsCapability("unauthorized");
          setLegacyRecent([]);
          setAttentionReconnecting(false);
        } else {
          setRunsCapability((current) => current === "loading" ? "unavailable" : current);
          setAttentionReconnecting(true);
        }
        return;
      }
      if (requestId !== attentionRequestRef.current) return;
      const capability = classifyRunsHealth(health);
      if (capability === "disabled") {
        setRunsCapability("disabled");
        setLegacyRecent([]);
        setAttentionReconnecting(false);
        return;
      }
      const result = await loadMonitorAttention((filters) => fetchRuns(filters));
      if (requestId !== attentionRequestRef.current) return;
      const allFailed = result.failedGroups.length === ATTENTION_REQUESTS.length;
      setAttention((current) => ({
        active: result.failedGroups.includes("active") ? current.active : result.groups.active,
        stuck: result.failedGroups.includes("stuck") ? current.stuck : result.groups.stuck,
        slow: result.failedGroups.includes("slow") ? current.slow : result.groups.slow,
        errors: result.failedGroups.includes("errors") ? current.errors : result.groups.errors,
        cancelled: result.failedGroups.includes("cancelled") ? current.cancelled : result.groups.cancelled,
      }));
      if (allFailed) {
        setRunsCapability((current) => current === "loading" ? "unavailable" : current);
        setAttentionReconnecting(true);
      } else {
        setRunsCapability("available");
        setLegacyRecent([]);
        setAttentionReconnecting(result.failedGroups.length > 0);
      }
    } finally {
      if (requestId === attentionRequestRef.current) setAttentionLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!active) return;
    void loadAttention();
    return () => { attentionRequestRef.current += 1; };
  }, [active, loadAttention]);

  // 自动轮询：指标 10s，历史 60s。
  // 历史快照由后端每 60s 落盘一次，跟着指标一起 10s 拉取的话
  // 六分之五的请求会拿到完全相同的数据，而每次请求后端都要回读文件。
  useEffect(() => {
    if (!active || !autoRefresh || online === false) return;
    const metricsTimer = setInterval(() => void load({ silent: true }), 10000);
    const historyTimer = setInterval(() => void loadHistory(), 60000);
    const attentionTimer = setInterval(() => void loadAttention(), 10000);
    return () => {
      clearInterval(metricsTimer);
      clearInterval(historyTimer);
      clearInterval(attentionTimer);
    };
  }, [active, autoRefresh, online, load, loadAttention, loadHistory]);

  const m = snapshot?.metrics ?? {};
  // query.total 由 HTTP 边界打点，流式与非流式两条路径都覆盖。
  // 此前读的是 graph.invoke —— 那只在 /api/query 的图编排路径上触发，
  // 而前端默认走 SSE，导致这三张卡片对着真实后端恒为 0，
  // 同时下面却列着几十条最近提问记录，自相矛盾。
  const invoke: MetricStat | undefined = m["query.total"];
  const invokeErrors: MetricStat | undefined = m["query.total.errors"];
  const metricRecent: RecentQuery[] = snapshot?.recent_queries ?? [];
  const recent = runsCapability === "legacy" ? legacyRecent : [];
  const abstainRate = abstentionPercent(m, metricRecent);
  // 引用验证质量（R3-B 埋点：verify.total / verify.citations.failed / verify.l3.evaluated）
  const verifyTotal = (m["verify.total"] as MetricStat | undefined)?.count ?? 0;
  const verifyFailed = (m["verify.citations.failed"] as MetricStat | undefined)?.count ?? 0;
  const citationFailRate = verifyTotal > 0 ? (verifyFailed / verifyTotal) * 100 : 0;
  const circuits = snapshot?.circuits ?? {};
  const trippedCircuits = Object.entries(circuits).filter(([, c]) => c.state !== "closed");
  // 查询缓存命中率（TTL 缓存：相同问题 10 分钟内直接命中，不重复检索）
  const cache = snapshot?.cache;
  const cacheHitRate = cacheHitPercent(cache);
  const protectionSignals = projectProtectionSignals(m);
  const embedCacheRate = cacheHitPercent(snapshot?.embed_cache);
  const llmCacheRate = cacheHitPercent(snapshot?.llm_cache);
  const sharedQueue = snapshot?.queue?.shared;
  const hasInfrastructureDetails =
    snapshot?.embed_cache !== undefined ||
    snapshot?.llm_cache !== undefined ||
    snapshot?.redis !== undefined ||
    sharedQueue !== undefined;

  const tableRows = Object.entries(m)
    .map(([key, stat]) => {
      const [name, tagsRaw] = key.split("|");
      return {
        key,
        name,
        tags: tagsRaw ? Object.fromEntries(tagsRaw.split(",").map((kv) => kv.split("="))) : {},
        stat,
      };
    })
    .sort((a, b) => a.name.localeCompare(b.name));

  return (
    <div className="page-slot">
      <PageTopbar
        icon={<ChartLineIcon />}
        title="运行监控"
        subtitle={
          <>
            最近数据 {snapshot?.ts ? formatTime(snapshot.ts) : "-"}
            {lastAt !== null && " · 上次刷新 " + new Date(lastAt).toLocaleTimeString("zh-CN", { hour12: false })}
          </>
        }
        extra={
          <>
            {/* 拉取失败必须在标题栏就能看见：这一页的数字会被当作事故依据 */}
            {error !== null && <Tag theme="danger" variant="light">数据可能已过期</Tag>}
            <Button
              tag="button"
              theme="primary"
              size="small"
              icon={<RefreshIcon className={loading ? "tdesign-icon-spin" : undefined} />}
              onClick={() => { void load(); void loadAttention(); }}
            >
              刷新
            </Button>
            <Button
              tag="button"
              variant="outline"
              size="small"
              icon={autoRefresh ? <PauseCircleIcon /> : <PlayCircleIcon />}
              onClick={() => setAutoRefresh((value) => !value)}
            >
              {autoRefresh ? "自动更新：开" : "自动更新：关"}
            </Button>
          </>
        }
      />
      <div className="page-shell">
        <div className="page-shell-inner">

        {/* 拉取失败：从没拿到过快照就整页兜底，拿到过就横幅提示并标注数据时间 */}
        {error !== null &&
          (snapshot === null ? (
            <PageState
              status="error"
              title="取不到监控数据"
              description={"无法连接后端服务（" + error + "）。请确认服务已启动后重试。"}
              extra={
                <Button
                  tag="button"
                  theme="primary"
                  icon={<RefreshIcon className={loading ? "tdesign-icon-spin" : undefined} />}
                  onClick={() => {
                    void refresh();
                    void load();
                  }}
                >
                  重试
                </Button>
              }
            />
          ) : (
            <Alert
              {...({ role: "alert", "aria-label": "监控数据已停止更新" } as Record<string, unknown>)}
              className="monitor-stale-alert"
              theme="error"
              title="监控数据已停止更新"
              message={
                "最近一次拉取失败（" +
                error +
                "）。下面显示的仍是 " +
                (snapshot.ts ? formatTime(snapshot.ts) : "上一次") +
                " 的数据，不代表当前状态。"
              }
              operation={
                <Button tag="button" size="small" variant="outline" loading={loading} aria-label="重试监控数据" onClick={() => { refresh(); void load({ force: true }); }}>
                  <RefreshIcon className={loading ? "tdesign-icon-spin" : undefined} /> 重试
                </Button>
              }
            />
          ))}

        <MonitorAttentionBoard groups={attention} capability={runsCapability} reconnecting={attentionReconnecting} loading={attentionLoading} onRetry={() => void loadAttention()} />

        {error !== null && snapshot === null ? null : (
          <>
        {/* 指标卡 */}
        <Row gutter={[12, 12]} className="monitor-summary monitor-summary-primary">
          <Col xs={12} md={6}>
            <StatCard
              label="完成的实际计算"
              hint="成功完成且实际执行了问答管线的累计次数；当前接口不包含缓存直接命中、排队拒绝等所有 HTTP 请求。"
              icon={<ThunderIcon />}
              value={(invoke?.count ?? 0).toLocaleString()}
            />
          </Col>
          <Col xs={12} md={6}>
            <StatCard
              label="实际计算累计 P95"
              hint="进程启动以来实际计算耗时的累计 P95，不是最近一分钟窗口的 P95。"
              icon={<TimeIcon />}
              value={(invoke?.p95 ?? 0).toLocaleString()}
              unit="ms"
            />
          </Col>
          <Col xs={12} md={6}>
            <StatCard
              label="记录到的计算错误"
              hint="当前接口提供的原始错误次数。由于请求总数分母不完整，这里不展示可能误导的错误率。"
              tone={invokeErrors?.count ? "danger" : "success"}
              icon={invokeErrors?.count ? <StopCircleIcon /> : <CheckCircleIcon />}
              value={(invokeErrors?.count ?? 0).toLocaleString()}
              unit="次"
            />
          </Col>
          <Col xs={12} md={6}>
            <StatCard
              label="已交付问答中的弃权"
              hint="在已完整交付给客户端的问答中，系统选择弃权的比例；包含缓存命中返回的结果。"
              tone="warning"
              icon={<StopCircleIcon />}
              value={abstainRate.toFixed(0)}
              unit="%"
            />
          </Col>
          <Col xs={12} md={6}>
            <StatCard
              label="引用验证次数"
              hint="答案经过三层引用验证（L1 存在性 / L2 文本哈希 / L3 蕴含判定）的累计次数。"
              icon={<CertificateIcon />}
              value={verifyTotal.toLocaleString()}
              unit="次"
            />
          </Col>
          <Col xs={12} md={6}>
            <StatCard
              label="引用失败率"
              hint="验证中判定为非 ok 的引用占比（含未支撑 / 陈旧 / 仅存在）。比例高时答案可信度需关注。"
              tone={citationFailRate > 0 ? "warning" : "success"}
              icon={citationFailRate > 0 ? <StopCircleIcon /> : <CheckCircleIcon />}
              value={citationFailRate.toFixed(1)}
              unit="%"
            />
          </Col>
        </Row>

        <DocumentIngestMonitor active={active} />

        {/* 并发负载与缓存（服务端限流保护状态） */}
        <Row gutter={[12, 12]} className="monitor-summary monitor-summary-secondary">
          <Col xs={12} md={6}>
            <StatCard
              label="正在等待处理"
              hint="正在处理或排队的问题数量。数量较高时，回答可能需要多等一会儿。"
              icon={<UsergroupIcon />}
              value={snapshot?.queue?.pending ?? 0}
              unit={"/ 同时处理 " + (snapshot?.queue?.max_concurrent ?? 4) + " 个"}
            />
          </Col>
          <Col xs={12} md={6}>
            <StatCard
              label="已暂存的回答"
              hint="最近相同的问题会暂时保存，重复提问可以更快得到结果。"
              icon={<DataBaseIcon />}
              value={(snapshot?.cache?.size ?? 0).toLocaleString()}
              unit={"/ 最多 " + (snapshot?.cache?.max ?? 64) + " 条"}
            />
          </Col>
          <Col xs={12} md={6}>
            <StatCard
              label="重复问题快速返回"
              hint="重复问题直接使用近期结果的比例。比例高时，系统会更快响应。"
              tone="success"
              icon={<ThunderIcon />}
              value={cacheHitRate.toFixed(0)}
              unit="%"
            />
          </Col>
          <Col xs={12} md={6}>
            <StatCard
              label="最多可等待的问题"
              hint="等待处理的问题达到这个数量后，系统会提示稍后再试，避免长时间卡住。"
              tone="warning"
              icon={<ChartBarIcon />}
              value={snapshot?.queue?.queue_max ?? 20}
            />
          </Col>
        </Row>

        <Card
          size="small"
          title="质量与降级保护（进程累计）"
          actions={<Tag variant="light-outline">原始计数</Tag>}
          style={{ marginBottom: 16 }}
        >
          <Row gutter={[12, 12]} className="monitor-summary monitor-summary-secondary">
            {protectionSignals.map((signal) => (
              <Col xs={12} md={6} key={signal.key}>
                <StatCard
                  label={signal.label}
                  hint={signal.hint}
                  tone={
                    signal.tone === "danger"
                      ? "danger"
                      : signal.tone === "warning"
                        ? "warning"
                        : "primary"
                  }
                  icon={<PauseCircleIcon />}
                  value={signal.count.toLocaleString()}
                  unit="次"
                />
              </Col>
            ))}
          </Row>
          <Text theme="secondary" style={{ fontSize: FONT_SIZE.xs }}>
            这些值是进程启动以来的保护和降级累计次数，用于定位系统何时主动保守处理，不代表请求比例。
          </Text>
        </Card>

        {hasInfrastructureDetails ? (
          <Card size="small" title="缓存与共享基础设施" style={{ marginBottom: 16 }}>
            <Row gutter={[12, 12]} className="monitor-summary monitor-summary-secondary">
              {snapshot?.embed_cache ? (
                <Col xs={12} md={6}>
                  <StatCard
                    label="查询向量缓存命中"
                    hint="只节省问题向量化，不代表整条问答链路被缓存。"
                    icon={<DataBaseIcon />}
                    value={embedCacheRate.toFixed(0)}
                    unit="%"
                  />
                </Col>
              ) : null}
              {snapshot?.llm_cache ? (
                <Col xs={12} md={6}>
                  <StatCard
                    label="检索期 LLM 缓存命中"
                    hint="减少问题改写、路由、HyDE 等预处理 LLM 调用。"
                    icon={<ThunderIcon />}
                    value={llmCacheRate.toFixed(0)}
                    unit="%"
                  />
                </Col>
              ) : null}
              {snapshot?.redis ? (
                <Col xs={12} md={6}>
                  <StatCard
                    label="Redis 共享层"
                    hint={snapshot.redis.detail}
                    tone={
                      snapshot.redis.configured
                        ? snapshot.redis.connected
                          ? "success"
                          : "danger"
                        : "primary"
                    }
                    icon={
                      snapshot.redis.configured
                        ? snapshot.redis.connected
                          ? <CheckCircleIcon />
                          : <StopCircleIcon />
                        : <DataBaseIcon />
                    }
                    value={
                      snapshot.redis.configured
                        ? snapshot.redis.connected
                          ? "已连接"
                          : "已降级"
                        : "未配置"
                    }
                  />
                </Col>
              ) : null}
              {sharedQueue?.active ? (
                <Col xs={12} md={6}>
                  <StatCard
                    label="跨副本排队"
                    hint="Redis Streams 共享队列中的等待与未确认任务。"
                    icon={<UsergroupIcon />}
                    value={(sharedQueue.queued ?? 0) + (sharedQueue.unacked ?? 0)}
                    unit="项"
                  />
                </Col>
              ) : null}
            </Row>
          </Card>
        ) : null}
        {/* 依赖保护状态：熔断器直接回答「系统为什么在拒答」 */}
        {Object.keys(circuits).length > 0 && (
          <Card
            size="small"
            title="依赖保护状态"
            style={{ marginBottom: 16 }}
            actions={
              trippedCircuits.length > 0 ? (
                <Tag theme="warning" variant="light">{trippedCircuits.length} 个正在保护中</Tag>
              ) : (
                <Tag theme="success" variant="light">全部正常</Tag>
              )
            }
          >
            <Text theme="secondary" style={{ fontSize: FONT_SIZE.sm, display: "block", marginBottom: 10 }}>
              某个依赖连续失败到阈值后会自动熔断，冷却期内快速失败而不是让请求一直等待。
              熔断期间问答会降级或拒答——这里是排查「为什么突然答不出来」的第一站。
            </Text>
            <Row gutter={[12, 12]}>
              {Object.entries(circuits).map(([name, c]) => (
                <Col xs={24} md={12} key={name}>
                  <Card size="small" className="stat-card" style={{ minHeight: 0 }}>
                    <div className="stat-label">
                      <span
                        className={
                          "stat-icon " + (c.state === "closed" ? "is-success" : "is-danger")
                        }
                      >
                        {c.state === "closed" ? <CheckCircleIcon /> : <StopCircleIcon />}
                      </span>
                      {CIRCUIT_LABELS[name] ?? name}
                      <Tag
                        theme={
                          c.state === "closed"
                            ? "success"
                            : c.state === "half_open"
                              ? "warning"
                              : "danger"
                        }
                        variant="light"
                        style={{ marginLeft: "auto" }}
                      >
                        {CIRCUIT_STATE_LABELS[c.state] ?? c.state}
                      </Tag>
                    </div>
                    <Text
                      theme="secondary"
                      className="tabular-nums"
                      style={{ fontSize: FONT_SIZE.sm, display: "block", marginTop: 8 }}
                    >
                      连续失败 {c.failures} 次 · 累计熔断 {c.open_count} 次 · 恢复 {c.close_count} 次
                    </Text>
                  </Card>
                </Col>
              ))}
            </Row>
          </Card>
        )}

        {/* 阶段耗时 */}
        <Card size="small" title="每一步耗时（进程累计分位数）" style={{ marginBottom: 16 }}>
          {snapshot && <StageBars snapshot={snapshot} />}
        </Card>

        {/* 历史趋势 */}
        <Card size="small" title="指标历史（累计快照）" style={{ marginBottom: 16 }}>
          <HistoryTrend items={history} />
        </Card>

        {/* 指标明细 */}
        <Card
          size="small"
          title="技术明细"
          actions={<Text theme="secondary" className="monitor-card-note">需要排查问题时查看</Text>}
          className="monitor-section-card"
        >
          <MonitorMetricsTable rows={tableRows} />
        </Card>

        {/* 最近查询 */}
        {runsCapability === "legacy" ? (
          <Card size="small" title="最近的提问记录（legacy）" className="monitor-section-card">
            <LegacyRecentQueriesTable rows={recent} />
          </Card>
        ) : null}
          </>
        )}
        </div>
      </div>
    </div>
  );
}
