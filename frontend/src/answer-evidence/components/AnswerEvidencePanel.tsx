import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button, Tag, Typography } from "../../ui/index";
import { ReloadOutlined } from "../../ui/icons";
import PageState from "../../components/PageState";
import {
  loadAnswerEvidence,
  resolveAnswerEvidenceDatasetId,
} from "../api/answerEvidenceApi";
import {
  citationStatusColor,
  citationStatusLabel,
  documentDeepLink,
  formatObservedAt,
  outcomeLabel,
  OUTCOME_COLORS,
  projectAnswerEvidenceError,
  routeLabel,
  sortEvidenceRefs,
  type AnswerEvidenceErrorView,
  type AnswerFact,
  type AnswerFactListResponse,
} from "../model/answerEvidenceModel";
import "../answer-evidence.css";

const { Text } = Typography;

export interface AnswerEvidencePanelProps {
  /** 当前选中的 run；有值时按 by-run 拉取，否则列最近 answer facts */
  runId?: string | null;
  datasetId?: string;
  tenantId?: string;
  actorToken?: string;
  limit?: number;
  className?: string;
}

function controlMinH(className?: string): string {
  return ["answer-evidence-control-min-h", className].filter(Boolean).join(" ");
}

function FactSwitcher({
  facts,
  selectedId,
  onSelect,
}: {
  facts: readonly AnswerFact[];
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  if (facts.length <= 1) return null;
  return (
    <div className="answer-evidence-fact-switcher" role="group" aria-label="选择答案事实">
      {facts.map((fact, index) => (
        <Button
          key={fact.id}
          size="small"
          type={fact.id === selectedId ? "primary" : "default"}
          ghost={fact.id !== selectedId}
          className={controlMinH()}
          aria-label={`选择答案事实 ${fact.id}`}
          aria-pressed={fact.id === selectedId}
          onClick={() => onSelect(fact.id)}
        >
          #{index + 1} {outcomeLabel(fact.outcome_code)}
        </Button>
      ))}
    </div>
  );
}

function EvidenceRefList({ fact, datasetId }: { fact: AnswerFact; datasetId: string }) {
  const refs = useMemo(() => sortEvidenceRefs(fact.evidence_refs), [fact.evidence_refs]);
  if (!refs.length) {
    return (
      <div className="answer-evidence-empty" aria-label="证据引用为空">
        本次答案事实没有登记证据引用。
      </div>
    );
  }
  return (
    <ul className="answer-evidence-refs" aria-label="证据引用列表">
      {refs.map((ref) => {
        const docLink = documentDeepLink(ref.document_id, fact.dataset_id || datasetId);
        return (
          <li
            key={ref.id}
            className="answer-evidence-ref"
            aria-label={`证据引用 ${ref.seq} ${ref.citation_status}`}
          >
            <span className="answer-evidence-ref__seq" aria-hidden="true">
              #{ref.seq}
            </span>
            <div className="answer-evidence-ref__body">
              <div className="answer-evidence-ref__tags">
                <Tag color={citationStatusColor(ref.citation_status)} variant="light">
                  {citationStatusLabel(ref.citation_status)}
                </Tag>
                <Text type="secondary" className="mono">
                  {ref.chunk_id || "chunk —"}
                </Text>
              </div>
              <div className="answer-evidence-ref__ids">
                <span>
                  chunk revision{" "}
                  <code>{ref.chunk_revision_id || "—"}</code>
                </span>
                <span>
                  document{" "}
                  {docLink ? (
                    <a
                      className="answer-evidence-doc-link"
                      href={docLink}
                      aria-label={`打开文档 ${ref.document_id}`}
                    >
                      <code>{ref.document_id}</code>
                    </a>
                  ) : (
                    <code>—</code>
                  )}
                </span>
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function FactSummary({ fact }: { fact: AnswerFact }) {
  return (
    <dl className="answer-evidence-summary" aria-label="答案事实摘要">
      <div>
        <dt>Outcome</dt>
        <dd>
          <Tag color={OUTCOME_COLORS[fact.outcome_code] ?? "default"} variant="light">
            {outcomeLabel(fact.outcome_code)}
          </Tag>
        </dd>
      </div>
      <div>
        <dt>Route</dt>
        <dd>{routeLabel(fact.route_code)}</dd>
      </div>
      <div>
        <dt>引用数</dt>
        <dd className="tabular-nums">{fact.citation_count}</dd>
      </div>
      <div>
        <dt>证据数</dt>
        <dd className="tabular-nums">{fact.evidence_count}</dd>
      </div>
      <div>
        <dt>观察时间</dt>
        <dd className="tabular-nums">{formatObservedAt(fact.observed_at)}</dd>
      </div>
      <div>
        <dt>Run</dt>
        <dd className="mono">{fact.run_id || "—"}</dd>
      </div>
    </dl>
  );
}

/**
 * 答案证据链面板：展示 Catalog 中的 answer fact + evidence refs。
 * 不展示原始问答正文；只展示 privacy-safe 摘要与 chunk/document 引用。
 */
export default function AnswerEvidencePanel({
  runId,
  datasetId,
  tenantId,
  actorToken,
  limit = 10,
  className,
}: AnswerEvidencePanelProps) {
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<AnswerEvidenceErrorView | null>(null);
  const [facts, setFacts] = useState<AnswerFact[]>([]);
  const [count, setCount] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const requestSeq = useRef(0);

  const scopeDatasetId = useMemo(
    () => datasetId?.trim() || resolveAnswerEvidenceDatasetId({ tenantId, actorToken, datasetId }),
    [actorToken, datasetId, tenantId],
  );

  const load = useCallback(async () => {
    const seq = ++requestSeq.current;
    setStatus("loading");
    setError(null);
    try {
      const response: AnswerFactListResponse = await loadAnswerEvidence(
        scopeDatasetId,
        { runId: runId ?? null, limit },
        { tenantId, actorToken, datasetId: scopeDatasetId },
      );
      if (seq !== requestSeq.current) return;
      setFacts(response.items);
      setCount(response.count);
      setSelectedId((prev) => {
        if (prev && response.items.some((item) => item.id === prev)) return prev;
        return response.items[0]?.id ?? null;
      });
      setStatus("ready");
    } catch (e) {
      if (seq !== requestSeq.current) return;
      setError(projectAnswerEvidenceError(e));
      setFacts([]);
      setCount(0);
      setSelectedId(null);
      setStatus("error");
    }
  }, [actorToken, limit, runId, scopeDatasetId, tenantId]);

  useEffect(() => {
    void load();
  }, [load]);

  const selectedFact = useMemo(
    () => facts.find((fact) => fact.id === selectedId) ?? facts[0] ?? null,
    [facts, selectedId],
  );

  return (
    <section
      className={["answer-evidence-panel", className].filter(Boolean).join(" ")}
      aria-label="答案证据链"
      data-testid="answer-evidence-panel"
    >
      <div className="answer-evidence-panel__toolbar">
        <div className="answer-evidence-panel__meta">
          <Text strong>证据链</Text>
          <Text type="secondary">
            {runId ? `run …${runId.slice(-8)}` : "最近答案事实"}
            {status === "ready" ? ` · ${count} 条` : ""}
          </Text>
        </div>
        <Button
          size="small"
          ghost
          className={controlMinH()}
          aria-label="刷新答案证据链"
          loading={status === "loading"}
          onClick={() => void load()}
        >
          <ReloadOutlined /> 刷新
        </Button>
      </div>

      <FactSwitcher
        facts={facts}
        selectedId={selectedFact?.id ?? ""}
        onSelect={setSelectedId}
      />

      {status === "loading" && !selectedFact ? (
        <PageState status="loading" title="正在加载答案证据链" compact />
      ) : null}

      {status === "error" && error ? (
        <PageState
          status="error"
          title={error.title}
          description={error.description}
          compact
          extra={
            error.canRetry ? (
              <Button
                className={controlMinH()}
                aria-label="重试加载答案证据链"
                onClick={() => void load()}
              >
                重试
              </Button>
            ) : null
          }
        />
      ) : null}

      {status === "ready" && !selectedFact ? (
        <div className="answer-evidence-empty" aria-label="暂无答案证据">
          {runId
            ? "该运行尚未在 Catalog 登记答案事实。查询完成后会自动写入。"
            : "当前知识库还没有答案事实。"}
        </div>
      ) : null}

      {selectedFact ? (
        <>
          <FactSummary fact={selectedFact} />
          {selectedFact.safe_query_preview ? (
            <p className="answer-evidence-preview" aria-label="脱敏查询预览">
              {selectedFact.safe_query_preview}
            </p>
          ) : null}
          <EvidenceRefList fact={selectedFact} datasetId={scopeDatasetId} />
        </>
      ) : null}
    </section>
  );
}
