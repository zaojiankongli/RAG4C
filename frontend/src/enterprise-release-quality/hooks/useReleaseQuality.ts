import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  createQualityIdempotencyKey,
  releaseQualityApi as defaultApi,
  type CertifyReleaseQualityInput,
  type CreateQualityBaselineInput,
  type QualityScope,
  type ReleaseQualityApi,
  type RequestQualityWaiverInput,
  type UpdateQualityPolicyInput,
} from "../api/qualityApi";
import type {
  QualityBaseline,
  QualityCertification,
  QualityGate,
  QualityMutationOutcome,
  QualityPolicy,
} from "../model/qualityModel";

export type QualityLoadStatus = "idle" | "loading" | "ready" | "error";
export type QualityMutationStatus = "idle" | "saving" | "success" | "error";

interface UseReleaseQualityOptions {
  enabled: boolean;
  releaseId: string | null;
  channelId: string | null;
  api?: ReleaseQualityApi;
}

type RetryAction = (signal: AbortSignal) => Promise<QualityMutationOutcome | null>;

function scopeKey(scope: QualityScope): string {
  return [scope.tenantId.trim(), scope.datasetId.trim(), scope.actorToken.trim()].join(":");
}

function mergeById<T extends { id: string }>(current: T[], incoming: T[]): T[] {
  const seen = new Set<string>();
  return [...current, ...incoming].filter((item) => {
    if (seen.has(item.id)) return false;
    seen.add(item.id);
    return true;
  });
}

export function useReleaseQuality(scope: QualityScope, options: UseReleaseQualityOptions) {
  const api = options.api ?? defaultApi;
  const key = scopeKey(scope);
  const releaseId = options.releaseId?.trim() || null;
  const channelId = options.channelId?.trim() || null;
  const active = options.enabled && Boolean(releaseId && channelId);
  const activeControllers = useRef(new Set<AbortController>());
  const loadRequest = useRef(0);
  const historyRequest = useRef(0);
  const retryRef = useRef<RetryAction | null>(null);
  const mutationRequest = useRef(0);
  const mutationBusy = useRef(false);

  const [gateStatus, setGateStatus] = useState<QualityLoadStatus>("idle");
  const [gate, setGate] = useState<QualityGate | null>(null);
  const [gateError, setGateError] = useState<Error | null>(null);
  const [policiesStatus, setPoliciesStatus] = useState<QualityLoadStatus>("idle");
  const [policies, setPolicies] = useState<QualityPolicy[]>([]);
  const [policiesError, setPoliciesError] = useState<Error | null>(null);
  const [baselinesStatus, setBaselinesStatus] = useState<QualityLoadStatus>("idle");
  const [baselines, setBaselines] = useState<QualityBaseline[]>([]);
  const [baselinesError, setBaselinesError] = useState<Error | null>(null);
  const [baselineCursor, setBaselineCursor] = useState<string | null>(null);
  const [certificationsStatus, setCertificationsStatus] = useState<QualityLoadStatus>("idle");
  const [certifications, setCertifications] = useState<QualityCertification[]>([]);
  const [certificationsError, setCertificationsError] = useState<Error | null>(null);
  const [certificationCursor, setCertificationCursor] = useState<string | null>(null);
  const [mutationStatus, setMutationStatus] = useState<QualityMutationStatus>("idle");
  const [mutationOutcome, setMutationOutcome] = useState<QualityMutationOutcome | null>(null);
  const [mutationError, setMutationError] = useState<Error | null>(null);

  const track = useCallback((controller: AbortController) => {
    activeControllers.current.add(controller);
    return () => activeControllers.current.delete(controller);
  }, []);

  const clearAuthority = useCallback(() => {
    loadRequest.current += 1;
    historyRequest.current += 1;
    mutationRequest.current += 1;
    mutationBusy.current = false;
    activeControllers.current.forEach((controller) => controller.abort());
    activeControllers.current.clear();
    setGateStatus("idle");
    setGate(null);
    setGateError(null);
    setPoliciesStatus("idle");
    setPolicies([]);
    setPoliciesError(null);
    setBaselinesStatus("idle");
    setBaselines([]);
    setBaselinesError(null);
    setBaselineCursor(null);
    setCertificationsStatus("idle");
    setCertifications([]);
    setCertificationsError(null);
    setCertificationCursor(null);
    setMutationStatus("idle");
    setMutationOutcome(null);
    setMutationError(null);
    retryRef.current = null;
  }, []);

  const reload = useCallback(async (): Promise<boolean> => {
    if (!active || !releaseId || !channelId) return false;
    const requestId = ++loadRequest.current;
    const controller = new AbortController();
    const release = track(controller);
    setGateStatus("loading");
    setPoliciesStatus("loading");
    setBaselinesStatus("loading");
    setGateError(null);
    setPoliciesError(null);
    setBaselinesError(null);
    try {
      const [nextGate, nextPolicies, nextBaselines] = await Promise.all([
        api.fetchGate(scope, releaseId, channelId, { signal: controller.signal }),
        api.fetchPolicies(scope, undefined, { signal: controller.signal }),
        api.fetchBaselines(scope, undefined, { signal: controller.signal }),
      ]);
      if (requestId !== loadRequest.current || controller.signal.aborted) return false;
      if (
        nextGate.tenant_id !== scope.tenantId ||
        nextGate.dataset_id !== scope.datasetId ||
        nextGate.release_id !== releaseId ||
        nextGate.channel_id !== channelId
      ) {
        throw new Error("Release Quality gate scope mismatch");
      }
      setGate(nextGate);
      setPolicies(nextPolicies.items);
      setBaselines(nextBaselines.items);
      setBaselineCursor(nextBaselines.next_cursor);
      setGateStatus("ready");
      setPoliciesStatus("ready");
      setBaselinesStatus("ready");
      return true;
    } catch (caught) {
      if (requestId !== loadRequest.current || controller.signal.aborted) return false;
      const error = caught instanceof Error ? caught : new Error("Release Quality unavailable");
      setGate(null);
      setPolicies([]);
      setBaselines([]);
      setGateStatus("error");
      setPoliciesStatus("error");
      setBaselinesStatus("error");
      setGateError(error);
      setPoliciesError(error);
      setBaselinesError(error);
      return false;
    } finally {
      release();
    }
  }, [active, api, channelId, releaseId, scope, track]);

  const loadCertifications = useCallback(
    async (append = false) => {
      if (!active || !releaseId) return;
      const requestId = ++historyRequest.current;
      const controller = new AbortController();
      const release = track(controller);
      setCertificationsStatus("loading");
      setCertificationsError(null);
      try {
        const page = await api.fetchCertifications(
          scope,
          releaseId,
          append ? { cursor: certificationCursor } : undefined,
          { signal: controller.signal },
        );
        if (requestId !== historyRequest.current || controller.signal.aborted) return;
        setCertifications((current) => (append ? mergeById(current, page.items) : page.items));
        setCertificationCursor(page.next_cursor);
        setCertificationsStatus("ready");
      } catch (caught) {
        if (requestId !== historyRequest.current || controller.signal.aborted) return;
        setCertificationsStatus("error");
        setCertificationsError(
          caught instanceof Error ? caught : new Error("Certification history unavailable"),
        );
      } finally {
        release();
      }
    },
    [active, api, certificationCursor, releaseId, scope, track],
  );

  const submit = useCallback(
    async (action: RetryAction, retrying = false): Promise<QualityMutationOutcome | null> => {
      if (!active || mutationBusy.current) return null;
      const requestId = ++mutationRequest.current;
      const controller = new AbortController();
      const release = track(controller);
      mutationBusy.current = true;
      if (!retrying) retryRef.current = action;
      setMutationStatus("saving");
      setMutationError(null);
      try {
        const outcome = await action(controller.signal);
        if (requestId !== mutationRequest.current || controller.signal.aborted) return null;
        if (outcome?.state === "unavailable") {
          throw new Error("Release Quality mutation authority is unavailable");
        }
        const refreshed = await reload();
        if (requestId !== mutationRequest.current || controller.signal.aborted) return null;
        if (!refreshed) throw new Error("Release Quality refresh failed");
        if (certificationsStatus !== "idle") await loadCertifications(false);
        if (requestId !== mutationRequest.current || controller.signal.aborted) return null;
        setMutationOutcome(outcome);
        setMutationStatus("success");
        return outcome;
      } catch (caught) {
        if (requestId !== mutationRequest.current || controller.signal.aborted) return null;
        setMutationStatus("error");
        setMutationOutcome(null);
        setMutationError(caught instanceof Error ? caught : new Error("Quality mutation failed"));
        return null;
      } finally {
        if (requestId === mutationRequest.current) mutationBusy.current = false;
        release();
      }
    },
    [active, certificationsStatus, loadCertifications, reload, track],
  );

  const certify = useCallback(
    async (input: CertifyReleaseQualityInput) => {
      if (!releaseId) return null;
      const idempotencyKey = createQualityIdempotencyKey();
      const action = (signal: AbortSignal) =>
        api.certify(scope, releaseId, input, { idempotencyKey, signal });
      return submit(action);
    },
    [api, releaseId, scope, submit],
  );

  const createBaseline = useCallback(
    async (input: CreateQualityBaselineInput) => {
      const idempotencyKey = createQualityIdempotencyKey();
      const action = (signal: AbortSignal) =>
        api.createBaseline(scope, input, { idempotencyKey, signal });
      return submit(action);
    },
    [api, scope, submit],
  );

  const requestWaiver = useCallback(
    async (input: RequestQualityWaiverInput) => {
      if (!releaseId) return null;
      const idempotencyKey = createQualityIdempotencyKey();
      const action = (signal: AbortSignal) =>
        api.requestWaiver(scope, releaseId, input, { idempotencyKey, signal });
      return submit(action);
    },
    [api, releaseId, scope, submit],
  );

  const updatePolicy = useCallback(
    async (policyId: string, input: UpdateQualityPolicyInput) => {
      const idempotencyKey = createQualityIdempotencyKey();
      const action = (signal: AbortSignal) =>
        api.updatePolicy(scope, policyId, input, { idempotencyKey, signal });
      return submit(action);
    },
    [api, scope, submit],
  );

  const retry = useCallback(async () => {
    const action = retryRef.current;
    return action ? submit(action, true) : null;
  }, [submit]);

  useEffect(() => {
    clearAuthority();
    const controllers = activeControllers.current;
    if (active) void reload();
    return () => {
      loadRequest.current += 1;
      historyRequest.current += 1;
      mutationRequest.current += 1;
      mutationBusy.current = false;
      controllers.forEach((controller) => controller.abort());
      controllers.clear();
    };
  }, [active, channelId, clearAuthority, key, releaseId, reload]);

  return useMemo(
    () => ({
      gate: { status: gateStatus, value: gate, error: gateError, reload },
      policies: { status: policiesStatus, items: policies, error: policiesError },
      baselines: {
        status: baselinesStatus,
        items: baselines,
        error: baselinesError,
        nextCursor: baselineCursor,
      },
      certifications: {
        status: certificationsStatus,
        items: certifications,
        error: certificationsError,
        nextCursor: certificationCursor,
        load: () => loadCertifications(false),
        loadMore: () => loadCertifications(true),
      },
      mutation: {
        status: mutationStatus,
        outcome: mutationOutcome,
        error: mutationError,
        certify,
        createBaseline,
        updatePolicy,
        requestWaiver,
        retry,
      },
    }),
    [
      baselineCursor,
      baselines,
      baselinesError,
      baselinesStatus,
      certificationCursor,
      certifications,
      certificationsError,
      certificationsStatus,
      certify,
      createBaseline,
      gate,
      gateError,
      gateStatus,
      loadCertifications,
      mutationError,
      mutationOutcome,
      mutationStatus,
      policies,
      policiesError,
      policiesStatus,
      reload,
      requestWaiver,
      retry,
      updatePolicy,
    ],
  );
}
