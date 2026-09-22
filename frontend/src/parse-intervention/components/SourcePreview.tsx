import { useEffect, useRef, useState } from "react";
import { Alert, Button } from "tdesign-react";
import { fetchDocumentSource, SourcePreviewRefusedError, type DocumentSourceBlob } from "../api/parseInterventionApi";
import type { ParseScope } from "../model/parseInterventionModel";

type Phase =
  | { kind: "loading" }
  | { kind: "ready"; source: DocumentSourceBlob; url: string }
  | { kind: "refused"; code: string; message: string }
  | { kind: "failed"; message: string };

/** 每种拒绝原因给一句人话：操作员要能分辨"这份没有"与"这台机器没开这个能力"。 */
export const REFUSAL_COPY: Record<string, string> = {
  source_preview_disabled: "这台服务没有开放原文查看：未配置可读取的原文目录（catalog.source_preview_roots）。",
  source_preview_missing: "这份文档的原文文件已不在磁盘上，或从未记录路径。",
  source_preview_out_of_scope: "这份文档的原文路径不在已声明的目录内，因此不提供查看。",
  source_preview_unsupported_kind: "这个后缀没有在可查看来源表里登记，界面不会猜测它的类型。",
  source_preview_too_large: "原文超过这一类可查看来源的大小上限，界面不做截断展示。",
  source_preview_unavailable: "原文当前读不出来（文件被移动、占用或权限不足）。",
};

/** 权威在後端那张可查看来源表；这里只是第二道：会执行文档自身内容的类型，界面一律不渲染、
 * 也不给"新标签页打开"的链接（blob: 链接继承本站源）。 */
const NEVER_RENDERED = new Set(["text/html", "image/svg+xml", "application/xhtml+xml"]);

export function previewKind(mediaType: string): "pdf" | "image" | "text" | "download" {
  const type = mediaType.split(";")[0].trim().toLowerCase();
  if (NEVER_RENDERED.has(type)) return "download";
  if (type === "application/pdf") return "pdf";
  if (type.startsWith("image/")) return "image";
  if (type.startsWith("text/") || type === "application/json") return "text";
  return "download";
}

export default function SourcePreview({ scope, documentName }: { scope: ParseScope; documentName: string }) {
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [reload, setReload] = useState(0);
  const sequence = useRef(0);
  const scopeKey = `${scope.datasetId}\u0000${scope.docId}`;

  useEffect(() => {
    const token = ++sequence.current;
    const controller = new AbortController();
    let objectUrl: string | null = null;
    setPhase({ kind: "loading" });
    fetchDocumentSource(scope, "inline", controller.signal)
      .then((source) => {
        if (token !== sequence.current) return;
        objectUrl = URL.createObjectURL(source.blob);
        setPhase({ kind: "ready", source, url: objectUrl });
      })
      .catch((error: unknown) => {
        if (token !== sequence.current) return;
        if (error instanceof DOMException && error.name === "AbortError") return;
        if (error instanceof SourcePreviewRefusedError) {
          setPhase({ kind: "refused", code: error.code, message: error.message });
          return;
        }
        setPhase({ kind: "failed", message: error instanceof Error ? error.message : "原文读取失败" });
      });
    return () => {
      controller.abort();
      // 只有本轮真的建立过 object URL 才回收；晚到的成功不会把下一轮的对象 URL 撤掉，
      // 因为 sequence 已经推进，.then 里就不会再走到这里赋值的分支。
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
    // scope 里只有 datasetId/docId 影响取哪份原文；actorToken 轮换不该重新下载。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeKey, reload]);

  const download = () => {
    const controller = new AbortController();
    fetchDocumentSource(scope, "attachment", controller.signal)
      .then((source) => {
        const url = URL.createObjectURL(source.blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = documentName || "document";
        link.click();
        URL.revokeObjectURL(url);
      })
      .catch(() => undefined);
  };

  if (phase.kind === "loading") return <p className="parse-muted">正在读取原文…</p>;
  if (phase.kind === "refused") {
    return (
      <Alert
        theme={phase.code === "source_preview_disabled" ? "warning" : "info"}
        title="这份文档暂时没有可看的原文"
        message={`${REFUSAL_COPY[phase.code] ?? phase.message}（${phase.code}）`}
      />
    );
  }
  if (phase.kind === "failed") {
    return (
      <div>
        <Alert theme="error" title="原文读取失败" message={phase.message} />
        <Button size="small" variant="text" onClick={() => setReload((value) => value + 1)}>重试</Button>
      </div>
    );
  }

  const { source, url } = phase;
  const kind = previewKind(source.mediaType);
  return (
    <div className="parse-source-preview">
      {kind === "pdf" ? (
        <iframe title={`${documentName} 原文预览`} src={url} sandbox="" referrerPolicy="no-referrer" style={{ width: "100%", height: 460, border: 0 }} />
      ) : null}
      {kind === "image" ? <img alt={`${documentName} 原文`} src={url} style={{ maxWidth: "100%" }} /> : null}
      {kind === "text" || kind === "download" ? (
        <div>
          <Alert
            theme="info"
            title={kind === "text" ? "文本型原文不在这一栏里铺开" : "这一类原文不支持在线查看"}
            message={
              kind === "text"
                ? "切片正文已经在本工作区左侧逐条呈现；这里给的是原文本身，避免把两份文本混成一份。"
                : `类型 ${source.mediaType || "未标注"} 按后端策略只提供下载。`
            }
          />
          <span>
            {kind === "text" ? <a href={url} target="_blank" rel="noreferrer">新标签页打开</a> : null}
            <Button size="small" variant="text" onClick={download}>下载原文</Button>
          </span>
        </div>
      ) : null}
      <div className="parse-source-preview-meta">
        <span>{source.mediaType || "未标注类型"}</span>
        <span>{(source.blob.size / 1024).toFixed(1)} KB</span>
        {source.etag ? <span className="is-mono">revision {source.etag.replace(/"/g, "")}</span> : null}
      </div>
    </div>
  );
}
