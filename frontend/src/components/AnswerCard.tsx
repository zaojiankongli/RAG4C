import { memo, useCallback, useState } from "react";
import type { ReactNode } from "react";
import {
  Alert,
  App as UIApp,
  Button,
  Card,
  Collapse,
  Popover,
  Space,
  Tag,
  Tooltip,
  Typography,
} from "../ui/index";
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  CopyOutlined,
  ExclamationCircleOutlined,
  FileTextOutlined,
  LinkOutlined,
  ReloadOutlined,
  WarningOutlined,
} from "../ui/icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import RetrievalTrace from "./RetrievalTrace";
import type { Citation, CitationStatus, EvidenceChunk, QueryResult } from "../types/rag";
import { routeLabel } from "../strategy/routes";
import { FONT_SIZE } from "../theme/tokens";

const { Text, Paragraph } = Typography;

/** 引用状态 -> 徽标配置（信任徽标：绿/黄/紫/红） */
const STATUS_META: Record<
  CitationStatus,
  { color: string; badge: string; icon: ReactNode; desc: string }
> = {
  ok: {
    color: "green",
    badge: "内容一致",
    icon: <CheckCircleOutlined />,
    desc: "答案中的引用与资料内容核对一致，可信度高",
  },
  exists_only: {
    color: "orange",
    badge: "出处待核",
    icon: <WarningOutlined />,
    desc: "资料里能找到这段话，但内容核对未完全通过",
  },
  stale: {
    color: "purple",
    badge: "内容已变",
    icon: <ExclamationCircleOutlined />,
    desc: "资料在答案生成后发生过修改，请以最新资料为准",
  },
  unsupported: {
    color: "red",
    badge: "暂无出处",
    icon: <CloseCircleOutlined />,
    desc: "没找到支撑这句话的资料，可能不可靠",
  },
};

/** 引用状态小圆点颜色（语义色走 CSS 变量；stale 用品牌靛蓝近似「紫」） */
const STATUS_DOT_COLOR: Record<CitationStatus, string> = {
  ok: "var(--color-success)",
  exists_only: "var(--color-warning)",
  stale: "var(--color-primary)",
  unsupported: "var(--color-danger)",
};

interface Props {
  result: QueryResult;
  usingMock?: boolean;
  /** 命中服务端短期缓存（相同 query+acl 10 分钟内） */
  cached?: boolean;
  durationMs?: number;
  /** 回答生成时间戳（消息时间） */
  ts?: number;
  /** 重新生成回调（由问答页注入） */
  onRegenerate?: () => void;
}

/**
 * 回答卡片 —— 参考 Perplexity / RAGFlow 的引用 UX：
 * 1. 答案 Markdown 渲染（表格/列表/代码），行内 [N] 引用标记 hover 预览、点击联动；
 * 2. 来源证据卡片：信任徽标 + chunk 元信息 + 可展开证据片段（深链到段落）；
 * 3. 引用汇总徽标（全部支撑 / 部分支撑）+ 弃权提示；
 * 4. 复制答案 / 重新生成操作；引用条目与行内标记均键盘可达。
 */
function AnswerCard({
  result,
  usingMock = false,
  cached = false,
  durationMs,
  ts,
  onRegenerate,
}: Props) {
  const { message } = UIApp.useApp();
  const [activeIdx, setActiveIdx] = useState<number | null>(null);

  const findEvidence = (chunkId: string): EvidenceChunk | undefined =>
    result.evidence?.find((e) => e.chunk_id === chunkId);

  const scrollToCitation = (idx: number) => {
    document
      .getElementById("evidence-" + idx)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const toggleActive = (idx: number) => {
    setActiveIdx((prev) => {
      if (prev === idx) return null;
      scrollToCitation(idx);
      return idx;
    });
  };

  const copyAnswer = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(result.answer);
      message.success("回答已复制，可以直接粘贴使用");
    } catch {
      message.error("暂时无法复制，请手动选择回答后再试");
    }
  }, [message, result.answer]);

  /** 渲染 inline 引用标记：Popover 预览 + 点击联动来源卡片（键盘可达） */
  const renderInlineRef = (n: number, key: number) => {
    const idx = n - 1;
    const citation = result.citations[idx];
    if (!citation) {
      return (
        <Tag key={key} className="ref-tag">
          {n}
        </Tag>
      );
    }
    const meta = STATUS_META[citation.status] ?? STATUS_META.unsupported;
    const ev = findEvidence(citation.chunk_id);
    const content = (
      <div style={{ maxWidth: 320 }}>
        <Space size={6} style={{ marginBottom: 6 }}>
          <Tag color={meta.color} style={{ marginRight: 0 }}>
            {meta.icon} {meta.badge}
          </Tag>
          <Text code style={{ fontSize: FONT_SIZE.sm }}>
            {citation.chunk_id}
          </Text>
        </Space>
        <Paragraph type="secondary" style={{ fontSize: FONT_SIZE.sm, marginBottom: 6 }}>
          {citation.claim}
        </Paragraph>
        {ev ? (
          <Paragraph
            ellipsis={{ rows: 3, expandable: true, symbol: "展开" }}
            style={{ fontSize: FONT_SIZE.sm, marginBottom: 0, color: "var(--color-text)" }}
          >
            {ev.text}
          </Paragraph>
        ) : (
          <Text type="secondary" style={{ fontSize: FONT_SIZE.sm }}>
            {citation.reason}
          </Text>
        )}
      </div>
    );
    return (
      <Popover key={key} content={content} title={"来源 " + n} trigger={["hover", "focus"]}>
        <button
          type="button"
          className={"ref-tag" + (activeIdx === idx ? " is-active" : "")}
          aria-label={"引用 " + n + "：" + meta.badge}
          onClick={() => toggleActive(idx)}
        >
          {n}
        </button>
      </Popover>
    );
  };

  const renderAnswer = () => {
    const parts = result.answer.split(/(\[\d+\])/g);
    return (
      <div className="answer-text markdown-body">
        {parts.map((part, i) => {
          const m = /^\[(\d+)\]$/.exec(part);
          if (m) return renderInlineRef(Number(m[1]), i);
          return part.trim() ? (
            <ReactMarkdown key={i} remarkPlugins={[remarkGfm]}>
              {part}
            </ReactMarkdown>
          ) : null;
        })}
      </div>
    );
  };

  const evidenceCount = result.evidence?.length ?? 0;
  const okCount = result.citations.filter((c) => c.status === "ok").length;

  return (
    <Card
      className="answer-card"
      size="small"
      styles={{ body: { paddingTop: 14 } }}
      extra={
        <Space size={2}>
          <Tooltip title="复制答案">
            <Button
              type="text"
              size="small"
              className="answer-action-btn"
              aria-label="复制答案"
              icon={<CopyOutlined />}
              onClick={() => void copyAnswer()}
            />
          </Tooltip>
          {onRegenerate && (
            <Tooltip title="重新生成">
              <Button
                type="text"
                size="small"
                className="answer-action-btn"
                aria-label="重新生成"
                icon={<ReloadOutlined />}
                onClick={onRegenerate}
              />
            </Tooltip>
          )}
        </Space>
      }
    >
      <style>{`
        .answer-card .answer-meta .t-tag,
        .answer-card .answer-citation-header .t-tag {
          margin-inline-end: 0;
        }
        .answer-card .answer-action-btn.t-button--variant-text:not(:disabled):hover {
          color: var(--color-primary) !important;
          background: var(--color-primary-bg) !important;
        }
        .answer-card .citation-item:hover {
          border-color: var(--color-primary);
        }
      `}</style>
      {/* 元信息条：路由 / 来源数量指示 / 耗时 / 演示标记 / 时间 */}
      <Space className="answer-meta" size={8} wrap style={{ marginBottom: 8, fontSize: FONT_SIZE.sm }}>
        <Tooltip
          title={`系统选择了「${routeLabel(result.route)}」的方式来查找资料`}
          trigger={["hover", "focus"]}
        >
          {/* tabIndex：Tag 默认不可聚焦，不加的话这条说明键盘永远触发不到 */}
          <Tag color="blue" icon={<FileTextOutlined />} tabIndex={0} style={{ cursor: "help" }}>
            {routeLabel(result.route)}
          </Tag>
        </Tooltip>
        {evidenceCount > 0 && (
          <Tag color="cyan" icon={<LinkOutlined />}>
            参考 {evidenceCount} 段资料
          </Tag>
        )}
        {usingMock && <Tag color="orange">示例回答</Tag>}
        {cached && <Tag color="cyan">快速返回</Tag>}
        {durationMs !== undefined && (
          <Text type="secondary" className="tabular-nums" style={{ fontSize: FONT_SIZE.sm }}>
            耗时 {(durationMs / 1000).toFixed(2)}s
          </Text>
        )}
        <Text type="secondary" style={{ fontSize: FONT_SIZE.sm }}>
          {result.citations.length} 条来源 · 已完成资料核对
        </Text>
        {ts !== undefined && (
          <Text type="secondary" className="tabular-nums" style={{ fontSize: FONT_SIZE.sm }}>
            {new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(new Date(ts))}
          </Text>
        )}
      </Space>

      {/* 弃权：拒答是边界不是失败 */}
      {result.abstained ? (
        <Alert
          type="warning"
          showIcon
          message="资料不足，暂不回答"
          description={
            <>
              在知识库里没找到足够可靠的资料来回答这个问题。为了不编造答案，系统选择如实说「不知道」。
              <br />
              <Text type="secondary" style={{ fontSize: FONT_SIZE.sm }}>
                可以补充相关资料，或换一种更具体的问法。需要了解原因时，可展开下方的「查看处理过程」。
              </Text>
            </>
          }
          style={{ marginBottom: 12 }}
        />
      ) : (
        result.answer && renderAnswer()
      )}

      {/* 来源证据卡片 */}
      {result.citations.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <Space className="answer-citation-header" style={{ marginBottom: 8 }} wrap>
            <LinkOutlined style={{ color: "var(--color-primary)" }} />
            <Text strong style={{ fontSize: FONT_SIZE.md }}>
              答案依据
            </Text>
            <Text type="secondary" style={{ fontSize: FONT_SIZE.sm }}>
              点击任一资料可查看支持这条结论的原文内容。
            </Text>
            <Tag
              color={
                okCount === result.citations.length ? "success" : okCount > 0 ? "warning" : "error"
              }
            >
              {okCount}/{result.citations.length} 已核对
            </Tag>
          </Space>
          {result.citations.map((c: Citation, idx: number) => {
            const meta = STATUS_META[c.status] ?? STATUS_META.unsupported;
            const ev = findEvidence(c.chunk_id);
            return (
              <div
                id={"evidence-" + idx}
                key={c.chunk_id + "-" + idx}
                role="button"
                tabIndex={0}
                className={"citation-item" + (activeIdx === idx ? " active" : "")}
                aria-pressed={activeIdx === idx}
                aria-label={"引用 " + (idx + 1) + "：" + meta.badge + "，" + c.claim}
                onClick={() => setActiveIdx((prev) => (prev === idx ? null : idx))}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setActiveIdx((prev) => (prev === idx ? null : idx));
                  }
                }}
                style={{
                  width: "100%",
                  textAlign: "left",
                  fontSize: FONT_SIZE.md,
                  color: "var(--color-text)",
                }}
              >
                <div className="citation-claim">
                  <span className="citation-index" aria-hidden="true">
                    {idx + 1}
                  </span>
                  {c.claim}
                </div>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    gap: 8,
                    flexWrap: "wrap",
                  }}
                >
                  <Tooltip title={meta.desc}>
                    <span
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 6,
                        fontSize: FONT_SIZE.sm,
                        color: "var(--color-text-secondary)",
                      }}
                    >
                      <span
                        aria-hidden="true"
                        style={{
                          width: 8,
                          height: 8,
                          borderRadius: "50%",
                          background: STATUS_DOT_COLOR[c.status],
                          flexShrink: 0,
                        }}
                      />
                      {meta.badge}
                    </span>
                  </Tooltip>
                  <Text className="citation-meta">
                    {c.chunk_id}
                    {ev ? " · score " + ev.score.toFixed(3) + " · " + (ev.source ?? "") : ""}
                  </Text>
                </div>
                {ev && (
                  <Paragraph
                    ellipsis={{ rows: 2, expandable: true, symbol: "展开证据片段" }}
                    style={{
                      fontSize: FONT_SIZE.sm,
                      color: "var(--color-text-secondary)",
                      margin: "8px 0 0",
                      background: "var(--color-bg-sunken)",
                      borderRadius: 6,
                      padding: "6px 10px",
                    }}
                  >
                    {ev.text}
                  </Paragraph>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* 检索过程透明化：阶段耗时 / 生效策略 / 引用核验 */}
      {result.traces.length > 0 && (
        <Collapse
          ghost
          size="small"
          style={{ marginTop: 12 }}
          items={[
            {
              key: "traces",
              label: (
                <Text type="secondary" style={{ fontSize: FONT_SIZE.sm }}>
                  查看检索过程（耗时分布、生效策略与引用核验）
                </Text>
              ),
              children: (
                <RetrievalTrace traces={result.traces} citations={result.citations} />
              ),
            },
          ]}
        />
      )}
    </Card>
  );
}

export default memo(AnswerCard);
