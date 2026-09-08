import type { RunEventIntegrity, RunHealthDto, RunPersistenceStatus } from "../types/runs";
import type { RunsCapability } from "../run/useRunHistory";

interface Props {
  capability: RunsCapability;
  health: RunHealthDto | null;
  liveTransportDesync: boolean;
  eventIntegrity: RunEventIntegrity;
  persistenceStatus: RunPersistenceStatus | null;
  reconnecting: boolean;
  expired: boolean;
}
interface BannerFact {
  key: string;
  tone: "info" | "warning" | "error";
  title: string;
  detail: string;
}
const CAPABILITY_FACTS: Partial<Record<RunsCapability, BannerFact>> = {
  disabled: {
    key: "disabled",
    tone: "info",
    title: "运行历史未启用",
    detail: "当前服务明确关闭了 Registry 历史能力。",
  },
  unauthorized: {
    key: "unauthorized",
    tone: "warning",
    title: "无权查看运行历史",
    detail: "当前凭据没有 Ops 历史读取权限。",
  },
  legacy: {
    key: "legacy",
    tone: "warning",
    title: "后端不支持运行历史",
    detail: "服务端没有提供当前版本的 Runs API。",
  },
  unavailable: {
    key: "unavailable",
    tone: "error",
    title: "运行历史暂不可用",
    detail: "保留上次成功数据，等待服务恢复。",
  },
};

export default function RunOpsBanners({
  capability,
  health,
  liveTransportDesync,
  eventIntegrity,
  persistenceStatus,
  reconnecting,
  expired,
}: Props) {
  const facts: BannerFact[] = [];
  const capabilityFact = CAPABILITY_FACTS[capability];
  if (capabilityFact) facts.push(capabilityFact);
  const serviceMemoryOnly =
    capability === "available" &&
    health?.enabled === true &&
    (!health.persistence.enabled ||
      health.persistence.state === "disabled" ||
      health.persistence.state === "memory_only" ||
      health.scope_stability === "boot");
  const memoryOnly =
    capability !== "disabled" &&
    (serviceMemoryOnly || persistenceStatus === "memory_only");
  if (health?.status === "degraded")
    facts.push({
      key: "health",
      tone: "warning",
      title: "Registry 服务降级",
      detail: "运行记录可读，但持久化或健康检查处于降级状态。",
    });
  if (reconnecting)
    facts.push({
      key: "reconnecting",
      tone: "info",
      title: "正在重新连接",
      detail: "界面继续显示最近一次成功快照。",
    });
  if (liveTransportDesync)
    facts.push({
      key: "transport",
      tone: "warning",
      title: "实时传输序列不同步",
      detail: "实时流存在缺口，正在用 Registry 历史补齐。",
    });
  if (eventIntegrity === "partial")
    facts.push({
      key: "integrity",
      tone: "warning",
      title: "事件历史不完整",
      detail: "当前区间与序号只代表仍可取得的历史。",
    });
  if (memoryOnly)
    facts.push({
      key: "memory",
      tone: "warning",
      title: "运行仅保存在内存",
      detail: "服务重启后当前运行历史与作用域不可恢复。",
    });
  if (persistenceStatus === "partial")
    facts.push({
      key: "partial",
      tone: "warning",
      title: "运行记录部分持久化",
      detail: "部分 canonical events 尚未写入持久存储。",
    });
  if (persistenceStatus === "unavailable")
    facts.push({
      key: "persistence",
      tone: "error",
      title: "持久化不可用",
      detail: "当前运行历史可能只存在于进程内存。",
    });
  if (expired)
    facts.push({
      key: "expired",
      tone: "error",
      title: "运行记录已过期",
      detail: "该运行已超出保留窗口，无法继续拉取完整详情。",
    });
  if (!facts.length) return null;
  return (
    <section className="run-ops-banners" aria-label="运行历史状态">
      {facts.map((fact) => (
        <div key={fact.key} className={`run-ops-banner is-${fact.tone}`} role="status">
          <strong>{fact.title}</strong>
          <span>{fact.detail}</span>
        </div>
      ))}
    </section>
  );
}
