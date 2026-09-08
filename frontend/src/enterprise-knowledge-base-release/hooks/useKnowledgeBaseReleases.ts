import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  createReleaseIdempotencyKey,
  releaseApi as defaultReleaseApi,
  type CaptureReleaseInput,
  type PromoteReleaseInput,
  type ReleaseApi,
  type ReleaseRequestOptions,
  type ReleaseScope,
  type RollbackReleaseInput,
} from "../api/releaseApi";
import type {
  ReleaseAuditPage,
  ReleaseChannel,
  ReleaseDetail,
  ReleaseHistoryPage,
  ReleaseImpact,
  ReleaseManifest,
  ReleaseMutationKind,
  ReleaseMutationOutcome,
  ReleaseReadiness,
} from "../model/releaseModel";

export type { ReleaseApi } from "../api/releaseApi";

type LoadStatus = "idle" | "loading" | "ready" | "error";
type MutationStatus = "idle" | "saving" | "success" | "error";

type MutationInput =
  | CaptureReleaseInput
  | ({ releaseId: string } & PromoteReleaseInput)
  | ({ channelId: string } & RollbackReleaseInput);

interface HookOptions {
  api?: ReleaseApi;
}

const EMPTY_HISTORY: ReleaseHistoryPage = {
  items: [],
  count: null,
  next_cursor: null,
  summary: null,
  invalid_item_count: 0,
};

function scopeKey(scope: ReleaseScope): string {
  return `${scope.tenantId.trim()}:${scope.datasetId.trim()}:${scope.actorToken.trim()}`;
}

function mergeReleases(current: ReleaseManifest[], next: ReleaseManifest[]): ReleaseManifest[] {
  const seen = new Set<string>();
  return [...current, ...next].filter((item) => {
    if (seen.has(item.id)) return false;
    seen.add(item.id);
    return true;
  });
}

export function useKnowledgeBaseReleases(
  scope: ReleaseScope,
  enabled: boolean,
  options: HookOptions = {},
) {
  const api = options.api ?? defaultReleaseApi;
  const key = scopeKey(scope);
  const [channelStatus, setChannelStatus] = useState<LoadStatus>("idle");
  const [channels, setChannels] = useState<ReleaseChannel[]>([]);
  const [channelInvalidItemCount, setChannelInvalidItemCount] = useState(0);
  const [channelError, setChannelError] = useState<Error | null>(null);
  const [selectedChannelId, setSelectedChannelId] = useState<string | null>(null);
  const selectedChannelRef = useRef<string | null>(null);
  const [historyStatus, setHistoryStatus] = useState<LoadStatus>("idle");
  const [history, setHistory] = useState<ReleaseHistoryPage>(EMPTY_HISTORY);
  const [historyError, setHistoryError] = useState<Error | null>(null);
  const [selectedReleaseId, setSelectedReleaseId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ReleaseDetail | null>(null);
  const [readiness, setReadiness] = useState<ReleaseReadiness | null>(null);
  const [impact, setImpact] = useState<ReleaseImpact | null>(null);
  const [audit, setAudit] = useState<ReleaseAuditPage | null>(null);
  const [detailStatus, setDetailStatus] = useState<LoadStatus>("idle");
  const [detailError, setDetailError] = useState<Error | null>(null);
  const [mutationStatus, setMutationStatus] = useState<MutationStatus>("idle");
  const [mutationOutcome, setMutationOutcome] = useState<ReleaseMutationOutcome | null>(null);
  const [mutationError, setMutationError] = useState<Error | null>(null);
  const channelRequest = useRef(0);
  const historyRequest = useRef(0);
  const detailRequest = useRef(0);
  const activeControllers = useRef(new Set<AbortController>());
  const retryRef = useRef<(() => Promise<ReleaseMutationOutcome | null>) | null>(null);

  const tracked = useCallback((controller: AbortController) => {
    activeControllers.current.add(controller);
    return () => activeControllers.current.delete(controller);
  }, []);

  const loadDetail = useCallback(
    async (releaseId: string) => {
      const requestId = ++detailRequest.current;
      const controller = new AbortController();
      const release = tracked(controller);
      setSelectedReleaseId(releaseId);
      setDetailStatus("loading");
      setDetailError(null);
      setImpact(null);
      setAudit(null);
      try {
        const [nextDetail, nextReadiness] = await Promise.all([
          api.fetchDetail(scope, releaseId, { signal: controller.signal }),
          api.fetchReadiness(scope, releaseId, { signal: controller.signal }),
        ]);
        if (
          nextDetail.manifest.tenant_id !== scope.tenantId ||
          nextDetail.manifest.dataset_id !== scope.datasetId ||
          nextDetail.manifest.id !== releaseId
        ) {
          throw new Error("Release detail scope mismatch");
        }
        if (requestId !== detailRequest.current || controller.signal.aborted) return;
        setDetail(nextDetail);
        setReadiness(nextReadiness);
        setDetailStatus("ready");
      } catch (caught) {
        if (requestId !== detailRequest.current || controller.signal.aborted) return;
        setDetail(null);
        setReadiness(null);
        setDetailStatus("error");
        setDetailError(caught instanceof Error ? caught : new Error("Release detail unavailable"));
      } finally {
        release();
      }
    },
    [api, scope, tracked],
  );

  const loadHistory = useCallback(
    async (channelId: string, cursor?: string, append = false) => {
      const requestId = ++historyRequest.current;
      const controller = new AbortController();
      const release = tracked(controller);
      setHistoryStatus("loading");
      setHistoryError(null);
      try {
        const page = await api.fetchHistory(
          scope,
          channelId,
          { ...(cursor ? { cursor } : {}) },
          { signal: controller.signal },
        );
        if (
          page.items.some(
            (item) => item.tenant_id !== scope.tenantId || item.dataset_id !== scope.datasetId,
          ) ||
          (page.summary !== null && page.summary.channel_id !== channelId)
        ) {
          throw new Error("Release history scope mismatch");
        }
        if (requestId !== historyRequest.current || controller.signal.aborted) return;
        setHistory((current) => ({
          ...page,
          items: append ? mergeReleases(current.items, page.items) : page.items,
        }));
        setHistoryStatus("ready");
        const first = page.items[0]?.id;
        if (!append && first) void loadDetail(first);
      } catch (caught) {
        if (requestId !== historyRequest.current || controller.signal.aborted) return;
        if (!append) setHistory(EMPTY_HISTORY);
        setHistoryStatus("error");
        setHistoryError(
          caught instanceof Error ? caught : new Error("Release history unavailable"),
        );
      } finally {
        release();
      }
    },
    [api, loadDetail, scope, tracked],
  );

  const loadChannels = useCallback(async () => {
    if (!enabled) return;
    const requestId = ++channelRequest.current;
    const controller = new AbortController();
    const release = tracked(controller);
    setChannelStatus("loading");
    setChannelError(null);
    try {
      const page = await api.fetchChannels(scope, {}, { signal: controller.signal });
      if (page.items.some((item) => item.tenant_id !== scope.tenantId)) {
        throw new Error("Release Channel Tenant scope mismatch");
      }
      if (requestId !== channelRequest.current || controller.signal.aborted) return;
      setChannels(page.items);
      setChannelInvalidItemCount(page.invalid_item_count ?? 0);
      setChannelStatus("ready");
      const selected =
        page.items.find((item) => item.id === selectedChannelRef.current)?.id ??
        page.items[0]?.id ??
        null;
      selectedChannelRef.current = selected;
      setSelectedChannelId(selected);
      if (selected) await loadHistory(selected);
      else setHistory(EMPTY_HISTORY);
    } catch (caught) {
      if (requestId !== channelRequest.current || controller.signal.aborted) return;
      setChannels([]);
      setChannelInvalidItemCount(0);
      selectedChannelRef.current = null;
      setSelectedChannelId(null);
      setChannelStatus("error");
      setChannelError(caught instanceof Error ? caught : new Error("Release channels unavailable"));
    } finally {
      release();
    }
  }, [api, enabled, loadHistory, scope, tracked]);

  useEffect(() => {
    const controllers = activeControllers.current;
    controllers.forEach((controller) => controller.abort());
    controllers.clear();
    channelRequest.current += 1;
    historyRequest.current += 1;
    detailRequest.current += 1;
    retryRef.current = null;
    setChannels([]);
    setChannelInvalidItemCount(0);
    selectedChannelRef.current = null;
    setSelectedChannelId(null);
    setHistory(EMPTY_HISTORY);
    setHistoryError(null);
    setHistoryStatus("idle");
    setSelectedReleaseId(null);
    setDetail(null);
    setReadiness(null);
    setImpact(null);
    setAudit(null);
    setDetailError(null);
    setDetailStatus("idle");
    setMutationStatus("idle");
    setMutationOutcome(null);
    setMutationError(null);
    if (!enabled) {
      setChannelStatus("idle");
      setChannelError(null);
      return;
    }
    setChannelStatus("loading");
    setChannelError(null);
    void loadChannels();
    return () => {
      controllers.forEach((controller) => controller.abort());
      controllers.clear();
    };
  }, [enabled, key, loadChannels]);

  const selectChannel = useCallback(
    async (channelId: string) => {
      selectedChannelRef.current = channelId;
      setSelectedChannelId(channelId);
      setHistory(EMPTY_HISTORY);
      setSelectedReleaseId(null);
      setDetail(null);
      await loadHistory(channelId);
    },
    [loadHistory],
  );

  const loadMore = useCallback(async () => {
    if (!selectedChannelId || !history.next_cursor || historyStatus === "loading") return;
    await loadHistory(selectedChannelId, history.next_cursor, true);
  }, [history.next_cursor, historyStatus, loadHistory, selectedChannelId]);

  const openRelease = useCallback(
    async (releaseId: string) => {
      await loadDetail(releaseId);
    },
    [loadDetail],
  );

  const loadImpact = useCallback(async () => {
    if (!selectedReleaseId) return;
    setDetailError(null);
    try {
      setImpact(await api.fetchImpact(scope, selectedReleaseId));
    } catch (caught) {
      setDetailError(caught instanceof Error ? caught : new Error("Release impact unavailable"));
    }
  }, [api, scope, selectedReleaseId]);

  const loadAudit = useCallback(async () => {
    if (!selectedReleaseId) return;
    setDetailError(null);
    try {
      setAudit(await api.fetchAudit(scope, selectedReleaseId));
    } catch (caught) {
      setDetailError(caught instanceof Error ? caught : new Error("Release audit unavailable"));
    }
  }, [api, scope, selectedReleaseId]);

  const runMutation = useCallback(
    async (
      kind: ReleaseMutationKind,
      input: MutationInput,
      idempotencyKey: string,
    ): Promise<ReleaseMutationOutcome | null> => {
      setMutationStatus("saving");
      setMutationError(null);
      setMutationOutcome(null);
      try {
        const requestOptions: ReleaseRequestOptions = { idempotencyKey };
        const outcome =
          kind === "capture"
            ? await api.capture(scope, input as CaptureReleaseInput, requestOptions)
            : kind === "promote"
              ? await api.promote(
                  scope,
                  (input as { releaseId: string }).releaseId,
                  input as PromoteReleaseInput,
                  requestOptions,
                )
              : await api.rollback(
                  scope,
                  (input as { channelId: string }).channelId,
                  input as RollbackReleaseInput,
                  requestOptions,
                );
        setMutationOutcome(outcome);
        setMutationStatus("success");
        return outcome;
      } catch (caught) {
        setMutationStatus("error");
        setMutationError(caught instanceof Error ? caught : new Error("Release mutation failed"));
        return null;
      }
    },
    [api, scope],
  );

  const submit = useCallback(
    async (kind: ReleaseMutationKind, input: MutationInput) => {
      const idempotencyKey = createReleaseIdempotencyKey();
      const execute = () => runMutation(kind, input, idempotencyKey);
      retryRef.current = execute;
      return execute();
    },
    [runMutation],
  );

  const retry = useCallback(async () => retryRef.current?.() ?? null, []);
  const clearMutation = useCallback(() => {
    retryRef.current = null;
    setMutationStatus("idle");
    setMutationOutcome(null);
    setMutationError(null);
  }, []);

  return useMemo(
    () => ({
      channels: {
        status: channelStatus,
        items: channels,
        selectedId: selectedChannelId,
        error: channelError,
        invalidItemCount: channelInvalidItemCount,
        refresh: loadChannels,
      },
      selectChannel,
      history: {
        status: historyStatus,
        items: history.items,
        count: history.count,
        nextCursor: history.next_cursor,
        summary: history.summary,
        invalidItemCount: history.invalid_item_count ?? 0,
        error: historyError,
        loadMore,
      },
      detail: {
        status: detailStatus,
        selectedId: selectedReleaseId,
        value: detail,
        readiness,
        impact,
        audit,
        error: detailError,
        open: openRelease,
        close: () => setSelectedReleaseId(null),
        loadImpact,
        loadAudit,
      },
      mutation: {
        status: mutationStatus,
        outcome: mutationOutcome,
        error: mutationError,
        submit,
        retry,
        clear: clearMutation,
      },
    }),
    [
      audit,
      channelError,
      channelInvalidItemCount,
      channelStatus,
      channels,
      clearMutation,
      detail,
      detailError,
      detailStatus,
      history,
      historyError,
      historyStatus,
      impact,
      loadAudit,
      loadChannels,
      loadImpact,
      loadMore,
      mutationError,
      mutationOutcome,
      mutationStatus,
      openRelease,
      readiness,
      retry,
      selectChannel,
      selectedChannelId,
      selectedReleaseId,
      submit,
    ],
  );
}
