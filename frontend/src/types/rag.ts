/**
 * 与 Python 侧 models/schemas.py 对应的 TS 类型。
 * 命名与字段保持一致，便于前后端契约对照。
 */

export type CitationStatus = "ok" | "exists_only" | "stale" | "unsupported";

export interface Citation {
  claim: string;
  chunk_id: string;
  status: CitationStatus;
  reason: string;
}

/** 证据片段（桥服务从 verdict.evidence_chunks 提取，供「深链到段落」展示） */
export interface EvidenceChunk {
  chunk_id: string;
  doc_id: string;
  /** Upstream evidence owner; required for safe cross-dataset deep links when present. */
  dataset_id?: string | null;
  text: string;
  score: number;
  rank: number;
  source: string | null;
}

export interface QueryResult {
  query: string;
  answer: string;
  citations: Citation[];
  verdict: Record<string, unknown>;
  abstained: boolean;
  route: string;
  traces: string[];
  /**
   * 本次问答的 LLM 用量台账（后端 core.llm_usage 产出）。
   * 没开台账时后端给空对象；金额只在配了价格表时才有意义。
   */
  usage?: QueryUsage;
  /** 桥服务附加：答案依赖的证据片段（非必须字段） */
  evidence?: EvidenceChunk[];
}

/** 单个（模型 × 槽位）的用量条目。 */
export interface QueryUsageEntry {
  model: string;
  slot: string;
  calls: number;
  cached_calls: number;
  failures: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  saved_prompt_tokens: number;
  saved_completion_tokens: number;
  saved_total_tokens: number;
  cost: number;
  unpriced_total_tokens: number;
}

/** 本次问答的 LLM 用量汇总（含按槽位拆分）。 */
export interface QueryUsage {
  calls: number;
  cached_calls: number;
  failures: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  saved_prompt_tokens: number;
  saved_completion_tokens: number;
  saved_total_tokens: number;
  /** 估算金额（货币单位由部署方自定）；cost_priced=false 时不可信 */
  cost: number;
  cost_priced: boolean;
  unpriced_total_tokens: number;
  by_slot: QueryUsageEntry[];
}

export interface QueryResponse {
  result: QueryResult;
  using_mock: boolean;
  duration_ms: number;
  /** 命中服务端短期缓存（相同 query+acl 10 分钟内） */
  cached?: boolean;
}

/* ===== 流式问答事件（SSE /api/query/stream） ===== */

export type BackendRunEventType =
  | "run.started"
  | "node.started"
  | "node.completed"
  | "node.failed"
  | "node.skipped"
  | "node.cancelled"
  | "route.selected"
  | "retry.started"
  | "retry.completed"
  | "retry.failed"
  | "retry.skipped"
  | "degraded"
  | "run.completed"
  | "run.failed"
  | "run.cancelled";

export type BackendTopologyGroup =
  "input" | "understand" | "retrieve" | "generate" | "verify" | "output" | "extension";

export interface BackendTopologyNode {
  id: string;
  label: string;
  group: BackendTopologyGroup;
  description: string;
  optional: boolean;
  repeatable: boolean;
  available: boolean;
  plugin?: string;
  attributes: Record<string, unknown>;
}

export interface BackendTopologyEdge {
  id: string;
  source: string;
  target: string;
  kind: "dependency" | "conditional" | "retry" | "failure";
  label?: string;
}

export interface BackendRunTopology {
  id: string;
  revision: string;
  executor: string;
  nodes: BackendTopologyNode[];
  edges: BackendTopologyEdge[];
}

export interface BackendRunEventError {
  type: string;
  code?: string;
  recoverable: boolean;
}

export interface BackendRunEvent {
  schema_version: 1;
  run_id: string;
  seq: number;
  occurred_at: string;
  elapsed_ms: number;
  topology_id: string;
  topology_revision: string;
  type: BackendRunEventType;
  node_id?: string;
  attempt?: number;
  duration_ms?: number;
  attributes: Record<string, unknown>;
  error?: BackendRunEventError;
}

export interface StreamRunEvent {
  type: "run_event";
  event: BackendRunEvent;
}

export interface StreamRunEventDesync {
  type: "run_event_desync";
  run_id: string;
  expected_seq: number;
  reason: "typed_buffer_overflow";
}

export type StreamPhase =
  "retrieving" | "retrieved" | "generating" | "verifying" | "retrieving_again";

export interface StreamPhaseEvent {
  type: "phase";
  phase: StreamPhase;
  route?: string;
  chunks?: number;
}

export interface StreamTokenEvent {
  type: "token";
  text: string;
}

export interface StreamDoneEvent {
  type: "done";
  result: QueryResponse;
}

export interface StreamErrorEvent {
  type: "error";
  code: string;
  message: string;
}

export type StreamEvent =
  | StreamRunEvent
  | StreamRunEventDesync
  | StreamPhaseEvent
  | StreamTokenEvent
  | StreamDoneEvent
  | StreamErrorEvent;

/** 组件探活状态（/api/health 深度探活） */
export type ComponentStatus = "ok" | "empty" | "error" | "unconfigured";

export interface HealthComponent {
  status: ComponentStatus;
  detail: string;
  latency_ms?: number;
  row_count?: number;
  slots?: Record<string, { status: ComponentStatus; model: string; base_url: string }>;
}

export interface CircuitState {
  state: "closed" | "open" | "half_open";
  failures: number;
  open_count: number;
  close_count: number;
}

export interface HealthInfo {
  /** 三级状态：ok=全部就绪 / degraded=部分不可用 / down=核心不可用 */
  status: "ok" | "degraded" | "down" | string;
  milvus_uri: string;
  embedding_model: string;
  reranker_model: string;
  generation_model: string;
  graph_engine_on: boolean;
  components?: {
    milvus?: HealthComponent;
    embedder?: HealthComponent;
    reranker?: HealthComponent;
    llm?: HealthComponent;
  };
  circuits?: Record<string, CircuitState>;
  probed_at?: string;
}

/* ===== 监控 ===== */

export interface MetricStat {
  count: number;
  sum: number;
  mean: number;
  min: number;
  max: number;
  p50: number;
  p95: number;
  p99: number;
  error_rate?: number;
}

/** 最近一次查询摘要（监控页回放 / 可视化页链路回放） */
export interface RecentQuery {
  query: string;
  route: string;
  abstained: boolean;
  duration_ms: number;
  citations: number;
  traces: string[];
  ts: string;
}

export interface CacheLayerStats {
  size?: number;
  max?: number;
  hits?: number;
  misses?: number;
  hit_rate?: number;
  l1_hits?: number;
  l2_hits?: number;
  l2_errors?: number;
  l2_configured?: boolean;
  l2_reachable?: boolean;
  error?: string;
}

export interface RedisStats {
  configured: boolean;
  connected: boolean;
  detail: string;
}

export interface SharedQueueStats {
  active: boolean;
  backend?: string;
  pending?: number;
  queued?: number;
  unacked?: number;
  consumers?: number;
}
/** 单个环节的降级/失败统计（服务端算好的比率，前端只负责显示）。 */
export interface DegradedScopeStats {
  total: number;
  degraded: number;
  rate: number;
}

/** 上游降级与端点可达性概览（/api/metrics 的 degraded 段）。 */
export interface DegradedStats {
  retrieval?: DegradedScopeStats;
  reranker?: DegradedScopeStats;
  /** 键形如 "llm.endpoint.unreachable|endpoint=host:port"，值为次数 */
  endpoint_unreachable?: Record<string, number>;
}

export interface MetricsSnapshot {
  ts: string;
  metrics: Record<string, MetricStat>;
  /** 上游降级率（检索 / 重排 / 端点不可达）。与质量指标分开看：降级是"上游没扛住"，不是"检索变差"。 */
  degraded?: DegradedStats;
  recent_queries: RecentQuery[];
  queue?: {
    pending: number;
    max_concurrent: number;
    queue_max: number;
    shared?: SharedQueueStats;
  };
  cache?: CacheLayerStats & { ttl_s?: number };
  embed_cache?: CacheLayerStats;
  llm_cache?: CacheLayerStats;
  redis?: RedisStats;
  /** 服务已运行时长（秒） */
  uptime_s?: number;
  /** 熔断器状态：直接回答「系统为什么在拒答」 */
  circuits?: Record<string, CircuitState>;
}

/** 指标历史快照（data/metrics-history.jsonl 尾部） */
export interface MetricsHistoryItem {
  ts: string;
  metrics: Record<string, MetricStat>;
}

export interface MetricsHistory {
  items: MetricsHistoryItem[];
}

/* ===== 图谱可视化 ===== */

export interface GraphEntityNode {
  id: string;
  text: string;
  score?: number;
  relation_ids?: string[];
  passage_ids?: string[];
}

export interface GraphRelationEdge {
  id: string;
  text: string;
  score?: number;
  entity_ids?: string[];
  passage_ids?: string[];
  subject?: string;
  predicate?: string;
  object?: string;
}

export interface GraphSubgraph {
  entities: GraphEntityNode[];
  relations: GraphRelationEdge[];
  error?: string;
}

/* ===== 评测 ===== */

export interface CaseResult {
  id: string;
  question: string;
  unanswerable: boolean;
  abstained: boolean;
  answered: boolean;
  groundedness: number | null;
  relevance: number | null;
  citations_ok: boolean;
  notes: string;
}

export interface EvalReport {
  metrics: Record<string, number>;
  cases: CaseResult[];
}

export interface EvalRunResponse {
  report: EvalReport;
  out: string;
  dry_run: boolean;
}

export interface DatasetList {
  datasets: string[];
  default: string;
}

/* ===== 文档管理（server/documents.py + core/catalog.py） ===== */

/** 文档状态机（models/orm.py::_DOC_TRANSITIONS） */
export type DocumentStatus =
  "waiting" | "parsing" | "splitting" | "indexing" | "completed" | "error";

export type DocumentLifecycleState =
  | "active"
  | "expired"
  | "delete_requested"
  | "deleting"
  | "delete_failed"
  | "deleted";

/** 文档列表项（GET /api/documents） */
export interface DocumentItem {
  id: string;
  name: string;
  status: DocumentStatus | string;
  /** 当前阶段说明，如「构建知识图谱 37/200」 */
  status_detail?: string;
  /** 0..1 */
  progress: number;
  chunk_count: number;
  doc_type: string;
  error_message: string;
  parser_meta?: DocumentParserMeta;
  updated_at?: string | null;
  logical_folder_path?: string;
  tags?: string[];
  source_uri?: string | null;
  source_type?: string | null;
  source_id?: string | null;
  external_id?: string | null;
  tenant_id?: string;
  dataset_id?: string;
  mutation_generation?: number;
  lifecycle_state?: DocumentLifecycleState;
  retrieval_enabled?: boolean;
  active_delete_operation_id?: string | null;
}

/** 入库可观测元信息（后端 parser_meta，完成时写入） */
export interface DocumentParserMeta {
  /** 解析耗时（毫秒） */
  parse_ms?: number;
  total_ms?: number;
  stage_ms?: Record<string, number>;
  text_chars?: number;
  layout_blocks?: number;
  segment_count?: number;
  chunk_count?: number;
  /** 实际使用的切分方式：recursive / parent_child / qa */
  chunking_mode?: string;
  /** 路由决策理由（可读中文） */
  chunking_reason?: string;
  /** 路由理由代码：explicit_mode / table_doc_type / simple_short_no_layout / complex_or_structured */
  chunking_reason_code?: string;
  /** 路由决策事实（阈值/字数/版面块等） */
  chunking_decision?: {
    doc_type?: string;
    text_chars?: number;
    layout_blocks?: number;
    simple_max_chars?: number;
    configured_mode?: string;
  };
  /** 解析引擎：fast（文本层直取）/ vision（OCR 等） */
  engine?: string;
  /** 实际承担解析的插件（mineru / docling / plain-read 等）；引擎只说走哪条路，插件才是谁在跑 */
  provider?: string;
  /** 路由为什么选这个引擎（后端声明表里的 reason，随决策一起落库） */
  route_reason?: string;
  /** 分类失败退到备用引擎时的原因；出现即说明这次解析是退路，不是正常路径 */
  fallback_reason?: string;
  /** PDF 分类：text_based / scanned / mixed / image_based 等 */
  pdf_type?: string;
  page_count?: number;
  confidence?: number;
  file_name?: string;
  file_size?: number;
  /** 知识图谱构建统计（未开启建图时不存在） */
  graph?: {
    entities: number;
    relations: number;
    triplets: number;
    failed_chunks: number;
  };
}

/** 文档详情（GET /api/documents/{id}，比列表项多出文件信息与入库元信息） */
export interface DocumentDetail extends DocumentItem {
  tenant_id: string;
  dataset_id: string;
  status_detail: string;
  file_path: string;
  file_hash: string;
  parser_meta?: DocumentParserMeta;
  updated_at: string | null;
}

export interface DocumentList {
  documents: DocumentItem[];
}

/** Modern authenticated document-catalog list filters. */
export interface DocumentPageQuery {
  offset: number;
  limit: number;
  /** Opaque continuation token for updated_at_desc keyset pagination. */
  cursor?: string;
  q?: string;
  status?: "all" | "processing" | DocumentStatus;
  doc_type?: string;
  engine?: string;
  folder?: string;
  folder_mode?: "exact" | "subtree";
  tag?: string;
  lifecycle_state?: "all" | DocumentLifecycleState;
  /** 按 parser_meta.chunking_reason_code 过滤；"unknown" = 这一列还没写。 */
  chunking_reason_code?: string;
  sort?: "updated_at_desc" | "created_at_asc" | "name_asc";
}

/** Fully scoped item returned by the modern document catalog. */
export interface DocumentCatalogItem extends DocumentItem {
  tenant_id: string;
  dataset_id: string;
  status: DocumentStatus;
  status_detail: string;
  parser_meta: DocumentParserMeta;
  updated_at: string | null;
  logical_folder_path: string;
  tags: string[];
  source_uri: string | null;
  source_type: string | null;
  source_id: string | null;
  external_id: string | null;
  mutation_generation: number;
  lifecycle_state: DocumentLifecycleState;
  retrieval_enabled: boolean;
  active_delete_operation_id: string | null;
}

export interface DocumentPageResponse {
  items: DocumentCatalogItem[];
  total: number;
  offset: number;
  limit: number;
  /** Opaque continuation token; null when the page is the final page. */
  next_cursor: string | null;
}

export interface DocumentCatalogSummaryResponse {
  dataset_id: string;
  summary: {
    total: number;
    completed: number;
    processing: number;
    failed: number;
    chunks: number;
    parser_observed: number;
    parser_coverage: number;
  };
  facets: {
    statuses: {
      all: number;
      waiting: number;
      parsing: number;
      splitting: number;
      indexing: number;
      processing: number;
      completed: number;
      error: number;
    };
    types: Array<{ value: string; count: number }>;
    engines: Array<{ value: string; count: number }>;
    /**
     * 滚动窗口里可能缺这把键（后端先于前端上线之前）。
     * 消费侧 projectSummaryFacets 会按缺失处理成空分面；
     * 后端 core/catalog.py 自 227f0f4 起恒产出该键。
     */
    chunking_reasons?: Array<{ value: string; count: number }>;
    folders: Array<{ path: string; documents: number; chunks: number }>;
    tags: Array<{ name: string; documents: number; chunks: number }>;
  };
  recent: DocumentCatalogItem[];
  generated_at: string;
  /** Whether the legacy JSON tag facet scan covered the complete dataset. */
  tag_facets_complete: boolean;
  tag_facets_scan_limit: number;
  tag_facets_scanned: number;
  tag_facets_truncated: boolean;
}

/** POST /api/documents/ingest 与 /{id}/reindex 的响应 */
export interface IngestResponse {
  document_id: string;
  status?: string;
  note?: string;
}

/** POST /api/documents/ingest-folder 的扫描与登记摘要 */
export interface FolderIngestResponse {
  folder_path: string;
  discovered_count: number;
  queued_count: number;
  skipped_count: number;
  unsupported_count: number;
  duplicate_count: number;
  rejected_count: number;
  rejected_files: string[];
  document_ids: string[];
  note?: string;
}

/* ===== 配置中心 ===== */

export interface ConfigField {
  path: string;
  env: string;
  value: unknown;
  type: string;
  source: "env" | "default";
  sensitive: boolean;
  /** 通俗说明（非专业人员友好，后端下发） */
  hint?: string;
  /** 配置项中文名（后端下发） */
  label?: string;
}

export interface ConfigSection {
  label: string;
  description: string;
  fields: ConfigField[];
}

/** 解析引擎插件（后端注册表下发，插拔式体系） */
export interface EnginePluginInfo {
  name: string;
  describe: string;
  /** 引擎内可选模式（如 mineru: free/paid；docling: local/api） */
  modes: string[];
  /** parsers 段是否有同构配置 */
  configured: boolean;
  enabled: boolean;
  mode: string;
  priority: number | null;
}

/** /api/config 的 engines 载荷：引擎选择 + 注册表插件清单 + 当前生效引擎 */
export interface EnginesInfo {
  /** parsers.engine：auto 或显式引擎名 */
  choice: string;
  /** 当前生效引擎（auto 时按 priority 推导；全部禁用为 null） */
  active: string | null;
  plugins: EnginePluginInfo[];
}

export interface ConfigSnapshot {
  ts: string;
  sections: Record<string, ConfigSection>;
  /** 解析引擎注册表载荷（后端不可用时为 null，前端降级为通用表格） */
  engines?: EnginesInfo | null;
}

export interface ConfigUpdateResponse {
  saved: { path: string; env: string; value: unknown }[];
  rejected: string[];
  env_file: string;
  /** 是否已热重建管线（true=对后续请求立即生效；false=需重启后端） */
  hot_reloaded?: boolean;
  note: string;
}


export type DocumentDeleteOperationStatus =
  | "rejected"
  | "queued"
  | "projecting"
  | "finalizing"
  | "completed"
  | "failed";

export interface DocumentDeleteOperationResponse {
  operation_id: string;
  batch_operation_id: string | null;
  document_id: string;
  requested_document_id: string;
  status: DocumentDeleteOperationStatus;
  code: string | null;
  message: string | null;
  expected_generation: number | null;
  delete_generation: number | null;
  retrieval_enabled: boolean | null;
  projection_pending: boolean;
  stores: { required: number; completed: number; failed: number };
  reason: string;
  timestamps: {
    started_at: string | null;
    finalized_at: string | null;
    created_at: string | null;
    updated_at: string | null;
  };
}

export interface DocumentDeleteBatchStatusResponse {
  batch_operation_id: string;
  status: string;
  requested: number;
  accepted: number;
  completed: number;
  failed: number;
  rejected: number;
  reason: string;
  items: DocumentDeleteOperationResponse[];
  timestamps: {
    created_at: string | null;
    updated_at: string | null;
    finished_at: string | null;
  };
}


export interface DocumentMetricsResponse {
  summary: { total: number; completed: number; failed: number; processing: number; chunks: number };
  latency_ms: {
    parse: MetricStat;
    total: MetricStat;
    stages: Record<string, MetricStat>;
  };
  engine_distribution: { name: string; value: number }[];
  type_distribution: { name: string; value: number }[];
  slow_documents: { document_id: string; name: string; total_ms: number; status: string }[];
  recent_failures: { document_id: string; name: string; message: string; updated_at?: string | null }[];
  legacy_metadata_count: number;
}


export interface DocumentChunkItem {
  chunk_id: string;
  doc_id: string;
  text: string;
  text_hash: string;
  content_revision: number;
  tenant_id?: string;
  dataset_id?: string;
  document_revision?: number;
  enabled?: boolean;
  chunk_role?: "flat" | "child" | "parent" | string;
  desired_index_revision?: number;
  index_status?: string;
  indexed_revision?: number;
  projection_pending?: boolean;
  parent_chunk_id?: string | null;
  source?: string | null;
  seq: number;
  page?: number | string | null;
  heading?: string | null;
  context: string;
  char_count: number;
  metadata?: Record<string, unknown>;
  parent_relation?: "known" | "missing" | "unknown" | "none";
  child_count?: number;
  token_estimate?: number;
  language?: string | null;
  mime_type?: string | null;
  source_reference?: string;
  projection_semantics?: string;
  created_at?: string | null;
  updated_at?: string | null;
  /** 解析器原始产出；只在单切片读取里返回，列表响应刻意不带 */
  source_content?: string;
  /** 最后一次人工写入的作者标识（当前由服务端固定为系统操作员） */
  editor_id?: string;
  /** 最后一次人工写入的来源：user | restore | revert | delete … */
  edit_source?: string;
  /** 最后一次人工写入的原因；记在 ChunkHead 元数据上，不随 Revision 逐版保存 */
  edit_reason?: string;
  edit_reason_at?: string | null;
}

/** 一条不可变的切片 Revision 快照（`GET …/chunks/{chunk_id}/revisions`） */
export interface DocumentChunkRevisionItem {
  revision: number;
  content: string;
  content_hash: string;
  enabled: boolean;
  editor_id: string;
  edit_source: string;
  edited_at: string | null;
}

export interface DocumentChunkRevisionList {
  chunk_id: string;
  items: DocumentChunkRevisionItem[];
}

export interface DocumentChunkList {
  authority_mode?: "off" | "shadow" | "active";
  items: DocumentChunkItem[];
  total: number;
  offset: number;
  limit: number;
  known_parent_ids?: string[];
  missing_parent_ids?: string[];
}


export interface DocumentChunkUpdateResponse extends DocumentChunkItem {
  graph_update?: {
    removed_relations: number;
    removed_entities: number;
    entities: number;
    relations: number;
  };
  authority_mode?: "off" | "shadow" | "active";
  projection_pending?: boolean;
  operation_ids?: string[];
}

export interface DocumentChunkDeleteResponse {
  document_id: string;
  chunk_id: string;
  removed_chunks: number;
  removed_relations: number;
  removed_entities: number;
  remaining_chunks: number;
  content_revision: number;
  authority_mode?: "off" | "shadow" | "active";
  projection_pending?: boolean;
  operation_ids?: string[];
}


export interface DocumentManagementSettings {
  id: string;
  logical_folder_path: string;
  tags: string[];
  parser_meta?: DocumentParserMeta;
}

export interface BatchDocumentSettingsResponse {
  requested: number;
  updated: number;
  not_found: string[];
}
