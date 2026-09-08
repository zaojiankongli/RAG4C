import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import { createSource, disableSource, enableSource, fetchSources, patchSource, syncSourceNow } from "../api/sourcesApi";
import type { RunAccepted, SourceCreate, SourcePatch, SourceRecord, SourceScope, SourceSyncRequest } from "../model/sourceModels";
import { projectSourceError, type SourceErrorView } from "../model/sourceProjection";
import { clearSyncIntent, getOrCreateSyncIntent } from "./syncIntentStore";

export type SourceControlStatus = "scope" | "offline" | "loading" | "ready" | "error";
function aborted(value: unknown): boolean { return value instanceof ApiError && value.kind === "aborted"; }
export function sourceScopeKey(scope: SourceScope | null): string { return scope ? `${scope.tenantId}\0${scope.datasetId}\0${scope.actorToken}` : ""; }

export function useSourceControl(scope: SourceScope | null, online: boolean | null) {
  const key = sourceScopeKey(scope);
  const keyRef = useRef(key); keyRef.current = key;
  const [sources, setSources] = useState<SourceRecord[]>([]);
  const [sourcesKey, setSourcesKey] = useState("");
  const [status, setStatus] = useState<SourceControlStatus>("loading");
  const [error, setError] = useState<SourceErrorView | null>(null);
  const [errorKey, setErrorKey] = useState("");
  const [mutatingId, setMutatingId] = useState<string | null>(null);
  const [lastAccepted, setLastAccepted] = useState<RunAccepted | null>(null);
  const [acceptedKey, setAcceptedKey] = useState("");
  const controllerRef = useRef<AbortController | null>(null);
  const mutationRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async (preserveError = false): Promise<boolean> => {
    const current = scope; const requestKey = key;
    if (!current) { setSources([]); setSourcesKey(""); setStatus("scope"); return false; }
    if (online !== true) { setSources([]); setSourcesKey(requestKey); setStatus(online === false ? "offline" : "loading"); return false; }
    controllerRef.current?.abort(); const controller = new AbortController(); controllerRef.current = controller;
    setStatus("loading"); if (!preserveError) { setError(null); setErrorKey(""); }
    try {
      const response = await fetchSources(current, undefined, { signal: controller.signal });
      if (controller.signal.aborted || keyRef.current !== requestKey) return false;
      setSources(response.items); setSourcesKey(requestKey); setStatus("ready"); return true;
    } catch (caught) {
      if (controller.signal.aborted || keyRef.current !== requestKey || aborted(caught)) return false;
      setSources([]); setSourcesKey(requestKey); setStatus("error");
      if (!preserveError) { setError(projectSourceError(caught)); setErrorKey(requestKey); }
      return false;
    } finally { if (controllerRef.current === controller) controllerRef.current = null; }
  }, [key, online, scope]);

  useEffect(() => { void refresh(); return () => { controllerRef.current?.abort(); mutationRef.current?.abort(); }; }, [refresh]);

  const mutate = useCallback(async (id: string, operation: (signal: AbortSignal) => Promise<unknown>): Promise<boolean> => {
    if (!scope || online !== true || mutationRef.current) return false;
    const requestKey = key; const controller = new AbortController(); mutationRef.current = controller; setMutatingId(id); setError(null); setErrorKey("");
    try { await operation(controller.signal); if (controller.signal.aborted || keyRef.current !== requestKey) return false; return await refresh(false); }
    catch (caught) { if (controller.signal.aborted || keyRef.current !== requestKey || aborted(caught)) return false; const view = projectSourceError(caught); setError(view); setErrorKey(requestKey); if (view.kind === "conflict") await refresh(true); return false; }
    finally { if (mutationRef.current === controller) mutationRef.current = null; if (keyRef.current === requestKey) setMutatingId(null); }
  }, [key, online, refresh, scope]);

  const create = useCallback((payload: SourceCreate) => mutate("new", (signal) => createSource(scope!, payload, { signal })), [mutate, scope]);
  const update = useCallback((source: SourceRecord, values: Omit<SourcePatch, "expected_generation">) => mutate(source.id, (signal) => patchSource(scope!, source.id, { expected_generation: source.generation, ...values }, { signal })), [mutate, scope]);
  const setEnabled = useCallback((source: SourceRecord, enabled: boolean) => mutate(source.id, (signal) => enabled ? enableSource(scope!, source.id, source.generation, { signal }) : disableSource(scope!, source.id, source.generation, { signal })), [mutate, scope]);
  const sync = useCallback(async (source: SourceRecord, payload: SourceSyncRequest): Promise<RunAccepted | null> => {
    if (!scope || online !== true || mutationRef.current) return null;
    const requestKey = key; const controller = new AbortController(); mutationRef.current = controller; setMutatingId(source.id); setError(null); setErrorKey("");
    const intent = getOrCreateSyncIntent(scope, source.id, source.generation, payload);
    try {
      const accepted = await syncSourceNow(scope, source.id, payload, intent.idempotencyKey, { signal: controller.signal });
      if (controller.signal.aborted || keyRef.current !== requestKey) return null;
      clearSyncIntent(intent.identity); setLastAccepted(accepted); setAcceptedKey(requestKey); await refresh(false); return accepted;
    } catch (caught) { if (!controller.signal.aborted && keyRef.current === requestKey && !aborted(caught)) { setError(projectSourceError(caught)); setErrorKey(requestKey); } return null; }
    finally { if (mutationRef.current === controller) mutationRef.current = null; if (keyRef.current === requestKey) setMutatingId(null); }
  }, [key, online, refresh, scope]);

  const visibleSources = useMemo(() => sourcesKey === key ? sources : [], [key, sources, sourcesKey]);
  const visibleStatus: SourceControlStatus = !scope ? "scope" : online === false ? "offline" : sourcesKey === key ? status : "loading";
  return useMemo(() => ({ sources: visibleSources, status: visibleStatus, error: errorKey === key ? error : null, mutatingId: sourcesKey === key ? mutatingId : null, lastAccepted: acceptedKey === key ? lastAccepted : null, refresh, create, update, setEnabled, sync, clearAccepted: () => { setLastAccepted(null); setAcceptedKey(""); } }), [acceptedKey, create, error, errorKey, key, lastAccepted, mutatingId, refresh, setEnabled, sourcesKey, sync, update, visibleSources, visibleStatus]);
}
