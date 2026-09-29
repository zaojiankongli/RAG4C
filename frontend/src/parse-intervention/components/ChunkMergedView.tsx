import { useMemo } from "react";
import type { ReactNode } from "react";
import { Alert, Button, Tag } from "tdesign-react";
import type { DocumentChunkItem } from "../../types/rag";
import { buildMergedDocument } from "../model/mergedDocument";

/**
 * 整篇合并视图：由当前权威 ChunkHead 拼回一篇可读全文，回答"这篇知识现在是什么"。
 *
 * 不是原文预览 —— 后者要文件伺服，且回答的是"解析器当初看到了什么"，两者不可互相冒充。
 * 样式复用切片列表的既有 `parse-*` 类：合并视图与列表是同一份数据的两种看法，
 * 不该长出第二套视觉语言。
 */
export default function ChunkMergedView(p: {
  chunks: DocumentChunkItem[];
  /** 服务端报的总数。带搜索词时它是"命中数"而不是文档切片总数，调用方须传 null。 */
  total: number | null;
  query?: string;
  selectedId: string;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
  onSelect: (id: string) => void;
  headerExtra?: ReactNode;
}) {
  const merged = useMemo(() => buildMergedDocument(p.chunks, p.total), [p.chunks, p.total]);
  return (
    <section className="parse-pane parse-list-pane" role="region" aria-label="整篇合并视图">
      <div className="parse-pane-heading">
        <div>
          <span>MERGED HEADS</span>
          <h2>整篇合并视图</h2>
        </div>
        <Tag variant="light">
          {merged.bodyCharCount} 字符 / {merged.segmentCount} 段
        </Tag>
        {p.headerExtra}
      </div>
      <Alert
        theme={merged.complete ? "info" : "warning"}
        title={merged.coverageLabel}
        message={`其中 ${merged.tombstoneCount} 处已停用、${merged.manuallyEditedCount} 处被人工改过。${
          merged.complete ? " 正文按服务端 ChunkHead 次序拼接。" : " 下面的正文不是全文。"
        }`}
      />
      {p.hasMore ? (
        <div className="parse-view-switch">
          <Button variant="outline" loading={p.loadingMore} onClick={p.onLoadMore}>
            继续载入
          </Button>
        </div>
      ) : null}
      {/* 可滚动区域必须能被键盘聚焦到，否则只用键盘的操作员根本滚不动这篇长文
          （CSS 给了 overflow:auto，鼠标能滚、Tab 进不来）。 */}
      <div className="parse-chunk-scroll" tabIndex={0} role="region" aria-label="合并正文，可用方向键滚动">
        {merged.segments.length ? (
          merged.segments.map((segment) =>
            segment.kind === "tombstone" ? (
              <div
                key={`${segment.chunkId}:tombstone`}
                aria-current={segment.chunkId === p.selectedId ? "true" : undefined}
                className={"parse-chunk-row parse-merged-gap" + (segment.chunkId === p.selectedId ? " is-selected" : "")}
              >
                <div className="parse-chunk-row-top">
                  <strong>#{segment.seq + 1}</strong>
                  <Tag theme="danger" variant="light">
                    已停用
                  </Tag>
                </div>
                <p>此处已被人工停用，正文留空；恢复它才会重新被检索到。</p>
                <div className="parse-chunk-row-facts">
                  <Button variant="text" aria-label={`定位到已停用的切片 ${segment.chunkId}`} onClick={() => p.onSelect(segment.chunkId)}>
                    定位到切片
                  </Button>
                </div>
              </div>
            ) : (
              <div
                key={`${segment.chunkId}:body`}
                aria-current={segment.chunkId === p.selectedId ? "true" : undefined}
                className={"parse-chunk-row" + (segment.chunkId === p.selectedId ? " is-selected" : "")}
              >
                <div className="parse-chunk-row-top">
                  <strong>#{segment.seq + 1}</strong>
                  {segment.editedManually ? (
                    <Tag theme="warning" variant="light">{`人工改过 R${segment.revision}`}</Tag>
                  ) : null}
                </div>
                <p>{segment.text}</p>
                <div className="parse-chunk-row-facts">
                  <Button variant="text" aria-label={`定位到切片 ${segment.chunkId}`} onClick={() => p.onSelect(segment.chunkId)}>
                    定位到切片
                  </Button>
                </div>
              </div>
            ),
          )
        ) : (
          <Alert
            theme="warning"
            title="这篇文档还没有可合并的切片"
            message="服务端没有返回任何非父块头部；先确认文档已完成解析，或清空搜索词后再看。"
          />
        )}
      </div>
      {p.query ? (
        <div className="parse-chunk-row-facts">{`当前处在服务端搜索「${p.query}」的命中结果里：列表给出的总数是命中数而不是全篇切片数，所以合并视图不宣称整篇。要看整篇请先清空搜索。`}</div>
      ) : null}
    </section>
  );
}
