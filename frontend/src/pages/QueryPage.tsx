/* eslint-disable @typescript-eslint/no-explicit-any -- compatibility callback types during TDesign migration */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Button,
  Card,
  Input,
  Popconfirm,
  Select,
  Space,
  Tag,
  Tooltip,
  Typography,
  message,
} from "../ui/index";
import {
  ArrowDownOutlined,
  ClearOutlined,
  CopyOutlined,
  FileSearchOutlined,
  LinkOutlined,
  MessageOutlined,
  SafetyCertificateOutlined,
  SendOutlined,
  StopOutlined,
} from "../ui/icons";
import AnswerCard from "../components/AnswerCard";
import PageTopbar from "../components/PageTopbar";
import PhaseStatus from "../components/PhaseStatus";
import StreamingCard from "../components/StreamingCard";
import { fetchAnswer, streamAnswer } from "../api/client";
import { DEMO_RESPONSES } from "../api/mock";
import { useConnection } from "../context/ConnectionContext";
import { useRunMonitor } from "../run/RunMonitorContext";
import type { QueryResponse } from "../types/rag";
import { FONT_SIZE } from "../theme/tokens";

const { TextArea } = Input;
const { Text } = Typography;
const CHAT_TIME_FORMATTER = new Intl.DateTimeFormat("zh-CN", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

/** 示例问题（空态引导，降低首次使用门槛） */
const SAMPLES = [
  "公司的报销流程是什么？",
  "员工年假制度是怎么规定的？",
  "入职需要准备哪些材料？",
  "去年 Q4 的毛利率是多少？",
];

/** 资料范围选项（对应后端 acl 过滤） */
const ACL_OPTIONS = [
  { value: "public", label: "公开资料" },
  { value: "hr", label: "人力资源" },
  { value: "finance", label: "财务数据" },
  { value: "product", label: "产品文档" },
];

/** 建议追问规则（基于关键词给 2 个后续问题，降低输入摩擦） */
const SUGGESTION_RULES: { match: RegExp; items: string[] }[] = [
  {
    match: /报销|流程|财务|发票/,
    items: ["公司的年假制度是怎么规定的？", "混合检索的 RRF 是如何融合两路结果的？"],
  },
  {
    match: /年假|请假|假期|考勤/,
    items: ["公司的报销流程是什么？", "三层引用验证（L1/L2/L3）分别做什么？"],
  },
  {
    match: /检索|RRF|混合|向量/,
    items: ["什么是双阈值弃权？系统什么时候会拒答？", "公司的报销流程是什么？"],
  },
  {
    match: /引用|验证|弃权|幻觉/,
    items: ["系统是怎么从资料里找到答案的？", "公司的年假制度是怎么规定的？"],
  },
];

function suggestFor(query: string): string[] {
  for (const rule of SUGGESTION_RULES) {
    if (rule.match.test(query)) return rule.items;
  }
  return SUGGESTION_RULES[0].items;
}

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  ts: number;
  response?: QueryResponse;
  /** 用户主动停止（无回答结果） */
  aborted?: boolean;
}

let msgSeq = 0;
const nextId = () => "msg-" + ++msgSeq;

interface Props {
  acl: string[];
  onAclChange: (v: string[]) => void;
}

/** 问答页：聊天式提问 + 流式回答 + 引用卡片 */
export default function QueryPage({ acl, onAclChange }: Props) {
  const { online } = useConnection();
  const {
    startRun,
    applyRunEvent,
    applyRunEventDesync,
    markRunEventInvalid,
    setRunPhase,
    completeRun,
    cancelRun,
  } = useRunMonitor();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  // 流式状态：当前阶段事件 + 已流出文本
  const [streamPhase, setStreamPhase] = useState<string | null>(null);
  const [streamText, setStreamText] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const requestEpochRef = useRef(0);

  // 新消息 / 加载态变化时滚到底部
  //
  // 空会话时**不要滚**：此时容器里只有欢迎页，它是从上往下读的内容，不是对话。
  // 窄屏（375px）上欢迎页比可视区高，一上来就滚到底会把顶部的图标和标题推出
  // 视口——截图里看到的正是一个被拦腰切掉的图标，像渲染坏了一样。
  // 会话一旦开始，滚到底才是对的：用户要看的是最新那条。
  useEffect(() => {
    if (messages.length === 0) return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, loading, streamText]);

  // 监听滚动位置：远离底部时显示「回到底部」
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
      setShowScrollBtn(distance > 400);
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  const scrollToBottom = () => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  };

  const ask = useCallback(
    async (rawQuery: string) => {
      const query = rawQuery.trim();
      if (!query || loading) return;
      setMessages((prev) => [
        ...prev,
        { id: nextId(), role: "user", content: query, ts: Date.now() },
      ]);
      setInput("");
      setLoading(true);
      setStreamText("");
      setStreamPhase("retrieving");
      const requestEpoch = ++requestEpochRef.current;
      const runId = startRun(query, online === false ? "demo" : "live_stream");
      const ctrl = new AbortController();
      abortRef.current = ctrl;

      const canCommit = () => requestEpochRef.current === requestEpoch && !ctrl.signal.aborted;

      const finish = (resp: QueryResponse, source: "live_stream" | "rest_fallback" | "demo") => {
        if (!canCommit()) return;
        completeRun(runId, resp, source);
        setMessages((prev) => [
          ...prev,
          {
            id: nextId(),
            role: "assistant",
            content: query,
            ts: Date.now(),
            response: resp,
          },
        ]);
      };

      const finishCancelled = () => {
        cancelRun(runId);
        if (requestEpochRef.current !== requestEpoch) return;
        setMessages((prev) => [
          ...prev,
          { id: nextId(), role: "assistant", content: query, ts: Date.now(), aborted: true },
        ]);
      };

      try {
        if (online === false) {
          const resp = DEMO_RESPONSES[query] ?? DEMO_RESPONSES["公司的报销流程是什么？"];
          const full = resp.result.answer || "（演示弃权）";
          setStreamPhase("generating");
          setRunPhase(runId, "generating");
          for (let i = 0; i < full.length; i += 4) {
            if (ctrl.signal.aborted) throw new DOMException("aborted", "AbortError");
            if (canCommit()) setStreamText((prev) => prev + full.slice(i, i + 4));
            await new Promise((resolve) => setTimeout(resolve, 18));
          }
          setStreamPhase("verifying");
          setRunPhase(runId, "verifying");
          await new Promise((resolve) => setTimeout(resolve, 300));
          if (ctrl.signal.aborted) throw new DOMException("aborted", "AbortError");
          finish(resp, "demo");
        } else {
          const resp = await streamAnswer(
            { query, acl: acl.length ? acl : null },
            {
              onRunEvent: (event: any) => {
                if (canCommit()) applyRunEvent(runId, event);
              },
              onRunEventDesync: (marker) => {
                if (canCommit()) applyRunEventDesync(runId, marker);
              },
              onRunEventInvalid: () => {
                if (canCommit()) markRunEventInvalid(runId);
              },
              onPhase: (phase, data) => {
                if (!canCommit()) return;
                setStreamPhase(phase);
                setRunPhase(runId, phase, {
                  route: typeof data.route === "string" ? data.route : undefined,
                  chunks: typeof data.chunks === "number" ? data.chunks : undefined,
                });
              },
              onToken: (token) => {
                if (canCommit()) setStreamText((prev) => prev + token);
              },
            },
            ctrl.signal,
          );
          finish(resp, resp.using_mock ? "demo" : "live_stream");
        }
      } catch (error) {
        if (ctrl.signal.aborted || (error instanceof DOMException && error.name === "AbortError")) {
          finishCancelled();
        } else {
          try {
            if (online === false) throw new Error("offline", { cause: error });
            const resp = await fetchAnswer({ query, acl: acl.length ? acl : null }, ctrl.signal);
            finish(resp, resp.using_mock ? "demo" : "rest_fallback");
          } catch {
            if (ctrl.signal.aborted) {
              finishCancelled();
              return;
            }
            if (!canCommit()) return;
            finish(DEMO_RESPONSES[query] ?? DEMO_RESPONSES["公司的报销流程是什么？"], "demo");
            message.warning("暂时无法连接后端服务，已展示演示回答。");
          }
        }
      } finally {
        if (abortRef.current === ctrl) abortRef.current = null;
        if (requestEpochRef.current === requestEpoch) {
          setLoading(false);
          setStreamPhase(null);
          setStreamText("");
        }
      }
    },
    [
      acl,
      applyRunEvent,
      applyRunEventDesync,
      cancelRun,
      markRunEventInvalid,
      completeRun,
      loading,
      online,
      setRunPhase,
      startRun,
    ],
  );

  const stop = useCallback(() => abortRef.current?.abort(), []);

  const clearChat = () => {
    requestEpochRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    setMessages([]);
    setLoading(false);
    setStreamPhase(null);
    setStreamText("");
  };

  const handleSend = () => void ask(input);

  const copyText = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      message.success("已复制");
    } catch {
      message.error("暂时无法复制，请手动选择内容后再试");
    }
  };

  return (
    <div className="page-slot">
      <PageTopbar
        icon={<MessageOutlined />}
        title="问答"
        subtitle="向知识库提问，答案会标注可核对的资料出处"
        extra={
          <>
            <Select
              size="small"
              mode="multiple"
              placeholder="全部资料"
              value={acl}
              onChange={onAclChange}
              style={{ minWidth: 170 }}
              options={ACL_OPTIONS}
              maxTagCount={1}
              maxTagPlaceholder={(omitted: any) => "+" + omitted.length}
              aria-label="限定查询的资料范围"
            />
            <Popconfirm
              title="清空这次对话？"
              description="页面上的提问和回答将不再显示"
              onConfirm={clearChat}
              okText="清空"
              cancelText="取消"
              disabled={messages.length === 0}
            >
              <Button size="small" icon={<ClearOutlined />} disabled={messages.length === 0}>
                清空对话
              </Button>
            </Popconfirm>
          </>
        }
      />

      <div className="page-shell query-page">
        <div
          className="page-shell-inner"
          style={{ display: "flex", flexDirection: "column", height: "100%", position: "relative" }}
        >
          <div className="chat-scroll" ref={scrollRef}>
            <div className="chat-inner">
              {messages.length === 0 && !loading && <Welcome onPick={(q) => void ask(q)} />}
              {messages.map((m, idx) =>
                m.role === "user" ? (
                  <div key={m.id} className="bubble-user-wrap">
                    <div className="bubble-user">{m.content}</div>
                    <div className="bubble-user-meta">
                      <span className="tabular-nums">
                        {CHAT_TIME_FORMATTER.format(new Date(m.ts))}
                      </span>
                      <Tooltip title="复制问题">
                        <Button
                          type="text"
                          size="small"
                          aria-label="复制问题"
                          icon={<CopyOutlined />}
                          onClick={() => void copyText(m.content)}
                        />
                      </Tooltip>
                    </div>
                  </div>
                ) : m.aborted ? (
                  <div key={m.id} className="bubble-assistant">
                    <Card size="small">
                      <Text type="secondary">已停止生成。你可以修改问题后重新发送。</Text>
                    </Card>
                  </div>
                ) : (
                  <div key={m.id} className="bubble-assistant">
                    <AnswerCard
                      result={m.response!.result}
                      usingMock={m.response!.using_mock}
                      durationMs={m.response!.duration_ms}
                      cached={m.response!.cached}
                      ts={m.ts}
                      onRegenerate={() => void ask(m.content)}
                    />
                    {/* 建议追问：仅最新一条回答且未弃权时展示 */}
                    {idx === messages.length - 1 && !m.response!.result.abstained && (
                      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
                        {suggestFor(m.content).map((s) => (
                          <Button
                            key={s}
                            size="small"
                            type="dashed"
                            aria-label={"追问：" + s}
                            onClick={() => void ask(s)}
                            className="suggestion-button"
                          >
                            {s}
                          </Button>
                        ))}
                      </div>
                    )}
                  </div>
                ),
              )}
              {loading && (
                <>
                  <StreamingCard text={streamText} phase={streamPhase} />
                  <PhaseStatus phase={streamPhase} />
                </>
              )}
            </div>
          </div>

          <div className="chat-input-bar">
            {/* 「回到底部」挂在输入区内部：它的定位锚点就是输入区上沿，
                输入框增高时按钮跟着上移，不会再被盖住（原来写死 bottom: 108px）。 */}
            {showScrollBtn && (
              <Button
                className="scroll-to-bottom"
                size="small"
                icon={<ArrowDownOutlined />}
                onClick={scrollToBottom}
              >
                回到底部
              </Button>
            )}
            <div className="input-inner">
              <div className="input-box">
                <TextArea
                  value={input}
                  onChange={(e: any) => setInput(e.target.value)}
                  onPressEnter={(e: any) => {
                    if (!e.shiftKey) {
                      e.preventDefault();
                      handleSend();
                    }
                  }}
                  placeholder="输入问题，例如：公司的报销流程是什么？"
                  autoSize={{ minRows: 1, maxRows: 6 }}
                  disabled={loading}
                  className="query-input"
                  aria-label="问题输入框"
                />
                <div className="input-row">
                  <Space size={6}>
                    <Text type="secondary" style={{ fontSize: FONT_SIZE.xs }}>
                      Enter 发送 · Shift+Enter 换行
                    </Text>
                    {acl.length > 0 && <Tag color="blue">仅查询：{acl.length} 类资料</Tag>}
                  </Space>
                  {loading ? (
                    <Button danger icon={<StopOutlined />} onClick={stop} className="send-btn">
                      停止
                    </Button>
                  ) : (
                    <Button
                      type="primary"
                      className="send-btn"
                      icon={<SendOutlined />}
                      onClick={handleSend}
                      disabled={!input.trim()}
                      aria-label="发送"
                    />
                  )}
                </div>
              </div>
              <div className="input-hint-bar">
                <span>回答基于已入库的资料生成；资料不足时系统会明确说明，不会编造。</span>
                <span className="tabular-nums">{input.length} 字</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/** 空态：以提问为中心，示例问题一键发起 */
function Welcome({ onPick }: { onPick: (q: string) => void }) {
  return (
    <div className="welcome">
      <div className="welcome-mark" aria-hidden="true">
        <MessageOutlined />
      </div>
      <h2>向知识库提问</h2>
      <div className="hint">
        系统会从已入库的资料中检索相关内容并生成回答，每条结论都可以回溯到原始出处。
      </div>

      <div className="welcome-cards">
        <div className="welcome-card">
          <LinkOutlined aria-hidden="true" />
          <div>
            <div className="welcome-card-title">答案带出处</div>
            <div className="welcome-card-desc">每条结论标注引用，可展开查看原文片段。</div>
          </div>
        </div>
        <div className="welcome-card">
          <SafetyCertificateOutlined aria-hidden="true" />
          <div>
            <div className="welcome-card-title">资料不足会拒答</div>
            <div className="welcome-card-desc">找不到可靠依据时如实说明，不补充臆测内容。</div>
          </div>
        </div>
        <div className="welcome-card">
          <FileSearchOutlined aria-hidden="true" />
          <div>
            <div className="welcome-card-title">过程可查</div>
            <div className="welcome-card-desc">检索与生成的每一步都可在「回答过程」回放。</div>
          </div>
        </div>
      </div>

      <div className="welcome-samples">
        {SAMPLES.map((q) => (
          <button key={q} type="button" className="sample-chip" onClick={() => onPick(q)}>
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}
