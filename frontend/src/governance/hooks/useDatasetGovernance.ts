import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import { archiveDataset, disableDataset, fetchDatasetProfile, patchDatasetProfile, restoreDataset } from "../api/governanceApi";
import { GovernanceScopeError, projectGovernanceError, type DatasetProfile, type DatasetProfilePatch, type DatasetRevisionRequest, type GovernanceErrorView, type GovernanceScope } from "../model/governanceModel";
import { useStableGovernanceScope } from "./useStableGovernanceScope";

export type GovernanceLoadStatus = "scope" | "offline" | "loading" | "ready" | "error";
const aborted = (e: unknown) => e instanceof ApiError && e.kind === "aborted";

export function useDatasetGovernance(inputScope: GovernanceScope | null, online: boolean | null) {
  const { scope, key } = useStableGovernanceScope(inputScope);
  const [status, setStatus] = useState<GovernanceLoadStatus>(!scope ? "scope" : "loading");
  const [profile, setProfile] = useState<DatasetProfile | null>(null);
  const [profileScopeKey, setProfileScopeKey] = useState("");
  const [error, setError] = useState<GovernanceErrorView | null>(null);
  const [mutating, setMutating] = useState(false);
  const profileRef = useRef<DatasetProfile | null>(null); profileRef.current = profile;
  const keyRef = useRef(key); const epochRef = useRef(0);
  if (keyRef.current !== key) { keyRef.current = key; epochRef.current += 1; }
  const loadRef = useRef<AbortController | null>(null);
  const mutationRef = useRef<AbortController | null>(null);
  const loadSeq = useRef(0);
  const current = useCallback((capturedKey: string, epoch: number) => keyRef.current === capturedKey && epochRef.current === epoch, []);

  const load = useCallback(async (preserveError = false): Promise<boolean> => {
    const capturedKey = key; const epoch = epochRef.current; const capturedScope = scope;
    if (!capturedScope) { setStatus("scope"); setProfile(null); setProfileScopeKey(""); profileRef.current = null; setError(projectGovernanceError(new GovernanceScopeError())); return false; }
    if (online !== true) { setStatus(online === false ? "offline" : "loading"); setProfile(null); setProfileScopeKey(""); profileRef.current = null; if (!preserveError) setError(online === false ? { kind:"offline", title:"知识治理服务未连接", description:"当前无法读取权威治理事实，请恢复连接后重试。", canRetry:true } : null); return false; }
    loadRef.current?.abort(); const controller = new AbortController(); loadRef.current = controller; const seq = ++loadSeq.current;
    setStatus("loading"); if (!preserveError) setError(null);
    try {
      const next = await fetchDatasetProfile(capturedScope, { signal: controller.signal });
      if (controller.signal.aborted || seq !== loadSeq.current || !current(capturedKey, epoch)) return false;
      setProfile(next); setProfileScopeKey(capturedKey); profileRef.current = next; setStatus("ready"); return true;
    } catch (caught) {
      if (controller.signal.aborted || !current(capturedKey, epoch) || aborted(caught)) return false;
      setProfile(null); setProfileScopeKey(""); profileRef.current = null; setStatus("error"); if (!preserveError) setError(projectGovernanceError(caught)); return false;
    }
  }, [current, key, online, scope]);

  useEffect(() => {
    loadRef.current?.abort(); mutationRef.current?.abort(); setMutating(false); setProfile(null); setProfileScopeKey(""); profileRef.current = null; setError(null);
    void load();
    return () => { loadRef.current?.abort(); mutationRef.current?.abort(); };
  }, [key, online, load]);

  const mutate = useCallback(async (allowed: (p: DatasetProfile) => boolean, operation: (s: GovernanceScope, signal: AbortSignal) => Promise<DatasetProfile>): Promise<boolean> => {
    const capturedScope = scope; const capturedKey = key; const epoch = epochRef.current; const existing = profileRef.current;
    if (!capturedScope || online !== true || !existing || !allowed(existing) || mutationRef.current) return false;
    const controller = new AbortController(); mutationRef.current = controller; setMutating(true); setError(null);
    try {
      const confirmed = await operation(capturedScope, controller.signal);
      if (controller.signal.aborted || !current(capturedKey, epoch)) return false;
      setProfile(confirmed); setProfileScopeKey(capturedKey); profileRef.current = confirmed; setStatus("ready");
      if (confirmed.status === "disabled") return true;
      return await load(false);
    } catch (caught) {
      if (controller.signal.aborted || !current(capturedKey, epoch) || aborted(caught)) return false;
      const view = projectGovernanceError(caught); setError(view);
      if (view.kind === "conflict") await load(true);
      return false;
    } finally {
      if (mutationRef.current === controller) mutationRef.current = null;
      if (current(capturedKey, epoch)) setMutating(false);
    }
  }, [current, key, load, online, scope]);

  return {
    status, profile: profileScopeKey === key ? profile : null, error, mutating, refresh: () => load(false),
    update: (p: DatasetProfilePatch) => mutate((x) => x.status === "active", (s, signal) => patchDatasetProfile(s, p, { signal })),
    archive: (p: DatasetRevisionRequest) => mutate((x) => x.status === "active", (s, signal) => archiveDataset(s, p, { signal })),
    restore: (p: DatasetRevisionRequest) => mutate((x) => x.status === "archived", (s, signal) => restoreDataset(s, p, { signal })),
    disable: (p: DatasetRevisionRequest) => mutate((x) => x.status === "active", (s, signal) => disableDataset(s, p, { signal })),
  };
}
