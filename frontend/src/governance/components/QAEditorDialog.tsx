import { Alert, Dialog, Input, Select, Textarea } from "tdesign-react";
import { useEffect, useState } from "react";
import AccessibleInput from "./AccessibleInput";
import { validateDateRange, type GovernanceErrorView, type QACreate, type QAKnowledge, type QAOrigin, type QAUpdate } from "../model/governanceModel";

interface Props {
  visible: boolean;
  qa: QAKnowledge | null;
  saving: boolean;
  onClose: () => void;
  onCreate: (payload: QACreate) => Promise<boolean>;
  onUpdate: (qaId: string, payload: QAUpdate) => Promise<boolean>;
  error?: GovernanceErrorView | null;
}

const originOptions = [
  { label: "人工维护", value: "manual" },
  { label: "自动生成", value: "automatic" },
];

export default function QAEditorDialog({ visible, qa, saving, onClose, onCreate, onUpdate, error }: Props) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [origin, setOrigin] = useState<QAOrigin>("manual");
  const [sourceDocumentId, setSourceDocumentId] = useState("");
  const [sourceUri, setSourceUri] = useState("");
  const [metadataText, setMetadataText] = useState("");
  const [effectiveFrom, setEffectiveFrom] = useState("");
  const [expiresAt, setExpiresAt] = useState("");
  const [validation, setValidation] = useState("");

  useEffect(() => {
    if (!visible) return;
    setQuestion(qa?.question ?? "");
    setAnswer(qa?.answer ?? "");
    setOrigin(qa?.origin ?? "manual");
    setSourceDocumentId(qa?.source_document_id ?? "");
    setSourceUri(qa?.source_uri ?? "");
    setMetadataText("");
    setEffectiveFrom(qa?.effective_from ?? "");
    setExpiresAt(qa?.expires_at ?? "");
    setValidation("");
  }, [qa, visible]);

  if (!visible) return null;

  const submit = async () => {
    if (!question.trim() || !answer.trim()) {
      setValidation("问题和答案不能为空。");
      return;
    }
    const dateError = validateDateRange(effectiveFrom, expiresAt); if (dateError) { setValidation(dateError); return; }
    let metadata: Record<string, unknown> | undefined;
    if (metadataText.trim()) {
      try {
        const parsed = JSON.parse(metadataText) as unknown;
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
        metadata = parsed as Record<string, unknown>;
      } catch {
        setValidation("元数据必须是 JSON 对象。");
        return;
      }
    }
    const common = {
      question: question.trim(),
      answer: answer.trim(),
      source_document_id: sourceDocumentId.trim() || null,
      source_uri: sourceUri.trim(),
      effective_from: effectiveFrom.trim() || null,
      expires_at: expiresAt.trim() || null,
    };
    const succeeded = qa
      ? await onUpdate(qa.id, {
          expected_revision: qa.revision,
          ...common,
          ...(metadata ? { metadata } : {}),
        })
      : await onCreate({ ...common, origin, metadata: metadata ?? {} });
    if (succeeded) onClose();
  };

  return (
    <Dialog
      className="governance-dialog"
      visible
      header={qa ? "编辑 QA" : "新建 QA"}
      width={720}
      destroyOnClose
      closeOnEscKeydown={!saving}
      closeOnOverlayClick={!saving}
      confirmBtn={{ content: "保存 QA", theme: "primary" }}
      cancelBtn={{ content: "取消" }}
      confirmLoading={saving}
      onClose={onClose}
      onConfirm={() => void submit()}
      {...({ role: "dialog", "aria-modal": "true", "aria-label": qa ? "编辑 QA" : "新建 QA" } as Record<string, unknown>)}
    >
      <div className="governance-form-grid governance-form-grid--qa">
        {error?.kind === "conflict" ? <Alert theme="warning" title={error.title} message={error.description} /> : null}
        {validation ? <div id="qa-validation-error"><Alert theme="error" title="无法保存 QA" message={validation} /></div> : null}
        <label className="governance-field governance-field--wide">
          <span>问题</span>
          <Textarea aria-invalid={validation.includes("问题和答案")} aria-describedby={validation.includes("问题和答案") ? "qa-validation-error" : undefined} value={question} onChange={(value) => setQuestion(String(value))} autosize={{ minRows: 2, maxRows: 5 }} />
        </label>
        <label className="governance-field governance-field--wide">
          <span>答案</span>
          <Textarea aria-invalid={validation.includes("问题和答案")} aria-describedby={validation.includes("问题和答案") ? "qa-validation-error" : undefined} value={answer} onChange={(value) => setAnswer(String(value))} autosize={{ minRows: 4, maxRows: 10 }} />
        </label>
        {!qa ? (
          <label className="governance-field">
            <span>来源</span>
            <Select value={origin} options={originOptions} onChange={(value) => setOrigin(value as QAOrigin)} />
          </label>
        ) : null}
        <label className="governance-field">
          <span>来源文档 ID</span>
          <Input value={sourceDocumentId} onChange={(value) => setSourceDocumentId(String(value))} />
        </label>
        <label className="governance-field governance-field--wide">
          <span>来源 URI</span>
          <Input value={sourceUri} onChange={(value) => setSourceUri(String(value))} />
        </label>
        <label className="governance-field">
          <span>生效时间（ISO 8601）</span>
          <AccessibleInput value={effectiveFrom} ariaInvalid={validation.includes("生效时间")} ariaDescribedBy={validation.includes("生效时间") ? "qa-validation-error" : undefined} onChange={(value) => { setEffectiveFrom(String(value)); setValidation(""); }} />
        </label>
        <label className="governance-field">
          <span>过期时间（ISO 8601）</span>
          <AccessibleInput value={expiresAt} ariaInvalid={validation.includes("过期时间") || validation.includes("不能晚于")} ariaDescribedBy={validation ? "qa-validation-error" : undefined} onChange={(value) => { setExpiresAt(String(value)); setValidation(""); }} />
        </label>
        <label className="governance-field governance-field--wide">
          <span>替换元数据（JSON，可留空）</span>
          <Textarea aria-invalid={validation.includes("元数据")} aria-describedby={validation.includes("元数据") ? "qa-validation-error" : undefined} value={metadataText} onChange={(value) => setMetadataText(String(value))} autosize={{ minRows: 3, maxRows: 8 }} />
        </label>
        <p className="governance-safe-note governance-field--wide">现有元数据不会回显，以避免在治理界面暴露潜在敏感值。</p>
      </div>
    </Dialog>
  );
}
