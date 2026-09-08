import type {
  ServingEvent,
  ServingHandoff,
  ServingMutationOutcome,
  ServingPolicyRevision,
  ServingPreview,
  ServingProfile,
  ServingSnapshot,
  ServingSnapshotDetail,
  ServingSummary,
} from "../model/servingModel";
import type { ServingLoadStatus, ServingDetailValue } from "../hooks/useEnterpriseKnowledgeServing";
import type {
  ActivateServingProfileInput,
  CreateServingPolicyRevisionInput,
  CreateServingProfileInput,
  PreviewServingPolicyInput,
} from "../api/servingApi";

export interface ControllerResource<T> {
  status: ServingLoadStatus;
  value: T | null;
  error: Error | string | null;
  reload: () => Promise<boolean>;
}
export interface ControllerCollection<T> {
  status: ServingLoadStatus;
  items: T[];
  count: number | null;
  nextCursor: string | null;
  invalidItemCount: number;
  error: Error | string | null;
  reload: () => Promise<boolean>;
}
export interface KnowledgeServingController {
  active: boolean;
  readOnly: boolean;
  summary: ControllerResource<ServingSummary>;
  profile: ControllerResource<{
    profile: ServingProfile;
    current_policy: ServingPolicyRevision | null;
    current_snapshot: ServingSnapshot | null;
  }>;
  snapshots: ControllerCollection<ServingSnapshot>;
  detail: {
    status: ServingLoadStatus;
    value: ServingDetailValue | ServingSnapshotDetail | null;
    error: Error | string | null;
    load: (id: string) => Promise<boolean>;
  };
  activity: {
    status: ServingLoadStatus;
    items: ServingEvent[];
    count: number | null;
    nextCursor: string | null;
    invalidItemCount: number;
    error: Error | string | null;
    load: () => Promise<boolean>;
  };
  mutation: {
    status: "idle" | "saving" | "success" | "error";
    outcome: ServingMutationOutcome | null;
    error: Error | string | null;
    createProfile: (input: CreateServingProfileInput) => Promise<ServingMutationOutcome | null>;
    createPolicyRevision: (
      input: CreateServingPolicyRevisionInput,
    ) => Promise<ServingMutationOutcome | null>;
    activateProfile: (input: ActivateServingProfileInput) => Promise<ServingMutationOutcome | null>;
    previewPolicy: (input: PreviewServingPolicyInput) => Promise<ServingPreview | null>;
  };
}
export type ServingHandoffCallback = (handoff: ServingHandoff) => void;
