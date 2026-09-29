// Vitest reads the backend registry in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  PROJECTION_REQUEUE_OPERATION_PAIRS,
  isKnownProjectionRequeuePair,
} from "./projectionOperationVocabulary";

const backendRequeueContract = readFileSync(
  new URL("../../../core/projection_target_contract.py", import.meta.url),
  "utf8",
);

function backendBuiltinPairs(): string[] {
  const body = backendRequeueContract.match(
    /_BUILTIN_FAMILIES:\s*dict\[str,\s*ProjectionRequeueFamily\]\s*=\s*\{([\s\S]*?)\n\}/,
  )?.[1];
  if (body === undefined) throw new Error("backend built-in requeue contract is missing");
  return parseBackendBuiltinPairs(body);
}

function parseBackendBuiltinPairs(body: string): string[] {
  return body
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const match = line.match(
        /^"([a-z][a-z0-9_]*:[a-z][a-z0-9_]*)":\s*"(?:ordinary|document_delete)",?$/,
      );
      if (!match) throw new Error(`backend requeue pair declaration is not recognized: ${line}`);
      return match[1];
    });
}

describe("projection operation vocabulary", () => {
  it("admits only the verified built-in requeue pairs", () => {
    expect(PROJECTION_REQUEUE_OPERATION_PAIRS).toEqual([
      ["milvus_chunks", "upsert"],
      ["milvus_chunks", "delete"],
      ["milvus_chunks", "reconcile"],
      ["graph_projection", "upsert"],
      ["graph_projection", "delete"],
      ["milvus_chunks", "delete_document"],
      ["graph_projection", "delete_document"],
      ["catalog_finalize", "finalize_document_delete"],
    ]);
    expect(isKnownProjectionRequeuePair("milvus_chunks", "reconcile")).toBe(true);
    expect(isKnownProjectionRequeuePair("catalog_finalize", "finalize_document_delete")).toBe(
      true,
    );
    expect(isKnownProjectionRequeuePair("custom_vector", "reconcile")).toBe(false);
    expect(isKnownProjectionRequeuePair("milvus_chunks", "custom_reconcile")).toBe(false);
  });

  it("matches the backend's built-in requeue registry in declaration order", () => {
    expect(PROJECTION_REQUEUE_OPERATION_PAIRS.map(([targetStore, operation]) =>
      `${targetStore}:${operation}`,
    )).toEqual(backendBuiltinPairs());
  });

  it("rejects unrecognized registry declarations instead of skipping them", () => {
    expect(() => parseBackendBuiltinPairs('"custom_vector:reconcile": "ordinary", # review'))
      .toThrow(/not recognized/);
  });

  it("fails closed for malformed and prototype-shaped values", () => {
    expect(isKnownProjectionRequeuePair("", "upsert")).toBe(false);
    expect(isKnownProjectionRequeuePair("__proto__", "toString")).toBe(false);
    expect(isKnownProjectionRequeuePair("milvus_chunks", "__proto__")).toBe(false);
    expect(Object.isFrozen(PROJECTION_REQUEUE_OPERATION_PAIRS)).toBe(true);
    expect(PROJECTION_REQUEUE_OPERATION_PAIRS.every((item) => Object.isFrozen(item))).toBe(true);
  });
});
