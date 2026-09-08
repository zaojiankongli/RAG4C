export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
export type ExperimentStatus = "completed" | "failed";
export type RouteTarget = "auto" | "hybrid" | "vector_graph_rag" | "full";
export type SourceDiversity = "off" | "group_only" | "group_mmr";
export type RelevanceLabel = "relevant" | "partial" | "irrelevant";

export interface RetrievalScope { tenantId: string; datasetId: string; actorToken: string; actorId: string; }
export interface RetrievalVariant {
  name: string; route_target: RouteTarget; top_k: number; hybrid_search_on: boolean;
  rerank_on: boolean; graph_retrieval_on: boolean; sentence_window_on: boolean;
  source_diversity: SourceDiversity;
}
export interface RetrievalVariantDraft extends RetrievalVariant { clientId: string; }
export interface ComposerDraft { query: string; acl: string[]; variants: RetrievalVariantDraft[]; }
export interface RunRetrievalRequest { query: string; acl: string[]; variants: RetrievalVariant[]; }

export interface Judgment {
  id: string; tenant_id: string; dataset_id: string; experiment_id: string; result_rank: number;
  document_id: string | null; chunk_id: string | null; relevance_label: RelevanceLabel;
  score: number | null; note: string; revision: number; created_by: string; created_at: string;
}
export interface JudgmentCreate {
  result_rank: number; relevance_label: RelevanceLabel; document_id?: string | null;
  chunk_id?: string | null; score?: number | null; note?: string;
}
export interface JudgmentPatch {
  expected_revision: number; relevance_label?: RelevanceLabel; score?: number | null; note?: string;
}

export interface Experiment {
  sequence: number; id: string; tenant_id: string; dataset_id: string; query: string; query_hash: string;
  strategy_snapshot: Record<string, JsonValue>; result_snapshot: Record<string, JsonValue>;
  evidence_lineage: Record<string, JsonValue>; latency_ms: number; status: ExperimentStatus;
  created_by: string; created_at: string; run_id: string | null;
}
export interface ExperimentDetail extends Experiment { judgments: Judgment[]; }
export interface RunItem extends Experiment {
  name: string; route: RouteTarget; result_count: number; reranked: boolean; degraded: boolean;
}
export interface RunResponse { run_id: string; dataset_serving_generation: number; items: RunItem[]; }
export interface ExperimentListResponse { items: Experiment[]; next_before_sequence: number | null; }
export interface Agreement {
  experiment_id: string; judged_results: number; judgment_count: number; multi_judged_results: number;
  unanimous_results: number; conflicting_results: number; exact_agreement_rate: number | null;
  label_counts: Partial<Record<RelevanceLabel, number>>; mean_score: number | null;
}
export interface HistoryFilters { status?: ExperimentStatus; runId?: string; query?: string; queryHash?: string; beforeSequence?: number; }

export interface LineageView {
  documentId: string | null; chunkId: string | null; documentRevision: number | null;
  contentRevision: number | null; contentHash: string | null;
}
export interface EvidenceView extends LineageView {
  rank: number; score: number | null; denseCosine: number | null; branch: string | null;
  excerpt: string | null; documentName: string | null; sourceType: string | null; lineage: LineageView | null;
}
export interface StrategyView {
  revision: number | null; routeTarget: string | null; topK: number | null; hybridSearchOn: boolean | null;
  rerankOn: boolean | null; graphRetrievalOn: boolean | null; sentenceWindowOn: boolean | null;
  sourceDiversity: string | null;
}
export type ExperimentViewState = "completed" | "failed" | "no-hit";
export interface VariantView {
  experimentId: string; sequence: number; runId: string | null; name: string; query: string;
  status: ExperimentStatus; state: ExperimentViewState; route: string | null; latencyMs: number;
  resultCount: number; reranked: boolean; degraded: boolean; datasetServingGeneration: number | null;
  createdBy: string; createdAt: string; failureCode: string | null; strategy: StrategyView;
  evidence: EvidenceView[]; traces: string[];
}
