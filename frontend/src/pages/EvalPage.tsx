/* eslint-disable @typescript-eslint/no-explicit-any -- compatibility callback types during TDesign migration */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Progress,
  Radio,
  Row,
  Select,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "../ui/index";
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  DownloadOutlined,
  PlayCircleOutlined,
  SafetyCertificateOutlined,
} from "../ui/icons";
import PageTopbar from "../components/PageTopbar";
import PageState from "../components/PageState";
import EChart, { type EChartsOption } from "../charts/EChart";
import { useChartPalette } from "../charts/chartTheme";
import { fetchEvalDatasets, fetchEvalResults, runEval } from "../api/client";
import { DEMO_DATASETS, DEMO_EVAL_REPORT } from "../api/mock";
import { useConnection } from "../context/ConnectionContext";
import {
  classifyEvalCase,
  filterEvalCases,
  summarizeEvalCases,
  type EvalCaseCategory,
  type EvalCaseFilter,
  type EvalCaseSummary,
} from "../eval/evalProjection";
import type { CaseResult, EvalReport } from "../types/rag";
import { FONT_SIZE } from "../theme/tokens";

const { Text, Paragraph } = Typography;

type EvalSource = "demo" | "dry-run" | "real" | "history";

interface EvalReportMeta {
  source: EvalSource;
  dataset: string;
  pipeline: string;
  loadedAt: number;
  outPath?: string;
  stale?: boolean;
}

const SOURCE_META: Record<EvalSource, { label: string; color: string; description: string }> = {
  demo: { label: "演示数据", color: "orange", description: "不反映当前资料、模型或设置" },
  "dry-run": { label: "快速演练", color: "gold", description: "使用占位管线和示例裁判" },
  real: { label: "真实评测", color: "green", description: "使用当前资料和真实问答管线" },
  history: { label: "历史报告", color: "blue", description: "后端保存的最近一次评测结果" },
};

const METRIC_COLORS = {
  good: "var(--color-success)",
  warn: "var(--color-warning)",
  bad: "var(--color-danger)",
  neutral: "var(--color-primary)",
};

type MetricDirection = "low" | "high" | "neutral";

const METRIC_DEFS: Array<{
  key: string;
  label: string;
  hint: string;
  direction: MetricDirection;
}> = [
  {
    key: "refusal_rate",
    label: "总体弃权比例",
    hint: "仅描述系统选择弃权的频率，不单独判断好坏；应结合题目可答性矩阵查看。",
    direction: "neutral",
  },
  {
    key: "over_refusal_rate",
    label: "过度弃权比例",
    hint: "资料足够但系统没有回答的比例，越低越好。",
    direction: "low",
  },
  {
    key: "hallucination_rate",
    label: "危险作答比例",
    hint: "资料不足时仍给出回答的比例，越低越好。",
    direction: "low",
  },
  {
    key: "avg_groundedness",
    label: "答案有资料支持",
    hint: "仅统计裁判成功返回分数的已回答用例，越高越好。",
    direction: "high",
  },
  {
    key: "avg_relevance",
    label: "答案贴近问题",
    hint: "仅统计裁判成功返回分数的已回答用例，越高越好。",
    direction: "high",
  },
  {
    key: "citation_failure_rate",
    label: "引用需复核比例",
    hint: "后端返回的是引用级比例，但当前报告没有提供引用总数，因此无法展示引用级分母。",
    direction: "low",
  },
];

const PERCENT_FORMAT = new Intl.NumberFormat("zh-CN", {
  style: "percent",
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

const TIME_FORMAT = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

function metricColor(direction: MetricDirection, value: number | undefined): string {
  if (value === undefined || direction === "neutral") return METRIC_COLORS.neutral;
  if (direction === "high") {
    return value >= 0.8 ? METRIC_COLORS.good : value >= 0.6 ? METRIC_COLORS.warn : METRIC_COLORS.bad;
  }
  return value <= 0.05 ? METRIC_COLORS.good : value <= 0.2 ? METRIC_COLORS.warn : METRIC_COLORS.bad;
}

function metricEvidence(key: string, summary: EvalCaseSummary): string {
  switch (key) {
    case "refusal_rate":
      return `${summary.abstained} / ${summary.total} 个用例`;
    case "over_refusal_rate":
      return `${summary.matrix.overRefusal} / ${summary.answerable} 个可答用例`;
    case "hallucination_rate":
      return `${summary.matrix.unsafeAnswer} / ${summary.unanswerable} 个不可答用例`;
    case "avg_groundedness":
      return `${summary.groundedness.scored} / ${summary.groundedness.eligible} 个已回答用例完成判分`;
    case "avg_relevance":
      return `${summary.relevance.scored} / ${summary.relevance.eligible} 个已回答用例完成判分`;
    case "citation_failure_rate":
      return `用例口径：${summary.citationCases.failed} / ${summary.citationCases.eligible} 需复核`;
    default:
      return "当前报告未提供分母";
  }
}

const CATEGORY_META: Record<EvalCaseCategory, { label: string; color: string }> = {
  correct_answer: { label: "正确回答", color: "success" },
  over_refusal: { label: "过度弃权", color: "warning" },
  safe_refusal: { label: "安全弃权", color: "blue" },
  unsafe_answer: { label: "危险作答", color: "error" },
  incomplete: { label: "状态不完整", color: "default" },
};

function DecisionMatrix({
  summary,
  filter,
  onFilter,
}: {
  summary: EvalCaseSummary;
  filter: EvalCaseFilter;
  onFilter: (filter: EvalCaseFilter) => void;
}) {
  const cells: Array<{ category: EvalCaseCategory; title: string; count: number; tone: string }> = [
    { category: "correct_answer", title: "正确回答", count: summary.matrix.correctAnswer, tone: "good" },
    { category: "over_refusal", title: "过度弃权", count: summary.matrix.overRefusal, tone: "warn" },
    { category: "unsafe_answer", title: "危险作答", count: summary.matrix.unsafeAnswer, tone: "danger" },
    { category: "safe_refusal", title: "安全弃权", count: summary.matrix.safeRefusal, tone: "safe" },
  ];
  return (
    <div className="eval-matrix" aria-label="可答性与回答决策矩阵">
      <div className="eval-matrix-corner">题目 / 系统</div>
      <div className="eval-matrix-axis">给出回答</div>
      <div className="eval-matrix-axis">选择弃权</div>
      <div className="eval-matrix-axis is-row">资料可回答</div>
      {cells.slice(0, 2).map((cell) => (
        <button
          key={cell.category}
          type="button"
          className={`eval-matrix-cell is-${cell.tone}${filter === cell.category ? " is-selected" : ""}`}
          aria-pressed={filter === cell.category}
          onClick={() => onFilter(filter === cell.category ? "all" : cell.category)}
        >
          <span>{cell.title}</span>
          <strong className="tabular-nums">{cell.count}</strong>
        </button>
      ))}
      <div className="eval-matrix-axis is-row">资料不可回答</div>
      {cells.slice(2).map((cell) => (
        <button
          key={cell.category}
          type="button"
          className={`eval-matrix-cell is-${cell.tone}${filter === cell.category ? " is-selected" : ""}`}
          aria-pressed={filter === cell.category}
          onClick={() => onFilter(filter === cell.category ? "all" : cell.category)}
        >
          <span>{cell.title}</span>
          <strong className="tabular-nums">{cell.count}</strong>
        </button>
      ))}
      {summary.matrix.incomplete > 0 ? (
        <div className="eval-matrix-note">另有 {summary.matrix.incomplete} 条用例状态不完整</div>
      ) : null}
    </div>
  );
}

function QualityScatter({ cases }: { cases: CaseResult[] }) {
  const pal = useChartPalette();
  const scored = useMemo(
    () => cases.filter((entry) => entry.groundedness !== null && entry.relevance !== null),
    [cases],
  );
  const option = useMemo<EChartsOption>(
    () => ({
      backgroundColor: "transparent",
      grid: { left: 44, right: 18, top: 20, bottom: 42 },
      tooltip: {
        trigger: "item",
        renderMode: "richText",
        formatter: (params: unknown) => {
          const point = params as { data?: { name?: string; value?: number[]; category?: string } };
          const values = point.data?.value ?? [];
          return `${point.data?.name ?? "用例"}\n贴题 ${PERCENT_FORMAT.format(values[0] ?? 0)}\n有据 ${PERCENT_FORMAT.format(values[1] ?? 0)}\n${point.data?.category ?? ""}`;
        },
      },
      xAxis: {
        type: "value",
        min: 0,
        max: 1,
        name: "贴近问题",
        nameLocation: "middle",
        nameGap: 28,
        axisLabel: { color: pal.axis, formatter: (value: number) => `${Math.round(value * 100)}%` },
        splitLine: { lineStyle: { color: pal.splitLine } },
      },
      yAxis: {
        type: "value",
        min: 0,
        max: 1,
        name: "资料支持",
        nameLocation: "middle",
        nameGap: 34,
        axisLabel: { color: pal.axis, formatter: (value: number) => `${Math.round(value * 100)}%` },
        splitLine: { lineStyle: { color: pal.splitLine } },
      },
      series: [
        {
          type: "scatter",
          symbolSize: 13,
          data: scored.map((entry) => {
            const category = classifyEvalCase(entry);
            const color =
              category === "unsafe_answer"
                ? pal.danger
                : !entry.citations_ok
                  ? pal.warning
                  : pal.primary;
            return {
              name: entry.question,
              value: [entry.relevance, entry.groundedness],
              category: CATEGORY_META[category].label,
              itemStyle: { color, borderColor: pal.bg, borderWidth: 1.5 },
            };
          }),
        },
      ],
    }),
    [pal, scored],
  );

  if (scored.length === 0) {
    return <PageState compact status="empty" title="暂无可绘制分数" description="裁判成功返回两个分数后显示散点图" />;
  }
  return (
    <>
      <EChart
        option={option}
        height={310}
        ariaLabel={`质量散点图，共 ${scored.length} 个已完成双重判分的用例，横轴为贴题程度，纵轴为资料支持程度`}
      />
      <div className="eval-scatter-legend" aria-hidden="true">
        <span><i className="is-good" />未标引用失败</span>
        <span><i className="is-warn" />引用需复核</span>
        <span><i className="is-danger" />危险作答</span>
      </div>
    </>
  );
}

/** 测试页：评测执行器 + 可信指标 + 弃权矩阵 + 用例诊断。 */
export default function EvalPage() {
  const { online } = useConnection();
  const [datasets, setDatasets] = useState<string[]>(DEMO_DATASETS);
  const [dataset, setDataset] = useState<string>(DEMO_DATASETS[0]);
  const [pipeline, setPipeline] = useState<string>("none");
  const [running, setRunning] = useState(false);
  const [report, setReport] = useState<EvalReport | null>(null);
  const [reportMeta, setReportMeta] = useState<EvalReportMeta | null>(null);
  const [previousReport, setPreviousReport] = useState<EvalReport | null>(null);
  const [previousMeta, setPreviousMeta] = useState<EvalReportMeta | null>(null);
  const [caseFilter, setCaseFilter] = useState<EvalCaseFilter>("all");
  const [error, setError] = useState<string | null>(null);
  const [loadedHistory, setLoadedHistory] = useState(false);
  const loadRequest = useRef(0);

  useEffect(() => {
    const requestId = ++loadRequest.current;
    const controller = new AbortController();
    if (online === false) {
      setLoadedHistory(true);
      return () => controller.abort();
    }
    setLoadedHistory(false);
    void Promise.allSettled([
      fetchEvalDatasets(controller.signal),
      fetchEvalResults(controller.signal),
    ]).then(([datasetsResult, reportResult]) => {
      if (controller.signal.aborted || requestId !== loadRequest.current) return;
      if (datasetsResult.status === "fulfilled") {
        const nextDatasets = datasetsResult.value.datasets;
        if (nextDatasets.length) {
          setDatasets(nextDatasets);
          setDataset(datasetsResult.value.default || nextDatasets[0]);
        }
      }
      if (reportResult.status === "fulfilled" && reportResult.value.report) {
        setReport(reportResult.value.report);
        setReportMeta({
          source: "history",
          dataset: "历史报告未记录数据集",
          pipeline: "历史报告未记录管线",
          loadedAt: Date.now(),
          outPath: "eval/results.json",
        });
      }
      setLoadedHistory(true);
    });
    return () => controller.abort();
  }, [online]);

  const acceptReport = useCallback(
    (nextReport: EvalReport, nextMeta: EvalReportMeta) => {
      if (report && reportMeta) {
        setPreviousReport(report);
        setPreviousMeta(reportMeta);
      }
      setReport(nextReport);
      setReportMeta(nextMeta);
      setCaseFilter("all");
    },
    [report, reportMeta],
  );

  const handleRun = useCallback(async () => {
    setRunning(true);
    setError(null);
    try {
      if (online === false) {
        await new Promise((resolve) => setTimeout(resolve, 900));
        acceptReport(DEMO_EVAL_REPORT, {
          source: "demo",
          dataset,
          pipeline,
          loadedAt: Date.now(),
          outPath: "eval/results.json（演示）",
        });
        return;
      }
      const response = await runEval({ dataset_spec: dataset, pipeline });
      acceptReport(response.report, {
        source: response.dry_run ? "dry-run" : "real",
        dataset,
        pipeline,
        loadedAt: Date.now(),
        outPath: response.out,
      });
    } catch (runError) {
      setError(String(runError));
      setReportMeta((current) => (current ? { ...current, stale: true } : current));
    } finally {
      setRunning(false);
    }
  }, [acceptReport, dataset, online, pipeline]);

  const cases = useMemo(() => report?.cases ?? [], [report]);
  const metrics = useMemo(() => report?.metrics ?? {}, [report]);
  const summary = useMemo(() => summarizeEvalCases(cases), [cases]);
  const filteredCases = useMemo(() => filterEvalCases(cases, caseFilter), [caseFilter, cases]);
  const comparablePrevious =
    previousReport &&
    previousMeta &&
    reportMeta &&
    previousMeta.source === reportMeta.source &&
    previousMeta.dataset === reportMeta.dataset &&
    previousMeta.pipeline === reportMeta.pipeline
      ? previousReport
      : null;

  const previousSummary = useMemo(
    () => (comparablePrevious ? summarizeEvalCases(comparablePrevious.cases) : null),
    [comparablePrevious],
  );
  const judgeMissingCount = filterEvalCases(cases, "judge_missing").length;
  const dualScoredCount = cases.filter(
    (entry) => entry.groundedness !== null && entry.relevance !== null,
  ).length;

  const exportReport = () => {
    if (!report) return;
    const blob = new Blob(
      [
        JSON.stringify(
          {
            exported_at: new Date().toISOString(),
            source: reportMeta,
            report,
          },
          null,
          2,
        ),
      ],
      { type: "application/json" },
    );
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "rag4c-eval-report.json";
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const filterOptions = [
    { value: "all", label: `全部用例 (${cases.length})` },
    { value: "correct_answer", label: `正确回答 (${summary.matrix.correctAnswer})` },
    { value: "over_refusal", label: `过度弃权 (${summary.matrix.overRefusal})` },
    { value: "safe_refusal", label: `安全弃权 (${summary.matrix.safeRefusal})` },
    { value: "unsafe_answer", label: `危险作答 (${summary.matrix.unsafeAnswer})` },
    { value: "citation_failed", label: `引用需复核 (${summary.citationCases.failed})` },
    { value: "judge_missing", label: `裁判缺失 (${judgeMissingCount})` },
    { value: "incomplete", label: `状态不完整 (${summary.matrix.incomplete})` },
  ];

  return (
    <div className="page-slot">
      <PageTopbar
        icon={<SafetyCertificateOutlined />}
        title="质量评测"
        subtitle="检查系统何时应该回答、何时应该弃权，以及回答是否贴题并有资料支持"
        extra={
          <Button size="small" icon={<DownloadOutlined />} disabled={!report} onClick={exportReport}>
            导出报告
          </Button>
        }
      />
      <div className="page-shell">
        <div className="page-shell-inner">
          <Card size="small" className="eval-config-card">
            <Row gutter={[16, 12]} align="middle">
              <Col flex="auto">
                <Text strong>要检查的题目</Text>
                <Select
                  value={dataset}
                  onChange={setDataset}
                  aria-label="选择要检查的题目"
                  className="eval-config-control"
                  options={datasets.map((entry) => ({ value: entry, label: entry }))}
                />
              </Col>
              <Col flex="auto">
                <Text strong>检查方式</Text>
                <Radio.Group
                  value={pipeline}
                  onChange={(event: any) => setPipeline(event.target.value)}
                  aria-label="选择检查方式"
                  className="eval-config-control"
                >
                  <Radio.Button value="none">快速演练</Radio.Button>
                  <Radio.Button value="rag:answer_query">真实资料评测</Radio.Button>
                </Radio.Group>
              </Col>
              <Col>
                <Button
                  type="primary"
                  icon={<PlayCircleOutlined />}
                  onClick={() => void handleRun()}
                  loading={running}
                  size="large"
                >
                  运行评测
                </Button>
              </Col>
            </Row>
            <Paragraph type="secondary" className="eval-config-help">
              快速演练使用占位问答和示例裁判，只用于检查评测流程是否可运行；真实资料评测才反映当前知识库和模型设置。
            </Paragraph>
            {running ? (
              <Alert
                type="info"
                showIcon
                message="正在检查回答质量"
                description="系统会逐题执行问答和裁判。你可以切换到其他页面，评测请求仍会继续等待结果。"
              />
            ) : null}
            {error ? (
              <Alert
                type="error"
                showIcon
                message={report ? "本次评测失败，继续显示上一份报告" : "本次评测失败"}
                description="没有使用演示分数替代失败结果。请检查服务、数据集或真实管线配置后重试。"
              />
            ) : null}
          </Card>

          {!loadedHistory && !report ? <PageState status="loading" title="正在加载历史评测" /> : null}
          {loadedHistory && !report && !error ? (
            <PageState status="empty" title="还没有评测报告" description="选择数据集和检查方式后运行评测" />
          ) : null}
          {loadedHistory && !report && error ? (
            <PageState status="error" title="没有可展示的评测报告" description="失败结果不会自动替换成演示分数" />
          ) : null}

          {report && reportMeta ? (
            <>
              <div className="eval-report-head">
                <div>
                  <div className="eval-report-source">
                    <Tag color={SOURCE_META[reportMeta.source].color}>{SOURCE_META[reportMeta.source].label}</Tag>
                    {reportMeta.stale ? <Tag color="warning">保留的旧报告</Tag> : null}
                    <span>{SOURCE_META[reportMeta.source].description}</span>
                  </div>
                  <div className="eval-report-lineage">
                    <span>数据集：{reportMeta.dataset}</span>
                    <span>管线：{reportMeta.pipeline}</span>
                    <span>载入时间：{TIME_FORMAT.format(new Date(reportMeta.loadedAt))}</span>
                  </div>
                </div>
                {reportMeta.outPath ? <Text code>{reportMeta.outPath}</Text> : null}
              </div>

              <Row gutter={[12, 12]} className="eval-metric-grid">
                {METRIC_DEFS.map((definition) => {
                  const value =
                    definition.key === "avg_groundedness"
                      ? (summary.groundedness.average ?? undefined)
                      : definition.key === "avg_relevance"
                        ? (summary.relevance.average ?? undefined)
                        : metrics[definition.key];
                  const previousValue =
                    definition.key === "avg_groundedness"
                      ? (previousSummary?.groundedness.average ?? undefined)
                      : definition.key === "avg_relevance"
                        ? (previousSummary?.relevance.average ?? undefined)
                        : comparablePrevious?.metrics[definition.key];
                  const delta =
                    value !== undefined && previousValue !== undefined
                      ? (value - previousValue) * 100
                      : null;
                  return (
                    <Col xs={12} md={8} xl={4} key={definition.key}>
                      <Card size="small" className="eval-metric-card">
                        <Statistic
                          title={<TooltipLabel label={definition.label} hint={definition.hint} />}
                          value={
                            value === undefined
                              ? "—"
                              : Number(((value * 100) as number).toFixed(1))
                          }
                          precision={value === undefined ? undefined : 1}
                          suffix={value === undefined ? undefined : "%"}
                          valueStyle={{
                            color: metricColor(definition.direction, value),
                            fontSize: FONT_SIZE.xxxl,
                          }}
                        />
                        <div className="eval-metric-evidence">{metricEvidence(definition.key, summary)}</div>
                        {delta !== null ? (
                          <div className="eval-metric-delta tabular-nums">
                            较当前会话上一份 {delta >= 0 ? "+" : ""}{delta.toFixed(1)} 个百分点
                          </div>
                        ) : null}
                      </Card>
                    </Col>
                  );
                })}
              </Row>

              <Row gutter={[12, 12]} className="eval-analysis-row">
                <Col xs={24} xl={10}>
                  <Card size="small" title="回答与弃权决策">
                    <DecisionMatrix summary={summary} filter={caseFilter} onFilter={setCaseFilter} />
                  </Card>
                </Col>
                <Col xs={24} xl={14}>
                  <Card
                    size="small"
                    title="回答质量分布"
                    extra={
                      <Text type="secondary" className="eval-card-extra">
                        双重判分 {dualScoredCount} / {summary.groundedness.eligible}
                      </Text>
                    }
                  >
                    <QualityScatter cases={cases} />
                  </Card>
                </Col>
              </Row>

              <Card
                size="small"
                title="逐用例诊断"
                extra={
                  <Select
                    value={caseFilter}
                    onChange={setCaseFilter}
                    aria-label="筛选评测用例"
                    className="eval-case-filter"
                    options={filterOptions}
                  />
                }
              >
                <Table
                  size="small"
                  dataSource={filteredCases}
                  rowKey="id"
                  pagination={{ pageSize: 10, size: "small", showTotal: (total: any) => `当前筛选 ${total} 条` }}
                  scroll={{ x: 980 }}
                  columns={[
                    {
                      title: "ID",
                      dataIndex: "id",
                      width: 150,
                      render: (value: string) => (
                        <Tooltip title={value}>
                          <Text code className="eval-case-id">{value}</Text>
                        </Tooltip>
                      ),
                    },
                    { title: "问题", dataIndex: "question", ellipsis: true },
                    {
                      title: "决策结果",
                      key: "outcome",
                      width: 105,
                      render: (_: unknown, entry: CaseResult) => {
                        const category = classifyEvalCase(entry);
                        const meta = CATEGORY_META[category];
                        return <Tag color={meta.color}>{meta.label}</Tag>;
                      },
                    },
                    {
                      title: "资料支持",
                      dataIndex: "groundedness",
                      width: 115,
                      render: (value: number | null) =>
                        value === null ? (
                          <Tag>未判定</Tag>
                        ) : (
                          <Progress
                            aria-label={`资料支持 ${PERCENT_FORMAT.format(value)}`}
                            percent={value * 100}
                            size="small"
                            format={(percent: any) => `${percent?.toFixed(0)}%`}
                          />
                        ),
                    },
                    {
                      title: "贴近问题",
                      dataIndex: "relevance",
                      width: 115,
                      render: (value: number | null) =>
                        value === null ? (
                          <Tag>未判定</Tag>
                        ) : (
                          <Progress
                            aria-label={`贴近问题 ${PERCENT_FORMAT.format(value)}`}
                            percent={value * 100}
                            size="small"
                            format={(percent: any) => `${percent?.toFixed(0)}%`}
                          />
                        ),
                    },
                    {
                      title: "引用状态",
                      dataIndex: "citations_ok",
                      width: 105,
                      render: (value: boolean, entry: CaseResult) =>
                        !entry.answered || entry.abstained ? (
                          <Text type="secondary">不适用</Text>
                        ) : value ? (
                          <Tooltip title="当前报告无法区分没有引用与所有引用都通过">
                            <Tag color="blue" icon={<CheckCircleOutlined />}>未标失败</Tag>
                          </Tooltip>
                        ) : (
                          <Tag color="error" icon={<CloseCircleOutlined />}>需复核</Tag>
                        ),
                    },
                    {
                      title: "备注",
                      dataIndex: "notes",
                      width: 220,
                      render: (value: string) => value || <Text type="secondary">—</Text>,
                    },
                  ]}
                />
              </Card>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function TooltipLabel({ label, hint }: { label: string; hint: string }) {
  return (
    <Tooltip title={hint} trigger={["hover", "focus"]}>
      <span tabIndex={0} className="eval-tooltip-label">{label}</span>
    </Tooltip>
  );
}