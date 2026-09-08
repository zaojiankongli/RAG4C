import { lazy, type ComponentType } from "react";

export interface KnowledgeServingPageProps {
  active: boolean;
  tenantId: string;
  accountId?: string;
  actorToken: string;
  datasetId: string;
  capabilityReady: boolean;
  tenantLabel: string;
  readOnly: boolean;
  mobile?: boolean;
  onServingHandoff?: (route: unknown) => void;
}

type KnowledgeServingRuntimeProps = KnowledgeServingPageProps & {
  onHandoff?: (route: unknown) => void;
};

type KnowledgeServingModule = {
  default: ComponentType<KnowledgeServingRuntimeProps>;
};

const KNOWLEDGE_SERVING_PAGE_MODULE = "../enterprise-knowledge-serving/KnowledgeServingPage.tsx";
type ImportMetaWithGlob = ImportMeta & {
  glob<T>(pattern: string): Record<string, () => Promise<T>>;
};

const knowledgeServingPageLoaders = (
  import.meta as ImportMetaWithGlob
).glob<KnowledgeServingModule>("../enterprise-knowledge-serving/KnowledgeServingPage.tsx");

const LazyKnowledgeServingPage = lazy(async () => {
  const load = knowledgeServingPageLoaders[KNOWLEDGE_SERVING_PAGE_MODULE];
  if (!load) {
    throw new Error("Knowledge Serving authority page module is unavailable");
  }
  return load();
});

export default function KnowledgeServingPageLoader(props: KnowledgeServingPageProps) {
  return <LazyKnowledgeServingPage {...props} onHandoff={props.onServingHandoff} />;
}
