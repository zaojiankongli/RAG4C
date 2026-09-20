import { Card } from "tdesign-react";
import {
  ChartLineIcon,
  CheckCircleIcon,
  FileSearchIcon,
  HardDiskStorageIcon,
  LayersIcon,
  TimeIcon,
} from "tdesign-icons-react";
import type { ServingSummary } from "../model/servingModel";
import { numberLabel, shortId, StateTag } from "./servingUi";

export default function GovernanceEvidenceStrip({ summary }: { summary: ServingSummary | null }) {
  const metrics = [
    {
      key: "serving",
      label: "Serving state",
      value: summary ? <StateTag state={summary.state} /> : "未返回",
      detail: summary?.snapshot_id ? "Snapshot " + shortId(summary.snapshot_id) : "等待首个快照",
      icon: <ChartLineIcon />,
      tone: summary?.state ?? "unavailable",
    },
    {
      key: "generation",
      label: "Serving generation",
      value: summary?.serving_generation == null ? "未返回" : "G" + summary.serving_generation,
      detail: "当前服务代次",
      icon: <LayersIcon />,
      tone: "primary",
    },
    {
      key: "source",
      label: "Source freshness",
      value:
        summary?.source_count == null
          ? "未返回"
          : numberLabel(summary.ready_source_count) + " / " + numberLabel(summary.source_count),
      detail: summary?.stale_source_count
        ? numberLabel(summary.stale_source_count) + " 个来源有延迟"
        : "所有来源均为最新事实",
      icon: <HardDiskStorageIcon />,
      tone: summary?.stale_source_count ? "warning" : "success",
    },
    {
      key: "projection",
      label: "Projection lag",
      value:
        summary?.pending_index_count == null ? "未返回" : numberLabel(summary.pending_index_count),
      detail: "待索引记录",
      icon: <TimeIcon />,
      tone: summary?.pending_index_count ? "warning" : "success",
    },
    {
      key: "documents",
      label: "Active documents",
      value: numberLabel(summary?.active_document_count),
      detail: summary?.failed_document_count
        ? numberLabel(summary.failed_document_count) + " 个解析失败"
        : "未发现解析失败",
      icon: <FileSearchIcon />,
      tone: summary?.failed_document_count ? "warning" : "success",
    },
    {
      key: "certification",
      label: "Quality evidence",
      value: summary?.current_certification_id ? "已认证" : "未返回",
      detail: summary?.current_certification_id
        ? shortId(summary.current_certification_id)
        : "质量认证权威未返回",
      icon: <CheckCircleIcon />,
      tone: summary?.current_certification_id ? "success" : "default",
    },
  ];
  return (
    <section className="knowledge-serving__evidence-strip" aria-label="Governance Evidence Strip">
      <div className="knowledge-serving__section-heading">
        <div>
          <span className="knowledge-serving__eyebrow">GOVERNANCE EVIDENCE STRIP</span>
          <h2>服务可靠性事实</h2>
        </div>
        <span className="knowledge-serving__evidence-caption">只读聚合 · 不复制来源权威</span>
      </div>
      <div className="knowledge-serving__evidence-grid">
        {metrics.map((metric) => (
          <Card
            key={metric.key}
            size="small"
            className={
              "knowledge-serving__evidence-card knowledge-serving__evidence-card--" + metric.tone
            }
            bordered
            hoverShadow
          >
            <div className="knowledge-serving__evidence-icon">{metric.icon}</div>
            <div className="knowledge-serving__evidence-copy">
              <span>{metric.label}</span>
              <strong>{metric.value}</strong>
              <small>{metric.detail}</small>
            </div>
          </Card>
        ))}
      </div>
    </section>
  );
}
