import { Button, Popconfirm } from "tdesign-react";
import type { DatasetProfile, DatasetRevisionRequest } from "../model/governanceModel";

interface Props {
  profile: DatasetProfile;
  mutating: boolean;
  onArchive: (payload: DatasetRevisionRequest) => Promise<boolean>;
  onRestore: (payload: DatasetRevisionRequest) => Promise<boolean>;
  onDisable: (payload: DatasetRevisionRequest) => Promise<boolean>;
}

export default function DatasetLifecycleActions({ profile, mutating, onArchive, onRestore, onDisable }: Props) {
  const revision = { expected_revision: profile.profile_revision };
  if (profile.status === "archived") {
    return (
      <Popconfirm
        theme="warning"
        content="恢复后将重新开放此数据集的治理写入。"
        confirmBtn={{ content: "确认恢复", theme: "primary" }}
        cancelBtn={{ content: "取消" }}
        onConfirm={() => void onRestore(revision)}
      >
        <Button variant="outline" loading={mutating} aria-label="恢复数据集">恢复数据集</Button>
      </Popconfirm>
    );
  }
  if (profile.status === "disabled") return null;
  return (
    <div className="governance-danger-actions">
      <Popconfirm
        theme="warning"
        content="归档后仍可读取资料，但写入会受到限制。"
        confirmBtn={{ content: "确认归档", theme: "primary" }}
        cancelBtn={{ content: "取消" }}
        onConfirm={() => void onArchive(revision)}
      >
        <Button variant="outline" loading={mutating} aria-label="归档数据集">归档数据集</Button>
      </Popconfirm>
      <Popconfirm
        theme="danger"
        content="停用会关闭该数据集的后续读取和治理操作。"
        confirmBtn={{ content: "确认停用", theme: "danger" }}
        cancelBtn={{ content: "取消" }}
        onConfirm={() => void onDisable(revision)}
      >
        <Button theme="danger" variant="outline" loading={mutating} aria-label="停用数据集">停用数据集</Button>
      </Popconfirm>
    </div>
  );
}
