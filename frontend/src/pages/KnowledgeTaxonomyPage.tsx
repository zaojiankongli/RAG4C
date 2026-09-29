import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button, Card, Empty, Input, Tag } from "tdesign-react";
import {
  DataBaseIcon,
  FileIcon,
  FolderOpenIcon,
  RefreshIcon,
  SearchIcon,
  TagIcon,
  ViewListIcon,
} from "tdesign-icons-react";
import PageState from "../components/PageState";
import PageTopbar from "../components/PageTopbar";
import { ApiError, fetchDocumentPage, fetchDocumentSummary } from "../api/client";
import KnowledgeWorkspacePageState from "../knowledge/KnowledgeWorkspacePageState";
import { projectTaxonomy } from "../knowledge/knowledgeModel";
import { useKnowledgeDocuments } from "../knowledge/useKnowledgeDocuments";
import {
  readKnowledgeActorToken,
  resolveKnowledgeWorkspaceScope,
} from "../knowledge/workspaceScope";
import { navigationIntent } from "../run/appRoute";
import { commitNavigationIntent } from "../run/navigationAdapter";
import type {
  DocumentCatalogSummaryResponse,
  DocumentItem,
  DocumentPageQuery,
  DocumentPageResponse,
} from "../types/rag";
import "./knowledge-taxonomy.css";

type TagTheme = "success" | "danger" | "primary" | "warning" | "default";
type ModernCatalogStatus = "idle" | "loading" | "ready" | "error";

interface FolderNode {
  name: string;
  path: string;
  documents: number;
  children: FolderNode[];
}

type PaginationItem = number | "ellipsis-start" | "ellipsis-end";

const PAGE_SIZE = 20;

const STATUS_PRESENTATION: Record<string, { label: string; theme: TagTheme }> = {
  completed: { label: "已完成", theme: "success" },
  error: { label: "解析失败", theme: "danger" },
  indexing: { label: "索引中", theme: "primary" },
  parsing: { label: "解析中", theme: "primary" },
  splitting: { label: "切片中", theme: "primary" },
  waiting: { label: "等待入库", theme: "warning" },
  queued: { label: "等待处理", theme: "warning" },
};

function buildFolderTree(categories: Array<{ name: string; documents: number }>): FolderNode[] {
  const roots: FolderNode[] = [];

  for (const category of categories) {
    const segments = category.name
      .split("/")
      .map((segment) => segment.trim())
      .filter(Boolean);
    const normalizedSegments = segments.length ? segments : ["未分类"];
    let siblings = roots;
    let path = "";

    for (const segment of normalizedSegments) {
      path = path ? `${path}/${segment}` : segment;
      let node = siblings.find((candidate) => candidate.name === segment);
      if (!node) {
        node = { name: segment, path, documents: 0, children: [] };
        siblings.push(node);
      }
      node.documents += category.documents;
      siblings = node.children;
    }
  }

  const sortNodes = (nodes: FolderNode[]) => {
    nodes.sort(
      (left, right) =>
        right.documents - left.documents || left.name.localeCompare(right.name, "zh-CN"),
    );
    nodes.forEach((node) => sortNodes(node.children));
  };
  sortNodes(roots);
  return roots;
}

function folderContains(documentFolder: string, selectedFolder: string): boolean {
  if (!selectedFolder) return true;
  return documentFolder === selectedFolder || documentFolder.startsWith(`${selectedFolder}/`);
}

function collectFolderPaths(nodes: FolderNode[]): Set<string> {
  const paths = new Set<string>();
  const visit = (items: FolderNode[]) => {
    for (const item of items) {
      paths.add(item.path);
      visit(item.children);
    }
  };
  visit(nodes);
  return paths;
}

function paginationItems(current: number, total: number): PaginationItem[] {
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1);
  if (current <= 4) return [1, 2, 3, 4, 5, "ellipsis-end", total];
  if (current >= total - 3) {
    return [1, "ellipsis-start", total - 4, total - 3, total - 2, total - 1, total];
  }
  return [1, "ellipsis-start", current - 1, current, current + 1, "ellipsis-end", total];
}

function navigateToDocuments() {
  const intent = navigationIntent(window.location, "documents");
  commitNavigationIntent(intent, { dispatchPopStateAfterHash: true });
}

function formatUpdatedAt(value: string | null | undefined): string {
  if (!value) return "更新时间未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "更新时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function FolderItems({
  nodes,
  selectedFolder,
  onSelect,
}: {
  nodes: FolderNode[];
  selectedFolder: string;
  onSelect: (path: string) => void;
}) {
  return (
    <ul className="knowledge-taxonomy-folder-list">
      {nodes.map((node) => {
        const active = selectedFolder === node.path;
        return (
          <li key={node.path}>
            <Button
              className="knowledge-taxonomy-folder-button"
              variant="text"
              theme={active ? "primary" : "default"}
              aria-label={`${node.name}，${node.documents} 篇文档`}
              aria-pressed={active}
              onClick={() => onSelect(active ? "" : node.path)}
            >
              <span className="knowledge-taxonomy-folder-label">
                <FolderOpenIcon aria-hidden="true" />
                <span>{node.name}</span>
              </span>
              <span className="knowledge-taxonomy-count" aria-hidden="true">
                {node.documents}
              </span>
            </Button>
            {node.children.length > 0 ? (
              <FolderItems
                nodes={node.children}
                selectedFolder={selectedFolder}
                onSelect={onSelect}
              />
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

function DocumentResult({ document }: { document: DocumentItem }) {
  const status = STATUS_PRESENTATION[document.status] ?? {
    label: "状态未知",
    theme: "default" as const,
  };
  const folder = document.logical_folder_path?.trim() || "未分类";
  const tags = document.tags ?? [];

  return (
    <li className="knowledge-taxonomy-document-item">
      <span className="knowledge-taxonomy-document-icon" aria-hidden="true">
        <FileIcon />
      </span>
      <div className="knowledge-taxonomy-document-main">
        <div className="knowledge-taxonomy-document-heading">
          <strong title={document.name}>{document.name}</strong>
          <Tag theme={status.theme} variant="light" shape="round" size="small">
            {status.label}
          </Tag>
        </div>
        <div className="knowledge-taxonomy-document-path">
          <FolderOpenIcon aria-hidden="true" />
          <span>{folder}</span>
        </div>
        <div className="knowledge-taxonomy-document-tags" aria-label="文档标签">
          {tags.length ? (
            tags.map((tag) => (
              <Tag key={tag} variant="light-outline" size="small">
                {tag}
              </Tag>
            ))
          ) : (
            <span>未设置标签</span>
          )}
        </div>
      </div>
      <dl className="knowledge-taxonomy-document-facts">
        <div>
          <dt>片段</dt>
          <dd>{Number(document.chunk_count || 0).toLocaleString()}</dd>
        </div>
        <div>
          <dt>类型</dt>
          <dd>{document.doc_type?.toUpperCase() || "未知"}</dd>
        </div>
        <div>
          <dt>更新</dt>
          <dd>{formatUpdatedAt(document.updated_at)}</dd>
        </div>
      </dl>
    </li>
  );
}

function getErrorStatus(error: unknown): number | undefined {
  if (error instanceof ApiError) return error.status;
  if (typeof error !== "object" || error === null || !("status" in error)) return undefined;
  const status = (error as { status?: unknown }).status;
  return typeof status === "number" ? status : undefined;
}

function getModernErrorPresentation(error: unknown): {
  title: string;
  description: string;
} {
  const status = getErrorStatus(error);
  if (status === 401) {
    return {
      title: "身份已失效",
      description: "当前 Actor Token 无法通过知识库认证，请重新连接企业身份。",
    };
  }
  if (status === 403) {
    return {
      title: "没有权限查看目录治理数据",
      description: "当前身份没有读取所选知识库目录的权限。",
    };
  }
  if (status === 422) {
    return {
      title: "当前筛选条件无法执行",
      description: "服务端拒绝了这组目录筛选条件，请清除筛选后重试。",
    };
  }
  if (status === 503) {
    return {
      title: "目录服务暂不可用",
      description: "知识目录服务或数据库尚未就绪，请稍后重试。",
    };
  }
  if (error instanceof ApiError && (error.kind === "network" || error.kind === "timeout")) {
    return {
      title: "目录服务暂不可用",
      description: "暂时无法连接知识目录服务，请确认服务状态后重试。",
    };
  }
  return {
    title: "知识目录加载失败",
    description: "服务没有返回可安全展示的目录结果，请稍后重试。",
  };
}

function createModernPageQuery(folder: string, tag: string, keyword: string): DocumentPageQuery {
  return {
    offset: 0,
    limit: PAGE_SIZE,
    folder_mode: "subtree",
    sort: "updated_at_desc",
    ...(folder ? { folder } : {}),
    ...(tag ? { tag } : {}),
    ...(keyword ? { q: keyword } : {}),
  };
}

interface ModernPageRequest {
  cursor?: string;
  includeSummary: boolean;
  cursorStack: string[];
}

export default function KnowledgeTaxonomyPage({ embedded = false }: { embedded?: boolean }) {
  const workspace = useKnowledgeDocuments();
  const {
    documents,
    status,
    error,
    loading,
    usingMock,
    summary: sharedSummary,
    facets: sharedFacets,
    recent: sharedRecent,
    tagFacetsComplete: sharedTagFacetsComplete,
    tagFacetsScanLimit: sharedTagFacetsScanLimit,
    tagFacetsScanned: sharedTagFacetsScanned,
    tagFacetsTruncated: sharedTagFacetsTruncated,
    summaryGeneratedAt,
    reload,
  } = workspace;
  const actorToken = readKnowledgeActorToken();
  const hasActorToken = Boolean(actorToken.trim());
  const scope = useMemo(() => resolveKnowledgeWorkspaceScope({ actorToken }), [actorToken]);

  const [selectedFolder, setSelectedFolder] = useState("");
  const [selectedTag, setSelectedTag] = useState("");
  const [keyword, setKeyword] = useState("");
  const [currentPage, setCurrentPage] = useState(1);
  const [modernSummary, setModernSummary] = useState<DocumentCatalogSummaryResponse | null>(null);
  const sharedSummaryResponse = useMemo<DocumentCatalogSummaryResponse | null>(
    () =>
      sharedSummary && sharedFacets
        ? {
            dataset_id: scope.datasetId,
            summary: sharedSummary,
            facets: sharedFacets,
            recent: sharedRecent,
            generated_at: summaryGeneratedAt ?? "",
            tag_facets_complete: sharedTagFacetsComplete ?? !sharedTagFacetsTruncated,
            tag_facets_scan_limit: sharedTagFacetsScanLimit ?? 0,
            tag_facets_scanned: sharedTagFacetsScanned ?? 0,
            tag_facets_truncated: sharedTagFacetsTruncated,
          }
        : null,
    [
      scope.datasetId,
      sharedFacets,
      sharedRecent,
      sharedSummary,
      sharedTagFacetsComplete,
      sharedTagFacetsScanLimit,
      sharedTagFacetsScanned,
      sharedTagFacetsTruncated,
      summaryGeneratedAt,
    ],
  );
  const effectiveSummary = sharedSummaryResponse ?? modernSummary;
  const [modernPage, setModernPage] = useState<DocumentPageResponse | null>(null);
  const [modernStatus, setModernStatus] = useState<ModernCatalogStatus>(
    hasActorToken ? "loading" : "idle",
  );
  const [modernError, setModernError] = useState<unknown>(null);
  const [cursorStack, setCursorStack] = useState<string[]>([]);
  const requestSequenceRef = useRef(0);
  const controllerRef = useRef<AbortController | null>(null);
  const previousScopeKeyRef = useRef("");
  const previousQueryKeyRef = useRef("");

  const normalizedKeyword = keyword.trim().toLocaleLowerCase("zh-CN");
  const localTaxonomy = useMemo(
    () => (hasActorToken ? { categories: [], tags: [] } : projectTaxonomy(documents)),
    [documents, hasActorToken],
  );
  const serverFolderFacets = useMemo(
    () => effectiveSummary?.facets.folders ?? [],
    [effectiveSummary?.facets.folders],
  );
  const folderTree = useMemo(
    () =>
      buildFolderTree(
        hasActorToken
          ? serverFolderFacets.map((folder) => ({
              name: folder.path,
              documents: folder.documents,
            }))
          : localTaxonomy.categories,
      ),
    [hasActorToken, localTaxonomy.categories, serverFolderFacets],
  );
  const availableFolders = useMemo(() => collectFolderPaths(folderTree), [folderTree]);
  const tagFacets = useMemo(
    () => (hasActorToken ? (effectiveSummary?.facets.tags ?? []) : localTaxonomy.tags),
    [effectiveSummary?.facets.tags, hasActorToken, localTaxonomy.tags],
  );
  const availableTags = useMemo(() => new Set(tagFacets.map((tag) => tag.name)), [tagFacets]);
  const activeFolder = selectedFolder && availableFolders.has(selectedFolder) ? selectedFolder : "";
  const activeTag = selectedTag && availableTags.has(selectedTag) ? selectedTag : "";

  const modernQuery = useMemo(
    () => createModernPageQuery(activeFolder, activeTag, normalizedKeyword),
    [activeFolder, activeTag, normalizedKeyword],
  );
  const queryKey = JSON.stringify({
    folder: activeFolder,
    tag: activeTag,
    q: normalizedKeyword,
  });
  const scopeKey = `${scope.tenantId}:${scope.datasetId}:${actorToken}`;

  const loadModernPage = useCallback(
    async ({ cursor, includeSummary, cursorStack: nextCursorStack }: ModernPageRequest) => {
      if (!hasActorToken) return;

      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;
      const sequence = ++requestSequenceRef.current;
      setModernStatus("loading");
      setModernError(null);

      const requestOptions = {
        tenantId: scope.tenantId,
        actorToken,
        signal: controller.signal,
      };

      try {
        // Start both requests before awaiting either one. The summary powers the
        // directory/facet surfaces while the page powers the result list.
        const pagePromise = fetchDocumentPage(
          scope.datasetId,
          { ...modernQuery, ...(cursor ? { cursor } : {}) },
          requestOptions,
        );
        const summaryPromise =
          includeSummary && !sharedSummaryResponse
            ? fetchDocumentSummary(scope.datasetId, requestOptions)
            : Promise.resolve(null);
        const [page, summary] = await Promise.all([pagePromise, summaryPromise]);

        if (controller.signal.aborted || sequence !== requestSequenceRef.current) return;
        if (summary) setModernSummary(summary);
        setModernPage(page);
        setCursorStack(nextCursorStack);
        setModernStatus("ready");
      } catch (requestError) {
        if (controller.signal.aborted || sequence !== requestSequenceRef.current) return;
        setModernError(requestError);
        setModernStatus("error");
      } finally {
        if (controllerRef.current === controller) controllerRef.current = null;
      }
    },
    [
      actorToken,
      hasActorToken,
      modernQuery,
      scope.datasetId,
      scope.tenantId,
      sharedSummaryResponse,
    ],
  );

  useEffect(() => {
    if (!hasActorToken) {
      controllerRef.current?.abort();
      previousScopeKeyRef.current = scopeKey;
      previousQueryKeyRef.current = queryKey;
      setModernStatus("idle");
      setModernError(null);
      setModernSummary(null);
      setModernPage(null);
      setCursorStack([]);
      return;
    }

    if (previousScopeKeyRef.current === scopeKey) return;
    previousScopeKeyRef.current = scopeKey;
    previousQueryKeyRef.current = queryKey;
    setModernSummary(null);
    setModernPage(null);
    setCursorStack([]);
    void loadModernPage({ includeSummary: true, cursorStack: [] });
  }, [hasActorToken, loadModernPage, queryKey, scopeKey]);

  useEffect(() => {
    if (!hasActorToken) return;
    if (previousQueryKeyRef.current === queryKey) return;
    previousQueryKeyRef.current = queryKey;
    setCurrentPage(1);
    setCursorStack([]);
    void loadModernPage({ includeSummary: false, cursorStack: [] });
  }, [hasActorToken, loadModernPage, queryKey]);

  useEffect(
    () => () => {
      controllerRef.current?.abort();
    },
    [],
  );

  const visibleDocuments = useMemo(() => {
    if (hasActorToken) return [];
    return [...documents]
      .filter((document) => {
        const folder = document.logical_folder_path?.trim() || "未分类";
        if (!folderContains(folder, activeFolder)) return false;
        if (activeTag && !(document.tags ?? []).includes(activeTag)) return false;
        if (!normalizedKeyword) return true;
        const searchable = [document.name, folder, ...(document.tags ?? [])]
          .join(" ")
          .toLocaleLowerCase("zh-CN");
        return searchable.includes(normalizedKeyword);
      })
      .sort((left, right) =>
        String(right.updated_at ?? "").localeCompare(String(left.updated_at ?? "")),
      );
  }, [activeFolder, activeTag, documents, hasActorToken, normalizedKeyword]);

  const pageCount = Math.max(1, Math.ceil(visibleDocuments.length / PAGE_SIZE));
  const activePage = Math.min(currentPage, pageCount);
  const pagedDocuments = useMemo(() => {
    const start = (activePage - 1) * PAGE_SIZE;
    return visibleDocuments.slice(start, start + PAGE_SIZE);
  }, [activePage, visibleDocuments]);
  const serverDocuments = modernPage?.items ?? [];
  const displayedDocuments = hasActorToken ? serverDocuments : pagedDocuments;
  const globalDocumentCount = hasActorToken
    ? (effectiveSummary?.summary.total ?? 0)
    : documents.length;
  const filteredDocumentCount = hasActorToken ? (modernPage?.total ?? 0) : visibleDocuments.length;
  const pageItems = useMemo(() => paginationItems(activePage, pageCount), [activePage, pageCount]);
  const hasFilters = Boolean(activeFolder || activeTag || normalizedKeyword);
  const modernBlockingState =
    hasActorToken && (modernStatus === "loading" || modernStatus === "error") && !modernPage;
  const showPageState = hasActorToken
    ? modernBlockingState
    : status === "loading" || status === "error";
  const serverHasPagination = Boolean(
    hasActorToken && modernPage && (modernPage.next_cursor || cursorStack.length > 0),
  );
  const modernErrorPresentation = getModernErrorPresentation(modernError);
  const tagFacetWarning =
    hasActorToken && effectiveSummary && !effectiveSummary.tag_facets_complete
      ? `仅统计前 ${effectiveSummary.tag_facets_scan_limit.toString()} 篇文档，标签结果可能不完整`
      : null;

  const selectFolder = (folder: string) => {
    setSelectedFolder(folder);
    setCurrentPage(1);
  };

  const selectTag = (tag: string) => {
    setSelectedTag(tag);
    setCurrentPage(1);
  };

  const updateKeyword = (value: string) => {
    setKeyword(value);
    setCurrentPage(1);
  };

  const clearFilters = () => {
    setSelectedFolder("");
    setSelectedTag("");
    setKeyword("");
    setCurrentPage(1);
  };

  const reloadCatalog = useCallback(() => {
    if (!hasActorToken) return reload();
    return loadModernPage({
      cursor: cursorStack[cursorStack.length - 1],
      includeSummary: true,
      cursorStack,
    });
  }, [cursorStack, hasActorToken, loadModernPage, reload]);

  const goToNextServerPage = () => {
    const nextCursor = modernPage?.next_cursor;
    if (!nextCursor || modernStatus === "loading") return;
    const nextStack = [...cursorStack, nextCursor];
    void loadModernPage({
      cursor: nextCursor,
      includeSummary: false,
      cursorStack: nextStack,
    });
  };

  const goToPreviousServerPage = () => {
    if (!cursorStack.length || modernStatus === "loading") return;
    const previousStack = cursorStack.slice(0, -1);
    void loadModernPage({
      cursor: previousStack[previousStack.length - 1],
      includeSummary: false,
      cursorStack: previousStack,
    });
  };

  return (
    <div className="page-slot knowledge-taxonomy-page">
      {!embedded ? (
        <PageTopbar
          icon={<DataBaseIcon />}
          title="目录治理"
          subtitle="按目录、标签与文档状态统一治理知识资产"
          extra={
            <div className="knowledge-taxonomy-topbar-actions">
              <Tag theme={usingMock ? "warning" : "primary"} variant="light-outline" shape="round">
                {usingMock ? "演示数据" : "目录真账"}
              </Tag>
              <Button
                size="small"
                variant="outline"
                icon={<RefreshIcon />}
                loading={hasActorToken ? modernStatus === "loading" : loading}
                onClick={() => void reloadCatalog()}
              >
                刷新
              </Button>
              <Button size="small" theme="primary" onClick={navigateToDocuments}>
                管理文档
              </Button>
            </div>
          }
        />
      ) : null}

      <div className="page-shell">
        <div className="page-shell-inner knowledge-taxonomy-shell">
          {showPageState ? (
            hasActorToken ? (
              modernStatus === "loading" ? (
                <PageState
                  status="loading"
                  title="正在读取服务端目录"
                  description="目录树、标签统计与文档结果正在从知识目录服务加载。"
                />
              ) : (
                <PageState
                  status="error"
                  title={modernErrorPresentation.title}
                  description={modernErrorPresentation.description}
                  extra={
                    <Button
                      theme="primary"
                      icon={<RefreshIcon />}
                      onClick={() => void reloadCatalog()}
                    >
                      重试读取目录
                    </Button>
                  }
                />
              )
            ) : (
              <KnowledgeWorkspacePageState status={status} error={error} onRetry={reload} />
            )
          ) : (
            <>
              <section
                className="knowledge-taxonomy-overview"
                aria-labelledby="knowledge-taxonomy-overview-title"
              >
                <div className="knowledge-taxonomy-overview-copy">
                  <span className="knowledge-taxonomy-eyebrow">KNOWLEDGE DIRECTORY</span>
                  <h2 id="knowledge-taxonomy-overview-title">让目录结构直接反映知识责任边界</h2>
                  <p>
                    {hasActorToken
                      ? "目录与标签来自服务端 facets，结果区按服务端游标分页，只展示当前筛选范围内的真实文档。"
                      : "目录定位知识归属，标签表达治理维度，结果区只展示当前筛选范围内的真实文档。"}
                  </p>
                </div>
                <div className="knowledge-taxonomy-metrics" aria-label="目录治理计数">
                  <div>
                    <FileIcon aria-hidden="true" />
                    <strong>{globalDocumentCount.toLocaleString()} 篇文档</strong>
                    <span>{hasActorToken ? "服务端目录总量" : "资产总量"}</span>
                  </div>
                  <div>
                    <FolderOpenIcon aria-hidden="true" />
                    <strong>
                      {(hasActorToken
                        ? serverFolderFacets.length
                        : localTaxonomy.categories.length
                      ).toLocaleString()}{" "}
                      个目录
                    </strong>
                    <span>{hasActorToken ? "目录 Facet" : "末级目录"}</span>
                  </div>
                  <div>
                    <TagIcon aria-hidden="true" />
                    <strong>{tagFacets.length.toLocaleString()} 个标签</strong>
                    <span>{hasActorToken ? "服务端标签 Facet" : "治理维度"}</span>
                  </div>
                </div>
              </section>

              <div className="knowledge-taxonomy-workspace">
                <nav className="knowledge-taxonomy-directory" aria-label="目录导航">
                  <Card title="目录导航" subtitle="选择上级目录时同时包含其子目录" bordered>
                    <Button
                      className="knowledge-taxonomy-folder-button knowledge-taxonomy-folder-all"
                      variant="text"
                      theme={activeFolder ? "default" : "primary"}
                      aria-label={`全部目录，${globalDocumentCount} 篇文档`}
                      aria-pressed={!activeFolder}
                      onClick={() => selectFolder("")}
                    >
                      <span className="knowledge-taxonomy-folder-label">
                        <ViewListIcon aria-hidden="true" />
                        <span>全部目录</span>
                      </span>
                      <span className="knowledge-taxonomy-count" aria-hidden="true">
                        {globalDocumentCount}
                      </span>
                    </Button>
                    {folderTree.length ? (
                      <FolderItems
                        nodes={folderTree}
                        selectedFolder={activeFolder}
                        onSelect={selectFolder}
                      />
                    ) : (
                      <Empty
                        className="knowledge-taxonomy-panel-empty"
                        size="small"
                        title="暂无目录"
                        description="文档入库后将按逻辑目录自动归集。"
                      />
                    )}
                  </Card>
                </nav>

                <section
                  className="knowledge-taxonomy-tags"
                  role="region"
                  aria-labelledby="knowledge-taxonomy-tags-title"
                >
                  <Card
                    title={<span id="knowledge-taxonomy-tags-title">标签筛选</span>}
                    subtitle="标签与目录采用交集筛选"
                    bordered
                  >
                    {tagFacetWarning ? (
                      <div className="knowledge-taxonomy-facet-warning" role="note">
                        <strong>标签统计为有界扫描</strong>
                        <span>{tagFacetWarning}</span>
                      </div>
                    ) : null}
                    {tagFacets.length ? (
                      <div className="knowledge-taxonomy-tag-list">
                        {tagFacets.map((tag) => {
                          const active = activeTag === tag.name;
                          return (
                            <Button
                              key={tag.name}
                              className="knowledge-taxonomy-tag-button"
                              variant={active ? "base" : "outline"}
                              theme={active ? "primary" : "default"}
                              aria-label={`${tag.name}，${tag.documents} 篇文档`}
                              aria-pressed={active}
                              onClick={() => selectTag(active ? "" : tag.name)}
                            >
                              <TagIcon aria-hidden="true" />
                              <span>{tag.name}</span>
                              <span className="knowledge-taxonomy-tag-count" aria-hidden="true">
                                {tag.documents}
                              </span>
                            </Button>
                          );
                        })}
                      </div>
                    ) : (
                      <Empty
                        className="knowledge-taxonomy-panel-empty"
                        size="small"
                        title="暂无标签"
                        description="为文档设置标签后可在此组合筛选。"
                      />
                    )}
                  </Card>
                </section>

                <section
                  className="knowledge-taxonomy-results"
                  role="region"
                  aria-labelledby="knowledge-taxonomy-results-title"
                  aria-busy={hasActorToken && modernStatus === "loading"}
                >
                  <Card bordered>
                    <div className="knowledge-taxonomy-results-header">
                      <div>
                        <span className="knowledge-taxonomy-section-icon" aria-hidden="true">
                          <ViewListIcon />
                        </span>
                        <div>
                          <h2 id="knowledge-taxonomy-results-title">文档结果</h2>
                          <p>
                            {hasActorToken
                              ? "服务端按更新时间排序，使用游标保持大规模知识库翻页稳定。"
                              : "按更新时间排序，保留目录、标签与索引状态上下文。"}
                          </p>
                        </div>
                      </div>
                      <label className="knowledge-taxonomy-search">
                        <span className="knowledge-taxonomy-visually-hidden">搜索文档</span>
                        <Input
                          type="search"
                          value={keyword}
                          placeholder="搜索名称、目录或标签"
                          prefixIcon={<SearchIcon />}
                          clearable
                          onChange={(value) => updateKeyword(String(value))}
                        />
                      </label>
                    </div>

                    <div className="knowledge-taxonomy-filter-strip">
                      <div className="knowledge-taxonomy-active-filters">
                        <span>当前范围</span>
                        <Tag theme="primary" variant="light">
                          {activeFolder || "全部目录"}
                        </Tag>
                        {activeTag ? (
                          <Tag theme="primary" variant="light">
                            #{activeTag}
                          </Tag>
                        ) : null}
                        {normalizedKeyword ? (
                          <Tag theme="default" variant="light">
                            关键词：{keyword.trim()}
                          </Tag>
                        ) : null}
                      </div>
                      <div
                        className="knowledge-taxonomy-result-summary"
                        role="status"
                        aria-live="polite"
                      >
                        筛选结果 <strong>{filteredDocumentCount.toLocaleString()}</strong>{" "}
                        篇文档，本页 {displayedDocuments.length} 篇
                      </div>
                      {hasFilters ? (
                        <Button size="small" variant="text" onClick={clearFilters}>
                          清除筛选
                        </Button>
                      ) : null}
                    </div>

                    {displayedDocuments.length ? (
                      <>
                        <ul className="knowledge-taxonomy-document-list">
                          {displayedDocuments.map((document) => (
                            <DocumentResult key={document.id} document={document} />
                          ))}
                        </ul>
                        {hasActorToken && serverHasPagination ? (
                          <nav
                            className="knowledge-taxonomy-pagination knowledge-taxonomy-server-pagination"
                            aria-label="文档结果分页"
                          >
                            <span
                              className="knowledge-taxonomy-pagination-summary"
                              aria-live="polite"
                            >
                              第 {cursorStack.length + 1} 页 · 服务端游标分页
                            </span>
                            <div className="knowledge-taxonomy-pagination-controls">
                              <Button
                                size="small"
                                variant="outline"
                                disabled={!cursorStack.length || modernStatus === "loading"}
                                aria-label="上一页"
                                onClick={goToPreviousServerPage}
                              >
                                上一页
                              </Button>
                              <Button
                                size="small"
                                variant="outline"
                                disabled={!modernPage?.next_cursor || modernStatus === "loading"}
                                aria-label="下一页"
                                onClick={goToNextServerPage}
                              >
                                下一页
                              </Button>
                            </div>
                          </nav>
                        ) : null}
                        {!hasActorToken && pageCount > 1 ? (
                          <nav className="knowledge-taxonomy-pagination" aria-label="文档结果分页">
                            <span
                              className="knowledge-taxonomy-pagination-summary"
                              aria-live="polite"
                            >
                              第 {activePage} / {pageCount} 页
                            </span>
                            <div className="knowledge-taxonomy-pagination-controls">
                              <Button
                                size="small"
                                variant="outline"
                                disabled={activePage === 1}
                                aria-label="上一页"
                                onClick={() => setCurrentPage(activePage - 1)}
                              >
                                上一页
                              </Button>
                              <div className="knowledge-taxonomy-pagination-pages">
                                {pageItems.map((item) =>
                                  typeof item === "number" ? (
                                    <Button
                                      key={item}
                                      size="small"
                                      variant={item === activePage ? "base" : "outline"}
                                      theme={item === activePage ? "primary" : "default"}
                                      aria-label={`第 ${item} 页`}
                                      aria-current={item === activePage ? "page" : undefined}
                                      onClick={() => setCurrentPage(item)}
                                    >
                                      {item}
                                    </Button>
                                  ) : (
                                    <span
                                      key={item}
                                      className="knowledge-taxonomy-pagination-ellipsis"
                                      aria-hidden="true"
                                    >
                                      …
                                    </span>
                                  ),
                                )}
                              </div>
                              <Button
                                size="small"
                                variant="outline"
                                disabled={activePage === pageCount}
                                aria-label="下一页"
                                onClick={() => setCurrentPage(activePage + 1)}
                              >
                                下一页
                              </Button>
                            </div>
                          </nav>
                        ) : null}
                      </>
                    ) : globalDocumentCount > 0 ? (
                      <Empty
                        className="knowledge-taxonomy-results-empty"
                        title="没有符合当前筛选条件的文档"
                        description="调整目录、标签或关键词，查看其他知识资产。"
                        action={
                          <Button theme="primary" variant="outline" onClick={clearFilters}>
                            清除筛选
                          </Button>
                        }
                      />
                    ) : (
                      <Empty
                        className="knowledge-taxonomy-results-empty"
                        title="知识库尚无可治理文档"
                        description="先导入真实文档，目录与标签分布会自动出现在这里。"
                        action={
                          <Button theme="primary" onClick={navigateToDocuments}>
                            导入文档
                          </Button>
                        }
                      />
                    )}
                  </Card>
                </section>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
