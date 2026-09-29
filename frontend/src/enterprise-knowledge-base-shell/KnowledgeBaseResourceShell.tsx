import {
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ComponentProps,
  type ReactNode,
} from "react";
import { Button, Drawer, Select, Tabs, Tag } from "tdesign-react";
import { InfoCircleIcon } from "tdesign-icons-react";
import { fetchEnterpriseKnowledgeBaseDetail } from "../enterprise-knowledge-base/api/enterpriseKnowledgeBaseApi";
import type { EnterpriseKnowledgeBaseDetail } from "../enterprise-knowledge-base/enterpriseKnowledgeBaseModel";
import type { EnterpriseScope } from "../enterprise-admin/model";
import { useOptionalKnowledgeWorkspace } from "../knowledge/KnowledgeWorkspaceContext";
import { readKnowledgeActorToken } from "../knowledge/workspaceScope";
import {
  KNOWLEDGE_BASE_RESOURCE_SECTIONS,
  type KnowledgeBaseResourceSection,
} from "./knowledgeBaseResourceRoute";
import { useKnowledgeBaseDrawerCoordinator } from "./KnowledgeBaseDrawerCoordinatorContext";
import "./knowledge-base-resource-shell.css";

export interface KnowledgeBaseResourceShellProps {
  active: boolean;
  section: KnowledgeBaseResourceSection;
  children: ReactNode;
  datasetId?: string | null;
  tenantId?: string;
  actorToken?: string;
  onSectionChange?: (section: KnowledgeBaseResourceSection, trigger: HTMLElement) => boolean | void;
}

const RESOURCE_SELECT_INPUT_PROPS = {
  role: "combobox",
  "aria-label": "知识库资源",
  "aria-haspopup": "listbox",
} as ComponentProps<typeof Select>["inputProps"] & {
  role: "combobox";
  "aria-label": string;
  "aria-haspopup": "listbox";
};

const RESOURCE_ITEMS: ReadonlyArray<{
  value: KnowledgeBaseResourceSection;
  label: string;
}> = [
  { value: "overview", label: "概览" },
  { value: "documents", label: "文档" },
  { value: "taxonomy", label: "目录与标签" },
  { value: "sources", label: "数据来源" },
  { value: "serving", label: "服务状态" },
  { value: "governance", label: "内容治理" },
  { value: "releases", label: "发布版本" },
];

function useCompactViewport(): boolean {
  const [compact, setCompact] = useState(false);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 768px)");
    const update = () => setCompact(media.matches);
    update();
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, []);
  return compact;
}

function statusLabel(status: string | null | undefined): string {
  if (status === "active") return "运行中";
  if (status === "archived") return "已归档";
  if (status === "disabled") return "已停用";
  return status?.trim() || "未返回";
}

function statusTheme(status: string | null | undefined): "success" | "warning" | "default" {
  if (status === "active") return "success";
  if (status === "archived" || status === "disabled") return "warning";
  return "default";
}

function trapDrawerFocus(
  event: Pick<globalThis.KeyboardEvent, "key" | "shiftKey" | "preventDefault">,
  body: HTMLElement | null,
) {
  if (event.key !== "Tab" || !body) return;
  const root = body.closest(".t-drawer") ?? body;
  const focusable = Array.from(
    root.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((item) => item.offsetParent !== null || item === document.activeElement);
  if (!focusable.length) {
    event.preventDefault();
    body.focus();
    return;
  }
  const first = focusable[0]!;
  const last = focusable[focusable.length - 1]!;
  if (event.shiftKey && (document.activeElement === first || document.activeElement === body)) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function revisionLabel(value: number | null | undefined): string {
  return value == null ? "未返回" : `R${value}`;
}

function shortId(value: string | null | undefined): string {
  const normalized = value?.trim() ?? "";
  if (!normalized) return "未返回";
  return normalized.length > 32
    ? `${normalized.slice(0, 14)}…${normalized.slice(-10)}`
    : normalized;
}

function authorityScope(
  workspace: ReturnType<typeof useOptionalKnowledgeWorkspace>,
  props: Pick<KnowledgeBaseResourceShellProps, "datasetId" | "tenantId" | "actorToken">,
): EnterpriseScope | null {
  const tenantId = props.tenantId?.trim() || workspace?.scope.tenantId.trim() || "";
  const datasetId = props.datasetId?.trim() || workspace?.datasetId.trim() || "";
  const actorToken = props.actorToken?.trim() || readKnowledgeActorToken();
  if (!tenantId || !datasetId || !actorToken) return null;
  return { tenantId, datasetId, actorToken };
}

export default function KnowledgeBaseResourceShell({
  active,
  section,
  children,
  datasetId,
  tenantId,
  actorToken,
  onSectionChange,
}: KnowledgeBaseResourceShellProps) {
  const workspace = useOptionalKnowledgeWorkspace();
  const compact = useCompactViewport();
  const coordinator = useKnowledgeBaseDrawerCoordinator();
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const factsTriggerRef = useRef<HTMLButtonElement | null>(null);
  const factsDialogRef = useRef<HTMLDivElement | null>(null);
  const [authority, setAuthority] = useState<EnterpriseKnowledgeBaseDetail | null>(null);
  const [authorityState, setAuthorityState] = useState<"idle" | "loading" | "ready" | "error">(
    "idle",
  );
  const [factsOpen, setFactsOpen] = useState(false);
  // 这里只有一份面板：内容随 section 变，所以**所有** tab 的 aria-controls 都指向同一个
  // panelId。若按 item.value 各造一个，未选中的 tab 会指向不存在的元素——那不是修好，
  // 而是把 no-aria-controls 换成另一种坏法（视觉门禁已有 dangling-aria-controls 判定）。
  // uid 用 useId 兜住同页多实例（App.tsx 里有两处 shell 用法）；React 的 id 含冒号，
  // 冒号在 CSS 选择器里要转义，而这里只经 getElementById/aria-controls 使用，去掉更省心。
  const uid = useId().replace(/[^A-Za-z0-9_-]/g, "");
  const panelId = `kbs-resource-panel-${uid}`;
  const resourceTabId = (value: string) => `kbs-resource-tab-${uid}-${value}`;
  const scope = useMemo(
    () => authorityScope(workspace, { datasetId, tenantId, actorToken }),
    [actorToken, datasetId, tenantId, workspace],
  );
  const verified = workspace ? workspace.workspaceScopeStatus === "verified" : Boolean(scope);
  const requestedDatasetId =
    scope?.datasetId ?? datasetId?.trim() ?? workspace?.datasetId.trim() ?? "";
  const authorityScopeKey = [
    active ? "active" : "inactive",
    verified ? "verified" : "unverified",
    scope?.tenantId ?? "",
    requestedDatasetId,
    scope?.actorToken ?? "",
  ].join("\u0000");
  const previousAuthorityScopeKeyRef = useRef(authorityScopeKey);
  const authorityScopeChanged = previousAuthorityScopeKeyRef.current !== authorityScopeKey;

  useLayoutEffect(() => {
    previousAuthorityScopeKeyRef.current = authorityScopeKey;
    if (!active) {
      setAuthority(null);
      setAuthorityState("idle");
      setFactsOpen(false);
      return;
    }
    if (!scope || !verified) {
      setAuthority(null);
      setAuthorityState(scope ? "loading" : "error");
      setFactsOpen(false);
      return;
    }
    setAuthority(null);
    setAuthorityState("loading");
    setFactsOpen(false);
  }, [active, authorityScopeKey, scope, verified]);

  useEffect(() => {
    if (!active || !scope || !verified) return;
    const controller = new AbortController();
    void fetchEnterpriseKnowledgeBaseDetail(scope, requestedDatasetId, {
      signal: controller.signal,
    })
      .then((detail) => {
        if (controller.signal.aborted) return;
        if (detail.knowledge_base.id !== requestedDatasetId) {
          throw new Error("Registry Dataset authority does not match the requested Dataset");
        }
        setAuthority(detail);
        setAuthorityState("ready");
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setAuthority(null);
          setAuthorityState("error");
        }
      });
    return () => controller.abort();
  }, [active, authorityScopeKey, requestedDatasetId, scope, verified]);

  useEffect(() => {
    if (coordinator.coordinated && factsOpen && coordinator.activeDrawer !== "facts") {
      setFactsOpen(false);
    }
  }, [coordinator.activeDrawer, coordinator.coordinated, factsOpen]);

  useEffect(() => {
    if (!factsOpen) return;
    const timer = window.setTimeout(() => factsDialogRef.current?.focus(), 360);
    return () => window.clearTimeout(timer);
  }, [factsOpen]);

  useEffect(() => {
    if (!factsOpen) return;
    const handleTab = (event: globalThis.KeyboardEvent) =>
      trapDrawerFocus(event, factsDialogRef.current);
    document.addEventListener("keydown", handleTab);
    return () => document.removeEventListener("keydown", handleTab);
  }, [factsOpen]);

  useLayoutEffect(() => {
    if (!active) return;
    const timer = window.setTimeout(() => headingRef.current?.focus({ preventScroll: true }), 0);
    return () => window.clearTimeout(timer);
  }, [active]);

  if (!active) return null;

  const visibleAuthority = authorityScopeChanged ? null : authority;
  const knowledgeBase = visibleAuthority?.knowledge_base ?? null;
  const displayName = knowledgeBase?.name || requestedDatasetId || "Knowledge Base";
  const lifecycle = knowledgeBase?.status ?? null;
  const owner = knowledgeBase?.owning_workspace ?? null;
  const authorityLabel =
    authorityState === "ready"
      ? "Registry 已验证"
      : authorityState === "loading"
        ? "权威事实读取中"
        : "权威事实不可用";
  const requestSectionChange = (next: KnowledgeBaseResourceSection, target: HTMLElement) => {
    if (next === section) return;
    const result = onSectionChange?.(next, target);
    if (result === false) target.focus({ preventScroll: true });
  };

  const openFacts = () => {
    setFactsOpen(true);
    coordinator.openDrawer("facts");
  };
  const closeFacts = (restoreFocus: boolean) => {
    setFactsOpen(false);
    coordinator.closeDrawer("facts");
    if (restoreFocus) {
      window.setTimeout(() => factsTriggerRef.current?.focus({ preventScroll: true }), 0);
    }
  };
  const handleResourceTabKeyDown = (
    event: React.KeyboardEvent<HTMLButtonElement>,
    value: KnowledgeBaseResourceSection,
  ) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      requestSectionChange(value, event.currentTarget);
      return;
    }
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const currentIndex = RESOURCE_ITEMS.findIndex((item) => item.value === value);
    const nextIndex =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? RESOURCE_ITEMS.length - 1
          : (currentIndex + (event.key === "ArrowRight" ? 1 : -1) + RESOURCE_ITEMS.length) %
            RESOURCE_ITEMS.length;
    const next = RESOURCE_ITEMS[nextIndex]!;
    const tabs = event.currentTarget
      .closest('[role="tablist"]')
      ?.querySelectorAll<HTMLButtonElement>(".knowledge-base-resource-shell__tab-trigger");
    const target = tabs?.[nextIndex] ?? event.currentTarget;
    target.focus();
    requestSectionChange(next.value, target);
  };

  return (
    <section
      className="knowledge-base-resource-shell"
      data-resource-shell-active="true"
      aria-label="Knowledge Base 资源工作区"
    >
      <header className="knowledge-base-resource-shell__header">
        <div className="knowledge-base-resource-shell__identity">
          <h1 ref={headingRef} tabIndex={-1}>
            {displayName}
          </h1>
          <div
            className="knowledge-base-resource-shell__status"
            role="status"
            aria-label="知识库状态"
          >
            <Tag theme={statusTheme(lifecycle)} variant="light-outline">
              {statusLabel(lifecycle)}
            </Tag>
            {authorityState !== "ready" && <span>{authorityLabel}</span>}
          </div>
        </div>
        <Button
          ref={factsTriggerRef}
          className="knowledge-base-resource-shell__facts-trigger"
          variant="text"
          icon={<InfoCircleIcon />}
          onClick={openFacts}
          aria-haspopup="dialog"
          aria-expanded={factsOpen}
        >
          查看权威事实
        </Button>
      </header>

      <div className="knowledge-base-resource-shell__navigation">
        {!compact ? (
          <div role="tablist" aria-label="知识库资源导航">
            <Tabs value={section} theme="normal">
              {RESOURCE_ITEMS.map((item) => (
                <Tabs.TabPanel
                  key={item.value}
                  value={item.value}
                  label={
                    <button
                      type="button"
                      role="tab"
                      id={resourceTabId(item.value)}
                      aria-controls={panelId}
                      aria-selected={section === item.value}
                      tabIndex={section === item.value ? 0 : -1}
                      className="knowledge-base-resource-shell__tab-trigger"
                      onClick={(event) => {
                        event.stopPropagation();
                        requestSectionChange(item.value, event.currentTarget);
                      }}
                      onKeyDown={(event) => handleResourceTabKeyDown(event, item.value)}
                    >
                      {item.label}
                    </button>
                  }
                />
              ))}
            </Tabs>
          </div>
        ) : (
          <label className="knowledge-base-resource-shell__select-field">
            <span>知识库资源</span>
            <Select
              value={section}
              options={RESOURCE_ITEMS.map(({ value, label }) => ({ value, label }))}
              inputProps={RESOURCE_SELECT_INPUT_PROPS}
              onChange={(value) => {
                const target =
                  document.activeElement instanceof HTMLElement
                    ? document.activeElement
                    : document.body;
                requestSectionChange(String(value) as KnowledgeBaseResourceSection, target);
              }}
            />
          </label>
        )}
      </div>

      <div
        className="knowledge-base-resource-shell__content"
        {...(compact
          ? {}
          : {
              role: "tabpanel" as const,
              id: panelId,
              "aria-labelledby": resourceTabId(section),
            })}
      >
        {children}
      </div>

      <Drawer
        visible={factsOpen}
        header="Knowledge Base 权威事实"
        destroyOnClose
        footer={false}
        aria-label="Knowledge Base 权威事实"
        closeBtn={
          <Button variant="text" onClick={() => closeFacts(true)}>
            关闭
          </Button>
        }
        onClose={() => closeFacts(true)}
        className="knowledge-base-resource-shell__facts-drawer"
      >
        <div
          ref={factsDialogRef}
          role="dialog"
          aria-modal="true"
          aria-label="Knowledge Base 权威事实"
          tabIndex={-1}
        >
          <dl className="knowledge-base-resource-shell__facts-list">
            <div>
              <dt>Authority</dt>
              <dd>{authorityLabel}</dd>
            </div>
            <div>
              <dt>Dataset ID</dt>
              <dd>{shortId(knowledgeBase?.id ?? requestedDatasetId)}</dd>
            </div>
            <div>
              <dt>Owning Workspace</dt>
              <dd>{owner?.name || "未返回"}</dd>
            </div>
            <div>
              <dt>Workspace ID</dt>
              <dd>{shortId(owner?.id)}</dd>
            </div>
            <div>
              <dt>Workspace Revision</dt>
              <dd>{revisionLabel(owner?.revision)}</dd>
            </div>
            <div>
              <dt>Profile Revision</dt>
              <dd>{revisionLabel(knowledgeBase?.profile_revision)}</dd>
            </div>
            <div>
              <dt>Ownership Revision</dt>
              <dd>{revisionLabel(knowledgeBase?.ownership_revision)}</dd>
            </div>
            <div>
              <dt>Catalog Revision</dt>
              <dd>{knowledgeBase?.catalog_revision || "未返回"}</dd>
            </div>
            <div>
              <dt>Release / Channel</dt>
              <dd>
                {coordinator.releaseContext?.channelName
                  ? `${coordinator.releaseContext.channelName} · 生效发布 ${coordinator.releaseContext.effectiveReleaseNumber ?? "未返回"} · 服务中发布 ${coordinator.releaseContext.servingReleaseNumber ?? "未返回"}`
                  : coordinator.releaseContext?.unavailableReason || "发布权威未返回"}
              </dd>
            </div>
          </dl>
        </div>
      </Drawer>
    </section>
  );
}

export { KNOWLEDGE_BASE_RESOURCE_SECTIONS };
