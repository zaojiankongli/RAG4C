import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import { runRetrievalComparison } from "../api/retrievalQualityApi";
import type { RetrievalScope, RunResponse, RunRetrievalRequest } from "../model/contracts";
import { retrievalScopeKey } from "./scope";

type RunStatus = "scope-error" | "idle" | "running" | "success" | "error";
interface Snapshot { key: string; status: RunStatus; response: RunResponse | null; error: Error | null; }
interface ActiveRun { id: number; key: string; controller: AbortController; }
const EMPTY: Snapshot = { key: "", status: "idle", response: null, error: null };
function errorOf(value: unknown): Error { return value instanceof Error ? value : new Error(String(value)); }
function aborted(value: unknown): boolean { return value instanceof ApiError && value.kind === "aborted"; }

export function useRetrievalRun(scope: RetrievalScope | null, onPendingChange?: (pending: boolean) => void) {
  const key = retrievalScopeKey(scope);
  const keyRef = useRef(key); keyRef.current = key;
  const callbackRef = useRef(onPendingChange); callbackRef.current = onPendingChange;
  const sequenceRef = useRef(0);
  const activeRef = useRef<ActiveRun | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot>(EMPTY);

  useEffect(() => {
    activeRef.current?.controller.abort(); activeRef.current = null;
    callbackRef.current?.(false);
    setSnapshot(key ? { key, status: "idle", response: null, error: null } : { key: "", status: "scope-error", response: null, error: null });
    return () => { activeRef.current?.controller.abort(); activeRef.current = null; callbackRef.current?.(false); };
  }, [key]);

  const run = useCallback(async (payload: RunRetrievalRequest): Promise<boolean> => {
    if (!scope || !key) return false;
    activeRef.current?.controller.abort();
    const controller = new AbortController();
    const id = ++sequenceRef.current;
    activeRef.current = { id, key, controller };
    setSnapshot({ key, status: "running", response: null, error: null });
    callbackRef.current?.(true);
    try {
      const response = await runRetrievalComparison(scope, payload, { signal: controller.signal });
      if (controller.signal.aborted || keyRef.current !== key || activeRef.current?.id !== id) return false;
      setSnapshot({ key, status: "success", response, error: null });
      return true;
    } catch (caught) {
      if (controller.signal.aborted || aborted(caught) || keyRef.current !== key || activeRef.current?.id !== id) return false;
      setSnapshot({ key, status: "error", response: null, error: errorOf(caught) });
      return false;
    } finally {
      if (activeRef.current?.id === id) {
        activeRef.current = null;
        callbackRef.current?.(false);
      }
    }
  }, [key, scope]);

  const reset = useCallback(() => {
    activeRef.current?.controller.abort(); activeRef.current = null; callbackRef.current?.(false);
    setSnapshot(key ? { key, status: "idle", response: null, error: null } : { key: "", status: "scope-error", response: null, error: null });
  }, [key]);

  const visible = snapshot.key === key;
  return {
    status: !key ? "scope-error" as const : visible ? snapshot.status : "idle" as const,
    response: visible ? snapshot.response : null,
    error: visible ? snapshot.error : null,
    pending: visible && snapshot.status === "running",
    run,
    reset,
  };
}
