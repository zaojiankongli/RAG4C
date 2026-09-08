import { Button, Loading, PrimaryTable, type PrimaryTableCol } from "tdesign-react";
import { FileSearchIcon, LinkIcon } from "tdesign-icons-react";
import type { ServingSnapshot } from "../model/servingModel";
import { dateLabel, numberLabel, shortDigest, StateTag } from "./servingUi";

export interface ServingTableProps {
  snapshots: ServingSnapshot[];
  mobile?: boolean;
  loading?: boolean;
  onOpenDetail?: (snapshot: ServingSnapshot, trigger: HTMLElement) => void;
}
function SnapshotIdentity({ snapshot }: { snapshot: ServingSnapshot }) {
  return (
    <div className="knowledge-serving__snapshot-identity">
      <strong>Snapshot {snapshot.id}</strong>
      <span>Observation {snapshot.observation_key}</span>
      <code>{shortDigest(snapshot.snapshot_digest)}</code>
    </div>
  );
}
function SnapshotFacts({ snapshot }: { snapshot: ServingSnapshot }) {
  return (
    <dl className="knowledge-serving__snapshot-facts">
      <div>
        <dt>状态</dt>
        <dd>
          <StateTag state={snapshot.state} />
        </dd>
      </div>
      <div>
        <dt>阶段</dt>
        <dd>
          {numberLabel(snapshot.ready_stage_count)} / {numberLabel(snapshot.stage_count)} ready
        </dd>
      </div>
      <div>
        <dt>文档</dt>
        <dd>{numberLabel(snapshot.active_document_count)}</dd>
      </div>
      <div>
        <dt>索引待处理</dt>
        <dd>{numberLabel(snapshot.pending_index_count)}</dd>
      </div>
      <div>
        <dt>Serving generation</dt>
        <dd>
          <code>G{snapshot.observed_serving_generation}</code>
        </dd>
      </div>
      <div>
        <dt>创建时间</dt>
        <dd>
          <time dateTime={snapshot.created_at}>{dateLabel(snapshot.created_at)}</time>
        </dd>
      </div>
    </dl>
  );
}
function SnapshotActions({
  snapshot,
  onOpenDetail,
}: {
  snapshot: ServingSnapshot;
  onOpenDetail?: (snapshot: ServingSnapshot, trigger: HTMLElement) => void;
}) {
  return (
    <div className="knowledge-serving__row-actions">
      <Button
        variant="text"
        size="small"
        icon={<FileSearchIcon />}
        aria-label={"查看服务快照 " + snapshot.id + " 详情"}
        onClick={(event) => onOpenDetail?.(snapshot, event.currentTarget)}
      >
        详情
      </Button>
      <Button
        variant="text"
        size="small"
        icon={<LinkIcon />}
        aria-label={"查看服务快照 " + snapshot.id + " 证据"}
        onClick={(event) => onOpenDetail?.(snapshot, event.currentTarget)}
      >
        证据
      </Button>
    </div>
  );
}
export default function ServingTable({
  snapshots,
  mobile = false,
  loading = false,
  onOpenDetail,
}: ServingTableProps) {
  if (loading && snapshots.length === 0)
    return (
      <div className="knowledge-serving__table-loading">
        <Loading text="正在读取服务快照…" />
      </div>
    );
  if (mobile)
    return (
      <section
        className="knowledge-serving__mobile-cards"
        data-testid="serving-mobile-cards"
        aria-label="服务快照卡片列表"
      >
        {snapshots.map((snapshot) => (
          <article className="knowledge-serving__snapshot-card" key={snapshot.id} tabIndex={0}>
            <header>
              <SnapshotIdentity snapshot={snapshot} />
              <StateTag state={snapshot.state} />
            </header>
            <SnapshotFacts snapshot={snapshot} />
            <SnapshotActions snapshot={snapshot} onOpenDetail={onOpenDetail} />
          </article>
        ))}
      </section>
    );
  const columns: Array<PrimaryTableCol<ServingSnapshot>> = [
    {
      title: "快照与观察",
      colKey: "identity",
      width: 280,
      cell: ({ row }) => <SnapshotIdentity snapshot={row} />,
    },
    {
      title: "服务状态",
      colKey: "state",
      width: 140,
      cell: ({ row }) => (
        <div className="knowledge-serving__status-cell">
          <StateTag state={row.state} />
          <small>
            {row.ready_stage_count} / {row.stage_count} stages ready
          </small>
        </div>
      ),
    },
    {
      title: "数据规模",
      colKey: "scale",
      width: 160,
      cell: ({ row }) => (
        <div className="knowledge-serving__scale-cell">
          <strong>{numberLabel(row.active_document_count)} docs</strong>
          <span>
            {numberLabel(row.source_count)} sources · {numberLabel(row.pending_index_count)} pending
          </span>
        </div>
      ),
    },
    {
      title: "Generation / Revision",
      colKey: "generation",
      width: 165,
      cell: ({ row }) => (
        <div className="knowledge-serving__scale-cell">
          <strong>G{row.observed_serving_generation}</strong>
          <span>Policy R{row.policy_revision_id.slice(-4)}</span>
        </div>
      ),
    },
    {
      title: "观测时间",
      colKey: "timing",
      width: 170,
      cell: ({ row }) => <time dateTime={row.as_of}>{dateLabel(row.as_of)}</time>,
    },
    {
      title: "操作",
      colKey: "actions",
      width: 170,
      cell: ({ row }) => <SnapshotActions snapshot={row} onOpenDetail={onOpenDetail} />,
    },
  ];
  return (
    <div
      className="knowledge-serving__desktop-table"
      data-testid="serving-desktop-table"
      tabIndex={0}
      aria-label="服务快照密集表格"
    >
      <PrimaryTable
        rowKey="id"
        columns={columns}
        data={snapshots}
        size="small"
        bordered
        hover
        verticalAlign="top"
        empty="暂无服务快照"
      />
    </div>
  );
}
