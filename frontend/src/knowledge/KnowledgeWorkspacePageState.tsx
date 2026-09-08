import PageState from "../components/PageState";
import { Button } from "../ui";
import { ReloadOutlined } from "../ui/icons";
import type { KnowledgeWorkspaceStatus } from "./KnowledgeWorkspaceContext";

interface KnowledgeWorkspacePageStateProps {
  status: KnowledgeWorkspaceStatus;
  error: Error | null;
  onRetry: () => Promise<void>;
}

/** Shared blocking states for knowledge pages; empty remains a valid product view. */
export default function KnowledgeWorkspacePageState({
  status,
  error,
  onRetry,
}: KnowledgeWorkspacePageStateProps) {
  if (status === "loading") {
    return (
      <PageState
        status="loading"
        title="正在加载知识库数据"
        description="概览、知识组织和数据来源将共享同一份文档快照。"
      />
    );
  }

  if (status === "error") {
    return (
      <PageState
        status="error"
        title="知识库数据加载失败"
        description={error?.message ?? "暂时无法读取知识库目录，请稍后重试。"}
        extra={
          <Button type="primary" icon={<ReloadOutlined />} onClick={() => void onRetry()}>
            重试加载知识库数据
          </Button>
        }
      />
    );
  }

  return null;
}
