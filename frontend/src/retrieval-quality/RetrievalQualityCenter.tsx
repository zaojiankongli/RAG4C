import "./retrieval-quality.css";
import { Button, Tag } from "../ui";
import { FileSearchIcon } from "tdesign-icons-react";
import { useEffect, useMemo, useRef, useState, type MouseEvent, type RefObject } from "react";
import PageState from "../components/PageState";
import PageTopbar from "../components/PageTopbar";
import { useConnection } from "../context/ConnectionContext";
import { useKnowledgeWorkspace } from "../knowledge/KnowledgeWorkspaceContext";
import { readKnowledgeActorToken } from "../knowledge/workspaceScope";
import ComparisonResults from "./components/ComparisonResults";
import ExperimentDetailDrawer from "./components/ExperimentDetailDrawer";
import ExperimentHistory from "./components/ExperimentHistory";
import RetrievalComposer from "./components/RetrievalComposer";
import { requireRetrievalScope } from "./hooks/scope";
import { useExperimentDetail } from "./hooks/useExperimentDetail";
import { useExperimentHistory } from "./hooks/useExperimentHistory";
import { useJudgmentMutation } from "./hooks/useJudgmentMutation";
import { useRetrievalRun } from "./hooks/useRetrievalRun";
import type { ComposerDraft, Experiment, HistoryFilters, RunRetrievalRequest } from "./model/contracts";

const INITIAL_DRAFT: ComposerDraft = { query: "公司的报销流程是什么？", acl: [], variants: [
  { clientId: "rq-initial-a", name: "策略 A", route_target: "auto", top_k: 8, hybrid_search_on: true, rerank_on: true, graph_retrieval_on: false, sentence_window_on: false, source_diversity: "off" },
  { clientId: "rq-initial-b", name: "策略 B", route_target: "hybrid", top_k: 8, hybrid_search_on: true, rerank_on: false, graph_retrieval_on: false, sentence_window_on: false, source_diversity: "group_only" },
] };
function useMobile() {
  const [mobile, setMobile] = useState(() => typeof matchMedia === "function" && matchMedia("(max-width: 600px)").matches);
  useEffect(() => { if (typeof matchMedia !== "function") return; const media=matchMedia("(max-width: 600px)"); const update=()=>setMobile(media.matches); update(); media.addEventListener?.("change",update); return()=>media.removeEventListener?.("change",update); }, []);
  return mobile;
}

export default function RetrievalQualityCenter() {
  const workspace = useKnowledgeWorkspace(); const { online } = useConnection(); const actorToken = readKnowledgeActorToken();
  const scope = useMemo(() => requireRetrievalScope(workspace.scope, actorToken), [actorToken, workspace.scope]);
  const effectiveScope = online === true ? scope : null;
  const [draft, setDraft] = useState<ComposerDraft>(INITIAL_DRAFT); const [mobileTab, setMobileTab] = useState("composer"); const mobile = useMobile();
  const runner = useRetrievalRun(effectiveScope);
  const history = useExperimentHistory(effectiveScope, Boolean(effectiveScope), runner.pending);
  const [selectedId, setSelectedId] = useState<string | null>(null); const openerRef = useRef<HTMLButtonElement | null>(null);
  const detail = useExperimentDetail(effectiveScope, selectedId);
  const judgments = useJudgmentMutation(effectiveScope, scope?.actorId ?? "", detail.detail, detail.refresh);
  const execute = async (payload: RunRetrievalRequest) => { const ok = await runner.run(payload); if (ok) { await history.refresh(); setMobileTab("results"); } };
  const select = (item: Experiment, opener: RefObject<HTMLButtonElement>) => { openerRef.current = opener.current; setSelectedId(item.id); };
  const switchMobileTab = (value: string, event: MouseEvent<HTMLButtonElement>) => {
    if (selectedId && value !== "history") openerRef.current = event.currentTarget;
    setMobileTab(value);
  };
  const close = () => setSelectedId(null);
  const historyPanel = <ExperimentHistory items={history.items} status={history.status} error={history.error} paging={history.paging} hasPrevious={history.hasPrevious} hasNext={history.nextBeforeSequence !== null} onFilters={(filters: HistoryFilters) => { void history.applyFilters(filters); }} onRefresh={() => { void history.refresh(); }} onNext={() => { void history.nextPage(); }} onPrevious={() => { void history.previousPage(); }} onSelect={select}/>;
  const composer = <RetrievalComposer value={draft} onChange={setDraft} running={runner.pending} onRun={execute}/>;
  const results = <ComparisonResults response={runner.response}/>;
  const topbar = <PageTopbar icon={<FileSearchIcon/>} title="检索质量中心" subtitle="纯检索策略对比、不可变证据与操作者判断" extra={scope ? <Tag variant="light-outline">{scope.datasetId}</Tag> : undefined}/>;

  if (!scope) return <div className="page-slot rq-page">{topbar}<div className="page-shell"><div className="page-shell-inner"><PageState status="error" title="缺少检索质量授权范围" description="需要有效的租户、知识库和 KnowledgeOps Actor Bearer 凭据。所有检索与历史请求均已关闭。"/></div></div></div>;
  if (online === false) return <div className="page-slot rq-page">{topbar}<div className="page-shell"><div className="page-shell-inner"><PageState status="error" title="检索质量服务未连接" description="当前无法读取或运行真实检索实验。恢复连接后再重试。"/></div></div></div>;
  if (online === null) return <div className="page-slot rq-page">{topbar}<div className="page-shell"><div className="page-shell-inner"><PageState status="loading" title="正在确认检索质量服务"/></div></div></div>;

  return <div className="page-slot rq-page">{topbar}<div className="page-shell"><div className="page-shell-inner rq-shell">
    <section className="rq-authority-banner" aria-label="检索质量权威范围"><div><span className="rq-eyebrow">AUTHENTICATED KNOWLEDGE WORKSPACE</span><strong>{scope.tenantId} / {scope.datasetId}</strong></div><p>运行结果和历史记录是当前知识库服务代次上的不可变历史证据；页面不会用演示数据替代权威事实。</p></section>
    {runner.error ? <div role="alert" className="rq-inline-error">{runner.error.message}</div> : null}
    {mobile ? <div className="rq-mobile-views"><div className="rq-mobile-tablist" role="tablist" aria-label="检索质量中心视图">{[["composer","配置"],["results","结果"],["history","历史"]].map(([value,label]) => <Button key={value} id={`rq-tab-${value}`} role="tab" aria-selected={mobileTab === value} aria-controls={mobileTab === value ? `rq-panel-${value}` : undefined} type={mobileTab === value ? "primary" : undefined} onClick={(event: MouseEvent<HTMLButtonElement>) => switchMobileTab(value, event)}>{label}</Button>)}</div><div id={`rq-panel-${mobileTab}`} role="tabpanel" aria-labelledby={`rq-tab-${mobileTab}`}>{mobileTab === "composer" ? composer : mobileTab === "results" ? results : historyPanel}</div></div> : <>{composer}{results}{historyPanel}</>}
  </div></div>
  <ExperimentDetailDrawer visible={Boolean(selectedId)} detail={detail.detail} agreement={detail.agreement} status={detail.status} error={detail.error} actorId={scope.actorId} openerRef={openerRef} conflictRanks={judgments.conflictRanks} savingRanks={judgments.savingRanks} draftFor={judgments.draftFor} setDraft={judgments.setDraft} onSave={(rank, value) => { void judgments.save(rank, value); }} onClose={close}/>
  </div>;
}
