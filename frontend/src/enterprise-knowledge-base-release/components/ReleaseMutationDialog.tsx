import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Alert, Button, Dialog, Textarea } from "tdesign-react";
import type {
  CaptureReleaseInput,
  PromoteReleaseInput,
  RollbackReleaseInput,
} from "../api/releaseApi";
import type {
  ReleaseChannel,
  ReleaseManifest,
  ReleaseMutationKind,
  ReleaseMutationOutcome,
} from "../model/releaseModel";
import {
  releaseMutationLabel,
  releaseStatusLabel,
  safeReleaseDisplayText,
} from "../model/releaseModel";

export type ReleaseMutationInput =
  | CaptureReleaseInput
  | ({ releaseId: string } & PromoteReleaseInput)
  | ({ channelId: string } & RollbackReleaseInput);

export interface ReleaseCaptureFence {
  profileRevision: number;
  ownershipRevision: number;
  workspaceRevision: number;
  mutationGeneration: number;
  servingGeneration: number;
}

export interface ReleaseMutationDialogProps {
  visible: boolean;
  kind: ReleaseMutationKind | null;
  channel: ReleaseChannel | null;
  release: ReleaseManifest | null;
  targetRelease: ReleaseManifest | null;
  captureFence: ReleaseCaptureFence | null;
  servingGeneration: number | null;
  readOnly: boolean;
  saving: boolean;
  error: Error | null;
  onClose: () => void;
  onRetry: () => Promise<ReleaseMutationOutcome | null>;
  onSubmit: (
    kind: ReleaseMutationKind,
    input: ReleaseMutationInput,
  ) => Promise<ReleaseMutationOutcome | null>;
}

function titleFor(kind: ReleaseMutationKind | null, release: ReleaseManifest | null): string {
  if (kind === "capture") return "生成 Release 候选";
  if (kind === "promote") return "发布 Release 到 Channel";
  if (kind === "rollback") return "回滚 Channel";
  return release ? `Release ${release.release_number} 操作` : "Release 操作";
}

function confirmLabelFor(kind: ReleaseMutationKind | null): string {
  if (kind === "capture") return "生成候选";
  if (kind === "promote") return "确认发布";
  if (kind === "rollback") return "确认回滚";
  return "确认";
}

function revisionLabel(value: number | null | undefined): string {
  return value == null ? "未返回" : `R${value}`;
}

function generationLabel(value: number | null | undefined): string {
  return value == null ? "未返回" : String(value);
}

function FenceList({ rows }: { rows: Array<{ label: string; value: ReactNode }> }) {
  return (
    <dl className="knowledge-base-release-mutation__fence">
      {rows.map((row) => (
        <div key={row.label}>
          <dt>{row.label}</dt>
          <dd>{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export default function ReleaseMutationDialog({
  visible,
  kind,
  channel,
  release,
  targetRelease,
  captureFence,
  servingGeneration,
  readOnly,
  saving,
  error,
  onClose,
  onRetry,
  onSubmit,
}: ReleaseMutationDialogProps) {
  const [reason, setReason] = useState("");
  const [validation, setValidation] = useState("");
  const title = titleFor(kind, release);
  const target = targetRelease ?? release;

  useEffect(() => {
    if (!visible) return;
    setReason("");
    setValidation("");
  }, [kind, release?.id, targetRelease?.id, visible]);

  const fenceRows = useMemo(() => {
    if (kind === "capture") {
      return [
        { label: "Profile revision", value: revisionLabel(captureFence?.profileRevision) },
        { label: "Ownership revision", value: revisionLabel(captureFence?.ownershipRevision) },
        { label: "Workspace revision", value: revisionLabel(captureFence?.workspaceRevision) },
        { label: "Mutation generation", value: generationLabel(captureFence?.mutationGeneration) },
        { label: "Serving generation", value: generationLabel(captureFence?.servingGeneration) },
      ];
    }
    if (kind === "promote") {
      return [
        { label: "Channel revision", value: revisionLabel(channel?.revision) },
        { label: "Profile revision", value: revisionLabel(release?.profile_revision) },
        { label: "Ownership revision", value: revisionLabel(release?.ownership_revision) },
        { label: "Workspace revision", value: revisionLabel(release?.workspace_revision) },
        { label: "Serving generation", value: generationLabel(servingGeneration) },
      ];
    }
    return [
      { label: "Target Release", value: target ? `Release ${target.release_number}` : "未返回" },
      { label: "Channel revision", value: revisionLabel(channel?.revision) },
      { label: "Serving generation", value: generationLabel(servingGeneration) },
    ];
  }, [captureFence, channel?.revision, kind, release, servingGeneration, target]);

  if (!visible || !kind) return null;

  const canSubmit = !readOnly && !saving && Boolean(reason.trim());
  const submit = async () => {
    const trimmedReason = reason.trim();
    if (!trimmedReason) {
      setValidation("变更原因不能为空。");
      return;
    }
    if (kind === "capture") {
      if (!captureFence) {
        setValidation("当前未返回完整的 revision fence，无法安全生成候选。");
        return;
      }
      const outcome = await onSubmit("capture", {
        expectedProfileRevision: captureFence.profileRevision,
        expectedOwnershipRevision: captureFence.ownershipRevision,
        expectedWorkspaceRevision: captureFence.workspaceRevision,
        expectedMutationGeneration: captureFence.mutationGeneration,
        expectedServingGeneration: captureFence.servingGeneration,
        reason: trimmedReason,
      });
      if (outcome?.state === "applied" || outcome?.state === "approval_required") onClose();
      return;
    }
    if (!channel || !release || servingGeneration === null) {
      setValidation("当前未返回完整的 Channel 或 Release 事实，无法安全提交。");
      return;
    }
    if (kind === "promote") {
      const outcome = await onSubmit("promote", {
        releaseId: release.id,
        channelId: channel.id,
        expectedChannelRevision: channel.revision,
        expectedProfileRevision: release.profile_revision,
        expectedOwnershipRevision: release.ownership_revision,
        expectedWorkspaceRevision: release.workspace_revision,
        expectedServingGeneration: servingGeneration,
        reason: trimmedReason,
      });
      if (outcome?.state === "applied" || outcome?.state === "approval_required") onClose();
      return;
    }
    if (!target) {
      setValidation("当前未返回可验证的目标 Release，无法安全回滚。");
      return;
    }
    const outcome = await onSubmit("rollback", {
      channelId: channel.id,
      targetReleaseId: target.id,
      expectedChannelRevision: channel.revision,
      expectedServingGeneration: servingGeneration,
      reason: trimmedReason,
    });
    if (outcome?.state === "applied" || outcome?.state === "approval_required") onClose();
  };

  const retry = async () => {
    if (readOnly) return;
    const outcome = await onRetry();
    if (outcome?.state === "applied" || outcome?.state === "approval_required") onClose();
  };

  return (
    <Dialog
      visible={visible}
      header={title}
      width={560}
      destroyOnClose
      closeOnEscKeydown={!saving}
      closeOnOverlayClick={!saving}
      confirmBtn={{
        tag: "button",
        content: confirmLabelFor(kind),
        theme: kind === "rollback" ? "danger" : "primary",
        disabled: !canSubmit,
      }}
      cancelBtn={{ tag: "button", content: "取消", disabled: saving }}
      confirmLoading={saving}
      onClose={onClose}
      onConfirm={() => void submit()}
      {...({ role: "dialog", "aria-modal": "true", "aria-label": title } as Record<
        string,
        unknown
      >)}
    >
      <div className="knowledge-base-release-mutation">
        <div className="knowledge-base-release-mutation__intro">
          <div>
            <span className="knowledge-base-release-detail__section-kicker">
              REVISION-FENCED OPERATION
            </span>
            <strong>{releaseMutationLabel(kind)}</strong>
            <p>
              {kind === "capture"
                ? "基于最近一次服务端返回的权威快照生成不可变候选。"
                : kind === "promote"
                  ? `将 Release ${release?.release_number ?? "未返回"} 发布到 ${channel?.name ?? "当前 Channel"}。`
                  : `将 ${channel?.name ?? "当前 Channel"} 指向 ${target ? `Release ${target.release_number}` : "未返回"}。`}
            </p>
          </div>
          <TagLikeStatus kind={kind} channel={channel} release={release} target={target} />
        </div>
        {error ? (
          <div className="knowledge-base-release-mutation__error" role="alert">
            <Alert
              theme="error"
              title="Release 操作未提交"
              message={safeReleaseDisplayText(error.message) || "服务端拒绝了本次 Release 操作。"}
            />
            {!readOnly ? (
              <Button
                variant="text"
                theme="primary"
                size="small"
                disabled={saving}
                onClick={() => void retry()}
              >
                使用同一请求重试
              </Button>
            ) : null}
          </div>
        ) : null}
        {validation ? <Alert theme="warning" title="请补齐操作信息" message={validation} /> : null}
        <section aria-label="Release revision fence">
          <div className="knowledge-base-release-mutation__section-heading">
            <strong>本次操作使用的权威 fence</strong>
            <span>提交时服务端会再次校验</span>
          </div>
          <FenceList rows={fenceRows} />
        </section>
        <label className="knowledge-base-release-mutation__reason-field">
          <span>{kind === "capture" ? "候选原因" : "变更原因"}</span>
          <Textarea
            aria-label={kind === "capture" ? "候选原因" : "变更原因"}
            value={reason}
            disabled={saving || readOnly}
            placeholder={
              kind === "capture"
                ? "说明为什么要生成新的候选 Release"
                : "说明本次发布或回滚的业务原因"
            }
            autosize={{ minRows: 3, maxRows: 6 }}
            onChange={(value) => {
              setReason(String(value));
              setValidation("");
            }}
          />
        </label>
        {readOnly ? (
          <Alert
            theme="info"
            title="只读模式"
            message="当前身份只能查看发布权威，所有发布、回滚和候选生成操作已禁用。"
          />
        ) : null}
      </div>
    </Dialog>
  );
}

function TagLikeStatus({
  kind,
  channel,
  release,
  target,
}: {
  kind: ReleaseMutationKind;
  channel: ReleaseChannel | null;
  release: ReleaseManifest | null;
  target: ReleaseManifest | null;
}) {
  const label =
    kind === "capture"
      ? "候选快照"
      : kind === "promote"
        ? releaseStatusLabel(release?.status)
        : target
          ? releaseStatusLabel(target.status)
          : "未返回";
  return <span className="knowledge-base-release-mutation__status">{channel?.name || label}</span>;
}
