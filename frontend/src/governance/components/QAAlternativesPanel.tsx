import { Alert, Button, Drawer, Input, Popconfirm } from "tdesign-react";
import { DeleteIcon, PlusIcon } from "tdesign-icons-react";
import { useEffect, useState } from "react";
import type { GovernanceErrorView, QAAlternativeCreate, QAKnowledge } from "../model/governanceModel";

interface Props {
  qa: QAKnowledge | null;
  visible: boolean;
  busy: boolean;
  onClose: () => void;
  onAdd: (qaId: string, payload: QAAlternativeCreate) => Promise<boolean>;
  onDelete: (qaId: string, alternativeId: string, expectedRevision: number) => Promise<boolean>;
  error?: GovernanceErrorView | null;
}

export default function QAAlternativesPanel({ qa, visible, busy, onClose, onAdd, onDelete, error }: Props) {
  const [question, setQuestion] = useState("");
  useEffect(() => { if (visible) setQuestion(""); }, [visible, qa?.id]);
  if (!qa || !visible) return null;

  const add = async () => {
    const value = question.trim();
    if (!value) return;
    if (await onAdd(qa.id, { expected_revision: qa.revision, question: value })) setQuestion("");
  };

  return (
    <Drawer
      className="governance-drawer"
      visible
      header="替代表述"
      size="460px"
      footer={false}
      destroyOnClose
      onClose={onClose}
      {...({ role: "dialog", "aria-modal": "true", "aria-label": `管理 ${qa.question} 的替代表述` } as Record<string, unknown>)}
    >
      <div className="governance-alternative-editor">
        {error?.kind === "conflict" ? <Alert theme="warning" title={error.title} message={error.description} /> : null}
        <p>替代表述共享同一 QA 修订号，添加或删除后都会重新读取权威记录。</p>
        <label className="governance-field">
          <span>新增替代问题</span>
          <Input value={question} onChange={(value) => setQuestion(String(value))} onEnter={() => void add()} />
        </label>
        <Button theme="primary" loading={busy} disabled={busy || !question.trim()} aria-label="添加替代问题" onClick={() => void add()}>
          <PlusIcon /> 添加替代问题
        </Button>
        <div className="governance-alternative-list">
          {qa.alternatives.map((alternative) => (
            <div key={alternative.id}>
              <span>{alternative.question}</span>
              <Popconfirm
                theme="danger"
                content="删除会提升 QA 修订号，并需要重新读取记录。"
                confirmBtn={{ content: "确认删除", theme: "danger" }}
                cancelBtn={{ content: "取消" }}
                onConfirm={() => { if (!busy) void onDelete(qa.id, alternative.id, qa.revision); }}
              >
                <Button
                  shape="square"
                  variant="text"
                  theme="danger"
                  disabled={busy}
                  aria-label={`删除替代问题 ${alternative.question}`}
                >
                  <DeleteIcon />
                </Button>
              </Popconfirm>
            </div>
          ))}
          {!qa.alternatives.length ? <p className="governance-empty-inline">暂无替代表述</p> : null}
        </div>
      </div>
    </Drawer>
  );
}
