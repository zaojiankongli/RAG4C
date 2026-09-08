import { Button, Loading, PrimaryTable, Tag, type PrimaryTableCol } from "tdesign-react";
import {
  ArrowRightIcon,
  DeleteTimeIcon,
  FileIcon,
  SecuredIcon,
  TimeIcon,
} from "tdesign-icons-react";

import type { RecoveryEntry, RecoveryEntryStatus } from "./contentRecoveryTypes";
import {
  formatRecoveryDate,
  numberLabel,
  recoveryStatusLabel,
  recoveryStatusTheme,
} from "./recoveryUi";

export interface RecoveryEntryTableProps {
  entries: RecoveryEntry[];
  mobile?: boolean;
  loading?: boolean;
  readOnly?: boolean;
  onOpenDetail?: (entry: RecoveryEntry) => void;
  onRestore?: (entry: RecoveryEntry) => void;
  onAddHold?: (entry: RecoveryEntry) => void;
  onManageHolds?: (entry: RecoveryEntry) => void;
  onRequestPurge?: (entry: RecoveryEntry) => void;
}

function EntryActions({
  entry,
  readOnly,
  onOpenDetail,
  onRestore,
  onAddHold,
  onManageHolds,
  onRequestPurge,
}: Omit<RecoveryEntryTableProps, "entries" | "mobile" | "loading"> & { entry: RecoveryEntry }) {
  return (
    <div className="content-recovery__entry-actions">
      {onOpenDetail ? (
        <Button
          variant="text"
          size="small"
          icon={<FileIcon />}
          aria-label={`查看 ${entry.document_label} 详情`}
          onClick={() => onOpenDetail(entry)}
        >
          详情
        </Button>
      ) : null}
      {entry.status === "recycled" && onRestore ? (
        <Button
          variant="outline"
          size="small"
          icon={<ArrowRightIcon />}
          disabled={readOnly}
          aria-label={`恢复 ${entry.document_label}`}
          onClick={() => onRestore(entry)}
        >
          恢复
        </Button>
      ) : null}
      {entry.status === "recycled" && (entry.active_hold_count ?? 0) > 0 && onManageHolds ? (
        <Button
          variant="text"
          size="small"
          icon={<SecuredIcon />}
          aria-label={`管理 ${entry.document_label} 法律保留`}
          onClick={() => onManageHolds(entry)}
        >
          法律保留
        </Button>
      ) : null}
      {entry.status === "recycled" && (entry.active_hold_count ?? 0) === 0 && onAddHold ? (
        <Button
          variant="text"
          size="small"
          icon={<SecuredIcon />}
          disabled={readOnly}
          aria-label={`为 ${entry.document_label} 添加法律保留`}
          onClick={() => onAddHold(entry)}
        >
          保全
        </Button>
      ) : null}
      {entry.status === "recycled" && onRequestPurge ? (
        <Button
          theme="danger"
          variant="text"
          size="small"
          icon={<DeleteTimeIcon />}
          disabled={readOnly}
          aria-label={`申请清除 ${entry.document_label}`}
          onClick={() => onRequestPurge(entry)}
        >
          申请清除
        </Button>
      ) : null}
    </div>
  );
}

function statusDescription(entry: RecoveryEntry): string {
  if (entry.status === "recycled" && (entry.active_hold_count ?? 0) > 0) {
    return `${numberLabel(entry.active_hold_count)} 个有效法律保留`;
  }
  if (entry.status === "recycled") return "保留期内，检索已关闭";
  if (entry.status === "purge_requested") return "已交由审批中心处理";
  return "由权威生命周期记录";
}

function EntryStatus({ entry }: { entry: RecoveryEntry }) {
  return (
    <div className="content-recovery__status-cell">
      <Tag theme={recoveryStatusTheme(entry.status)} variant="light-outline" size="small">
        {recoveryStatusLabel(entry.status)}
      </Tag>
      <small>{statusDescription(entry)}</small>
    </div>
  );
}

function EntryIdentity({ entry }: { entry: RecoveryEntry }) {
  return (
    <div className="content-recovery__entry-identity">
      <strong>{entry.document_label}</strong>
      <span>{entry.dataset_label}</span>
      <code>{entry.document_id}</code>
    </div>
  );
}

function EntryFacts({ entry }: { entry: RecoveryEntry }) {
  return (
    <dl className="content-recovery__entry-facts">
      <div>
        <dt>状态</dt>
        <dd>
          <EntryStatus entry={entry} />
        </dd>
      </div>
      <div>
        <dt>回收时间</dt>
        <dd>
          <time dateTime={entry.recycled_at ?? undefined}>
            {formatRecoveryDate(entry.recycled_at)}
          </time>
        </dd>
      </div>
      <div>
        <dt>清除资格</dt>
        <dd>
          <time dateTime={entry.purge_eligible_at ?? undefined}>
            {formatRecoveryDate(entry.purge_eligible_at)}
          </time>
        </dd>
      </div>
      <div>
        <dt>版本栅栏</dt>
        <dd>
          <code>revision {entry.revision}</code>
        </dd>
      </div>
    </dl>
  );
}

const EVENT_STATUS: Record<RecoveryEntryStatus, string> = {
  recycled: "保留中",
  restoring: "恢复中",
  restored: "已恢复",
  purge_requested: "待清除审批",
  purged: "已清除",
  failed: "失败待处理",
};

export default function RecoveryEntryTable({
  entries,
  mobile = false,
  loading = false,
  readOnly = false,
  onOpenDetail,
  onRestore,
  onAddHold,
  onManageHolds,
  onRequestPurge,
}: RecoveryEntryTableProps) {
  if (loading && entries.length === 0) {
    return (
      <div className="content-recovery__entry-loading" data-testid="recovery-table-loading">
        <Loading text="正在读取回收条目…" />
      </div>
    );
  }

  if (mobile) {
    return (
      <section
        className="content-recovery__mobile-cards"
        data-testid="recovery-mobile-cards"
        aria-label="回收条目卡片列表"
      >
        {entries.map((entry) => (
          <article className="content-recovery__entry-card" key={entry.id} tabIndex={0}>
            <header>
              <EntryIdentity entry={entry} />
              <Tag theme={recoveryStatusTheme(entry.status)} variant="light-outline" size="small">
                {EVENT_STATUS[entry.status]}
              </Tag>
            </header>
            <EntryFacts entry={entry} />
            <EntryActions
              entry={entry}
              readOnly={readOnly}
              onOpenDetail={onOpenDetail}
              onRestore={onRestore}
              onAddHold={onAddHold}
              onManageHolds={onManageHolds}
              onRequestPurge={onRequestPurge}
            />
          </article>
        ))}
      </section>
    );
  }

  const columns: Array<PrimaryTableCol<RecoveryEntry>> = [
    {
      title: "文档与知识库",
      colKey: "document",
      width: 270,
      cell: ({ row }) => <EntryIdentity entry={row} />,
    },
    {
      title: "生命周期",
      colKey: "status",
      width: 160,
      cell: ({ row }) => <EntryStatus entry={row} />,
    },
    {
      title: "回收时间",
      colKey: "recycled_at",
      width: 160,
      cell: ({ row }) => (
        <time dateTime={row.recycled_at ?? undefined}>{formatRecoveryDate(row.recycled_at)}</time>
      ),
    },
    {
      title: "清除资格",
      colKey: "purge_eligible_at",
      width: 160,
      cell: ({ row }) => (
        <span className="content-recovery__eligible-cell">
          <TimeIcon aria-hidden="true" />
          <time dateTime={row.purge_eligible_at ?? undefined}>
            {formatRecoveryDate(row.purge_eligible_at)}
          </time>
        </span>
      ),
    },
    {
      title: "法律保留",
      colKey: "holds",
      width: 110,
      cell: ({ row }) => (
        <Tag
          theme={(row.active_hold_count ?? 0) > 0 ? "success" : "default"}
          variant="light-outline"
          size="small"
        >
          {(row.active_hold_count ?? 0) > 0 ? `${row.active_hold_count} 个有效` : "无有效保留"}
        </Tag>
      ),
    },
    {
      title: "操作",
      colKey: "actions",
      width: 320,
      cell: ({ row }) => (
        <EntryActions
          entry={row}
          readOnly={readOnly}
          onOpenDetail={onOpenDetail}
          onRestore={onRestore}
          onAddHold={onAddHold}
          onManageHolds={onManageHolds}
          onRequestPurge={onRequestPurge}
        />
      ),
    },
  ];

  return (
    <div
      className="content-recovery__desktop-table"
      data-testid="recovery-desktop-table"
      tabIndex={0}
      aria-label="回收条目表格，可横向滚动"
    >
      <PrimaryTable
        rowKey="id"
        columns={columns}
        data={entries}
        size="small"
        bordered
        hover
        verticalAlign="top"
        empty="暂无回收条目"
      />
    </div>
  );
}
