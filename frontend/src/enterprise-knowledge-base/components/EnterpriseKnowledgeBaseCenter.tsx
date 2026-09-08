import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ComponentProps,
  type MouseEvent,
  type ComponentType,
  type HTMLAttributes,
  type ChangeEvent,
  type ReactNode,
} from "react";
import {
  Alert,
  Button,
  Dialog,
  Drawer,
  Input,
  PrimaryTable,
  Select,
  Tag,
  Textarea,
  type PrimaryTableCol,
} from "tdesign-react";
import {
  AddIcon,
  AppIcon,
  ArrowRightIcon,
  CheckCircleIcon,
  CloudIcon,
  DataBaseIcon,
  DeleteIcon,
  ErrorCircleIcon,
  FilterIcon,
  InfoCircleIcon,
  LinkIcon,
  RefreshIcon,
  SecuredIcon,
  ShareIcon,
  UserArrowRightIcon,
} from "tdesign-icons-react";
import PageState from "../../components/PageState";
import type { EnterpriseContext, EnterpriseScope } from "../../enterprise-admin/model";
import type {
  EnterpriseKnowledgeBase,
  KnowledgeBaseApplicationReference,
  KnowledgeBaseDetail,
} from "../enterpriseKnowledgeBaseModel";
import type { KnowledgeBaseTab } from "../knowledgeBaseRoute";
import {
  useEnterpriseKnowledgeBaseCenter,
  type KnowledgeBaseCenterFilters,
  type KnowledgeBaseCenterResult,
  type KnowledgeBaseResourceError,
} from "../hooks/useEnterpriseKnowledgeBaseCenter";
import { knowledgeBaseResourceNavigationUrl } from "../../enterprise-knowledge-base-shell/knowledgeBaseResourceRoute";
import {
  datasetDeepLink,
  knowledgeBaseIdFromLocation,
  knowledgeBaseNavigationUrl,
  knowledgeBaseRouteFromLocation,
  knowledgeBaseTabFromLocation,
} from "../knowledgeBaseRoute";
import {
  validateApplicationReferenceInput,
  validateWorkspaceTransferInput,
  type KnowledgeBaseValidationErrors,
} from "../enterpriseKnowledgeBaseValidation";
import "../enterprise-knowledge-base.css";

export interface KnowledgeBaseWorkspaceOption {
  value: string;
  label: string;
  environment?: string;
  revision?: number;
}

export interface EnterpriseKnowledgeBaseCenterProps {
  scope: EnterpriseScope;
  context: EnterpriseContext;
  workspaceOptions?: KnowledgeBaseWorkspaceOption[];
}

type DialogState =
  | { kind: "add-reference" }
  | { kind: "remove-reference"; reference: KnowledgeBaseApplicationReference }
  | { kind: "transfer" };

type AccessibleDialogProps = ComponentProps<typeof Dialog> & HTMLAttributes<HTMLDivElement>;
type AccessibleSelectInputProps = NonNullable<ComponentProps<typeof Select>["inputProps"]> &
  HTMLAttributes<HTMLDivElement>;
const AccessibleDialog = Dialog as unknown as ComponentType<AccessibleDialogProps>;
const STATUS_SELECT_INPUT_PROPS: AccessibleSelectInputProps = {
  role: "combobox",
  "aria-label": "知识库状态",
  "aria-haspopup": "listbox",
};
const WORKSPACE_SELECT_INPUT_PROPS: AccessibleSelectInputProps = {
  role: "combobox",
  "aria-label": "知识库 Workspace",
  "aria-haspopup": "listbox",
};

function AccessibleField({
  label,
  field,
  children,
  onChange,
}: {
  label: string;
  field: string;
  children: ReactNode;
  onChange?: (event: ChangeEvent<HTMLLabelElement>) => void;
}) {
  const fieldRef = useRef<HTMLLabelElement | null>(null);
  useLayoutEffect(() => {
    const control = fieldRef.current?.querySelector("input, textarea");
    control?.setAttribute("aria-label", label);
  }, [label]);
  return (
    <label
      ref={fieldRef}
      data-field={field}
      className="enterprise-knowledge-base-dialog__field"
      onChange={onChange}
    >
      <span>{label}</span>
      {children}
    </label>
  );
}

const STATUS_OPTIONS = [
  { label: "全部状态", value: "all" },
  { label: "运行中", value: "active" },
  { label: "已归档", value: "archived" },
];

const DETAIL_TABS: Array<{ value: KnowledgeBaseTab; label: string }> = [
  { value: "overview", label: "Overview" },
  { value: "workspace", label: "Workspace" },
  { value: "applications", label: "Application references" },
  { value: "dependencies", label: "Dependencies" },
  { value: "operations", label: "Operations" },
];

function countLabel(value: number | null): string {
  return value === null ? "未返回" : new Intl.NumberFormat("zh-CN").format(value);
}

function dateLabel(value: string | null): string {
  if (!value) return "未返回";
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

function statusLabel(status: string): string {
  return status === "active" ? "运行中" : status === "archived" ? "已归档" : status;
}

function statusTheme(status: string): "success" | "warning" | "default" {
  return status === "active" ? "success" : status === "archived" ? "warning" : "default";
}

function capabilityTheme(state: string): "success" | "warning" | "default" {
  return state === "ready" ? "success" : state === "limited" ? "warning" : "default";
}

function capabilityLabel(state: string): string {
  return state === "ready"
    ? "Registry 已接入"
    : state === "limited"
      ? "Registry 能力受限"
      : "Registry 能力未返回";
}

function ownerLabel(item: EnterpriseKnowledgeBase): string {
  return item.owning_workspace?.name ?? "未返回 owning Workspace";
}

function ownerRevisionLabel(item: EnterpriseKnowledgeBase): string {
  return item.ownership_revision == null ? "O 未返回" : "O" + item.ownership_revision;
}

function errorDescription(error: KnowledgeBaseResourceError | null): string {
  return error?.description ?? "服务端未返回可验证的知识库事实。";
}

function writeKnowledgeBaseRoute(
  datasetId: string | null,
  tab: KnowledgeBaseTab,
  replace: boolean,
): void {
  if (typeof window === "undefined") return;
  const intent = knowledgeBaseNavigationUrl(window.location, { datasetId, tab });
  if (intent.mode === "history") {
    if (replace) window.history.replaceState(window.history.state, "", intent.url);
    else window.history.pushState(window.history.state, "", intent.url);
    window.dispatchEvent(new PopStateEvent("popstate"));
    return;
  }
  const nextHash = "#" + intent.url;
  if (window.location.hash !== nextHash) window.location.hash = intent.url;
}

function dependencySummary(detail: KnowledgeBaseDetail): {
  owner: string;
  associations: string;
  references: string;
  archive: string;
} {
  const owner = detail.dependencies.owning_workspace?.name ?? "未返回";
  const associationCount = detail.knowledge_base.shared_association_count;
  const referenceCount = detail.knowledge_base.active_application_reference_count;
  const archive =
    detail.dependencies.archive.ready === null
      ? "未返回"
      : detail.dependencies.archive.ready
        ? "可归档"
        : "存在 blocker";
  return {
    owner,
    associations: countLabel(associationCount),
    references: countLabel(referenceCount),
    archive,
  };
}

function ResourceError({
  error,
  onRetry,
}: {
  error: KnowledgeBaseResourceError;
  onRetry: () => void;
}) {
  return (
    <div className="enterprise-knowledge-base-detail__unavailable" role="alert">
      <strong>{error.title}</strong>
      <div>{error.description}</div>
      {error.canRetry ? (
        <Button variant="outline" icon={<RefreshIcon />} onClick={onRetry}>
          重新读取
        </Button>
      ) : null}
    </div>
  );
}

function AuthorityStrip({ result }: { result: KnowledgeBaseCenterResult }) {
  const evidence = result.page?.evidence;
  const activeReferenceCount = evidence?.active_application_reference_count ?? null;
  return (
    <section
      className="enterprise-knowledge-base-center__authority"
      role="region"
      aria-label="Knowledge Base 权威证据"
    >
      <dl className="enterprise-knowledge-base-center__authority-item">
        <dt>Knowledge Base 总数</dt>
        <dd>{countLabel(evidence?.knowledge_base_count ?? null)}</dd>
        <small>只显示 Registry 返回的总数</small>
      </dl>
      <dl className="enterprise-knowledge-base-center__authority-item">
        <dt>有效知识库</dt>
        <dd>{countLabel(evidence?.active_count ?? null)}</dd>
        <small>生命周期状态来自 Dataset authority</small>
      </dl>
      <dl className="enterprise-knowledge-base-center__authority-item">
        <dt>可证明 ownership</dt>
        <dd>{countLabel(evidence?.owned_count ?? null)}</dd>
        <small>shared association 不计入 ownership</small>
      </dl>
      <dl className="enterprise-knowledge-base-center__authority-item">
        <dt>活跃引用</dt>
        <dd>{countLabel(activeReferenceCount)}</dd>
        <small>
          {activeReferenceCount === null
            ? "未返回"
            : activeReferenceCount + " 个活跃 Application 引用"}
        </small>
      </dl>
      <dl className="enterprise-knowledge-base-center__authority-item">
        <dt>归档就绪</dt>
        <dd>{countLabel(evidence?.archive_ready_count ?? null)}</dd>
        <small>{evidence?.catalog_revision ?? "catalog revision 未返回"}</small>
      </dl>
    </section>
  );
}

function KnowledgeBaseNameCell({ row }: { row: EnterpriseKnowledgeBase }) {
  return (
    <div className="enterprise-knowledge-base-name-cell">
      <span className="enterprise-knowledge-base-name-cell__mark" aria-hidden="true">
        <DataBaseIcon />
      </span>
      <span>
        <strong>{row.name}</strong>
        <small>{row.id}</small>
      </span>
    </div>
  );
}

function KnowledgeBaseCard({
  row,
  onOpen,
}: {
  row: EnterpriseKnowledgeBase;
  onOpen: (datasetId: string, trigger: HTMLElement) => void;
}) {
  return (
    <li className="enterprise-knowledge-base-card">
      <header className="enterprise-knowledge-base-card__header">
        <div>
          <strong>{row.name}</strong>
          <small>{row.id}</small>
        </div>
        <Tag theme={statusTheme(row.status)} variant="light-outline">
          {statusLabel(row.status)}
        </Tag>
      </header>
      <dl className="enterprise-knowledge-base-card__meta">
        <div>
          <dt>Owning Workspace</dt>
          <dd>{ownerLabel(row)}</dd>
        </div>
        <div>
          <dt>引用</dt>
          <dd>{countLabel(row.active_application_reference_count)}</dd>
        </div>
        <div>
          <dt>Revision</dt>
          <dd>
            P{row.profile_revision} / {ownerRevisionLabel(row)}
          </dd>
        </div>
      </dl>
      <Button
        className="enterprise-knowledge-base-card__action"
        variant="outline"
        icon={<ArrowRightIcon />}
        onClick={(event) => onOpen(row.id, event.currentTarget)}
        aria-label={"查看知识库 " + row.name}
      >
        查看详情
      </Button>
    </li>
  );
}

export default function EnterpriseKnowledgeBaseCenter({
  scope,
  context,
  workspaceOptions = [],
}: EnterpriseKnowledgeBaseCenterProps) {
  const [keyword, setKeyword] = useState("");
  const [status, setStatus] = useState("all");
  const [workspaceId, setWorkspaceId] = useState("");
  const [activeTab, setActiveTab] = useState<KnowledgeBaseTab>("overview");
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [applicationId, setApplicationId] = useState("");
  const [reason, setReason] = useState("");
  const [targetWorkspaceId, setTargetWorkspaceId] = useState("");
  const [validationErrors, setValidationErrors] = useState<KnowledgeBaseValidationErrors>({});
  const [notice, setNotice] = useState<string | null>(null);
  const [filterDrawerOpen, setFilterDrawerOpen] = useState(false);
  const lastTriggerRef = useRef<HTMLElement | null>(null);
  const tableHostRef = useRef<HTMLDivElement | null>(null);
  const initialRouteId =
    typeof window === "undefined" ? null : knowledgeBaseIdFromLocation(window.location);
  const filters = useMemo<KnowledgeBaseCenterFilters>(
    () => ({ keyword, status, workspaceId, limit: 50 }),
    [keyword, status, workspaceId],
  );
  const activeFilterCount =
    Number(Boolean(keyword.trim())) + Number(status !== "all") + Number(Boolean(workspaceId));
  const clearFilters = useCallback(() => {
    setKeyword("");
    setStatus("all");
    setWorkspaceId("");
  }, []);
  const renderFilterFields = (variant: "inline" | "drawer") => (
    <div
      className={`enterprise-knowledge-base-center__filters enterprise-knowledge-base-center__filters--${variant}`}
    >
      <label className="enterprise-knowledge-base-center__field">
        <span>搜索</span>
        <Input
          value={keyword}
          placeholder="搜索名称、ID 或描述"
          clearable
          aria-label="搜索知识库"
          onChange={(value) => setKeyword(value)}
        />
      </label>
      <label className="enterprise-knowledge-base-center__field">
        <span>状态</span>
        <Select
          value={status}
          options={STATUS_OPTIONS}
          inputProps={STATUS_SELECT_INPUT_PROPS}
          onChange={(value) => setStatus(String(value))}
        />
      </label>
      <label className="enterprise-knowledge-base-center__field">
        <span>Owning Workspace</span>
        <Select
          value={workspaceId || undefined}
          options={[{ label: "全部 Workspace", value: "" }, ...workspaceOptions]}
          clearable
          inputProps={WORKSPACE_SELECT_INPUT_PROPS}
          onChange={(value) => setWorkspaceId(String(value ?? ""))}
        />
      </label>
    </div>
  );
  const center = useEnterpriseKnowledgeBaseCenter(scope, filters);
  const selectedId = center.selected?.knowledge_base.id ?? initialRouteId;
  const transferWorkspaceOptions = useMemo(() => {
    const currentOwnerId = center.selected?.knowledge_base.owning_workspace?.id ?? null;
    return workspaceOptions.filter(
      (option) =>
        option.value !== currentOwnerId &&
        Number.isInteger(option.revision) &&
        (option.revision as number) > 0,
    );
  }, [center.selected, workspaceOptions]);
  const selectedTransferWorkspace = useMemo(
    () => transferWorkspaceOptions.find((option) => option.value === targetWorkspaceId) ?? null,
    [targetWorkspaceId, transferWorkspaceOptions],
  );
  const selectedFromCenter = center.selected;
  const openKnowledgeBase = center.openKnowledgeBase;
  const closeKnowledgeBase = center.closeKnowledgeBase;

  const openDetail = useCallback(
    (datasetId: string, trigger?: HTMLElement) => {
      const normalized = datasetId.trim();
      if (!normalized) return;
      if (trigger) lastTriggerRef.current = trigger;
      setActiveTab("overview");
      setNotice(null);
      writeKnowledgeBaseRoute(normalized, "overview", false);
      void openKnowledgeBase(normalized);
    },
    [openKnowledgeBase],
  );

  const closeDetail = useCallback(() => {
    closeKnowledgeBase();
    setDialog(null);
    setNotice(null);
    writeKnowledgeBaseRoute(null, "overview", true);
    window.setTimeout(() => lastTriggerRef.current?.focus(), 0);
  }, [closeKnowledgeBase]);

  const closeMutationDialog = useCallback(() => {
    setDialog(null);
    setValidationErrors({});
    window.setTimeout(() => lastTriggerRef.current?.focus(), 0);
  }, []);

  useEffect(() => {
    const applyLocation = () => {
      if (!knowledgeBaseRouteFromLocation(window.location)) return;
      const routeId = knowledgeBaseIdFromLocation(window.location);
      const routeTab = knowledgeBaseTabFromLocation(window.location);
      setActiveTab(routeTab);
      if (routeId && routeId !== selectedFromCenter?.knowledge_base.id)
        void openKnowledgeBase(routeId);
      if (!routeId && selectedFromCenter) closeKnowledgeBase();
    };
    applyLocation();
    window.addEventListener("hashchange", applyLocation);
    window.addEventListener("popstate", applyLocation);
    return () => {
      window.removeEventListener("hashchange", applyLocation);
      window.removeEventListener("popstate", applyLocation);
    };
  }, [closeKnowledgeBase, openKnowledgeBase, selectedFromCenter]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (dialog) {
        event.preventDefault();
        closeMutationDialog();
        return;
      }
      if (selectedId) {
        event.preventDefault();
        closeDetail();
      }
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [closeDetail, closeMutationDialog, dialog, selectedId]);

  useLayoutEffect(() => {
    if (!dialog) return;
    const labels: Array<[string, string]> = [
      ["[data-field=application-id] input", "Application ID"],
      ["[data-field=target-workspace] input", "目标 Workspace"],
      ["[data-field=mutation-reason] textarea", "变更原因"],
    ];
    for (const [selector, label] of labels) {
      document.querySelector(selector)?.setAttribute("aria-label", label);
    }
    const decorateTargetWorkspaceInput = () => {
      const targetWorkspaceInput = document.querySelector<HTMLInputElement>(
        "[data-field=target-workspace] input",
      );
      const internalWrapper = document.querySelector<HTMLElement>(
        "[data-field=target-workspace] .enterprise-knowledge-base-dialog__select-control .t-input__wrap",
      );
      targetWorkspaceInput?.setAttribute("aria-label", "目标 Workspace");
      internalWrapper?.removeAttribute("role");
      internalWrapper?.removeAttribute("aria-label");
      internalWrapper?.setAttribute("aria-hidden", "true");
    };
    decorateTargetWorkspaceInput();
    const firstSelector =
      dialog.kind === "add-reference"
        ? "[data-field=application-id] input"
        : dialog.kind === "transfer"
          ? "[data-field=target-workspace] .t-input__wrap"
          : "[data-field=mutation-reason] textarea";
    const focus = () => {
      decorateTargetWorkspaceInput();
      document.querySelector<HTMLElement>(firstSelector)?.focus({ preventScroll: true });
    };
    const timers = [window.setTimeout(focus, 0), window.setTimeout(focus, 50)];
    return () => timers.forEach((timer) => window.clearTimeout(timer));
  }, [dialog]);

  useLayoutEffect(() => {
    const table = tableHostRef.current?.querySelector("table");
    table?.setAttribute("aria-label", "企业知识库列表");
  }, [center.page?.items.length]);

  const rememberCurrentTrigger = useCallback(() => {
    if (document.activeElement instanceof HTMLElement) {
      lastTriggerRef.current = document.activeElement;
    }
  }, []);

  const openAddReference = useCallback((event: MouseEvent<HTMLElement>) => {
    lastTriggerRef.current = event.currentTarget;
    setApplicationId("");
    setReason("");
    setValidationErrors({});
    setNotice(null);
    setDialog({ kind: "add-reference" });
  }, []);

  const openRemoveReference = useCallback(
    (reference: KnowledgeBaseApplicationReference) => {
      rememberCurrentTrigger();
      setReason("");
      setValidationErrors({});
      setNotice(null);
      setDialog({ kind: "remove-reference", reference });
    },
    [rememberCurrentTrigger],
  );

  const openTransfer = useCallback((event: MouseEvent<HTMLElement>) => {
    lastTriggerRef.current = event.currentTarget;
    setTargetWorkspaceId("");
    setReason("");
    setValidationErrors({});
    setNotice(null);
    setDialog({ kind: "transfer" });
  }, []);

  const submitDialog = useCallback(async () => {
    if (!dialog || !center.selected) return;
    const knowledgeBase = center.selected.knowledge_base;
    if (dialog.kind === "add-reference") {
      const errors = validateApplicationReferenceInput({ appId: applicationId, reason });
      if (Object.keys(errors).length > 0) {
        setValidationErrors(errors);
        return;
      }
      const result = await center.addApplicationReference({
        appId: applicationId.trim(),
        datasetId: knowledgeBase.id,
        reason: reason.trim(),
      });
      if (result?.state === "applied") {
        closeMutationDialog();
        setNotice("Application 引用已添加");
      }
      return;
    }
    if (dialog.kind === "remove-reference") {
      const errors = reason.trim() ? {} : { reason: "请填写变更原因" };
      if (Object.keys(errors).length > 0) {
        setValidationErrors(errors);
        return;
      }
      const result = await center.removeApplicationReference({
        appId: dialog.reference.app_id,
        datasetId: knowledgeBase.id,
        expectedRevision: dialog.reference.revision ?? 0,
        reason: reason.trim(),
      });
      if (result?.state === "applied") {
        closeMutationDialog();
        setNotice("Application 引用已移除");
      }
      return;
    }
    const errors = validateWorkspaceTransferInput({
      targetWorkspaceId,
      expectedDatasetProfileRevision: knowledgeBase.profile_revision,
      expectedOwnershipRevision: knowledgeBase.ownership_revision ?? null,
      expectedSourceWorkspaceRevision: knowledgeBase.owning_workspace?.revision ?? null,
      expectedTargetWorkspaceRevision: selectedTransferWorkspace?.revision ?? null,
      reason,
    });
    if (Object.keys(errors).length > 0) {
      setValidationErrors(errors);
      return;
    }
    const result = await center.transferOwnership(knowledgeBase.id, {
      targetWorkspaceId: targetWorkspaceId.trim(),
      expectedDatasetProfileRevision: knowledgeBase.profile_revision,
      expectedOwnershipRevision: knowledgeBase.ownership_revision as number,
      expectedSourceWorkspaceRevision: knowledgeBase.owning_workspace?.revision as number,
      expectedTargetWorkspaceRevision: selectedTransferWorkspace?.revision as number,
      reason: reason.trim(),
    });
    if (result?.state === "approval_required") {
      closeMutationDialog();
      setNotice("已提交审批，等待 Workspace 所有者审核");
    } else if (result?.state === "applied") {
      closeMutationDialog();
      setNotice("知识库所有权已转移");
    }
  }, [
    applicationId,
    center,
    closeMutationDialog,
    dialog,
    reason,
    selectedTransferWorkspace,
    targetWorkspaceId,
  ]);

  const openResourceWorkspace = useCallback(
    (datasetId: string) => {
      const intent = knowledgeBaseResourceNavigationUrl(window.location, {
        datasetId,
        section: "overview",
      });
      closeKnowledgeBase();
      setDialog(null);
      setNotice(null);
      if (intent.mode === "history") {
        window.history.pushState(window.history.state, "", intent.url);
        window.dispatchEvent(new PopStateEvent("popstate"));
      } else {
        window.location.hash = intent.url;
      }
    },
    [closeKnowledgeBase],
  );

  const columns = useMemo<PrimaryTableCol<EnterpriseKnowledgeBase>[]>(
    () => [
      {
        colKey: "name",
        title: "Knowledge Base",
        width: 230,
        cell: ({ row }) => <KnowledgeBaseNameCell row={row} />,
      },
      {
        colKey: "status",
        title: "状态",
        width: 100,
        cell: ({ row }) => (
          <Tag theme={statusTheme(row.status)} variant="light-outline">
            {statusLabel(row.status)}
          </Tag>
        ),
      },
      {
        colKey: "workspace",
        title: "Owning Workspace",
        width: 160,
        cell: ({ row }) => ownerLabel(row),
      },
      {
        colKey: "references",
        title: "Application references",
        width: 140,
        cell: ({ row }) => countLabel(row.active_application_reference_count),
      },
      {
        colKey: "documents",
        title: "文档",
        width: 90,
        cell: ({ row }) => countLabel(row.document_count),
      },
      {
        colKey: "sources",
        title: "来源",
        width: 90,
        cell: ({ row }) => countLabel(row.source_count),
      },
      {
        colKey: "revision",
        title: "Revision",
        width: 115,
        cell: ({ row }) => "P" + row.profile_revision + " / " + ownerRevisionLabel(row),
      },
      {
        colKey: "updated_at",
        title: "更新时间",
        width: 150,
        cell: ({ row }) => dateLabel(row.updated_at),
      },
      {
        colKey: "actions",
        title: "操作",
        width: 100,
        fixed: "right",
        cell: ({ row }) => (
          <Button
            variant="text"
            size="small"
            icon={<ArrowRightIcon />}
            onClick={(event) => openDetail(row.id, event.currentTarget)}
            aria-label={"查看知识库 " + row.name}
          >
            详情
          </Button>
        ),
      },
    ],
    [openDetail],
  );

  const renderOverview = (detail: KnowledgeBaseDetail) => {
    const item = detail.knowledge_base;
    return (
      <div className="enterprise-knowledge-base-detail__section">
        <header>
          <h3>Registry projection</h3>
          <Tag theme={capabilityTheme(item.capability_state)} variant="light-outline">
            {capabilityLabel(item.capability_state)}
          </Tag>
        </header>
        <div className="enterprise-knowledge-base-detail__facts">
          <div className="enterprise-knowledge-base-detail__fact">
            <dt>Dataset ID</dt>
            <dd>{item.id}</dd>
          </div>
          <div className="enterprise-knowledge-base-detail__fact">
            <dt>Visibility</dt>
            <dd>{item.visibility ?? "未返回"}</dd>
          </div>
          <div className="enterprise-knowledge-base-detail__fact">
            <dt>Profile revision</dt>
            <dd>{item.profile_revision}</dd>
          </div>
          <div className="enterprise-knowledge-base-detail__fact">
            <dt>Updated</dt>
            <dd>{dateLabel(item.updated_at)}</dd>
          </div>
        </div>
        <div className="enterprise-knowledge-base-detail__copy">
          <strong>描述</strong>
          <span>{item.description ?? "未返回描述"}</span>
        </div>
        <div className="enterprise-knowledge-base-detail__section">
          <header>
            <div className="enterprise-knowledge-base-detail__copy">
              <h3>Knowledge Base workspace</h3>
              <span>进入持久资源工作台；Registry Drawer 继续只负责快速权威操作。</span>
            </div>
            <Button
              theme="primary"
              icon={<ArrowRightIcon />}
              onClick={() => openResourceWorkspace(item.id)}
            >
              打开 Knowledge Base 工作台
            </Button>
          </header>
          <div className="enterprise-knowledge-base-detail__links">
            {(["documents", "taxonomy", "sources", "governance"] as const).map((page) => {
              const link = datasetDeepLink(window.location, page, item.id);
              const labels = {
                documents: "打开 Documents",
                taxonomy: "打开 Taxonomy",
                sources: "打开 Sources",
                governance: "打开 Governance",
              };
              return (
                <a key={page} href={link.mode === "hash" ? "#" + link.url : link.url}>
                  <LinkIcon /> {labels[page]}
                </a>
              );
            })}
          </div>
        </div>
      </div>
    );
  };

  const renderWorkspace = (detail: KnowledgeBaseDetail) => {
    const item = detail.knowledge_base;
    const canTransfer = ["owner", "admin"].includes(context.actor.role);
    const revisionsReady =
      item.owning_workspace?.revision != null &&
      item.ownership_revision != null &&
      transferWorkspaceOptions.length > 0;
    return (
      <div className="enterprise-knowledge-base-detail__section">
        <header>
          <div className="enterprise-knowledge-base-detail__copy">
            <h3>Authoritative ownership</h3>
            <span>所有权不是权限；Workspace authorization 仍由 Stage 17 事实决定。</span>
          </div>
          <Button
            tag="button"
            variant="outline"
            icon={<UserArrowRightIcon />}
            disabled={!canTransfer || !item.owning_workspace || !revisionsReady}
            title={
              !canTransfer
                ? "仅租户所有者或管理员可转移所有权"
                : !revisionsReady
                  ? "Workspace revision 未完整返回，所有权转移已禁用"
                  : undefined
            }
            onClick={openTransfer}
          >
            转移所有权
          </Button>
        </header>
        {item.owning_workspace ? (
          <div className="enterprise-knowledge-base-detail__facts">
            <div className="enterprise-knowledge-base-detail__fact">
              <dt>Owning Workspace</dt>
              <dd>{item.owning_workspace.name}</dd>
            </div>
            <div className="enterprise-knowledge-base-detail__fact">
              <dt>Workspace status</dt>
              <dd>{statusLabel(item.owning_workspace.status)}</dd>
            </div>
            <div className="enterprise-knowledge-base-detail__fact">
              <dt>Workspace ID</dt>
              <dd>{item.owning_workspace.id}</dd>
            </div>
            <div className="enterprise-knowledge-base-detail__fact">
              <dt>Ownership revision</dt>
              <dd>{item.ownership_revision ?? "未返回"}</dd>
            </div>
          </div>
        ) : (
          <div className="enterprise-knowledge-base-detail__unavailable" role="status">
            未返回可证明的 owning Workspace；不会把 shared association 当作 ownership。
          </div>
        )}
        {!canTransfer ? (
          <span className="enterprise-knowledge-base-dialog__hint">
            仅租户所有者或管理员可执行所有权转移。
          </span>
        ) : !revisionsReady ? (
          <span className="enterprise-knowledge-base-dialog__hint">
            source/target Workspace revision 未完整返回；为避免覆盖并发变更，已 fail-closed
            禁用转移。
          </span>
        ) : null}
      </div>
    );
  };

  const renderApplications = (detail: KnowledgeBaseDetail) => {
    const references = detail.application_references.items;
    const canManageReferences = context.effective_permissions.includes("knowledge.manage");
    return (
      <div className="enterprise-knowledge-base-detail__section">
        <header>
          <div className="enterprise-knowledge-base-detail__copy">
            <h3>Application references</h3>
            <span>引用是依赖事实，不是 Dataset permission。</span>
          </div>
          <Button
            theme="primary"
            icon={<AddIcon />}
            tag="button"
            disabled={!canManageReferences}
            title={!canManageReferences ? "当前身份没有 knowledge.manage 权限" : undefined}
            onClick={openAddReference}
          >
            添加 Application 引用
          </Button>
        </header>
        {!canManageReferences ? (
          <div className="enterprise-knowledge-base-dialog__hint" role="status">
            当前为只读模式；没有 knowledge.manage 权限，不能新增或移除 Application 引用。
          </div>
        ) : null}
        {references.length === 0 ? (
          <div className="enterprise-knowledge-base-detail__empty">
            {detail.application_references.count === null
              ? "Application 引用未返回"
              : "暂无 Application 引用"}
          </div>
        ) : (
          <ul className="enterprise-knowledge-base-detail__list" aria-label="Application 引用列表">
            {references.map((reference) => (
              <li className="enterprise-knowledge-base-detail__list-item" key={reference.id}>
                <div>
                  <strong>{reference.app_name ?? reference.app_id}</strong>
                  <small>
                    {reference.app_id} · Revision {reference.revision ?? "未返回"}
                  </small>
                </div>
                <Button
                  variant="text"
                  theme="danger"
                  icon={<DeleteIcon />}
                  tag="button"
                  disabled={
                    !canManageReferences ||
                    reference.status !== "active" ||
                    reference.revision === null
                  }
                  aria-label={"移除 Application 引用 " + (reference.app_name ?? reference.app_id)}
                  onClick={() => openRemoveReference(reference)}
                >
                  移除
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  };

  const renderDependencies = (detail: KnowledgeBaseDetail) => {
    const summary = dependencySummary(detail);
    const dependencies = detail.dependencies;
    if (dependencies.state === "unavailable") {
      return (
        <div className="enterprise-knowledge-base-detail__section">
          <header>
            <h3>Dependency Rail</h3>
          </header>
          <div className="enterprise-knowledge-base-detail__unavailable" role="alert">
            依赖证据暂不可用：{dependencies.reason ?? "服务端未返回原因"}。未把不可用解释成空依赖。
          </div>
        </div>
      );
    }
    return (
      <div className="enterprise-knowledge-base-detail__section">
        <header>
          <div className="enterprise-knowledge-base-detail__copy">
            <h3>Dependency Rail</h3>
            <span>证据链用于评估影响，不授予任何权限。</span>
          </div>
          <Tag
            theme={dependencies.state === "ready" ? "success" : "default"}
            variant="light-outline"
          >
            {dependencies.state === "ready" ? "证据已返回" : "状态未返回"}
          </Tag>
        </header>
        <section
          className="enterprise-knowledge-base-detail__rail"
          role="region"
          aria-label="依赖证据链"
        >
          <div className="enterprise-knowledge-base-detail__rail-step">
            <span className="enterprise-knowledge-base-detail__rail-dot">
              <SecuredIcon />
            </span>
            <div className="enterprise-knowledge-base-detail__rail-copy">
              <strong>Owning Workspace</strong>
              <span>{summary.owner}</span>
            </div>
          </div>
          <div className="enterprise-knowledge-base-detail__rail-step">
            <span className="enterprise-knowledge-base-detail__rail-dot">
              <ShareIcon />
            </span>
            <div className="enterprise-knowledge-base-detail__rail-copy">
              <strong>Shared associations</strong>
              <span>{summary.associations} 条服务端 association</span>
            </div>
          </div>
          <div className="enterprise-knowledge-base-detail__rail-step">
            <span className="enterprise-knowledge-base-detail__rail-dot">
              <AppIcon />
            </span>
            <div className="enterprise-knowledge-base-detail__rail-copy">
              <strong>Application references</strong>
              <span>{summary.references} 个活跃引用</span>
            </div>
          </div>
          <div className="enterprise-knowledge-base-detail__rail-step">
            <span className="enterprise-knowledge-base-detail__rail-dot">
              <CloudIcon />
            </span>
            <div className="enterprise-knowledge-base-detail__rail-copy">
              <strong>Archive readiness</strong>
              <span>{summary.archive}</span>
            </div>
          </div>
        </section>
        {dependencies.shared_associations.length > 0 ? (
          <ul
            className="enterprise-knowledge-base-detail__list"
            aria-label="Shared association 列表"
          >
            {dependencies.shared_associations.map((association) => (
              <li className="enterprise-knowledge-base-detail__list-item" key={association.id}>
                <div>
                  <strong>{association.workspace_name ?? association.workspace_id}</strong>
                  <small>
                    {association.workspace_id} · {association.binding_kind}
                  </small>
                </div>
                <Tag variant="light-outline">{statusLabel(association.status)}</Tag>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    );
  };

  const renderOperations = (detail: KnowledgeBaseDetail) => {
    const readiness = detail.dependencies.archive;
    const referenceCount = detail.knowledge_base.active_application_reference_count;
    return (
      <div className="enterprise-knowledge-base-detail__section">
        <header>
          <div className="enterprise-knowledge-base-detail__copy">
            <h3>Lifecycle operations</h3>
            <span>Stage 18 不执行物理 Dataset 删除。</span>
          </div>
          <Tag
            theme={
              readiness.ready === false
                ? "warning"
                : readiness.ready === true
                  ? "success"
                  : "default"
            }
            variant="light-outline"
          >
            {readiness.ready === false
              ? "归档受阻"
              : readiness.ready === true
                ? "归档就绪"
                : "就绪状态未返回"}
          </Tag>
        </header>
        {readiness.ready === false ? (
          <Alert
            theme="warning"
            icon={<InfoCircleIcon />}
            title="当前无法归档知识库"
            message={
              "存在 " +
              countLabel(referenceCount) +
              " 个活跃 Application 引用或其他依赖 blocker。审批不能绕过该依赖。"
            }
          />
        ) : readiness.ready === null ? (
          <Alert
            theme="info"
            icon={<ErrorCircleIcon />}
            title="归档状态未返回"
            message="依赖证据不可用时不显示归档操作，也不把未知状态当成可归档。"
          />
        ) : (
          <Alert
            theme="success"
            icon={<CheckCircleIcon />}
            title="服务端未报告归档 blocker"
            message="执行仍需遵守 Dataset lifecycle 的 revision 和权限约束。"
          />
        )}
        {readiness.blockers.length > 0 ? (
          <ul className="enterprise-knowledge-base-detail__list" aria-label="归档 blocker 列表">
            {readiness.blockers.map((blocker) => (
              <li className="enterprise-knowledge-base-detail__list-item" key={blocker.code}>
                <div>
                  <strong>{blocker.label}</strong>
                  <small>
                    {blocker.code} · {blocker.count === null ? "数量未返回" : blocker.count}
                  </small>
                </div>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    );
  };

  const renderTab = (detail: KnowledgeBaseDetail) => {
    if (activeTab === "workspace") return renderWorkspace(detail);
    if (activeTab === "applications") return renderApplications(detail);
    if (activeTab === "dependencies") return renderDependencies(detail);
    if (activeTab === "operations") return renderOperations(detail);
    return renderOverview(detail);
  };

  if (center.status === "identity-missing") {
    return (
      <PageState
        status="error"
        title="需要企业身份"
        description="连接签名企业身份后才能读取 Knowledge Base Registry 真账。"
      />
    );
  }
  if (center.status === "loading" && !center.page) {
    return (
      <PageState
        status="loading"
        title="正在读取 Knowledge Base Registry"
        description="不使用模拟知识库填充企业资源中心。"
      />
    );
  }
  if ((center.status === "error" || center.status === "unavailable") && !center.page) {
    return (
      <PageState
        status="error"
        title={center.error?.title ?? "Knowledge Base Registry 不可用"}
        description={errorDescription(center.error)}
        extra={
          center.error?.canRetry ? (
            <Button icon={<RefreshIcon />} onClick={() => void center.reload()}>
              重新读取
            </Button>
          ) : undefined
        }
      />
    );
  }

  const page = center.page;
  const rows = page?.items ?? [];
  const selected = center.selected;
  const selectedTitle = selected?.knowledge_base.name ?? "Knowledge Base 详情";
  const routeOpen = Boolean(selectedId) || center.selectedLoading || Boolean(center.detailError);

  return (
    <section
      className="enterprise-knowledge-base-center"
      aria-label="Enterprise Knowledge Base Center"
    >
      <div className="enterprise-knowledge-base-center__title-row">
        <div className="enterprise-knowledge-base-center__copy">
          <span className="enterprise-knowledge-base-center__eyebrow">
            REGISTRY / DATASET AUTHORITY
          </span>
          <h2>知识库真账</h2>
          <p>以 Workspace ownership、Application references 和依赖证据连接企业知识运营。</p>
        </div>
        <Tag theme="primary" variant="light-outline" icon={<SecuredIcon />}>
          0028 Registry
        </Tag>
      </div>
      <AuthorityStrip result={center} />
      <div
        className="enterprise-knowledge-base-center__toolbar"
        role="region"
        aria-label="知识库筛选和操作"
      >
        {renderFilterFields("inline")}
        <div className="enterprise-knowledge-base-center__toolbar-actions">
          <Button
            className="enterprise-knowledge-base-center__filter-trigger"
            variant="outline"
            icon={<FilterIcon />}
            aria-label="打开筛选"
            onClick={() => setFilterDrawerOpen(true)}
          >
            筛选{activeFilterCount ? ` · ${activeFilterCount}` : ""}
          </Button>
          <Button variant="outline" icon={<RefreshIcon />} onClick={() => void center.reload()}>
            刷新事实
          </Button>
        </div>
        {center.moreError ? (
          <span className="enterprise-knowledge-base-center__toolbar-note" role="alert">
            {center.moreError.description}
          </span>
        ) : null}
      </div>
      {notice ? (
        <Alert theme="success" title={notice} message="已保留服务端返回的最新事实。" />
      ) : null}
      {center.mutation.error ? (
        <ResourceError error={center.mutation.error} onRetry={() => void center.retryMutation()} />
      ) : null}
      {rows.length === 0 ? (
        <div className="enterprise-knowledge-base-detail__empty" role="status">
          {page?.count === 0
            ? "当前租户没有可展示的 Knowledge Base"
            : "Knowledge Base 列表未返回可验证资源"}
        </div>
      ) : (
        <>
          <div ref={tableHostRef} className="enterprise-knowledge-base-center__table">
            <PrimaryTable
              aria-label="企业知识库列表"
              rowKey="id"
              data={rows}
              columns={columns}
              bordered
              hover
              tableLayout="fixed"
            />
          </div>
          <ul className="enterprise-knowledge-base-cards" aria-label="移动端知识库列表">
            {rows.map((row) => (
              <KnowledgeBaseCard key={row.id} row={row} onOpen={openDetail} />
            ))}
          </ul>
          {page?.next_cursor ? (
            <Button
              variant="outline"
              loading={center.moreLoading}
              onClick={() => void center.loadMore()}
            >
              加载更多知识库
            </Button>
          ) : null}
        </>
      )}
      <Drawer
        visible={filterDrawerOpen}
        header="知识库筛选"
        destroyOnClose
        aria-label="知识库筛选"
        size="min(360px, 100vw)"
        footer={false}
        closeBtn={
          <Button
            variant="text"
            aria-label="关闭知识库筛选"
            onClick={() => setFilterDrawerOpen(false)}
          >
            关闭
          </Button>
        }
        onClose={() => setFilterDrawerOpen(false)}
        className="enterprise-knowledge-base-filter-drawer"
      >
        {filterDrawerOpen ? (
          <div
            role="dialog"
            aria-modal="true"
            aria-label="知识库筛选"
            className="enterprise-knowledge-base-filter-drawer__body"
          >
            {renderFilterFields("drawer")}
            <div className="enterprise-knowledge-base-filter-drawer__actions">
              <Button variant="text" onClick={clearFilters}>
                清除筛选
              </Button>
              <Button theme="primary" onClick={() => setFilterDrawerOpen(false)}>
                完成
              </Button>
            </div>
          </div>
        ) : null}
      </Drawer>
      <Drawer
        visible={routeOpen}
        header={"知识库详情：" + selectedTitle}
        aria-label={"知识库详情：" + selectedTitle}
        size="min(720px, 100vw)"
        destroyOnClose
        footer={false}
        closeBtn={
          <Button variant="text" aria-label="关闭知识库详情" onClick={closeDetail}>
            关闭
          </Button>
        }
        onClose={closeDetail}
        className="enterprise-knowledge-base-detail-drawer"
      >
        <div role="dialog" aria-modal="true" aria-label={"知识库详情：" + selectedTitle}>
          {center.selectedLoading ? (
            <PageState status="loading" title="正在读取知识库详情" />
          ) : center.detailError ? (
            <PageState
              status="error"
              title={center.detailError.title}
              description={center.detailError.description}
            />
          ) : selected ? (
            <div className="enterprise-knowledge-base-detail">
              <header className="enterprise-knowledge-base-detail__header">
                <div>
                  <h2>{selected.knowledge_base.name}</h2>
                  <span className="enterprise-knowledge-base-detail__copy">
                    {selected.knowledge_base.id}
                  </span>
                </div>
                <div className="enterprise-knowledge-base-detail__meta">
                  <Tag theme={statusTheme(selected.knowledge_base.status)} variant="light-outline">
                    {statusLabel(selected.knowledge_base.status)}
                  </Tag>
                  <Tag variant="light-outline">P{selected.knowledge_base.profile_revision}</Tag>
                </div>
              </header>
              {center.mutation.outcome?.state === "approval_required" ? (
                <Alert
                  theme="warning"
                  title="已提交审批"
                  message="等待 Workspace 所有者审核；页面不会展示原始审批票据。"
                />
              ) : null}
              <div className="enterprise-knowledge-base-detail-tabs">
                <div role="tablist" aria-label="知识库详情标签页">
                  {DETAIL_TABS.map((tab) => (
                    <Button
                      key={tab.value}
                      role="tab"
                      aria-selected={activeTab === tab.value}
                      variant={activeTab === tab.value ? "base" : "text"}
                      onClick={() => {
                        setActiveTab(tab.value);
                        writeKnowledgeBaseRoute(selected.knowledge_base.id, tab.value, false);
                      }}
                    >
                      {tab.label}
                    </Button>
                  ))}
                </div>
                <div role="tabpanel" tabIndex={0}>
                  {renderTab(selected)}
                </div>
              </div>
            </div>
          ) : (
            <PageState status="loading" title="正在读取知识库详情" />
          )}
        </div>
      </Drawer>
      <AccessibleDialog
        role="dialog"
        aria-modal="true"
        aria-label={
          dialog?.kind === "add-reference"
            ? "添加 Application 引用"
            : dialog?.kind === "remove-reference"
              ? "移除 Application 引用"
              : "转移知识库所有权"
        }
        visible={Boolean(dialog)}
        header={
          dialog?.kind === "add-reference"
            ? "添加 Application 引用"
            : dialog?.kind === "remove-reference"
              ? "移除 Application 引用"
              : "转移知识库所有权"
        }
        destroyOnClose
        confirmBtn={
          dialog?.kind === "add-reference"
            ? "添加引用"
            : dialog?.kind === "remove-reference"
              ? "确认移除"
              : {
                  content: "提交所有权转移",
                  disabled:
                    !selectedTransferWorkspace ||
                    center.selected?.knowledge_base.owning_workspace?.revision == null ||
                    center.selected?.knowledge_base.ownership_revision == null,
                }
        }
        cancelBtn="取消"
        confirmLoading={center.mutation.saving}
        onCancel={closeMutationDialog}
        onClose={closeMutationDialog}
        onConfirm={() => void submitDialog()}
        className="enterprise-knowledge-base-dialog"
      >
        <div className="enterprise-knowledge-base-dialog__body">
          <div className="enterprise-knowledge-base-dialog__fields">
            {dialog?.kind === "add-reference" ? (
              <AccessibleField label="Application ID" field="application-id">
                <Input
                  value={applicationId}
                  aria-label="Application ID"
                  onChange={(value) => setApplicationId(value)}
                />
                {validationErrors.appId ? (
                  <small className="enterprise-knowledge-base-dialog__error">
                    {validationErrors.appId}
                  </small>
                ) : null}
              </AccessibleField>
            ) : null}
            {dialog?.kind === "remove-reference" ? (
              <div className="enterprise-knowledge-base-dialog__hint">
                将移除 {dialog.reference.app_name ?? dialog.reference.app_id} 的 active
                reference；服务端会校验 Revision {dialog.reference.revision ?? "未返回"}。
              </div>
            ) : null}
            {dialog?.kind === "transfer" ? (
              <AccessibleField label="目标 Workspace" field="target-workspace">
                <div
                  className="enterprise-knowledge-base-dialog__select-control"
                  role="combobox"
                  aria-label="目标 Workspace"
                  aria-haspopup="listbox"
                  tabIndex={0}
                  onClick={(event) => {
                    if (event.target === event.currentTarget) {
                      event.currentTarget.querySelector<HTMLInputElement>("input")?.click();
                    }
                  }}
                >
                  <Select
                    value={targetWorkspaceId || undefined}
                    options={transferWorkspaceOptions.map((option) => ({
                      value: option.value,
                      label: option.environment
                        ? `${option.label} · ${option.environment} · Revision ${option.revision}`
                        : `${option.label} · Revision ${option.revision}`,
                    }))}
                    disabled={!transferWorkspaceOptions.length}
                    placeholder={
                      transferWorkspaceOptions.length
                        ? "选择服务端 Workspace"
                        : "Workspace 列表未返回"
                    }
                    empty="Workspace 列表未返回"
                    onChange={(value) => setTargetWorkspaceId(String(value ?? ""))}
                  />
                </div>
                {validationErrors.workspaceId ? (
                  <small className="enterprise-knowledge-base-dialog__error">
                    {validationErrors.workspaceId}
                  </small>
                ) : null}
              </AccessibleField>
            ) : null}
            {dialog?.kind === "transfer" &&
            (center.selected?.knowledge_base.ownership_revision == null ||
              center.selected?.knowledge_base.owning_workspace?.revision == null) ? (
              <div className="enterprise-knowledge-base-dialog__hint">
                source Workspace revision 未完整返回，提交按钮保持禁用。
              </div>
            ) : dialog?.kind === "transfer" && !selectedTransferWorkspace ? (
              <div className="enterprise-knowledge-base-dialog__hint">
                请选择带权威 Workspace revision 的目标 Workspace。
              </div>
            ) : null}
            <AccessibleField label="变更原因" field="mutation-reason">
              <Textarea
                value={reason}
                aria-label="变更原因"
                autosize={{ minRows: 3, maxRows: 6 }}
                onChange={(value) => setReason(value)}
              />
              {validationErrors.reason ? (
                <small className="enterprise-knowledge-base-dialog__error">
                  {validationErrors.reason}
                </small>
              ) : null}
            </AccessibleField>
            {validationErrors.expectedProfileRevision ? (
              <small className="enterprise-knowledge-base-dialog__error">
                {validationErrors.expectedProfileRevision}
              </small>
            ) : null}
            {validationErrors.expectedOwnershipRevision ? (
              <small className="enterprise-knowledge-base-dialog__error">
                {validationErrors.expectedOwnershipRevision}
              </small>
            ) : null}
            {validationErrors.expectedSourceWorkspaceRevision ? (
              <small className="enterprise-knowledge-base-dialog__error">
                {validationErrors.expectedSourceWorkspaceRevision}
              </small>
            ) : null}
            {validationErrors.expectedTargetWorkspaceRevision ? (
              <small className="enterprise-knowledge-base-dialog__error">
                {validationErrors.expectedTargetWorkspaceRevision}
              </small>
            ) : null}
          </div>
        </div>
      </AccessibleDialog>
    </section>
  );
}
