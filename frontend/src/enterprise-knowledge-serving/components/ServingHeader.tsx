import { Button, Tag } from "tdesign-react";
import { RefreshIcon, ServerIcon, SettingIcon } from "tdesign-icons-react";
import type { ServingProfile, ServingSummary } from "../model/servingModel";
import { dateLabel, safeErrorMessage, shortId, StateTag } from "./servingUi";

export interface ServingHeaderProps {
  summary: ServingSummary | null;
  profile: ServingProfile | null;
  tenantLabel: string;
  datasetName?: string;
  workspaceName?: string;
  readOnly: boolean;
  onRefresh?: () => void;
  onOpenPolicy?: () => void;
}
export default function ServingHeader({
  summary,
  profile,
  tenantLabel,
  datasetName,
  workspaceName,
  readOnly,
  onRefresh,
  onOpenPolicy,
}: ServingHeaderProps) {
  const asOf = summary?.as_of ?? profile?.updated_at ?? null;
  return (
    <header className="knowledge-serving__header">
      <div className="knowledge-serving__brand-mark" aria-hidden="true">
        <ServerIcon />
      </div>
      <div className="knowledge-serving__heading">
        <span className="knowledge-serving__eyebrow">KNOWLEDGE BASE / SERVING RELIABILITY</span>
        <h1>知识服务可靠性中心</h1>
        <p>
          {datasetName ?? summary?.dataset_id ?? "当前知识库"} ·{" "}
          {workspaceName ?? profile?.workspace_id ?? "Workspace 未返回"} ·
          用证据回答当前知识库能否可靠服务。
        </p>
      </div>
      <div className="knowledge-serving__header-meta">
        <div className="knowledge-serving__header-badges">
          <Tag theme={readOnly ? "default" : "primary"} variant="light-outline">
            {readOnly ? "只读" : "服务控制面"}
          </Tag>
          {summary ? <StateTag state={summary.state} /> : null}
        </div>
        <span>
          <strong>{tenantLabel}</strong> · {shortId(summary?.dataset_id ?? profile?.dataset_id)}
        </span>
        <span>As of {dateLabel(asOf)}</span>
      </div>
      <div className="knowledge-serving__header-actions">
        <Button variant="outline" size="small" icon={<RefreshIcon />} onClick={onRefresh}>
          刷新事实
        </Button>
        <Button
          variant="outline"
          size="small"
          icon={<SettingIcon />}
          aria-disabled={readOnly}
          className={readOnly ? "knowledge-serving__button-is-disabled" : undefined}
          onClick={() => {
            if (!readOnly) onOpenPolicy?.();
          }}
        >
          调整服务策略
        </Button>
      </div>
      {safeErrorMessage(null) ? null : null}
    </header>
  );
}
