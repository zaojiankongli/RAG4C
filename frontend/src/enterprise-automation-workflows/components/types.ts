import type {
  AutomationActionRequest,
  AutomationActionStep,
  AutomationConditionCode,
  AutomationEvent,
  AutomationPage,
  AutomationRule,
  AutomationRuleRevision,
  AutomationRun,
  AutomationSafeValue,
  AutomationSummary,
  AutomationTriggerCode,
} from "../model/automationModel";

export type AutomationResourceStatus =
  "idle" | "loading" | "ready" | "partial" | "empty" | "unavailable" | "error";
export type AutomationMutationStatus = "idle" | "saving" | "success" | "error";
export type AutomationCenterTab = "rules" | "runs" | "requests" | "activity";

export interface AutomationResource<T> {
  status: AutomationResourceStatus;
  value: T | null;
  error: string | null;
  load?: (id?: string) => void | Promise<unknown>;
  reload?: () => void | Promise<unknown>;
}

export interface AutomationCollection<T> {
  status: AutomationResourceStatus;
  items: T[];
  invalidItemCount: number;
  nextCursor: string | null;
  error: string | null;
  reload?: () => void | Promise<unknown>;
}

export interface AutomationRuleDetailValue {
  rule: AutomationRule;
  current_revision: AutomationRuleRevision | null;
  revisions: AutomationRuleRevision[];
  recent_runs: AutomationRun[];
  events: AutomationEvent[];
}

export interface AutomationRuleDefinitionInput {
  trigger_code: AutomationTriggerCode;
  condition_code: AutomationConditionCode;
  condition_params: Readonly<Record<string, AutomationSafeValue>>;
  action_plan: AutomationActionStep[];
}

export interface AutomationRuleBuilderInput {
  name: string;
  priority: number;
  workspaceId?: string;
  datasetId?: string;
  definition: AutomationRuleDefinitionInput;
  reason: string;
}

export interface AutomationRevisionBuilderInput {
  expectedRevision: number;
  definition: AutomationRuleDefinitionInput;
  reason: string;
}

export interface AutomationMutationOutcome {
  state: "applied" | "replayed" | "conflict" | "blocked" | "rejected" | "unavailable";
  resource_id?: string | null;
  message?: string | null;
}

export interface AutomationMutationController {
  status: AutomationMutationStatus;
  error: string | null;
  createRule?: (
    input: AutomationRuleBuilderInput,
  ) => void | Promise<AutomationMutationOutcome | unknown>;
  createRevision?: (
    rule: AutomationRule,
    input: AutomationRevisionBuilderInput,
  ) => void | Promise<AutomationMutationOutcome | unknown>;
  activateRule?: (rule: AutomationRule) => void | Promise<AutomationMutationOutcome | unknown>;
  pauseRule?: (rule: AutomationRule) => void | Promise<AutomationMutationOutcome | unknown>;
}

export interface AutomationCenterController {
  active: boolean;
  summary: AutomationResource<AutomationSummary>;
  rules: AutomationCollection<AutomationRule>;
  revisions: AutomationCollection<AutomationRuleRevision>;
  runs: AutomationCollection<AutomationRun>;
  requests: AutomationCollection<AutomationActionRequest>;
  activity: AutomationCollection<AutomationEvent>;
  detail: AutomationResource<AutomationRuleDetailValue>;
  mutation: AutomationMutationController;
}

export type AutomationPageLike<T> = AutomationPage<T>;
