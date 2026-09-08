import { useMemo } from "react";
import type { GovernanceScope } from "../model/governanceModel";

export function useStableGovernanceScope(scope: GovernanceScope | null) {
  const tenantId = scope?.tenantId.trim() ?? "";
  const datasetId = scope?.datasetId.trim() ?? "";
  const actorToken = scope?.actorToken.trim() ?? "";
  const stable = useMemo<GovernanceScope | null>(
    () => tenantId && datasetId && actorToken ? { tenantId, datasetId, actorToken } : null,
    [tenantId, datasetId, actorToken],
  );
  return { scope: stable, key: stable ? `${tenantId}\u0000${datasetId}\u0000${actorToken}` : "" };
}
