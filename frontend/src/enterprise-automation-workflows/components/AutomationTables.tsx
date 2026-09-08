import {
  Alert,
  Button,
  Loading,
  PrimaryTable,
  Tag,
  Timeline,
  type PrimaryTableCol,
} from "tdesign-react";
import { FileIcon, TimeIcon } from "tdesign-icons-react";

import type {
  AutomationActionRequest,
  AutomationEvent,
  AutomationRule,
  AutomationRuleRevision,
  AutomationRun,
} from "../model/automationModel";
import {
  actionLabel,
  AutomationStateNotice,
  formatAutomationDate,
  requestStatusLabel,
  ruleStatusLabel,
  runStatusLabel,
  safeSnapshotEntries,
  statusTheme,
  triggerLabel,
  conditionLabel,
} from "./automationUi";
import type { AutomationCollection } from "./types";

export interface RulesSurfaceProps {
  rules: AutomationCollection<AutomationRule>;
  revisions: AutomationCollection<AutomationRuleRevision>;
  mobile?: boolean;
  readOnly?: boolean;
  onOpenRule: (rule: AutomationRule, trigger: HTMLElement) => void;
}

function RuleIdentity({ rule }: { rule: AutomationRule }) {
  return (
    <div className="automation-workflows__identity-cell">
      <strong>{rule.name}</strong>
      <span>
        {rule.workspace_id ?? "workspace 未返回"} · {rule.dataset_id ?? "dataset 未返回"}
      </span>
      <code>{rule.id}</code>
    </div>
  );
}

function currentRevisionFor(
  rule: AutomationRule,
  revisions: AutomationRuleRevision[],
): AutomationRuleRevision | undefined {
  if (!rule.current_revision_id) return undefined;
  return revisions.find(
    (revision) => revision.rule_id === rule.id && revision.id === rule.current_revision_id,
  );
}

function RevisionSummary({
  rule,
  revisions,
}: {
  rule: AutomationRule;
  revisions: AutomationRuleRevision[];
}) {
  const current = currentRevisionFor(rule, revisions);
  return (
    <div className="automation-workflows__revision-cell">
      <strong>
        {current ? `current revision ${current.revision}` : "current revision 未返回"}
      </strong>
      <span>{current ? triggerLabel(current.trigger_code) : "定义未返回"}</span>
      {current && current.revision !== rule.revision ? (
        <span>head revision {rule.revision} · 待启用</span>
      ) : null}
      {current ? <code>{current.definition_digest.slice(0, 12)}…</code> : null}
    </div>
  );
}

function RuleAction({
  rule,
  readOnly,
  onOpenRule,
}: {
  rule: AutomationRule;
  readOnly: boolean;
  onOpenRule: (rule: AutomationRule, trigger: HTMLElement) => void;
}) {
  return (
    <Button
      variant="text"
      size="small"
      icon={<FileIcon />}
      aria-label={`查看规则 ${rule.name}`}
      aria-disabled={readOnly ? "true" : undefined}
      onClick={(event) => onOpenRule(rule, event.currentTarget)}
    >
      查看规则
    </Button>
  );
}

export function RulesSurface({
  rules,
  revisions,
  mobile = false,
  readOnly = false,
  onOpenRule,
}: RulesSurfaceProps) {
  const notice = (
    <AutomationStateNotice
      status={rules.status}
      invalidItemCount={rules.invalidItemCount}
      hasItems={rules.items.length > 0}
      resourceLabel="自动化规则"
      emptyTitle="暂无自动化规则"
      emptyDescription="创建第一条受限规则后，它会在这里留下 revision 与证据链。"
    />
  );
  if (rules.status === "loading" && rules.items.length === 0) {
    return <>{notice}</>;
  }
  if (
    !rules.items.length &&
    ["ready", "empty", "partial", "unavailable", "error"].includes(rules.status)
  ) {
    return <>{notice}</>;
  }
  if (mobile) {
    return (
      <>
        {notice}
        <section
          className="automation-workflows__mobile-cards"
          data-testid="automation-rules-mobile-cards"
          aria-label="自动化规则卡片列表"
        >
          {rules.items.map((item) => {
            const current = currentRevisionFor(item, revisions.items);
            return (
              <article className="automation-workflows__rule-card" key={item.id} tabIndex={0}>
                <header>
                  <RuleIdentity rule={item} />
                  <Tag theme={statusTheme(item.status)} variant="light-outline" size="small">
                    {ruleStatusLabel(item.status)}
                  </Tag>
                </header>
                <dl className="automation-workflows__card-facts">
                  <div>
                    <dt>触发器</dt>
                    <dd>{current ? triggerLabel(current.trigger_code) : "未返回"}</dd>
                  </div>
                  <div>
                    <dt>条件</dt>
                    <dd>{current ? conditionLabel(current.condition_code) : "未返回"}</dd>
                  </div>
                  <div>
                    <dt>优先级</dt>
                    <dd>{item.priority}</dd>
                  </div>
                  <div>
                    <dt>版本</dt>
                    <dd>
                      <code>current r{current?.revision ?? "未返回"}</code>
                      {current && current.revision !== item.revision ? (
                        <span>head r{item.revision} · 待启用</span>
                      ) : null}
                    </dd>
                  </div>
                </dl>
                <div className="automation-workflows__row-actions">
                  <RuleAction rule={item} readOnly={readOnly} onOpenRule={onOpenRule} />
                </div>
              </article>
            );
          })}
        </section>
      </>
    );
  }

  const columns: Array<PrimaryTableCol<AutomationRule>> = [
    {
      title: "规则",
      colKey: "rule",
      width: 290,
      cell: ({ row }) => <RuleIdentity rule={row} />,
    },
    {
      title: "状态",
      colKey: "status",
      width: 110,
      cell: ({ row }) => (
        <Tag theme={statusTheme(row.status)} variant="light-outline" size="small">
          {ruleStatusLabel(row.status)}
        </Tag>
      ),
    },
    {
      title: "当前版本",
      colKey: "revision",
      width: 190,
      cell: ({ row }) => <RevisionSummary rule={row} revisions={revisions.items} />,
    },
    {
      title: "优先级",
      colKey: "priority",
      width: 90,
      cell: ({ row }) => <code>{row.priority}</code>,
    },
    {
      title: "更新时间",
      colKey: "updated_at",
      width: 175,
      cell: ({ row }) => (
        <time dateTime={row.updated_at}>{formatAutomationDate(row.updated_at)}</time>
      ),
    },
    {
      title: "操作",
      colKey: "actions",
      width: 160,
      cell: ({ row }) => <RuleAction rule={row} readOnly={readOnly} onOpenRule={onOpenRule} />,
    },
  ];

  return (
    <>
      {notice}
      <div
        className="automation-workflows__desktop-table"
        data-testid="automation-rules-desktop-table"
        tabIndex={0}
        aria-label="自动化规则表格，可横向滚动"
      >
        <PrimaryTable
          rowKey="id"
          columns={columns}
          data={rules.items}
          size="small"
          bordered
          hover
          verticalAlign="top"
          empty="暂无自动化规则"
        />
      </div>
    </>
  );
}

export interface RunsSurfaceProps {
  runs: AutomationCollection<AutomationRun>;
  mobile?: boolean;
}

function RunFacts({ run }: { run: AutomationRun }) {
  return (
    <dl className="automation-workflows__card-facts">
      <div>
        <dt>规则</dt>
        <dd>
          <code>{run.rule_id}</code>
        </dd>
      </div>
      <div>
        <dt>条件</dt>
        <dd>{run.condition_matched ? "已命中" : "未命中"}</dd>
      </div>
      <div>
        <dt>请求</dt>
        <dd>
          {run.requested_count} / {run.action_count}
        </dd>
      </div>
      <div>
        <dt>开始</dt>
        <dd>{formatAutomationDate(run.started_at)}</dd>
      </div>
    </dl>
  );
}

export function RunsSurface({ runs, mobile = false }: RunsSurfaceProps) {
  const notice = (
    <AutomationStateNotice
      status={runs.status}
      invalidItemCount={runs.invalidItemCount}
      hasItems={runs.items.length > 0}
      resourceLabel="自动化运行记录"
      emptyTitle="暂无自动化运行"
      emptyDescription="规则被安全观察后，运行尝试会在这里留下状态和证据。"
    />
  );
  if (!runs.items.length) return <>{notice}</>;
  if (mobile) {
    return (
      <>
        {notice}
        <section className="automation-workflows__mobile-cards" aria-label="自动化运行卡片列表">
          {runs.items.map((run) => (
            <article className="automation-workflows__run-card" key={run.id} tabIndex={0}>
              <header>
                <div className="automation-workflows__identity-cell">
                  <strong>{run.id}</strong>
                  <span>
                    {run.rule_id} · revision {run.rule_revision_id}
                  </span>
                </div>
                <Tag theme={statusTheme(run.status)} variant="light-outline" size="small">
                  {runStatusLabel(run.status)}
                </Tag>
              </header>
              <RunFacts run={run} />
            </article>
          ))}
        </section>
      </>
    );
  }
  const columns: Array<PrimaryTableCol<AutomationRun>> = [
    {
      title: "运行",
      colKey: "run",
      width: 250,
      cell: ({ row }) => (
        <div className="automation-workflows__identity-cell">
          <strong>{row.id}</strong>
          <span>
            {row.rule_id} · revision {row.rule_revision_id}
          </span>
          <code>{row.trigger_event_id}</code>
        </div>
      ),
    },
    {
      title: "状态",
      colKey: "status",
      width: 160,
      cell: ({ row }) => (
        <Tag theme={statusTheme(row.status)} variant="light-outline" size="small">
          {runStatusLabel(row.status)}
        </Tag>
      ),
    },
    {
      title: "条件 / 请求",
      colKey: "result",
      width: 160,
      cell: ({ row }) => (
        <div className="automation-workflows__stack-cell">
          <strong>{row.condition_matched ? "已命中" : "未命中"}</strong>
          <span>
            {row.requested_count} / {row.action_count} requests
          </span>
        </div>
      ),
    },
    {
      title: "开始时间",
      colKey: "started_at",
      width: 180,
      cell: ({ row }) => (
        <time dateTime={row.started_at}>{formatAutomationDate(row.started_at)}</time>
      ),
    },
  ];
  return (
    <>
      {notice}
      <div
        className="automation-workflows__desktop-table"
        data-testid="automation-runs-desktop-table"
        tabIndex={0}
        aria-label="自动化运行表格，可横向滚动"
      >
        <PrimaryTable
          rowKey="id"
          columns={columns}
          data={runs.items}
          size="small"
          bordered
          hover
          verticalAlign="top"
        />
      </div>
    </>
  );
}

export interface RequestsSurfaceProps {
  requests: AutomationCollection<AutomationActionRequest>;
  mobile?: boolean;
}

function RequestIdentity({ request }: { request: AutomationActionRequest }) {
  return (
    <div className="automation-workflows__identity-cell">
      <strong>{actionLabel(request.action_code)}</strong>
      <span>
        {request.rule_id} · step {request.step_index + 1}
      </span>
      <code>{request.id}</code>
    </div>
  );
}

export function RequestsSurface({ requests, mobile = false }: RequestsSurfaceProps) {
  const notice = (
    <AutomationStateNotice
      status={requests.status}
      invalidItemCount={requests.invalidItemCount}
      hasItems={requests.items.length > 0}
      resourceLabel="动作请求"
      emptyTitle="暂无动作请求"
      emptyDescription="规则只会生成受限请求，不会在这个页面直接执行通知、审批或任务动作。"
    />
  );
  if (!requests.items.length) return <>{notice}</>;
  if (mobile) {
    return (
      <>
        {notice}
        <section className="automation-workflows__mobile-cards" aria-label="动作请求卡片列表">
          {requests.items.map((item) => (
            <article className="automation-workflows__request-card" key={item.id} tabIndex={0}>
              <header>
                <RequestIdentity request={item} />
                <Tag theme={statusTheme(item.status)} variant="light-outline" size="small">
                  {requestStatusLabel(item.status)}
                </Tag>
              </header>
              <p>{item.safe_reason}</p>
              <span className="automation-workflows__card-meta">
                expires {formatAutomationDate(item.expires_at)}
              </span>
            </article>
          ))}
        </section>
      </>
    );
  }
  const columns: Array<PrimaryTableCol<AutomationActionRequest>> = [
    {
      title: "动作请求",
      colKey: "request",
      width: 280,
      cell: ({ row }) => <RequestIdentity request={row} />,
    },
    {
      title: "状态",
      colKey: "status",
      width: 120,
      cell: ({ row }) => (
        <Tag theme={statusTheme(row.status)} variant="light-outline" size="small">
          {requestStatusLabel(row.status)}
        </Tag>
      ),
    },
    {
      title: "安全原因",
      colKey: "safe_reason",
      width: 300,
      cell: ({ row }) => <span>{row.safe_reason}</span>,
    },
    {
      title: "过期时间",
      colKey: "expires_at",
      width: 180,
      cell: ({ row }) => (
        <time dateTime={row.expires_at}>{formatAutomationDate(row.expires_at)}</time>
      ),
    },
  ];
  return (
    <>
      {notice}
      <div
        className="automation-workflows__desktop-table"
        data-testid="automation-requests-desktop-table"
        tabIndex={0}
        aria-label="动作请求表格，可横向滚动"
      >
        <PrimaryTable
          rowKey="id"
          columns={columns}
          data={requests.items}
          size="small"
          bordered
          hover
          verticalAlign="top"
        />
      </div>
    </>
  );
}

export interface ActivitySurfaceProps {
  activity: AutomationCollection<AutomationEvent>;
}

export function ActivitySurface({ activity }: ActivitySurfaceProps) {
  const notice = (
    <AutomationStateNotice
      status={activity.status}
      invalidItemCount={activity.invalidItemCount}
      hasItems={activity.items.length > 0}
      resourceLabel="自动化活动"
      emptyTitle="暂无自动化活动"
      emptyDescription="规则、运行与受限请求的追加证据会按顺序出现在这里。"
    />
  );
  if (!activity.items.length) return <>{notice}</>;
  return (
    <section
      className="automation-workflows__activity-panel"
      aria-labelledby="automation-activity-title"
    >
      {notice}
      <div className="automation-workflows__section-heading">
        <div>
          <span className="automation-workflows__eyebrow">IMMUTABLE EVENT LEDGER</span>
          <h2 id="automation-activity-title">不可变事件链</h2>
          <p>每条记录保留 sequence、前序摘要和安全快照；事件只能追加，不能编辑或删除。</p>
        </div>
        <span className="automation-workflows__activity-count">
          <TimeIcon aria-hidden="true" /> {activity.items.length} records
        </span>
      </div>
      <div data-testid="automation-event-timeline">
        <Timeline className="automation-workflows__timeline" mode="same" theme="dot">
          {activity.items.map((event, index) => (
            <Timeline.Item
              key={event.id}
              label={
                <time dateTime={event.occurred_at}>{formatAutomationDate(event.occurred_at)}</time>
              }
              dotColor={index === activity.items.length - 1 ? "primary" : "default"}
            >
              <div className="automation-workflows__timeline-item">
                <strong>
                  #{event.sequence} · {event.event_type}
                </strong>
                <span>
                  {event.actor_id} · request {event.request_id}
                </span>
                <code>{event.event_digest}</code>
                <dl className="automation-workflows__safe-snapshot">
                  {safeSnapshotEntries(event.safe_snapshot).map(([label, value]) => (
                    <div key={label}>
                      <dt>{label}</dt>
                      <dd>{value}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            </Timeline.Item>
          ))}
        </Timeline>
      </div>
      {activity.status === "partial" ? (
        <Alert
          theme="warning"
          title="部分事件已隐藏"
          message={`${activity.invalidItemCount} 条记录未通过安全校验。`}
        />
      ) : null}
    </section>
  );
}

export function LoadingSurface({ label }: { label: string }) {
  return (
    <div className="automation-workflows__state automation-workflows__state--loading">
      <Loading text={`正在读取${label}…`} />
    </div>
  );
}
