import "../sources-control/sources-control.css";
import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Tag } from "../ui";
import { CloudServerOutlined, PlusOutlined, ReloadOutlined } from "../ui/icons";
import PageState from "../components/PageState";
import PageTopbar from "../components/PageTopbar";
import { useConnection } from "../context/ConnectionContext";
import { useKnowledgeWorkspace } from "../knowledge/KnowledgeWorkspaceContext";
import { readKnowledgeActorToken } from "../knowledge/workspaceScope";
import RunHistoryPanel from "../sources-control/components/RunHistoryPanel";
import SourceEditorDrawer from "../sources-control/components/SourceEditorDrawer";
import SourceList from "../sources-control/components/SourceList";
import SyncNowDialog from "../sources-control/components/SyncNowDialog";
import { sourceScopeKey, useSourceControl } from "../sources-control/hooks/useSourceControl";
import { useSourceRuns } from "../sources-control/hooks/useSourceRuns";
import type {
  SourceCreate,
  SourcePatch,
  SourceRecord,
  SourceScope,
} from "../sources-control/model/sourceModels";
import {
  acceptedRequestSummary,
  projectSourceError,
  requireSourceScope,
  SourceScopeError,
} from "../sources-control/model/sourceProjection";

interface Props {
  active?: boolean;
  embedded?: boolean;
}
function Topbar({
  scoped,
  embedded,
  onCreate,
}: {
  scoped: boolean;
  embedded: boolean;
  onCreate?: () => void;
}) {
  if (embedded) return null;
  return (
    <PageTopbar
      icon={<CloudServerOutlined />}
      title="来源控制面"
      subtitle="管理权威连接器、同步意图与代次围栏"
      extra={
        scoped ? (
          <Button type="primary" icon={<PlusOutlined />} onClick={onCreate}>
            创建来源
          </Button>
        ) : undefined
      }
    />
  );
}

function SourceWorkspace({
  scope,
  active,
  embedded,
}: {
  scope: SourceScope;
  active: boolean;
  embedded: boolean;
}) {
  const control = useSourceControl(scope, true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editing, setEditing] = useState<SourceRecord | null | undefined>(undefined);
  const [syncing, setSyncing] = useState<SourceRecord | null>(null);
  useEffect(() => {
    if (!control.sources.length) {
      setSelectedId(null);
      return;
    }
    if (!selectedId || !control.sources.some((item) => item.id === selectedId))
      setSelectedId(control.sources[0].id);
  }, [control.sources, selectedId]);
  const selected = control.sources.find((item) => item.id === selectedId) ?? null;
  const runs = useSourceRuns(scope, selected, active);
  if (control.status === "loading" && !control.sources.length)
    return (
      <div className="page-slot sources-control-page">
        <Topbar scoped embedded={embedded} onCreate={() => setEditing(null)} />
        <div className="page-shell">
          <div className="page-shell-inner">
            <PageState
              status="loading"
              title="正在读取来源控制面"
              description="从当前租户和数据集读取权威来源登记。"
            />
          </div>
        </div>
      </div>
    );
  if (control.status === "error" && !control.sources.length)
    return (
      <div className="page-slot sources-control-page">
        <Topbar scoped embedded={embedded} onCreate={() => setEditing(null)} />
        <div className="page-shell">
          <div className="page-shell-inner">
            <PageState
              status="error"
              title={control.error?.title}
              description={control.error?.description}
              extra={
                control.error?.canRetry ? (
                  <Button icon={<ReloadOutlined />} onClick={() => void control.refresh()}>
                    刷新来源
                  </Button>
                ) : undefined
              }
            />
          </div>
        </div>
      </div>
    );
  const save = async (payload: SourceCreate | Omit<SourcePatch, "expected_generation">) =>
    editing
      ? control.update(editing, payload as Omit<SourcePatch, "expected_generation">)
      : control.create(payload as SourceCreate);
  const accepted = control.lastAccepted ? acceptedRequestSummary(control.lastAccepted) : null;
  return (
    <div className="page-slot sources-control-page">
      <Topbar scoped embedded={embedded} onCreate={() => setEditing(null)} />
      <div className="page-shell">
        <div className="page-shell-inner sources-control-shell">
          <section className="source-authority-banner" aria-label="来源控制面语义">
            <div>
              <span className="source-eyebrow">SOURCE API / AUTHORITATIVE</span>
              <h2>至少一次执行，代次围栏收敛投影</h2>
              <p>
                同步请求和恢复可能至少执行一次；持久化幂等键绑定来源、mutation generation
                与请求载荷。运行记录中的来源/数据集 generation 是判断投影是否仍属于当前配置的围栏。
              </p>
            </div>
            <div className="source-authority-badges">
              <Tag color="success">认证工作区</Tag>
              <Tag variant="light-outline">{scope.datasetId}</Tag>
            </div>
          </section>
          <Alert
            type="info"
            message="能力边界"
            description="定时同步尚未由后端实现；当前仅支持手动同步。执行 API 也未提供 execution_next_attempt_at，因此不会推测下次尝试时间或退避时长。"
          />
          <p className="source-safe-note">
            读取、同步和管理操作均由 Source API 使用当前操作者令牌授权；前端不推测角色。
          </p>
          {control.error ? (
            <div role="alert" className="source-inline-warning">
              <strong>{control.error.title}</strong>
              <span>{control.error.description}</span>
              {control.error.canRetry ? (
                <Button size="small" onClick={() => void control.refresh()}>
                  刷新权威事实
                </Button>
              ) : null}
            </div>
          ) : null}
          {accepted && control.lastAccepted ? (
            <div className="source-accepted" role="status">
              <strong>{accepted.action}</strong>
              <span>{accepted.current}</span>
            </div>
          ) : null}
          <SourceList
            sources={control.sources}
            selectedId={selectedId}
            mutatingId={control.mutatingId}
            onSelect={(item) => setSelectedId(item.id)}
            onEdit={(item) => setEditing(item)}
            onToggle={(item, enabled) => void control.setEnabled(item, enabled)}
            onSync={setSyncing}
          />
          {selected ? (
            <RunHistoryPanel
              source={selected}
              runs={runs.runs}
              status={runs.status}
              error={runs.error}
              runStatus={runs.runStatus}
              trigger={runs.trigger}
              nextCursor={runs.nextCursor}
              hasPrevious={runs.hasPrevious}
              paging={runs.paging}
              selectedRun={runs.selectedRun}
              items={runs.items}
              itemsNextCursor={runs.itemsNextCursor}
              hasPreviousItems={runs.hasPreviousItems}
              itemsPaging={runs.itemsPaging}
              detailLoading={runs.detailLoading}
              itemResult={runs.itemResult}
              itemAction={runs.itemAction}
              retrying={runs.retrying}
              lastAccepted={runs.lastAccepted}
              onRefresh={() => void runs.refresh()}
              onNext={() => void runs.nextPage()}
              onPrevious={() => void runs.previousPage()}
              onFilters={runs.setFilters}
              onSelect={(run) => void runs.selectRun(run)}
              onClose={runs.closeRun}
              onNextItems={() => void runs.nextItemsPage()}
              onPreviousItems={() => void runs.previousItemsPage()}
              onItemFilters={runs.setItemFilters}
              onRetry={(run) => void runs.retry(run)}
            />
          ) : (
            <PageState
              status="empty"
              title="尚未注册来源"
              description="创建本地目录或 GitHub 来源后，可在这里管理同步与运行历史。"
              compact
            />
          )}
          <SourceEditorDrawer
            open={editing !== undefined}
            source={editing ?? null}
            saving={control.mutatingId === (editing?.id ?? "new")}
            error={control.error}
            onClose={() => setEditing(undefined)}
            onSave={save}
          />
          <SyncNowDialog
            open={Boolean(syncing)}
            source={syncing}
            submitting={control.mutatingId === syncing?.id}
            onClose={() => setSyncing(null)}
            onSubmit={async (payload) => {
              if (!syncing) return false;
              const response = await control.sync(syncing, payload);
              if (!response) return false;
              await runs.resetAndRefresh();
              return true;
            }}
          />
        </div>
      </div>
    </div>
  );
}

export default function KnowledgeSourcesPage({ active = true, embedded = false }: Props) {
  const workspace = useKnowledgeWorkspace();
  const { online } = useConnection();
  const actorToken = readKnowledgeActorToken();
  const resolved = useMemo(() => {
    try {
      return { scope: requireSourceScope(workspace.scope, actorToken), error: null };
    } catch (caught) {
      return {
        scope: null,
        error: projectSourceError(
          caught instanceof SourceScopeError ? caught : new SourceScopeError(),
        ),
      };
    }
  }, [actorToken, workspace.scope]);
  if (!resolved.scope)
    return (
      <div className="page-slot sources-control-page">
        <Topbar scoped={false} embedded={embedded} />
        <div className="page-shell">
          <div className="page-shell-inner">
            <PageState
              status="error"
              title={resolved.error?.title}
              description={resolved.error?.description}
            />
          </div>
        </div>
      </div>
    );
  if (online === false)
    return (
      <div className="page-slot sources-control-page">
        <Topbar scoped embedded={embedded} />
        <div className="page-shell">
          <div className="page-shell-inner">
            <PageState
              status="error"
              title="来源服务未连接"
              description="当前无法读取 Source API 权威事实，不会使用演示数据或文档投影替代。"
            />
          </div>
        </div>
      </div>
    );
  return (
    <SourceWorkspace
      key={sourceScopeKey(resolved.scope)}
      scope={resolved.scope}
      active={active && online === true}
      embedded={embedded}
    />
  );
}
