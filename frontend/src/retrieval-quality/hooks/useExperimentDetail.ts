import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import { fetchAgreement, fetchExperiment } from "../api/retrievalQualityApi";
import type { Agreement, ExperimentDetail, RetrievalScope } from "../model/contracts";
import { retrievalScopeKey } from "./scope";

type DetailStatus = "idle" | "loading" | "ready" | "error";
interface State { key: string; detail: ExperimentDetail | null; agreement: Agreement | null; status: DetailStatus; error: Error | null; }
const EMPTY: State = { key: "", detail: null, agreement: null, status: "idle", error: null };
function errorOf(value: unknown): Error { return value instanceof Error ? value : new Error(String(value)); }
function aborted(value: unknown): boolean { return value instanceof ApiError && value.kind === "aborted"; }

export function useExperimentDetail(scope: RetrievalScope | null, experimentId: string | null) {
  const scopeKey = retrievalScopeKey(scope); const selectionKey = scopeKey && experimentId ? `${scopeKey}\u0000${experimentId}` : "";
  const keyRef = useRef(selectionKey); keyRef.current = selectionKey;
  const requestRef = useRef<AbortController | null>(null); const sequenceRef = useRef(0);
  const [state, setState] = useState<State>(EMPTY);

  const refresh = useCallback(async (): Promise<boolean> => {
    if (!scope || !experimentId || !selectionKey) return false;
    requestRef.current?.abort(); const controller = new AbortController(); requestRef.current = controller; const sequence = ++sequenceRef.current;
    setState({ key: selectionKey, detail: null, agreement: null, status: "loading", error: null });
    const detailPromise = fetchExperiment(scope, experimentId, { signal: controller.signal });
    const agreementPromise = fetchAgreement(scope, experimentId, { signal: controller.signal });
    const [detailResult, agreementResult] = await Promise.allSettled([detailPromise, agreementPromise]);
    if (controller.signal.aborted || keyRef.current !== selectionKey || sequenceRef.current !== sequence) return false;
    if (detailResult.status === "rejected") {
      if (!aborted(detailResult.reason)) setState({ key: selectionKey, detail: null, agreement: null, status: "error", error: errorOf(detailResult.reason) });
      return false;
    }
    const agreement = agreementResult.status === "fulfilled" ? agreementResult.value : null;
    const error = agreementResult.status === "rejected" && !aborted(agreementResult.reason) ? errorOf(agreementResult.reason) : null;
    setState({ key: selectionKey, detail: detailResult.value, agreement, status: "ready", error });
    return true;
  }, [experimentId, scope, selectionKey]);

  useEffect(() => {
    requestRef.current?.abort(); requestRef.current = null; sequenceRef.current += 1;
    setState(selectionKey ? { key: selectionKey, detail: null, agreement: null, status: "loading", error: null } : EMPTY);
    if (selectionKey) void refresh();
    return () => { requestRef.current?.abort(); requestRef.current = null; };
  }, [refresh, selectionKey]);

  const visible = state.key === selectionKey;
  return { detail: visible ? state.detail : null, agreement: visible ? state.agreement : null, status: !selectionKey ? "idle" as const : visible ? state.status : "loading" as const, error: visible ? state.error : null, refresh };
}
