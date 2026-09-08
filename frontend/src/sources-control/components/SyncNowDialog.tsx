import { useEffect, useRef, useState } from "react";
import { Modal, Switch } from "../../ui";
import type { SourceRecord, SourceSyncRequest } from "../model/sourceModels";

interface Props { open: boolean; source: SourceRecord | null; submitting: boolean; onClose: () => void; onSubmit: (payload: SourceSyncRequest) => Promise<boolean>; }
export default function SyncNowDialog({ open, source, submitting, onClose, onSubmit }: Props) {
  const [forceFull, setForceFull] = useState(false); const [dryRun, setDryRun] = useState(false); const opener = useRef<HTMLElement | null>(null);
  useEffect(() => { if (open) { opener.current = document.activeElement as HTMLElement | null; setForceFull(false); setDryRun(false); } }, [open, source?.id]);
  const close = () => { if (submitting) return; onClose(); queueMicrotask(() => opener.current?.focus()); };
  return <Modal open={open} title={`立即同步${source ? ` · ${source.name}` : ""}`} onCancel={close} onOk={() => void onSubmit({ force_full: forceFull, dry_run: dryRun }).then((ok) => { if (ok) close(); })} okText="提交同步" cancelText="取消" confirmLoading={submitting}>
    <div className="source-sync-form"><label><Switch checked={forceFull} onChange={setForceFull}/><span>强制全量同步</span></label><label><Switch checked={dryRun} onChange={setDryRun}/><span>仅演练，不写入</span></label><p>提交前会持久化与来源、G{source?.generation ?? 0} 代次及选项绑定的 Idempotency-Key。响应丢失时会使用同一密钥重放。</p><strong>HTTP 202 仅表示已排队，不表示同步完成。</strong></div>
  </Modal>;
}
