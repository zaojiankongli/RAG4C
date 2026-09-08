import { Alert, Button, Drawer, Tag, Timeline } from "tdesign-react";
import {
  ArrowRightIcon,
  CheckCircleIcon,
  CloseCircleIcon,
  EditIcon,
  PauseIcon,
  PlayCircleIcon,
  SecuredIcon,
} from "tdesign-icons-react";

import type {
  AutomationEvent,
  AutomationRule,
  AutomationRuleRevision,
} from "../model/automationModel";
import {
  actionLabel,
  AutomationStateNotice,
  conditionLabel,
  formatAutomationDate,
  ruleStatusLabel,
  safeSnapshotEntries,
  statusTheme,
  triggerLabel,
  useAutomationEscape,
} from "./automationUi";
import type { AutomationResourceStatus, AutomationRuleDetailValue } from "./types";

export interface RuleDetailDrawerProps {
  visible: boolean;
  detail: AutomationRuleDetailValue | null;
  status: AutomationResourceStatus;
  readOnly?: boolean;
  mobile?: boolean;
  onClose: () => void;
  onCreateRevision?: (rule: AutomationRule) => void;
  onActivate?: (rule: AutomationRule) => void;
  onPause?: (rule: AutomationRule) => void;
  onHandoff?: (rule: AutomationRule, revision: AutomationRuleRevision) => void;
}

function RevisionTimeline({ revisions }: { revisions: AutomationRuleRevision[] }) {
  if (!revisions.length) {
    return <p className="automation-workflows__muted">暂无 revision 记录。</p>;
  }
  return (
    <div data-testid="automation-revision-timeline">
      <Timeline className="automation-workflows__revision-timeline" mode="same" theme="dot">
        {revisions.map((revision, index) => (
          <Timeline.Item
            key={revision.id}
            label={
              <time dateTime={revision.created_at}>
                {formatAutomationDate(revision.created_at)}
              </time>
            }
            dotColor={index === revisions.length - 1 ? "primary" : "default"}
          >
            <div className="automation-workflows__revision-item">
              <strong>revision {revision.revision}</strong>
              <span>
                {triggerLabel(revision.trigger_code)} → {conditionLabel(revision.condition_code)}
              </span>
              <code>{revision.definition_digest}</code>
              <Tag theme="success" variant="light-outline" size="small">
                <SecuredIcon aria-hidden="true" /> 不可变
              </Tag>
            </div>
          </Timeline.Item>
        ))}
      </Timeline>
      <p className="automation-workflows__immutable-note">rule_created / revision_created 仅追加</p>
    </div>
  );
}

function EventTimeline({ events }: { events: AutomationEvent[] }) {
  if (!events.length) {
    return <p className="automation-workflows__muted">暂无自动化事件。</p>;
  }
  return (
    <ol className="automation-workflows__detail-events" aria-label="规则事件证据链">
      {events.map((event) => (
        <li key={event.id}>
          <div className="automation-workflows__timeline-marker" aria-hidden="true" />
          <div className="automation-workflows__timeline-item">
            <strong>
              #{event.sequence} · {event.event_type}
            </strong>
            <span>
              {formatAutomationDate(event.occurred_at)} · {event.actor_id}
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
        </li>
      ))}
    </ol>
  );
}

function RuleFacts({ detail }: { detail: AutomationRuleDetailValue }) {
  const revision = detail.current_revision;
  return (
    <dl className="automation-workflows__detail-facts">
      <div>
        <dt>规则 ID</dt>
        <dd>
          <code>{detail.rule.id}</code>
        </dd>
      </div>
      <div>
        <dt>状态</dt>
        <dd>
          <Tag theme={statusTheme(detail.rule.status)} variant="light-outline" size="small">
            {ruleStatusLabel(detail.rule.status)}
          </Tag>
        </dd>
      </div>
      <div>
        <dt>revision</dt>
        <dd>
          <code>{detail.rule.revision}</code>
        </dd>
      </div>
      <div>
        <dt>当前触发器</dt>
        <dd>{revision ? triggerLabel(revision.trigger_code) : "未返回"}</dd>
      </div>
      <div>
        <dt>当前条件</dt>
        <dd>{revision ? conditionLabel(revision.condition_code) : "未返回"}</dd>
      </div>
      <div>
        <dt>更新时间</dt>
        <dd>
          <time dateTime={detail.rule.updated_at}>
            {formatAutomationDate(detail.rule.updated_at)}
          </time>
        </dd>
      </div>
    </dl>
  );
}

export default function RuleDetailDrawer({
  visible,
  detail,
  status,
  readOnly = false,
  mobile = false,
  onClose,
  onCreateRevision,
  onActivate,
  onPause,
  onHandoff,
}: RuleDetailDrawerProps) {
  useAutomationEscape(onClose, visible);
  const title = detail ? `规则详情 ${detail.rule.id}` : "规则详情";
  return (
    <Drawer
      visible={visible}
      header={title}
      size={mobile ? "100%" : "680px"}
      placement={mobile ? "bottom" : "right"}
      destroyOnClose
      closeOnEscKeydown
      className="automation-workflows__detail-drawer"
      onClose={onClose}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="automation-workflows__detail-content"
      >
        {status === "loading" && !detail ? (
          <AutomationStateNotice status="loading" resourceLabel="规则详情" />
        ) : null}
        {status === "unavailable" ? (
          <AutomationStateNotice status="unavailable" resourceLabel="规则详情" />
        ) : null}
        {status === "error" ? (
          <AutomationStateNotice status="error" resourceLabel="规则详情" />
        ) : null}
        {status === "partial" ? (
          <Alert
            theme="warning"
            title="规则详情部分可用"
            message="未返回的 revision 或事件不会被推断。"
          />
        ) : null}
        {detail ? (
          <>
            <div className="automation-workflows__detail-toolbar">
              <div>
                <span className="automation-workflows__eyebrow">RULE AUTHORITY</span>
                <h2>{detail.rule.name}</h2>
                <p>immutable revision / safe request boundary</p>
              </div>
              <div className="automation-workflows__detail-toolbar-actions">
                {detail.current_revision && onHandoff ? (
                  <Button
                    variant="outline"
                    size="small"
                    icon={<ArrowRightIcon />}
                    aria-label="查看触发来源"
                    onClick={() => onHandoff(detail.rule, detail.current_revision!)}
                  >
                    查看来源
                  </Button>
                ) : null}
                <Button
                  variant="outline"
                  size="small"
                  icon={<EditIcon />}
                  aria-disabled={readOnly}
                  disabled={readOnly}
                  onClick={() => {
                    if (!readOnly) onCreateRevision?.(detail.rule);
                  }}
                >
                  创建下一版
                </Button>
                {detail.rule.status === "active" ? (
                  <Button
                    theme="warning"
                    variant="text"
                    size="small"
                    icon={<PauseIcon />}
                    aria-disabled={readOnly}
                    disabled={readOnly}
                    onClick={() => {
                      if (!readOnly) onPause?.(detail.rule);
                    }}
                  >
                    暂停规则
                  </Button>
                ) : detail.rule.status === "paused" || detail.rule.status === "draft" ? (
                  <Button
                    theme="primary"
                    variant="text"
                    size="small"
                    icon={<PlayCircleIcon />}
                    aria-disabled={readOnly}
                    disabled={readOnly}
                    onClick={() => {
                      if (!readOnly) onActivate?.(detail.rule);
                    }}
                  >
                    启用规则
                  </Button>
                ) : null}
              </div>
            </div>
            <Alert
              theme="info"
              icon={<SecuredIcon aria-hidden="true" />}
              title="不可变 revision"
              message="定义摘要、revision 和事件证据均来自租户权威；此页面不会编辑历史或执行动作。"
            />
            <RuleFacts detail={detail} />
            <section
              className="automation-workflows__detail-section"
              aria-labelledby="automation-current-definition-title"
            >
              <div className="automation-workflows__section-heading">
                <div>
                  <span className="automation-workflows__eyebrow">CURRENT DEFINITION</span>
                  <h3 id="automation-current-definition-title">当前规则定义</h3>
                </div>
                <Tag theme="primary" variant="light-outline" size="small">
                  {detail.current_revision
                    ? `${detail.current_revision.action_plan.length} 个受限动作`
                    : "未返回"}
                </Tag>
              </div>
              {detail.current_revision ? (
                <div className="automation-workflows__definition-summary">
                  <div>
                    <span>WHEN</span>
                    <strong>{triggerLabel(detail.current_revision.trigger_code)}</strong>
                  </div>
                  <div>
                    <span>IF</span>
                    <strong>{conditionLabel(detail.current_revision.condition_code)}</strong>
                  </div>
                  <div>
                    <span>REQUEST</span>
                    <strong>
                      {detail.current_revision.action_plan
                        .map((step) => actionLabel(step.action_code))
                        .join("、")}
                    </strong>
                  </div>
                  <div>
                    <span>DIGEST</span>
                    <code>{detail.current_revision.definition_digest}</code>
                  </div>
                </div>
              ) : (
                <p className="automation-workflows__muted">当前 revision 未返回。</p>
              )}
            </section>
            <section
              className="automation-workflows__detail-section"
              aria-labelledby="automation-revision-history-title"
            >
              <div className="automation-workflows__section-heading">
                <div>
                  <span className="automation-workflows__eyebrow">REVISION HISTORY</span>
                  <h3 id="automation-revision-history-title">Revision 历史</h3>
                </div>
              </div>
              <RevisionTimeline revisions={detail.revisions} />
            </section>
            <section
              className="automation-workflows__detail-section"
              aria-labelledby="automation-event-evidence-title"
            >
              <div className="automation-workflows__section-heading">
                <div>
                  <span className="automation-workflows__eyebrow">EVENT EVIDENCE</span>
                  <h3 id="automation-event-evidence-title">事件证据</h3>
                </div>
                <Tag theme="success" variant="light-outline" size="small">
                  <CheckCircleIcon aria-hidden="true" /> append-only
                </Tag>
              </div>
              <EventTimeline events={detail.events} />
            </section>
            {detail.recent_runs.length ? (
              <section
                className="automation-workflows__detail-section"
                aria-labelledby="automation-recent-runs-title"
              >
                <div className="automation-workflows__section-heading">
                  <div>
                    <span className="automation-workflows__eyebrow">RECENT RUNS</span>
                    <h3 id="automation-recent-runs-title">最近运行</h3>
                  </div>
                </div>
                <ul className="automation-workflows__recent-run-list">
                  {detail.recent_runs.map((run) => (
                    <li key={run.id}>
                      <strong>{run.id}</strong>
                      <span>
                        {run.status} · {run.requested_count}/{run.action_count} requests
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
          </>
        ) : null}
        {readOnly ? (
          <Alert
            theme="warning"
            icon={<CloseCircleIcon aria-hidden="true" />}
            title="只读模式"
            message="当前页面仅用于审计查看；任何规则 mutation 都已禁用。"
          />
        ) : null}
      </section>
    </Drawer>
  );
}
