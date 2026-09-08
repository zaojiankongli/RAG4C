/**
 * Runs API 类型。
 *
 * 契约来源：后端 OpenAPI schema（单一事实源）。
 * 本文件的 DTO 接口一律引用 `./generated/openapi` 中的
 * `components["schemas"]["Run*Response"]`，字段增删/类型收窄由后端 Pydantic
 * 模型决定；此处仅叠加**前端领域窄类型**（运行时校验器 api/runs.ts 使用的
 * 字面量联合），它们是 generated 宽类型的合法子集。
 *
 * 纯前端概念（路由参数、视图、过滤器）不来自后端，保持手写。
 */

import type { BackendRunEvent, BackendRunTopology } from "./rag";
import type { components } from "./generated/openapi";

// 注意：RunListResponse 在 OpenAPI 中名为 server__run_ops__RunListResponse
// （knowledge_sources_api 也定义了一个 RunListResponse，FastAPI 为避免
// schema 名冲突给 run_ops 的加了模块前缀）。其余 Run*Response 无冲突用短名。
type RunSummarySchema = components["schemas"]["RunSummaryResponse"];
type RunListSchema = components["schemas"]["server__run_ops__RunListResponse"];
type RunDetailSchema = components["schemas"]["RunDetailResponse"];
type RunEventsSchema = components["schemas"]["RunEventsResponse"];
type RunHealthSchema = components["schemas"]["RunHealthResponse"];
type RunNodeRollupSchema = components["schemas"]["RunNodeRollupResponse"];
type RunEventSchema = components["schemas"]["RunEventResponse"];

export type RunExecutor = "sequential_stream" | "sequential" | "langgraph" | "cache_replay";
export type RunStatus = "running" | "completed" | "failed" | "cancelled" | "interrupted";
export type RunOutcome = "answered" | "abstained" | "unknown";
export type RunAttention = "error" | "interrupted" | "cancelled" | "slow" | "stuck";
export type RunEventIntegrity = "complete" | "partial" | "unknown";
export type RunPersistenceStatus =
  "pending" | "durable" | "partial" | "memory_only" | "unavailable";
export type RunHistoryState = "complete" | "partial" | "expired";
export type RunListView = "recent" | "active" | "slow" | "errors" | "stuck";
export type RunDetailTab = "process" | "timeline" | "events" | "knowledge";

/** 运行时校验器对 attention 的窄化（generated 为 string[]，前端穷举表需要窄联合） */
export interface RunSummaryDto
  extends Omit<RunSummarySchema, "attention" | "executor" | "current_node_ids" | "failed_node_ids"> {
  attention: RunAttention[];
  executor: RunExecutor;
  current_node_ids: string[];
  failed_node_ids: string[];
}
export interface RunListDto extends Omit<RunListSchema, "items"> {
  items: RunSummaryDto[];
}
export type RunNodeRollupDto = RunNodeRollupSchema;
export type RunTopologyDto = Omit<BackendRunTopology, "executor"> & { executor: RunExecutor };
export interface RunDetailDto extends Omit<RunDetailSchema, "summary" | "node_rollup" | "topology"> {
  summary: RunSummaryDto;
  node_rollup: RunNodeRollupDto[];
  topology: RunTopologyDto;
}
export interface RunEventsDto extends Omit<RunEventsSchema, "events"> {
  events: BackendRunEvent[];
}
export type RunHealthDto = RunHealthSchema;
export type RunEventDto = RunEventSchema;

/** 运行时校验器使用的窄化派生（保证 generated 宽类型与前端窄化不脱钩） */
export type NarrowRunEvent = BackendRunEvent;

export interface RunListFilters {
  view?: RunListView;
  status?: RunStatus[];
  slowMs?: number;
  startedAfter?: string;
  startedBefore?: string;
  fingerprint?: string;
  limit?: number;
  cursor?: string;
}
