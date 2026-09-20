import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Input,
  Popconfirm,
  Select,
  Tag,
  Typography,
  message,
} from "../../ui/index";
import {
  DatabaseOutlined,
  PlusOutlined,
  ReloadOutlined,
  SettingOutlined,
} from "../../ui/icons";
import {
  bindDatasetStorageBackend,
  createStorageBackend,
  deleteStorageBackend,
  fetchKnowledgeBaseOptions,
  fetchStorageBackendTypes,
  fetchStorageBackends,
  patchStorageBackend,
  setDefaultStorageBackend,
  testStorageBackend,
  testStorageBackendConfig,
} from "../api/storageBackendsApi";
import {
  FALLBACK_PROVIDER_FIELDS,
  SOURCE_LABELS,
  STATUS_LABELS,
  TEST_STATUS_LABELS,
  buildCreateConfig,
  buildPatchConfig,
  formatMaskedConfigEntries,
  maskedConfigToDraft,
  projectStorageBackendError,
  providerLabel,
  resolveProviderFields,
  validateStorageConfig,
  type StorageBackend,
  type StorageBackendErrorView,
  type StorageBackendField,
  type StorageBackendProviderType,
  type StorageBackendTestResult,
  type KnowledgeBaseOption,
} from "../model/storageBackendModel";
import "../storage-backends.css";

const { Text } = Typography;

type DialogMode = "create" | "edit" | null;

interface DialogState {
  mode: DialogMode;
  editingId: string | null;
  name: string;
  provider: string;
  draft: Record<string, string>;
  testResult: StorageBackendTestResult | null;
}

const emptyDialog: DialogState = {
  mode: null,
  editingId: null,
  name: "",
  provider: "local",
  draft: {},
  testResult: null,
};

function controlMinH(className?: string) {
  return ["storage-backends-control-min-h", className].filter(Boolean).join(" ");
}

export default function StorageBackendsPanel() {
  const [items, setItems] = useState<StorageBackend[]>([]);
  const [defaultId, setDefaultId] = useState<string | null>(null);
  const [providers, setProviders] = useState<StorageBackendProviderType[]>(
    FALLBACK_PROVIDER_FIELDS,
  );
  const [kbOptions, setKbOptions] = useState<KnowledgeBaseOption[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<StorageBackendErrorView | null>(null);
  const [mutatingId, setMutatingId] = useState<string | null>(null);
  const [dialog, setDialog] = useState<DialogState>(emptyDialog);
  const [formError, setFormError] = useState<string | null>(null);
  const [bindBackendId, setBindBackendId] = useState<string>("");
  const [bindDatasetId, setBindDatasetId] = useState<string>("");
  const [bindBusy, setBindBusy] = useState(false);
  const lastInvokerRef = useRef<HTMLElement | null>(null);
  const createButtonRef = useRef<HTMLButtonElement | null>(null);

  const providerFields = useMemo(
    () => resolveProviderFields(providers, dialog.provider),
    [providers, dialog.provider],
  );

  const load = useCallback(async () => {
    setStatus("loading");
    setError(null);
    try {
      const [types, list, kbs] = await Promise.all([
        fetchStorageBackendTypes(),
        fetchStorageBackends(),
        fetchKnowledgeBaseOptions(),
      ]);
      setProviders(types.providers?.length ? types.providers : FALLBACK_PROVIDER_FIELDS);
      setItems(list.items ?? []);
      setDefaultId(list.default_storage_backend_id ?? null);
      setKbOptions(kbs);
      setStatus("ready");
    } catch (e) {
      setError(projectStorageBackendError(e));
      setStatus("error");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const closeDialog = useCallback(() => {
    setDialog(emptyDialog);
    setFormError(null);
    queueMicrotask(() => (lastInvokerRef.current ?? createButtonRef.current)?.focus?.());
  }, []);

  const openCreate = useCallback(
    (event?: { currentTarget: HTMLElement }) => {
      lastInvokerRef.current = event?.currentTarget ?? null;
      const provider = providers.find((p) => p.provider === "local")?.provider
        ?? providers[0]?.provider
        ?? "local";
      const fields = resolveProviderFields(providers, provider);
      setDialog({
        mode: "create",
        editingId: null,
        name: "",
        provider,
        draft: Object.fromEntries(fields.map((f) => [f.name, ""])),
        testResult: null,
      });
      setFormError(null);
    },
    [providers],
  );

  const openEdit = useCallback(
    (item: StorageBackend, event?: { currentTarget: HTMLElement }) => {
      lastInvokerRef.current = event?.currentTarget ?? null;
      const fields = resolveProviderFields(providers, item.provider);
      setDialog({
        mode: "edit",
        editingId: item.id,
        name: item.name,
        provider: item.provider,
        draft: maskedConfigToDraft(fields, item.config_masked),
        testResult: null,
      });
      setFormError(null);
    },
    [providers],
  );

  const onProviderChange = useCallback(
    (provider: string) => {
      const fields = resolveProviderFields(providers, provider);
      setDialog((prev) => ({
        ...prev,
        provider,
        draft: Object.fromEntries(fields.map((f) => [f.name, ""])),
        testResult: null,
      }));
    },
    [providers],
  );

  const setDraftField = useCallback((name: string, value: string) => {
    setDialog((prev) => ({
      ...prev,
      draft: { ...prev.draft, [name]: value },
    }));
  }, []);

  const handleTestInDialog = useCallback(async () => {
    const validation = validateStorageConfig(
      providerFields,
      dialog.draft,
      dialog.mode === "edit" ? "edit" : "create",
    );
    if (dialog.mode === "create" && validation) {
      setFormError(validation);
      return;
    }
    setFormError(null);
    const busyKey = dialog.editingId ?? "dialog";
    setMutatingId(busyKey);
    try {
      const result =
        dialog.mode === "edit" && dialog.editingId
          ? await testStorageBackend(dialog.editingId)
          : await testStorageBackendConfig({
              provider: dialog.provider,
              config: buildCreateConfig(providerFields, dialog.draft),
            });
      setDialog((prev) => ({ ...prev, testResult: result }));
    } catch (e) {
      const view = projectStorageBackendError(e);
      setDialog((prev) => ({
        ...prev,
        testResult: { status: "error", detail: view.description },
      }));
    } finally {
      setMutatingId(null);
    }
  }, [dialog.draft, dialog.editingId, dialog.mode, dialog.provider, providerFields]);

  const handleSave = useCallback(async () => {
    const mode = dialog.mode === "edit" ? "edit" : "create";
    const validation = validateStorageConfig(providerFields, dialog.draft, mode);
    if (validation) {
      setFormError(validation);
      return;
    }
    const name = dialog.name.trim();
    if (!name) {
      setFormError("请填写名称");
      return;
    }
    setFormError(null);
    const busyKey = dialog.editingId ?? "dialog";
    setMutatingId(busyKey);
    try {
      if (mode === "edit" && dialog.editingId) {
        await patchStorageBackend(dialog.editingId, {
          name,
          config: buildPatchConfig(providerFields, dialog.draft),
        });
        message.success("已更新存储后端");
      } else {
        await createStorageBackend({
          name,
          provider: dialog.provider,
          config: buildCreateConfig(providerFields, dialog.draft),
        });
        message.success("已创建存储后端");
      }
      closeDialog();
      await load();
    } catch (e) {
      const view = projectStorageBackendError(e);
      setFormError(view.description);
    } finally {
      setMutatingId(null);
    }
  }, [closeDialog, dialog, load, providerFields]);

  const handleTestSaved = useCallback(async (item: StorageBackend) => {
    setMutatingId(item.id);
    try {
      const result = await testStorageBackend(item.id);
      const label = TEST_STATUS_LABELS[result.status] ?? result.status;
      if (result.status === "error") {
        message.error(`${item.name}：${label} — ${result.detail}`);
      } else if (result.status === "validated_config_only") {
        message.warning(`${item.name}：${label} — ${result.detail}`);
      } else {
        message.success(`${item.name}：${label}`);
      }
    } catch (e) {
      const view = projectStorageBackendError(e);
      message.error(view.description);
    } finally {
      setMutatingId(null);
    }
  }, []);

  const handleSetDefault = useCallback(async (item: StorageBackend) => {
    setMutatingId(item.id);
    try {
      const updated = await setDefaultStorageBackend(item.id);
      message.success(`已将「${item.name}」设为默认存储后端`);
      setDefaultId(updated.id);
      setItems((prev) => prev.map((row) => ({ ...row, is_default: row.id === updated.id })));
    } catch (e) {
      const view = projectStorageBackendError(e);
      setError(view);
      message.error(view.description);
    } finally {
      setMutatingId(null);
    }
  }, []);

  const handleDelete = useCallback(
    async (item: StorageBackend) => {
      setMutatingId(item.id);
      try {
        await deleteStorageBackend(item.id);
        message.success(`已删除「${item.name}」`);
        await load();
      } catch (e) {
        const view = projectStorageBackendError(e);
        setError(view);
        message.error(view.description);
      } finally {
        setMutatingId(null);
      }
    },
    [load],
  );

  const handleBind = useCallback(async () => {
    const datasetId = bindDatasetId.trim();
    const backendId = bindBackendId.trim();
    if (!datasetId || !backendId) {
      message.warning("请选择存储后端并填写知识库 ID");
      return;
    }
    setBindBusy(true);
    try {
      await bindDatasetStorageBackend({
        dataset_id: datasetId,
        storage_backend_id: backendId,
      });
      message.success("已绑定知识库存储后端");
      setBindDatasetId("");
    } catch (e) {
      const view = projectStorageBackendError(e);
      setError(view);
      message.error(view.description);
    } finally {
      setBindBusy(false);
    }
  }, [bindBackendId, bindDatasetId]);

  const providerOptions = useMemo(
    () =>
      (providers.length ? providers : FALLBACK_PROVIDER_FIELDS).map((item) => ({
        label: item.label || providerLabel(item.provider),
        value: String(item.provider),
      })),
    [providers],
  );

  const backendOptions = useMemo(
    () =>
      items.map((item) => ({
        label: item.is_default ? `${item.name}（默认）` : item.name,
        value: item.id,
      })),
    [items],
  );

  const kbSelectOptions = useMemo(() => {
    const base = kbOptions.map((item) => ({
      label: item.name || item.id,
      value: item.id,
    }));
    const current = bindDatasetId.trim();
    if (current && !base.some((opt) => opt.value === current)) {
      return [{ label: current, value: current }, ...base];
    }
    return base;
  }, [bindDatasetId, kbOptions]);

  const isDefaultId = defaultId ?? items.find((item) => item.is_default)?.id ?? null;
  const dialogBusy = mutatingId === (dialog.editingId ?? "dialog") && dialog.mode !== null;
  const dialogLabel = dialog.mode === "edit" ? "编辑存储后端" : "新建存储后端";

  return (
    <section
      aria-labelledby="storage-backends-heading"
      className="storage-backends-section"
      data-testid="storage-backends-panel"
    >
      {error ? (
        <div className="storage-backends-error" role="group" aria-label="存储后端错误">
          <Alert type={error.kind === "conflict" ? "warning" : "error"} showIcon message={error.title} description={error.description} />
          {error.canRetry ? (
            <Button
              size="small"
              variant="outline"
              aria-label="刷新存储后端列表"
              className={controlMinH()}
              onClick={() => {
                setError(null);
                void load();
              }}
            >
              <ReloadOutlined /> 刷新
            </Button>
          ) : null}
        </div>
      ) : null}

      <Card
        size="small"
        title={
          <span id="storage-backends-heading">
            <DatabaseOutlined aria-hidden="true" /> 对象存储后端注册表
          </span>
        }
        extra={
          <div className="storage-backends-toolbar">
            <Button
              size="small"
              aria-label="刷新存储后端"
              className={controlMinH()}
              loading={status === "loading"}
              onClick={() => void load()}
            >
              <ReloadOutlined /> 刷新
            </Button>
            <Button
              ref={createButtonRef}
              size="small"
              type="primary"
              aria-label="新建存储后端"
              className={controlMinH()}
              onClick={(event: { currentTarget: HTMLElement }) => openCreate(event)}
            >
              <PlusOutlined /> 新建存储后端
            </Button>
          </div>
        }
      >
        <Text type="secondary" style={{ display: "block", marginBottom: 12 }}>
          控制面注册表：MySQL 拥有身份与绑定；接口永不回传明文密钥。空密钥字段在编辑时保持原值。
        </Text>

        {status === "loading" && items.length === 0 ? (
          <div className="storage-backend-empty" aria-busy="true">
            正在加载存储后端…
          </div>
        ) : null}

        {status !== "loading" && items.length === 0 ? (
          <div className="storage-backend-empty">暂无存储后端，点击「新建存储后端」开始注册。</div>
        ) : null}

        {items.length > 0 ? (
          <div className="storage-backend-grid" role="list" aria-label="存储后端列表">
            {items.map((item) => {
              const isDefault = item.id === isDefaultId || item.is_default;
              const entries = formatMaskedConfigEntries(item.config_masked);
              return (
                <article
                  key={item.id}
                  role="listitem"
                  className={"storage-backend-card" + (isDefault ? " is-default" : "")}
                  aria-label={`存储后端 ${item.name}`}
                >
                  <div className="storage-backend-card-head">
                    <h3 className="storage-backend-card-title">{item.name}</h3>
                    <div className="storage-backend-card-meta">
                      <Tag>{providerLabel(item.provider)}</Tag>
                      <Tag color={item.status === "active" ? "success" : "default"}>
                        {STATUS_LABELS[item.status] ?? item.status}
                      </Tag>
                      <Tag>{SOURCE_LABELS[item.source] ?? item.source}</Tag>
                      {isDefault ? <Tag color="primary">默认</Tag> : null}
                    </div>
                  </div>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    <code>{item.id}</code>
                  </Text>
                  {entries.length ? (
                    <ul
                      className="storage-backend-config-list"
                      aria-label={`${item.name} 配置摘要`}
                    >
                      {entries.map((entry) => (
                        <li key={entry.key}>
                          {entry.label}: <code>{entry.value}</code>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      无公开配置字段
                    </Text>
                  )}
                  <div className="storage-backend-card-actions">
                    <Button
                      size="small"
                      aria-label={`编辑存储后端 ${item.name}`}
                      className={controlMinH()}
                      disabled={mutatingId === item.id}
                      onClick={(event: { currentTarget: HTMLElement }) => openEdit(item, event)}
                    >
                      编辑
                    </Button>
                    <Button
                      size="small"
                      aria-label={`测试连接 ${item.name}`}
                      className={controlMinH()}
                      disabled={mutatingId === item.id}
                      loading={mutatingId === item.id}
                      onClick={() => void handleTestSaved(item)}
                    >
                      测试连接
                    </Button>
                    <Button
                      size="small"
                      aria-label={`设为默认 ${item.name}`}
                      className={controlMinH()}
                      disabled={isDefault || mutatingId === item.id}
                      onClick={() => void handleSetDefault(item)}
                    >
                      设为默认
                    </Button>
                    <Popconfirm
                      title={`删除存储后端「${item.name}」？`}
                      description="默认后端或已绑定知识库的后端将被拒绝删除（409）。"
                      okText="确认删除"
                      cancelText="取消"
                      onConfirm={() => void handleDelete(item)}
                    >
                      <Button
                        size="small"
                        danger
                        aria-label={`删除存储后端 ${item.name}`}
                        className={controlMinH()}
                        disabled={mutatingId === item.id}
                      >
                        删除
                      </Button>
                    </Popconfirm>
                  </div>
                </article>
              );
            })}
          </div>
        ) : null}

        <div className="storage-backend-bind-bar" role="group" aria-label="知识库绑定">
          <label className="storage-backend-form-field">
            <span>存储后端</span>
            <Select
              aria-label="选择存储后端用于绑定"
              className={controlMinH()}
              value={bindBackendId || undefined}
              options={backendOptions}
              onChange={(value: string) => setBindBackendId(String(value ?? ""))}
            />
          </label>
          <label className="storage-backend-form-field">
            <span>知识库 ID{kbOptions.length === 0 ? "（可手动输入）" : ""}</span>
            <Select
              aria-label="选择知识库用于绑定"
              className={controlMinH()}
              allowClear
              showSearch
              value={bindDatasetId || undefined}
              options={kbSelectOptions}
              onChange={(value: string) => setBindDatasetId(String(value ?? ""))}
            />
          </label>
          <Button
            type="primary"
            aria-label="绑定知识库存储后端"
            className={controlMinH()}
            loading={bindBusy}
            disabled={!bindBackendId || !bindDatasetId || bindBusy}
            onClick={() => void handleBind()}
          >
            <SettingOutlined /> 绑定知识库
          </Button>
        </div>
      </Card>

      {dialog.mode !== null ? (
        <div className="rag-modal-mask">
          <div
            role="dialog"
            aria-modal="true"
            aria-label={dialogLabel}
            className="rag-modal storage-backend-dialog"
          >
            <h2>{dialogLabel}</h2>
            <div className="storage-backend-form">
              {formError ? <Alert type="error" showIcon message={formError} /> : null}

              <label className="storage-backend-form-field">
                <span>名称</span>
                <Input
                  aria-label="存储后端名称"
                  className={controlMinH()}
                  value={dialog.name}
                  maxLength={128}
                  onChange={(e: { target: { value: string } }) =>
                    setDialog((prev) => ({ ...prev, name: e.target.value }))
                  }
                />
              </label>

              <label className="storage-backend-form-field">
                <span>Provider</span>
                <Select
                  aria-label="存储 Provider"
                  className={controlMinH()}
                  value={dialog.provider}
                  options={providerOptions}
                  disabled={dialog.mode === "edit"}
                  onChange={(value: string) => onProviderChange(String(value ?? ""))}
                />
              </label>

              {providerFields.length === 0 ? (
                <Text type="secondary">该 Provider 暂无表单字段定义，请使用通用配置键。</Text>
              ) : (
                providerFields.map((field: StorageBackendField) => (
                  <label key={field.name} className="storage-backend-form-field">
                    <span>
                      {field.label}
                      {field.required ? " *" : ""}
                      {field.secret ? "（密钥）" : ""}
                    </span>
                    <Input
                      aria-label={field.label}
                      className={controlMinH()}
                      type={field.secret ? "password" : "text"}
                      autoComplete={field.secret ? "new-password" : "off"}
                      value={dialog.draft[field.name] ?? ""}
                      placeholder={
                        field.secret && dialog.mode === "edit"
                          ? "留空保持现有密钥"
                          : field.required
                            ? "必填"
                            : "可选"
                      }
                      onChange={(e: { target: { value: string } }) =>
                        setDraftField(field.name, e.target.value)
                      }
                    />
                    {field.secret ? (
                      <small className="storage-backend-secret-hint">
                        响应始终脱敏；编辑时留空不修改原密钥。
                      </small>
                    ) : null}
                  </label>
                ))
              )}

              <div className="storage-backends-toolbar">
                <Button
                  aria-label="测试连接配置"
                  className={controlMinH()}
                  loading={dialogBusy}
                  onClick={() => void handleTestInDialog()}
                >
                  测试连接
                </Button>
              </div>

              {dialog.testResult ? (
                <div
                  className="storage-backend-test-result"
                  role="status"
                  aria-label="连接测试结果"
                >
                  <Alert
                    type={
                      dialog.testResult.status === "ok"
                        ? "success"
                        : dialog.testResult.status === "validated_config_only"
                          ? "warning"
                          : "error"
                    }
                    showIcon
                    message={TEST_STATUS_LABELS[dialog.testResult.status] ?? dialog.testResult.status}
                    description={dialog.testResult.detail}
                  />
                </div>
              ) : null}
            </div>
            <div className="storage-backend-dialog-actions">
              <Button aria-label="取消存储后端编辑" className={controlMinH()} onClick={closeDialog}>
                取消
              </Button>
              <Button
                type="primary"
                aria-label={dialog.mode === "edit" ? "保存存储后端" : "创建存储后端"}
                className={controlMinH()}
                loading={dialogBusy}
                onClick={() => void handleSave()}
              >
                {dialog.mode === "edit" ? "保存修改" : "创建"}
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
