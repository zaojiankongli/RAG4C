import type { ReleaseMutationKind } from "./releaseModel";

export interface ReleaseMutationValidationInput {
  reason: string;
  channelId?: string;
  releaseId?: string;
  targetReleaseId?: string;
  expectedChannelRevision?: number | null;
  expectedProfileRevision?: number | null;
  expectedOwnershipRevision?: number | null;
  expectedWorkspaceRevision?: number | null;
  expectedMutationGeneration?: number | null;
  expectedServingGeneration?: number | null;
}

export type ReleaseMutationValidationErrors = Partial<
  Record<keyof ReleaseMutationValidationInput, string>
>;

function required(value: string | undefined, label: string, maximum = 512): string | undefined {
  const normalized = value?.trim() ?? "";
  if (!normalized) return `${label}不能为空`;
  if (normalized.length > maximum) return `${label}不能超过 ${maximum} 个字符`;
  return undefined;
}

function revision(
  value: number | null | undefined,
  label: string,
  allowZero = false,
): string | undefined {
  const minimum = allowZero ? 0 : 1;
  if (!Number.isInteger(value) || (value as number) < minimum) {
    return `${label}必须是${allowZero ? "非负" : "正"}整数`;
  }
  return undefined;
}

export function validateReleaseMutation(
  kind: ReleaseMutationKind,
  input: ReleaseMutationValidationInput,
): ReleaseMutationValidationErrors {
  const errors: ReleaseMutationValidationErrors = {};
  const reasonError = required(input.reason, "变更原因");
  if (reasonError) errors.reason = reasonError;

  if (kind === "capture" || kind === "promote") {
    for (const [field, label] of [
      ["expectedProfileRevision", "Profile 修订号"],
      ["expectedOwnershipRevision", "Ownership 修订号"],
      ["expectedWorkspaceRevision", "Workspace 修订号"],
    ] as const) {
      const error = revision(input[field], label);
      if (error) errors[field] = error;
    }
  }
  if (kind === "capture") {
    const mutationError = revision(input.expectedMutationGeneration, "Mutation generation", true);
    if (mutationError) errors.expectedMutationGeneration = mutationError;
  }
  if (kind === "promote" || kind === "rollback") {
    const channelError = required(input.channelId, "Channel", 128);
    if (channelError) errors.channelId = channelError;
    const channelRevisionError = revision(input.expectedChannelRevision, "Channel 修订号");
    if (channelRevisionError) errors.expectedChannelRevision = channelRevisionError;
  }
  if (kind === "capture" || kind === "promote" || kind === "rollback") {
    const servingError = revision(input.expectedServingGeneration, "Serving generation", true);
    if (servingError) errors.expectedServingGeneration = servingError;
  }
  if (kind === "promote") {
    const releaseError = required(input.releaseId, "Release", 64);
    if (releaseError) errors.releaseId = releaseError;
  }
  if (kind === "rollback") {
    const targetError = required(input.targetReleaseId, "Target Release", 64);
    if (targetError) errors.targetReleaseId = targetError;
  }
  return errors;
}

export function firstReleaseValidationField(
  errors: ReleaseMutationValidationErrors,
): keyof ReleaseMutationValidationInput | null {
  return (
    (
      [
        "reason",
        "channelId",
        "releaseId",
        "targetReleaseId",
        "expectedChannelRevision",
        "expectedProfileRevision",
        "expectedOwnershipRevision",
        "expectedWorkspaceRevision",
        "expectedMutationGeneration",
        "expectedServingGeneration",
      ] as const
    ).find((field) => Boolean(errors[field])) ?? null
  );
}
