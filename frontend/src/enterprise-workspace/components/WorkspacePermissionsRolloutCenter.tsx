import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, PrimaryTable, Tag, Textarea, type PrimaryTableCol } from "tdesign-react";
import { SecuredIcon } from "tdesign-icons-react";
import { ApiError } from "../../api/client";
import type { EnterpriseContext, EnterpriseScope } from "../../enterprise-admin/model";
import {
  createApprovalIdempotencyKey,
  createApprovalRequest,
} from "../../enterprise-approval/api/enterpriseApprovalApi";
import { approvalCenterNavigationUrl } from "../../enterprise-admin/memberRoleApprovalNavigation";
import type { EnterpriseWorkspace } from "../enterpriseWorkspaceModel";
import {
  createWorkspaceAuthorizationIdempotencyKey,
  fetchWorkspaceAuthorizationImpact,
  fetchWorkspaceAuthorizationPolicy,
  updateWorkspaceAuthorizationMode,
} from "../api/enterpriseWorkspaceAuthorizationApi";
import {
  type WorkspaceAuthorizationImpact,
  type WorkspaceAuthorizationMode,
  type WorkspaceAuthorizationPolicyResponse,
} from "../enterpriseWorkspaceAuthorizationModel";
import {
  highRiskWorkspaceAuthorizationEvidenceError,
  requiresWorkspaceAuthorizationApproval,
  validateWorkspaceAuthorizationModeChange,
  type WorkspaceAuthorizationModeChangeErrors,
} from "../enterpriseWorkspaceAuthorizationValidation";

interface Props {
  scope: EnterpriseScope;
  context: EnterpriseContext;
  workspace: EnterpriseWorkspace;
  legacyAuthorizationState?: string;
}

type RoleRow = {
  role: string;
  label: string;
  datasetProjectionRole: string;
  permissions: string[];
};

type ImpactRow = {
  key: string;
  current: string;
  candidate: string;
  delta: string;
};

const ROLE_ROWS: RoleRow[] = [
  {
    role: "owner",
    label: "所有者",
    datasetProjectionRole: "manager",
    permissions: ["read", "write", "delete", "manage", "audit"],
  },
  {
    role: "admin",
    label: "管理员",
    datasetProjectionRole: "manager",
    permissions: ["read", "write", "delete", "manage", "audit"],
  },
  {
    role: "editor",
    label: "编辑者",
    datasetProjectionRole: "editor",
    permissions: ["read", "write", "delete"],
  },
  {
    role: "viewer",
    label: "查看者",
    datasetProjectionRole: "viewer",
    permissions: ["read"],
  },
];

const MODE_OPTIONS: Array<[WorkspaceAuthorizationMode, string]> = [
  ["disabled", "已停用"],
  ["shadow", "Shadow 观察"],
  ["enforced", "强制执行"],
];

const MODE_LABELS: Record<WorkspaceAuthorizationMode, string> = {
  disabled: "已停用",
  shadow: "Shadow 观察",
  enforced: "强制执行",
};

const STATE_LABELS: Record<WorkspaceAuthorizationImpact["state"], string> = {
  workspace_authorization_not_available: "未返回可用授权状态",
  workspace_authorization_disabled: "Disabled：未启用 Workspace 授权",
  workspace_authorization_shadow: "Shadow：仅观察候选权限",
  workspace_authorization_enforced: "Enforced：Workspace 权限已生效",
};

const REASON_ERROR_ID = "workspace-authorization-reason-error";

interface WorkspaceAuthorizationTextareaRef {
  currentElement: HTMLDivElement;
  textareaElement: HTMLTextAreaElement;
}

function listLabel(values: string[]): string {
  return values.length ? values.join(" / ") : "无";
}

function nullableLabel(value: string | null | undefined): string {
  return value?.trim() || "未返回";
}

function apiErrorCode(error: ApiError): string | null {
  const body = error.body;
  if (!body || typeof body !== "object") return null;
  const detail = (body as { detail?: unknown }).detail;
  if (!detail || typeof detail !== "object") return null;
  const code = (detail as { code?: unknown }).code;
  return typeof code === "string" ? code : null;
}

function workspaceAuthorizationErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return error instanceof Error && error.message.trim()
      ? error.message
      : "Workspace 授权服务返回了无法识别的错误";
  }
  const code = apiErrorCode(error);
  const messages: Record<string, string> = {
    workspace_authorization_forbidden: "当前身份无权管理 Workspace 授权模式。",
    workspace_authorization_not_found: "Workspace 授权策略不存在，无法继续变更。",
    workspace_authorization_policy_revision_conflict:
      "Policy revision 已变化，请刷新 Workspace 授权事实后重试。",
    workspace_authorization_workspace_revision_conflict:
      "Workspace revision 已变化，请刷新 Workspace 事实后重试。",
    workspace_authorization_model_conflict:
      "权限模型版本或指纹已变化，请刷新授权事实后重试。",
    workspace_authorization_approval_required: "服务端要求该模式变更先经过企业审批。",
    workspace_authorization_workspace_archived: "Archived Workspace 不能启用授权。",
    workspace_authorization_unavailable: "Workspace 授权服务暂不可用，请稍后重试。",
  };
  if (code && messages[code]) return messages[code];
  if (error.message.trim()) return `服务端拒绝了 Workspace 授权变更：${error.message}`;
  if (error.status === 403) return "当前身份无权管理 Workspace 授权模式。";
  if (error.status === 404) return "Workspace 授权策略不存在，无法继续变更。";
  if (error.status === 409) return "Workspace 授权状态已变化，请刷新授权事实后重试。";
  if (error.status === 422) return "Workspace 授权变更参数无效，请检查输入。";
  if (error.status === 503) return "Workspace 授权服务暂不可用，请稍后重试。";
  return "Workspace 授权变更失败，请查看服务端返回的错误。";
}

function impactSemantics(impact: WorkspaceAuthorizationImpact): ImpactRow {
  if (impact.state === "workspace_authorization_disabled") {
    return {
      key: "permissions",
      current: `当前有效权限：${listLabel(impact.current_effective_permissions)}`,
      candidate: "Workspace candidate：未启用",
      delta: "权限增量：未启用",
    };
  }
  if (impact.state === "workspace_authorization_shadow") {
    return {
      key: "permissions",
      current: `当前有效权限：${listLabel(impact.current_effective_permissions)}`,
      candidate: `Workspace candidate：${listLabel(impact.candidate_permissions)}`,
      delta: `权限增量（would grant）：${listLabel(impact.would_grant_permissions)}`,
    };
  }
  if (impact.state === "workspace_authorization_enforced") {
    return {
      key: "permissions",
      current: `当前有效权限：${listLabel(impact.current_effective_permissions)}`,
      candidate: `Workspace candidate：${listLabel(impact.candidate_permissions)}`,
      delta: `权限增量（granted）：${listLabel(impact.granted_permissions)}`,
    };
  }
  return {
    key: "permissions",
    current: "当前有效权限：未返回",
    candidate: "Workspace candidate：未返回",
    delta: "权限增量：未返回",
  };
}

function relationLabel(value: string): string {
  return value.replaceAll("_", " ");
}

function navigateToApprovalRequest(requestId: string): void {
  if (typeof window === "undefined") return;
  const base = approvalCenterNavigationUrl(window.location);
  const separator = base.includes("?") ? "&" : "?";
  window.history.pushState(
    window.history.state,
    "",
    `${base}${separator}request=${encodeURIComponent(requestId)}`,
  );
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export default function WorkspacePermissionsRolloutCenter({ scope, context, workspace }: Props) {
  const [authority, setAuthority] = useState<WorkspaceAuthorizationPolicyResponse | null>(null);
  const [impact, setImpact] = useState<WorkspaceAuthorizationImpact | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [targetMode, setTargetMode] = useState<WorkspaceAuthorizationMode>("shadow");
  const [reason, setReason] = useState("");
  const [validationErrors, setValidationErrors] = useState<WorkspaceAuthorizationModeChangeErrors>(
    {},
  );
  const [saving, setSaving] = useState(false);
  const [success, setSuccess] = useState<string | null>(null);
  const [approvalRequestId, setApprovalRequestId] = useState<string | null>(null);
  const [submissionError, setSubmissionError] = useState<string | null>(null);
  const triggerRef = useRef<HTMLElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const panelTitleRef = useRef<HTMLHeadingElement>(null);
  const reasonRef = useRef<WorkspaceAuthorizationTextareaRef>(null);
  const roleTableRef = useRef<HTMLDivElement>(null);
  const impactTableRef = useRef<HTMLDivElement>(null);
  const approvalIdempotencyKeyRef = useRef<string | null>(null);
  const directMutationIdempotencyKeyRef = useRef<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [nextAuthority, nextImpact] = await Promise.all([
        fetchWorkspaceAuthorizationPolicy(scope, workspace.id),
        fetchWorkspaceAuthorizationImpact(scope, workspace.id),
      ]);
      setAuthority(nextAuthority);
      setImpact(nextImpact.impact);
      setTargetMode(nextAuthority.policy.mode);
    } catch (reasonValue) {
      setError(workspaceAuthorizationErrorMessage(reasonValue));
    } finally {
      setLoading(false);
    }
  }, [scope, workspace.id]);

  useEffect(() => {
    void load();
  }, [load]);

  useLayoutEffect(() => {
    roleTableRef.current
      ?.querySelector("table")
      ?.setAttribute("aria-label", "Workspace 角色权限矩阵");
    impactTableRef.current
      ?.querySelector("table")
      ?.setAttribute("aria-label", "Workspace 授权影响预览");
  }, [authority, impact]);

  useLayoutEffect(() => {
    if (!panelOpen) return;
    panelRef.current?.scrollIntoView?.({ block: "start", inline: "nearest" });
    panelTitleRef.current?.focus({ preventScroll: true });
  }, [panelOpen]);

  useEffect(() => {
    const element = reasonRef.current?.textareaElement;
    if (!element) return;
    if (validationErrors.reason) {
      element.setAttribute("aria-invalid", "true");
      element.setAttribute("aria-describedby", REASON_ERROR_ID);
    } else {
      element.removeAttribute("aria-invalid");
      element.removeAttribute("aria-describedby");
    }
  }, [validationErrors.reason]);

  const roleColumns = useMemo<PrimaryTableCol<RoleRow>[]>(
    () => [
      { colKey: "label", title: "Workspace role" },
      { colKey: "datasetProjectionRole", title: "Dataset projection role" },
      {
        colKey: "permissions",
        title: "权限",
        cell: ({ row }) => row.permissions.join(" / "),
      },
    ],
    [],
  );

  const impactRows = useMemo(() => (impact ? [impactSemantics(impact)] : []), [impact]);
  const impactColumns: PrimaryTableCol<ImpactRow>[] = [
    { colKey: "current", title: "当前有效权限" },
    { colKey: "candidate", title: "Workspace candidate" },
    { colKey: "delta", title: "权限增量" },
  ];

  const manager = ["owner", "admin"].includes(context.actor.role);
  const approval = authority?.evidence.matching_approval_policy;
  const approvalRequired = requiresWorkspaceAuthorizationApproval(targetMode, approval);
  const highRiskTarget = targetMode === "disabled" || targetMode === "enforced";
  const evidenceError = authority
    ? highRiskWorkspaceAuthorizationEvidenceError({
        authority,
        expectedTenantId: scope.tenantId,
        expectedWorkspaceId: workspace.id,
      })
    : null;
  const submissionBlocked = highRiskTarget && Boolean(evidenceError);
  const approvalSubmitted = Boolean(approvalRequestId);

  const closePanel = useCallback(() => {
    setPanelOpen(false);
    setValidationErrors({});
    setSubmissionError(null);
    approvalIdempotencyKeyRef.current = null;
    directMutationIdempotencyKeyRef.current = null;
    triggerRef.current?.focus();
  }, []);

  const openPanel = useCallback(() => {
    if (!authority) return;
    setTargetMode(authority.policy.mode === "disabled" ? "shadow" : authority.policy.mode);
    setReason("");
    setValidationErrors({});
    setSubmissionError(null);
    setSuccess(null);
    setApprovalRequestId(null);
    approvalIdempotencyKeyRef.current = createApprovalIdempotencyKey();
    directMutationIdempotencyKeyRef.current = createWorkspaceAuthorizationIdempotencyKey();
    setPanelOpen(true);
  }, [authority]);

  const submit = async () => {
    if (!authority || saving || approvalSubmitted || submissionBlocked) return;
    const errors = validateWorkspaceAuthorizationModeChange({
      currentMode: authority.policy.mode,
      targetMode,
      policyRevision: authority.policy.revision,
      workspaceRevision: workspace.revision,
      reason,
    });
    setValidationErrors(errors);
    setSubmissionError(null);
    const first =
      errors.targetMode || errors.policyRevision || errors.workspaceRevision || errors.reason;
    if (first) {
      if (errors.reason) reasonRef.current?.textareaElement.focus();
      return;
    }
    if (highRiskTarget && evidenceError) {
      setSubmissionError(evidenceError);
      return;
    }
    setSaving(true);
    try {
      if (approvalRequired && approval) {
        const request = await createApprovalRequest(
          scope,
          {
            policy_id: approval.id,
            resource_type: "tenant_workspace",
            resource_id: workspace.id,
            snapshot: {
              workspace_id: workspace.id,
              workspace_revision: workspace.revision,
              policy_revision: authority.policy.revision,
              from_mode: authority.policy.mode,
              target_mode: targetMode,
              permission_model_version: authority.policy.permission_model_version,
              permission_matrix_fingerprint: authority.evidence.permission_matrix_fingerprint,
              catalog_revision: authority.evidence.catalog_revision,
              reason: reason.trim(),
            },
            reason: reason.trim(),
          },
          { idempotencyKey: approvalIdempotencyKeyRef.current ?? undefined },
        );
        setApprovalRequestId(request.id);
        setSuccess("审批申请已提交");
      } else {
        const next = await updateWorkspaceAuthorizationMode(
          scope,
          workspace.id,
          {
            expected_revision: authority.policy.revision,
            target_mode: targetMode,
            reason: reason.trim(),
          },
          { idempotencyKey: directMutationIdempotencyKeyRef.current ?? undefined },
        );
        setAuthority(next);
        setSuccess("Workspace 授权模式已更新");
        closePanel();
        await load();
      }
    } catch (reasonValue) {
      setSubmissionError(workspaceAuthorizationErrorMessage(reasonValue));
    } finally {
      setSaving(false);
    }
  };

  const handlePanelKeyDown = (event: React.KeyboardEvent<HTMLElement>) => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    event.stopPropagation();
    closePanel();
  };

  if (loading)
    return (
      <Alert
        theme="warning"
        title="正在读取 Workspace 授权事实"
        message="Workspace mode、policy revision 与 impact evidence 正在从服务端加载。"
      />
    );
  if (error || !authority || !impact)
    return (
      <Alert
        theme="error"
        title="Workspace 授权事实读取失败"
        message={error ?? "服务端未返回完整的 Workspace policy 与 impact evidence。"}
      />
    );

  const impactSummary = impactSemantics(impact);
  const matchingApproval = approval
    ? `${approval.name} · ${approval.required_approvals} 人审批 · ${approval.state}`
    : "未匹配审批策略";

  return (
    <section
      className="workspace-authorization-rollout"
      aria-label="Workspace Permissions Rollout Center"
    >
      <div className="workspace-authorization-mode-row">
        <div>
          <span>WORKSPACE AUTHORIZATION</span>
          <h3>{MODE_LABELS[authority.policy.mode]}</h3>
          <p>{STATE_LABELS[impact.state]}</p>
        </div>
        <Tag
          theme={
            authority.policy.mode === "enforced"
              ? "success"
              : authority.policy.mode === "shadow"
                ? "warning"
                : "default"
          }
        >
          {authority.policy.mode.toUpperCase()}
        </Tag>
        <Button
          ref={triggerRef}
          tag="button"
          disabled={!manager}
          onClick={openPanel}
          aria-haspopup="dialog"
          aria-expanded={panelOpen}
        >
          变更 Workspace 授权模式
        </Button>
      </div>

      <dl
        className="workspace-authorization-evidence"
        role="region"
        aria-label="Workspace 授权策略证据"
      >
        <div>
          <dt>Workspace ID</dt>
          <dd>{workspace.id}</dd>
        </div>
        <div>
          <dt>Catalog revision</dt>
          <dd>{nullableLabel(authority.evidence.catalog_revision)}</dd>
        </div>
        <div>
          <dt>Policy Revision</dt>
          <dd>{authority.policy.revision}</dd>
        </div>
        <div>
          <dt>Permission Model</dt>
          <dd>v{authority.policy.permission_model_version}</dd>
        </div>
        <div>
          <dt>Matrix Fingerprint</dt>
          <dd>{nullableLabel(authority.evidence.permission_matrix_fingerprint)}</dd>
        </div>
        <div>
          <dt>Approval policy</dt>
          <dd>{matchingApproval}</dd>
        </div>
        <div>
          <dt>成员 / 绑定</dt>
          <dd>
            {authority.evidence.active_member_count ?? "未返回"} /{" "}
            {authority.evidence.active_dataset_binding_count ?? "未返回"}
          </dd>
        </div>
      </dl>

      {success ? (
        <Alert
          theme="success"
          title={success}
          message={approvalRequestId ? `Request ID：${approvalRequestId}` : "策略已刷新"}
        />
      ) : null}
      {impact.warnings.map((warning) => (
        <Alert
          key={warning}
          theme="warning"
          icon={<SecuredIcon />}
          title={warning}
          message={STATE_LABELS[impact.state]}
        />
      ))}

      <section className="workspace-authorization-section" aria-label="Role Matrix">
        <header>
          <div>
            <h4>Role Matrix</h4>
            <p>Workspace role 映射到 Dataset projection role；仅表示 v1 权限模型的投影。</p>
          </div>
        </header>
        <div ref={roleTableRef} className="workspace-authorization-table">
          <PrimaryTable rowKey="role" data={ROLE_ROWS} columns={roleColumns} bordered />
        </div>
        <ul className="workspace-authorization-cards" aria-label="移动端 Workspace 角色权限矩阵">
          {ROLE_ROWS.map((row) => (
            <li key={row.role}>
              <strong>{row.label}</strong>
              <span>Dataset projection role：{row.datasetProjectionRole}</span>
              <span>{row.permissions.join(" / ")}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="workspace-authorization-section" aria-label="Impact Preview">
        <header>
          <div>
            <h4>Impact Preview</h4>
            <p>当前权限、Workspace candidate 与 mode-specific delta 必须按服务端状态解释。</p>
          </div>
          <Tag variant="light-outline">{STATE_LABELS[impact.state]}</Tag>
        </header>
        <dl className="workspace-authorization-impact-facts">
          <div>
            <dt>Dataset</dt>
            <dd>{impact.dataset_id}</dd>
          </div>
          <div>
            <dt>Tenant role</dt>
            <dd>{nullableLabel(impact.tenant_role)}</dd>
          </div>
          <div>
            <dt>Dataset ACL role</dt>
            <dd>{nullableLabel(impact.dataset_acl_role)}</dd>
          </div>
        </dl>

        <section className="workspace-authorization-relations" aria-label="Matched grants">
          <h5>Matched grants</h5>
          {impact.matched_grants.length ? (
            <ul>
              {impact.matched_grants.map((grant) => (
                <li key={grant.id}>
                  <strong>{grant.subject_name}</strong>
                  <span>
                    {relationLabel(grant.subject_type)} · {grant.subject_id} · role: {grant.role}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p>无匹配 grant</p>
          )}
        </section>

        <section className="workspace-authorization-relations" aria-label="Workspace roles">
          <h5>Workspace roles</h5>
          {impact.workspace_roles.length ? (
            <ul>
              {impact.workspace_roles.map((role) => (
                <li key={`${role.workspace_id}-${role.binding_kind}`}>
                  <strong>{role.workspace_name}</strong>
                  <span>
                    Workspace role: {role.role} · binding kind: {role.binding_kind} · policy
                    revision: {role.policy_revision} · mode: {role.policy_mode}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p>无 Workspace role evidence</p>
          )}
        </section>

        <section className="workspace-authorization-relations" aria-label="Contributing workspaces">
          <h5>Contributing workspaces</h5>
          {impact.contributing_workspaces.length ? (
            <ul>
              {impact.contributing_workspaces.map((workspaceEvidence) => (
                <li
                  key={`${workspaceEvidence.workspace_id}-${workspaceEvidence.binding_kind}-${workspaceEvidence.policy_revision}`}
                >
                  <strong>{workspaceEvidence.workspace_name}</strong>
                  <span>
                    Workspace role: {workspaceEvidence.role} · binding kind:{" "}
                    {workspaceEvidence.binding_kind} · policy revision:{" "}
                    {workspaceEvidence.policy_revision}
                  </span>
                  <span>permissions: {listLabel(workspaceEvidence.permissions)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p>当前 mode 没有实际贡献 Workspace 权限</p>
          )}
        </section>

        <div ref={impactTableRef} className="workspace-authorization-table">
          <PrimaryTable rowKey="key" data={impactRows} columns={impactColumns} bordered />
        </div>
        <ul className="workspace-authorization-cards" aria-label="移动端 Workspace 授权影响预览">
          <li>
            <strong>{impactSummary.current}</strong>
            <span>{impactSummary.candidate}</span>
            <span>{impactSummary.delta}</span>
          </li>
        </ul>
      </section>

      {panelOpen ? (
        <section
          ref={panelRef}
          role="dialog"
          aria-modal="false"
          aria-labelledby="workspace-authorization-mode-title"
          aria-describedby="workspace-authorization-mode-description"
          className="workspace-authorization-mode-dialog"
          onKeyDown={handlePanelKeyDown}
        >
          <header>
            <div>
              <h4 id="workspace-authorization-mode-title" ref={panelTitleRef} tabIndex={-1}>
                变更 Workspace 授权模式
              </h4>
              <p id="workspace-authorization-mode-description">
                {workspace.id} · {MODE_LABELS[authority.policy.mode]} → {MODE_LABELS[targetMode]}
              </p>
            </div>
            <Button tag="button" variant="text" onClick={closePanel}>
              关闭
            </Button>
          </header>

          <div className="workspace-authorization-dialog">
            <dl className="workspace-authorization-change-evidence">
              <div>
                <dt>catalog revision</dt>
                <dd>{nullableLabel(authority.evidence.catalog_revision)}</dd>
              </div>
              <div>
                <dt>Policy Revision</dt>
                <dd>{authority.policy.revision}</dd>
              </div>
              <div>
                <dt>Workspace revision</dt>
                <dd>{workspace.revision}</dd>
              </div>
              <div>
                <dt>permission model / fingerprint</dt>
                <dd>
                  v{authority.policy.permission_model_version} ·{" "}
                  {nullableLabel(authority.evidence.permission_matrix_fingerprint)}
                </dd>
              </div>
              <div>
                <dt>impact summary</dt>
                <dd>{impactSummary.delta}</dd>
              </div>
            </dl>

            <fieldset className="workspace-authorization-mode-options">
              <legend>目标授权模式</legend>
              {MODE_OPTIONS.map(([mode, label]) => (
                <label key={mode}>
                  <input
                    type="radio"
                    name="workspace-authorization-target-mode"
                    value={mode}
                    checked={targetMode === mode}
                    onChange={() => {
                      setTargetMode(mode);
                      setValidationErrors((current) => ({ ...current, targetMode: undefined }));
                      setSubmissionError(null);
                    }}
                  />
                  <span>{label}</span>
                </label>
              ))}
            </fieldset>

            <Textarea
              ref={reasonRef}
              aria-label="变更原因"
              aria-invalid={Boolean(validationErrors.reason)}
              aria-describedby={validationErrors.reason ? REASON_ERROR_ID : undefined}
              name="workspace-authorization-reason"
              value={reason}
              maxlength={512}
              count
              status={validationErrors.reason ? "error" : "default"}
              onChange={(value) => {
                setReason(String(value));
                setValidationErrors((current) => ({ ...current, reason: undefined }));
              }}
            />
            {validationErrors.reason ? (
              <p id={REASON_ERROR_ID} role="alert" className="workspace-authorization-field-error">
                {validationErrors.reason}
              </p>
            ) : null}
            {validationErrors.targetMode ? (
              <p role="alert" className="workspace-authorization-field-error">
                {validationErrors.targetMode}
              </p>
            ) : null}
            {evidenceError && highRiskTarget ? (
              <Alert theme="error" title="无法提交高风险变更" message={evidenceError} />
            ) : null}
            {submissionError ? (
              <Alert theme="error" title="Workspace 授权变更未完成" message={submissionError} />
            ) : null}
            {approvalRequestId ? (
              <Button tag="button" onClick={() => navigateToApprovalRequest(approvalRequestId)}>
                前往审批中心
              </Button>
            ) : null}
          </div>

          <footer>
            <Button tag="button" variant="outline" onClick={closePanel}>
              取消
            </Button>
            <Button
              tag="button"
              loading={saving}
              disabled={saving || approvalSubmitted || submissionBlocked}
              onClick={() => void submit()}
            >
              {approvalRequired
                ? "提交审批申请"
                : targetMode === "shadow"
                  ? "切换到 Shadow"
                  : targetMode === "disabled"
                    ? "停用授权"
                    : "启用强制执行"}
            </Button>
          </footer>
        </section>
      ) : null}
    </section>
  );
}
