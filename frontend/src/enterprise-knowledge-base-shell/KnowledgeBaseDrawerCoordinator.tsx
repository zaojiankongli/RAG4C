import { useCallback, useMemo, useState, type ReactNode } from "react";
import {
  DrawerCoordinatorContext,
  type KnowledgeBaseDrawerKind,
  type KnowledgeBaseReleaseHeaderContext,
} from "./KnowledgeBaseDrawerCoordinatorContext";

export function KnowledgeBaseDrawerCoordinatorProvider({ children }: { children: ReactNode }) {
  const [activeDrawer, setActiveDrawer] = useState<KnowledgeBaseDrawerKind | null>(null);
  const [releaseContext, setReleaseContext] = useState<KnowledgeBaseReleaseHeaderContext | null>(
    null,
  );
  const openDrawer = useCallback((kind: KnowledgeBaseDrawerKind) => setActiveDrawer(kind), []);
  const closeDrawer = useCallback(
    (kind: KnowledgeBaseDrawerKind) =>
      setActiveDrawer((current) => (current === kind ? null : current)),
    [],
  );
  const value = useMemo(
    () => ({
      coordinated: true,
      activeDrawer,
      openDrawer,
      closeDrawer,
      releaseContext,
      setReleaseContext,
    }),
    [activeDrawer, closeDrawer, openDrawer, releaseContext],
  );
  return (
    <DrawerCoordinatorContext.Provider value={value}>{children}</DrawerCoordinatorContext.Provider>
  );
}
