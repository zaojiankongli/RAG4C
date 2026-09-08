import { PrimaryTable, Tag, Typography, type PrimaryTableCol } from "tdesign-react";
import { routeLabel } from "../strategy/routes";
import type { MetricStat, RecentQuery } from "../types/rag";

const { Text } = Typography;

export interface MonitorMetricRow {
  key: string;
  name: string;
  tags: Record<string, string>;
  stat: MetricStat;
}

function formatTime(ts: string): string {
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return ts;
  return new Intl.DateTimeFormat("zh-CN", {
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

const metricColumns: PrimaryTableCol<MonitorMetricRow>[] = [
  {
    title: "指标",
    colKey: "name",
    width: 190,
    cell: ({ row }) => <code className="monitor-metric-name">{row.name}</code>,
  },
  {
    title: "标签",
    colKey: "tags",
    width: 180,
    cell: ({ row }) =>
      Object.keys(row.tags).length ? (
        <div className="monitor-metric-tags">
          {Object.entries(row.tags).map(([key, value]) => (
            <Tag key={key} size="small" variant="light-outline">
              {key}={value}
            </Tag>
          ))}
        </div>
      ) : (
        <Text theme="secondary">—</Text>
      ),
  },
  {
    title: "count",
    colKey: "count",
    width: 86,
    align: "right",
    className: "tabular-nums",
    cell: ({ row }) => row.stat.count.toLocaleString(),
  },
  {
    title: "mean",
    colKey: "mean",
    width: 86,
    align: "right",
    className: "tabular-nums",
    cell: ({ row }) => row.stat.mean.toFixed(1),
  },
  {
    title: "P50",
    colKey: "p50",
    width: 78,
    align: "right",
    className: "tabular-nums",
    cell: ({ row }) => row.stat.p50.toFixed(0),
  },
  {
    title: "P95",
    colKey: "p95",
    width: 78,
    align: "right",
    className: "tabular-nums monitor-metric-p95",
    cell: ({ row }) => row.stat.p95.toFixed(0),
  },
  {
    title: "P99",
    colKey: "p99",
    width: 78,
    align: "right",
    className: "tabular-nums",
    cell: ({ row }) => row.stat.p99.toFixed(0),
  },
];

export default function MonitorMetricsTable({ rows }: { rows: MonitorMetricRow[] }) {
  if (rows.length === 0) {
    return (
      <div className="monitor-table-empty" role="status">
        完成一次问答后，这里会显示原始指标与分位数。
      </div>
    );
  }
  return (
    <div
      className="monitor-table-scroll"
      role="region"
      aria-label="技术指标明细，可横向滚动"
      tabIndex={0}
    >
      <PrimaryTable
        className="monitor-metrics-table"
        data={rows}
        columns={metricColumns}
        rowKey="key"
        size="small"
        tableLayout="fixed"
        bordered={false}
        hover
      />
    </div>
  );
}

type RecentQueryRow = RecentQuery & { key: string };

const recentColumns: PrimaryTableCol<RecentQueryRow>[] = [
  { title: "时间", colKey: "ts", width: 176, cell: ({ row }) => formatTime(row.ts) },
  {
    title: "问题",
    colKey: "query",
    width: 320,
    ellipsis: true,
    cell: ({ row }) => (
      <span className="monitor-query-text" title={row.query}>
        {row.query}
      </span>
    ),
  },
  {
    title: "查找方式",
    colKey: "route",
    width: 180,
    cell: ({ row }) => (
      <Tag theme={row.route === "vector_graph_rag" ? "warning" : "primary"} variant="light">
        {routeLabel(row.route)}
      </Tag>
    ),
  },
  {
    title: "耗时",
    colKey: "duration_ms",
    width: 92,
    align: "right",
    className: "tabular-nums",
    cell: ({ row }) => `${(row.duration_ms / 1000).toFixed(2)}s`,
  },
  {
    title: "来源",
    colKey: "citations",
    width: 76,
    align: "right",
    className: "tabular-nums",
    cell: ({ row }) => row.citations,
  },
  {
    title: "状态",
    colKey: "abstained",
    width: 104,
    cell: ({ row }) =>
      row.abstained ? (
        <Tag theme="warning" variant="light">
          资料不足
        </Tag>
      ) : (
        <Tag theme="success" variant="light">
          已完成
        </Tag>
      ),
  },
];

export function LegacyRecentQueriesTable({ rows }: { rows: RecentQuery[] }) {
  if (rows.length === 0) {
    return (
      <div className="monitor-table-empty" role="status">
        暂无最近提问。完成一次问答后会在这里显示。
      </div>
    );
  }
  const data: RecentQueryRow[] = rows.map((row, index) => ({
    ...row,
    key: `${row.ts}:${row.query}:${index}`,
  }));
  return (
    <div
      className="monitor-table-scroll monitor-recent-scroll"
      role="region"
      aria-label="最近提问记录，可横向滚动"
      tabIndex={0}
    >
      <PrimaryTable
        className="monitor-recent-table"
        data={data}
        columns={recentColumns}
        rowKey="key"
        size="small"
        tableLayout="fixed"
        hover
        pagination={{
          defaultCurrent: 1,
          defaultPageSize: 6,
          total: data.length,
          showPageSize: false,
          showJumper: false,
        }}
      />
    </div>
  );
}
