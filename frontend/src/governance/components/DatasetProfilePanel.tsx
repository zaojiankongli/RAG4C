import { Alert, Button, Card, Tag } from "tdesign-react";
import { EditIcon, RefreshIcon } from "tdesign-icons-react";
import { useRef, useState } from "react";
import {
  datasetStatusPresentation,
  summarizeDatasetPolicies,
  type DatasetProfile,
  type DatasetProfilePatch,
  type DatasetRevisionRequest,
  type GovernanceErrorView,
} from "../model/governanceModel";
import DatasetLifecycleActions from "./DatasetLifecycleActions";
import DatasetProfileEditor from "./DatasetProfileEditor";

interface Props {
  profile: DatasetProfile;
  error: GovernanceErrorView | null;
  mutating: boolean;
  onRefresh: () => Promise<boolean>;
  onUpdate: (payload: DatasetProfilePatch) => Promise<boolean>;
  onArchive: (payload: DatasetRevisionRequest) => Promise<boolean>;
  onRestore: (payload: DatasetRevisionRequest) => Promise<boolean>;
  onDisable: (payload: DatasetRevisionRequest) => Promise<boolean>;
}

const visibilityLabels = { private: "仅自己", tenant: "租户内", public: "公开" } as const;

export default function DatasetProfilePanel(props: Props) {
  const { profile, error, mutating, onRefresh, onUpdate, onArchive, onRestore, onDisable } = props;
  const [editing, setEditing] = useState(false);
  const editButtonRef = useRef<HTMLButtonElement | null>(null);
  const closeEditor = () => { setEditing(false); queueMicrotask(() => editButtonRef.current?.focus()); };
  const status = datasetStatusPresentation[profile.status];
  const summaries = summarizeDatasetPolicies(profile.policies);
  return (
    <section aria-labelledby="dataset-profile-heading" className="governance-section">
      {error ? (
        <Alert
          className="governance-action-error"
          theme={error.kind === "conflict" ? "warning" : "error"}
          title={error.title}
          message={error.description}
          operation={
            error.canRetry ? (
              <Button size="small" variant="outline" aria-label="刷新数据集资料" onClick={() => void onRefresh()}>
                <RefreshIcon /> 刷新
              </Button>
            ) : undefined
          }
        />
      ) : null}
      <Card
        title={<span id="dataset-profile-heading">数据集资料与策略</span>}
        subtitle="修订号是所有资料和生命周期操作的并发边界"
        actions={profile.status === "active" ?
          <Button ref={editButtonRef} theme="primary" variant="outline" onClick={() => setEditing(true)} aria-label="编辑数据集资料">
            <EditIcon /> 编辑资料
          </Button> : undefined
        }
      >
        <div className="governance-profile-head">
          <div>
            <span className="governance-eyebrow">{profile.id}</span>
            <h3>{profile.name}</h3>
            <p>{profile.description || "未提供描述"}</p>
          </div>
          <div className="governance-profile-tags">
            <Tag theme={status.theme}>{status.label}</Tag>
            <Tag theme="primary" variant="light-outline">Revision {profile.profile_revision}</Tag>
          </div>
        </div>
        <dl className="governance-fact-grid">
          <div><dt>负责人</dt><dd>{profile.owner_id || "未指定"}</dd></div>
          <div><dt>可见范围</dt><dd>{visibilityLabels[profile.visibility]}</dd></div>
          <div><dt>默认语言</dt><dd>{profile.default_language}</dd></div>
          <div><dt>文档 / 片段</dt><dd>{profile.usage.documents} / {profile.usage.chunks}</dd></div>
          <div><dt>知识图谱</dt><dd>{profile.graph_enabled ? "已启用" : "未启用"}</dd></div>
          <div><dt>QA 知识</dt><dd>{profile.qa_enabled ? "已启用" : "未启用"}</dd></div>
          <div><dt>最近更新</dt><dd>{new Date(profile.timestamps.updated_at).toLocaleString("zh-CN")}</dd></div>
        </dl>
        <div className="governance-policy-grid">
          {summaries.map((summary) => (
            <article key={summary.key} className="governance-policy-card">
              <h4>{summary.label}</h4>
              {summary.facts.length ? summary.facts.map((fact) => (
                <div key={fact.label}><span>{fact.label}</span><strong>{fact.value}</strong></div>
              )) : <p>未公开安全摘要</p>}
              {summary.credentialRefs.length ? (
                <div className="governance-reference-badges" role="group" aria-label={`${summary.label}凭据引用`}>
                  {summary.credentialRefs.map((reference) => <Tag key={reference} variant="light-outline">{reference}</Tag>)}
                </div>
              ) : null}
            </article>
          ))}
        </div>
        <div className="governance-card-actions">
          <DatasetLifecycleActions
            profile={profile}
            mutating={mutating}
            onArchive={onArchive}
            onRestore={onRestore}
            onDisable={onDisable}
          />
        </div>
      </Card>
      <DatasetProfileEditor
        profile={profile}
        visible={editing && profile.status === "active"}
        saving={mutating}
        error={error}
        onClose={closeEditor}
        onSave={onUpdate}
      />
    </section>
  );
}
