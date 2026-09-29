import type { ComponentStatus, HealthInfo } from "../types/rag";

export type ServiceState =
  "checking" | "unconfigured" | "degraded" | "ready" | "offline" | "down" | "unknown";
export type ServiceTone = "pending" | "warning" | "success" | "danger";
export interface ServiceIssue {
  key: string;
  label: string;
  status: ComponentStatus;
  statusLabel: string;
}
interface StatusInput {
  online: boolean | null;
  checking: boolean;
  health: HealthInfo | null;
}
interface ServiceStatus {
  state: ServiceState;
  label: string;
  tone: ServiceTone;
  description: string;
  issues: ServiceIssue[];
}

const NAMES: Record<string, string> = {
  milvus: "向量检索",
  embedder: "向量模型",
  reranker: "重排模型",
  llm: "回答模型",
};
const STATUS_LABELS: Record<ComponentStatus, string> = {
  unconfigured: "待配置",
  error: "连接异常",
  empty: "暂无数据",
  ok: "已就绪",
};
const STATES: Record<ServiceState, Omit<ServiceStatus, "state" | "issues">> = {
  checking: {
    label: "检查中",
    tone: "pending",
    description: "正在检查连接和服务配置，完成后会在这里更新状态。",
  },
  unconfigured: {
    label: "待配置",
    tone: "warning",
    description: "以下能力尚未配置。补全配置后重新检查，确认可用时状态会自动更新。",
  },
  degraded: {
    label: "部分可用",
    tone: "warning",
    description: "部分能力尚未就绪，具体影响取决于当前操作所需的服务。",
  },
  ready: {
    label: "已就绪",
    tone: "success",
    description: "服务检查通过，可按当前权限使用真实资料。",
  },
  offline: {
    label: "离线演示",
    tone: "warning",
    description: "后端服务尚未连接。问答可展示已标记的演示内容；企业管理功能仍需连接真实服务。",
  },
  down: {
    label: "连接异常",
    tone: "danger",
    description: "核心服务暂不可用，请检查连接与配置后重试。",
  },
  unknown: {
    label: "状态未知",
    tone: "pending",
    description: "尚未获得完整的服务检查结果，请重新检查后确认。",
  },
};

/** Project server facts without pretending a saved config or demo fixture is ready. */
export function getServiceStatus({ online, checking, health }: StatusInput): ServiceStatus {
  const issues: ServiceIssue[] =
    online === true && !checking
      ? Object.entries(health?.components ?? {})
          .filter(([, value]) => value && value.status !== "ok")
          .map(([key, value]) => ({
            key,
            label: NAMES[key] ?? key,
            status: value.status,
            statusLabel: STATUS_LABELS[value.status] ?? "状态未知",
          }))
      : [];
  const state: ServiceState =
    checking || online === null
      ? "checking"
      : online === false
        ? "offline"
        : !health || !["ok", "down", "degraded"].includes(health.status)
          ? "unknown"
          : health.status === "down"
            ? "down"
            : issues.some((issue) => issue.status === "unconfigured")
              ? "unconfigured"
              : health.status === "degraded" || issues.length > 0
                ? "degraded"
                : "ready";
  return { state, ...STATES[state], issues };
}
