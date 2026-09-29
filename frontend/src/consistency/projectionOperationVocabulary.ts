/** Frontend action-admission contract for verified projection requeue pairs.
 *
 * Read/display values remain open strings in consistency responses. This
 * allow-list only decides whether this UI has a verified requeue affordance.
 */
const pair = <T extends string, O extends string>(targetStore: T, operation: O) =>
  Object.freeze([targetStore, operation] as const);

export const PROJECTION_REQUEUE_OPERATION_PAIRS = Object.freeze([
  pair("milvus_chunks", "upsert"),
  pair("milvus_chunks", "delete"),
  pair("milvus_chunks", "reconcile"),
  pair("graph_projection", "upsert"),
  pair("graph_projection", "delete"),
  pair("milvus_chunks", "delete_document"),
  pair("graph_projection", "delete_document"),
  pair("catalog_finalize", "finalize_document_delete"),
] as const);

const PROJECTION_REQUEUE_OPERATION_KEYS = new Set(
  PROJECTION_REQUEUE_OPERATION_PAIRS.map(([targetStore, operation]) =>
    `${targetStore}:${operation}`,
  ),
);

export function isKnownProjectionRequeuePair(
  targetStore: unknown,
  operation: unknown,
): boolean {
  return (
    typeof targetStore === "string" &&
    typeof operation === "string" &&
    PROJECTION_REQUEUE_OPERATION_KEYS.has(`${targetStore}:${operation}`)
  );
}
