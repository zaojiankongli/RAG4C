import {
  Button,
  Card,
  Progress,
  Tag,
  Typography,
} from "tdesign-react";
import {
  DataBaseIcon,
  FileSearchIcon,
  FlashlightIcon,
  FolderOpenIcon,
  RefreshIcon,
  TagIcon,
} from "tdesign-icons-react";
import PageTopbar from "../components/PageTopbar";
import StatCard from "../components/StatCard";
import KnowledgeWorkspacePageState from "../knowledge/KnowledgeWorkspacePageState";
import { useKnowledgeDocuments } from "../knowledge/useKnowledgeDocuments";
import { projectKnowledgeOverview } from "../knowledge/knowledgeModel";
import { navigationIntent, type PageKey } from "../run/appRoute";
import { commitNavigationIntent } from "../run/navigationAdapter";
import type { DocumentCatalogSummaryResponse, DocumentItem } from "../types/rag";
import "./knowledge-overview.css";

const STATUS_PRESENTATION: Record<
  string,
  {
    label: string;
    theme: "success" | "danger" | "primary" | "warning" | "default";
  }
> = {
  completed: { label: "已完成", theme: "success" },
  error: { label: "处理失败", theme: "danger" },
  waiting: { label: "等待入库", theme: "warning" },
  queued: { label: "等待处理", theme: "warning" },
  parsing: { label: "解析中", theme: "primary" },
  splitting: { label: "切片中", theme: "primary" },
  indexing: { label: "索引中", theme: "primary" },
};

type LifelineState = "complete" | "active" | "pending" | "warning" | "error";

interface LifelineStage {
  label: string;
  state: LifelineState;
  detail: string;
}

const LIFELINE_STATE_LABEL: Record<LifelineState, string> = {
  complete: "已有完成证据",
  active: "正在处理",
  pending: "等待证据",
  warning: "证据不完整",
  error: "存在失败记录",
};

const KNOWLEDGE_LIFELINE = ["来源", "解析", "切片", "索引", "召回", "引用", "回答"];
const DOWNSTREAM_RUNTIME_STAGES = new Set(["召回", "引用", "回答"]);

interface AttentionItem {
  key: string;
  label: string;
  detail: string;
  theme: "danger" | "warning" | "primary";
  target: PageKey;
}

function firstDocumentName(documents: DocumentItem[], statuses: string[], fallback: string) {
  return documents.find((document) => statuses.includes(document.status))?.name ?? fallback;
}

function hasSourceEvidence(document: DocumentItem) {
  return [
    document.source_id,
    document.source_type,
    document.source_uri,
    document.external_id,
  ].some((value) => typeof value === "string" && value.trim().length > 0);
}

function hasParserEvidence(document: DocumentItem) {
  return Object.keys(document.parser_meta ?? {}).length > 0;
}

function aggregateLifelineState(states: LifelineState[]): LifelineState {
  if (states.length === 0) return "pending";
  if (states.includes("error")) return "error";
  if (states.includes("active")) return "active";
  if (states.includes("warning")) return "warning";
  if (states.includes("complete") && states.includes("pending")) return "warning";
  if (states.every((state) => state === "complete")) return "complete";
  return "pending";
}

function documentStageState(document: DocumentItem, stage: string): LifelineState {
  const status = document.status;
  if (stage === "来源") {
    return hasSourceEvidence(document) ? "complete" : "warning";
  }
  if (stage === "解析") {
    if (status === "error") return "error";
    if (status === "parsing") return "active";
    if (["splitting", "indexing", "completed"].includes(status)) {
      return hasParserEvidence(document) ? "complete" : "warning";
    }
    return "pending";
  }
  if (stage === "切片") {
    if (status === "splitting") return "active";
    if (["indexing", "completed"].includes(status)) return "complete";
    if (status === "error") return "warning";
    return "pending";
  }
  if (stage === "索引") {
    if (status === "indexing") return "active";
    if (status === "completed") return document.chunk_count > 0 ? "complete" : "warning";
    if (status === "error") return "warning";
    return "pending";
  }
  return "pending";
}

function projectKnowledgeLifeline(documents: DocumentItem[]): LifelineStage[] {
  return KNOWLEDGE_LIFELINE.map((label) => {
    if (documents.length === 0 || DOWNSTREAM_RUNTIME_STAGES.has(label)) {
      return {
        label,
        state: "pending",
        detail: documents.length === 0
          ? "暂无文档处理记录"
          : "当前文档目录未提供该阶段的运行指标",
      };
    }
    const state = aggregateLifelineState(
      documents.map((document) => documentStageState(document, label)),
    );
    const detail = state === "complete"
      ? "现有目录记录包含该阶段的完成证据"
      : state === "active"
        ? "现有目录记录包含正在处理的文档"
        : state === "error"
          ? "现有目录记录包含处理失败的文档"
          : state === "warning"
            ? "现有目录记录的证据不完整或部分文档受阻"
            : "现有目录记录尚未到达该阶段";
    return { label, state, detail };
  });
}

type SummaryProjection = DocumentCatalogSummaryResponse["summary"];
type FacetsProjection = DocumentCatalogSummaryResponse["facets"];

interface OverviewProjection {
  total: number;
  completed: number;
  processing: number;
  failed: number;
  chunks: number;
  categories: number;
  tags: number;
  parserCoverage: number;
  recent: DocumentItem[];
}

function projectSummaryOverview(
  summary: SummaryProjection,
  facets: FacetsProjection | null,
  recent: DocumentItem[],
): OverviewProjection {
  return {
    total: Number(summary.total || 0),
    completed: Number(summary.completed || 0),
    processing: Number(summary.processing || 0),
    failed: Number(summary.failed || 0),
    chunks: Number(summary.chunks || 0),
    categories: facets?.folders.length ?? 0,
    tags: facets?.tags.length ?? 0,
    parserCoverage: Number(summary.parser_coverage || 0),
    recent: recent.slice(0, 6),
  };
}

function summaryStage(
  label: string,
  summary: SummaryProjection,
  facets: FacetsProjection | null,
): LifelineStage {
  const total = Number(summary.total || 0);
  const statuses = facets?.statuses;
  const completed = Number(summary.completed || 0);
  const chunks = Number(summary.chunks || 0);
  const parserObserved = Number(summary.parser_observed || 0);

  if (total === 0) {
    return { label, state: "pending", detail: "摘要暂无文档处理记录" };
  }
  if (label === "来源") {
    return { label, state: "pending", detail: "当前摘要未提供全量来源观测" };
  }
  if (label === "解析") {
    if (Number(summary.failed || 0) > 0 || Number(statuses?.error || 0) > 0) {
      return { label, state: "error", detail: "摘要包含处理失败记录" };
    }
    if (Number(statuses?.parsing || 0) > 0) {
      return { label, state: "active", detail: "摘要包含正在解析的文档" };
    }
    if (parserObserved >= total && Number(summary.parser_coverage || 0) >= 100) {
      return { label, state: "complete", detail: "摘要确认全部文档具备解析观测" };
    }
    if (parserObserved > 0) {
      return { label, state: "warning", detail: "摘要确认部分文档具备解析观测" };
    }
    return { label, state: "pending", detail: "摘要未提供解析观测" };
  }
  if (label === "切片") {
    if (Number(statuses?.splitting || 0) > 0) {
      return { label, state: "active", detail: "摘要包含正在切片的文档" };
    }
    if (completed === total && chunks > 0) {
      return { label, state: "complete", detail: "摘要确认全部文档已完成切片" };
    }
    if (completed > 0 || Number(statuses?.indexing || 0) > 0) {
      return { label, state: "warning", detail: "摘要只确认部分文档已完成切片" };
    }
    return { label, state: "pending", detail: "摘要未确认切片完成" };
  }
  if (label === "索引") {
    if (Number(statuses?.indexing || 0) > 0) {
      return { label, state: "active", detail: "摘要包含正在索引的文档" };
    }
    if (completed === total && chunks > 0) {
      return { label, state: "complete", detail: "摘要确认全部文档具备片段记录" };
    }
    if (chunks > 0 && completed > 0) {
      return { label, state: "warning", detail: "摘要只确认部分文档具备索引片段" };
    }
    return { label, state: "pending", detail: "摘要未确认索引完成" };
  }
  return { label, state: "pending", detail: "摘要未提供该阶段的运行指标" };
}

function projectSummaryLifeline(
  summary: SummaryProjection,
  facets: FacetsProjection | null,
): LifelineStage[] {
  return KNOWLEDGE_LIFELINE.map((label) => summaryStage(label, summary, facets));
}

function navigateTo(target: PageKey) {
  const intent = navigationIntent(window.location, target);
  commitNavigationIntent(intent, { dispatchPopStateAfterHash: true });
}

export default function KnowledgeOverviewPage() {
  const workspace = useKnowledgeDocuments();
  const {
    documents,
    status,
    error,
    loading,
    usingMock,
    reload,
    dataMode = "legacy-local",
    summary = null,
    facets = null,
    recent = [],
    tagAuthority = "unknown",
    tagFacetsComplete = null,
    tagFacetsTruncated = false,
  } = workspace;
  const summaryMode = dataMode === "summary-api" && summary !== null;
  const overview = summaryMode
    ? projectSummaryOverview(summary, facets, recent)
    : projectKnowledgeOverview(documents);
  const recentAssets = summaryMode ? recent : overview.recent;
  const showPageState = status === "loading" || status === "error";
  const completionRate = overview.total
    ? Math.round((overview.completed / overview.total) * 100)
    : 0;
  const sourceKeys = summaryMode
    ? []
    : documents.flatMap((document) => {
        const sourceKey = document.source_id
          || document.source_uri
          || document.external_id
          || document.source_type;
        return typeof sourceKey === "string" && sourceKey.trim() ? [sourceKey.trim()] : [];
      });
  const sourceCount = new Set(sourceKeys).size;
  const lifeline = summaryMode
    ? projectSummaryLifeline(summary, facets)
    : projectKnowledgeLifeline(documents);
  const tagQualityMessage = summaryMode
    ? tagFacetsComplete === false || tagFacetsTruncated
      ? "标签统计为有限扫描结果"
      : "标签统计已完整覆盖"
    : null;

  const attentionItems: AttentionItem[] = [];
  if (overview.failed > 0) {
    attentionItems.push({
      key: "failed",
      label: `${overview.failed} 个解析失败`,
      detail: summaryMode
        ? "摘要包含失败记录，打开文档中心查看详情"
        : firstDocumentName(documents, ["error"], "查看失败文档"),
      theme: "danger",
      target: "documents",
    });
  }
  if (overview.processing > 0) {
    attentionItems.push({
      key: "processing",
      label: `${overview.processing} 个正在处理`,
      detail: summaryMode
        ? "摘要包含处理中的文档，打开文档中心查看阶段"
        : firstDocumentName(
            documents,
            ["waiting", "queued", "parsing", "splitting", "indexing"],
            "查看处理队列",
          ),
      theme: "primary",
      target: "documents",
    });
  }
  if (overview.parserCoverage < 100 && overview.total > 0) {
    attentionItems.push({
      key: "coverage",
      label: `${100 - overview.parserCoverage}% 尚无解析观测`,
      detail: summaryMode
        ? "摘要报告解析覆盖未达到 100%"
        : "补齐处理记录后可进行质量追溯",
      theme: "warning",
      target: "consistency",
    });
  }

  return (
    <div className="page-slot knowledge-overview-page">
      <PageTopbar
        icon={<DataBaseIcon />}
        title="知识概览"
        subtitle="基于文档目录证据的资产、处理状态与运营风险总览"
        extra={
          <Button
            size="small"
            variant="outline"
            icon={<RefreshIcon />}
            loading={loading}
            onClick={() => void reload()}
          >
            刷新
          </Button>
        }
      />
      <div className="page-shell">
        <div className="page-shell-inner">
          {showPageState ? (
            <KnowledgeWorkspacePageState status={status} error={error} onRetry={reload} />
          ) : (
            <div className="knowledge-command-center">
              <section
                className="knowledge-command-header"
                aria-labelledby="knowledge-command-title"
              >
                <div className="knowledge-command-copy">
                  <div className="knowledge-command-kicker">
                    <span>KNOWLEDGE OPERATIONS</span>
                    <Tag
                      theme={usingMock ? "warning" : "primary"}
                      variant="light-outline"
                      shape="round"
                    >
                      {usingMock ? "演示数据" : "权威目录"}
                    </Tag>
                  </div>
                  <h2 id="knowledge-command-title">企业知识资产运营指挥台</h2>
                  <p>
                    {usingMock
                      ? "当前为演示投影，不代表企业数据库真账"
                      : summaryMode
                        ? "当前视图来自企业目录摘要，指标由服务器聚合；最近资产为有限窗口"
                        : overview.total === 0
                          ? "当前目录暂无文档记录，未推断来源或处理状态"
                          : sourceCount > 0
                            ? `当前视图来自企业目录记录，识别到 ${sourceCount} 个来源标识`
                            : "当前视图来自企业目录记录，但文档尚未提供来源标识"}
                  </p>
                  {!usingMock && summaryMode ? (
                    <div className="knowledge-data-quality" role="status">
                      <span>企业目录摘要 · 服务器聚合指标</span>
                      <span>{tagQualityMessage}</span>
                      {tagAuthority !== "unknown" ? (
                        <span>标签来源：{tagAuthority === "legacy_projection" ? "兼容投影" : "治理目录"}</span>
                      ) : null}
                    </div>
                  ) : null}
                </div>
                <div className="knowledge-command-actions" aria-label="主要操作">
                  <Button theme="primary" onClick={() => navigateTo("documents")}>
                    管理知识资产
                  </Button>
                  <Button variant="outline" onClick={() => navigateTo("retrieval-lab")}>
                    检索验证
                  </Button>
                </div>
              </section>

              <section className="knowledge-metric-strip" aria-label="知识资产指标">
                <StatCard
                  label="知识文档"
                  value={overview.total}
                  labelIcon={<FolderOpenIcon />}
                  hint="当前知识库目录中登记的文档总数"
                />
                <StatCard
                  label="目录片段"
                  value={overview.chunks.toLocaleString()}
                  labelIcon={<FileSearchIcon />}
                  tone="success"
                  hint="目录记录中的片段数量，不代表实时索引或召回结果"
                />
                <StatCard
                  label="逻辑分类"
                  value={overview.categories}
                  labelIcon={<DataBaseIcon />}
                  tone="graph"
                  hint="按知识目录路径归集的分类数量"
                />
                <StatCard
                  label="文档标签"
                  value={overview.tags}
                  labelIcon={<TagIcon />}
                  tone="warning"
                  hint="用于知识筛选和治理的唯一标签数量"
                />
              </section>

              <section
                className="knowledge-panel knowledge-lifeline-panel"
                role="region"
                aria-labelledby="knowledge-lifeline-heading"
              >
                <Card
                  className="knowledge-command-card"
                  title={<span id="knowledge-lifeline-heading">知识生命线</span>}
                  subtitle="目录证据投影，不替代来源同步、检索与问答运行监控"
                  bordered
                >
                  <ol className="knowledge-lifeline" aria-label="知识生命线">
                    {lifeline.map((stage) => (
                      <li
                        key={stage.label}
                        className={`is-${stage.state}`}
                        aria-label={`${stage.label}：${LIFELINE_STATE_LABEL[stage.state]}，${stage.detail}`}
                        title={stage.detail}
                      >
                        {stage.label}
                      </li>
                    ))}
                  </ol>
                  <div className="knowledge-lifeline-evidence" aria-live="polite">
                    {overview.total === 0 ? (
                      <span>暂无文档，生命线等待真实处理记录</span>
                    ) : summaryMode ? (
                      <>
                        <span>仅依据服务器摘要中的状态、片段与解析覆盖字段推断</span>
                        <span>来源、召回、引用和回答暂无全量运行指标</span>
                      </>
                    ) : (
                      <>
                        <span>仅依据文档状态、解析元数据与来源标识推断</span>
                        <span>召回、引用和回答暂无运行指标</span>
                      </>
                    )}
                  </div>
                  <div className="knowledge-health-grid" aria-label="运营健康">
                    <div className="knowledge-health-item">
                      <div className="knowledge-health-heading">
                        <span>文档完成状态占比</span>
                        <strong>{completionRate}%</strong>
                      </div>
                      <div
                        className="knowledge-health-progress"
                        role="progressbar"
                        aria-label="文档完成状态占比"
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-valuenow={completionRate}
                      >
                        <Progress
                          percentage={completionRate}
                          label={false}
                          status={completionRate === 100 ? "success" : "active"}
                          size="small"
                        />
                      </div>
                    </div>
                    <div className="knowledge-health-item">
                      <div className="knowledge-health-heading">
                        <span>解析元数据覆盖</span>
                        <strong>{overview.parserCoverage}%</strong>
                      </div>
                      <div
                        className="knowledge-health-progress"
                        role="progressbar"
                        aria-label="解析元数据覆盖"
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-valuenow={overview.parserCoverage}
                      >
                        <Progress
                          percentage={overview.parserCoverage}
                          label={false}
                          status={overview.parserCoverage === 100 ? "success" : "active"}
                          size="small"
                        />
                      </div>
                    </div>
                  </div>
                </Card>
              </section>

              <div className="knowledge-operations-grid">
                <section
                  className="knowledge-panel knowledge-attention-panel"
                  role="region"
                  aria-labelledby="knowledge-attention-heading"
                >
                  <Card
                    className="knowledge-command-card"
                    title={<span id="knowledge-attention-heading">待处理事项</span>}
                    subtitle="按风险优先级集中处理知识运营问题"
                    bordered
                  >
                    <div className="knowledge-attention-list">
                      {attentionItems.map((item) => (
                        <button
                          key={item.key}
                          type="button"
                          className="knowledge-attention-item"
                          onClick={() => navigateTo(item.target)}
                        >
                          <span className={`knowledge-attention-marker is-${item.theme}`} />
                          <span className="knowledge-attention-copy">
                            <strong>{item.label}</strong>
                            <small>{item.detail}</small>
                          </span>
                          <span className="knowledge-attention-arrow" aria-hidden="true">
                            →
                          </span>
                        </button>
                      ))}
                      {attentionItems.length === 0 ? (
                        <div className="knowledge-attention-empty">
                          <strong>{overview.total === 0 ? "暂无目录级异常" : "未发现目录级异常"}</strong>
                          <span>
                            {overview.total === 0
                              ? "空库尚无可评估的处理记录"
                              : "召回、引用和回答仍需运行数据核验"}
                          </span>
                        </div>
                      ) : null}
                    </div>
                  </Card>
                </section>

                <section
                  className="knowledge-panel knowledge-recent-panel"
                  role="region"
                  aria-labelledby="knowledge-recent-heading"
                >
                  <Card
                    className="knowledge-command-card"
                    title={<span id="knowledge-recent-heading">最近资产</span>}
                    subtitle="最近发生变化的企业知识内容"
                    bordered
                  >
                    <div className="knowledge-recent-list">
                      {recentAssets.slice(0, 4).map((document) => {
                        const statusMeta = STATUS_PRESENTATION[document.status] ?? {
                          label: "未知状态",
                          theme: "default" as const,
                        };
                        return (
                          <div key={document.id} className="knowledge-recent-item">
                            <span className="knowledge-doc-icon" aria-hidden="true">
                              <FlashlightIcon />
                            </span>
                            <div className="knowledge-recent-copy">
                              <strong>{document.name}</strong>
                              <small>
                                {document.logical_folder_path || "未分类"} · {document.chunk_count} 个片段
                              </small>
                            </div>
                            <Tag theme={statusMeta.theme} variant="light" shape="round">
                              {statusMeta.label}
                            </Tag>
                          </div>
                        );
                      })}
                      {recentAssets.length === 0 ? (
                        <Typography.Text theme="secondary">
                          {summaryMode && overview.total > 0 ? "摘要未返回最近资产窗口" : "暂无知识资产"}
                        </Typography.Text>
                      ) : null}
                    </div>
                  </Card>
                </section>

                <section
                  className="knowledge-panel knowledge-actions-panel"
                  role="region"
                  aria-labelledby="knowledge-actions-heading"
                >
                  <Card
                    className="knowledge-command-card"
                    title={<span id="knowledge-actions-heading">快捷操作</span>}
                    subtitle="进入高频知识运营工作面"
                    bordered
                  >
                    <div className="knowledge-quick-actions">
                      <Button
                        className="knowledge-quick-action"
                        theme="primary"
                        icon={<FolderOpenIcon />}
                        onClick={() => navigateTo("documents")}
                      >
                        导入文档
                      </Button>
                      <Button
                        className="knowledge-quick-action"
                        variant="outline"
                        icon={<DataBaseIcon />}
                        onClick={() => navigateTo("sources")}
                      >
                        创建数据源
                      </Button>
                      <Button
                        className="knowledge-quick-action"
                        variant="outline"
                        icon={<FileSearchIcon />}
                        onClick={() => navigateTo("retrieval-lab")}
                      >
                        检索验证
                      </Button>
                      <Button
                        className="knowledge-quick-action"
                        variant="outline"
                        icon={<FlashlightIcon />}
                        onClick={() => navigateTo("eval")}
                      >
                        发起评测
                      </Button>
                    </div>
                  </Card>
                </section>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
