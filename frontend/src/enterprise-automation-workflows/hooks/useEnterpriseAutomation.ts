import { useCallback, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  activateAutomationRule,
  createAutomationIdempotencyKey,
  createAutomationRule,
  createAutomationRuleRevision,
  fetchAutomationActionRequests,
  fetchAutomationEvents,
  fetchAutomationRule,
  fetchAutomationRuleRevisions,
  fetchAutomationRules,
  fetchAutomationRun,
  fetchAutomationRuns,
  fetchAutomationSummary,
  pauseAutomationRule,
  previewAutomationRule,
  type AutomationApiScope,
  type AutomationListQuery,
  type AutomationMutationOutcome,
  type AutomationRequestOptions,
  type AutomationRuleDetail,
} from "../api/automationApi";
import type {
  AutomationActionRequest,
  AutomationEvent,
  AutomationPage,
  AutomationRule,
  AutomationRuleRevision,
  AutomationRun,
  AutomationSummary,
} from "../model/automationModel";
import type {
  AutomationRuleBuilderInput,
  AutomationRevisionBuilderInput,
} from "../components/types";

export type AutomationLoadStatus =
  "idle" | "loading" | "ready" | "partial" | "empty" | "unavailable" | "error";
export interface AutomationApi {
  fetchSummary: typeof fetchAutomationSummary;
  fetchRules: typeof fetchAutomationRules;
  fetchRule: typeof fetchAutomationRule;
  fetchRevisions: typeof fetchAutomationRuleRevisions;
  fetchRuns: typeof fetchAutomationRuns;
  fetchRun: typeof fetchAutomationRun;
  fetchRequests: typeof fetchAutomationActionRequests;
  fetchEvents: typeof fetchAutomationEvents;
  createRule: typeof createAutomationRule;
  createRevision: typeof createAutomationRuleRevision;
  previewRule: typeof previewAutomationRule;
  activateRule: typeof activateAutomationRule;
  pauseRule: typeof pauseAutomationRule;
}
const defaultApi: AutomationApi = {
  fetchSummary: fetchAutomationSummary,
  fetchRules: fetchAutomationRules,
  fetchRule: fetchAutomationRule,
  fetchRevisions: fetchAutomationRuleRevisions,
  fetchRuns: fetchAutomationRuns,
  fetchRun: fetchAutomationRun,
  fetchRequests: fetchAutomationActionRequests,
  fetchEvents: fetchAutomationEvents,
  createRule: createAutomationRule,
  createRevision: createAutomationRuleRevision,
  previewRule: previewAutomationRule,
  activateRule: activateAutomationRule,
  pauseRule: pauseAutomationRule,
};
export interface UseEnterpriseAutomationOptions {
  enabled: boolean;
  readOnly?: boolean;
  api?: AutomationApi;
  ruleQuery?: AutomationListQuery;
}
interface Collection<T> {
  status: AutomationLoadStatus;
  items: T[];
  nextCursor: string | null;
  invalidItemCount: number;
  error: Error | null;
  load: () => Promise<boolean>;
  reload: () => Promise<boolean>;
}
export interface EnterpriseAutomationHook {
  active: boolean;
  readOnly: boolean;
  load: { status: AutomationLoadStatus; error: Error | null; reload: () => Promise<boolean> };
  summary: { status: AutomationLoadStatus; value: AutomationSummary | null; error: Error | null };
  rules: Collection<AutomationRule>;
  revisions: Collection<AutomationRuleRevision>;
  runs: Collection<AutomationRun>;
  requests: Collection<AutomationActionRequest>;
  activity: Collection<AutomationEvent>;
  detail: {
    status: AutomationLoadStatus;
    value:
      | (AutomationRuleDetail & { revisions: AutomationRuleRevision[]; events: AutomationEvent[] })
      | null;
    error: Error | null;
    load: (id: string) => Promise<boolean>;
  };
  mutation: {
    status: "idle" | "saving" | "success" | "error";
    error: Error | null;
    outcome: AutomationMutationOutcome | null;
    createRule: (input: AutomationRuleBuilderInput) => Promise<AutomationMutationOutcome | null>;
    createRevision: (
      rule: AutomationRule,
      input: AutomationRevisionBuilderInput,
    ) => Promise<AutomationMutationOutcome | null>;
    activate: (rule: AutomationRule) => Promise<AutomationMutationOutcome | null>;
    pause: (rule: AutomationRule) => Promise<AutomationMutationOutcome | null>;
  };
}
const err = (value: unknown, fallback: string) =>
  value instanceof Error ? value : new Error(fallback);
const statusOf = <T>(page: AutomationPage<T>): AutomationLoadStatus =>
  page.items.length === 0 && page.invalid_item_count === 0
    ? "empty"
    : page.invalid_item_count > 0
      ? "partial"
      : "ready";

interface RequestContext {
  generation: number;
  controller: AbortController;
}

function emptyCollection<T>() {
  return {
    page: null as AutomationPage<T> | null,
    status: "idle" as AutomationLoadStatus,
    error: null as Error | null,
  };
}

export function useEnterpriseAutomation(
  scope: AutomationApiScope,
  options: UseEnterpriseAutomationOptions,
): EnterpriseAutomationHook {
  const active = options.enabled,
    readOnly = Boolean(options.readOnly),
    api = options.api ?? defaultApi;
  const generation = useRef(0),
    mounted = useRef(false),
    controllers = useRef(new Set<AbortController>()),
    mutationQueue = useRef<Promise<unknown>>(Promise.resolve());
  const [loadStatus, setLoadStatus] = useState<AutomationLoadStatus>("idle"),
    [loadError, setLoadError] = useState<Error | null>(null);
  const [summary, setSummary] = useState<AutomationSummary | null>(null),
    [summaryStatus, setSummaryStatus] = useState<AutomationLoadStatus>("idle"),
    [summaryError, setSummaryError] = useState<Error | null>(null);
  const [rules, setRules] = useState<AutomationPage<AutomationRule> | null>(null),
    [rulesStatus, setRulesStatus] = useState<AutomationLoadStatus>("idle"),
    [rulesError, setRulesError] = useState<Error | null>(null);
  const [detail, setDetail] = useState<
      | (AutomationRuleDetail & { revisions: AutomationRuleRevision[]; events: AutomationEvent[] })
      | null
    >(null),
    [detailStatus, setDetailStatus] = useState<AutomationLoadStatus>("idle"),
    [detailError, setDetailError] = useState<Error | null>(null);
  const [revisionsState, setRevisionsState] = useState(emptyCollection<AutomationRuleRevision>()),
    [runsState, setRunsState] = useState(emptyCollection<AutomationRun>()),
    [requestsState, setRequestsState] = useState(emptyCollection<AutomationActionRequest>()),
    [activityState, setActivityState] = useState(emptyCollection<AutomationEvent>());
  const [mutationStatus, setMutationStatus] = useState<"idle" | "saving" | "success" | "error">(
      "idle",
    ),
    [mutationError, setMutationError] = useState<Error | null>(null),
    [mutationOutcome, setMutationOutcome] = useState<AutomationMutationOutcome | null>(null);
  const ruleQueryKey = JSON.stringify(options.ruleQuery ?? {});
  const contextKey = JSON.stringify({
    scope,
    active,
    readOnly,
    ruleQuery: options.ruleQuery ?? {},
  });
  const current = (g: number) => mounted.current && generation.current === g;
  const requestCurrent = (request: RequestContext) =>
    current(request.generation) && !request.controller.signal.aborted;
  const resetState = useCallback(() => {
    setLoadStatus("idle");
    setLoadError(null);
    setSummary(null);
    setSummaryStatus("idle");
    setSummaryError(null);
    setRules(null);
    setRulesStatus("idle");
    setRulesError(null);
    setDetail(null);
    setDetailStatus("idle");
    setDetailError(null);
    setRevisionsState(emptyCollection<AutomationRuleRevision>());
    setRunsState(emptyCollection<AutomationRun>());
    setRequestsState(emptyCollection<AutomationActionRequest>());
    setActivityState(emptyCollection<AutomationEvent>());
    setMutationStatus("idle");
    setMutationError(null);
    setMutationOutcome(null);
  }, []);
  const abortInflight = useCallback(() => {
    controllers.current.forEach((controller) => controller.abort());
    controllers.current.clear();
    mutationQueue.current = Promise.resolve();
  }, []);
  const reload = useCallback(async () => {
    if (!active) return false;
    const request: RequestContext = {
      generation: generation.current,
      controller: new AbortController(),
    };
    if (!requestCurrent(request)) return false;
    controllers.current.add(request.controller);
    setLoadStatus("loading");
    setSummaryStatus("loading");
    setRulesStatus("loading");
    const results = await Promise.allSettled([
      api.fetchSummary(scope, { signal: request.controller.signal }),
      api.fetchRules(scope, options.ruleQuery ?? {}, {
        signal: request.controller.signal,
      }),
    ]);
    controllers.current.delete(request.controller);
    if (!requestCurrent(request)) return false;
    let ok = 0;
    if (results[0].status === "fulfilled") {
      setSummary(results[0].value);
      setSummaryStatus(results[0].value.state === "unavailable" ? "unavailable" : "ready");
      setSummaryError(null);
      ok++;
    } else {
      const e = err(results[0].reason, "summary unavailable");
      setSummaryError(e);
      setSummaryStatus("error");
    }
    if (results[1].status === "fulfilled") {
      const rulePage =
        options.ruleQuery?.status === undefined
          ? {
              ...results[1].value,
              items: results[1].value.items.filter((item) => item.status !== "archived"),
            }
          : results[1].value;
      setRules(rulePage);
      setRulesStatus(statusOf(rulePage));
      setRulesError(null);
      ok++;
    } else {
      const e = err(results[1].reason, "rules unavailable");
      setRulesError(e);
      setRulesStatus("error");
    }
    setLoadStatus(ok === 2 ? "ready" : ok ? "partial" : "error");
    setLoadError(
      results.find((r) => r.status === "rejected")?.status === "rejected"
        ? err(
            (results.find((r) => r.status === "rejected") as PromiseRejectedResult).reason,
            "authority unavailable",
          )
        : null,
    );
    return ok > 0;
  }, [active, api, contextKey, ruleQueryKey]);
  const loadDetail = useCallback(
    async (ruleId: string) => {
      if (!active) return false;
      const request: RequestContext = {
        generation: generation.current,
        controller: new AbortController(),
      };
      if (!requestCurrent(request)) return false;
      controllers.current.add(request.controller);
      setDetailStatus("loading");
      const results = await Promise.allSettled([
        api.fetchRule(scope, ruleId, { signal: request.controller.signal }),
        api.fetchRevisions(scope, ruleId, { limit: 50 }, { signal: request.controller.signal }),
        api.fetchEvents(scope, { ruleId, limit: 100 }, { signal: request.controller.signal }),
      ]);
      controllers.current.delete(request.controller);
      if (!requestCurrent(request)) return false;
      if (results.every((r) => r.status === "fulfilled")) {
        const d = (results[0] as PromiseFulfilledResult<AutomationRuleDetail>).value;
        const rev = (results[1] as PromiseFulfilledResult<AutomationPage<AutomationRuleRevision>>)
          .value;
        const ev = (results[2] as PromiseFulfilledResult<AutomationPage<AutomationEvent>>).value;
        setDetail({ ...d, revisions: rev.items, events: ev.items });
        setDetailStatus(rev.invalid_item_count || ev.invalid_item_count ? "partial" : "ready");
        setDetailError(null);
        setRevisionsState({ page: rev, status: statusOf(rev), error: null });
        return true;
      }
      const e = err(
        (results.find((r) => r.status === "rejected") as PromiseRejectedResult)?.reason,
        "detail unavailable",
      );
      setDetailError(e);
      setDetailStatus("error");
      return false;
    },
    [active, api, contextKey],
  );
  const lazy =
    <T>(
      fetcher: (signal: AbortSignal) => Promise<AutomationPage<T>>,
      setter: React.Dispatch<
        React.SetStateAction<{
          page: AutomationPage<T> | null;
          status: AutomationLoadStatus;
          error: Error | null;
        }>
      >,
    ) =>
    async () => {
      if (!active) return false;
      const request: RequestContext = {
        generation: generation.current,
        controller: new AbortController(),
      };
      if (!requestCurrent(request)) return false;
      controllers.current.add(request.controller);
      setter((s) => ({ ...s, status: "loading", error: null }));
      try {
        const page = await fetcher(request.controller.signal);
        if (!requestCurrent(request)) return false;
        setter({ page, status: statusOf(page), error: null });
        return true;
      } catch (e) {
        if (!requestCurrent(request)) return false;
        setter({ page: null, status: "error", error: err(e, "unavailable") });
        return false;
      } finally {
        controllers.current.delete(request.controller);
      }
    };
  const loadRuns = useCallback(
    () => lazy((signal) => api.fetchRuns(scope, { limit: 50 }, { signal }), setRunsState)(),
    [active, api, contextKey],
  );
  const loadRequests = useCallback(
    () =>
      lazy(
        (signal) => api.fetchRequests(scope, { status: "requested", limit: 50 }, { signal }),
        setRequestsState,
      )(),
    [active, api, contextKey],
  );
  const loadActivity = useCallback(
    () => lazy((signal) => api.fetchEvents(scope, { limit: 100 }, { signal }), setActivityState)(),
    [active, api, contextKey],
  );
  const authorityFor = (rule: AutomationRule) =>
    detail?.rule.id === rule.id
      ? detail.current_revision
      : (revisionsState.page?.items.find((revision) => revision.id === rule.current_revision_id) ??
        null);
  const resolveAuthority = async (
    rule: AutomationRule,
    submittedGeneration: number,
  ): Promise<AutomationRuleRevision | null> => {
    if (!current(submittedGeneration)) return null;
    const currentAuthority = authorityFor(rule);
    if (currentAuthority) return currentAuthority;
    const request: RequestContext = {
      generation: submittedGeneration,
      controller: new AbortController(),
    };
    if (!requestCurrent(request)) return null;
    controllers.current.add(request.controller);
    try {
      const authority = await api.fetchRule(scope, rule.id, {
        signal: request.controller.signal,
      });
      if (!requestCurrent(request)) return null;
      return authority.current_revision ?? null;
    } catch {
      return null;
    } finally {
      controllers.current.delete(request.controller);
    }
  };
  const runMutation = useCallback(
    (
      submittedGeneration: number,
      invoke: (request: AutomationRequestOptions) => Promise<AutomationMutationOutcome>,
    ) => {
      if (!active || readOnly || !current(submittedGeneration)) return Promise.resolve(null);
      const key = createAutomationIdempotencyKey();
      const task = mutationQueue.current.then(async () => {
        if (!current(submittedGeneration)) return null;
        const request: RequestContext = {
          generation: submittedGeneration,
          controller: new AbortController(),
        };
        if (!requestCurrent(request)) return null;
        controllers.current.add(request.controller);
        setMutationStatus("saving");
        setMutationError(null);
        try {
          const out = await invoke({
            idempotencyKey: key,
            signal: request.controller.signal,
          });
          if (!requestCurrent(request)) return null;
          setMutationOutcome(out);
          setMutationStatus("success");
          await reload();
          if (!requestCurrent(request)) return null;
          return out;
        } catch (e) {
          if (!requestCurrent(request)) return null;
          setMutationError(err(e, "mutation failed"));
          setMutationStatus("error");
          return null;
        } finally {
          controllers.current.delete(request.controller);
        }
      });
      mutationQueue.current = task.then(
        () => undefined,
        () => undefined,
      );
      return task;
    },
    [active, readOnly, reload],
  );
  const renderGeneration = generation.current;
  const mutations = {
    createRule: (input: AutomationRuleBuilderInput) =>
      runMutation(renderGeneration, (requestOptions) =>
        api.createRule(scope, input, requestOptions),
      ),
    createRevision: async (rule: AutomationRule, input: AutomationRevisionBuilderInput) => {
      const authority = await resolveAuthority(rule, renderGeneration);
      return authority
        ? runMutation(renderGeneration, (requestOptions) =>
            api.createRevision(
              scope,
              rule.id,
              { ...input, expectedDefinitionDigest: authority.definition_digest },
              requestOptions,
            ),
          )
        : null;
    },
    activate: async (rule: AutomationRule) => {
      const authority = await resolveAuthority(rule, renderGeneration);
      return authority
        ? runMutation(renderGeneration, (requestOptions) =>
            api.activateRule(
              scope,
              rule.id,
              {
                revisionId: authority.id,
                expectedRevision: rule.revision,
                expectedDefinitionDigest: authority.definition_digest,
                reason: "Automation Center explicit activation",
              },
              requestOptions,
            ),
          )
        : null;
    },
    pause: async (rule: AutomationRule) => {
      const authority = await resolveAuthority(rule, renderGeneration);
      return authority
        ? runMutation(renderGeneration, (requestOptions) =>
            api.pauseRule(
              scope,
              rule.id,
              {
                expectedRevision: rule.revision,
                expectedDefinitionDigest: authority.definition_digest,
                reason: "Automation Center explicit pause",
              },
              requestOptions,
            ),
          )
        : null;
    },
  };
  useLayoutEffect(() => {
    mounted.current = true;
    generation.current++;
    abortInflight();
    resetState();
    if (active) void reload();
    return () => {
      mounted.current = false;
      generation.current++;
      abortInflight();
    };
  }, [abortInflight, active, contextKey, reload, resetState]);
  const collection = <T>(
    state: { page: AutomationPage<T> | null; status: AutomationLoadStatus; error: Error | null },
    load: () => Promise<boolean>,
  ): Collection<T> => ({
    status: state.status,
    items: state.page?.items ?? [],
    nextCursor: state.page?.next_cursor ?? null,
    invalidItemCount: state.page?.invalid_item_count ?? 0,
    error: state.error,
    load,
    reload: load,
  });
  return useMemo(
    () => ({
      active,
      readOnly,
      load: { status: loadStatus, error: loadError, reload },
      summary: { status: summaryStatus, value: summary, error: summaryError },
      rules: {
        status: rulesStatus,
        items: rules?.items ?? [],
        nextCursor: rules?.next_cursor ?? null,
        invalidItemCount: rules?.invalid_item_count ?? 0,
        error: rulesError,
        load: reload,
        reload,
      },
      revisions: collection(revisionsState, () =>
        detail?.rule.id ? loadDetail(detail.rule.id) : Promise.resolve(false),
      ),
      runs: collection(runsState, loadRuns),
      requests: collection(requestsState, loadRequests),
      activity: collection(activityState, loadActivity),
      detail: { status: detailStatus, value: detail, error: detailError, load: loadDetail },
      mutation: {
        status: mutationStatus,
        error: mutationError,
        outcome: mutationOutcome,
        ...mutations,
      },
    }),
    [
      active,
      readOnly,
      loadStatus,
      loadError,
      summary,
      summaryStatus,
      summaryError,
      rules,
      rulesStatus,
      rulesError,
      detail,
      detailStatus,
      detailError,
      revisionsState,
      runsState,
      requestsState,
      activityState,
      mutationStatus,
      mutationError,
      mutationOutcome,
    ],
  );
}
