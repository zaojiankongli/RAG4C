import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import { createJudgment, patchJudgment } from "../api/retrievalQualityApi";
import type { ExperimentDetail, RelevanceLabel, RetrievalScope } from "../model/contracts";
import { projectExperiment } from "../model/projection";
import { retrievalScopeKey } from "./scope";

export interface JudgmentDraft { relevanceLabel: RelevanceLabel; score: number | null; note: string; }
const BLANK: JudgmentDraft = { relevanceLabel: "partial", score: null, note: "" };
function same(a: JudgmentDraft, b: JudgmentDraft): boolean { return a.relevanceLabel === b.relevanceLabel && a.score === b.score && a.note === b.note; }

export function useJudgmentMutation(scope: RetrievalScope | null, actorId: string, detail: ExperimentDetail | null, refreshAuthority: () => Promise<unknown>) {
  const key = `${retrievalScopeKey(scope)}\u0000${detail?.id ?? ""}\u0000${actorId}`;
  const keyRef = useRef(key); keyRef.current = key;
  const controllersRef = useRef(new Map<number, AbortController>());
  const [drafts, setDrafts] = useState<Record<number, JudgmentDraft>>({});
  const [savingRanks, setSavingRanks] = useState<number[]>([]);
  const [conflictRanks, setConflictRanks] = useState<number[]>([]);
  const [error, setError] = useState<Error | null>(null);
  const owned = useMemo(() => new Map((detail?.judgments ?? []).filter((item) => item.created_by === actorId).map((item) => [item.result_rank, item])), [actorId, detail]);
  const evidence = useMemo(() => new Map(detail ? projectExperiment(detail).evidence.map((item) => [item.rank, item]) : []), [detail]);

  useEffect(() => {
    const controllers = controllersRef.current;
    for (const controller of controllers.values()) controller.abort();
    controllers.clear(); setDrafts({}); setSavingRanks([]); setConflictRanks([]); setError(null);
    return () => { for (const controller of controllers.values()) controller.abort(); controllers.clear(); };
  }, [key]);

  const draftFor = useCallback((rank: number): JudgmentDraft => {
    const saved = drafts[rank]; if (saved) return saved;
    const judgment = owned.get(rank);
    return judgment ? { relevanceLabel: judgment.relevance_label, score: judgment.score, note: judgment.note } : BLANK;
  }, [drafts, owned]);
  const setDraft = useCallback((rank: number, draft: JudgmentDraft) => setDrafts((current) => ({ ...current, [rank]: draft })), []);

  const save = useCallback(async (rank: number, draft: JudgmentDraft): Promise<boolean> => {
    if (!scope || !detail || !actorId || rank < 1) return false;
    setDraft(rank, draft); setConflictRanks((items) => items.filter((item) => item !== rank)); setError(null);
    controllersRef.current.get(rank)?.abort(); const controller = new AbortController(); controllersRef.current.set(rank, controller);
    setSavingRanks((items) => items.includes(rank) ? items : [...items, rank]);
    const capturedKey = key;
    try {
      const judgment = owned.get(rank);
      if (judgment) {
        const original: JudgmentDraft = { relevanceLabel: judgment.relevance_label, score: judgment.score, note: judgment.note };
        if (same(original, draft)) return true;
        const patch: { expected_revision: number; relevance_label?: RelevanceLabel; score?: number | null; note?: string } = { expected_revision: judgment.revision };
        if (draft.relevanceLabel !== original.relevanceLabel) patch.relevance_label = draft.relevanceLabel;
        if (draft.score !== original.score) patch.score = draft.score;
        if (draft.note !== original.note) patch.note = draft.note;
        await patchJudgment(scope, detail.id, judgment.id, patch, { signal: controller.signal });
      } else {
        const item = evidence.get(rank);
        await createJudgment(scope, detail.id, {
          result_rank: rank,
          document_id: item?.documentId ?? undefined,
          chunk_id: item?.chunkId ?? undefined,
          relevance_label: draft.relevanceLabel,
          score: draft.score,
          note: draft.note,
        }, { signal: controller.signal });
      }
      if (controller.signal.aborted || keyRef.current !== capturedKey) return false;
      await refreshAuthority();
      return true;
    } catch (caught) {
      if (controller.signal.aborted || keyRef.current !== capturedKey) return false;
      if (caught instanceof ApiError && caught.status === 409) {
        setConflictRanks((items) => items.includes(rank) ? items : [...items, rank]);
        await refreshAuthority();
      } else setError(caught instanceof Error ? caught : new Error(String(caught)));
      return false;
    } finally {
      if (controllersRef.current.get(rank) === controller) controllersRef.current.delete(rank);
      if (keyRef.current === capturedKey) setSavingRanks((items) => items.filter((item) => item !== rank));
    }
  }, [actorId, detail, evidence, key, owned, refreshAuthority, scope, setDraft]);

  return { draftFor, setDraft, save, savingRanks, conflictRanks, error };
}
