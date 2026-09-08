import type { KnowledgeWorkspaceScope } from "../../knowledge/workspaceScope";

export type SourceKind = "local_dir" | "github_repo";
export type SourceStatus = "active" | "disabled";
export type RunStatus = "running" | "completed" | "failed" | "incomplete" | "dry_run" | "superseded";
export type RunTrigger = "manual" | "scheduled" | "retry";
export type ExecutionState = "pending" | "executing" | "failed" | "completed";
export type ItemAction = "upsert" | "skip" | "delete";
export type ItemResult = "completed" | "failed" | "skipped" | "suppressed" | "dry_run" | "queued";
export type JsonObject = Record<string, unknown>;

export interface SourceScope extends KnowledgeWorkspaceScope { actorToken: string; }

export interface LocalDirConfig extends JsonObject {
  path: string;
  include?: string[];
  exclude?: string[];
  extensions?: string[] | null;
  max_files?: number;
  credential_ref?: string | null;
}

export interface GitHubRepoConfig extends JsonObject {
  repo: string;
  ref?: string;
  include?: string[];
  exclude?: string[];
  extensions?: string[] | null;
  strip_prefix?: string;
  timeout?: number;
  max_files?: number;
  mode?: "auto" | "tarball" | "api";
  credential_ref?: string | null;
}

export interface SourceRecord {
  id: string;
  tenant_id: string;
  dataset_id: string;
  name: string;
  kind: SourceKind;
  config: JsonObject;
  metadata: JsonObject;
  status: SourceStatus;
  generation: number;
  last_cursor: JsonObject;
  last_result: JsonObject;
  last_error: string;
  last_sync_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface SourceListResponse { items: SourceRecord[]; count: number; }
export interface SourceCreate {
  name: string;
  kind: SourceKind;
  config: LocalDirConfig | GitHubRepoConfig;
  metadata?: JsonObject;
  enabled?: boolean;
}
export interface SourcePatch {
  expected_generation: number;
  name?: string;
  kind?: SourceKind;
  config?: LocalDirConfig | GitHubRepoConfig;
  metadata?: JsonObject;
}
export interface SourceGenerationRequest { expected_generation: number; }
export interface SourceSyncRequest { force_full: boolean; dry_run: boolean; }

export interface RunCounts {
  fetched: number;
  ingested: number;
  skipped: number;
  removed: number;
  pending_deletes: number;
  chunks: number;
  failed: number;
}
export interface SourceRun {
  id: string;
  source_id: string;
  tenant_id: string;
  dataset_id: string;
  status: RunStatus;
  trigger: RunTrigger;
  force_full: boolean;
  dry_run: boolean;
  source_generation: number;
  dataset_generation: number;
  retry_of_run_id: string | null;
  execution_state: ExecutionState;
  execution_attempts: number;
  execution_last_error: string;
  execution_finished_at: string | null;
  cursor_before: JsonObject;
  cursor_after: JsonObject;
  counts: RunCounts;
  fetch_error: string;
  duration_ms: number;
  started_at: string;
  finished_at: string | null;
}
export interface RunAccepted {
  run_id: string;
  source_id: string;
  status: RunStatus;
  trigger: RunTrigger;
  retry_of_run_id: string | null;
  execution_state: ExecutionState;
  replayed: boolean;
}
export interface RunListResponse { items: SourceRun[]; count: number; next_cursor: string | null; }
export interface RunItem {
  id: string;
  run_id: string;
  source_id: string;
  external_id: string;
  doc_id: string;
  source_uri: string;
  content_hash: string;
  action: ItemAction;
  result: ItemResult;
  chunk_count: number;
  error_code: string;
  error_message: string;
  created_at: string;
  updated_at: string;
}
export interface RunItemListResponse { items: RunItem[]; count: number; next_cursor: string | null; }
export interface RunFilters { status?: RunStatus; trigger?: RunTrigger; cursor?: string | null; limit?: number; }
export interface ItemFilters { result?: ItemResult; action?: ItemAction; cursor?: string | null; limit?: number; }
