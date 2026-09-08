import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import { fetchExperiments, hashNormalizedQuery } from "../api/retrievalQualityApi";
import type { Experiment, HistoryFilters, RetrievalScope } from "../model/contracts";
import { retrievalScopeKey } from "./scope";

type HistoryStatus = "idle" | "loading" | "ready" | "error";
interface Snapshot { key: string; items: Experiment[]; next: number | null; status: HistoryStatus; error: Error | null; }
const EMPTY: Snapshot = { key: "", items: [], next: null, status: "idle", error: null };
function errorOf(value: unknown): Error { return value instanceof Error ? value : new Error(String(value)); }
function isAbort(value: unknown): boolean { return value instanceof ApiError && value.kind === "aborted"; }

export function useExperimentHistory(scope: RetrievalScope | null, active: boolean, runPending: boolean) {
  const key = retrievalScopeKey(scope);
  const keyRef = useRef(key); keyRef.current = key;
  const scopeRef = useRef(scope); scopeRef.current = scope;
  const activeRef = useRef(active); activeRef.current = active;
  const requestRef = useRef<AbortController | null>(null);
  const sequenceRef = useRef(0);
  const filtersRef = useRef<HistoryFilters>({});
  const cursorRef = useRef<number | undefined>(undefined);
  const [snapshot, setSnapshot] = useState<Snapshot>(EMPTY);
  const [filters, setFilters] = useState<HistoryFilters>({});
  const [cursorStack, setCursorStack] = useState<Array<number | undefined>>([]);
  const [paging, setPaging] = useState(false);

  const loadPage = useCallback(async (cursor: number | undefined, nextFilters: HistoryFilters, silent = false): Promise<boolean> => {
    const currentScope = scopeRef.current; const currentKey = keyRef.current;
    if (!currentScope || !currentKey || !activeRef.current) return false;
    requestRef.current?.abort();
    const controller = new AbortController(); requestRef.current = controller;
    const sequence = ++sequenceRef.current;
    if (!silent) setSnapshot((current) => ({ ...current, key: currentKey, status: "loading", error: null }));
    try {
      const query = nextFilters.query?.trim() ?? "";
      const queryHash = query ? await hashNormalizedQuery(nextFilters.query ?? "") : undefined;
      if (controller.signal.aborted || keyRef.current !== currentKey || sequenceRef.current !== sequence) return false;
      const requestFilters: HistoryFilters = {
        status: nextFilters.status,
        runId: nextFilters.runId,
        query: nextFilters.query,
        queryHash,
        beforeSequence: cursor,
      };
      const response = await fetchExperiments(currentScope, requestFilters, { signal: controller.signal });
      if (controller.signal.aborted || keyRef.current !== currentKey || sequenceRef.current !== sequence) return false;
      setSnapshot({ key: currentKey, items: response.items, next: response.next_before_sequence, status: "ready", error: null });
      return true;
    } catch (caught) {
      if (controller.signal.aborted || isAbort(caught) || keyRef.current !== currentKey || sequenceRef.current !== sequence) return false;
      if (!silent) setSnapshot({ key: currentKey, items: [], next: null, status: "error", error: errorOf(caught) });
      return false;
    } finally {
      if (requestRef.current === controller) requestRef.current = null;
    }
  }, []);

  useEffect(() => {
    requestRef.current?.abort(); requestRef.current = null; sequenceRef.current += 1;
    filtersRef.current = {}; cursorRef.current = undefined; setFilters({}); setCursorStack([]);
    setSnapshot(key ? { key, items: [], next: null, status: active ? "loading" : "idle", error: null } : EMPTY);
    if (scope && key && active) void loadPage(undefined, {});
    return () => { requestRef.current?.abort(); requestRef.current = null; };
  }, [active, key, loadPage, scope]);

  useEffect(() => {
    if (!runPending || !active || !key) return;
    const timer = window.setInterval(() => { void loadPage(cursorRef.current, filtersRef.current, true); }, 2_000);
    return () => window.clearInterval(timer);
  }, [active, key, loadPage, runPending]);

  const applyFilters = useCallback(async (next: HistoryFilters): Promise<boolean> => {
    const normalized = { status: next.status, runId: next.runId?.trim() || undefined, query: next.query ?? "" };
    filtersRef.current = normalized; cursorRef.current = undefined;
    setFilters(normalized); setCursorStack([]); setPaging(true);
    try { return await loadPage(undefined, normalized); } finally { setPaging(false); }
  }, [loadPage]);
  const refresh = useCallback(() => loadPage(cursorRef.current, filtersRef.current), [loadPage]);
  const nextPage = useCallback(async (): Promise<boolean> => {
    if (paging || snapshot.next === null) return false;
    const target = snapshot.next; const previous = cursorRef.current; setPaging(true);
    try {
      const ok = await loadPage(target, filtersRef.current);
      if (ok) { setCursorStack((items) => [...items, previous]); cursorRef.current = target; }
      return ok;
    } finally { setPaging(false); }
  }, [loadPage, paging, snapshot.next]);
  const previousPage = useCallback(async (): Promise<boolean> => {
    if (paging || cursorStack.length === 0) return false;
    const target = cursorStack[cursorStack.length - 1]; setPaging(true);
    try {
      const ok = await loadPage(target, filtersRef.current);
      if (ok) { setCursorStack((items) => items.slice(0, -1)); cursorRef.current = target; }
      return ok;
    } finally { setPaging(false); }
  }, [cursorStack, loadPage, paging]);

  const visible = snapshot.key === key;
  return {
    items: visible ? snapshot.items : [], nextBeforeSequence: visible ? snapshot.next : null,
    status: !scope || !active ? "idle" as const : visible ? snapshot.status : "loading" as const,
    error: visible ? snapshot.error : null, filters, paging,
    hasPrevious: visible && cursorStack.length > 0,
    refresh, nextPage, previousPage, applyFilters,
  };
}
