import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Card, Col, Row, Tag, Typography } from "tdesign-react";
import {
  ChartBarIcon,
  CheckCircleIcon,
  CloseCircleIcon,
  FileIcon,
  RefreshIcon,
  TimeIcon,
} from "tdesign-icons-react";
import { fetchDocumentMetrics } from "../api/client";
import StatCard from "../components/StatCard";
import type { DocumentMetricsResponse } from "../types/rag";
import { formatDuration, STAGE_LABELS } from "../documents/documentModel";

const { Text } = Typography;

function latencyValue(count: number, value: number): string {
  return count > 0 ? formatDuration(value) : "暂无可比数据";
}

export default function DocumentIngestMonitor({ active = true }: { active?: boolean }) {
  const [data, setData] = useState<DocumentMetricsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const requestRef = useRef(0);

  const load = useCallback(async () => {
    const requestId = ++requestRef.current;
    const controller = new AbortController();
    setLoading(true);
    try {
      const value = await fetchDocumentMetrics("default", controller.signal);
      if (requestId !== requestRef.current) return;
      setData(value);
      setError(null);
    } catch (reason) {
      if (requestId !== requestRef.current || controller.signal.aborted) return;
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (requestId === requestRef.current) setLoading(false);
    }
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!active) return;
    void load();
    return () => {
      requestRef.current += 1;
    };
  }, [active, load]);

  const stages = useMemo(
    () =>
      Object.entries(data?.latency_ms.stages ?? {}).sort(
        (a, b) => (b[1].p95 ?? 0) - (a[1].p95 ?? 0),
      ),
    [data],
  );
  const maxP95 = Math.max(1, ...stages.map(([, stat]) => stat.p95));
  const totalDocuments = data?.summary.total ?? 0;
  const totalDurationCount = data?.latency_ms.total.count ?? 0;
  const missingTotalDurationCount = Math.max(0, totalDocuments - totalDurationCount);
  const legacyCount = data?.legacy_metadata_count ?? 0;
  const hasCoverageGap = missingTotalDurationCount > 0 || legacyCount > 0;

  return (
    <section className="ingest-monitor" aria-label="文档入库监控">
      <div className="section-heading-row">
        <div>
          <span className="monitor-section-kicker">KNOWLEDGE LIFELINE</span>
          <h2>文档入库监控</h2>
          <Text theme="secondary">从目录真账聚合，服务重启后仍可查看解析健康度</Text>
        </div>
        <Tag theme={hasCoverageGap ? "warning" : "success"} variant="light-outline">
          {hasCoverageGap ? "观测待补齐" : "解析观测完整"}
        </Tag>
      </div>

      {error ? (
        <Alert
          {...({ role: "alert", "aria-label": "文档监控暂时不可用" } as Record<string, unknown>)}
          className="ingest-monitor-alert"
          theme="error"
          title="文档监控暂时不可用"
          message={`无法读取入库指标（${error}）。已有页面数据不会被伪造或清空。`}
          operation={
            <Button
              tag="button"
              size="small"
              variant="outline"
              loading={loading}
              aria-label="重试文档监控"
              onClick={() => void load()}
            >
              <RefreshIcon /> 重试
            </Button>
          }
        />
      ) : null}

      {missingTotalDurationCount > 0 ? (
        <Alert
          {...({ role: "status", "aria-label": "总耗时观测不完整" } as Record<string, unknown>)}
          className="ingest-monitor-alert"
          theme="warning"
          title="总耗时观测不完整"
          message={`${missingTotalDurationCount} 篇缺少完整总耗时，不纳入入库总耗时 P95；避免把缺失值显示成 0 秒。`}
        />
      ) : null}

      {legacyCount > 0 ? (
        <Alert
          {...({ role: "status", "aria-label": "历史解析元数据缺失" } as Record<string, unknown>)}
          className="ingest-monitor-alert"
          theme="warning"
          title="历史解析元数据缺失"
          message={`${legacyCount} 篇完全缺少解析元数据，解析阶段与引擎分布可能不完整。`}
        />
      ) : null}

      <Row gutter={[12, 12]} className="monitor-summary ingest-summary">
        <Col xs={12} md={3}>
          <StatCard label="知识文档" icon={<FileIcon />} value={data?.summary.total ?? 0} />
        </Col>
        <Col xs={12} md={3}>
          <StatCard
            label="入库完成"
            tone="success"
            icon={<CheckCircleIcon />}
            value={data?.summary.completed ?? 0}
          />
        </Col>
        <Col xs={12} md={3}>
          <StatCard
            label="处理中"
            tone="warning"
            icon={<TimeIcon />}
            value={data?.summary.processing ?? 0}
          />
        </Col>
        <Col xs={12} md={3}>
          <StatCard
            label="处理失败"
            tone={data?.summary.failed ? "danger" : "success"}
            icon={<CloseCircleIcon />}
            value={data?.summary.failed ?? 0}
          />
        </Col>
        <Col xs={12} md={6}>
          <StatCard
            label="解析 P95"
            hint="只统计具有解析耗时记录的文档。"
            icon={<ChartBarIcon />}
            value={latencyValue(data?.latency_ms.parse.count ?? 0, data?.latency_ms.parse.p95 ?? 0)}
          />
        </Col>
        <Col xs={12} md={6}>
          <StatCard
            label="入库总耗时 P95"
            hint="从解析开始到索引完成；缺失 total_ms 的旧文档不计入。"
            icon={<TimeIcon />}
            value={latencyValue(data?.latency_ms.total.count ?? 0, data?.latency_ms.total.p95 ?? 0)}
          />
        </Col>
      </Row>

      <div className="ingest-monitor-grid">
        <Card
          size="small"
          title="阶段耗时瀑布 · P95"
          className={`ingest-stage-card monitor-panel-card${stages.length ? "" : " is-empty"}`}
        >
          {stages.length ? (
            <ol className="ingest-stage-list" aria-label="解析阶段耗时瀑布">
              {stages.map(([stage, stageStat], index) => (
                <li className="ingest-stage-row" key={stage}>
                  <span className="ingest-stage-index">{String(index + 1).padStart(2, "0")}</span>
                  <span className="ingest-stage-name">{STAGE_LABELS[stage] ?? stage}</span>
                  <span className="ingest-stage-track" aria-hidden="true">
                    <i style={{ width: `${Math.max(3, (stageStat.p95 / maxP95) * 100)}%` }} />
                  </span>
                  <b>{formatDuration(stageStat.p95)}</b>
                </li>
              ))}
            </ol>
          ) : (
            <div className="monitor-panel-empty" role="status">
              新文档完成入库后会显示解析、切分和索引的 P95。
            </div>
          )}
        </Card>

        <Card
          size="small"
          title="解析引擎与类型"
          className="ingest-distribution-card monitor-panel-card"
        >
          <div className="ingest-distributions">
            <section aria-label="解析引擎分布">
              <h3>解析引擎</h3>
              {data?.engine_distribution.length ? (
                data.engine_distribution.map((item) => (
                  <div key={item.name}>
                    <span>{item.name}</span>
                    <b>{item.value}</b>
                  </div>
                ))
              ) : (
                <Text theme="secondary">暂无引擎数据</Text>
              )}
            </section>
            <section aria-label="文档类型分布">
              <h3>文档类型</h3>
              {data?.type_distribution.length ? (
                data.type_distribution.map((item) => (
                  <div key={item.name}>
                    <span>{item.name}</span>
                    <b>{item.value}</b>
                  </div>
                ))
              ) : (
                <Text theme="secondary">暂无类型数据</Text>
              )}
            </section>
          </div>
        </Card>

        <Card size="small" title="慢文档排行" className="ingest-slow-card monitor-panel-card">
          {data?.slow_documents.length ? (
            <ol className="ingest-slow-list">
              {data.slow_documents.slice(0, 6).map((item, index) => (
                <li key={item.document_id}>
                  <span className="rank">{index + 1}</span>
                  <span title={item.name}>{item.name}</span>
                  <b>{formatDuration(item.total_ms)}</b>
                </li>
              ))}
            </ol>
          ) : (
            <div className="monitor-panel-empty" role="status">
              暂无具有完整总耗时的慢文档。
            </div>
          )}
        </Card>

        <Card
          size="small"
          title="最近解析失败"
          className="ingest-failure-card monitor-panel-card"
          actions={
            <Tag theme={data?.recent_failures.length ? "danger" : "success"} variant="light">
              {data?.recent_failures.length ?? 0} 项
            </Tag>
          }
        >
          {data?.recent_failures.length ? (
            <ul className="ingest-failure-list">
              {data.recent_failures.slice(0, 6).map((item) => (
                <li key={item.document_id}>
                  <strong title={item.name}>{item.name}</strong>
                  <span>{item.message}</span>
                </li>
              ))}
            </ul>
          ) : (
            <div className="monitor-panel-empty" role="status">
              当前没有需要处理的解析失败。
            </div>
          )}
        </Card>
      </div>
    </section>
  );
}
