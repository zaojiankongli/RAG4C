import { createContext, useContext } from "react";

export type KnowledgeBaseDrawerKind = "facts" | "release-detail";

export interface KnowledgeBaseReleaseHeaderContext {
  channelName: string | null;
  effectiveReleaseNumber: number | null;
  servingReleaseNumber: number | null;
  unavailableReason: string | null;
}

export interface DrawerCoordinatorValue {
  coordinated: boolean;
  activeDrawer: KnowledgeBaseDrawerKind | null;
  openDrawer: (kind: KnowledgeBaseDrawerKind) => void;
  closeDrawer: (kind: KnowledgeBaseDrawerKind) => void;
  releaseContext: KnowledgeBaseReleaseHeaderContext | null;
  setReleaseContext: (value: KnowledgeBaseReleaseHeaderContext | null) => void;
}

const NOOP = () => undefined;
export const DrawerCoordinatorContext = createContext<DrawerCoordinatorValue>({
  coordinated: false,
  activeDrawer: null,
  openDrawer: NOOP,
  closeDrawer: NOOP,
  releaseContext: null,
  setReleaseContext: NOOP,
});

export function useKnowledgeBaseDrawerCoordinator(): DrawerCoordinatorValue {
  return useContext(DrawerCoordinatorContext);
}
