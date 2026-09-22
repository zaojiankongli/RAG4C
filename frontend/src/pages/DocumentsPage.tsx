import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import {
  Button,
  Card,
  Dialog,
  Input,
  Popconfirm,
  Progress,
  Select,
  Tag,
  Tooltip,
  Typography,
} from "tdesign-react";
import {
  AddIcon,
  CheckCircleIcon,
  CloseCircleIcon,
  DataBaseIcon,
  DeleteIcon,
  FileExcelIcon,
  FileIcon,
  FileMarkdownIcon,
  FilePdfIcon,
  FileWordIcon,
  FolderAddIcon,
  FolderIcon,
  FolderOpenIcon,
  InfoCircleIcon,
  LoadingIcon,
  RefreshIcon,
  SearchIcon,
  SettingIcon,
  TimeIcon,
} from "tdesign-icons-react";
import PageTopbar from "../components/PageTopbar";
import PageState from "../components/PageState";
import StatCard from "../components/StatCard";
import {
  ApiError,
  batchUpdateDocumentSettings,
  createDocumentDeleteIdempotencyKey,
  fetchDocumentDeleteBatch,
  fetchDocumentPage,
  fetchDocumentDeleteOperation,
  ingestDocument,
  ingestFolder,
  reindexDocument,
  requestDocumentBatchDelete,
  requestDocumentDelete,
  shouldRetainDocumentDeleteIdempotencyKey,
  updateDocumentSettings,
} from "../api/client";
import type {
  DocumentCatalogSummaryResponse,
  DocumentDeleteOperationResponse,
  DocumentItem,
  DocumentPageQuery,
  DocumentPageResponse,
  DocumentStatus,
} from "../types/rag";
import { FONT_SIZE } from "../theme/tokens";
import {
  chunkingDecisionAria,
  formatChunkingDecision,
} from "../parse-intervention/model/chunkDiagnostics";
import { formatDuration, isDocumentSelectable } from "../documents/documentModel";
import ParseInterventionWorkspace from "../parse-intervention/ParseInterventionWorkspace";
import {
  documentWorkspaceNavigationIntent,
  documentsReturnIntent,
  parseDocumentWorkspaceLocation,
} from "../documents/documentParseRoute";
import { useConnection } from "../context/ConnectionContext";
import { navigationIntent } from "../run/appRoute";
import { readKnowledgeActorToken } from "../knowledge/workspaceScope";
import KnowledgeDocumentTable from "../documents/KnowledgeDocumentTable";
import { buildDocumentFacets, filterDocuments } from "../documents/documentWorkspaceModel";
import KnowledgeWorkspacePageState from "../knowledge/KnowledgeWorkspacePageState";
import { useKnowledgeDocuments } from "../knowledge/useKnowledgeDocuments";
import { message } from "../ui/feedback";
import {
  createRecoveryIdempotencyKey,
  recycleDocument as recycleDocumentToBin,
} from "../enterprise-content-recovery/api/recoveryApi";

const { Text } = Typography;

/** 状态机状态 -> 展示配置（后端 models/orm.py::_DOC_TRANSITIONS） */
const STATUS_META: Record<
  DocumentStatus,
  {
    label: string;
    theme: "default" | "primary" | "success" | "danger" | "warning";
    icon: ReactElement;
    hint: string;
  }
> = {
  waiting: {
    label: "排队中",
    theme: "warning",
    icon: <TimeIcon />,
    hint: "已登记，等待后台任务开始处理",
  },
  parsing: {
    label: "解析中",
    theme: "primary",
    icon: <LoadingIcon className="tdesign-icon-spin" />,
    hint: "正在从文件中提取文字、表格与版面结构",
  },
  splitting: {
    label: "切分中",
    theme: "primary",
    icon: <LoadingIcon className="tdesign-icon-spin" />,
    hint: "正在按章节或长度把文档切成检索片段",
  },
  indexing: {
    label: "索引中",
    theme: "primary",
    icon: <LoadingIcon className="tdesign-icon-spin" />,
    hint: "正在计算向量并写入向量库",
  },
  completed: {
    label: "已完成",
    theme: "success",
    icon: <CheckCircleIcon />,
    hint: "已入库，可以被问答检索到",
  },
  error: {
    label: "失败",
    theme: "danger",
    icon: <CloseCircleIcon />,
    hint: "处理过程中出错，可修复后重新索引",
  },
};

/** 处理中的状态：需要轮询刷新进度 */
const IN_FLIGHT: ReadonlySet<string> = new Set(["waiting", "parsing", "splitting", "indexing"]);
const DOCUMENT_PAGE_SIZE = 10;

function statusMeta(status: string) {
  return STATUS_META[status as DocumentStatus] ?? STATUS_META.waiting;
}

const DELETE_PENDING_STATUSES = new Set(["queued", "projecting", "finalizing"]);
const DELETE_POLL_BACKOFF_MS = [1000, 2000, 3000, 5000] as const;

type DocumentDeleteRuntime = Pick<
  DocumentDeleteOperationResponse,
  "operation_id" | "batch_operation_id" | "status" | "message"
>;

function isDeleteTerminal(status: string): boolean {
  return status === "completed" || status === "failed" || status === "rejected";
}

function waitForDeletePoll(delayMs: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Delete polling aborted", "AbortError"));
      return;
    }
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, delayMs);
    const abort = () => {
      window.clearTimeout(timer);
      signal.removeEventListener("abort", abort);
      reject(new DOMException("Delete polling aborted", "AbortError"));
    };
    signal.addEventListener("abort", abort, { once: true });
  });
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

/** 文件类型 -> 图标 + 配色 class（按扩展名兜底推断） */
function fileVisual(doc: DocumentItem): { icon: ReactNode; cls: string } {
  const raw = (doc.doc_type || "").toLowerCase();
  const ext = doc.name.split(".").pop()?.toLowerCase() ?? "";
  const kind = raw || ext;
  if (kind === "pdf") return { icon: <FilePdfIcon />, cls: "is-pdf" };
  if (kind === "word" || kind === "doc" || kind === "docx") {
    return { icon: <FileWordIcon />, cls: "is-word" };
  }
  if (kind === "excel" || kind === "xls" || kind === "xlsx" || kind === "csv") {
    return { icon: <FileExcelIcon />, cls: "is-excel" };
  }
  if (kind === "markdown" || kind === "md") {
    return { icon: <FileMarkdownIcon />, cls: "is-markdown" };
  }
  return { icon: <FileIcon />, cls: "is-text" };
}

/** 解析引擎 / PDF 类型 -> 中文 */
const ENGINE_LABELS: Record<string, string> = {
  fast: "快速通道（直取文字层）",
  vision: "视觉解析（OCR）",
  router: "自动分流",
};
const PARSE_ORIGIN_STATE = "rag4cParseWorkspace";

type DocumentsSearchRoute = {
  mode: "history" | "hash";
  pathname: string;
  params: URLSearchParams;
};

function parseDocumentsSearchRoute(
  location: Pick<Location, "pathname" | "search" | "hash">,
): DocumentsSearchRoute | null {
  const historyPath = location.pathname.replace(/\/+$/, "") || "/";
  if (historyPath === "/documents") {
    return {
      mode: "history",
      pathname: location.pathname,
      params: new URLSearchParams(location.search),
    };
  }

  const hashRoute = location.hash.replace(/^#/, "");
  const queryStart = hashRoute.indexOf("?");
  const hashPath =
    (queryStart >= 0 ? hashRoute.slice(0, queryStart) : hashRoute).replace(/\/+$/, "") || "/";
  if (hashPath !== "/documents") return null;
  return {
    mode: "hash",
    pathname: queryStart >= 0 ? hashRoute.slice(0, queryStart) : hashRoute,
    params: new URLSearchParams(queryStart >= 0 ? hashRoute.slice(queryStart + 1) : ""),
  };
}

type DocumentsFilterState = {
  keyword: string;
  status: string;
  type: string;
  engine: string;
  category: string;
  tag: string;
};

const DEFAULT_DOCUMENT_FILTERS: DocumentsFilterState = {
  keyword: "",
  status: "all",
  type: "all",
  engine: "all",
  category: "all",
  tag: "all",
};

function readDocumentsFilters(
  location: Pick<Location, "pathname" | "search" | "hash">,
): DocumentsFilterState {
  const route = parseDocumentsSearchRoute(location);
  if (!route) return DEFAULT_DOCUMENT_FILTERS;
  const params = route.params;
  return {
    keyword: params.get("q") ?? "",
    status: params.get("status") || "all",
    type: params.get("type") || params.get("doc_type") || "all",
    engine: params.get("engine") || "all",
    category: params.get("category") || params.get("folder") || "all",
    tag: params.get("tag") || "all",
  };
}

function replaceDocumentsFilter(
  location: Location,
  key: keyof DocumentsFilterState,
  value: string,
): void {
  const route = parseDocumentsSearchRoute(location);
  if (!route) return;
  const params = route.params;
  const parameter =
    key === "keyword" ? "q" : key === "type" ? "type" : key === "category" ? "category" : key;
  const normalized = value.trim();
  if (normalized && normalized !== "all") params.set(parameter, normalized);
  else params.delete(parameter);
  if (key === "type") params.delete("doc_type");
  if (key === "category") params.delete("folder");
  const query = params.toString();
  const routeUrl = `${route.pathname}${query ? `?${query}` : ""}`;
  const url =
    route.mode === "history"
      ? `${routeUrl}${location.hash}`
      : `${location.pathname}${location.search}#${routeUrl}`;
  window.history.replaceState(window.history.state, "", url);
}

function projectSummaryFacets(facets: DocumentCatalogSummaryResponse["facets"] | null) {
  if (!facets) {
    return {
      statuses: { all: 0, completed: 0, processing: 0, error: 0 },
      types: {} as Record<string, number>,
      engines: {} as Record<string, number>,
      categories: {} as Record<string, number>,
      tags: {} as Record<string, number>,
    };
  }
  const statuses = facets.statuses;
  return {
    statuses: {
      all: statuses.all,
      completed: statuses.completed,
      processing: statuses.processing,
      error: statuses.error,
    },
    types: Object.fromEntries(facets.types.map((item) => [item.value, item.count])),
    engines: Object.fromEntries(facets.engines.map((item) => [item.value, item.count])),
    categories: Object.fromEntries(
      facets.folders.map((item) => [item.path || "未分类", item.documents]),
    ),
    tags: Object.fromEntries(facets.tags.map((item) => [item.name, item.documents])),
  };
}

/**
 * 文档管理页 —— 对应后端 server/documents.py 的完整能力：
 * 列表 / 状态机进度 / 入库（后台任务）/ 增量重索引。
 *
 * 处理中的文档会被自动轮询（3s），进度条与状态标签实时更新；
 * 文档快照由 KnowledgeWorkspaceProvider 统一管理，保活页面共享刷新与错误状态。
 */
export default function DocumentsPage({
  active = true,
  onDirtyChange,
  preferModernCatalog = true,
  embedded = false,
  contentRecoveryCapabilityReady,
  contentRecoveryReadOnly = false,
}: {
  active?: boolean;
  onDirtyChange?: (dirty: boolean) => void;
  preferModernCatalog?: boolean;
  embedded?: boolean;
  contentRecoveryCapabilityReady?: boolean;
  contentRecoveryReadOnly?: boolean;
}) {
  const { online } = useConnection();
  const actorToken = readKnowledgeActorToken();
  const {
    tenantId,
    datasetId,
    workspaceScopeStatus,
    documents: docs,
    status,
    error,
    loading,
    usingMock,
    summary: workspaceSummary,
    facets: workspaceFacets,
    refresh,
    invalidate,
  } = useKnowledgeDocuments();
  const initialDocumentFilters = readDocumentsFilters(window.location);
  const [keyword, setKeyword] = useState(initialDocumentFilters.keyword);
  const [statusFilter, setStatusFilter] = useState<string>(initialDocumentFilters.status);
  const [typeFilter, setTypeFilter] = useState<string>(initialDocumentFilters.type);
  const [engineFilter, setEngineFilter] = useState<string>(initialDocumentFilters.engine);
  const [categoryFilter, setCategoryFilter] = useState<string>(initialDocumentFilters.category);
  const [tagFilter, setTagFilter] = useState<string>(initialDocumentFilters.tag);
  const [ingestOpen, setIngestOpen] = useState(false);
  const [importStep, setImportStep] = useState<1 | 2>(1);
  const [ingestPath, setIngestPath] = useState("");
  const [ingesting, setIngesting] = useState(false);
  const [importMode, setImportMode] = useState<"file" | "folder">("file");
  const [folderPath, setFolderPath] = useState("");
  const [folderImporting, setFolderImporting] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [batchDeleting, setBatchDeleting] = useState(false);
  const [singleDeletingId, setSingleDeletingId] = useState("");
  const [batchDeleteIssues, setBatchDeleteIssues] = useState<
    Array<{ documentId: string; name: string; code: string; message: string }>
  >([]);
  const [deleteRuntimeByDocument, setDeleteRuntimeByDocument] = useState<
    Record<string, DocumentDeleteRuntime>
  >({});
  const deleteLockRef = useRef(false);
  const singleDeleteKeysRef = useRef(new Map<string, string>());
  const batchDeleteKeysRef = useRef(new Map<string, string>());
  const singleIdentityByOperationRef = useRef(new Map<string, string>());
  const batchIdentityByOperationRef = useRef(new Map<string, string>());
  const operationPollsRef = useRef(new Map<string, AbortController>());
  const batchPollsRef = useRef(new Map<string, AbortController>());
  const handledTerminalOperationsRef = useRef(new Set<string>());
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsIds, setSettingsIds] = useState<string[]>([]);
  const [settingsCategory, setSettingsCategory] = useState("");
  const [settingsTags, setSettingsTags] = useState("");
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [parseRoute, setParseRoute] = useState(() =>
    parseDocumentWorkspaceLocation(window.location),
  );
  const [parseDirty, setParseDirty] = useState(false);
  const parseModeRef = useRef<"history" | "hash">(
    window.location.pathname.startsWith("/documents") ? "history" : "hash",
  );
  const modernCatalogEnabled =
    Boolean(actorToken?.trim()) && preferModernCatalog && workspaceScopeStatus === "verified";
  const [modernPage, setModernPage] = useState<DocumentPageResponse | null>(null);
  const [modernStatus, setModernStatus] = useState<"loading" | "ready" | "error">("loading");
  const [modernError, setModernError] = useState<Error | null>(null);
  const [modernLoading, setModernLoading] = useState(false);
  const [modernPageNumber, setModernPageNumber] = useState(1);
  const [modernCursorStack, setModernCursorStack] = useState<Array<string | null>>([]);
  const modernRequestRef = useRef<{ id: number; controller: AbortController } | null>(null);
  const modernRequestIdRef = useRef(0);
  const selectedDocumentsByIdRef = useRef(new Map<string, DocumentItem>());

  const modernCursor =
    modernPageNumber > 1 ? (modernCursorStack[modernPageNumber - 2] ?? null) : null;
  const hasInFlight = modernCatalogEnabled
    ? Boolean(workspaceSummary?.processing)
    : docs.some((d) => IN_FLIGHT.has(d.status));

  const setWorkspaceDirty = useCallback(
    (dirty: boolean) => {
      setParseDirty(dirty);
      onDirtyChange?.(dirty);
    },
    [onDirtyChange, setParseDirty],
  );

  const resetModernPagination = useCallback(() => {
    setModernPageNumber(1);
    setModernCursorStack([]);
    setModernPage(null);
  }, []);

  const loadModernCatalog = useCallback(async () => {
    if (!modernCatalogEnabled || !active || online !== true) return;
    modernRequestRef.current?.controller.abort();
    const controller = new AbortController();
    const requestId = modernRequestIdRef.current + 1;
    modernRequestIdRef.current = requestId;
    modernRequestRef.current = { id: requestId, controller };
    setModernLoading(true);
    setModernStatus("loading");
    setModernError(null);

    const query: DocumentPageQuery = {
      offset: modernCursor ? 0 : Math.max(0, (modernPageNumber - 1) * DOCUMENT_PAGE_SIZE),
      limit: DOCUMENT_PAGE_SIZE,
      q: keyword,
      status: statusFilter as DocumentPageQuery["status"],
      doc_type: typeFilter,
      engine: engineFilter,
      folder: categoryFilter,
      folder_mode: "exact",
      tag: tagFilter,
      lifecycle_state: "all",
      sort: "updated_at_desc",
    };
    if (modernCursor) query.cursor = modernCursor;
    const options = { tenantId, actorToken, signal: controller.signal };

    try {
      const page = await fetchDocumentPage(datasetId, query, options);
      if (controller.signal.aborted || modernRequestIdRef.current !== requestId) return;
      const maxPage = Math.max(1, Math.ceil(page.total / DOCUMENT_PAGE_SIZE));
      if (modernPageNumber > maxPage) {
        setModernPage(null);
        setModernPageNumber(maxPage);
        setModernCursorStack((current) => current.slice(0, Math.max(0, maxPage - 1)));
      } else {
        setModernPage(page);
      }
      setModernStatus("ready");
    } catch (caught) {
      if (controller.signal.aborted || modernRequestIdRef.current !== requestId) return;
      setModernStatus("error");
      setModernError(caught instanceof Error ? caught : new Error(String(caught)));
    } finally {
      if (modernRequestIdRef.current === requestId) {
        modernRequestRef.current = null;
        setModernLoading(false);
      }
    }
  }, [
    active,
    actorToken,
    categoryFilter,
    datasetId,
    engineFilter,
    modernCatalogEnabled,
    keyword,
    modernCursor,
    modernPageNumber,
    online,
    statusFilter,
    tagFilter,
    tenantId,
    typeFilter,
  ]);

  useEffect(() => {
    if (!modernCatalogEnabled || !active) return;
    if (online !== true) {
      setModernStatus("error");
      setModernError(new Error("后端服务未连接，无法读取服务端文档目录"));
      return;
    }
    void loadModernCatalog();
    return () => {
      modernRequestRef.current?.controller.abort();
    };
  }, [active, modernCatalogEnabled, loadModernCatalog, online]);

  const refreshVisibleDocuments = useCallback(() => {
    if (modernCatalogEnabled) void loadModernCatalog();
    invalidate();
  }, [modernCatalogEnabled, invalidate, loadModernCatalog]);

  useEffect(() => {
    const onLocationChange = () => {
      const nextFilters = readDocumentsFilters(window.location);
      const filtersChanged =
        nextFilters.keyword !== keyword ||
        nextFilters.status !== statusFilter ||
        nextFilters.type !== typeFilter ||
        nextFilters.engine !== engineFilter ||
        nextFilters.category !== categoryFilter ||
        nextFilters.tag !== tagFilter;
      if (filtersChanged) {
        setKeyword(nextFilters.keyword);
        setStatusFilter(nextFilters.status);
        setTypeFilter(nextFilters.type);
        setEngineFilter(nextFilters.engine);
        setCategoryFilter(nextFilters.category);
        setTagFilter(nextFilters.tag);
        resetModernPagination();
      }
      const next = parseDocumentWorkspaceLocation(window.location);
      if (parseRoute && (!next || next.docId !== parseRoute.docId) && parseDirty) {
        if (!window.confirm("当前切片修改尚未提交。离开解析干预工作区将丢弃草稿，是否继续？")) {
          const intent = documentWorkspaceNavigationIntent(
            parseModeRef.current === "history"
              ? { pathname: "/documents", search: "", hash: "" }
              : { pathname: "/", search: "", hash: "#/documents" },
            parseRoute.docId,
          );
          if (intent.mode === "history")
            window.history.pushState({ [PARSE_ORIGIN_STATE]: true }, "", intent.url);
          else window.history.pushState({ [PARSE_ORIGIN_STATE]: true }, "", `#${intent.url}`);
          return;
        }
        setWorkspaceDirty(false);
      }
      setParseRoute(next);
    };
    window.addEventListener("popstate", onLocationChange);
    window.addEventListener("hashchange", onLocationChange);
    return () => {
      window.removeEventListener("popstate", onLocationChange);
      window.removeEventListener("hashchange", onLocationChange);
    };
  }, [
    categoryFilter,
    engineFilter,
    keyword,
    parseDirty,
    parseRoute,
    resetModernPagination,
    setWorkspaceDirty,
    statusFilter,
    tagFilter,
    typeFilter,
  ]);

  useEffect(() => {
    if (!parseDirty) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [parseDirty]);

  const openParseWorkspace = useCallback(
    (document: DocumentItem) => {
      if (usingMock || (document.lifecycle_state ?? "active") !== "active") return;
      const intent = documentWorkspaceNavigationIntent(window.location, document.id);
      parseModeRef.current = intent.mode;
      if (intent.mode === "history")
        window.history.pushState(
          { ...window.history.state, [PARSE_ORIGIN_STATE]: true },
          "",
          intent.url,
        );
      else
        window.history.pushState(
          { ...window.history.state, [PARSE_ORIGIN_STATE]: true },
          "",
          `#${intent.url}`,
        );
      setWorkspaceDirty(false);
      setParseRoute({ docId: document.id, chunkId: "", datasetId: "" });
    },
    [setParseRoute, setWorkspaceDirty, usingMock],
  );

  const closeParseWorkspace = useCallback(() => {
    if (parseDirty && !window.confirm("当前切片修改尚未提交。返回文档列表将丢弃草稿，是否继续？"))
      return;
    setWorkspaceDirty(false);
    if (window.history.state?.[PARSE_ORIGIN_STATE]) {
      window.history.back();
      setParseRoute(null);
      return;
    }
    const intent = documentsReturnIntent(window.location);
    if (intent.mode === "history")
      window.history.replaceState(window.history.state, "", intent.url);
    else window.history.replaceState(window.history.state, "", `#${intent.url}`);
    setParseRoute(null);
  }, [parseDirty, setParseRoute, setWorkspaceDirty]);

  const catalogDocuments = useMemo(
    () => (modernCatalogEnabled ? (modernPage?.items ?? []) : docs),
    [docs, modernCatalogEnabled, modernPage],
  );
  const catalogStatus = modernCatalogEnabled ? modernStatus : status;
  const catalogError = modernCatalogEnabled ? modernError : error;
  const catalogLoading = modernCatalogEnabled ? modernLoading : loading;
  const catalogUsingMock = !modernCatalogEnabled && usingMock;
  const knownDocuments = useMemo(() => {
    const next = new Map<string, DocumentItem>();
    for (const document of docs) next.set(document.id, document);
    for (const document of catalogDocuments) next.set(document.id, document);
    return next;
  }, [catalogDocuments, docs]);

  useEffect(() => {
    const selected = new Set(selectedIds);
    for (const document of knownDocuments.values()) {
      if (selected.has(document.id)) selectedDocumentsByIdRef.current.set(document.id, document);
    }
    for (const id of selectedDocumentsByIdRef.current.keys()) {
      if (!selected.has(id)) selectedDocumentsByIdRef.current.delete(id);
    }
  }, [knownDocuments, selectedIds]);

  useEffect(() => {
    if (modernCatalogEnabled) return;
    setSelectedIds((current) =>
      current.filter((id) =>
        docs.some((doc) => {
          if (doc.id !== id) return false;
          const runtime = deleteRuntimeByDocument[id];
          return (
            ((doc.lifecycle_state ?? "active") === "active" && isDocumentSelectable(doc)) ||
            Boolean(
              runtime &&
              (DELETE_PENDING_STATUSES.has(runtime.status) || runtime.status === "failed"),
            )
          );
        }),
      ),
    );
  }, [deleteRuntimeByDocument, docs, modernCatalogEnabled]);

  // 兼容模式继续依赖共享快照轮询；现代目录由 summary.processing 驱动。
  useEffect(() => {
    if (!active || modernCatalogEnabled || usingMock || !hasInFlight) return;
    const timer = setInterval(() => void refresh(), 3000);
    return () => clearInterval(timer);
  }, [active, modernCatalogEnabled, usingMock, hasInFlight, refresh]);

  useEffect(() => {
    if (!active || !modernCatalogEnabled || !workspaceSummary?.processing) return;
    const timer = setInterval(() => {
      void loadModernCatalog();
      void refresh();
    }, 3000);
    return () => clearInterval(timer);
  }, [active, modernCatalogEnabled, loadModernCatalog, refresh, workspaceSummary?.processing]);

  const showWorkspaceState = modernCatalogEnabled
    ? modernStatus === "error" || (modernStatus === "loading" && !modernPage)
    : status === "error" || (status === "loading" && docs.length === 0);

  const stats = useMemo(() => {
    if (modernCatalogEnabled && workspaceSummary) return workspaceSummary;
    const total = docs.length;
    const completed = docs.filter((d) => d.status === "completed").length;
    const failed = docs.filter((d) => d.status === "error").length;
    const processing = docs.filter((d) => IN_FLIGHT.has(d.status)).length;
    const chunks = docs.reduce((sum, d) => sum + (d.chunk_count || 0), 0);
    return { total, completed, failed, processing, chunks };
  }, [docs, modernCatalogEnabled, workspaceSummary]);

  const facets = useMemo(
    () =>
      modernCatalogEnabled ? projectSummaryFacets(workspaceFacets) : buildDocumentFacets(docs),
    [docs, modernCatalogEnabled, workspaceFacets],
  );
  const filtered = useMemo(
    () =>
      modernCatalogEnabled
        ? catalogDocuments
        : filterDocuments(docs, {
            keyword,
            status: statusFilter,
            type: typeFilter,
            engine: engineFilter,
            category: categoryFilter,
            tag: tagFilter,
          }),
    [
      catalogDocuments,
      docs,
      engineFilter,
      modernCatalogEnabled,
      keyword,
      statusFilter,
      tagFilter,
      typeFilter,
      categoryFilter,
    ],
  );
  const tableDocuments = useMemo(
    () =>
      filtered.map((document) => {
        const runtime = deleteRuntimeByDocument[document.id];
        const runtimeLifecycle = runtime
          ? runtime.status === "completed"
            ? "deleted"
            : runtime.status === "failed"
              ? "delete_failed"
              : "deleting"
          : (document.lifecycle_state ?? "active");
        if (runtimeLifecycle === "active") return document;
        return { ...document, lifecycle_state: runtimeLifecycle, status: "indexing" };
      }),
    [filtered, deleteRuntimeByDocument],
  );

  const applyDocumentFilter = (key: keyof DocumentsFilterState, value: string) => {
    const normalized = value.trim() || "all";
    if (key === "keyword") setKeyword(value);
    else if (key === "status") setStatusFilter(normalized);
    else if (key === "type") setTypeFilter(normalized);
    else if (key === "engine") setEngineFilter(normalized);
    else if (key === "category") setCategoryFilter(normalized);
    else if (key === "tag") setTagFilter(normalized);
    replaceDocumentsFilter(window.location, key, value);
    resetModernPagination();
  };

  const clearAllDocumentFilters = () => {
    applyDocumentFilter("keyword", "");
    applyDocumentFilter("status", "all");
    applyDocumentFilter("type", "all");
    applyDocumentFilter("engine", "all");
    applyDocumentFilter("category", "all");
    applyDocumentFilter("tag", "all");
  };

  const goToNextModernPage = useCallback(() => {
    if (!modernCatalogEnabled || !modernPage) return;
    const hasNext =
      Boolean(modernPage.next_cursor) || modernPageNumber * DOCUMENT_PAGE_SIZE < modernPage.total;
    if (!hasNext) return;
    setModernCursorStack((current) => [
      ...current.slice(0, modernPageNumber - 1),
      modernPage.next_cursor,
    ]);
    setModernPageNumber((current) => current + 1);
    setModernPage(null);
  }, [modernCatalogEnabled, modernPage, modernPageNumber]);

  const goToPreviousModernPage = useCallback(() => {
    if (!modernCatalogEnabled || modernPageNumber <= 1) return;
    setModernCursorStack((current) => current.slice(0, -1));
    setModernPageNumber((current) => Math.max(1, current - 1));
    setModernPage(null);
  }, [modernCatalogEnabled, modernPageNumber]);

  const handleIngest = async () => {
    const path = ingestPath.trim();
    if (!path) return;
    setIngesting(true);
    try {
      const res = await ingestDocument({ file_path: path, dataset_id: datasetId });
      message.success(res.note || "入库任务已启动，进度会自动刷新");
      setIngestOpen(false);
      setIngestPath("");
      refreshVisibleDocuments();
    } catch (e) {
      message.error("入库失败：" + (e instanceof Error ? e.message : String(e)));
    } finally {
      setIngesting(false);
    }
  };

  const handleReindex = async (doc: DocumentItem, force: boolean) => {
    try {
      await reindexDocument(doc.id, force);
      message.success("重新索引已启动，进度会自动刷新");
      refreshVisibleDocuments();
    } catch (e) {
      message.error("重新索引失败：" + (e instanceof Error ? e.message : String(e)));
    }
  };

  const handleFolderIngest = async () => {
    const path = folderPath.trim();
    if (!path || folderImporting) return;
    setFolderImporting(true);
    try {
      const res = await ingestFolder({ folder_path: path, dataset_id: datasetId });
      message.success(
        res.note || `已登记 ${res.queued_count} 个文件，跳过 ${res.skipped_count} 个`,
      );
      setIngestOpen(false);
      setFolderPath("");
      refreshVisibleDocuments();
    } catch (e) {
      message.error("批量导入失败：" + (e instanceof Error ? e.message : String(e)));
    } finally {
      setFolderImporting(false);
    }
  };

  const requireGeneration = (document: DocumentItem): number | null => {
    const generation = document.mutation_generation;
    if (typeof generation !== "number" || !Number.isInteger(generation) || generation < 0) {
      message.error("文档缺少删除 Generation，请刷新后重试");
      return null;
    }
    return generation;
  };

  const recordDeleteItems = (items: DocumentDeleteOperationResponse[]) => {
    setDeleteRuntimeByDocument((current) => {
      const next = { ...current };
      for (const item of items) {
        if (item.status === "rejected") continue;
        next[item.document_id] = {
          operation_id: item.operation_id,
          batch_operation_id: item.batch_operation_id,
          status: item.status,
          message: item.message,
        };
      }
      return next;
    });
  };

  const settleImmediateItems = useCallback(
    (items: DocumentDeleteOperationResponse[], documentNames: Map<string, string>) => {
      const completedIds: string[] = [];
      const notFoundIds: string[] = [];
      const issues: Array<{ documentId: string; name: string; code: string; message: string }> = [];
      for (const item of items) {
        if (
          !isDeleteTerminal(item.status) ||
          handledTerminalOperationsRef.current.has(item.operation_id)
        ) {
          continue;
        }
        handledTerminalOperationsRef.current.add(item.operation_id);
        if (item.status === "completed") completedIds.push(item.document_id);
        if (item.status === "rejected" && item.code === "not_found")
          notFoundIds.push(item.document_id);
        if (item.status === "failed" || item.status === "rejected") {
          issues.push({
            documentId: item.document_id,
            name: documentNames.get(item.document_id) ?? item.document_id,
            code: item.code ?? item.status,
            message:
              item.status === "failed"
                ? (item.message || "后台清理失败") + "，暂无在线重试，请联系管理员处理"
                : item.message || "删除请求未被接受",
          });
        }
      }
      if (completedIds.length || notFoundIds.length) {
        const removed = new Set([...completedIds, ...notFoundIds]);
        setSelectedIds((ids) => ids.filter((id) => !removed.has(id)));
      }
      if (issues.length) {
        setBatchDeleteIssues((current) => {
          const next = new Map(current.map((issue) => [issue.documentId, issue]));
          for (const issue of issues) next.set(issue.documentId, issue);
          return [...next.values()];
        });
      }
      if (completedIds.length) message.success("已删除 " + completedIds.length + " 篇文档");
      if (issues.length) message.warning(issues.length + " 篇文档未完成删除，请查看原因");
      if (completedIds.length || issues.length || notFoundIds.length) refreshVisibleDocuments();
    },
    [refreshVisibleDocuments, setBatchDeleteIssues, setSelectedIds],
  );

  const handleDelete = async (doc: DocumentItem) => {
    if (
      (doc.lifecycle_state ?? "active") !== "active" ||
      !isDocumentSelectable(doc) ||
      deleteLockRef.current
    ) {
      return;
    }
    const expectedGeneration = requireGeneration(doc);
    if (expectedGeneration === null) return;
    if (contentRecoveryCapabilityReady !== undefined) {
      if (!contentRecoveryCapabilityReady || contentRecoveryReadOnly) {
        message.warning("内容恢复权威不可用，未执行永久删除");
        return;
      }
      if (!actorToken) {
        message.error("企业身份不可用，无法移入回收站");
        return;
      }
      const identity = JSON.stringify([doc.id, expectedGeneration, "recycle"]);
      const idempotencyKey =
        singleDeleteKeysRef.current.get(identity) ?? createRecoveryIdempotencyKey();
      singleDeleteKeysRef.current.set(identity, idempotencyKey);
      deleteLockRef.current = true;
      setSingleDeletingId(doc.id);
      try {
        await recycleDocumentToBin(
          { tenantId: doc.tenant_id ?? tenantId, actorToken },
          doc.id,
          {
            datasetId,
            expectedMutationGeneration: expectedGeneration,
            reason: "Documents workspace explicit recycle",
          },
          { idempotencyKey },
        );
        singleDeleteKeysRef.current.delete(identity);
        setSelectedIds((ids) => ids.filter((id) => id !== doc.id));
        message.success(`已将 ${doc.name} 移入回收站`);
        invalidate();
        refreshVisibleDocuments();
      } catch (error) {
        message.error(
          "移入回收站失败：" + (error instanceof Error ? error.message : String(error)),
        );
      } finally {
        deleteLockRef.current = false;
        setSingleDeletingId("");
      }
      return;
    }
    const identity = JSON.stringify([doc.id, expectedGeneration]);
    const idempotencyKey =
      singleDeleteKeysRef.current.get(identity) ??
      createDocumentDeleteIdempotencyKey("delete-document");
    singleDeleteKeysRef.current.set(identity, idempotencyKey);
    deleteLockRef.current = true;
    setSingleDeletingId(doc.id);
    try {
      const accepted = await requestDocumentDelete(
        datasetId,
        doc.id,
        { expected_generation: expectedGeneration },
        { tenantId: doc.tenant_id ?? "default", idempotencyKey },
      );
      singleIdentityByOperationRef.current.set(accepted.operation_id, identity);
      recordDeleteItems([accepted]);
      refreshVisibleDocuments();
      if (DELETE_PENDING_STATUSES.has(accepted.status)) {
        message.info("删除请求已接受，正在清理向量与图谱…");
      } else if (accepted.status === "completed") {
        handledTerminalOperationsRef.current.add(accepted.operation_id);
        singleDeleteKeysRef.current.delete(identity);
        setSelectedIds((ids) => ids.filter((id) => id !== doc.id));
        message.success("已删除 " + doc.name);
        refreshVisibleDocuments();
      } else if (accepted.status === "failed") {
        handledTerminalOperationsRef.current.add(accepted.operation_id);
        singleDeleteKeysRef.current.delete(identity);
        message.error(
          "删除失败：" +
            (accepted.message || "后台清理未完成") +
            "，暂无在线重试，请联系管理员处理",
        );
      }
    } catch (error) {
      if (!shouldRetainDocumentDeleteIdempotencyKey(error)) {
        singleDeleteKeysRef.current.delete(identity);
        refreshVisibleDocuments();
      }
      if (error instanceof ApiError && error.status === 404) {
        setSelectedIds((ids) => ids.filter((id) => id !== doc.id));
        message.info("文档已不存在，列表已同步刷新");
      } else {
        message.error("删除请求失败：" + (error instanceof Error ? error.message : String(error)));
      }
    } finally {
      deleteLockRef.current = false;
      setSingleDeletingId("");
    }
  };

  const handleBatchDelete = async () => {
    if (!selectedIds.length || deleteLockRef.current) return;
    const requestedIds = [...selectedIds];
    const selectedDocuments = requestedIds
      .map((id) => knownDocuments.get(id) ?? selectedDocumentsByIdRef.current.get(id))
      .filter((document): document is DocumentItem => Boolean(document));
    if (selectedDocuments.length !== requestedIds.length) {
      message.error("部分文档已不在当前列表，请刷新后重试");
      return;
    }
    const items: Array<{ document_id: string; expected_generation: number }> = [];
    for (const document of selectedDocuments) {
      const generation = requireGeneration(document);
      if (generation === null) return;
      items.push({ document_id: document.id, expected_generation: generation });
    }
    if (contentRecoveryCapabilityReady !== undefined) {
      if (!contentRecoveryCapabilityReady || contentRecoveryReadOnly) {
        message.warning("内容恢复权威不可用，未执行永久删除");
        return;
      }
      if (!actorToken) {
        message.error("企业身份不可用，无法批量移入回收站");
        return;
      }
      deleteLockRef.current = true;
      setBatchDeleting(true);
      setBatchDeleteIssues([]);
      const issues: Array<{ documentId: string; name: string; code: string; message: string }> = [];
      const recycledIds: string[] = [];
      try {
        for (const document of selectedDocuments) {
          const generation = requireGeneration(document);
          if (generation === null) continue;
          const identity = JSON.stringify([document.id, generation, "recycle"]);
          const idempotencyKey =
            singleDeleteKeysRef.current.get(identity) ?? createRecoveryIdempotencyKey();
          singleDeleteKeysRef.current.set(identity, idempotencyKey);
          try {
            await recycleDocumentToBin(
              { tenantId: document.tenant_id ?? tenantId, actorToken },
              document.id,
              {
                datasetId,
                expectedMutationGeneration: generation,
                reason: "Documents workspace explicit bulk recycle",
              },
              { idempotencyKey },
            );
            singleDeleteKeysRef.current.delete(identity);
            recycledIds.push(document.id);
          } catch (error) {
            issues.push({
              documentId: document.id,
              name: document.name,
              code: "recycle_failed",
              message: error instanceof Error ? error.message : String(error),
            });
          }
        }
        setSelectedIds((ids) => ids.filter((id) => !recycledIds.includes(id)));
        setBatchDeleteIssues(issues);
        if (recycledIds.length) message.success(`已将 ${recycledIds.length} 篇文档移入回收站`);
        if (issues.length) message.warning(`${issues.length} 篇文档未能移入回收站`);
        invalidate();
        refreshVisibleDocuments();
      } finally {
        deleteLockRef.current = false;
        setBatchDeleting(false);
      }
      return;
    }
    const signature = JSON.stringify(
      items.map((item) => [item.document_id, item.expected_generation]),
    );
    const idempotencyKey =
      batchDeleteKeysRef.current.get(signature) ??
      createDocumentDeleteIdempotencyKey("delete-batch");
    batchDeleteKeysRef.current.set(signature, idempotencyKey);
    const documentNames = new Map(
      Array.from(knownDocuments.values()).map((document) => [document.id, document.name]),
    );
    deleteLockRef.current = true;
    setBatchDeleting(true);
    setBatchDeleteIssues([]);
    try {
      const accepted = await requestDocumentBatchDelete(
        datasetId,
        { items },
        { tenantId: selectedDocuments[0].tenant_id ?? "default", idempotencyKey },
      );
      batchIdentityByOperationRef.current.set(accepted.batch_operation_id, signature);
      recordDeleteItems(accepted.items);
      settleImmediateItems(accepted.items, documentNames);
      refreshVisibleDocuments();
      if (accepted.items.some((item) => DELETE_PENDING_STATUSES.has(item.status))) {
        message.info("删除请求已接受，正在清理向量与图谱…");
      } else {
        batchDeleteKeysRef.current.delete(signature);
      }
    } catch (error) {
      if (!shouldRetainDocumentDeleteIdempotencyKey(error)) {
        batchDeleteKeysRef.current.delete(signature);
        refreshVisibleDocuments();
      }
      message.error(
        "批量删除请求失败：" + (error instanceof Error ? error.message : String(error)),
      );
    } finally {
      deleteLockRef.current = false;
      setBatchDeleting(false);
    }
  };

  useEffect(() => {
    if (!active) return;
    setDeleteRuntimeByDocument((current) => {
      let changed = false;
      const next = { ...current };
      for (const document of docs) {
        const operationId = document.active_delete_operation_id;
        const lifecycle = document.lifecycle_state ?? "active";
        if (
          !operationId ||
          !["delete_requested", "deleting", "delete_failed"].includes(lifecycle)
        ) {
          continue;
        }
        if (next[document.id]?.operation_id === operationId) continue;
        next[document.id] = {
          operation_id: operationId,
          batch_operation_id: null,
          status: lifecycle === "delete_failed" ? "failed" : "projecting",
          message: null,
        };
        changed = true;
      }
      return changed ? next : current;
    });
  }, [active, datasetId, docs]);

  useEffect(() => {
    const abortAll = () => {
      for (const controller of operationPollsRef.current.values()) controller.abort();
      for (const controller of batchPollsRef.current.values()) controller.abort();
      operationPollsRef.current.clear();
      batchPollsRef.current.clear();
    };
    if (!active) abortAll();
    return abortAll;
  }, [active, datasetId]);

  useEffect(() => {
    setDeleteRuntimeByDocument({});
    handledTerminalOperationsRef.current.clear();
  }, [datasetId]);

  useEffect(() => {
    if (!active) return;
    const documentNames = new Map(
      Array.from(knownDocuments.values()).map((document) => [document.id, document.name]),
    );
    const tenantByDocument = new Map(
      Array.from(knownDocuments.values())
        .filter((document) => (document.dataset_id ?? datasetId) === datasetId)
        .map((document) => [document.id, document.tenant_id ?? "default"]),
    );
    const batchIds = new Set(
      Object.values(deleteRuntimeByDocument)
        .filter(
          (runtime) =>
            DELETE_PENDING_STATUSES.has(runtime.status) &&
            runtime.batch_operation_id &&
            batchIdentityByOperationRef.current.has(runtime.batch_operation_id),
        )
        .map((runtime) => runtime.batch_operation_id as string),
    );

    for (const [documentId, runtime] of Object.entries(deleteRuntimeByDocument)) {
      const belongsToKnownBatch = Boolean(
        runtime.batch_operation_id &&
        batchIdentityByOperationRef.current.has(runtime.batch_operation_id),
      );
      if (!DELETE_PENDING_STATUSES.has(runtime.status) || belongsToKnownBatch) continue;
      if (
        !tenantByDocument.has(documentId) ||
        operationPollsRef.current.has(runtime.operation_id)
      ) {
        continue;
      }
      const controller = new AbortController();
      operationPollsRef.current.set(runtime.operation_id, controller);
      const tenantId = tenantByDocument.get(documentId) ?? "default";
      void (async () => {
        let delayIndex = 0;
        try {
          while (!controller.signal.aborted) {
            await waitForDeletePoll(DELETE_POLL_BACKOFF_MS[delayIndex], controller.signal);
            const current = await fetchDocumentDeleteOperation(datasetId, runtime.operation_id, {
              tenantId,
              signal: controller.signal,
            });
            setDeleteRuntimeByDocument((state) => ({
              ...state,
              [current.document_id]: {
                operation_id: current.operation_id,
                batch_operation_id: current.batch_operation_id,
                status: current.status,
                message: current.message,
              },
            }));
            if (isDeleteTerminal(current.status)) {
              if (!handledTerminalOperationsRef.current.has(current.operation_id)) {
                handledTerminalOperationsRef.current.add(current.operation_id);
                const identity = singleIdentityByOperationRef.current.get(current.operation_id);
                if (identity) singleDeleteKeysRef.current.delete(identity);
                if (current.status === "completed") {
                  setSelectedIds((ids) => ids.filter((id) => id !== current.document_id));
                  message.success(
                    "已删除 " + (documentNames.get(current.document_id) ?? current.document_id),
                  );
                } else if (current.status === "failed") {
                  message.error(
                    "删除失败：" +
                      (current.message || "后台清理未完成") +
                      "，暂无在线重试，请联系管理员处理",
                  );
                }
                refreshVisibleDocuments();
              }
              break;
            }
            delayIndex = (delayIndex + 1) % DELETE_POLL_BACKOFF_MS.length;
          }
        } catch (error) {
          if (!controller.signal.aborted && !isAbortError(error)) {
            operationPollsRef.current.delete(runtime.operation_id);
            setDeleteRuntimeByDocument((state) => ({ ...state }));
          }
        } finally {
          operationPollsRef.current.delete(runtime.operation_id);
        }
      })();
    }

    for (const batchId of batchIds) {
      if (batchPollsRef.current.has(batchId)) continue;
      const controller = new AbortController();
      batchPollsRef.current.set(batchId, controller);
      const documentId = Object.keys(deleteRuntimeByDocument).find(
        (id) => deleteRuntimeByDocument[id].batch_operation_id === batchId,
      );
      const tenantId = (documentId && tenantByDocument.get(documentId)) || "default";
      void (async () => {
        let delayIndex = 0;
        try {
          while (!controller.signal.aborted) {
            await waitForDeletePoll(DELETE_POLL_BACKOFF_MS[delayIndex], controller.signal);
            const current = await fetchDocumentDeleteBatch(datasetId, batchId, {
              tenantId,
              signal: controller.signal,
            });
            setDeleteRuntimeByDocument((state) => {
              const next = { ...state };
              for (const item of current.items) {
                if (item.status === "rejected") continue;
                next[item.document_id] = {
                  operation_id: item.operation_id,
                  batch_operation_id: item.batch_operation_id,
                  status: item.status,
                  message: item.message,
                };
              }
              return next;
            });
            settleImmediateItems(current.items, documentNames);
            const stillPending = current.items.some((item) =>
              DELETE_PENDING_STATUSES.has(item.status),
            );
            if (current.status !== "running" || !stillPending) {
              const identity = batchIdentityByOperationRef.current.get(batchId);
              if (identity) batchDeleteKeysRef.current.delete(identity);
              refreshVisibleDocuments();
              break;
            }
            delayIndex = (delayIndex + 1) % DELETE_POLL_BACKOFF_MS.length;
          }
        } catch (error) {
          if (!controller.signal.aborted && !isAbortError(error)) {
            batchPollsRef.current.delete(batchId);
            setDeleteRuntimeByDocument((state) => ({ ...state }));
          }
        } finally {
          batchPollsRef.current.delete(batchId);
        }
      })();
    }
  }, [
    active,
    datasetId,
    deleteRuntimeByDocument,
    knownDocuments,
    refreshVisibleDocuments,
    settleImmediateItems,
  ]);

  const openDocumentSettings = (doc: DocumentItem) => {
    setSettingsIds([doc.id]);
    setSettingsCategory(doc.logical_folder_path ?? "");
    setSettingsTags((doc.tags ?? []).join(", "));
    setSettingsOpen(true);
  };

  const openBatchSettings = () => {
    setSettingsIds(selectedIds);
    setSettingsCategory("");
    setSettingsTags("");
    setSettingsOpen(true);
  };

  const saveDocumentSettings = async () => {
    if (!settingsIds.length || settingsSaving) return;
    const tags = settingsTags
      .split(/[，,]/)
      .map((tag) => tag.trim())
      .filter(Boolean);
    setSettingsSaving(true);
    try {
      if (settingsIds.length === 1) {
        await updateDocumentSettings(settingsIds[0], {
          logical_folder_path: settingsCategory.trim(),
          tags,
        });
        message.success("文档分类和标签已更新");
      } else {
        const result = await batchUpdateDocumentSettings({
          document_ids: settingsIds,
          logical_folder_path: settingsCategory.trim(),
          add_tags: tags,
        });
        message.success(`已更新 ${result.updated} 篇文档`);
      }
      setSettingsOpen(false);
      refreshVisibleDocuments();
    } catch (error) {
      message.error(
        "保存文档设置失败：" + (error instanceof Error ? error.message : String(error)),
      );
    } finally {
      setSettingsSaving(false);
    }
  };

  const selectedDocuments = selectedIds
    .map((id) => knownDocuments.get(id) ?? selectedDocumentsByIdRef.current.get(id))
    .filter((document): document is DocumentItem => Boolean(document));
  const selectedChunkCount = selectedDocuments.reduce(
    (total, document) => total + (document.chunk_count || 0),
    0,
  );

  const renderFileCell = (doc: DocumentItem) => {
    const visual = fileVisual(doc);
    return (
      <div className="doc-name">
        <span className={`doc-file-icon ${visual.cls}`}>{visual.icon}</span>
        <div className="doc-name-text">
          <div className="doc-name-title" title={doc.name}>
            {doc.name}
          </div>
          <div className="doc-name-path" title={doc.id}>
            {doc.id}
          </div>
          <div className="doc-management-meta">
            <span>{doc.logical_folder_path || "未分类"}</span>
            {(doc.tags ?? []).slice(0, 2).map((tag) => (
              <em key={tag}>{tag}</em>
            ))}
          </div>
        </div>
      </div>
    );
  };

  const renderParserProfile = (doc: DocumentItem) => {
    const meta = doc.parser_meta ?? {};
    const engine = ENGINE_LABELS[meta.engine ?? ""] ?? (meta.engine || "文本直读");
    const diag = formatChunkingDecision(meta);
    return (
      <div
        className="doc-parser-profile"
        aria-label={`${doc.name} 解析画像：${chunkingDecisionAria(diag)}`}
        title={chunkingDecisionAria(diag)}
        data-testid={`doc-parser-profile-${doc.id}`}
      >
        <span>{engine}</span>
        <small>{diag.modeLabel}</small>
        {diag.reasonCodeLabel ? (
          <small className="doc-parser-chunk-reason" data-testid="doc-parser-chunk-reason">
            {diag.reasonCodeLabel}
          </small>
        ) : null}
        {diag.reason ? (
          <small className="doc-parser-chunk-reason-detail" data-testid="doc-parser-chunk-reason-detail">
            {diag.reason}
          </small>
        ) : null}
        <small className="doc-parser-duration">
          总耗时 {formatDuration(meta.total_ms ?? meta.parse_ms)}
        </small>
      </div>
    );
  };

  const renderStatus = (doc: DocumentItem) => {
    const runtime = deleteRuntimeByDocument[doc.id];
    if (doc.lifecycle_state === "delete_requested" || doc.lifecycle_state === "deleting") {
      return (
        <div className="doc-progress">
          <Tag
            theme="primary"
            variant="light"
            shape="round"
            icon={<LoadingIcon className="tdesign-icon-spin" />}
          >
            删除清理中
          </Tag>
          <span className="doc-status-text">正在清理向量与图谱</span>
        </div>
      );
    }
    if (doc.lifecycle_state === "delete_failed") {
      return (
        <div className="doc-progress">
          <Tag theme="danger" variant="light" shape="round" icon={<CloseCircleIcon />}>
            删除失败
          </Tag>
          <span className="doc-status-text">
            {runtime?.message
              ? runtime.message + "；暂无在线重试，请联系管理员"
              : "暂无在线重试，请联系管理员"}
          </span>
        </div>
      );
    }
    if (doc.lifecycle_state === "deleted") {
      return (
        <Tag theme="default" variant="light" shape="round">
          已删除
        </Tag>
      );
    }
    const presentation = statusMeta(doc.status);
    const inFlight = IN_FLIGHT.has(doc.status);
    return (
      <div className="doc-progress">
        <Tooltip content={doc.error_message || presentation.hint} trigger="hover" showArrow>
          <span
            className="doc-status-trigger"
            tabIndex={0}
            aria-label={`${presentation.label}：${doc.error_message || presentation.hint}`}
          >
            <Tag theme={presentation.theme} variant="light" shape="round" icon={presentation.icon}>
              {presentation.label}
            </Tag>
          </span>
        </Tooltip>
        {inFlight && doc.status !== "waiting" ? (
          <Progress
            percentage={Math.round((doc.progress ?? 0) * 100)}
            size="small"
            status="active"
            label={false}
          />
        ) : null}
        {inFlight && doc.status_detail ? (
          <span className="doc-status-text" title={doc.status_detail}>
            {doc.status_detail}
          </span>
        ) : null}
        {doc.status === "error" && doc.error_message ? (
          <span className="doc-status-text" title={doc.error_message}>
            {doc.error_message}
          </span>
        ) : null}
      </div>
    );
  };

  const renderActions = (doc: DocumentItem) => {
    const deleting = (doc.lifecycle_state ?? "active") !== "active";
    const inFlight = IN_FLIGHT.has(doc.status) || deleting;
    const force = doc.status === "error" && !deleting;
    const reindexButton = (
      <Button
        tag="button"
        size="small"
        variant="outline"
        icon={<RefreshIcon />}
        disabled={catalogUsingMock || inFlight}
        onClick={force ? undefined : () => void handleReindex(doc, false)}
      >
        重新索引
      </Button>
    );
    return (
      <>
        <Button
          tag="button"
          size="small"
          variant="text"
          icon={<SettingIcon />}
          disabled={catalogUsingMock || deleting}
          onClick={() => openDocumentSettings(doc)}
        >
          设置
        </Button>
        <Tooltip
          content={
            catalogUsingMock
              ? "演示数据不可操作"
              : inFlight
                ? "任务进行中，完成后可重新索引"
                : force
                  ? "修复问题后强制重建索引"
                  : "文件内容有变化时增量重建索引"
          }
          trigger="hover"
        >
          <span>
            {force && !catalogUsingMock && !inFlight ? (
              <Popconfirm
                theme="danger"
                content={`将丢弃「${doc.name}」已有片段并从头解析，大文件可能需要数分钟。`}
                confirmBtn={{ content: "重建", theme: "danger" }}
                cancelBtn={{ content: "取消" }}
                onConfirm={() => void handleReindex(doc, true)}
              >
                {reindexButton}
              </Popconfirm>
            ) : (
              reindexButton
            )}
          </span>
        </Tooltip>
        <Popconfirm
          theme="danger"
          content={
            <div className="document-delete-confirm">
              <strong>{contentRecoveryCapabilityReady === true ? "移入回收站" : "删除文档"}</strong>
              <p>
                {contentRecoveryCapabilityReady === true
                  ? `文档「${doc.name}」将停止检索并进入企业回收站，可在保留期内恢复。`
                  : `提交删除请求后将异步清理「${doc.name}」的 ${doc.chunk_count || 0} `}
                个检索片段及关联图谱。
              </p>
            </div>
          }
          confirmBtn={{
            content: contentRecoveryCapabilityReady === true ? "确认移入" : "确认删除",
            theme: contentRecoveryCapabilityReady === true ? "warning" : "danger",
            loading: singleDeletingId === doc.id,
            disabled: Boolean(singleDeletingId || batchDeleting),
          }}
          cancelBtn={{ content: "取消" }}
          onConfirm={() => void handleDelete(doc)}
        >
          <Button
            tag="button"
            size="small"
            variant="text"
            theme="danger"
            icon={<DeleteIcon />}
            aria-label={`${contentRecoveryCapabilityReady === true ? "移入回收站" : "删除"} ${doc.name}`}
            loading={singleDeletingId === doc.id}
            disabled={
              usingMock ||
              inFlight ||
              Boolean(singleDeletingId) ||
              batchDeleting ||
              contentRecoveryCapabilityReady === false ||
              contentRecoveryReadOnly
            }
          >
            {contentRecoveryCapabilityReady === true ? "移入回收站" : "删除"}
          </Button>
        </Popconfirm>
      </>
    );
  };

  if (parseRoute) {
    const target = knownDocuments.get(parseRoute.docId);
    const declaredTenant = target?.tenant_id?.trim() ?? "";
    const targetTenant = declaredTenant || tenantId;
    const targetDataset = target?.dataset_id?.trim() || datasetId;
    const tenantMatches = !declaredTenant || tenantId === "default" || declaredTenant === tenantId;
    const scoped =
      target && !usingMock && targetTenant && tenantMatches && targetDataset === datasetId
        ? { tenantId: targetTenant, datasetId, actorToken, docId: target.id }
        : null;
    return (
      <ParseInterventionWorkspace
        scope={scoped}
        document={target ?? null}
        initialChunkId={parseRoute.chunkId || null}
        online={online}
        onReturn={closeParseWorkspace}
        onChanged={refreshVisibleDocuments}
        onDirtyChange={setWorkspaceDirty}
        onNavigateOperator={(targetPage) => {
          if (
            parseDirty &&
            !window.confirm("当前切片修改尚未提交。离开解析干预工作区将丢弃草稿，是否继续？")
          )
            return;
          setWorkspaceDirty(false);
          const intent = navigationIntent(window.location, targetPage);
          if (intent.mode === "history")
            window.history.pushState(window.history.state, "", intent.url);
          else window.location.hash = intent.url;
          window.dispatchEvent(new PopStateEvent("popstate"));
        }}
      />
    );
  }

  return (
    <div className="page-slot">
      {!embedded && (
        <PageTopbar
          icon={<FolderOpenIcon />}
          title="文档管理"
          subtitle="上传资料并跟踪解析、切分、索引的处理进度"
          extra={
            <>
              <Select
                size="small"
                value={datasetId}
                style={{ width: 150 }}
                options={[{ value: "default", label: "默认知识库" }]}
                aria-label="选择知识库"
              />
              <Button
                tag="button"
                size="small"
                icon={<RefreshIcon className={catalogLoading ? "tdesign-icon-spin" : undefined} />}
                onClick={() => {
                  if (modernCatalogEnabled) void loadModernCatalog();
                  else void refresh();
                }}
              >
                刷新
              </Button>
              <Button
                tag="button"
                size="small"
                icon={<FolderAddIcon />}
                disabled={catalogUsingMock}
                onClick={() => {
                  setImportMode("folder");
                  setImportStep(1);
                  setIngestOpen(true);
                }}
              >
                批量导入
              </Button>
              <Button
                tag="button"
                size="small"
                theme="primary"
                icon={<AddIcon />}
                disabled={catalogUsingMock}
                onClick={() => {
                  setImportMode("file");
                  setImportStep(1);
                  setIngestOpen(true);
                }}
              >
                添加文件
              </Button>
            </>
          }
        />
      )}

      <div className="page-shell">
        <div className="page-shell-inner">
          <div className="documents-workbench-layout">
            <aside className="documents-facet-rail" aria-label="文档分类与筛选">
              <div className="facet-rail-title">
                <FolderOpenIcon />
                <div>
                  <strong>文档导航</strong>
                  <span>按状态、类型和解析引擎筛选</span>
                </div>
              </div>
              <div className="facet-group">
                <h3>处理状态</h3>
                {[
                  ["all", "全部文档", facets.statuses.all],
                  ["processing", "处理中", facets.statuses.processing],
                  ["completed", "已完成", facets.statuses.completed],
                  ["error", "失败", facets.statuses.error],
                ].map(([value, label, count]) => (
                  <button
                    type="button"
                    key={String(value)}
                    className={statusFilter === value ? "facet-item is-active" : "facet-item"}
                    onClick={() => applyDocumentFilter("status", String(value))}
                  >
                    <span>{label}</span>
                    <b>{count}</b>
                  </button>
                ))}
              </div>
              <div className="facet-group">
                <h3>文档类型</h3>
                <button
                  type="button"
                  className={typeFilter === "all" ? "facet-item is-active" : "facet-item"}
                  onClick={() => applyDocumentFilter("type", "all")}
                >
                  <span>全部类型</span>
                  <b>{stats.total}</b>
                </button>
                {Object.entries(facets.types).map(([value, count]) => (
                  <button
                    type="button"
                    key={value}
                    className={typeFilter === value ? "facet-item is-active" : "facet-item"}
                    onClick={() => applyDocumentFilter("type", value)}
                  >
                    <span>{value}</span>
                    <b>{count}</b>
                  </button>
                ))}
              </div>
              <div className="facet-group">
                <h3>解析引擎</h3>
                <button
                  type="button"
                  className={engineFilter === "all" ? "facet-item is-active" : "facet-item"}
                  onClick={() => applyDocumentFilter("engine", "all")}
                >
                  <span>全部引擎</span>
                  <b>{stats.total}</b>
                </button>
                {Object.entries(facets.engines).map(([value, count]) => (
                  <button
                    type="button"
                    key={value}
                    className={engineFilter === value ? "facet-item is-active" : "facet-item"}
                    onClick={() => applyDocumentFilter("engine", value)}
                  >
                    <span>{ENGINE_LABELS[value] ?? (value === "unknown" ? "待记录" : value)}</span>
                    <b>{count}</b>
                  </button>
                ))}
              </div>
              <div className="facet-group">
                <h3>文档分类</h3>
                <button
                  type="button"
                  className={categoryFilter === "all" ? "facet-item is-active" : "facet-item"}
                  onClick={() => applyDocumentFilter("category", "all")}
                >
                  <span>全部分类</span>
                  <b>{stats.total}</b>
                </button>
                {Object.entries(facets.categories).map(([value, count]) => (
                  <button
                    type="button"
                    key={value}
                    className={categoryFilter === value ? "facet-item is-active" : "facet-item"}
                    onClick={() => applyDocumentFilter("category", value)}
                  >
                    <span>{value}</span>
                    <b>{count}</b>
                  </button>
                ))}
              </div>
              {Object.keys(facets.tags).length > 0 && (
                <div className="facet-group">
                  <h3>文档标签</h3>
                  <button
                    type="button"
                    className={tagFilter === "all" ? "facet-item is-active" : "facet-item"}
                    onClick={() => applyDocumentFilter("tag", "all")}
                  >
                    <span>全部标签</span>
                    <b>{Object.keys(facets.tags).length}</b>
                  </button>
                  {Object.entries(facets.tags)
                    .slice(0, 10)
                    .map(([value, count]) => (
                      <button
                        type="button"
                        key={value}
                        className={tagFilter === value ? "facet-item is-active" : "facet-item"}
                        onClick={() => applyDocumentFilter("tag", value)}
                      >
                        <span>#{value}</span>
                        <b>{count}</b>
                      </button>
                    ))}
                </div>
              )}
              {(typeFilter !== "all" ||
                engineFilter !== "all" ||
                statusFilter !== "all" ||
                categoryFilter !== "all" ||
                tagFilter !== "all" ||
                keyword.trim()) && (
                <Button
                  tag="button"
                  variant="text"
                  size="small"
                  aria-label="清除全部文档筛选"
                  onClick={clearAllDocumentFilters}
                >
                  清除筛选
                </Button>
              )}
            </aside>
            <div className="documents-workbench-main">
              {/* 概览 */}
              <div className="stat-grid">
                <StatCard labelIcon={<FolderIcon />} label="文档总数" value={stats.total} />
                <StatCard
                  tone="success"
                  labelIcon={<CheckCircleIcon />}
                  label="已完成入库"
                  value={stats.completed}
                  unit={"/ " + stats.total}
                />
                <StatCard
                  tone="warning"
                  labelIcon={stats.processing > 0 ? <LoadingIcon /> : <LoadingIcon />}
                  label="处理中"
                  value={stats.processing}
                />
                <StatCard
                  tone="graph"
                  labelIcon={<DataBaseIcon />}
                  label="检索片段总数"
                  value={stats.chunks.toLocaleString()}
                />
              </div>

              <Card size="small" bodyStyle={{ padding: 16 }} bordered>
                {/* 工具条 */}
                <div className="docs-toolbar">
                  <div className="docs-toolbar-left">
                    <label className="documents-search-label">
                      <span className="sr-only">搜索文件名</span>
                      <Input
                        placeholder="搜索文件名"
                        value={keyword}
                        onChange={(value) => {
                          const nextKeyword = String(value);
                          applyDocumentFilter("keyword", nextKeyword);
                        }}
                        prefixIcon={<SearchIcon />}
                        clearable
                        style={{ width: 240 }}
                      />
                    </label>
                    <div className="docs-status-filter" role="group" aria-label="处理状态筛选">
                      {[
                        ["all", "全部"],
                        ["processing", "处理中"],
                        ["completed", "已完成"],
                        ["error", "失败"],
                      ].map(([value, label]) => (
                        <Button
                          tag="button"
                          key={value}
                          size="small"
                          variant={statusFilter === value ? "base" : "text"}
                          theme={statusFilter === value ? "primary" : "default"}
                          aria-pressed={statusFilter === value}
                          onClick={() => applyDocumentFilter("status", value)}
                        >
                          {label}
                        </Button>
                      ))}
                    </div>
                  </div>
                  <Text theme="secondary" style={{ fontSize: FONT_SIZE.sm }}>
                    {usingMock
                      ? "当前展示演示数据（桥服务未连接）"
                      : stats.failed > 0
                        ? stats.failed + " 个文件处理失败，可重新索引"
                        : "共 " +
                          (modernCatalogEnabled ? (modernPage?.total ?? 0) : filtered.length) +
                          " 个文件"}
                  </Text>
                </div>

                {selectedIds.length > 0 ? (
                  <div className="docs-batch-bar" role="region" aria-label="批量文档操作">
                    <div>
                      <strong>已选择 {selectedIds.length} 篇</strong>
                      <span>预计影响 {selectedChunkCount} 个检索片段；处理中不可选择</span>
                    </div>
                    <div className="docs-batch-actions">
                      <Button
                        tag="button"
                        size="small"
                        variant="outline"
                        icon={<FolderOpenIcon />}
                        disabled={batchDeleting || Boolean(singleDeletingId)}
                        onClick={openBatchSettings}
                      >
                        移动分类 / 添加标签
                      </Button>
                      <Button
                        tag="button"
                        size="small"
                        variant="text"
                        disabled={batchDeleting}
                        onClick={() => {
                          setSelectedIds([]);
                          setBatchDeleteIssues([]);
                        }}
                      >
                        取消选择
                      </Button>
                      <Popconfirm
                        theme="danger"
                        content={
                          <div className="document-delete-confirm">
                            <strong>
                              {contentRecoveryCapabilityReady === true
                                ? `移入回收站 ${selectedIds.length} 篇文档`
                                : `请求删除 ${selectedIds.length} 篇文档`}
                            </strong>
                            <p>
                              {contentRecoveryCapabilityReady === true
                                ? "所选文档将停止检索并进入企业回收站，可在保留期内恢复。"
                                : `请求接受后将异步清理 ${selectedChunkCount} 个检索片段及其向量、图谱关联。此操作不可撤销。`}
                            </p>
                          </div>
                        }
                        confirmBtn={{
                          content:
                            contentRecoveryCapabilityReady === true ? "确认移入" : "确认删除",
                          theme: contentRecoveryCapabilityReady === true ? "warning" : "danger",
                          loading: batchDeleting,
                          disabled: batchDeleting,
                        }}
                        cancelBtn={{ content: "取消" }}
                        onConfirm={() => void handleBatchDelete()}
                      >
                        <Button
                          tag="button"
                          size="small"
                          theme="danger"
                          variant="outline"
                          icon={<DeleteIcon />}
                          loading={batchDeleting}
                          disabled={
                            Boolean(singleDeletingId) ||
                            contentRecoveryCapabilityReady === false ||
                            contentRecoveryReadOnly
                          }
                        >
                          {contentRecoveryCapabilityReady === true ? "批量移入回收站" : "批量删除"}
                        </Button>
                      </Popconfirm>
                    </div>
                  </div>
                ) : null}

                {batchDeleteIssues.length > 0 ? (
                  <section className="batch-delete-result" role="region" aria-label="批量删除结果">
                    <div>
                      <strong>有 {batchDeleteIssues.length} 篇未完成删除</strong>
                      <span>失败项已保留选择，请联系管理员处理；不存在项已从选择中移除。</span>
                    </div>
                    <ul>
                      {batchDeleteIssues.map((issue) => (
                        <li key={`${issue.code}:${issue.documentId}`}>
                          <Tag
                            theme={issue.code === "not_found" ? "warning" : "danger"}
                            variant="light"
                          >
                            {issue.code === "not_found" ? "已不存在" : "删除失败"}
                          </Tag>
                          <span>
                            {issue.name}：{issue.message}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </section>
                ) : null}

                {showWorkspaceState ? (
                  <KnowledgeWorkspacePageState
                    status={catalogStatus}
                    error={catalogError}
                    onRetry={modernCatalogEnabled ? loadModernCatalog : refresh}
                  />
                ) : (modernCatalogEnabled ? (modernPage?.total ?? 0) : docs.length) === 0 &&
                  !catalogLoading ? (
                  <PageState
                    status="empty"
                    icon={<FolderIcon />}
                    title="这个知识库还没有文档"
                    description="添加本地文件后，系统会依次完成解析、切分和建立索引；完成后就可以在问答页向这些资料提问。"
                    extra={
                      <Button
                        tag="button"
                        theme="primary"
                        icon={<AddIcon />}
                        disabled={catalogUsingMock}
                        onClick={() => {
                          setImportMode("file");
                          setImportStep(1);
                          setIngestOpen(true);
                        }}
                      >
                        添加第一个文件
                      </Button>
                    }
                  />
                ) : (
                  <KnowledgeDocumentTable
                    documents={tableDocuments}
                    loading={catalogLoading}
                    selectionDisabled={catalogUsingMock}
                    selectedIds={selectedIds}
                    onSelectionChange={(ids) => {
                      for (const document of catalogDocuments) {
                        if (ids.includes(document.id))
                          selectedDocumentsByIdRef.current.set(document.id, document);
                      }
                      setSelectedIds(ids);
                      setBatchDeleteIssues([]);
                    }}
                    renderFile={renderFileCell}
                    renderParserProfile={renderParserProfile}
                    renderStatus={renderStatus}
                    renderActions={renderActions}
                    onOpenParse={openParseWorkspace}
                    canOpenParse={(document) =>
                      !catalogUsingMock &&
                      (document.lifecycle_state ?? "active") === "active" &&
                      (document.status === "completed" || document.status === "error")
                    }
                    emptyContent={
                      <PageState
                        compact
                        status="empty"
                        title="没有符合筛选条件的文件"
                        description="调整关键词或状态筛选后再试，或清除筛选查看全部文档。"
                        extra={
                          <Button
                            tag="button"
                            type="button"
                            aria-label="清除文档筛选"
                            onClick={clearAllDocumentFilters}
                          >
                            清除筛选
                          </Button>
                        }
                      />
                    }
                    serverPagination={
                      modernCatalogEnabled && modernPage
                        ? {
                            current: modernPageNumber,
                            total: modernPage.total,
                            pageSize: DOCUMENT_PAGE_SIZE,
                            hasPrevious: modernPageNumber > 1,
                            hasNext:
                              Boolean(modernPage.next_cursor) ||
                              modernPageNumber * DOCUMENT_PAGE_SIZE < modernPage.total,
                            loading: modernLoading,
                            onPrevious: goToPreviousModernPage,
                            onNext: goToNextModernPage,
                          }
                        : undefined
                    }
                  />
                )}
              </Card>
            </div>
          </div>
        </div>
      </div>

      {ingestOpen ? (
        <Dialog
          header="导入知识"
          {...({ role: "dialog", "aria-modal": "true", "aria-label": "导入知识" } as Record<
            string,
            unknown
          >)}
          visible
          width={680}
          placement="center"
          destroyOnClose
          closeOnEscKeydown={!ingesting && !folderImporting}
          closeOnOverlayClick={!ingesting && !folderImporting}
          onClose={() => {
            if (!ingesting && !folderImporting) setIngestOpen(false);
          }}
          footer={
            <div className="document-dialog-footer">
              <Button
                tag="button"
                variant="text"
                disabled={ingesting || folderImporting}
                onClick={() => setIngestOpen(false)}
              >
                取消
              </Button>
              <div>
                {importStep === 2 ? (
                  <Button
                    tag="button"
                    variant="outline"
                    disabled={ingesting || folderImporting}
                    onClick={() => setImportStep(1)}
                  >
                    上一步
                  </Button>
                ) : null}
                {importStep === 1 ? (
                  <Button
                    tag="button"
                    theme="primary"
                    disabled={importMode === "file" ? !ingestPath.trim() : !folderPath.trim()}
                    onClick={() => setImportStep(2)}
                  >
                    下一步：解析设置
                  </Button>
                ) : (
                  <Button
                    tag="button"
                    theme="primary"
                    loading={ingesting || folderImporting}
                    onClick={() =>
                      void (importMode === "file" ? handleIngest() : handleFolderIngest())
                    }
                  >
                    {importMode === "file" ? "开始导入" : "开始批量导入"}
                  </Button>
                )}
              </div>
            </div>
          }
        >
          <div className="document-import-wizard">
            <ol className="document-import-steps" aria-label="导入步骤">
              <li className={importStep === 1 ? "is-active" : "is-complete"}>
                <span>1</span>
                <div>
                  <strong>1. 选择来源</strong>
                  <small>指定文件或服务器目录</small>
                </div>
              </li>
              <li className={importStep === 2 ? "is-active" : ""}>
                <span>2</span>
                <div>
                  <strong>2. 设置解析与切分</strong>
                  <small>核对系统处理计划</small>
                </div>
              </li>
            </ol>

            {importStep === 1 ? (
              <section className="import-stage-panel" aria-labelledby="import-source-heading">
                <div className="import-stage-heading">
                  <div>
                    <span>来源</span>
                    <h3 id="import-source-heading">选择知识来源</h3>
                  </div>
                  <Tag theme="primary" variant="light">
                    仅启用已接通能力
                  </Tag>
                </div>
                <div className="import-source-tabs" role="tablist" aria-label="导入来源">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={importMode === "file"}
                    className={importMode === "file" ? "is-active" : ""}
                    onClick={() => setImportMode("file")}
                  >
                    <FileIcon />
                    <span>
                      <strong>本地文件</strong>
                      <small>PDF、Office、Markdown、文本</small>
                    </span>
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={importMode === "folder"}
                    className={importMode === "folder" ? "is-active" : ""}
                    onClick={() => setImportMode("folder")}
                  >
                    <FolderOpenIcon />
                    <span>
                      <strong>服务器文件夹</strong>
                      <small>递归扫描并跳过重复文件</small>
                    </span>
                  </button>
                </div>
                <div className="import-guide-note">
                  <InfoCircleIcon />
                  <span>
                    {importMode === "file"
                      ? "输入运行 RAG4C 服务的本地文件绝对路径。登记后会进入知识生命线：解析、切分、索引。"
                      : "输入服务器文件夹绝对路径。隐藏项、不支持格式和已完成的同路径文件会自动跳过。"}
                  </span>
                </div>
                <label className="import-path-field">
                  <span>{importMode === "file" ? "文件路径" : "文件夹路径"}</span>
                  <Input
                    placeholder={
                      importMode === "file"
                        ? "例如：D:/data/员工手册.pdf"
                        : "例如：D:/knowledge/公司制度"
                    }
                    value={importMode === "file" ? ingestPath : folderPath}
                    onChange={(value) =>
                      importMode === "file"
                        ? setIngestPath(String(value))
                        : setFolderPath(String(value))
                    }
                    onEnter={() => {
                      const hasPath = importMode === "file" ? ingestPath.trim() : folderPath.trim();
                      if (hasPath) setImportStep(2);
                    }}
                    prefixIcon={importMode === "folder" ? <FolderOpenIcon /> : <FileIcon />}
                    clearable
                  />
                </label>
              </section>
            ) : (
              <section className="import-stage-panel" aria-labelledby="import-plan-heading">
                <div className="import-stage-heading">
                  <div>
                    <span>处理计划</span>
                    <h3 id="import-plan-heading">解析与切分计划</h3>
                  </div>
                  <Tag theme="success" variant="light">
                    准备就绪
                  </Tag>
                </div>
                <div className="import-selection-summary">
                  {importMode === "folder" ? <FolderOpenIcon /> : <FileIcon />}
                  <div>
                    <strong>{importMode === "folder" ? "服务器文件夹" : "本地文件"}</strong>
                    <span>{importMode === "folder" ? folderPath : ingestPath}</span>
                  </div>
                  <Button tag="button" variant="text" size="small" onClick={() => setImportStep(1)}>
                    修改
                  </Button>
                </div>
                <section className="import-capability-preview" aria-label="解析能力预检">
                  <div>
                    <strong>Knowledge Lifeline 入库计划</strong>
                    <Tag theme="primary" variant="light-outline">
                      {importMode === "folder"
                        ? "逐文件自动识别"
                        : ingestPath.split(".").pop()?.toUpperCase() || "自动识别"}
                    </Tag>
                  </div>
                  <div className="import-capability-grid">
                    <span>
                      <b>解析引擎</b>
                      <small>自动识别文本层与 OCR 需求</small>
                    </span>
                    <span>
                      <b>切分策略</b>
                      <small>按系统配置选择固定长度、章节结构或表格逐行</small>
                    </span>
                    <span>
                      <b>上下文增强</b>
                      <small>沿用当前 Contextual Retrieval 设置</small>
                    </span>
                    <span>
                      <b>索引与图谱</b>
                      <small>写入向量投影，并按当前开关构建图谱</small>
                    </span>
                  </div>
                </section>
                <div className="import-guide-note">
                  <InfoCircleIcon />
                  <span>提交后即可关闭窗口。文档状态、阶段耗时和失败原因会在工作台持续更新。</span>
                </div>
              </section>
            )}
          </div>
        </Dialog>
      ) : null}

      {settingsOpen ? (
        <Dialog
          header={
            settingsIds.length > 1 ? `批量整理 ${settingsIds.length} 篇文档` : "文档分类与标签"
          }
          {...({
            role: "dialog",
            "aria-modal": "true",
            "aria-label": settingsIds.length > 1 ? "批量整理文档" : "文档分类与标签",
          } as Record<string, unknown>)}
          visible
          destroyOnClose
          closeOnEscKeydown={!settingsSaving}
          closeOnOverlayClick={!settingsSaving}
          confirmBtn={{ content: "保存设置", theme: "primary" }}
          cancelBtn={{ content: "取消" }}
          confirmLoading={settingsSaving}
          onClose={() => {
            if (!settingsSaving) setSettingsOpen(false);
          }}
          onConfirm={() => void saveDocumentSettings()}
        >
          <div className="document-settings-form">
            <div className="settings-form-intro">
              <FolderOpenIcon />
              <span>
                {settingsIds.length > 1
                  ? "批量移动会把所选文档放入同一逻辑分类；输入的标签会追加到每篇文档。"
                  : "分类用于左侧目录导航；标签用于跨分类组合筛选。分类路径可使用“制度/人力”这样的层级格式。"}
              </span>
            </div>
            <label>
              <span>逻辑分类</span>
              <Input
                value={settingsCategory}
                placeholder="例如：制度/人力；留空表示未分类"
                onChange={(value) => setSettingsCategory(String(value))}
              />
            </label>
            <label>
              <span>{settingsIds.length > 1 ? "追加标签" : "文档标签"}</span>
              <Input
                value={settingsTags}
                placeholder="多个标签使用逗号分隔，例如：员工, 制度, 2026"
                onChange={(value) => setSettingsTags(String(value))}
              />
            </label>
            <div className="settings-tag-preview">
              <span>预览</span>
              {settingsTags
                .split(/[，,]/)
                .map((tag) => tag.trim())
                .filter(Boolean)
                .map((tag) => (
                  <Tag key={tag} variant="light">
                    #{tag}
                  </Tag>
                ))}
            </div>
          </div>
        </Dialog>
      ) : null}
    </div>
  );
}
