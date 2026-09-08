import {
  Alert,
  Button,
  Card,
  Pagination,
  Popconfirm,
  PrimaryTable,
  Select,
  Tag,
  type PrimaryTableCol,
} from "tdesign-react";
import { AddIcon, EditIcon, MoreIcon, RefreshIcon } from "tdesign-icons-react";
import { useMemo, useRef, useState } from "react";
import type { GovernanceLoadStatus } from "../hooks/useDatasetGovernance";
import {
  lifecyclePresentation,
  qaActionPolicy,
  reviewStatusPresentation,
  type DatasetRevisionRequest,
  type GovernanceErrorView,
  type QAAlternativeCreate,
  type QACreate,
  type QAKnowledge,
  type QAListFilters,
  type QAReviewRequest,
  type QAUpdate,
} from "../model/governanceModel";
import QAAlternativesPanel from "./QAAlternativesPanel";
import QAEditorDialog from "./QAEditorDialog";

export interface QAGovernancePanelProps {
  status: GovernanceLoadStatus;
  items: QAKnowledge[];
  pageItems: QAKnowledge[];
  page: number;
  pageCount: number;
  pageSize: number;
  filters: QAListFilters;
  error: GovernanceErrorView | null;
  mutatingId: string | null;
  setPage: (page: number) => void;
  setFilters: (filters: QAListFilters) => void;
  refresh: () => Promise<boolean>;
  create: (payload: QACreate) => Promise<boolean>;
  update: (qaId: string, payload: QAUpdate) => Promise<boolean>;
  review: (qaId: string, payload: QAReviewRequest) => Promise<boolean>;
  expire: (qaId: string, payload: DatasetRevisionRequest) => Promise<boolean>;
  restore: (qaId: string, payload: DatasetRevisionRequest) => Promise<boolean>;
  addAlternative: (qaId: string, payload: QAAlternativeCreate) => Promise<boolean>;
  deleteAlternative: (qaId: string, alternativeId: string, expectedRevision: number) => Promise<boolean>;
  truncated?: boolean;
  readOnly?: boolean;
}

const reviewOptions = [
  { label: "全部审核状态", value: "" },
  { label: "待审核", value: "pending" },
  { label: "已通过", value: "approved" },
  { label: "已拒绝", value: "rejected" },
];
const lifecycleOptions = [
  { label: "全部生命周期", value: "" },
  { label: "有效", value: "active" },
  { label: "已过期", value: "expired" },
  { label: "待删除", value: "delete_requested" },
  { label: "删除中", value: "deleting" },
  { label: "删除失败", value: "delete_failed" },
  { label: "已删除", value: "deleted" },
];
const originOptions = [
  { label: "全部来源", value: "" },
  { label: "人工维护", value: "manual" },
  { label: "自动生成", value: "automatic" },
];

export default function QAGovernancePanel(props: QAGovernancePanelProps) {
  const [editingId, setEditingId] = useState<string | "create" | null>(null);
  const [alternativeId, setAlternativeId] = useState<string | null>(null);
  const createButtonRef = useRef<HTMLButtonElement | null>(null);
  const lastInvokerRef = useRef<HTMLElement | null>(null);
  const globalBusy = props.mutatingId !== null;
  const editingQA = editingId && editingId !== "create" ? props.items.find((item) => item.id === editingId) ?? null : null;
  const alternativeQA = alternativeId ? props.items.find((item) => item.id === alternativeId) ?? null : null;

  const filter = (key: keyof QAListFilters, value: unknown) => {
    const next = { ...props.filters };
    if (value) (next as Record<string, unknown>)[key] = value;
    else delete next[key];
    props.setFilters(next);
  };

  const columns = useMemo<PrimaryTableCol<QAKnowledge>[]>(() => [
    {
      colKey: "question",
      title: "问题与答案",
      width: 360,
      cell: ({ row }) => <div className="governance-qa-copy"><strong>{row.question}</strong><p>{row.answer}</p><small>Revision {row.revision}</small></div>,
    },
    {
      colKey: "origin",
      title: "来源",
      width: 110,
      cell: ({ row }) => <Tag variant="light-outline">{row.origin === "manual" ? "人工维护" : "自动生成"}</Tag>,
    },
    {
      colKey: "review_status",
      title: "审核",
      width: 110,
      cell: ({ row }) => {
        const item = reviewStatusPresentation[row.review_status];
        return <Tag theme={item.theme as "success" | "warning" | "danger"}>{item.label}</Tag>;
      },
    },
    {
      colKey: "lifecycle_state",
      title: "生命周期",
      width: 120,
      cell: ({ row }) => {
        const item = lifecyclePresentation[row.lifecycle_state];
        return <Tag theme={item.theme as "success" | "warning" | "danger" | "primary" | "default"}>{item.label}</Tag>;
      },
    },
    {
      colKey: "operation",
      title: "操作",
      width: 330,
      fixed: "right",
      cell: ({ row }) => {
        const policy = qaActionPolicy[row.lifecycle_state];
        if (props.readOnly || !Object.values(policy).some(Boolean)) return null;
        const disabled = globalBusy;
        return (
          <div className="governance-row-actions">
            {policy.edit ? <Button tag="button" size="small" variant="text" aria-label="编辑 QA" disabled={disabled} onClick={(event) => { lastInvokerRef.current=event.currentTarget; setEditingId(row.id); }}><EditIcon /> 编辑</Button> : null}
            {policy.review ? <><Button tag="button" size="small" variant="text" aria-label="通过 QA" disabled={disabled || row.review_status === "approved"} onClick={() => void props.review(row.id,{expected_revision:row.revision,decision:"approved"})}>通过</Button><Button tag="button" size="small" variant="text" theme="danger" aria-label="拒绝 QA" disabled={disabled || row.review_status === "rejected"} onClick={() => void props.review(row.id,{expected_revision:row.revision,decision:"rejected"})}>拒绝</Button></> : null}
            {policy.restore ? <Popconfirm theme="warning" content="恢复后仍需满足审核状态才可参与检索。" confirmBtn={{content:"确认恢复",theme:"primary"}} cancelBtn={{content:"取消"}} onConfirm={() => void props.restore(row.id,{expected_revision:row.revision})}><Button tag="button" size="small" variant="text" aria-label="恢复 QA" disabled={disabled}>恢复</Button></Popconfirm> : null}
            {policy.expire ? <Popconfirm theme="warning" content="过期会立即关闭检索，但保留 QA 记录。" confirmBtn={{content:"确认过期",theme:"primary"}} cancelBtn={{content:"取消"}} onConfirm={() => void props.expire(row.id,{expected_revision:row.revision})}><Button tag="button" size="small" variant="text" aria-label="过期 QA" disabled={disabled}>过期</Button></Popconfirm> : null}
            {policy.alternatives ? <Button tag="button" size="small" variant="text" aria-label="管理替代表述" disabled={disabled} onClick={(event) => { lastInvokerRef.current=event.currentTarget; setAlternativeId(row.id); }}><MoreIcon /> 替代表述</Button> : null}
          </div>
        );
      },
    },
  ], [globalBusy, props]);

  const closeEditor = () => {
    setEditingId(null);
    queueMicrotask(() => (lastInvokerRef.current ?? createButtonRef.current)?.focus());
  };

  return (
    <section aria-labelledby="qa-governance-heading" className="governance-section">
      {props.truncated ? <Alert theme="warning" title="结果可能被截断" message="后端最多返回 500 条 QA，请使用筛选缩小范围。" /> : null}
      {props.error ? <Alert theme={props.error.kind === "conflict" ? "warning" : "error"} title={props.error.title} message={props.error.description} operation={props.error.canRetry ? <Button size="small" variant="outline" onClick={() => void props.refresh()}><RefreshIcon /> 刷新 QA</Button> : undefined} /> : null}
      <Card
        title={<span id="qa-governance-heading">QA 生命周期</span>}
        subtitle="审核状态决定内容质量，生命周期决定记录是否仍然有效"
        actions={!props.readOnly ? <Button ref={createButtonRef} tag="button" theme="primary" aria-label="新建 QA" disabled={globalBusy} onClick={(event) => { lastInvokerRef.current=event.currentTarget; setEditingId("create"); }}><AddIcon /> 新建 QA</Button> : undefined}
      >
        <div className="governance-filter-bar" role="group" aria-label="QA 筛选">
          <label className="governance-filter-field">
            <span className="governance-sr-only">审核状态筛选</span>
            <Select value={props.filters.review_status ?? ""} options={reviewOptions} onChange={(value) => filter("review_status", value)} />
          </label>
          <label className="governance-filter-field">
            <span className="governance-sr-only">生命周期筛选</span>
            <Select value={props.filters.lifecycle_state ?? ""} options={lifecycleOptions} onChange={(value) => filter("lifecycle_state", value)} />
          </label>
          <label className="governance-filter-field">
            <span className="governance-sr-only">来源筛选</span>
            <Select value={props.filters.origin ?? ""} options={originOptions} onChange={(value) => filter("origin", value)} />
          </label>
          <Button variant="outline" loading={props.status === "loading"} onClick={() => void props.refresh()}><RefreshIcon /> 刷新</Button>
        </div>
        <div className="governance-table-scroll" role="region" aria-label="QA 治理表格，可横向滚动" tabIndex={0}>
          <PrimaryTable<QAKnowledge>
            rowKey="id"
            data={props.pageItems}
            columns={columns}
            loading={props.status === "loading"}
            tableLayout="fixed"
            empty={<div className="governance-empty-inline">当前筛选下暂无 QA 记录</div>}
          />
        </div>
        <div className="governance-pagination">
          <span>每页 {props.pageSize} 条</span>
          <Pagination
            current={props.page}
            pageSize={props.pageSize}
            total={props.items.length}
            showPageSize={false}
            showJumper={false}
            onCurrentChange={props.setPage}
          />
        </div>
      </Card>
      <QAEditorDialog
        visible={editingId !== null}
        qa={editingQA}
        saving={props.mutatingId === (editingQA?.id ?? "create")}
        error={props.error}
        onClose={closeEditor}
        onCreate={props.create}
        onUpdate={props.update}
      />
      <QAAlternativesPanel
        qa={alternativeQA}
        visible={alternativeQA !== null}
        busy={props.mutatingId === alternativeQA?.id}
        error={props.error}
        onClose={() => { setAlternativeId(null); queueMicrotask(() => lastInvokerRef.current?.focus()); }}
        onAdd={props.addAlternative}
        onDelete={props.deleteAlternative}
      />
    </section>
  );
}
