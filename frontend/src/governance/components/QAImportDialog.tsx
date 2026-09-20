import { Alert, Button, Dialog, Textarea } from "tdesign-react";
import { useEffect, useRef, useState } from "react";
import { DownloadIcon } from "tdesign-icons-react";
import {
  parseQAImportText,
  type GovernanceErrorView,
  type QAImportRequest,
  type QAImportResult,
} from "../model/governanceModel";

interface Props {
  visible: boolean;
  busy: boolean;
  error?: GovernanceErrorView | null;
  result: QAImportResult | null;
  onClose: () => void;
  onImport: (payload: QAImportRequest) => Promise<QAImportResult | null>;
}

export default function QAImportDialog({ visible, busy, error, result, onClose, onImport }: Props) {
  const [payload, setPayload] = useState("");
  const [parseError, setParseError] = useState("");
  const fileRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!visible) {
      setPayload("");
      setParseError("");
    }
  }, [visible]);

  if (!visible) return null;

  const submit = async () => {
    const parsed = parseQAImportText(payload);
    if (parsed.error) {
      setParseError(parsed.error);
      return;
    }
    setParseError("");
    await onImport({
      items: parsed.items,
      origin: "import",
    });
  };

  const onFile = (file: File | undefined) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      setPayload(String(reader.result ?? ""));
      setParseError("");
    };
    reader.readAsText(file);
  };

  return (
    <Dialog
      className="governance-dialog"
      visible
      header="导入 QA"
      destroyOnClose
      width={720}
      footer={false}
      onClose={onClose}
      {...({ role: "dialog", "aria-modal": "true", "aria-label": "导入 QA" } as Record<string, unknown>)}
    >
      <div className="governance-import-editor">
        <p>
          粘贴 CSV（question,answer[,alternatives[,negative_questions[,source_uri]]]，列表用 | 分隔）
          或 JSON 数组 / {"{items:[...]}" }。导入默认 origin=import，审核状态为待审核。
        </p>
        {error ? (
          <Alert theme="error" title={error.title} message={error.description} />
        ) : null}
        {parseError ? (
          <Alert theme="error" title="导入内容无效" message={parseError} />
        ) : null}
        <label className="governance-field governance-field--wide">
          <span>QA 导入内容</span>
          <Textarea
            value={payload}
            onChange={(value) => setPayload(String(value))}
            autosize={{ minRows: 8, maxRows: 16 }}
            aria-label="QA 导入内容"
            placeholder={"question,answer,alt1|alt2,neg1|neg2\nHow is access approved?,By the owner.,Who approves access?,How to bypass approval?"}
          />
        </label>
        <div className="governance-import-actions">
          <input
            ref={fileRef}
            type="file"
            accept=".csv,.json,text/csv,application/json"
            aria-label="选择 QA 导入文件"
            className="governance-file-input"
            onChange={(event) => onFile(event.target.files?.[0])}
          />
          <Button
            variant="outline"
            aria-label="选择导入文件"
            className="governance-control-min-h"
            onClick={() => fileRef.current?.click()}
          >
            <DownloadIcon /> 选择文件
          </Button>
          <Button
            theme="primary"
            loading={busy}
            disabled={busy || !payload.trim()}
            aria-label="确认导入 QA"
            className="governance-control-min-h"
            onClick={() => void submit()}
          >
            开始导入
          </Button>
        </div>
        {result ? (
          <div className="governance-import-result" role="status" aria-live="polite">
            <h4>导入批次结果</h4>
            <p>
              批次 <code>{result.batch_id}</code>：新建 {result.counts.created} · 跳过重复{" "}
              {result.counts.skipped_duplicate} · 失败 {result.counts.failed}
            </p>
            {result.failed.length > 0 ? (
              <Alert
                theme="warning"
                title="部分条目导入失败"
                message={
                  <ul className="governance-import-failed-list">
                    {result.failed.map((item) => (
                      <li key={`${item.index}-${item.question ?? ""}`}>
                        #{item.index + 1}
                        {item.question ? ` ${item.question}` : ""}：{item.reason}
                      </li>
                    ))}
                  </ul>
                }
              />
            ) : null}
            {result.skipped_duplicate.length > 0 ? (
              <p className="governance-import-skipped">
                跳过重复：
                {result.skipped_duplicate
                  .map((item) => `#${item.index + 1} (${item.reason})`)
                  .join("；")}
              </p>
            ) : null}
          </div>
        ) : null}
      </div>
    </Dialog>
  );
}
