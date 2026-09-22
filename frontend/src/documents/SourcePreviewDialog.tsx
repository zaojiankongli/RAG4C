import { Dialog } from "tdesign-react";
import SourcePreview from "../parse-intervention/components/SourcePreview";
import type { ParseScope } from "../parse-intervention/model/parseInterventionModel";

/**
 * 原文查看的一等入口：从文档列表直接看这份原始文件，不必先进切分干预界面。
 *
 * 存在的理由就一条 —— "查看"不该是"编辑"的子面。此前 `SourcePreview` 全仓唯一挂载点在
 * `parse-intervention/components/ParsedContextPane.tsx`，也就是编辑台内部的一栏。
 *
 * 这个壳刻意**什么都不判断**：渲染面选 iframe / img / pre 还是只给下载，权威在 `SourcePreview`
 * 里的 `previewKind` + `dispositionToken`（后端 `Content-Disposition` 的表态优先于类型猜测），
 * 外加一张 `NEVER_RENDERED` 名单。在这里再判一次，就是在后端那张表之外另开一个"这类可以 inline"
 * 的判断源 —— 第九轮那个 blocker 的形状就是这么来的。所以本文件不许出现 mediaType / 后缀 / blob
 * 相关的分支。
 */
export default function SourcePreviewDialog({
  documentId,
  documentName,
  scope,
  onClose,
}: {
  documentId: string;
  documentName: string;
  scope: Omit<ParseScope, "docId">;
  onClose: () => void;
}) {
  const label = `原文查看 ${documentName}`;
  return (
    <Dialog
      header={`原文查看 · ${documentName}`}
      {...({ role: "dialog", "aria-modal": "true", "aria-label": label } as Record<string, unknown>)}
      visible
      width={880}
      placement="center"
      destroyOnClose
      footer={false}
      closeOnEscKeydown
      closeOnOverlayClick
      onClose={onClose}
    >
      <SourcePreview scope={{ ...scope, docId: documentId }} documentName={documentName} />
    </Dialog>
  );
}
