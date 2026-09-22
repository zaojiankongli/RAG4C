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

/** 权威在後端那张可查看来源表：`?disposition=` 那一侧算出的 Content-Disposition 就是它对本
 * 这份字节的表态（`inline = spec.inline_renderable and 不是 attachment`）。所以这里先问表态、
 * 再按类型挑渲染面 —— 反过来只按 `text/` 前缀判，将来任何登记成"只下载"的 text 型后缀都会
 * 从这条前缀规则里白拿到一个"新标签页打开"的 blob 链接。
 * NEVER_RENDERED 是第二道：表态说 inline 也照样不渲染、不给链接（blob: 链接继承本站源）。 */
const NEVER_RENDERED = new Set(["text/html", "image/svg+xml", "application/xhtml+xml"]);

/** `Content-Disposition` 的权威形态是 ``inline; filename="…"; filename*=UTF-8''…``
 * （`server/knowledge_source_preview_api.py:_content_disposition`，`tests/…:210` 钉着
 * `startswith("inline;")`），**从来不是光秃秃的 `inline`**。所以这里只取前导 token，
 * 并且区分"后端说 attachment"与"我没听到表态"：跨源时这个头不在 CORS 的白名单里，
 * 客户端可能拿到空串 —— 把空串当成"要求下载"会一刀切掉整条在线查看。 */
export function dispositionToken(header: string | null | undefined): "inline" | "attachment" | null {
  const value = (header ?? "").trim().toLowerCase();
  if (value.startsWith("inline")) return "inline";
  if (value.startsWith("attachment")) return "attachment";
  return null;
}

export function previewKind(
  mediaType: string,
  disposition?: string | null,
): "pdf" | "image" | "text" | "download" {
  if (dispositionToken(disposition) === "attachment") return "download";
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
  const pendingDownloads = useRef(new Set<string>());
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

  // 只挂在卸载上：换文档/重试不是"这一轮下载结束了"，在那里回收会把在写的文件截断。
  useEffect(() => () => {
    for (const url of pendingDownloads.current) URL.revokeObjectURL(url);
    pendingDownloads.current.clear();
  }, []);

  const download = () => {
    const controller = new AbortController();
    fetchDocumentSource(scope, "attachment", controller.signal)
      .then((source) => {
        const url = URL.createObjectURL(source.blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = documentName || "document";
        pendingDownloads.current.add(url);
        link.click();
        // 不在同一个 tick 回收：click() 只是把请求交给浏览器，同 tick revoke 有把还在写盘的
        // 那份下载截断的风险（真浏览器行为 jsdom 测不到，这里只按"交出去之后再延后回收"处理）。
        // 定时器与卸载兜底二选一先到，delete() 的返回值保证每条只撤一次。
        setTimeout(() => {
          if (pendingDownloads.current.delete(url)) URL.revokeObjectURL(url);
        }, 0);
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
  const kind = previewKind(source.mediaType, source.disposition);
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
