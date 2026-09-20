// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

afterEach(cleanup);

import type {
  StorageBackend,
  StorageBackendListResponse,
  StorageBackendProviderType,
  StorageBackendTestResult,
} from "../model/storageBackendModel";
import StorageBackendsPanel from "./StorageBackendsPanel";
import * as api from "../api/storageBackendsApi";

vi.mock("../api/storageBackendsApi", () => ({
  fetchStorageBackendTypes: vi.fn(),
  fetchStorageBackends: vi.fn(),
  fetchKnowledgeBaseOptions: vi.fn(),
  createStorageBackend: vi.fn(),
  patchStorageBackend: vi.fn(),
  deleteStorageBackend: vi.fn(),
  testStorageBackend: vi.fn(),
  testStorageBackendConfig: vi.fn(),
  setDefaultStorageBackend: vi.fn(),
  bindDatasetStorageBackend: vi.fn(),
}));

const providers: StorageBackendProviderType[] = [
  {
    provider: "local",
    label: "本地目录",
    fields: [{ name: "root_path", label: "根路径", required: true }],
  },
  {
    provider: "minio",
    label: "MinIO",
    fields: [
      { name: "endpoint", label: "Endpoint", required: true },
      { name: "bucket", label: "存储桶", required: true },
      { name: "access_key_id", label: "Access Key ID", required: true },
      { name: "secret_access_key", label: "Secret Access Key", required: true, secret: true },
    ],
  },
];

const localBackend: StorageBackend = {
  id: "sb-local",
  name: "local-data",
  provider: "local",
  status: "active",
  source: "user",
  is_default: false,
  config_masked: { root_path: "/var/rag4c" },
  created_at: "2026-09-20T00:00:00Z",
  updated_at: "2026-09-20T00:00:00Z",
};

const minioDefault: StorageBackend = {
  id: "sb-minio",
  name: "prod-minio",
  provider: "minio",
  status: "active",
  source: "user",
  is_default: true,
  config_masked: {
    endpoint: "http://minio:9000",
    bucket: "rag4c",
    access_key_id: "ak_***wxyz",
    secret_access_key: "super-secret-plain",
  },
  created_at: "2026-09-20T00:00:00Z",
  updated_at: "2026-09-20T00:00:00Z",
};

const listResponse: StorageBackendListResponse = {
  items: [localBackend, minioDefault],
  default_storage_backend_id: "sb-minio",
};

function mockReady() {
  vi.mocked(api.fetchStorageBackendTypes).mockResolvedValue({ providers });
  vi.mocked(api.fetchStorageBackends).mockResolvedValue(listResponse);
  vi.mocked(api.fetchKnowledgeBaseOptions).mockResolvedValue([
    { id: "dataset-a", name: "Support KB" },
  ]);
}

beforeEach(() => {
  vi.clearAllMocks();
  mockReady();
});

describe("StorageBackendsPanel", () => {
  it("lists backends with masked credentials and default marker; never shows plaintext secrets", async () => {
    render(<StorageBackendsPanel />);

    await waitFor(() => expect(screen.getByTestId("storage-backends-panel")).toBeTruthy());
    expect(await screen.findByRole("list", { name: "存储后端列表" })).toBeTruthy();
    expect(screen.getByLabelText("存储后端 prod-minio")).toBeTruthy();
    expect(screen.getByLabelText("存储后端 local-data")).toBeTruthy();
    expect(screen.getAllByText("默认").length).toBeGreaterThan(0);
    expect(document.body.textContent).toContain("ak_***wxyz");
    expect(document.body.textContent).not.toContain("super-secret-plain");
    expect(document.body.textContent).toContain("***");

    const bindButton = screen.getByRole("button", { name: "绑定知识库存储后端" });
    expect(bindButton.getAttribute("class") || "").toContain("storage-backends-control-min-h");
  });

  it("opens create dialog, tests config-only connection, and creates with required fields", async () => {
    const user = userEvent.setup();
    vi.mocked(api.testStorageBackendConfig).mockResolvedValue({
      status: "validated_config_only",
      detail: "结构校验通过",
    } satisfies StorageBackendTestResult);
    vi.mocked(api.createStorageBackend).mockResolvedValue(localBackend);

    render(<StorageBackendsPanel />);
    await user.click(await screen.findByRole("button", { name: "新建存储后端" }));

    const dialog = await screen.findByRole("dialog", { name: "新建存储后端" });
    expect(dialog).toBeTruthy();

    fireEvent.change(screen.getByRole("textbox", { name: "存储后端名称" }), {
      target: { value: "local-root" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "根路径" }), {
      target: { value: "/data/rag4c" },
    });

    await user.click(screen.getByRole("button", { name: "测试连接配置" }));
    await waitFor(() =>
      expect(api.testStorageBackendConfig).toHaveBeenCalledWith({
        provider: "local",
        config: { root_path: "/data/rag4c" },
      }),
    );
    expect(await screen.findByRole("status", { name: "连接测试结果" })).toBeTruthy();
    expect(document.body.textContent).toContain("仅校验配置");

    await user.click(screen.getByRole("button", { name: "创建存储后端" }));
    await waitFor(() =>
      expect(api.createStorageBackend).toHaveBeenCalledWith({
        name: "local-root",
        provider: "local",
        config: { root_path: "/data/rag4c" },
      }),
    );
  });

  it("edits a backend keeping empty secret fields out of the PATCH config", async () => {
    const user = userEvent.setup();
    vi.mocked(api.patchStorageBackend).mockResolvedValue(minioDefault);

    render(<StorageBackendsPanel />);
    await user.click(await screen.findByRole("button", { name: "编辑存储后端 prod-minio" }));

    await screen.findByRole("dialog", { name: "编辑存储后端" });
    const secretInput = screen.getByLabelText("Secret Access Key") as HTMLInputElement;
    expect(secretInput.value).toBe("");
    expect(secretInput.placeholder).toContain("留空保持现有密钥");

    await user.click(screen.getByRole("button", { name: "保存存储后端" }));
    await waitFor(() => expect(api.patchStorageBackend).toHaveBeenCalled());
    const patched = vi.mocked(api.patchStorageBackend).mock.calls[0][1];
    expect(patched.name).toBe("prod-minio");
    expect(patched.config).not.toHaveProperty("secret_access_key");
    expect(patched.config?.endpoint).toBe("http://minio:9000");
  });

  it("tests saved backend, sets default, confirms delete, and binds a dataset", async () => {
    const user = userEvent.setup();
    const okTest: StorageBackendTestResult = { status: "ok", detail: "写探测通过" };
    vi.mocked(api.testStorageBackend).mockResolvedValue(okTest);
    vi.mocked(api.setDefaultStorageBackend).mockResolvedValue({
      ...localBackend,
      is_default: true,
    });
    vi.mocked(api.deleteStorageBackend).mockResolvedValue(undefined);
    vi.mocked(api.bindDatasetStorageBackend).mockResolvedValue({ ok: true });

    render(<StorageBackendsPanel />);

    await user.click(await screen.findByRole("button", { name: "测试连接 local-data" }));
    await waitFor(() => expect(api.testStorageBackend).toHaveBeenCalledWith("sb-local"));

    await user.click(screen.getByRole("button", { name: "设为默认 local-data" }));
    await waitFor(() => expect(api.setDefaultStorageBackend).toHaveBeenCalledWith("sb-local"));

    await user.click(screen.getByRole("button", { name: "删除存储后端 local-data" }));
    const confirm = await screen.findByRole("button", { name: "确认删除" });
    await user.click(confirm);
    await waitFor(() => expect(api.deleteStorageBackend).toHaveBeenCalledWith("sb-local"));

    const backendSelect = screen.getByRole("combobox", { name: "选择存储后端用于绑定" });
    fireEvent.change(backendSelect, { target: { value: "sb-minio" } });
    const kbSelect = screen.getByRole("combobox", { name: "选择知识库用于绑定" });
    fireEvent.change(kbSelect, { target: { value: "dataset-a" } });

    await waitFor(() => {
      const btn = screen.getByRole("button", { name: "绑定知识库存储后端" }) as HTMLButtonElement;
      expect(btn.disabled).toBe(false);
    });
    await user.click(screen.getByRole("button", { name: "绑定知识库存储后端" }));
    await waitFor(() =>
      expect(api.bindDatasetStorageBackend).toHaveBeenCalledWith({
        dataset_id: "dataset-a",
        storage_backend_id: "sb-minio",
      }),
    );
  });

  it("surfaces list load errors with retry", async () => {
    vi.mocked(api.fetchStorageBackends).mockRejectedValue(new Error("offline"));
    render(<StorageBackendsPanel />);
    expect(await screen.findByRole("button", { name: "刷新存储后端列表" })).toBeTruthy();
    expect(document.body.textContent).toContain("对象存储后端不可用");
  });
});
