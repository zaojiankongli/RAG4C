import { describe, expect, it } from "vitest";
import { DEMO_HEALTH } from "../api/mock";
import type { HealthInfo } from "../types/rag";
import { getServiceStatus } from "./serviceStatusModel";

type StatusInput = {
  online: boolean | null;
  checking: boolean;
  health: HealthInfo | null;
};

const PRESENTATION = {
  checking: { label: "检查中", tone: "pending" },
  unconfigured: { label: "待配置", tone: "warning" },
  degraded: { label: "部分可用", tone: "warning" },
  ready: { label: "已就绪", tone: "success" },
  offline: { label: "离线演示", tone: "warning" },
  down: { label: "连接异常", tone: "danger" },
  unknown: { label: "状态未知", tone: "pending" },
} as const;

function makeHealth(overrides: Partial<HealthInfo> = {}): HealthInfo {
  return {
    status: "ok",
    milvus_uri: "http://localhost:19530",
    embedding_model: "test-embedder",
    reranker_model: "test-reranker",
    generation_model: "test-llm",
    graph_engine_on: false,
    components: {
      milvus: { status: "ok", detail: "connected" },
      embedder: { status: "ok", detail: "connected" },
      reranker: { status: "ok", detail: "connected" },
      llm: { status: "ok", detail: "connected" },
    },
    ...overrides,
  };
}

function expectStatus(
  result: ReturnType<typeof getServiceStatus>,
  state: keyof typeof PRESENTATION,
) {
  expect(result).toMatchObject({ state, ...PRESENTATION[state] });
  // Copy can evolve; every state still needs a meaningful description.
  expect(result.description).toEqual(expect.stringMatching(/\S/));
  expect(Array.isArray(result.issues)).toBe(true);
}

const mixedComponents: HealthInfo["components"] = {
  milvus: { status: "empty", detail: "no indexed documents" },
  embedder: { status: "ok", detail: "connected" },
  reranker: { status: "error", detail: "connection refused" },
  llm: { status: "unconfigured", detail: "missing model configuration" },
};

const mixedIssues = [
  { key: "milvus", label: "向量检索", status: "empty", statusLabel: "暂无数据" },
  { key: "reranker", label: "重排模型", status: "error", statusLabel: "连接异常" },
  { key: "llm", label: "回答模型", status: "unconfigured", statusLabel: "待配置" },
];

function expectMixedIssues(result: ReturnType<typeof getServiceStatus>) {
  expect(result.issues).toHaveLength(mixedIssues.length);
  expect(result.issues).toEqual(expect.arrayContaining(mixedIssues));
}

describe("getServiceStatus", () => {
  describe("checking takes precedence over connection and health", () => {
    it.each([true, false, null] as const)(
      "suppresses stale issues while checking with online=%s",
      (online) => {
        const result = getServiceStatus({
          online,
          checking: true,
          health: makeHealth({ status: "down", components: mixedComponents }),
        });

        expectStatus(result, "checking");
        expect(result.issues).toEqual([]);
      },
    );

    it.each([null, makeHealth(), makeHealth({ status: "down", components: mixedComponents })])(
      "keeps an unresolved connection in checking regardless of health: %j",
      (health) => {
        const result = getServiceStatus({ online: null, checking: false, health });

        expectStatus(result, "checking");
        expect(result.issues).toEqual([]);
      },
    );

    it("does not retain ready while a connected service is being rechecked", () => {
      const health = makeHealth();
      const ready = getServiceStatus({ online: true, checking: false, health });
      const rechecking = getServiceStatus({ online: true, checking: true, health });
      const checked = getServiceStatus({ online: true, checking: false, health });

      expectStatus(ready, "ready");
      expectStatus(rechecking, "checking");
      expect(rechecking.issues).toEqual([]);
      expect(rechecking.description).not.toBe(ready.description);
      expectStatus(checked, "ready");
      expect(checked.issues).toEqual([]);
    });
  });

  describe("offline health is not evidence about real services", () => {
    it.each([
      { name: "no health", health: null },
      { name: "the actual demo fixture", health: DEMO_HEALTH },
      { name: "stale ready health", health: makeHealth() },
      {
        name: "stale down health",
        health: makeHealth({ status: "down", components: mixedComponents }),
      },
      { name: "demo component issues", health: { ...DEMO_HEALTH, components: mixedComponents } },
    ])("ignores $name while offline", ({ health }) => {
      const result = getServiceStatus({ online: false, checking: false, health });
      const withoutHealth = getServiceStatus({ online: false, checking: false, health: null });

      expectStatus(result, "offline");
      expect(result.issues).toEqual([]);
      expect(result.description).toBe(withoutHealth.description);
    });
  });

  describe("online health priority", () => {
    it.each(["ok", "degraded"])(
      "reports unconfigured ahead of other component failures when overall status is %s",
      (status) => {
        const result = getServiceStatus({
          online: true,
          checking: false,
          health: makeHealth({ status, components: mixedComponents }),
        });

        expectStatus(result, "unconfigured");
        expectMixedIssues(result);
      },
    );

    it("keeps down ahead of unconfigured and degraded components without hiding issues", () => {
      const result = getServiceStatus({
        online: true,
        checking: false,
        health: makeHealth({ status: "down", components: mixedComponents }),
      });

      expectStatus(result, "down");
      expectMixedIssues(result);
    });

    it("keeps down even when every reported component is ok", () => {
      const result = getServiceStatus({
        online: true,
        checking: false,
        health: makeHealth({ status: "down" }),
      });

      expectStatus(result, "down");
      expect(result.issues).toEqual([]);
    });

    it.each([
      { status: "empty", statusLabel: "暂无数据" },
      { status: "error", statusLabel: "连接异常" },
    ] as const)(
      "reports an $status component as degraded despite an ok health status",
      ({ status, statusLabel }) => {
        const result = getServiceStatus({
          online: true,
          checking: false,
          health: makeHealth({ components: { milvus: { status, detail: "not ready" } } }),
        });

        expectStatus(result, "degraded");
        expect(result.issues).toEqual([{ key: "milvus", label: "向量检索", status, statusLabel }]);
      },
    );

    it("respects an overall degraded status even when every component is ok", () => {
      const result = getServiceStatus({
        online: true,
        checking: false,
        health: makeHealth({ status: "degraded" }),
      });

      expectStatus(result, "degraded");
      expect(result.issues).toEqual([]);
    });

    it.each([
      { name: "all components ok", components: makeHealth().components },
      {
        name: "only an optional component reported",
        components: { llm: { status: "ok", detail: "connected" } },
      },
      { name: "an empty optional component map", components: {} },
      { name: "no optional component map", components: undefined },
    ] satisfies { name: string; components: HealthInfo["components"] }[])(
      "reports ready for explicit ok health with $name",
      ({ components }) => {
        const result = getServiceStatus({
          online: true,
          checking: false,
          health: makeHealth({ components }),
        });

        expectStatus(result, "ready");
        expect(result.issues).toEqual([]);
      },
    );
  });

  describe("unknown and incomplete health never implies ready", () => {
    // These casts represent incomplete JSON responses, not valid typed fixtures.
    const missingStatus = { components: makeHealth().components } as HealthInfo;
    const incompleteHealth = {} as HealthInfo;

    it.each([
      { name: "null health", health: null },
      { name: "an empty response", health: incompleteHealth },
      { name: "missing overall status despite healthy components", health: missingStatus },
      { name: "an empty status", health: makeHealth({ status: "" }) },
      {
        name: "an unknown status despite healthy components",
        health: makeHealth({ status: "starting" }),
      },
      {
        name: "an unknown status without components",
        health: makeHealth({ status: "unknown", components: undefined }),
      },
    ])("reports unknown for $name", ({ health }) => {
      const result = getServiceStatus({ online: true, checking: false, health });

      expectStatus(result, "unknown");
      expect(result.issues).toEqual([]);
    });

    it("keeps an unknown overall status ahead of component classifications but preserves real issues", () => {
      const result = getServiceStatus({
        online: true,
        checking: false,
        health: makeHealth({ status: "starting", components: mixedComponents }),
      });

      expectStatus(result, "unknown");
      expectMixedIssues(result);
    });

    it.each(["degraded", "down"] as const)(
      "does not upgrade %s to ready when optional components are absent",
      (status) => {
        const result = getServiceStatus({
          online: true,
          checking: false,
          health: makeHealth({ status, components: undefined }),
        });

        expectStatus(result, status);
        expect(result.issues).toEqual([]);
      },
    );
  });

  describe("component issue projection", () => {
    it.each([
      { key: "milvus", label: "向量检索" },
      { key: "embedder", label: "向量模型" },
      { key: "reranker", label: "重排模型" },
      { key: "llm", label: "回答模型" },
    ] as const)("labels $key and omits healthy components", ({ key, label }) => {
      const health = makeHealth();
      health.components = {
        ...health.components,
        [key]: { status: "error", detail: "connection refused", latency_ms: 25 },
      };
      const result = getServiceStatus({ online: true, checking: false, health });

      expectStatus(result, "degraded");
      expect(result.issues).toEqual([{ key, label, status: "error", statusLabel: "连接异常" }]);
    });

    it("does not mutate health while projecting or suppressing issues", () => {
      const health = makeHealth({ status: "degraded", components: mixedComponents });
      const before = JSON.parse(JSON.stringify(health)) as HealthInfo;

      getServiceStatus({ online: true, checking: false, health });
      expect(health).toEqual(before);
      getServiceStatus({ online: true, checking: true, health });
      expect(health).toEqual(before);
      getServiceStatus({ online: false, checking: false, health });
      expect(health).toEqual(before);
    });
  });

  it("recomputes state and issues across checking, recovery, failure and reconnects", () => {
    const steps: {
      input: StatusInput;
      state: keyof typeof PRESENTATION;
      issues: typeof mixedIssues;
    }[] = [
      { input: { online: null, checking: false, health: null }, state: "checking", issues: [] },
      {
        input: {
          online: true,
          checking: false,
          health: makeHealth({ components: mixedComponents }),
        },
        state: "unconfigured",
        issues: mixedIssues,
      },
      {
        input: { online: true, checking: false, health: makeHealth({ status: "degraded" }) },
        state: "degraded",
        issues: [],
      },
      {
        input: { online: true, checking: false, health: makeHealth() },
        state: "ready",
        issues: [],
      },
      {
        input: { online: true, checking: true, health: makeHealth() },
        state: "checking",
        issues: [],
      },
      {
        input: {
          online: true,
          checking: false,
          health: makeHealth({ status: "down", components: mixedComponents }),
        },
        state: "down",
        issues: mixedIssues,
      },
      {
        input: {
          online: false,
          checking: false,
          health: { ...DEMO_HEALTH, components: mixedComponents },
        },
        state: "offline",
        issues: [],
      },
      { input: { online: true, checking: false, health: null }, state: "unknown", issues: [] },
      {
        input: { online: true, checking: false, health: makeHealth() },
        state: "ready",
        issues: [],
      },
    ];

    for (const { input, state, issues } of steps) {
      const result = getServiceStatus(input);

      expectStatus(result, state);
      expect(result.issues).toHaveLength(issues.length);
      expect(result.issues).toEqual(expect.arrayContaining(issues));
    }
  });
});
