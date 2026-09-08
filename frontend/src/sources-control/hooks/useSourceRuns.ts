import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import { fetchRun, fetchRunItems, fetchRuns, retryRun } from "../api/sourcesApi";
import type { ItemAction, ItemResult, RunAccepted, RunFilters, RunItem, RunStatus, RunTrigger, SourceRecord, SourceRun, SourceScope } from "../model/sourceModels";
import { isExecutionActive, projectSourceError, sameSourceGeneration, type SourceErrorView } from "../model/sourceProjection";

const BACKOFF = [2000, 3000, 5000, 8000, 13000, 20000] as const;
function aborted(value: unknown): boolean { return value instanceof ApiError && value.kind === "aborted"; }
interface ItemFilterState { result?: ItemResult; action?: ItemAction; }
interface RunFilterState { status?: RunStatus; trigger?: RunTrigger; }

export function useSourceRuns(scope: SourceScope | null, source: SourceRecord | null, active: boolean) {
  const key = scope && source ? `${scope.tenantId}\0${scope.datasetId}\0${scope.actorToken}\0${source.id}\0${source.generation}` : "";
  const keyRef = useRef(key); keyRef.current = key;
  const [runs, setRuns] = useState<SourceRun[]>([]); const [runsKey, setRunsKey] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "ready" | "error">("idle"); const [error, setError] = useState<SourceErrorView | null>(null);
  const [runFilters, setRunFilterState] = useState<RunFilterState>({}); const runFiltersRef = useRef(runFilters); runFiltersRef.current = runFilters;
  const [cursor, setCursor] = useState<string | null>(null); const cursorRef = useRef(cursor); cursorRef.current = cursor;
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([]); const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [paging, setPaging] = useState(false); const pagingRef = useRef(false);
  const [selectedRun, setSelectedRun] = useState<SourceRun | null>(null); const [detailKey, setDetailKey] = useState("");
  const selectedRunIdRef = useRef<string | null>(null);
  const [items, setItems] = useState<RunItem[]>([]); const itemsCursorRef = useRef<string | null>(null);
  const [itemsStack, setItemsStack] = useState<(string | null)[]>([]); const [itemsNextCursor, setItemsNextCursor] = useState<string | null>(null);
  const [itemFilters, setItemFilterState] = useState<ItemFilterState>({}); const itemFiltersRef = useRef(itemFilters); itemFiltersRef.current = itemFilters;
  const [itemsPaging, setItemsPaging] = useState(false); const itemsPagingRef = useRef(false); const [detailLoading, setDetailLoading] = useState(false);
  const [retrying, setRetrying] = useState(false); const [lastAccepted, setLastAccepted] = useState<RunAccepted | null>(null);
  const listRef = useRef<AbortController | null>(null); const detailRef = useRef<AbortController | null>(null); const retryRef = useRef<AbortController | null>(null);

  const loadPage = useCallback(async (targetCursor: string | null, filters: RunFilterState, quiet = false) => {
    if (!scope || !source || !active) return null;
    const requestKey = key; listRef.current?.abort(); const controller = new AbortController(); listRef.current = controller;
    if (!quiet) setStatus("loading");
    try {
      const requestFilters: RunFilters = { status: filters.status, trigger: filters.trigger, cursor: targetCursor, limit: 10 };
      const response = await fetchRuns(scope, source.id, requestFilters, { signal: controller.signal });
      if (controller.signal.aborted || keyRef.current !== requestKey) return null;
      setRuns(response.items); setRunsKey(requestKey); setNextCursor(response.next_cursor); setStatus("ready"); setError(null); return response;
    } catch (caught) {
      if (controller.signal.aborted || keyRef.current !== requestKey || aborted(caught)) return null;
      setStatus("error"); setError(projectSourceError(caught)); return null;
    } finally { if (listRef.current === controller) listRef.current = null; }
  }, [active, key, scope, source]);

  const loadDetail = useCallback(async (runId: string, filters: ItemFilterState, targetCursor: string | null, quiet = false) => {
    if (!scope || !source || !active) return false;
    const requestKey = key; detailRef.current?.abort(); const controller = new AbortController(); detailRef.current = controller; if (!quiet) setDetailLoading(true);
    try {
      const [detail, response] = await Promise.all([
        fetchRun(scope, source.id, runId, { signal: controller.signal }),
        fetchRunItems(scope, source.id, runId, { result: filters.result, action: filters.action, cursor: targetCursor, limit: 10 }, { signal: controller.signal }),
      ]);
      if (controller.signal.aborted || keyRef.current !== requestKey) return false;
      setSelectedRun(detail); setDetailKey(requestKey); selectedRunIdRef.current = detail.id; setItems(response.items); setItemsNextCursor(response.next_cursor); itemsCursorRef.current = targetCursor; setError(null); return true;
    } catch (caught) { if (!controller.signal.aborted && keyRef.current === requestKey && !aborted(caught)) setError(projectSourceError(caught)); return false; }
    finally { if (detailRef.current === controller) detailRef.current = null; if (keyRef.current === requestKey) setDetailLoading(false); }
  }, [active, key, scope, source]);

  const abortActive = useCallback(() => { listRef.current?.abort(); detailRef.current?.abort(); retryRef.current?.abort(); }, []);
  useEffect(() => {
    setCursor(null); cursorRef.current = null; setCursorStack([]); setNextCursor(null); setRunsKey(""); setDetailKey(""); setSelectedRun(null); selectedRunIdRef.current = null; setItems([]); setItemsStack([]); setItemsNextCursor(null); setLastAccepted(null);
    void loadPage(null, runFiltersRef.current);
    return abortActive;
  }, [abortActive, key, active, runFilters.status, runFilters.trigger, loadPage]);

  const reconcile = useCallback(async () => {
    const response = await loadPage(cursorRef.current, runFiltersRef.current, true);
    if (!response) return false;
    const selectedId = selectedRunIdRef.current;
    if (selectedId) await loadDetail(selectedId, itemFiltersRef.current, itemsCursorRef.current, true);
    return true;
  }, [loadDetail, loadPage]);

  useEffect(() => {
    if (!active || runsKey !== key || !runs.some((item) => isExecutionActive(item.execution_state))) return;
    let stopped = false; let timer: ReturnType<typeof setTimeout> | null = null; let attempt = 0;
    const schedule = () => { if (stopped || document.visibilityState !== "visible") return; timer = setTimeout(async () => { const ok = await reconcile(); attempt = ok ? 0 : Math.min(attempt + 1, BACKOFF.length - 1); schedule(); }, BACKOFF[Math.min(attempt, BACKOFF.length - 1)]); };
    const visibility = () => { if (document.visibilityState === "hidden") { if (timer) clearTimeout(timer); abortActive(); } else { if (timer) clearTimeout(timer); void reconcile().then(schedule); } };
    document.addEventListener("visibilitychange", visibility); schedule();
    return () => { stopped = true; if (timer) clearTimeout(timer); document.removeEventListener("visibilitychange", visibility); };
  }, [abortActive, active, key, reconcile, runs, runsKey]);

  const nextPage = useCallback(async () => {
    if (pagingRef.current || !nextCursor) return false; pagingRef.current = true; setPaging(true); const target = nextCursor; const previous = cursorRef.current;
    try { const response = await loadPage(target, runFiltersRef.current); if (!response) return false; setCursorStack((value) => [...value, previous]); setCursor(target); cursorRef.current = target; return true; }
    finally { pagingRef.current = false; setPaging(false); }
  }, [loadPage, nextCursor]);
  const previousPage = useCallback(async () => {
    if (pagingRef.current || !cursorStack.length) return false; pagingRef.current = true; setPaging(true); const target = cursorStack[cursorStack.length - 1] ?? null;
    try { const response = await loadPage(target, runFiltersRef.current); if (!response) return false; setCursorStack((value) => value.slice(0, -1)); setCursor(target); cursorRef.current = target; return true; }
    finally { pagingRef.current = false; setPaging(false); }
  }, [cursorStack, loadPage]);
  const setFilters = useCallback((next: RunFilterState) => { setRunFilterState(next); setCursor(null); cursorRef.current = null; setCursorStack([]); }, []);
  const selectRun = useCallback((run: SourceRun) => { setSelectedRun(run); setDetailKey(key); selectedRunIdRef.current = run.id; setItemsStack([]); return loadDetail(run.id, itemFiltersRef.current, null); }, [key, loadDetail]);
  const closeRun = useCallback(() => { detailRef.current?.abort(); setSelectedRun(null); setDetailKey(""); selectedRunIdRef.current = null; setItems([]); setItemsStack([]); }, []);
  const setItemFilters = useCallback((next: ItemFilterState) => { setItemFilterState(next); setItemsStack([]); const runId = selectedRunIdRef.current; if (runId) void loadDetail(runId, next, null); }, [loadDetail]);
  const nextItemsPage = useCallback(async () => {
    const runId = selectedRunIdRef.current; if (itemsPagingRef.current || !runId || !itemsNextCursor) return false; itemsPagingRef.current = true; setItemsPaging(true); const target = itemsNextCursor; const previous = itemsCursorRef.current;
    try { const ok = await loadDetail(runId, itemFiltersRef.current, target); if (!ok) return false; setItemsStack((value) => [...value, previous]); return true; }
    finally { itemsPagingRef.current = false; setItemsPaging(false); }
  }, [itemsNextCursor, loadDetail]);
  const previousItemsPage = useCallback(async () => {
    const runId = selectedRunIdRef.current; if (itemsPagingRef.current || !runId || !itemsStack.length) return false; itemsPagingRef.current = true; setItemsPaging(true); const target = itemsStack[itemsStack.length - 1] ?? null;
    try { const ok = await loadDetail(runId, itemFiltersRef.current, target); if (!ok) return false; setItemsStack((value) => value.slice(0, -1)); return true; }
    finally { itemsPagingRef.current = false; setItemsPaging(false); }
  }, [itemsStack, loadDetail]);
  const resetAndRefresh = useCallback(async () => { setCursor(null); cursorRef.current = null; setCursorStack([]); return Boolean(await loadPage(null, runFiltersRef.current)); }, [loadPage]);
  const retry = useCallback(async (run: SourceRun): Promise<boolean> => {
    if (!scope || !source || source.status !== "active" || retryRef.current || run.status !== "failed" || !sameSourceGeneration(run, source)) return false;
    const controller = new AbortController(); retryRef.current = controller; setRetrying(true); setError(null);
    try { const accepted = await retryRun(scope, source.id, run.id, { signal: controller.signal }); if (controller.signal.aborted || keyRef.current !== key) return false; setLastAccepted(accepted); await resetAndRefresh(); return true; }
    catch (caught) { if (!controller.signal.aborted && !aborted(caught)) { const view = projectSourceError(caught); setError(view); if (view.kind === "conflict") await resetAndRefresh(); } return false; }
    finally { if (retryRef.current === controller) retryRef.current = null; if (keyRef.current === key) setRetrying(false); }
  }, [key, resetAndRefresh, scope, source]);
  useEffect(() => abortActive, [abortActive]);

  const visible = runsKey === key; const detailVisible = detailKey === key;
  return useMemo(() => ({ runs: visible ? runs : [], status: !scope || !source || !active ? "idle" : visible ? status : "loading", error: visible ? error : null, runStatus: runFilters.status, trigger: runFilters.trigger, nextCursor: visible ? nextCursor : null, hasPrevious: visible && cursorStack.length > 0, paging, selectedRun: detailVisible ? selectedRun : null, items: detailVisible ? items : [], itemsNextCursor: detailVisible ? itemsNextCursor : null, hasPreviousItems: detailVisible && itemsStack.length > 0, itemsPaging, detailLoading, itemResult: itemFilters.result, itemAction: itemFilters.action, retrying, lastAccepted, refresh: reconcile, resetAndRefresh, nextPage, previousPage, setFilters, selectRun, closeRun, nextItemsPage, previousItemsPage, setItemFilters, retry }), [active, closeRun, cursorStack.length, detailLoading, detailVisible, error, itemFilters.action, itemFilters.result, items, itemsNextCursor, itemsPaging, itemsStack.length, lastAccepted, nextCursor, nextItemsPage, nextPage, paging, previousItemsPage, previousPage, reconcile, resetAndRefresh, retry, retrying, runFilters.status, runFilters.trigger, runs, scope, selectRun, selectedRun, setFilters, setItemFilters, source, status, visible]);
}
