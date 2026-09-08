import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { fetchDocumentSummary, fetchDocuments } from "../api/client";
import { DEMO_DOCUMENTS } from "../api/mock";
import { useConnection } from "../context/ConnectionContext";
import type {
  DocumentCatalogItem,
  DocumentCatalogSummaryResponse,
  DocumentItem,
} from "../types/rag";
import { fetchWorkspaceDatasets } from "../enterprise-workspace/api/enterpriseWorkspaceApi";
import { fetchEnterpriseKnowledgeBaseDetail } from "../enterprise-knowledge-base/api/enterpriseKnowledgeBaseApi";
import {
  KNOWLEDGE_DATASET_STORAGE_KEY,
  readKnowledgeActorToken,
  readKnowledgeDatasetIdFromLocation,
  resolveKnowledgeWorkspaceScope,
  type KnowledgeWorkspaceScope,
} from "./workspaceScope";

export type KnowledgeWorkspaceStatus = "loading" | "ready" | "empty" | "error" | "demo";
export type KnowledgeWorkspaceDataMode = "summary-api" | "legacy-local" | "demo";
export type KnowledgeTagAuthority = "governed" | "legacy_projection" | "unknown";
export type KnowledgeWorkspaceScopeStatus =
  "unverified" | "loading" | "verified" | "no_primary_knowledge_base" | "unavailable";
export type KnowledgeWorkspaceSelectionStatus =
  "verified" | "no_primary_knowledge_base" | "unavailable";

export interface KnowledgeWorkspaceSelectionResult {
  workspaceId: string;
  datasetId: string | null;
  status: KnowledgeWorkspaceSelectionStatus;
}

type KnowledgeSummary = DocumentCatalogSummaryResponse["summary"];
type KnowledgeFacets = DocumentCatalogSummaryResponse["facets"];

export interface KnowledgeWorkspaceValue {
  tenantId: string;
  datasetId: string;
  workspaceId: string | null;
  workspaceScopeStatus: KnowledgeWorkspaceScopeStatus;
  scope: KnowledgeWorkspaceScope;
  status: KnowledgeWorkspaceStatus;
  dataMode: KnowledgeWorkspaceDataMode;
  documents: DocumentItem[];
  summary: KnowledgeSummary | null;
  facets: KnowledgeFacets | null;
  recent: DocumentCatalogItem[];
  tagAuthority: KnowledgeTagAuthority;
  tagFacetsComplete: boolean | null;
  tagFacetsScanLimit: number | null;
  tagFacetsScanned: number | null;
  tagFacetsTruncated: boolean;
  summaryGeneratedAt: string | null;
  error: Error | null;
  loading: boolean;
  usingMock: boolean;
  refresh: () => Promise<void>;
  invalidate: () => void;
  selectWorkspace: (workspaceId: string) => Promise<KnowledgeWorkspaceSelectionResult>;
}

interface KnowledgeWorkspaceProviderProps {
  children: ReactNode;
  tenantId?: string;
  datasetId?: string;
  actorToken?: string;
  preferSummaryApi?: boolean;
  demoEnabled?: boolean;
}

interface WorkspaceSnapshot {
  status: KnowledgeWorkspaceStatus;
  dataMode: KnowledgeWorkspaceDataMode;
  documents: DocumentItem[];
  summary: KnowledgeSummary | null;
  facets: KnowledgeFacets | null;
  recent: DocumentCatalogItem[];
  tagAuthority: KnowledgeTagAuthority;
  tagFacetsComplete: boolean | null;
  tagFacetsScanLimit: number | null;
  tagFacetsScanned: number | null;
  tagFacetsTruncated: boolean;
  summaryGeneratedAt: string | null;
  error: Error | null;
}

interface RequestScope {
  tenantId: string;
  datasetId: string;
  actorToken: string;
  dataMode: KnowledgeWorkspaceDataMode;
  online: boolean | null;
  verified: boolean;
  generation: number;
  key: string;
}

interface ActiveRequest {
  key: string;
  promise: Promise<void>;
  controller: AbortController;
}

type SummaryResponseWithAuthority = DocumentCatalogSummaryResponse & {
  tag_authority?: unknown;
};

const MAX_WORKSPACE_DATASET_PAGES = 100;

function emptySnapshot(dataMode: KnowledgeWorkspaceDataMode): WorkspaceSnapshot {
  return {
    status: "loading",
    dataMode,
    documents: [],
    summary: null,
    facets: null,
    recent: [],
    tagAuthority: "unknown",
    tagFacetsComplete: null,
    tagFacetsScanLimit: null,
    tagFacetsScanned: null,
    tagFacetsTruncated: false,
    summaryGeneratedAt: null,
    error: null,
  };
}

const KnowledgeWorkspaceContext = createContext<KnowledgeWorkspaceValue | null>(null);

function toError(value: unknown): Error {
  return value instanceof Error ? value : new Error(String(value));
}

function normalizeTagAuthority(value: unknown): KnowledgeTagAuthority {
  if (value === "governed" || value === "legacy_projection") return value;
  return "unknown";
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function clearStoredDatasetId(): void {
  try {
    localStorage.removeItem(KNOWLEDGE_DATASET_STORAGE_KEY);
  } catch {
    // The authoritative selection remains in memory when browser storage is unavailable.
  }
}

function storeDatasetId(datasetId: string): void {
  try {
    localStorage.setItem(KNOWLEDGE_DATASET_STORAGE_KEY, datasetId);
  } catch {
    // The authoritative selection remains in memory when browser storage is unavailable.
  }
}

export function KnowledgeWorkspaceProvider({
  children,
  tenantId,
  datasetId,
  actorToken: explicitActorToken,
  preferSummaryApi = true,
  demoEnabled = false,
}: KnowledgeWorkspaceProviderProps) {
  const { online } = useConnection();
  const actorToken = readKnowledgeActorToken(explicitActorToken);
  // The missing-function branch only protects older test doubles and partially
  // upgraded bundles. In the application bundle the modern client is present,
  // so any actor token deterministically selects the summary API.
  const summaryApiAvailable = typeof fetchDocumentSummary === "function";
  const dataMode: KnowledgeWorkspaceDataMode =
    actorToken && preferSummaryApi && summaryApiAvailable ? "summary-api" : "legacy-local";
  const initialScope = useMemo(
    () => resolveKnowledgeWorkspaceScope({ tenantId, datasetId, actorToken }),
    [actorToken, datasetId, tenantId],
  );
  const initialRouteDatasetId =
    typeof window === "undefined" ? null : readKnowledgeDatasetIdFromLocation(window.location);
  const initialDataMode =
    actorToken && preferSummaryApi && summaryApiAvailable ? "summary-api" : "legacy-local";
  const initialScopeStatus: KnowledgeWorkspaceScopeStatus = actorToken ? "unverified" : "verified";
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string | null>(null);
  const [selectedDatasetId, setSelectedDatasetId] = useState<string | null | undefined>(
    initialRouteDatasetId ?? undefined,
  );
  const [workspaceScopeStatus, setWorkspaceScopeStatus] =
    useState<KnowledgeWorkspaceScopeStatus>(initialScopeStatus);
  const resolvedDatasetId =
    selectedDatasetId === undefined ? initialScope.datasetId : (selectedDatasetId ?? "");
  const scope = useMemo<KnowledgeWorkspaceScope>(
    () => ({ tenantId: initialScope.tenantId, datasetId: resolvedDatasetId }),
    [initialScope.tenantId, resolvedDatasetId],
  );
  const [snapshot, setSnapshot] = useState<WorkspaceSnapshot>(() => emptySnapshot(initialDataMode));
  const scopeRef = useRef<RequestScope>({
    tenantId: scope.tenantId,
    datasetId: resolvedDatasetId,
    actorToken,
    dataMode,
    online,
    verified: initialScopeStatus === "verified",
    generation: 0,
    key: `${scope.tenantId}:${resolvedDatasetId}:${dataMode}:${initialScopeStatus}:0`,
  });
  const requestRef = useRef<ActiveRequest | null>(null);
  const workspaceSelectionControllerRef = useRef<AbortController | null>(null);
  const datasetDeepLinkControllerRef = useRef<AbortController | null>(null);
  const verifiedDatasetAuthorityRef = useRef<{
    tenantId: string;
    datasetId: string;
    actorToken: string;
  } | null>(null);
  const mountedRef = useRef(false);
  const pendingInvalidateRef = useRef<string | null>(null);
  const startRequestRef = useRef<(scope: RequestScope) => Promise<void>>(async () => undefined);

  const startRequest = useCallback((requestScope: RequestScope): Promise<void> => {
    if (!mountedRef.current) return Promise.resolve();

    const activeRequest = requestRef.current;
    if (activeRequest?.key === requestScope.key) return activeRequest.promise;
    if (requestScope.online !== true || !requestScope.verified || !requestScope.datasetId.trim()) {
      return Promise.resolve();
    }

    if (activeRequest) {
      activeRequest.controller.abort();
      requestRef.current = null;
    }

    const controller = new AbortController();
    setSnapshot((current) => ({
      ...current,
      status: "loading",
      dataMode: requestScope.dataMode,
      error: null,
    }));

    const request = (
      requestScope.dataMode === "summary-api"
        ? fetchDocumentSummary(requestScope.datasetId, {
            tenantId: requestScope.tenantId,
            actorToken: requestScope.actorToken,
            signal: controller.signal,
          }).then((result) => {
            if (
              !mountedRef.current ||
              controller.signal.aborted ||
              scopeRef.current.key !== requestScope.key
            ) {
              return;
            }
            const summary = result.summary;
            const total = Number(summary?.total ?? 0);
            const response = result as SummaryResponseWithAuthority;
            setSnapshot({
              status: total > 0 ? "ready" : "empty",
              dataMode: "summary-api",
              documents: [],
              summary: result.summary ?? null,
              facets: result.facets ?? null,
              recent: Array.isArray(result.recent) ? result.recent : [],
              tagAuthority: normalizeTagAuthority(response.tag_authority),
              tagFacetsComplete:
                typeof result.tag_facets_complete === "boolean" ? result.tag_facets_complete : null,
              tagFacetsScanLimit: numberOrNull(result.tag_facets_scan_limit),
              tagFacetsScanned: numberOrNull(result.tag_facets_scanned),
              tagFacetsTruncated: result.tag_facets_truncated === true,
              summaryGeneratedAt:
                typeof result.generated_at === "string" ? result.generated_at : null,
              error: null,
            });
          })
        : fetchDocuments(requestScope.datasetId, controller.signal).then((result) => {
            if (
              !mountedRef.current ||
              controller.signal.aborted ||
              scopeRef.current.key !== requestScope.key
            ) {
              return;
            }
            const documents = result.documents ?? [];
            setSnapshot({
              status: documents.length > 0 ? "ready" : "empty",
              dataMode: "legacy-local",
              documents,
              summary: null,
              facets: null,
              recent: [],
              tagAuthority: "unknown",
              tagFacetsComplete: null,
              tagFacetsScanLimit: null,
              tagFacetsScanned: null,
              tagFacetsTruncated: false,
              summaryGeneratedAt: null,
              error: null,
            });
          })
    )
      .catch((error: unknown) => {
        if (
          !mountedRef.current ||
          controller.signal.aborted ||
          scopeRef.current.key !== requestScope.key
        ) {
          return;
        }
        setSnapshot({
          ...emptySnapshot(requestScope.dataMode),
          status: "error",
          error: toError(error),
        });
      })
      .finally(() => {
        if (requestRef.current?.promise === request) requestRef.current = null;
        if (!mountedRef.current) {
          pendingInvalidateRef.current = null;
          return;
        }
        if (pendingInvalidateRef.current === requestScope.key) {
          pendingInvalidateRef.current = null;
          void startRequestRef.current(scopeRef.current);
        }
      });

    requestRef.current = { key: requestScope.key, promise: request, controller };
    return request;
  }, []);

  startRequestRef.current = startRequest;

  const refresh = useCallback((): Promise<void> => {
    if (workspaceScopeStatus !== "verified") return Promise.resolve();
    return startRequestRef.current(scopeRef.current);
  }, [workspaceScopeStatus]);

  const selectWorkspace = useCallback(
    async (workspaceId: string): Promise<KnowledgeWorkspaceSelectionResult> => {
      const normalizedWorkspaceId = workspaceId.trim();
      if (!normalizedWorkspaceId || !actorToken.trim()) {
        setWorkspaceScopeStatus("unavailable");
        return { workspaceId: normalizedWorkspaceId, datasetId: null, status: "unavailable" };
      }

      workspaceSelectionControllerRef.current?.abort();
      datasetDeepLinkControllerRef.current?.abort();
      const controller = new AbortController();
      workspaceSelectionControllerRef.current = controller;
      setWorkspaceScopeStatus("loading");
      setSelectedWorkspaceId(null);
      setSelectedDatasetId(null);
      try {
        const bindings = [];
        let cursor: string | undefined;
        let paginationComplete = false;
        const seenCursors = new Set<string>();
        for (let pageNumber = 0; pageNumber < MAX_WORKSPACE_DATASET_PAGES; pageNumber += 1) {
          const page = await fetchWorkspaceDatasets(
            {
              tenantId: initialScope.tenantId,
              actorToken,
            },
            normalizedWorkspaceId,
            { status: "active", limit: 100, ...(cursor ? { cursor } : {}) },
            { signal: controller.signal },
          );
          if (controller.signal.aborted) {
            return { workspaceId: normalizedWorkspaceId, datasetId: null, status: "unavailable" };
          }
          bindings.push(...page.items);
          const nextCursor = page.next_cursor?.trim() || null;
          if (!nextCursor) {
            paginationComplete = true;
            break;
          }
          if (seenCursors.has(nextCursor))
            throw new Error("Workspace bindings returned a repeated cursor");
          seenCursors.add(nextCursor);
          cursor = nextCursor;
        }
        if (!paginationComplete) {
          throw new Error(
            `Workspace bindings exceeded the ${MAX_WORKSPACE_DATASET_PAGES} page safety limit`,
          );
        }
        const primaryBindings = bindings.filter(
          (binding) => binding.status === "active" && binding.binding_kind === "primary",
        );
        if (primaryBindings.length > 1) {
          throw new Error("Workspace has multiple active primary knowledge bases");
        }
        const primary = primaryBindings[0] ?? null;
        setSelectedWorkspaceId(normalizedWorkspaceId);
        if (!primary) {
          setSelectedDatasetId(null);
          verifiedDatasetAuthorityRef.current = null;
          clearStoredDatasetId();
          setWorkspaceScopeStatus("no_primary_knowledge_base");
          return {
            workspaceId: normalizedWorkspaceId,
            datasetId: null,
            status: "no_primary_knowledge_base",
          };
        }
        setSelectedDatasetId(primary.dataset_id);
        verifiedDatasetAuthorityRef.current = {
          tenantId: initialScope.tenantId,
          datasetId: primary.dataset_id,
          actorToken,
        };
        storeDatasetId(primary.dataset_id);
        setWorkspaceScopeStatus("verified");
        return {
          workspaceId: normalizedWorkspaceId,
          datasetId: primary.dataset_id,
          status: "verified",
        };
      } catch (error) {
        if (!controller.signal.aborted) {
          setSelectedWorkspaceId(null);
          setSelectedDatasetId(null);
          verifiedDatasetAuthorityRef.current = null;
          clearStoredDatasetId();
          setWorkspaceScopeStatus("unavailable");
          setSnapshot({
            ...emptySnapshot(dataMode),
            status: "error",
            error: toError(error),
          });
        }
        return { workspaceId: normalizedWorkspaceId, datasetId: null, status: "unavailable" };
      }
    },
    [actorToken, dataMode, initialScope.tenantId],
  );

  useEffect(() => {
    const syncDatasetAuthority = () => {
      const routeDatasetId = readKnowledgeDatasetIdFromLocation(window.location);
      const requestedDatasetId = routeDatasetId ?? datasetId?.trim() ?? null;
      if (!requestedDatasetId || !actorToken.trim()) return;
      const verifiedAuthority = verifiedDatasetAuthorityRef.current;
      if (
        verifiedAuthority?.tenantId === initialScope.tenantId &&
        verifiedAuthority.datasetId === requestedDatasetId &&
        verifiedAuthority.actorToken === actorToken
      )
        return;
      datasetDeepLinkControllerRef.current?.abort();
      const controller = new AbortController();
      datasetDeepLinkControllerRef.current = controller;
      setWorkspaceScopeStatus("loading");
      setSelectedWorkspaceId(null);
      setSelectedDatasetId(null);
      void fetchEnterpriseKnowledgeBaseDetail(
        {
          tenantId: initialScope.tenantId,
          datasetId: requestedDatasetId,
          actorToken,
        },
        requestedDatasetId,
        { signal: controller.signal },
      )
        .then((detail) => {
          if (controller.signal.aborted) return;
          if (detail.knowledge_base.id !== requestedDatasetId) {
            throw new Error("Registry Dataset authority does not match the requested Dataset");
          }
          const owner = detail.knowledge_base.owning_workspace;
          if (!owner || owner.status !== "active") {
            verifiedDatasetAuthorityRef.current = null;
            clearStoredDatasetId();
            setWorkspaceScopeStatus("unavailable");
            return;
          }
          setSelectedWorkspaceId(owner.id);
          setSelectedDatasetId(detail.knowledge_base.id);
          verifiedDatasetAuthorityRef.current = {
            tenantId: initialScope.tenantId,
            datasetId: detail.knowledge_base.id,
            actorToken,
          };
          setWorkspaceScopeStatus("verified");
          storeDatasetId(detail.knowledge_base.id);
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted) return;
          verifiedDatasetAuthorityRef.current = null;
          clearStoredDatasetId();
          setSelectedWorkspaceId(null);
          setSelectedDatasetId(null);
          setWorkspaceScopeStatus("unavailable");
          setSnapshot({
            ...emptySnapshot(dataMode),
            status: "error",
            error: toError(error),
          });
        });
    };
    syncDatasetAuthority();
    window.addEventListener("hashchange", syncDatasetAuthority);
    window.addEventListener("popstate", syncDatasetAuthority);
    return () => {
      datasetDeepLinkControllerRef.current?.abort();
      window.removeEventListener("hashchange", syncDatasetAuthority);
      window.removeEventListener("popstate", syncDatasetAuthority);
    };
  }, [actorToken, dataMode, datasetId, initialScope.tenantId]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      pendingInvalidateRef.current = null;
      requestRef.current?.controller.abort();
      requestRef.current = null;
      workspaceSelectionControllerRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    const generation = scopeRef.current.generation + 1;
    const requestScope: RequestScope = {
      tenantId: scope.tenantId,
      datasetId: resolvedDatasetId,
      actorToken,
      dataMode,
      online,
      verified: workspaceScopeStatus === "verified",
      generation,
      key: `${scope.tenantId}:${resolvedDatasetId}:${dataMode}:${workspaceScopeStatus}:${generation}`,
    };
    scopeRef.current = requestScope;
    pendingInvalidateRef.current = null;
    requestRef.current?.controller.abort();
    requestRef.current = null;

    if (workspaceScopeStatus === "no_primary_knowledge_base") {
      setSnapshot({
        ...emptySnapshot(dataMode),
        status: "error",
        error: new Error("no_primary_knowledge_base"),
      });
      return;
    }
    if (workspaceScopeStatus !== "verified") {
      setSnapshot(emptySnapshot(dataMode));
      return;
    }
    if (online === true) {
      void startRequestRef.current(requestScope);
      return;
    }
    if (online === false) {
      if (demoEnabled && !actorToken) {
        setSnapshot({
          ...emptySnapshot("demo"),
          status: "demo",
          documents: DEMO_DOCUMENTS,
        });
      } else {
        setSnapshot({
          ...emptySnapshot(dataMode),
          status: "error",
          error: new Error("后端服务未连接，无法读取知识库文档"),
        });
      }
      return;
    }
    setSnapshot(emptySnapshot(dataMode));
  }, [
    actorToken,
    dataMode,
    demoEnabled,
    online,
    resolvedDatasetId,
    scope.tenantId,
    workspaceScopeStatus,
  ]);

  const invalidate = useCallback(() => {
    if (!mountedRef.current) return;
    const currentScope = scopeRef.current;
    if (requestRef.current?.key === currentScope.key) {
      pendingInvalidateRef.current = currentScope.key;
      return;
    }
    void startRequestRef.current(currentScope);
  }, []);

  const value = useMemo<KnowledgeWorkspaceValue>(
    () => ({
      tenantId: scope.tenantId,
      datasetId: resolvedDatasetId,
      workspaceId: selectedWorkspaceId,
      workspaceScopeStatus,
      scope,
      ...snapshot,
      loading: snapshot.status === "loading",
      usingMock: snapshot.status === "demo",
      refresh,
      invalidate,
      selectWorkspace,
    }),
    [
      invalidate,
      refresh,
      resolvedDatasetId,
      scope,
      selectWorkspace,
      selectedWorkspaceId,
      snapshot,
      workspaceScopeStatus,
    ],
  );

  return (
    <KnowledgeWorkspaceContext.Provider value={value}>
      {children}
    </KnowledgeWorkspaceContext.Provider>
  );
}

// Provider and its public hook intentionally share this context module; keep the
// exception local instead of weakening Fast Refresh checks for the application.
// eslint-disable-next-line react-refresh/only-export-components
export function useKnowledgeWorkspace(): KnowledgeWorkspaceValue {
  const value = useContext(KnowledgeWorkspaceContext);
  if (!value) {
    throw new Error("useKnowledgeWorkspace 必须在 KnowledgeWorkspaceProvider 内使用");
  }
  return value;
}

// App is also used as a focused shell component in tests without AppProviders.
// Optional access keeps that composition valid while the real application still
// receives the verified Workspace -> Dataset scope from the provider boundary.
// eslint-disable-next-line react-refresh/only-export-components
export function useOptionalKnowledgeWorkspace(): KnowledgeWorkspaceValue | null {
  return useContext(KnowledgeWorkspaceContext);
}
