import { describe, expect, it } from "vitest";
import openApi from "../../types/generated/openapi.json";
import {
  TASK_CATEGORY_API_VALUES,
  TASK_CATEGORY_FACT_TO_DISPLAY,
  TASK_CATEGORY_FACT_VALUES,
  TASK_CATEGORY_LABELS,
  TASK_DISPLAY_CATEGORY_VALUES,
  TASK_DISPLAY_STATUS_VALUES,
  TASK_SOURCE_KIND_CATEGORY,
  TASK_SOURCE_KIND_VALUES,
  TASK_STATUS_DISPLAY_LABELS,
  TASK_STATUS_FACT_VALUES,
  displayTaskCategory,
  displayTaskStatus,
  normalizeTaskCategory,
  normalizeTaskStatus,
  savedViewTaskCategory,
} from "./taskVocabulary";

type OpenApiContract = {
  components: {
    schemas: Record<
      string,
      { properties?: Record<string, unknown> }
    >;
  };
  paths: Record<
    string,
    {
      get?: {
        parameters?: Array<{ name?: string; schema?: unknown }>;
      };
    }
  >;
};

const contract = openApi as unknown as OpenApiContract;

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function enumValues(value: unknown): string[] {
  const current = record(value);
  if (!current) return [];
  if (Array.isArray(current.enum) && current.enum.every((item) => typeof item === "string"))
    return current.enum as string[];
  if (Array.isArray(current.anyOf)) return current.anyOf.flatMap(enumValues);
  if (record(current.items)) return enumValues(current.items);
  return [];
}

function apiStatusValues(): string[] {
  const parameters = contract.paths["/api/enterprise/tasks"]?.get?.parameters ?? [];
  return enumValues(parameters.find((parameter) => parameter.name === "status")?.schema);
}

function apiSourceKindValues(): string[] {
  const parameters = contract.paths["/api/enterprise/tasks"]?.get?.parameters ?? [];
  return enumValues(parameters.find((parameter) => parameter.name === "source_kind")?.schema);
}

function apiTaskListParameterNames(): string[] {
  const parameters = contract.paths["/api/enterprise/tasks"]?.get?.parameters ?? [];
  return parameters.flatMap((parameter) =>
    typeof parameter.name === "string" ? [parameter.name] : [],
  );
}

describe("frontend task vocabulary contract", () => {
  it("keeps canonical backend categories and UI display categories separate", () => {
    expect(TASK_CATEGORY_FACT_VALUES).toEqual([
      "documents",
      "indexing",
      "sources",
      "compliance",
      "quality",
    ]);
    expect(TASK_DISPLAY_CATEGORY_VALUES).toEqual([
      "content",
      "indexing",
      "source",
      "compliance",
      "quality",
    ]);
    expect(TASK_CATEGORY_FACT_TO_DISPLAY).toEqual({
      documents: "content",
      indexing: "indexing",
      sources: "source",
      compliance: "compliance",
      quality: "quality",
    });
    expect(Object.isFrozen(TASK_SOURCE_KIND_VALUES)).toBe(true);
    expect(Object.isFrozen(TASK_CATEGORY_FACT_VALUES)).toBe(true);
    expect(Object.isFrozen(TASK_DISPLAY_CATEGORY_VALUES)).toBe(true);
    expect(Object.isFrozen(TASK_CATEGORY_API_VALUES)).toBe(true);
    expect(Object.isFrozen(TASK_CATEGORY_FACT_TO_DISPLAY)).toBe(true);
    expect(Object.isFrozen(TASK_SOURCE_KIND_CATEGORY)).toBe(true);
    expect(Object.isFrozen(TASK_CATEGORY_LABELS)).toBe(true);
  });

  it("covers every source kind with its backend and display category", () => {
    expect(TASK_SOURCE_KIND_VALUES).toEqual([
      "document_ingest",
      "index_operation",
      "source_sync",
      "document_delete",
      "audit_export",
      "release_quality_scan",
      "release_recertification",
    ]);
    expect(TASK_SOURCE_KIND_CATEGORY).toEqual({
      document_ingest: "documents",
      index_operation: "indexing",
      source_sync: "sources",
      document_delete: "documents",
      audit_export: "compliance",
      release_quality_scan: "quality",
      release_recertification: "quality",
    });
    expect(displayTaskCategory("documents", "document_ingest")).toBe("content");
    expect(displayTaskCategory("sources", "source_sync")).toBe("source");
    expect(() => displayTaskCategory("quality", "document_ingest")).toThrow(
      "category does not match source_kind",
    );
  });

  it("normalizes category aliases only at the read boundary and fails closed", () => {
    expect(normalizeTaskCategory(" content ")).toBe("documents");
    expect(normalizeTaskCategory("release-quality")).toBe("quality");
    expect(savedViewTaskCategory("sources")).toBe("source");
    expect(() => normalizeTaskCategory("toString")).toThrow("category is not allowed");
    expect(() => savedViewTaskCategory("__proto__")).toThrow("category is not allowed");
  });

  it("keeps canonical fact statuses separate from display status and labels", () => {
    expect(TASK_STATUS_FACT_VALUES).toEqual([
      "queued",
      "running",
      "succeeded",
      "failed",
      "cancelled",
      "blocked",
      "unavailable",
    ]);
    expect(TASK_DISPLAY_STATUS_VALUES).toEqual([
      "queued",
      "running",
      "completed",
      "failed",
      "cancelled",
      "blocked",
      "unavailable",
    ]);
    expect(normalizeTaskStatus("completed")).toBe("succeeded");
    expect(displayTaskStatus("succeeded")).toBe("completed");
    expect(TASK_STATUS_DISPLAY_LABELS.completed).toBe("已完成");
    expect(() => normalizeTaskStatus("toString")).toThrow("normalized_status is not allowed");
    expect(Object.keys(TASK_STATUS_DISPLAY_LABELS)).toEqual(TASK_DISPLAY_STATUS_VALUES);
    expect(Object.keys(TASK_CATEGORY_LABELS)).toEqual(TASK_DISPLAY_CATEGORY_VALUES);
    expect(Object.isFrozen(TASK_STATUS_DISPLAY_LABELS)).toBe(true);
  });

  it("matches the generated public API category/status vocabularies", () => {
    const categorySchema = contract.components.schemas.SavedViewFilters?.properties?.categories;
    expect(enumValues(categorySchema)).toEqual(TASK_CATEGORY_API_VALUES);
    expect(apiSourceKindValues()).toEqual(TASK_SOURCE_KIND_VALUES);
    expect(apiStatusValues()).toEqual(TASK_STATUS_FACT_VALUES);
    expect(apiTaskListParameterNames()).toEqual([
      "cursor",
      "limit",
      "status",
      "source_kind",
      "action_required",
    ]);
    expect(TASK_STATUS_FACT_VALUES).not.toContain("completed");
  });
});
