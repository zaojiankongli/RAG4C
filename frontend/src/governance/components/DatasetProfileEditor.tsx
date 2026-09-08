import { Alert, Checkbox, Drawer, Input, Select } from "tdesign-react";
import { useEffect, useState } from "react";
import AccessibleInput from "./AccessibleInput";
import type { DatasetProfile, DatasetProfilePatch, DatasetVisibility, GovernanceErrorView } from "../model/governanceModel";

interface Props {
  profile: DatasetProfile;
  visible: boolean;
  saving: boolean;
  onClose: () => void;
  onSave: (payload: DatasetProfilePatch) => Promise<boolean>;
  error?: GovernanceErrorView | null;
}

const visibilityOptions = [
  { label: "仅自己", value: "private" },
  { label: "租户内", value: "tenant" },
  { label: "公开", value: "public" },
];

export default function DatasetProfileEditor({ profile, visible, saving, onClose, onSave, error }: Props) {
  const [ownerId, setOwnerId] = useState(profile.owner_id ?? "");
  const [visibility, setVisibility] = useState<DatasetVisibility>(profile.visibility);
  const [language, setLanguage] = useState(profile.default_language);
  const [graphEnabled, setGraphEnabled] = useState(profile.graph_enabled);
  const [qaEnabled, setQaEnabled] = useState(profile.qa_enabled);
  const [languageError, setLanguageError] = useState("");

  useEffect(() => {
    if (!visible) return;
    setOwnerId(profile.owner_id ?? "");
    setVisibility(profile.visibility);
    setLanguage(profile.default_language);
    setGraphEnabled(profile.graph_enabled);
    setQaEnabled(profile.qa_enabled);
    setLanguageError("");
  }, [profile, visible]);

  const save = async () => {
    if (!language.trim()) { setLanguageError("默认语言不能为空。"); return; }
    const succeeded = await onSave({
      expected_revision: profile.profile_revision,
      owner_id: ownerId.trim() || null,
      visibility,
      default_language: language.trim(),
      graph_enabled: graphEnabled,
      qa_enabled: qaEnabled,
    });
    if (succeeded) onClose();
  };

  return (
    <Drawer
      className="governance-drawer"
      visible={visible}
      header="编辑数据集资料"
      size="480px"
      destroyOnClose
      closeOnEscKeydown={!saving}
      closeOnOverlayClick={!saving}
      confirmBtn={{ content: "保存资料", theme: "primary", loading: saving }}
      cancelBtn={{ content: "取消", disabled: saving }}
      onConfirm={() => void save()}
      onClose={onClose}
      {...({ role: "dialog", "aria-modal": "true", "aria-label": "编辑数据集资料" } as Record<string, unknown>)}
    >
      <div className="governance-form-grid">
        {error?.kind === "conflict" ? <Alert theme="warning" title={error.title} message={error.description} /> : null}
        <label className="governance-field">
          <span>负责人 ID</span>
          <Input value={ownerId} onChange={(value) => setOwnerId(String(value))} />
        </label>
        <label className="governance-field">
          <span>可见范围</span>
          <Select
            value={visibility}
            options={visibilityOptions}
            onChange={(value) => setVisibility(value as DatasetVisibility)}
          />
        </label>
        <label className="governance-field">
          <span>默认语言</span>
          <AccessibleInput value={language} status={languageError ? "error" : "default"} ariaInvalid={Boolean(languageError)} ariaDescribedBy={languageError ? "dataset-language-error" : undefined} onChange={(value) => { setLanguage(String(value)); setLanguageError(""); }} />
          {languageError ? <small id="dataset-language-error" role="alert">{languageError}</small> : null}
        </label>
        <div className="governance-checkboxes" role="group" aria-label="数据集能力开关">
          <Checkbox checked={graphEnabled} onChange={(checked) => setGraphEnabled(Boolean(checked))}>
            启用知识图谱
          </Checkbox>
          <Checkbox checked={qaEnabled} onChange={(checked) => setQaEnabled(Boolean(checked))}>
            启用 QA 知识
          </Checkbox>
        </div>
        <p className="governance-safe-note">
          自定义资料和策略中的任意值不会在编辑器中回显；策略凭据仅以引用标记展示。
        </p>
      </div>
    </Drawer>
  );
}
