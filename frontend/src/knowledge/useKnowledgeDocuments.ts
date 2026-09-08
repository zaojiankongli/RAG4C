import { useKnowledgeWorkspace } from "./KnowledgeWorkspaceContext";

/**
 * Compatibility hook for knowledge pages.
 *
 * Document data now lives in KnowledgeWorkspaceProvider so kept-alive pages share
 * one request, one snapshot, and one invalidation boundary. The optional argument
 * remains for source compatibility; activation is owned by the provider.
 */
export function useKnowledgeDocuments(_active = true) {
  const workspace = useKnowledgeWorkspace();
  return {
    ...workspace,
    reload: workspace.refresh,
  };
}
