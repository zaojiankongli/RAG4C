import { Fragment, useMemo } from "react";
import type { ReactNode } from "react";
import { Tag, Tooltip, Typography } from "../ui/index";
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ExclamationCircleOutlined,
  MinusCircleOutlined,
  WarningOutlined,
} from "../ui/icons";
import { parseTraces, stageColor } from "../strategy/traceParse";
import type { StrategyState } from "../strategy/traceParse";
import type { Citation, CitationStatus, EvidenceChunk } from "../types/rag";
import { chunkWorkbenchDeepLink } from "../run/appRoute";
import { FONT_SIZE } from "../theme/tokens";

const { Text } = Typography;

/** 引用状态 -> 它在三层验证里停在了哪一层 */
const LEVEL_BY_STATUS: Record<
  CitationStatus,
  { level: string; label: string; tone: "ok" | "warn" | "error" }
> = {
  ok: { level: "L1+L2+L3", label: "三层全部通过", tone: "ok" },
  stale: { level: "L2", label: "文本已变更（引用时的原文已被修改）", tone: "warn" },
  exists_only: { level: "L3", label: "出处存在，但结论未获原文充分支撑", tone: "warn" },
  unsupported: { level: "L1", label: "引用编号不存在于本次检索结果", tone: "error" },
};

const STATE_META: Record<StrategyState, { color: string; icon: ReactNode; text: string }> = {
  active: { color: "success", icon: <CheckCircleOutlined />, text: "已生效" },
  degraded: { color: "warning", icon: <WarningOutlined />, text: "已降级" },
  off: { color: "default", icon: <MinusCircleOutlined />, text: "已关闭" },
};

interface Props {
  traces: string[];
  citations: Citation[];
  /** 证据片段带 doc_id：用于把 stale 引用深链回被改的那个 ChunkHead */
  evidence?: EvidenceChunk[];
}

/**
 * 检索过程透明化面板。
 *
 * 把后端返回的 traces 从「一坨原始日志」变成三块可读信息：
 * 各阶段耗时占比、本次实际生效的策略、引用逐层核验结果。
 * 解析规则见 `strategy/traceParse.ts`。
 */
export default function RetrievalTrace({ traces, citations, evidence = [] }: Props) {
  const parsed = useMemo(() => parseTraces(traces), [traces]);
  const { stages, totalMs, strategies, notes, retried } = parsed;

  // 引用按「停在哪一层」归类
  const byStatus = useMemo(() => {
    const map = new Map<CitationStatus, number>();
    for (const c of citations) {
      map.set(c.status, (map.get(c.status) ?? 0) + 1);
    }
    return map;
  }, [citations]);

  // stale 引用 -> 只有证据自身同时声明 document/dataset 归属才生成深链；
  // 不能拿当前工作区或查询请求的 dataset 冒充跨库证据出处。
  const staleLinks = useMemo(() => {
    const scopeByChunk = new Map(
      evidence.map((item) => [
        item.chunk_id,
        {
          docId: item.doc_id.trim(),
          datasetId: item.dataset_id?.trim() ?? "",
        },
      ]),
    );
    return citations
      .filter((item) => item.status === "stale")
      .map((item) => {
        const scope = scopeByChunk.get(item.chunk_id);
        const docId = scope?.docId ?? "";
        const datasetId = scope?.datasetId ?? "";
        return {
          chunkId: item.chunk_id,
          url:
            docId && datasetId
              ? chunkWorkbenchDeepLink(docId, item.chunk_id, datasetId)
              : null,
        };
      });
  }, [citations, evidence]);

  const slowest = stages.reduce(
    (max, s) => (s.ms > (max?.ms ?? -1) ? s : max),
    undefined as (typeof stages)[number] | undefined,
  );

  return (
    <div className="rtrace">
      {/* ---------- 阶段耗时 ---------- */}
      {stages.length > 0 && (
        <section className="rtrace-section">
          <div className="rtrace-head">
            <span className="rtrace-title">各阶段耗时</span>
            <Text type="secondary" style={{ fontSize: FONT_SIZE.sm }}>
              合计 {(totalMs / 1000).toFixed(2)}s
              {slowest && totalMs > 0 && (
                <> · 最慢：{slowest.label}（{Math.round((slowest.ms / totalMs) * 100)}%）</>
              )}
              {retried && <> · 含二轮检索</>}
            </Text>
          </div>

          {totalMs > 0 && (
            <div className="rtrace-bar" role="img" aria-label="各阶段耗时占比">
              {stages
                .filter((s) => s.ms > 0)
                .map((s) => (
                  <Tooltip key={s.key} title={`${s.label} ${s.ms.toFixed(0)}ms`}>
                    <span
                      className="rtrace-bar-seg"
                      style={{
                        width: (s.ms / totalMs) * 100 + "%",
                        background: stageColor(s.key),
                      }}
                    />
                  </Tooltip>
                ))}
            </div>
          )}

          <div className="rtrace-stages">
            {stages.map((s) => (
              <div key={s.key} className="rtrace-stage">
                <span className="rtrace-dot" style={{ background: stageColor(s.key) }} />
                <span className="rtrace-stage-label">{s.label}</span>
                <span className="rtrace-stage-ms tabular-nums">
                  {s.ms >= 1 ? s.ms.toFixed(0) + "ms" : "<1ms"}
                </span>
                {s.children && s.children.length > 0 && (
                  <span className="rtrace-stage-children">
                    {s.children.map((c) => (
                      <span key={c.key}>
                        {c.label} {c.ms.toFixed(0)}ms
                      </span>
                    ))}
                  </span>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ---------- 本次生效的策略 ---------- */}
      {strategies.length > 0 && (
        <section className="rtrace-section">
          <div className="rtrace-head">
            <span className="rtrace-title">本次生效的策略</span>
            <Text type="secondary" style={{ fontSize: FONT_SIZE.sm }}>
              可在「设置 → 策略编排」调整
            </Text>
          </div>
          <div className="rtrace-badges">
            {strategies.map((s) => {
              const meta = STATE_META[s.state];
              return (
                <Tooltip key={s.key} title={s.reason || meta.text} trigger={["hover", "focus"]}>
                  {/* tabIndex：.rtrace-badge 靠 CSS 的 cursor:help 暗示可查看说明，
                      但 Tag 本身不可聚焦，不补这一项键盘用户就拿不到 reason */}
                  <Tag color={meta.color} className="rtrace-badge" tabIndex={0}>
                    {meta.icon} {s.label}
                    {s.state !== "active" && <span className="rtrace-badge-state">{meta.text}</span>}
                  </Tag>
                </Tooltip>
              );
            })}
          </div>
        </section>
      )}

      {/* ---------- 引用逐层核验 ---------- */}
      {citations.length > 0 && (
        <section className="rtrace-section">
          <div className="rtrace-head">
            <span className="rtrace-title">引用核验</span>
            <Text type="secondary" style={{ fontSize: FONT_SIZE.sm }}>
              L1 出处存在 → L2 原文未变 → L3 结论获支撑
            </Text>
          </div>
          <div className="rtrace-verify">
            {(Object.keys(LEVEL_BY_STATUS) as CitationStatus[])
              .filter((status) => (byStatus.get(status) ?? 0) > 0)
              .map((status) => {
                const info = LEVEL_BY_STATUS[status];
                const count = byStatus.get(status) ?? 0;
                return (
                  <Fragment key={status}>
                    <div className={"rtrace-verify-row is-" + info.tone}>
                    <span className="rtrace-verify-icon">
                      {info.tone === "ok" ? (
                        <CheckCircleOutlined />
                      ) : info.tone === "warn" ? (
                        <ExclamationCircleOutlined />
                      ) : (
                        <CloseCircleOutlined />
                      )}
                    </span>
                    <span className="rtrace-verify-level mono">{info.level}</span>
                    <span className="rtrace-verify-label">{info.label}</span>
                      <span className="rtrace-verify-count tabular-nums">{count} 条</span>
                    </div>
                    {status === "stale" && staleLinks.length > 0 && (
                    <div className="rtrace-verify-links">
                      {staleLinks.map((link, linkIndex) =>
                        link.url ? (
                          <a
                            key={`${link.chunkId}:${linkIndex}`}
                            className="rtrace-verify-link"
                            href={link.url}
                            aria-label={`在解析干预中查看已变更的切片 ${link.chunkId}`}
                          >
                            在解析干预中核对 <code>{link.chunkId}</code>
                          </a>
                        ) : (
                          <span key={`${link.chunkId}:${linkIndex}`} className="rtrace-verify-link is-unavailable">
                            切片 <code>{link.chunkId}</code> 缺少文档或知识库归属，无法直达
                          </span>
                        ),
                      )}
                    </div>
                  )}
                  </Fragment>
                );
              })}
          </div>
        </section>
      )}

      {/* ---------- 其余过程说明 ---------- */}
      {notes.length > 0 && (
        <section className="rtrace-section">
          <div className="rtrace-head">
            <span className="rtrace-title">过程说明</span>
          </div>
          <ul className="rtrace-notes">
            {notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
