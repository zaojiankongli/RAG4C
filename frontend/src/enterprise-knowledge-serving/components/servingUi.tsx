import type { ReactNode } from "react";
import { Button } from "tdesign-react";
import { CheckCircleIcon, ErrorCircleIcon, InfoCircleIcon, RefreshIcon } from "tdesign-icons-react";
import {
  safeServingDisplayText,
  type ServingOverallState,
  type ServingStageCode,
  type ServingStageState,
} from "../model/servingModel";
import type { ServingLoadStatus } from "../hooks/useEnterpriseKnowledgeServing";
import { Alert, Tag } from "../../ui";

export const STAGE_LABELS: Record<ServingStageCode, string> = {
  source: "SOURCE",
  parse: "PARSE",
  chunk: "CHUNK",
  index: "INDEX",
  serve: "SERVE",
};
export const STAGE_DESCRIPTIONS: Record<ServingStageCode, string> = {
  source: "来源连接与同步",
  parse: "解析尝试与文档版本",
  chunk: "Chunk Head 与 Revision",
  index: "向量 / 图谱投影",
  serve: "Release 与检索服务",
};
export const STAGE_STATE_LABELS: Record<ServingStageState, string> = {
  ready: "就绪",
  lagging: "有延迟",
  blocked: "已阻断",
  missing: "缺失",
  unavailable: "不可用",
};
export const OVERALL_STATE_LABELS: Record<ServingOverallState, string> = {
  ready: "就绪",
  degraded: "降级",
  blocked: "阻断",
  unavailable: "不可用",
};
export function stateLabel(state: ServingOverallState): string {
  return "服务状态：" + OVERALL_STATE_LABELS[state];
}
export function stateTheme(state: ServingOverallState): "success" | "warning" | "danger" {
  return state === "ready" ? "success" : state === "degraded" ? "warning" : "danger";
}
export function stageTheme(state: ServingStageState): "success" | "warning" | "danger" | "default" {
  return state === "ready"
    ? "success"
    : state === "lagging"
      ? "warning"
      : state === "blocked" || state === "unavailable"
        ? "danger"
        : "default";
}
export function safeErrorMessage(value: Error | string | null | undefined): string | null {
  if (!value) return null;
  return safeServingDisplayText(value instanceof Error ? value.message : value);
}
export function dateLabel(value: string | null | undefined): string {
  if (!value) return "未返回";
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return "未返回";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}
export function numberLabel(value: number | null | undefined): string {
  return value == null ? "未返回" : new Intl.NumberFormat("zh-CN").format(value);
}
export function shortDigest(value: string | null | undefined): string {
  if (!value) return "未返回";
  return value.length > 26 ? value.slice(0, 12) + "…" + value.slice(-8) : value;
}
export function shortId(value: string | null | undefined): string {
  if (!value) return "未返回";
  return value.length > 28 ? value.slice(0, 13) + "…" + value.slice(-9) : value;
}

export function StateTag({ state }: { state: ServingOverallState }) {
  return (
    <Tag theme={stateTheme(state)} variant="light-outline" className="knowledge-serving__state-tag">
      {OVERALL_STATE_LABELS[state]}
    </Tag>
  );
}
export function StageTag({ state }: { state: ServingStageState }) {
  return (
    <Tag theme={stageTheme(state)} variant="light-outline">
      {STAGE_STATE_LABELS[state]}
    </Tag>
  );
}

export function LoadState({
  status,
  title,
  description,
  onRetry,
}: {
  status: ServingLoadStatus;
  title?: string;
  description?: string;
  onRetry?: () => void;
}) {
  if (status === "loading")
    return (
      <div className="knowledge-serving__inline-state" role="status" aria-live="polite">
        <span className="knowledge-serving__spinner" aria-hidden="true" />
        {title ?? "正在读取权威事实…"}
      </div>
    );
  if (status === "unavailable" || status === "error")
    return (
      <div className="knowledge-serving__state-wrap">
        <Alert
          theme="error"
          title={title ?? "权威事实不可用"}
          message={description ?? "当前无法读取知识服务可靠性事实。"}
          operation={
            onRetry ? (
              <Button variant="text" theme="primary" icon={<RefreshIcon />} onClick={onRetry}>
                重试
              </Button>
            ) : undefined
          }
        />
      </div>
    );
  if (status === "partial")
    return (
      <div className="knowledge-serving__state-wrap">
        <Alert
          theme="warning"
          title={title ?? "部分服务快照可用"}
          message={description ?? "部分权威记录未通过严格校验，已隐藏。"}
        />
      </div>
    );
  if (status === "empty")
    return (
      <div className="knowledge-serving__empty" role="status">
        <InfoCircleIcon aria-hidden="true" />
        <strong>{title ?? "暂无服务快照"}</strong>
        <span>{description ?? "当前 Dataset 尚未产生可展示的服务可靠性快照。"}</span>
      </div>
    );
  return null;
}

export function SafeError({ value }: { value: string | null }) {
  return value ? (
    <span className="knowledge-serving__safe-error">
      <ErrorCircleIcon aria-hidden="true" />
      {value}
    </span>
  ) : (
    <span className="knowledge-serving__safe-ok">
      <CheckCircleIcon aria-hidden="true" />
      无阻断
    </span>
  );
}
export function Fact({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="knowledge-serving__fact">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
