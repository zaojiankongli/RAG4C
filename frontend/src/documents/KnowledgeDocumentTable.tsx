import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Button, Checkbox, PrimaryTable, type PrimaryTableCol } from "tdesign-react";
import { ChevronDownIcon, ChevronRightIcon } from "tdesign-icons-react";
import type { DocumentItem } from "../types/rag";
import { isDocumentSelectable } from "./documentModel";

const PAGE_SIZE = 10;
export const MAX_DOCUMENT_SELECTION = 100;

export interface ServerDocumentPagination {
  current: number;
  total: number;
  pageSize: number;
  hasPrevious: boolean;
  hasNext: boolean;
  loading?: boolean;
  onPrevious: () => void;
  onNext: () => void;
}

export interface KnowledgeDocumentTableProps {
  documents: DocumentItem[];
  loading: boolean;
  selectionDisabled?: boolean;
  selectedIds: string[];
  onSelectionChange: (ids: string[]) => void;
  renderFile: (document: DocumentItem) => ReactNode;
  renderParserProfile: (document: DocumentItem) => ReactNode;
  renderStatus: (document: DocumentItem) => ReactNode;
  renderActions: (document: DocumentItem) => ReactNode;
  renderExpanded?: (document: DocumentItem) => ReactNode;
  canExpand?: (document: DocumentItem) => boolean;
  onOpenParse?: (document: DocumentItem) => void;
  canOpenParse?: (document: DocumentItem) => boolean;
  emptyContent: ReactNode;
  serverPagination?: ServerDocumentPagination;
}

export default function KnowledgeDocumentTable({
  documents,
  loading,
  selectionDisabled = false,
  selectedIds,
  onSelectionChange,
  renderFile,
  renderParserProfile,
  renderStatus,
  renderActions,
  renderExpanded,
  canExpand,
  onOpenParse,
  canOpenParse,
  emptyContent,
  serverPagination,
}: KnowledgeDocumentTableProps) {
  const [expandedIds, setExpandedIds] = useState<Array<string | number>>([]);
  const [current, setCurrent] = useState(1);
  const maxPage = Math.max(1, Math.ceil(documents.length / PAGE_SIZE));
  const safeCurrent = serverPagination?.current ?? Math.min(current, maxPage);
  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const pageDocuments = useMemo(
    () =>
      serverPagination
        ? documents
        : documents.slice((safeCurrent - 1) * PAGE_SIZE, safeCurrent * PAGE_SIZE),
    [documents, safeCurrent, serverPagination],
  );
  const selectablePageDocuments = pageDocuments.filter(
    (document) => !selectionDisabled && isDocumentSelectable(document),
  );
  const selectedOnPage = selectablePageDocuments.filter((document) => selectedSet.has(document.id));
  const selectionAtLimit = selectedIds.length >= MAX_DOCUMENT_SELECTION;

  useEffect(() => {
    if (serverPagination) return;
    if (current !== safeCurrent) setCurrent(safeCurrent);
  }, [current, safeCurrent, serverPagination]);

  const updateOne = (document: DocumentItem, checked: boolean) => {
    if (checked) {
      if (selectionAtLimit || selectedSet.has(document.id)) return;
      onSelectionChange([...selectedIds, document.id].slice(0, MAX_DOCUMENT_SELECTION));
      return;
    }
    onSelectionChange(selectedIds.filter((id) => id !== document.id));
  };

  const updateCurrentPage = (checked: boolean) => {
    const pageIds = new Set(selectablePageDocuments.map((document) => document.id));
    if (!checked) {
      onSelectionChange(selectedIds.filter((id) => !pageIds.has(id)));
      return;
    }
    const next = [...selectedIds];
    for (const document of selectablePageDocuments) {
      if (next.length >= MAX_DOCUMENT_SELECTION) break;
      if (!next.includes(document.id)) next.push(document.id);
    }
    onSelectionChange(next);
  };

  const columns: Array<PrimaryTableCol<DocumentItem>> = [
    {
      title: (
        <Checkbox
          aria-label="选择当前页可操作文档"
          checked={
            selectablePageDocuments.length > 0 &&
            selectedOnPage.length === selectablePageDocuments.length
          }
          indeterminate={
            selectedOnPage.length > 0 && selectedOnPage.length < selectablePageDocuments.length
          }
          disabled={
            !selectablePageDocuments.length || (selectionAtLimit && selectedOnPage.length === 0)
          }
          onChange={updateCurrentPage}
        />
      ),
      colKey: "selection",
      width: 44,
      cell: ({ row }) => {
        const selected = selectedSet.has(row.id);
        const disabled =
          selectionDisabled || !isDocumentSelectable(row) || (selectionAtLimit && !selected);
        return (
          <Checkbox
            aria-label={`选择 ${row.name}`}
            checked={selected}
            disabled={disabled}
            title={
              disabled && !selected ? "单次最多选择 100 篇，且处理中不可选择" : `选择 ${row.name}`
            }
            onChange={(checked) => updateOne(row, checked)}
          />
        );
      },
    },
    { title: "文件", colKey: "name", width: 250, cell: ({ row }) => renderFile(row) },
    {
      title: "解析画像",
      colKey: "parser",
      width: 190,
      cell: ({ row }) => renderParserProfile(row),
    },
    {
      title: "片段数",
      colKey: "chunk_count",
      width: 72,
      align: "right",
      cell: ({ row }) =>
        row.chunk_count > 0 ? (
          <span className="tabular-nums">{row.chunk_count.toLocaleString()}</span>
        ) : (
          <span className="documents-table-empty-value">—</span>
        ),
    },
    { title: "处理状态", colKey: "status", width: 170, cell: ({ row }) => renderStatus(row) },
    {
      title: "操作",
      colKey: "actions",
      width: 340,
      align: "right",
      fixed: "right",
      cell: ({ row }) => {
        const expandable = Boolean(renderExpanded && canExpand?.(row));
        const parseOpenable = canOpenParse?.(row) ?? false;
        const expanded = expandedIds.includes(row.id);
        const accessibility = expandable
          ? {
              "aria-expanded": expanded,
              "aria-label": `${expanded ? "收起" : "展开"} ${row.name} 详情`,
            }
          : { "aria-label": `${row.name} 暂不可展开` };
        return (
          <div className="doc-row-actions">
            {renderActions(row)}
            {onOpenParse ? (
              <Button
                tag="button"
                size="small"
                variant="outline"
                disabled={!parseOpenable}
                aria-label={`解析干预 ${row.name}`}
                onClick={() => { if (parseOpenable) onOpenParse(row); }}
              >
                解析干预
              </Button>
            ) : null}
            <Button
              tag="button"
              size="small"
              variant="text"
              icon={expanded ? <ChevronDownIcon /> : <ChevronRightIcon />}
              disabled={!expandable}
              {...accessibility}
              onClick={() => {
                if (!expandable) return;
                setExpandedIds((ids) =>
                  ids.includes(row.id) ? ids.filter((id) => id !== row.id) : [...ids, row.id],
                );
              }}
            >
              {expanded ? "收起详情" : "展开详情"}
            </Button>
          </div>
        );
      },
    },
  ];

  return (
    <div className="knowledge-document-table" data-testid="knowledge-document-table">
      {selectionAtLimit ? (
        <div className="document-selection-limit" role="status">
          单次最多选择 100 篇文档，请先完成当前批量操作
        </div>
      ) : null}
      <PrimaryTable<DocumentItem>
        rowKey="id"
        data={pageDocuments}
        columns={columns}
        loading={loading}
        expandedRowKeys={expandedIds}
        expandedRow={({ row }) => renderExpanded?.(row) ?? null}
        expandIcon={false}
        tableLayout="fixed"
        verticalAlign="middle"
        hover
        bordered={false}
        stripe={false}
        empty={emptyContent}
        disableDataPage
        pagination={
          serverPagination
            ? undefined
            : {
                current: safeCurrent,
                pageSize: PAGE_SIZE,
                total: documents.length,
                pageSizeOptions: [PAGE_SIZE],
                showJumper: false,
                showPageSize: false,
                onCurrentChange: setCurrent,
              }
        }
      />
      {serverPagination && (
        serverPagination.total > serverPagination.pageSize ||
        serverPagination.hasPrevious ||
        serverPagination.hasNext
      ) ? (
        <nav className="documents-table-pagination" aria-label="文档结果分页">
          <Button
            tag="button"
            size="small"
            variant="outline"
            disabled={!serverPagination.hasPrevious || serverPagination.loading}
            onClick={serverPagination.onPrevious}
          >
            上一页
          </Button>
          <span aria-live="polite">
            第 {serverPagination.current} 页 · 共 {serverPagination.total.toLocaleString()} 条
          </span>
          <Button
            tag="button"
            size="small"
            variant="outline"
            disabled={!serverPagination.hasNext || serverPagination.loading}
            onClick={serverPagination.onNext}
          >
            下一页
          </Button>
        </nav>
      ) : null}
    </div>
  );
}
