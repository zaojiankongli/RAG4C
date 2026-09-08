import { Alert, Button, Card, Checkbox, Dialog, Input, Tag, Textarea, Timeline } from "tdesign-react";
import { AddIcon, FileSearchIcon, RefreshIcon } from "tdesign-icons-react";
import { useEffect, useRef, useState } from "react";
import type { GovernanceLoadStatus } from "../hooks/useDatasetGovernance";
import AccessibleInput from "./AccessibleInput";
import { safeReference, validateVersionDates, type DocumentVersion, type DocumentVersionCreate, type GovernanceErrorView, type JsonObject } from "../model/governanceModel";

interface Props {
  status: GovernanceLoadStatus;
  documentId: string;
  versions: DocumentVersion[];
  error: GovernanceErrorView | null;
  creating: boolean;
  onInspect: (documentId: string) => Promise<boolean>;
  onRefresh: () => Promise<boolean>;
  onCreate: (payload: DocumentVersionCreate) => Promise<boolean>;
  truncated?: boolean;
  readOnly?: boolean;
}

function parseObject(value: string, label: string): JsonObject {
  if (!value.trim()) return {};
  const parsed = JSON.parse(value) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error(`${label}必须是 JSON 对象。`);
  return parsed as JsonObject;
}

export default function DocumentVersionInspector(props: Props) {
  const [candidateId, setCandidateId] = useState(props.documentId);
  const [creatingOpen, setCreatingOpen] = useState(false);
  const [sourceIdentity, setSourceIdentity] = useState("");
  const [sourceHash, setSourceHash] = useState("");
  const [policyText, setPolicyText] = useState("");
  const [metadataText, setMetadataText] = useState("");
  const [contentRef, setContentRef] = useState("");
  const [reason, setReason] = useState("");
  const [effectiveFrom, setEffectiveFrom] = useState("");
  const [expiresAt, setExpiresAt] = useState("");
  const [purgeAfter, setPurgeAfter] = useState("");
  const [overrideRetrieval, setOverrideRetrieval] = useState(false);
  const [retrievalEnabled, setRetrievalEnabled] = useState(true);
  const [validation, setValidation] = useState("");
  const createButtonRef = useRef<HTMLButtonElement | null>(null);
  const createInvokerRef = useRef<HTMLElement | null>(null);
  const head = props.versions[0] ?? null;

  useEffect(() => setCandidateId(props.documentId), [props.documentId]);
  const wasOpenRef = useRef(false);
  useEffect(() => { if (wasOpenRef.current && !creatingOpen) setTimeout(() => (createInvokerRef.current ?? createButtonRef.current)?.focus(), 0); wasOpenRef.current = creatingOpen; }, [creatingOpen]);
  useEffect(() => {
    if (!creatingOpen) return;
    setSourceIdentity("");
    setSourceHash("");
    setPolicyText("");
    setMetadataText("");
    setContentRef("");
    setReason("");
    setEffectiveFrom("");
    setExpiresAt("");
    setPurgeAfter("");
    setOverrideRetrieval(false);
    setRetrievalEnabled(true);
    setValidation("");
  }, [creatingOpen]);

  const inspect = () => {
    const value = candidateId.trim();
    if (value) void props.onInspect(value);
  };

  const create = async () => {
    if (!sourceIdentity.trim()) {
      setValidation("来源标识不能为空。");
      return;
    }
    if (!/^[0-9a-fA-F]{64}$/.test(sourceHash.trim())) {
      setValidation("来源 SHA-256 必须是 64 位十六进制字符串。");
      return;
    }
    const dateError = validateVersionDates(effectiveFrom, expiresAt, purgeAfter); if (dateError) { setValidation(dateError); return; }
    try {
      const payload: DocumentVersionCreate = {
        expected_current_revision: head?.revision ?? 0,
        ...(head ? { expected_current_version_id: head.id } : {}),
        source_identity: sourceIdentity.trim(),
        source_hash: sourceHash.trim(),
        parser_policy_snapshot: parseObject(policyText, "解析策略快照"),
        parser_metadata: parseObject(metadataText, "解析元数据"),
        source_content_ref: contentRef.trim(),
        change_reason: reason.trim(),
        ...(effectiveFrom.trim() ? { effective_from: effectiveFrom.trim() } : {}),
        ...(expiresAt.trim() ? { expires_at: expiresAt.trim() } : {}),
        ...(purgeAfter.trim() ? { purge_after: purgeAfter.trim() } : {}),
        ...(overrideRetrieval ? { retrieval_enabled: retrievalEnabled } : {}),
      };
      if (await props.onCreate(payload)) setCreatingOpen(false);
    } catch (caught) {
      setValidation(caught instanceof Error ? caught.message : "版本表单无效。");
    }
  };

  return (
    <section aria-labelledby="document-version-heading" className="governance-section">
      {props.error ? (
        <Alert
          theme={props.error.kind === "conflict" ? "warning" : "error"}
          title={props.error.title}
          message={props.error.description}
          operation={props.error.canRetry && props.documentId ? <Button size="small" variant="outline" onClick={() => void props.onRefresh()}><RefreshIcon /> 刷新版本</Button> : undefined}
        />
      ) : null}
      <Card
        title={<span id="document-version-heading">文档版本检查器</span>}
        subtitle="Document Identity 保持稳定，每个版本都是不可变来源和解析策略快照"
        actions={props.documentId && !props.readOnly ? <Button ref={createButtonRef} theme="primary" aria-label="创建文档版本" onClick={(event) => { createInvokerRef.current=event.currentTarget; setCreatingOpen(true); }}><AddIcon /> 创建版本</Button> : undefined}
      >
        {props.truncated ? <Alert theme="warning" title="结果可能被截断" message="后端最多返回 100 个版本，当前时间线不保证完整。" /> : null}
        <div className="governance-version-search">
          <label className="governance-field">
            <span>文档 ID</span>
            <Input value={candidateId} onChange={(value) => setCandidateId(String(value))} onEnter={inspect} />
          </label>
          <Button tag="button" theme="primary" variant="outline" disabled={!candidateId.trim()} loading={props.status === "loading"} aria-label="检查文档版本" onClick={inspect}><FileSearchIcon /> 检查版本</Button>
        </div>
        {!props.documentId ? (
          <div className="governance-version-empty"><FileSearchIcon /><strong>输入文档 ID 后读取不可变版本时间线</strong><span>不会自动选择或猜测文档。</span></div>
        ) : props.versions.length ? (
          <Timeline mode="same" theme="dot">
            {props.versions.map((version, index) => (
              <Timeline.Item
                key={version.id}
                label={<time dateTime={version.created_at}>{new Date(version.created_at).toLocaleString("zh-CN")}</time>}
                dotColor={index === 0 ? "primary" : "default"}
              >
                <article className="governance-version-card">
                  <div><strong>Revision {version.revision}</strong>{index === 0 ? <Tag theme="primary">当前头版本</Tag> : <Tag variant="light-outline">不可变历史</Tag>}</div>
                  <p>{version.source_identity}</p>
                  <code>{version.source_hash}</code>
                  <dl>
                    <div><dt>变更原因</dt><dd>{version.change_reason || "未填写"}</dd></div>
                    <div><dt>创建者</dt><dd>{version.created_by}</dd></div>
                    <div><dt>解析策略字段</dt><dd>{Object.keys(version.parser_policy_snapshot).length}</dd></div>
                    <div><dt>解析元数据字段</dt><dd>{Object.keys(version.parser_metadata).length}</dd></div>
                  </dl>
                  {version.source_content_ref ? <Tag variant="light-outline">{safeReference(version.source_content_ref)}</Tag> : null}
                </article>
              </Timeline.Item>
            ))}
          </Timeline>
        ) : (
          <div className="governance-version-empty"><strong>该文档暂无版本</strong><span>可以使用当前修订 0 创建首个版本。</span></div>
        )}
      </Card>
      {creatingOpen ? (
        <Dialog
          className="governance-dialog"
          visible
          header="创建文档版本"
          width={760}
          destroyOnClose
          confirmBtn={{ content: "提交新版本", theme: "primary" }}
          cancelBtn={{ content: "取消" }}
          confirmLoading={props.creating}
          onClose={() => { setCreatingOpen(false); queueMicrotask(() => (createInvokerRef.current ?? createButtonRef.current)?.focus()); }}
          onConfirm={() => void create()}
          {...({ role: "dialog", "aria-modal": "true", "aria-label": "创建文档版本" } as Record<string, unknown>)}
        >
          <div className="governance-form-grid governance-form-grid--version">
            {props.error?.kind === "conflict" ? <Alert theme="warning" title={props.error.title} message={props.error.description} /> : null}
            {validation ? <div id="version-validation-error"><Alert theme="error" title="无法创建版本" message={validation} /></div> : null}
            <div className="governance-version-fence governance-field--wide">
              <span>当前修订 <strong>{head?.revision ?? 0}</strong></span>
              <span>当前版本 ID <strong>{head?.id ?? "无"}</strong></span>
            </div>
            <label className="governance-field"><span>来源标识</span><AccessibleInput ariaInvalid={validation.includes("来源标识")} ariaDescribedBy={validation.includes("来源标识") ? "version-validation-error" : undefined} value={sourceIdentity} onChange={(value) => setSourceIdentity(String(value))} /></label>
            <label className="governance-field"><span>来源 SHA-256</span><AccessibleInput value={sourceHash} ariaInvalid={validation.includes("SHA-256")} ariaDescribedBy={validation.includes("SHA-256") ? "version-validation-error" : undefined} onChange={(value) => { setSourceHash(String(value)); setValidation(""); }} /></label>
            <label className="governance-field governance-field--wide"><span>解析策略快照 JSON</span><Textarea aria-invalid={validation.includes("解析策略快照")} aria-describedby={validation.includes("解析策略快照") ? "version-validation-error" : undefined} value={policyText} onChange={(value) => setPolicyText(String(value))} autosize={{ minRows: 3, maxRows: 8 }} /></label>
            <label className="governance-field governance-field--wide"><span>解析元数据 JSON</span><Textarea aria-invalid={validation.includes("解析元数据")} aria-describedby={validation.includes("解析元数据") ? "version-validation-error" : undefined} value={metadataText} onChange={(value) => setMetadataText(String(value))} autosize={{ minRows: 3, maxRows: 8 }} /></label>
            <label className="governance-field governance-field--wide"><span>来源内容引用</span><Input value={contentRef} onChange={(value) => setContentRef(String(value))} /></label>
            <label className="governance-field governance-field--wide"><span>变更原因</span><Input value={reason} onChange={(value) => setReason(String(value))} /></label>
            <label className="governance-field"><span>生效时间（ISO 8601）</span><AccessibleInput value={effectiveFrom} ariaInvalid={validation.includes("生效时间")} ariaDescribedBy={validation ? "version-validation-error" : undefined} onChange={(value) => { setEffectiveFrom(String(value)); setValidation(""); }} /></label>
            <label className="governance-field"><span>过期时间（ISO 8601）</span><AccessibleInput value={expiresAt} ariaInvalid={validation.includes("过期时间") || validation.includes("不能晚于")} ariaDescribedBy={validation ? "version-validation-error" : undefined} onChange={(value) => { setExpiresAt(String(value)); setValidation(""); }} /></label>
            <label className="governance-field"><span>清理时间（ISO 8601）</span><AccessibleInput ariaInvalid={validation.includes("清理时间")} ariaDescribedBy={validation.includes("清理时间") ? "version-validation-error" : undefined} value={purgeAfter} onChange={(value) => setPurgeAfter(String(value))} /></label>
            <div className="governance-checkboxes">
              <Checkbox checked={overrideRetrieval} onChange={(checked) => setOverrideRetrieval(Boolean(checked))}>覆盖检索开关</Checkbox>
              <Checkbox checked={retrievalEnabled} disabled={!overrideRetrieval} onChange={(checked) => setRetrievalEnabled(Boolean(checked))}>允许检索</Checkbox>
            </div>
          </div>
        </Dialog>
      ) : null}
    </section>
  );
}
