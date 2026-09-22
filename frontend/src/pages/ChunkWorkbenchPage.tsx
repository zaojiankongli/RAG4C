import { useCallback, useEffect, useMemo, useState } from "react";
import { Button, Select } from "../ui/index";
import PageState from "../components/PageState";
import { useConnection } from "../context/ConnectionContext";
import { useOptionalKnowledgeWorkspace } from "../knowledge/KnowledgeWorkspaceContext";
import { readKnowledgeActorToken, resolveKnowledgeWorkspaceScope } from "../knowledge/workspaceScope";
import { fetchDocument } from "../api/client";
import type { DocumentItem } from "../types/rag";
import ParseInterventionWorkspace from "../parse-intervention/ParseInterventionWorkspace";
import {
  chunkWorkbenchNavigationIntent,
  navigationIntent,
  parseChunkWorkbenchLocation,
  type PageKey,
} from "../run/appRoute";

function navigateTo(url: string, mode: "history" | "hash") {
  if (mode === "history") {
    window.history.pushState(window.history.state, "", url);
    window.dispatchEvent(new PopStateEvent("popstate"));
  } else {
    window.location.hash = url;
  }
}

/** 侧栏入口落在没有 doc 参数的路由上时不该是死路：就地选文档，不新造查询端点。 */
function openDocuments() {
  const intent = navigationIntent(window.location, "documents");
  navigateTo(intent.url, intent.mode);
}

function WorkbenchLauncher({
  documents,
  onOpen,
}: {
  documents: DocumentItem[];
  onOpen: (docId: string) => void;
}) {
  const options = useMemo(
    () =>
      documents
        .filter((doc) => (doc.lifecycle_state ?? "active") === "active")
        .map((doc) => ({ value: doc.id, label: `${doc.name} · ${doc.chunk_count} 切片` })),
    [documents],
  );
  return (
    <div className="page-shell">
      <div className="page-shell-inner">
        <PageState
          status={options.length ? "empty" : "error"}
          title={options.length ? "选择要干预切片的文档" : "当前知识库没有可选文档"}
          description={
            options.length
              ? "解析干预工作区按文档打开：选定一篇后进入「上下文｜切片｜编辑」三栏工作区，URL 会带上 doc 与 chunk，可直接分享。"
              : "先在文档管理里入库并完成解析的文档，或切换主知识库后再回来。"
          }
          extra={
            options.length ? (
              <Select
                className="parse-workbench-control-min-h"
                placeholder="选择文档"
                options={options}
                filterable
                aria-label="选择要干预切片的文档"
                onChange={(value: string) => onOpen(String(value))}
              />
            ) : (
              <Button className="parse-workbench-control-min-h" onClick={openDocuments}>
                前往文档管理
              </Button>
            )
          }
        />
      </div>
    </div>
  );
}

export default function ChunkWorkbenchPage({
  onDirtyChange,
}: {
  onDirtyChange?: (dirty: boolean) => void;
} = {}) {
  const { online } = useConnection();
  const workspace = useOptionalKnowledgeWorkspace();
  const actorToken = readKnowledgeActorToken();
  const fallbackScope = resolveKnowledgeWorkspaceScope({ actorToken });
  const [route, setRoute] = useState(() => parseChunkWorkbenchLocation(window.location));
  const [document, setDocument] = useState<DocumentItem | null>(null);
  const [documentError, setDocumentError] = useState("");
  const [datasetOverride, setDatasetOverride] = useState("");
  const [dirty, setDirtyState] = useState(false);
  const setDirty = useCallback(
    (next: boolean) => {
      setDirtyState(next);
      onDirtyChange?.(next);
    },
    [onDirtyChange],
  );

  useEffect(() => {
    const onLocation = () => {
      setDatasetOverride("");
      setRoute(parseChunkWorkbenchLocation(window.location));
    };
    window.addEventListener("popstate", onLocation);
    window.addEventListener("hashchange", onLocation);
    return () => {
      window.removeEventListener("popstate", onLocation);
      window.removeEventListener("hashchange", onLocation);
    };
  }, []);

  useEffect(() => {
    if (!dirty) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [dirty]);

  const docId = route?.docId ?? "";
  const contextDocuments = useMemo(() => workspace?.documents ?? [], [workspace?.documents]);
  const sessionDatasetId = workspace?.datasetId || fallbackScope.datasetId;
  const datasetId = datasetOverride || sessionDatasetId;
  const tenantId = document?.tenant_id?.trim() || workspace?.tenantId || fallbackScope.tenantId;

  useEffect(() => {
    if (!docId) {
      setDocument(null);
      setDocumentError("");
      return;
    }
    const known = contextDocuments.find((item) => item.id === docId) ?? null;
    if (known) {
      setDocument(known);
      setDocumentError("");
      return;
    }
    if (online !== true) {
      setDocument(null);
      setDocumentError("解析干预不可离线演示：需要连接权威后端才能读取该文档。");
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    fetchDocument(docId, controller.signal)
      .then((detail) => {
        if (cancelled) return;
        setDocument(detail);
        setDocumentError("");
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setDocument(null);
        setDocumentError(error instanceof Error ? error.message : String(error));
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [contextDocuments, docId, online]);

  const datasetMismatch = Boolean(route?.datasetId && datasetId && route.datasetId !== datasetId);
  const scope = useMemo(
    () =>
      docId &&
      actorToken &&
      datasetId &&
      !datasetMismatch &&
      (!document?.dataset_id || document.dataset_id === datasetId)
        ? { tenantId: tenantId || "default", datasetId, actorToken, docId }
        : null,
    [actorToken, datasetId, datasetMismatch, docId, document, tenantId],
  );

  const goTo = useCallback((key: PageKey) => {
    const intent = navigationIntent(window.location, key);
    navigateTo(intent.url, intent.mode);
  }, []);

  const leaveWorkspace = useCallback(
    (message: string, next: () => void) => {
      if (dirty && !window.confirm(message)) return;
      setDirty(false);
      next();
    },
    [dirty, setDirty],
  );

  if (!route) {
    return (
      <WorkbenchLauncher
        documents={contextDocuments}
        onOpen={(id) => {
          const intent = chunkWorkbenchNavigationIntent(window.location, {
            docId: id,
            chunkId: "",
            datasetId,
          });
          navigateTo(intent.url, intent.mode);
        }}
      />
    );
  }

  if (datasetMismatch) {
    return (
      <div className="page-shell">
        <div className="page-shell-inner">
          <PageState
            status="error"
            title="链接指向另一个知识库"
            description={`深链要求知识库 ${route.datasetId}，当前主知识库是 ${datasetId}。切片权威按知识库隔离，这里不做跨库读写。`}
            extra={
              <Button
                className="parse-workbench-control-min-h"
                onClick={() => setDatasetOverride(route.datasetId)}
              >
                按链接里的知识库打开
              </Button>
            }
          />
        </div>
      </div>
    );
  }

  if (!scope) {
    return (
      <div className="page-shell">
        <div className="page-shell-inner">
          <PageState
            status="error"
            title={documentError || "工作区范围不可用"}
            description={
              documentError
                ? "取不到该文档的权威范围，无法进入解析干预工作区。"
                : "需要已签名的 Actor、可用的主知识库，且文档归属当前知识库。"
            }
            extra={
              <Button className="parse-workbench-control-min-h" onClick={() => leaveWorkspace("离开解析干预工作区将丢弃未提交草稿，是否继续？", () => goTo("documents"))}>
                返回文档管理
              </Button>
            }
          />
        </div>
      </div>
    );
  }

  return (
    <ParseInterventionWorkspace
      scope={scope}
      document={document}
      online={online}
      initialChunkId={route.chunkId || null}
      onDirtyChange={setDirty}
      onReturn={() =>
        leaveWorkspace("当前切片修改尚未提交。返回文档列表将丢弃草稿，是否继续？", () => goTo("documents"))
      }
      onNavigateOperator={(target) =>
        leaveWorkspace("当前切片修改尚未提交。离开解析干预工作区将丢弃草稿，是否继续？", () => goTo(target))
      }
    />
  );
}
