import { request } from "../../api/client";
import {
  projectAutomationActionRequest,
  projectAutomationEventPage,
  projectAutomationRule,
  projectAutomationRuleRevision,
  projectAutomationRun,
  projectAutomationSummary,
  type AutomationRuleDefinition,
  type AutomationActionRequest,
  type AutomationEvent,
  type AutomationPage,
  type AutomationRule,
  type AutomationRuleRevision,
  type AutomationRun,
  type AutomationScope,
} from "../model/automationModel";

export interface AutomationApiScope extends AutomationScope {
  actorToken: string;
}
export interface AutomationRequestOptions {
  signal?: AbortSignal;
  idempotencyKey?: string;
}
export interface AutomationListQuery {
  cursor?: string;
  limit?: number;
  status?: string;
  ruleId?: string;
}
export interface AutomationMutationOutcome {
  state: "applied" | "replayed" | "conflict" | "blocked" | "rejected" | "unavailable";
  operation: string;
  resource_id: string | null;
  revision: number | null;
  message: string | null;
  retryable: boolean;
}
export interface AutomationRuleDetail {
  rule: AutomationRule;
  current_revision: AutomationRuleRevision | null;
  recent_runs: AutomationRun[];
}
export interface AutomationRunDetail {
  run: AutomationRun;
  action_requests: AutomationActionRequest[];
  events: AutomationEvent[];
}
export interface CreateAutomationRuleInput {
  name: string;
  priority: number;
  workspaceId?: string;
  datasetId?: string;
  definition: unknown;
  reason: string;
}
export interface CreateAutomationRevisionInput {
  expectedRevision: number;
  expectedDefinitionDigest: string;
  definition: unknown;
  reason: string;
}
export interface PreviewAutomationRuleInput {
  expectedRevision: number;
  expectedDefinitionDigest: string;
  triggerEvent: unknown;
  reason: string;
}
export interface ActivateAutomationRuleInput {
  revisionId: string;
  expectedRevision: number;
  expectedDefinitionDigest: string;
  reason: string;
}
export interface PauseAutomationRuleInput {
  expectedRevision: number;
  expectedDefinitionDigest: string;
  reason: string;
}

function hasControlCharacters(value: string): boolean {
  for (const character of value) {
    const code = character.charCodeAt(0);
    if (code < 32 || code === 127) return true;
  }
  return false;
}
const ID = /^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const URL = /(?:https?|ftp|file|mailto|javascript|data):\S+|(?:^|\s)(?:www\.)\S+/i;
const SECRET =
  /(?:password|secret|credential|authorization|bearer|token|ticket|api[_ -]?key)\s*[:=]?\s*\S+/i;
function object(value: unknown, field: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    throw new Error(`${field} is invalid`);
  return value as Record<string, unknown>;
}
function exact(value: unknown, field: string, keys: readonly string[]): Record<string, unknown> {
  const s = object(value, field),
    a = new Set(keys);
  for (const key of Object.keys(s))
    if (!a.has(key)) throw new Error(`${field} contains unexpected field: ${key}`);
  return s;
}
function text(value: unknown, field: string, max = 512): string {
  if (typeof value !== "string") throw new Error(`${field} is invalid`);
  const r = value.trim();
  if (!r || r.length > max || hasControlCharacters(r) || URL.test(r) || SECRET.test(r))
    throw new Error(`${field} is unsafe`);
  return r;
}
function id(value: unknown, field: string): string {
  const r = text(value, field, 128);
  if (!ID.test(r) || r.includes("..")) throw new Error(`${field} invalid`);
  return r;
}
function integer(value: unknown, field: string, min = 0, max = Number.MAX_SAFE_INTEGER): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < min || value > max)
    throw new Error(`${field} must be an exact integer`);
  return value;
}
function digest(value: unknown, field: string): string {
  const r = text(value, field, 64);
  if (!DIGEST.test(r)) throw new Error(`${field} invalid digest`);
  return r;
}
function scopeOf(scope: AutomationApiScope): AutomationApiScope {
  return {
    tenantId: id(scope.tenantId, "tenantId"),
    accountId: scope.accountId === undefined ? undefined : id(scope.accountId, "accountId"),
    actorToken: text(scope.actorToken, "actorToken", 2048),
  };
}
function modelScope(scope: AutomationApiScope | AutomationScope): AutomationScope {
  return { tenantId: scope.tenantId, accountId: scope.accountId };
}
function headers(scope: AutomationApiScope, key?: string): Record<string, string> {
  const s = scopeOf(scope),
    h: Record<string, string> = {
      "Content-Type": "application/json",
      "X-RAG4C-Tenant": s.tenantId,
      Authorization: `Bearer ${s.actorToken}`,
    };
  if (s.accountId) h["X-RAG4C-Account"] = s.accountId;
  if (key) h["Idempotency-Key"] = text(key, "Idempotency-Key", 128);
  return h;
}
function mutationKey(options?: AutomationRequestOptions): string {
  if (!options?.idempotencyKey) throw new Error("Idempotency-Key is required");
  return text(options.idempotencyKey, "Idempotency-Key", 128);
}
function query(value: AutomationListQuery = {}): string {
  const entries: Array<[string, string]> = [];
  if (value.cursor !== undefined) entries.push(["cursor", text(value.cursor, "cursor", 2048)]);
  if (value.limit !== undefined)
    entries.push(["limit", String(integer(value.limit, "limit", 1, 200))]);
  if (value.ruleId !== undefined) entries.push(["rule_id", id(value.ruleId, "ruleId")]);
  if (value.status !== undefined) entries.push(["status", text(value.status, "status", 32)]);
  entries.sort(([a], [b]) => a.localeCompare(b));
  return entries.length ? `?${new URLSearchParams(entries).toString()}` : "";
}
async function call(path: string, init: RequestInit): Promise<unknown> {
  return request(path, init);
}
function page<T>(
  value: unknown,
  scope: AutomationScope,
  project: (value: unknown, scope: AutomationScope) => T,
): AutomationPage<T> {
  const s = exact(value, "page", ["items", "next_cursor", "invalid_item_count"]);
  if (!Array.isArray(s.items)) throw new Error("page items invalid");
  return {
    items: s.items.map((item) => project(item, scope)),
    next_cursor: s.next_cursor == null ? null : text(s.next_cursor, "next_cursor", 2048),
    invalid_item_count: integer(s.invalid_item_count, "invalid_item_count"),
  };
}
function outcome(value: unknown): AutomationMutationOutcome {
  const s = exact(value, "mutation_outcome", [
    "state",
    "operation",
    "resource_id",
    "revision",
    "message",
    "retryable",
  ]);
  const state = text(s.state, "state", 32) as AutomationMutationOutcome["state"];
  if (
    !new Set(["applied", "replayed", "conflict", "blocked", "rejected", "unavailable"]).has(state)
  )
    throw new Error("state invalid");
  return {
    state,
    operation: text(s.operation, "operation", 128),
    resource_id: s.resource_id == null ? null : id(s.resource_id, "resource_id"),
    revision: s.revision == null ? null : integer(s.revision, "revision", 1),
    message: s.message == null ? null : text(s.message, "message"),
    retryable:
      typeof s.retryable === "boolean"
        ? s.retryable
        : (() => {
            throw new Error("retryable invalid");
          })(),
  };
}
function normalizeDefinitionActionPlan(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) throw new Error("definition.action_plan is invalid");
  return value.map((item, index) => {
    const source = object(item, `definition.action_plan[${index}]`);
    if (Object.prototype.hasOwnProperty.call(source, "step_index")) {
      const internal = exact(source, `definition.action_plan[${index}]`, [
        "step_index",
        "action_code",
        "params",
      ]);
      if (
        integer(internal.step_index, `definition.action_plan[${index}].step_index`, 0, 3) !== index
      )
        throw new Error("definition.action_plan step_index is not contiguous");
      return { action_code: internal.action_code, params: internal.params };
    }
    return exact(source, `definition.action_plan[${index}]`, ["action_code", "params"]);
  });
}
function definitionBody(value: unknown, scope: AutomationScope): AutomationRuleDefinition {
  const source = exact(value, "definition", [
    "trigger_code",
    "condition_code",
    "condition_params",
    "action_plan",
  ]);
  const actionPlan = normalizeDefinitionActionPlan(source.action_plan).map((item, index) => ({
    step_index: index,
    action_code: item.action_code,
    params: item.params,
  }));
  const projected = projectAutomationRuleRevision(
    {
      id: "validation-revision",
      tenant_id: scope.tenantId,
      rule_id: "validation-rule",
      revision: 1,
      trigger_code: source.trigger_code,
      condition_code: source.condition_code,
      condition_params_json: source.condition_params,
      action_plan_json: actionPlan,
      definition_digest: "0".repeat(64),
      created_at: "2026-08-30T00:00:00.000000Z",
      created_by: scope.accountId ?? "validation-actor",
    },
    modelScope(scope),
  );
  return {
    trigger_code: projected.trigger_code,
    condition_code: projected.condition_code,
    condition_params: projected.condition_params,
    action_plan: projected.action_plan,
  };
}
export function validateAutomationRuleDefinition(value: unknown): AutomationRuleDefinition {
  return definitionBody(value, { tenantId: "validation-tenant", accountId: "validation-actor" });
}
function definitionRequestBody(value: unknown, scope: AutomationScope) {
  const definition = definitionBody(value, scope);
  return {
    trigger_code: definition.trigger_code,
    condition_code: definition.condition_code,
    condition_params: definition.condition_params,
    action_plan: definition.action_plan.map(({ action_code, params }) => ({ action_code, params })),
  };
}

async function read<T>(
  scope: AutomationApiScope,
  path: string,
  project: (value: unknown) => T,
  options: AutomationRequestOptions = {},
): Promise<T> {
  const s = scopeOf(scope);
  return project(await call(path, { method: "GET", headers: headers(s), signal: options.signal }));
}
function mutate(
  scope: AutomationApiScope,
  path: string,
  body: Record<string, unknown>,
  options: AutomationRequestOptions,
): Promise<AutomationMutationOutcome> {
  const s = scopeOf(scope);
  const key = mutationKey(options);
  return call(path, {
    method: "POST",
    headers: headers(s, key),
    body: JSON.stringify(body),
    signal: options.signal,
  }).then(outcome);
}

export const fetchAutomationSummary = (
  scope: AutomationApiScope,
  options: AutomationRequestOptions = {},
) =>
  read(
    scope,
    "/api/enterprise/automations/summary",
    (raw) => projectAutomationSummary(raw, modelScope(scope)),
    options,
  );
export const fetchAutomationRules = (
  scope: AutomationApiScope,
  list: AutomationListQuery = {},
  options: AutomationRequestOptions = {},
) =>
  read(
    scope,
    `/api/enterprise/automations/rules${query(list)}`,
    (raw) => page(raw, modelScope(scope), projectAutomationRule),
    options,
  );
export const fetchAutomationRule = (
  scope: AutomationApiScope,
  ruleId: string,
  options: AutomationRequestOptions = {},
) =>
  read(
    scope,
    `/api/enterprise/automations/rules/${encodeURIComponent(id(ruleId, "ruleId"))}`,
    (raw) => {
      const s = exact(raw, "rule_detail", ["rule", "current_revision", "recent_runs"]);
      if (!Array.isArray(s.recent_runs)) throw new Error("recent_runs invalid");
      return {
        rule: projectAutomationRule(s.rule, modelScope(scope)),
        current_revision:
          s.current_revision == null
            ? null
            : projectAutomationRuleRevision(s.current_revision, modelScope(scope)),
        recent_runs: s.recent_runs.map((item) => projectAutomationRun(item, modelScope(scope))),
      };
    },
    options,
  );
export const fetchAutomationRuleRevisions = (
  scope: AutomationApiScope,
  ruleId: string,
  list: AutomationListQuery = {},
  options: AutomationRequestOptions = {},
) =>
  read(
    scope,
    `/api/enterprise/automations/rules/${encodeURIComponent(id(ruleId, "ruleId"))}/revisions${query(list)}`,
    (raw) => page(raw, modelScope(scope), projectAutomationRuleRevision),
    options,
  );
export const fetchAutomationRuns = (
  scope: AutomationApiScope,
  list: AutomationListQuery = {},
  options: AutomationRequestOptions = {},
) =>
  read(
    scope,
    `/api/enterprise/automations/runs${query(list)}`,
    (raw) => page(raw, modelScope(scope), projectAutomationRun),
    options,
  );
export const fetchAutomationRun = (
  scope: AutomationApiScope,
  runId: string,
  options: AutomationRequestOptions = {},
) =>
  read(
    scope,
    `/api/enterprise/automations/runs/${encodeURIComponent(id(runId, "runId"))}`,
    (raw) => {
      const s = exact(raw, "run_detail", ["run", "action_requests", "events"]);
      if (!Array.isArray(s.action_requests) || !Array.isArray(s.events))
        throw new Error("run detail invalid");
      const eventPage = projectAutomationEventPage(
        { items: s.events, next_cursor: null, invalid_item_count: 0 },
        modelScope(scope),
      );
      return {
        run: projectAutomationRun(s.run, modelScope(scope)),
        action_requests: s.action_requests.map((item) =>
          projectAutomationActionRequest(item, modelScope(scope)),
        ),
        events: eventPage.items,
      };
    },
    options,
  );
export const fetchAutomationActionRequests = (
  scope: AutomationApiScope,
  list: AutomationListQuery = {},
  options: AutomationRequestOptions = {},
) =>
  read(
    scope,
    `/api/enterprise/automations/action-requests${query(list)}`,
    (raw) => page(raw, modelScope(scope), projectAutomationActionRequest),
    options,
  );
export const fetchAutomationEvents = (
  scope: AutomationApiScope,
  list: AutomationListQuery = {},
  options: AutomationRequestOptions = {},
) =>
  read(
    scope,
    `/api/enterprise/automations/events${query(list)}`,
    (raw) => projectAutomationEventPage(raw, modelScope(scope)),
    options,
  );
export function createAutomationRule(
  scope: AutomationApiScope,
  input: CreateAutomationRuleInput,
  options?: AutomationRequestOptions,
) {
  const body = {
    name: text(input.name, "name", 128),
    priority: integer(input.priority, "priority", 0, 1000),
    ...(input.workspaceId ? { workspace_id: id(input.workspaceId, "workspaceId") } : {}),
    ...(input.datasetId ? { dataset_id: id(input.datasetId, "datasetId") } : {}),
    ...definitionRequestBody(input.definition, modelScope(scope)),
    reason: text(input.reason, "reason"),
  };
  return mutate(scope, "/api/enterprise/automations/rules", body, options ?? {});
}
export function createAutomationRuleRevision(
  scope: AutomationApiScope,
  ruleId: string,
  input: CreateAutomationRevisionInput,
  options?: AutomationRequestOptions,
) {
  return mutate(
    scope,
    `/api/enterprise/automations/rules/${encodeURIComponent(id(ruleId, "ruleId"))}/revisions`,
    {
      expected_revision: integer(input.expectedRevision, "expectedRevision", 1),
      expected_definition_digest: digest(
        input.expectedDefinitionDigest,
        "expectedDefinitionDigest",
      ),
      ...definitionRequestBody(input.definition, modelScope(scope)),
      reason: text(input.reason, "reason"),
    },
    options ?? {},
  );
}
export function previewAutomationRule(
  scope: AutomationApiScope,
  ruleId: string,
  input: PreviewAutomationRuleInput,
  options?: AutomationRequestOptions,
) {
  return mutate(
    scope,
    `/api/enterprise/automations/rules/${encodeURIComponent(id(ruleId, "ruleId"))}/preview`,
    {
      expected_revision: integer(input.expectedRevision, "expectedRevision", 1),
      expected_definition_digest: digest(
        input.expectedDefinitionDigest,
        "expectedDefinitionDigest",
      ),
      trigger_event: object(input.triggerEvent, "triggerEvent"),
      reason: text(input.reason, "reason"),
    },
    options ?? {},
  );
}
export function activateAutomationRule(
  scope: AutomationApiScope,
  ruleId: string,
  input: ActivateAutomationRuleInput,
  options?: AutomationRequestOptions,
) {
  return mutate(
    scope,
    `/api/enterprise/automations/rules/${encodeURIComponent(id(ruleId, "ruleId"))}/activate`,
    {
      revision_id: id(input.revisionId, "revisionId"),
      expected_revision: integer(input.expectedRevision, "expectedRevision", 1),
      expected_definition_digest: digest(
        input.expectedDefinitionDigest,
        "expectedDefinitionDigest",
      ),
      reason: text(input.reason, "reason"),
    },
    options ?? {},
  );
}
export function pauseAutomationRule(
  scope: AutomationApiScope,
  ruleId: string,
  input: PauseAutomationRuleInput,
  options?: AutomationRequestOptions,
) {
  return mutate(
    scope,
    `/api/enterprise/automations/rules/${encodeURIComponent(id(ruleId, "ruleId"))}/pause`,
    {
      expected_revision: integer(input.expectedRevision, "expectedRevision", 1),
      expected_definition_digest: digest(
        input.expectedDefinitionDigest,
        "expectedDefinitionDigest",
      ),
      reason: text(input.reason, "reason"),
    },
    options ?? {},
  );
}
export function createAutomationIdempotencyKey(): string {
  const c = globalThis.crypto;
  if (c?.randomUUID) return `rag4c-automation-${c.randomUUID()}`;
  return `rag4c-automation-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}
