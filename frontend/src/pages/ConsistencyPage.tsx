import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Pagination,
  PrimaryTable,
  Tag,
  Typography,
  type PrimaryTableCol,
} from "tdesign-react";
import {
  CheckCircleIcon,
  CopyIcon,
  DataBaseIcon,
  RefreshIcon,
  RocketIcon,
  TimeIcon,
} from "tdesign-icons-react";
import {
  fetchConsistencyDeadLetters,
  fetchConsistencySummary,
  requeueConsistencyDeadLetter,
} from "../api/client";
import PageState from "../components/PageState";
import AuthRecoveryHint, { isAuthError } from "../components/AuthRecoveryHint";
import PageTopbar from "../components/PageTopbar";
import {
  projectConsistencySummary,
  type ConsistencySummaryResponse,
  type DeadLetterItem,
  type DeadLetterListResponse,
  type DeadLetterRequeueResponse,
} from "../consistency/consistencyModel";
import { useConnection } from "../context/ConnectionContext";
import { useKnowledgeWorkspace } from "../knowledge/KnowledgeWorkspaceContext";
import { readKnowledgeActorToken } from "../knowledge/workspaceScope";

const { Text } = Typography;
const DEAD_LETTER_PAGE_SIZE = 10;
const KEYBOARD_SCROLL_STEP = 96;

type ConsoleStatus = "loading" | "ready" | "error" | "offline";

interface ConsoleSnapshot {
  status: ConsoleStatus;
  summary: ConsistencySummaryResponse | null;
  deadLetters: DeadLetterListResponse;
  error: Error | null;
}

interface ActiveLoad {
  key: string;
  controller: AbortController;
  promise: Promise<void>;
}

interface RequeueOutcome {
  response: DeadLetterRequeueResponse;
  refreshError: string | null;
}

type DeadLetterRow = DeadLetterItem & {
  requeuePending: boolean;
  requeueOutcome: RequeueOutcome | null;
};

const INITIAL_SNAPSHOT: ConsoleSnapshot = {
  status: "loading",
  summary: null,
  deadLetters: { items: [], count: 0 },
  error: null,
};

function toError(value: unknown): Error {
  return value instanceof Error ? value : new Error(String(value));
}

function isAbortError(value: unknown): boolean {
  return (
    (typeof value === "object" && value !== null && "kind" in value && value.kind === "aborted") ||
    (value instanceof DOMException && value.name === "AbortError")
  );
}

function formatFailureTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function ReferenceValue({
  label,
  value,
  onCopy,
  copied,
}: {
  label: string;
  value: string;
  onCopy: (value: string) => void;
  copied: boolean;
}) {
  return (
    <div className="consistency-ref-row">
      <span className="consistency-ref-label">{label}</span>
      <code className="consistency-ref-text" title={value}>
        {value}
      </code>
      <Button
        className="consistency-copy-button"
        theme="default"
        variant="text"
        size="small"
        shape="square"
        icon={copied ? <CheckCircleIcon /> : <CopyIcon />}
        aria-label={`复制${label}引用`}
        title={`复制完整${label}引用`}
        onClick={() => onCopy(value)}
      />
    </div>
  );
}

export default function ConsistencyPage() {
  const { scope } = useKnowledgeWorkspace();
  const { datasetId, tenantId } = scope;
  const { online, refresh: refreshConnection } = useConnection();
  const [snapshot, setSnapshot] = useState<ConsoleSnapshot>(INITIAL_SNAPSHOT);
  const [retrying, setRetrying] = useState(false);
  const [requeueingRefs, setRequeueingRefs] = useState<Set<string>>(() => new Set());
  const [requeueOutcomes, setRequeueOutcomes] = useState<Map<string, RequeueOutcome>>(
    () => new Map(),
  );
  const [copiedRef, setCopiedRef] = useState<string | null>(null);
  const [copyError, setCopyError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<Error | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const mountedRef = useRef(false);
  const activeLoadRef = useRef<ActiveLoad | null>(null);
  const retryPromiseRef = useRef<Promise<void> | null>(null);
  const retryingRef = useRef(false);
  const requeueControllersRef = useRef<Map<string, AbortController>>(new Map());
  const copyTimerRef = useRef<number | null>(null);
  const scopeKey = `${tenantId}:${datasetId}`;

  const load = useCallback((force = false): Promise<void> => {
    const active = activeLoadRef.current;
    if (active?.key === scopeKey) {
      if (!force) return active.promise;
      return active.promise.then(() => (mountedRef.current ? load() : undefined));
    }
    if (active) active.controller.abort();

    const controller = new AbortController();
    setActionError(null);
    setSnapshot((current) => ({ ...current, status: "loading", error: null }));

    if (!readKnowledgeActorToken().trim()) {
      setSnapshot({
        status: "error",
        summary: null,
        deadLetters: { items: [], count: 0 },
        error: new Error("需要有效的 KnowledgeOps Actor Bearer 凭据"),
      });
      activeLoadRef.current = null;
      return Promise.resolve();
    }

    const promise = Promise.all([
      fetchConsistencySummary(datasetId, { tenantId, signal: controller.signal }),
      fetchConsistencyDeadLetters(datasetId, { tenantId, signal: controller.signal }),
    ])
      .then(([summary, deadLetters]) => {
        if (!mountedRef.current || controller.signal.aborted) return;
        setSnapshot({ status: "ready", summary, deadLetters, error: null });
      })
      .catch((error: unknown) => {
        if (!mountedRef.current || controller.signal.aborted || isAbortError(error)) return;
        setSnapshot({
          status: "error",
          summary: null,
          deadLetters: { items: [], count: 0 },
          error: toError(error),
        });
      })
      .finally(() => {
        if (activeLoadRef.current?.controller === controller) activeLoadRef.current = null;
      });

    activeLoadRef.current = { key: scopeKey, controller, promise };
    return promise;
  }, [datasetId, scopeKey, tenantId]);

  useEffect(() => {
    mountedRef.current = true;
    const requeueControllers = requeueControllersRef.current;
    return () => {
      mountedRef.current = false;
      activeLoadRef.current?.controller.abort();
      activeLoadRef.current = null;
      for (const controller of requeueControllers.values()) controller.abort();
      requeueControllers.clear();
      if (copyTimerRef.current !== null) window.clearTimeout(copyTimerRef.current);
    };
  }, []);

  useEffect(() => {
    activeLoadRef.current?.controller.abort();
    activeLoadRef.current = null;
    setCurrentPage(1);
    setRequeueOutcomes(new Map());
    if (retryingRef.current) return;
    if (online === null) {
      setSnapshot({ ...INITIAL_SNAPSHOT, status: "loading" });
      return;
    }
    if (online === false) {
      setSnapshot({ ...INITIAL_SNAPSHOT, status: "offline" });
      return;
    }
    void load();
  }, [load, online, scopeKey]);

  const retry = useCallback((): Promise<void> => {
    if (retryPromiseRef.current) return retryPromiseRef.current;
    retryingRef.current = true;
    setRetrying(true);
    const promise = (async () => {
      try {
        await refreshConnection();
        if (!mountedRef.current) return;
        await load(true);
      } finally {
        retryingRef.current = false;
        retryPromiseRef.current = null;
        if (mountedRef.current) setRetrying(false);
      }
    })();
    retryPromiseRef.current = promise;
    return promise;
  }, [load, refreshConnection]);

  const copyReference = useCallback(async (value: string) => {
    setCopyError(null);
    const writeText = navigator.clipboard?.writeText;
    if (typeof writeText !== "function") {
      setCopiedRef(null);
      setCopyError("当前环境不支持剪贴板复制，请使用引用文本的 title 查看完整值。");
      return;
    }
    try {
      await writeText.call(navigator.clipboard, value);
      if (!mountedRef.current) return;
      setCopiedRef(value);
      if (copyTimerRef.current !== null) window.clearTimeout(copyTimerRef.current);
      copyTimerRef.current = window.setTimeout(() => {
        if (mountedRef.current) setCopiedRef((current) => (current === value ? null : current));
      }, 1_500);
    } catch (error) {
      if (!mountedRef.current) return;
      setCopiedRef(null);
      setCopyError(`复制失败：${toError(error).message}`);
    }
  }, []);

  const refreshDeadLettersAfterRequeue = useCallback(
    async (deadLetterRef: string, controller: AbortController) => {
      try {
        const deadLetters = await fetchConsistencyDeadLetters(datasetId, {
          tenantId,
          signal: controller.signal,
        });
        if (!mountedRef.current || controller.signal.aborted) return;
        setSnapshot((current) => ({ ...current, deadLetters }));
      } catch (error) {
        if (!mountedRef.current || controller.signal.aborted || isAbortError(error)) return;
        setRequeueOutcomes((current) => {
          const outcome = current.get(deadLetterRef);
          if (!outcome) return current;
          const next = new Map(current);
          next.set(deadLetterRef, {
            ...outcome,
            refreshError: `列表刷新失败：${toError(error).message}`,
          });
          return next;
        });
      }
    },
    [datasetId, tenantId],
  );

  const requeue = useCallback(
    async (item: DeadLetterItem) => {
      const deadLetterRef = item.dead_letter_ref;
      if (requeueControllersRef.current.has(deadLetterRef)) return;

      const controller = new AbortController();
      requeueControllersRef.current.set(deadLetterRef, controller);
      setRequeueingRefs((current) => new Set(current).add(deadLetterRef));
      setActionError(null);
      try {
        const response = await requeueConsistencyDeadLetter(datasetId, deadLetterRef, {
          tenantId,
          operatorNote: "RAG4C Consistency Console replay",
          signal: controller.signal,
        });
        if (!mountedRef.current || controller.signal.aborted) return;
        setRequeueOutcomes((current) => {
          const next = new Map(current);
          next.set(deadLetterRef, { response, refreshError: null });
          return next;
        });
        setSnapshot((current) => ({
          ...current,
          deadLetters: {
            ...current.deadLetters,
            items: current.deadLetters.items.map((letter) =>
              letter.dead_letter_ref === deadLetterRef
                ? { ...letter, requeued_operation_ref: response.operation_ref }
                : letter,
            ),
          },
        }));
        await refreshDeadLettersAfterRequeue(deadLetterRef, controller);
      } catch (error) {
        if (!mountedRef.current || controller.signal.aborted || isAbortError(error)) return;
        setActionError(toError(error));
      } finally {
        requeueControllersRef.current.delete(deadLetterRef);
        if (mountedRef.current) {
          setRequeueingRefs((current) => {
            const next = new Set(current);
            next.delete(deadLetterRef);
            return next;
          });
        }
      }
    },
    [datasetId, refreshDeadLettersAfterRequeue, tenantId],
  );

  const columns = useMemo<PrimaryTableCol<DeadLetterRow>[]>(
    () => [
      {
        title: "引用",
        colKey: "dead_letter_ref",
        width: 380,
        cell: ({ row }) => (
          <div className="consistency-ref-stack">
            <ReferenceValue
              label="死信"
              value={row.dead_letter_ref}
              copied={copiedRef === row.dead_letter_ref}
              onCopy={(value) => void copyReference(value)}
            />
            <ReferenceValue
              label="操作"
              value={row.operation_ref}
              copied={copiedRef === row.operation_ref}
              onCopy={(value) => void copyReference(value)}
            />
            <ReferenceValue
              label="文档"
              value={row.document_ref}
              copied={copiedRef === row.document_ref}
              onCopy={(value) => void copyReference(value)}
            />
          </div>
        ),
      },
      {
        title: "操作 / 存储",
        colKey: "operation",
        width: 190,
        cell: ({ row }) => (
          <div className="consistency-operation-cell">
            <code>{row.operation}</code>
            <Tag size="small" variant="light-outline">
              {row.target_store}
            </Tag>
          </div>
        ),
      },
      {
        title: "重试",
        colKey: "retry_count",
        width: 82,
        align: "right",
        className: "tabular-nums",
      },
      {
        title: "失败时间",
        colKey: "failed_at",
        width: 190,
        cell: ({ row }) => (
          <span className="consistency-failure-time">
            <TimeIcon aria-hidden="true" />
            {formatFailureTime(row.failed_at)}
          </span>
        ),
      },
      {
        title: "处理",
        colKey: "action",
        width: 150,
        fixed: "right",
        cell: ({ row }) => {
          const outcome = row.requeueOutcome;
          if (outcome) {
            return (
              <div className="consistency-requeue-outcome" role="status">
                <Tag theme="success" variant="light" icon={<CheckCircleIcon />}>
                  {outcome.response.status === "enqueued" ? "已入队" : "此前已重放"}
                </Tag>
                {outcome.refreshError ? <small>{outcome.refreshError}</small> : null}
              </div>
            );
          }
          if (row.requeued_operation_ref) {
            return (
              <Tag theme="success" variant="light" icon={<CheckCircleIcon />}>
                已重放
              </Tag>
            );
          }
          return (
            <Button
              theme="primary"
              variant="outline"
              size="small"
              tag="button"
              icon={<RocketIcon />}
              disabled={row.requeuePending}
              aria-label={`重放死信 ${row.dead_letter_ref}`}
              onClick={() => void requeue(row)}
            >
              {row.requeuePending ? "重放中" : "重放"}
            </Button>
          );
        },
      },
    ],
    [copiedRef, copyReference, requeue],
  );

  const projection = snapshot.summary ? projectConsistencySummary(snapshot.summary) : null;
  const loading = snapshot.status === "loading";
  const pageCount = Math.max(1, Math.ceil(snapshot.deadLetters.items.length / DEAD_LETTER_PAGE_SIZE));
  const safeCurrentPage = Math.min(currentPage, pageCount);
  const pagedDeadLetters = useMemo<DeadLetterRow[]>(
    () =>
      snapshot.deadLetters.items
        .slice(
          (safeCurrentPage - 1) * DEAD_LETTER_PAGE_SIZE,
          safeCurrentPage * DEAD_LETTER_PAGE_SIZE,
        )
        .map((letter) => ({
          ...letter,
          requeuePending: requeueingRefs.has(letter.dead_letter_ref),
          requeueOutcome: requeueOutcomes.get(letter.dead_letter_ref) ?? null,
        })),
    [requeueOutcomes, requeueingRefs, safeCurrentPage, snapshot.deadLetters.items],
  );

  useEffect(() => {
    if (currentPage !== safeCurrentPage) setCurrentPage(safeCurrentPage);
  }, [currentPage, safeCurrentPage]);

  const handleTableKeyDown = useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    event.currentTarget.scrollLeft +=
      event.key === "ArrowRight" ? KEYBOARD_SCROLL_STEP : -KEYBOARD_SCROLL_STEP;
  }, []);

  const retryButton = (
    <Button
      theme="primary"
      icon={<RefreshIcon />}
      tag="button"
      disabled={retrying}
      aria-label="重新连接并重试"
      onClick={() => void retry()}
    >
      {retrying ? "正在重新连接" : "重新连接并重试"}
    </Button>
  );

  return (
    <div className="page-slot consistency-page">
      <PageTopbar
        icon={<DataBaseIcon />}
        title="一致性控制台"
        subtitle="检查目录期望、索引投影漂移与可安全重放的死信"
        extra={
          <Button
            theme="primary"
            icon={<RefreshIcon />}
            tag="button"
            disabled={loading || retrying}
            aria-label="刷新"
            onClick={() => void retry()}
          >
            刷新
          </Button>
        }
      />
      <div className="page-shell">
        <div className="page-shell-inner consistency-shell">
          {snapshot.status === "loading" ? (
            <PageState
              status="loading"
              title="正在读取一致性报告"
              description="目录摘要与死信记录并行加载，不会使用演示数据填充。"
            />
          ) : snapshot.status === "offline" ? (
            <PageState
              status="error"
              title="后端服务未连接"
              description="一致性控制台只展示生产控制面返回的数据；离线时不会生成演示摘要或死信。"
              extra={retryButton}
            />
          ) : snapshot.status === "error" ? (
            <PageState
              status="error"
              title={
                isAuthError(snapshot.error) ||
                !readKnowledgeActorToken().trim()
                  ? "一致性报告身份校验失败"
                  : "一致性报告加载失败"
              }
              description={
                isAuthError(snapshot.error) || !readKnowledgeActorToken().trim()
                  ? "需要有效的 KnowledgeOps Actor Bearer 凭据。请完成身份接入后重试；本页不会生成演示数据。"
                  : (snapshot.error?.message ?? "暂时无法读取一致性控制面。")
              }
              extra={
                isAuthError(snapshot.error) || !readKnowledgeActorToken().trim() ? (
                  <AuthRecoveryHint
                    compact
                    onRetry={() => void retry()}
                    title="无法加载一致性报告"
                  />
                ) : (
                  retryButton
                )
              }
            />
          ) : projection && snapshot.summary ? (
            <>
              <Alert
                className="consistency-authority-alert"
                theme="warning"
                title="最佳努力目录报告"
                message={
                  <span>
                    本摘要为 best_effort={projection.bestEffort ? "true" : "未返回"}，只覆盖目录快照
                    （catalog-only），明确为 complete=false、confirmable=false。Milvus 投影尚无代次权威，
                    因此不生成修复计划，也不提供修复操作。
                  </span>
                }
                {...({ role: "alert", "aria-label": "最佳努力目录报告" } as Record<
                  string,
                  unknown
                >)}
              />

              <section className="consistency-overview" aria-labelledby="consistency-lifeline-title">
                <div className="consistency-section-heading">
                  <div>
                    <Text className="consistency-eyebrow">KNOWLEDGE LIFELINE</Text>
                    <h2 id="consistency-lifeline-title">目录期望到投影漂移</h2>
                  </div>
                  <Tag theme={snapshot.summary.has_drift ? "warning" : "success"} variant="light">
                    {snapshot.summary.has_drift ? "检测到漂移" : "未检测到漂移"}
                  </Tag>
                </div>
                <ol className="consistency-lifeline" aria-label="知识一致性生命线">
                  <li>
                    <span>期望</span>
                    <strong>{projection.desired.toLocaleString()}</strong>
                    <small>权威头记录</small>
                  </li>
                  <li>
                    <span>投影</span>
                    <strong>{projection.projection.toLocaleString()}</strong>
                    <small>已观察索引片段</small>
                  </li>
                  <li className={projection.drift ? "is-drift" : "is-clear"}>
                    <span>漂移</span>
                    <strong>{projection.drift.toLocaleString()}</strong>
                    <small>分类问题合计</small>
                  </li>
                </ol>
              </section>

              <section className="consistency-qa-authority" aria-labelledby="consistency-qa-title">
                <div className="consistency-section-heading">
                  <div>
                    <Text className="consistency-eyebrow">QA AUTHORITY</Text>
                    <h2 id="consistency-qa-title">QA 检索权威（Catalog）</h2>
                  </div>
                  <div className="consistency-qa-heading-actions">
                    <a
                      className="consistency-qa-governance-link"
                      href="#/governance"
                      aria-label="打开 QA 治理"
                    >
                      打开 QA 治理
                    </a>
                    <Tag variant="light">
                      {projection.qaAuthority
                        ? `${projection.qaAuthority.effective_retrieval.toLocaleString()} 可检索`
                        : "未返回"}
                    </Tag>
                  </div>
                </div>
                {projection.qaAuthority ? (
                  <>
                    <ol className="consistency-lifeline" aria-label="QA 权威生命线">
                      <li>
                        <span>可检索</span>
                        <strong>{projection.qaAuthority.effective_retrieval.toLocaleString()}</strong>
                        <small>approved+active+启用</small>
                      </li>
                      <li>
                        <span>待审核</span>
                        <strong>{projection.qaAuthority.pending_review.toLocaleString()}</strong>
                        <small>pending</small>
                      </li>
                      <li
                        className={
                          projection.qaAuthority.expired || projection.qaAuthority.retrieval_disabled
                            ? "is-drift"
                            : "is-clear"
                        }
                      >
                        <span>过审未启用</span>
                        <strong>{projection.qaAuthority.retrieval_disabled.toLocaleString()}</strong>
                        <small>approved 但禁检索</small>
                      </li>
                      <li
                        className={
                          projection.qaAuthority.expired || projection.qaAuthority.rejected
                            ? "is-drift"
                            : "is-clear"
                        }
                      >
                        <span>失效/驳回</span>
                        <strong>
                          {(
                            projection.qaAuthority.expired + projection.qaAuthority.rejected
                          ).toLocaleString()}
                        </strong>
                        <small>expired + rejected</small>
                      </li>
                    </ol>
                    <p className="consistency-qa-note" aria-label="QA 权威说明">
                      {projection.qaAuthority.note ||
                        "QA 权威来自 MySQL Catalog；不投影 Milvus，不作为可确认修复对象。"}
                    </p>
                  </>
                ) : (
                  <div className="consistency-empty" role="status">
                    QA 权威计数未能从 Catalog 读取（未用 0 伪装生产事实）。文档投影摘要不受影响。
                  </div>
                )}
              </section>

              <section className="consistency-category-grid" aria-label="漂移分类">
                {projection.categories.map((category) => (
                  <Card key={category.key} className={`consistency-category-card is-${category.key}`}>
                    <span>{category.label}</span>
                    <strong>{category.count.toLocaleString()}</strong>
                    <small>
                      {category.key === "missing"
                        ? "目录存在，投影缺失"
                        : category.key === "stale"
                          ? "投影版本落后"
                          : category.key === "orphaned"
                            ? "投影无目录归属"
                            : "文档暂不可安全处理"}
                    </small>
                  </Card>
                ))}
              </section>

              <section className="consistency-dead-letter-section" aria-labelledby="dead-letter-title">
                <div className="consistency-section-heading">
                  <div>
                    <Text className="consistency-eyebrow">SAFE REPLAY QUEUE</Text>
                    <h2 id="dead-letter-title">死信记录</h2>
                  </div>
                  <Tag variant="light">{snapshot.deadLetters.count.toLocaleString()} 条</Tag>
                </div>
                {copyError ? (
                  <Alert
                    className="consistency-action-error"
                    theme="error"
                    title="复制失败"
                    message={copyError}
                    {...({ role: "alert" } as Record<string, unknown>)}
                  />
                ) : null}
                {actionError ? (
                  <Alert
                    className="consistency-action-error"
                    theme="error"
                    title="死信操作失败"
                    message={actionError.message}
                    {...({ role: "alert" } as Record<string, unknown>)}
                  />
                ) : null}
                {snapshot.deadLetters.items.length ? (
                  <>
                    <div
                      className="consistency-table-scroll"
                      role="region"
                      aria-label="死信记录，可横向滚动"
                      tabIndex={0}
                      onKeyDown={handleTableKeyDown}
                    >
                      <PrimaryTable
                        className="consistency-dead-letter-table"
                        data={pagedDeadLetters}
                        columns={columns}
                        rowKey="dead_letter_ref"
                        size="small"
                        tableLayout="fixed"
                        hover
                      />
                    </div>
                    {snapshot.deadLetters.items.length > DEAD_LETTER_PAGE_SIZE ? (
                      <Pagination
                        className="consistency-pagination"
                        current={safeCurrentPage}
                        pageSize={DEAD_LETTER_PAGE_SIZE}
                        total={snapshot.deadLetters.items.length}
                        showPageSize={false}
                        showJumper={false}
                        size="small"
                        onCurrentChange={setCurrentPage}
                      />
                    ) : null}
                  </>
                ) : (
                  <div className="consistency-empty" role="status">
                    <CheckCircleIcon aria-hidden="true" />
                    <div>
                      <strong>当前没有死信记录</strong>
                      <span>没有需要人工重放的投影操作。</span>
                    </div>
                  </div>
                )}
              </section>
              <span className="consistency-copy-status" aria-live="polite">
                {copiedRef ? "完整引用已复制" : ""}
              </span>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
