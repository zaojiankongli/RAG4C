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
import { AddIcon, DownloadIcon, EditIcon, ImportIcon, MoreIcon, RefreshIcon } from "tdesign-icons-react";
import { useMemo, useRef, useState } from "react";
import type { GovernanceLoadStatus } from "../hooks/useDatasetGovernance";
import {
  lifecyclePresentation,
  originPresentation,
  qaActionPolicy,
  reviewStatusPresentation,
  type DatasetRevisionRequest,
  type GovernanceErrorView,
  type QAAlternativeCreate,
  type QABatchResult,
  type QABatchReviewItem,
  type QABatchRevisionItem,
  type QACreate,
  type QAExportFormat,
  type QAImportRequest,
  type QAImportResult,
  type QAKnowledge,
  type QAListFilters,
  type QANegativeCreate,
  type QAReviewRequest,
  type QAUpdate,
} from "../model/governanceModel";
import QAAlternativesPanel from "./QAAlternativesPanel";
import QAEditorDialog from "./QAEditorDialog";
import QAImportDialog from "./QAImportDialog";

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
  addNegative?: (qaId: string, payload: QANegativeCreate) => Promise<boolean>;
  deleteNegative?: (qaId: string, negativeId: string, expectedRevision: number) => Promise<boolean>;
  importItems?: (payload: QAImportRequest) => Promise<QAImportResult | null>;
  batchReview?: (items: QABatchReviewItem[]) => Promise<QABatchResult | null>;
  batchExpire?: (items: QABatchRevisionItem[]) => Promise<QABatchResult | null>;
  batchRestore?: (items: QABatchRevisionItem[]) => Promise<QABatchResult | null>;
  exportQA?: (format: QAExportFormat) => Promise<string | null>;
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
  { label: "导入", value: "import" },
];

function downloadText(filename: string, content: string, mime: string) {
  if (typeof URL.createObjectURL !== "function") return;
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export default function QAGovernancePanel(props: QAGovernancePanelProps) {
  const [editingId, setEditingId] = useState<string | "create" | null>(null);
  const [alternativeId, setAlternativeId] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [importResult, setImportResult] = useState<QAImportResult | null>(null);
  const [batchAlert, setBatchAlert] = useState<QABatchResult | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const createButtonRef = useRef<HTMLButtonElement | null>(null);
  const importButtonRef = useRef<HTMLButtonElement | null>(null);
  const lastInvokerRef = useRef<HTMLElement | null>(null);
  const globalBusy = props.mutatingId !== null;
  const activeFilterKeys = useMemo(() => {
    const f = props.filters || {};
    return (Object.keys(f) as Array<keyof QAListFilters>).filter((key) => {
      const value = f[key];
      return value !== undefined && value !== null && String(value).trim() !== "";
    });
  }, [props.filters]);
  const hasActiveFilters = activeFilterKeys.length > 0;
  const clearFilters = () => {
    props.setFilters({});
  };
  const editingQA = editingId && editingId !== "create" ? props.items.find((item) => item.id === editingId) ?? null : null;
  const alternativeQA = alternativeId ? props.items.find((item) => item.id === alternativeId) ?? null : null;
  const selectedRows = useMemo(
    () => props.items.filter((item) => selectedIds.has(item.id)),
    [props.items, selectedIds],
  );
  const pageIds = props.pageItems.map((item) => item.id);
  const allPageSelected = pageIds.length > 0 && pageIds.every((id) => selectedIds.has(id));

  const filter = (key: keyof QAListFilters, value: unknown) => {
    const next = { ...props.filters };
    if (value) (next as Record<string, unknown>)[key] = value;
    else delete next[key];
    props.setFilters(next);
  };

  const toggleSelect = (id: string, checked: boolean) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  };

  const toggleSelectAllPage = (checked: boolean) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      for (const id of pageIds) {
        if (checked) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  };

  const applyBatchResult = (result: QABatchResult | null | undefined) => {
    if (!result) return;
    setBatchAlert(result.failed.length > 0 ? result : null);
    const succeeded = new Set(result.succeeded.map((item) => item.qa_id));
    setSelectedIds((prev) => {
      const next = new Set<string>();
      for (const id of prev) {
        if (!succeeded.has(id)) next.add(id);
      }
      return next;
    });
  };

  const eligible = (predicate: (item: QAKnowledge) => boolean) =>
    !props.readOnly && selectedRows.filter(predicate).length > 0;

  const runBatch = async (
    kind: "review" | "expire" | "restore",
    decision?: "approved" | "rejected",
  ) => {
    if (kind === "review") {
      const targets = selectedRows
        .filter((row) => qaActionPolicy[row.lifecycle_state].review)
        .map((row) => ({
          qa_id: row.id,
          expected_revision: row.revision,
          decision: (decision ?? "approved") as "approved" | "rejected",
        }));
      if (!targets.length || !props.batchReview) return;
      const result = await props.batchReview(targets);
      applyBatchResult(result);
      return;
    }
    const policyKey = kind === "expire" ? "expire" : "restore";
    const handler = kind === "expire" ? props.batchExpire : props.batchRestore;
    const targets = selectedRows
      .filter((row) => qaActionPolicy[row.lifecycle_state][policyKey])
      .map((row) => ({ qa_id: row.id, expected_revision: row.revision }));
    if (!targets.length || !handler) return;
    const result = await handler(targets);
    applyBatchResult(result);
  };

  const handleExport = async (format: QAExportFormat) => {
    if (!props.exportQA) return;
    const content = await props.exportQA(format);
    if (content == null) return;
    downloadText(
      `qa-export.${format}`,
      content,
      format === "csv" ? "text/csv;charset=utf-8" : "application/json;charset=utf-8",
    );
  };

  const openImport = () => {
    setImportResult(null);
    setImportOpen(true);
  };

  const openCreate = () => {
    lastInvokerRef.current = createButtonRef.current;
    setEditingId("create");
  };

  const columns = useMemo<PrimaryTableCol<QAKnowledge>[]>(() => [
    {
      colKey: "select",
      title: "选择",
      width: 64,
      cell: ({ row }) => (
        <input
          type="checkbox"
          aria-label={`选择 QA ${row.question}`}
          checked={selectedIds.has(row.id)}
          disabled={globalBusy || props.readOnly}
          className="governance-row-select"
          onChange={(event) => toggleSelect(row.id, event.target.checked)}
        />
      ),
    },
    {
      colKey: "question",
      title: "问题与答案",
      width: 320,
      cell: ({ row }) => (
        <div className="governance-qa-copy">
          <strong>{row.question}</strong>
          <p>{row.answer}</p>
          <small>
            Revision {row.revision}
            {(row.negative_questions?.length ?? 0) > 0
              ? ` · 反例问 ${row.negative_questions?.length}`
              : ""}
            {row.import_batch_id ? ` · 批次 ${row.import_batch_id}` : ""}
          </small>
        </div>
      ),
    },
    {
      colKey: "origin",
      title: "来源",
      width: 110,
      cell: ({ row }) => (
        <Tag variant="light-outline">{originPresentation[row.origin]?.label ?? row.origin}</Tag>
      ),
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
  // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [globalBusy, props.readOnly, props.review, props.restore, props.expire, selectedIds]);

  const closeEditor = () => {
    setEditingId(null);
    queueMicrotask(() => (lastInvokerRef.current ?? createButtonRef.current)?.focus());
  };

  const emptyItems = props.items.length === 0 && props.status !== "loading";

  return (
    <section aria-labelledby="qa-governance-heading" className="governance-section">
      {props.truncated ? <Alert theme="warning" title="结果可能被截断" message="后端最多返回 500 条 QA，请使用筛选缩小范围。" /> : null}
      {props.error ? <Alert theme={props.error.kind === "conflict" ? "warning" : "error"} title={props.error.title} message={props.error.description} operation={props.error.canRetry ? <Button size="small" variant="outline" onClick={() => void props.refresh()}><RefreshIcon /> 刷新 QA</Button> : undefined} /> : null}
      {batchAlert ? (
        <Alert
          theme="warning"
          title={`批量操作部分失败（成功 ${batchAlert.counts.succeeded} / 失败 ${batchAlert.counts.failed}）`}
          message={
            <ul className="governance-batch-failed-list" aria-label="批量失败列表">
              {batchAlert.failed.map((item) => (
                <li key={`${item.qa_id}-${item.index}`}>
                  {item.qa_id}：{item.reason}
                </li>
              ))}
            </ul>
          }
          operation={
            <Button size="small" variant="outline" aria-label="刷新批量结果" onClick={() => { setBatchAlert(null); void props.refresh(); }}>
              <RefreshIcon /> 刷新
            </Button>
          }
        />
      ) : null}
      <Card
        title={<span id="qa-governance-heading">QA 生命周期</span>}
        subtitle="审核状态决定内容质量，生命周期决定记录是否仍然有效"
        actions={
          <div className="governance-card-actions-inline">
            {!props.readOnly && props.importItems ? (
              <Button
                ref={importButtonRef}
                tag="button"
                variant="outline"
                aria-label="导入 QA"
                disabled={globalBusy}
                className="governance-control-min-h"
                onClick={(event) => {
                  lastInvokerRef.current = event.currentTarget;
                  openImport();
                }}
              >
                <ImportIcon /> 导入 QA
              </Button>
            ) : null}
            {props.exportQA ? (
              <Button
                tag="button"
                variant="outline"
                aria-label="导出 QA JSON"
                disabled={props.status === "loading"}
                className="governance-control-min-h"
                onClick={() => void handleExport("json")}
              >
                <DownloadIcon /> 导出 JSON
              </Button>
            ) : null}
            {props.exportQA ? (
              <Button
                tag="button"
                variant="outline"
                aria-label="导出 QA CSV"
                disabled={props.status === "loading"}
                className="governance-control-min-h"
                onClick={() => void handleExport("csv")}
              >
                <DownloadIcon /> 导出 CSV
              </Button>
            ) : null}
            {!props.readOnly ? (
              <Button ref={createButtonRef} tag="button" theme="primary" aria-label="新建 QA" disabled={globalBusy} className="governance-control-min-h" onClick={openCreate}><AddIcon /> 新建 QA</Button>
            ) : null}
          </div>
        }
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
          <Button variant="outline" loading={props.status === "loading"} aria-label="刷新 QA 列表" onClick={() => void props.refresh()}><RefreshIcon /> 刷新</Button>
        </div>

        {selectedRows.length > 0 && !props.readOnly ? (
          <div className="governance-batch-bar" role="toolbar" aria-label="QA 批量操作">
            <label className="governance-batch-select-all">
              <input
                type="checkbox"
                aria-label="全选本页 QA"
                checked={allPageSelected}
                disabled={globalBusy || pageIds.length === 0}
                onChange={(event) => toggleSelectAllPage(event.target.checked)}
              />
              <span aria-live="polite">已选 {selectedRows.length} 条</span>
            </label>
            <Button
              size="small"
              theme="primary"
              variant="outline"
              aria-label="批量通过 QA"
              className="governance-control-min-h"
              disabled={globalBusy || !eligible((row) => qaActionPolicy[row.lifecycle_state].review)}
              onClick={() => void runBatch("review", "approved")}
            >
              批量通过
            </Button>
            <Button
              size="small"
              theme="danger"
              variant="outline"
              aria-label="批量拒绝 QA"
              className="governance-control-min-h"
              disabled={globalBusy || !eligible((row) => qaActionPolicy[row.lifecycle_state].review)}
              onClick={() => void runBatch("review", "rejected")}
            >
              批量拒绝
            </Button>
            <Button
              size="small"
              variant="outline"
              aria-label="批量过期 QA"
              className="governance-control-min-h"
              disabled={globalBusy || !eligible((row) => qaActionPolicy[row.lifecycle_state].expire)}
              onClick={() => void runBatch("expire")}
            >
              批量过期
            </Button>
            <Button
              size="small"
              variant="outline"
              aria-label="批量恢复 QA"
              className="governance-control-min-h"
              disabled={globalBusy || !eligible((row) => qaActionPolicy[row.lifecycle_state].restore)}
              onClick={() => void runBatch("restore")}
            >
              批量恢复
            </Button>
            <Button
              size="small"
              variant="text"
              aria-label="清除 QA 选择"
              className="governance-control-min-h"
              disabled={globalBusy}
              onClick={() => setSelectedIds(new Set())}
            >
              清除选择
            </Button>
          </div>
        ) : !props.readOnly && pageIds.length > 0 ? (
          <div className="governance-batch-bar governance-batch-bar--idle" role="toolbar" aria-label="QA 批量操作">
            <label className="governance-batch-select-all">
              <input
                type="checkbox"
                aria-label="全选本页 QA"
                checked={allPageSelected}
                disabled={globalBusy}
                onChange={(event) => toggleSelectAllPage(event.target.checked)}
              />
              <span>选择行以启用批量操作</span>
            </label>
          </div>
        ) : null}

        {importResult && !importOpen ? (
          <Alert
            theme="success"
            title={`导入完成：新建 ${importResult.counts.created} · 跳过 ${importResult.counts.skipped_duplicate} · 失败 ${importResult.counts.failed}`}
            message={`批次 ${importResult.batch_id}`}
            operation={
              <Button size="small" variant="text" aria-label="关闭导入结果" onClick={() => setImportResult(null)}>
                关闭
              </Button>
            }
          />
        ) : null}

        <div className="governance-table-scroll" role="region" aria-label="QA 治理表格，可横向滚动" tabIndex={0}>
          <PrimaryTable<QAKnowledge>
            rowKey="id"
            data={props.pageItems}
            columns={columns}
            loading={props.status === "loading"}
            tableLayout="fixed"
            empty={
              emptyItems ? (
                <div className="governance-empty-cta">
                  <p className="governance-empty-inline">当前筛选下暂无 QA 记录</p>
                  <div className="governance-empty-actions">
                    {hasActiveFilters ? (
                      <Button
                        variant="outline"
                        aria-label="清除 QA 筛选"
                        className="governance-control-min-h"
                        onClick={clearFilters}
                      >
                        清除筛选
                      </Button>
                    ) : null}
                    {!props.readOnly && props.importItems ? (
                      <Button
                        theme="primary"
                        aria-label="导入 QA"
                        className="governance-control-min-h"
                        onClick={() => openImport()}
                      >
                        <ImportIcon /> 导入
                      </Button>
                    ) : null}
                    {!props.readOnly ? (
                      <Button
                        variant="outline"
                        aria-label="新建 QA"
                        className="governance-control-min-h"
                        onClick={openCreate}
                      >
                        <AddIcon /> 新建 QA
                      </Button>
                    ) : null}
                    {props.readOnly ? <span className="governance-empty-inline">暂无记录</span> : null}
                  </div>
                </div>
              ) : (
                <div className="governance-empty-inline">当前筛选下暂无 QA 记录</div>
              )
            }
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
      <QAImportDialog
        visible={importOpen}
        busy={props.mutatingId === "import"}
        error={props.error}
        result={importResult}
        onClose={() => {
          setImportOpen(false);
          queueMicrotask(() => (lastInvokerRef.current ?? importButtonRef.current)?.focus());
        }}
        onImport={async (payload) => {
          if (!props.importItems) return null;
          const result = await props.importItems(payload);
          setImportResult(result);
          return result;
        }}
      />
      <QAAlternativesPanel
        qa={alternativeQA}
        visible={alternativeQA !== null}
        busy={props.mutatingId === alternativeQA?.id}
        error={props.error}
        onClose={() => { setAlternativeId(null); queueMicrotask(() => lastInvokerRef.current?.focus()); }}
        onAdd={props.addAlternative}
        onDelete={props.deleteAlternative}
        onAddNegative={props.addNegative}
        onDeleteNegative={props.deleteNegative}
      />
    </section>
  );
}
