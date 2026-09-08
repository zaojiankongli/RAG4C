import { useEffect, useRef, useState, type ReactNode } from "react";
import { Avatar, Button, Input, Select, Tag } from "tdesign-react";
import type { InputRef } from "tdesign-react";
import { HistoryIcon, NotificationIcon, SearchIcon, SecuredIcon } from "tdesign-icons-react";

export type WorkspaceHealthTone = "success" | "warning" | "danger" | "default";
export type WorkspaceSelectorStatus = "idle" | "loading" | "ready" | "error";

export interface WorkspaceScopeOption {
  value: string;
  label: string;
  environment?: string;
}

export interface WorkspaceScopeBarProps {
  organizationLabel: string;
  knowledgeBaseLabel: string;
  environmentLabel: string;
  healthLabel: string;
  compactHealthLabel?: string;
  healthTone?: WorkspaceHealthTone;
  actorLabel: string;
  actorRole: string;
  workspaceValue?: string;
  workspaceOptions?: WorkspaceScopeOption[];
  workspaceStatus?: WorkspaceSelectorStatus;
  onWorkspaceChange?: (workspaceId: string) => void;
  searchPlaceholder?: string;
  onSearch?: (value: string) => void;
  notificationControl?: ReactNode;
  onOpenNotifications?: () => void;
  onOpenAudit?: () => void;
}

/**
 * 企业控制台的全局工作区上下文。
 *
 * Workspace 下拉项只能来自服务端 Workspace authority；加载失败时明确禁用，
 * 不用本地 Dataset 名称或演示数据拼装假的 Workspace。
 */
export default function WorkspaceScopeBar({
  organizationLabel,
  knowledgeBaseLabel,
  environmentLabel,
  healthLabel,
  compactHealthLabel = healthLabel,
  healthTone = "default",
  actorLabel,
  actorRole,
  workspaceValue = "",
  workspaceOptions,
  workspaceStatus = workspaceOptions ? "ready" : "idle",
  onWorkspaceChange,
  searchPlaceholder = "搜索知识、文档或问答",
  onSearch,
  notificationControl,
  onOpenNotifications,
  onOpenAudit,
}: WorkspaceScopeBarProps) {
  const [searchValue, setSearchValue] = useState("");
  const inputRef = useRef<InputRef>(null);
  const workspaceSelectRef = useRef<HTMLLabelElement>(null);
  const hasWorkspaceSelector = workspaceOptions !== undefined || workspaceStatus !== "idle";

  // TDesign Input 1.x 将未知 aria 属性放在外层；把名称和快捷键同步到真正的 input。
  useEffect(() => {
    const input = inputRef.current?.inputElement;
    input?.setAttribute("aria-label", "全局知识搜索");
    input?.setAttribute("aria-keyshortcuts", "Control+K Meta+K");

    const focusGlobalSearch = (event: KeyboardEvent) => {
      if (event.altKey || (!event.ctrlKey && !event.metaKey) || event.key.toLowerCase() !== "k") {
        return;
      }
      event.preventDefault();
      inputRef.current?.focus();
    };

    window.addEventListener("keydown", focusGlobalSearch);
    return () => window.removeEventListener("keydown", focusGlobalSearch);
  }, []);

  useEffect(() => {
    const input = workspaceSelectRef.current?.querySelector("input");
    input?.setAttribute("role", "combobox");
    input?.setAttribute("aria-label", "当前 Workspace");
    input?.setAttribute("aria-haspopup", "listbox");
  }, [workspaceOptions, workspaceStatus, workspaceValue]);

  const submitSearch = (value: string) => {
    onSearch?.(value.trim());
  };

  const actorInitial = actorLabel.trim().slice(0, 1) || "企";
  const selectorPlaceholder =
    workspaceStatus === "loading"
      ? "Workspace 加载中"
      : workspaceStatus === "error"
        ? "Workspace 不可用"
        : "未返回 Workspace";

  return (
    <section
      className={`enterprise-workspace-scope${hasWorkspaceSelector ? " has-workspace-selector" : ""}`}
      role="region"
      aria-label="当前企业工作区"
    >
      <div className="enterprise-workspace-scope__identity">
        <span className="enterprise-workspace-scope__mark" aria-hidden="true">
          <SecuredIcon />
        </span>
        <div className="enterprise-workspace-scope__scope-copy">
          <div className="enterprise-workspace-scope__organization">{organizationLabel}</div>
          <div className="enterprise-workspace-scope__knowledge-base">
            <span>{knowledgeBaseLabel}</span>
            <span className="enterprise-workspace-scope__divider" aria-hidden="true" />
            <span className="enterprise-workspace-scope__environment">{environmentLabel}</span>
          </div>
        </div>
        <Tag
          className={`enterprise-workspace-scope__health is-${healthTone}`}
          theme={healthTone}
          variant="light-outline"
          shape="round"
          size="small"
          aria-label={`服务健康状态：${healthLabel}`}
        >
          <span className="enterprise-workspace-scope__health-dot" aria-hidden="true" />
          <span className="enterprise-workspace-scope__health-label">{healthLabel}</span>
          {compactHealthLabel !== healthLabel ? (
            <span className="enterprise-workspace-scope__health-label-compact" aria-hidden="true">
              {compactHealthLabel}
            </span>
          ) : null}
        </Tag>
      </div>

      {hasWorkspaceSelector ? (
        <label ref={workspaceSelectRef} className="enterprise-workspace-scope__selector">
          <span>Workspace</span>
          <Select
            value={workspaceValue || undefined}
            options={(workspaceOptions ?? []).map((option) => ({
              value: option.value,
              label: option.environment ? `${option.label} · ${option.environment}` : option.label,
            }))}
            loading={workspaceStatus === "loading"}
            disabled={workspaceStatus !== "ready" || !workspaceOptions?.length}
            inputProps={{
              name: "enterprise-workspace",
              autocomplete: "off",
            }}
            placeholder={selectorPlaceholder}
            empty={selectorPlaceholder}
            onChange={(value) => onWorkspaceChange?.(String(value))}
          />
        </label>
      ) : null}

      <div className="enterprise-workspace-scope__search">
        <Input
          ref={inputRef}
          type="search"
          value={searchValue}
          placeholder={searchPlaceholder}
          prefixIcon={<SearchIcon />}
          clearable
          onChange={(value) => setSearchValue(value)}
          onEnter={(value) => submitSearch(value)}
        />
        <kbd aria-hidden="true">Ctrl / ⌘ K</kbd>
      </div>

      <div className="enterprise-workspace-scope__tools">
        {onOpenAudit ? (
          <Button
            className="enterprise-workspace-scope__icon-button"
            theme="default"
            variant="text"
            shape="square"
            icon={<HistoryIcon />}
            aria-label="打开审计日志"
            title="审计日志"
            onClick={onOpenAudit}
          />
        ) : null}
        {notificationControl ??
          (onOpenNotifications ? (
            <Button
              className="enterprise-workspace-scope__icon-button"
              theme="default"
              variant="text"
              shape="square"
              icon={<NotificationIcon />}
              aria-label="打开通知中心"
              title="通知中心"
              onClick={onOpenNotifications}
            />
          ) : null)}
        <div
          className="enterprise-workspace-scope__actor"
          aria-label={`当前操作者：${actorLabel}，${actorRole}`}
        >
          <Avatar className="enterprise-workspace-scope__avatar" size="32px">
            {actorInitial}
          </Avatar>
          <span className="enterprise-workspace-scope__actor-copy">
            <strong>{actorLabel}</strong>
            <small>{actorRole}</small>
          </span>
        </div>
      </div>
    </section>
  );
}
