import { Button } from "../ui/index";
import { SettingOutlined } from "../ui/icons";
import { readKnowledgeActorToken } from "../knowledge/workspaceScope";

export interface AuthRecoveryHintProps {
  /** 覆盖默认标题 */
  title?: string;
  /** 覆盖默认说明 */
  description?: string;
  /** 附加重试按钮（调用方提供） */
  onRetry?: () => void;
  retryLabel?: string;
  compact?: boolean;
}

/**
 * 鉴权门禁恢复提示：告诉操作员「缺什么、去哪补」，而不是只抛后端错误串。
 */
export default function AuthRecoveryHint({
  title = "缺少 KnowledgeOps 身份凭据",
  description = "当前浏览器未配置有效的 Actor Bearer。请在工作区/系统设置中完成身份接入，或联系租户管理员签发凭据后重试。本页不会使用演示数据填充。",
  onRetry,
  retryLabel = "重新连接并重试",
  compact = false,
}: AuthRecoveryHintProps) {
  const hasToken = Boolean(readKnowledgeActorToken().trim());
  return (
    <div
      className={"auth-recovery-hint" + (compact ? " is-compact" : "")}
      aria-label="鉴权恢复提示"
    >
      <p className="auth-recovery-title">{title}</p>
      <p className="auth-recovery-desc">
        {hasToken
          ? "已检测到本地 Actor Token，但仍被服务端拒绝（可能过期、租户不匹配或权限不足）。请刷新凭据或联系管理员。"
          : description}
      </p>
      <div className="auth-recovery-actions">
        <Button
          tag="button"
          type="button"
          aria-label="打开系统设置"
          className="auth-recovery-control-min-h"
          onClick={() => {
            if (typeof window !== "undefined") {
              window.location.hash = "#/config";
            }
          }}
        >
          <SettingOutlined /> 打开系统设置
        </Button>
        {onRetry ? (
          <Button
            tag="button"
            type="button"
            aria-label={retryLabel}
            className="auth-recovery-control-min-h"
            onClick={() => onRetry()}
          >
            {retryLabel}
          </Button>
        ) : null}
      </div>
    </div>
  );
}

export function isAuthError(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const e = error as { status?: number; kind?: string; message?: string };
  const status = e.status;
  const message = String(e.message || "");
  return (
    status === 401 ||
    status === 403 ||
    /bearer|凭据|unauthorized|forbidden|401|403/i.test(message)
  );
}
