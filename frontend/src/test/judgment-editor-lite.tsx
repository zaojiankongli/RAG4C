import type { Judgment } from "../retrieval-quality/model/contracts";
import type { JudgmentDraft } from "../retrieval-quality/hooks/useJudgmentMutation";

interface Props {
  rank: number;
  judgments: Judgment[];
  actorId: string;
  draft: JudgmentDraft;
  conflict: boolean;
  saving: boolean;
  onChange: (draft: JudgmentDraft) => void;
  onSave: () => void;
}

export default function JudgmentEditorLite({ judgments, actorId, conflict }: Props) {
  const owned = judgments.find((item) => item.created_by === actorId);
  const foreign = judgments.filter((item) => item.created_by !== actorId);
  return (
    <section aria-label="轻量判断编辑器">
      {owned ? (
        <span>
          {owned.created_by} · r{owned.revision}
        </span>
      ) : (
        <span>尚未判断</span>
      )}
      {conflict ? <div role="alert">判断已被更新</div> : null}
      {foreign.map((item) => (
        <span key={item.id}>
          {item.created_by} · r{item.revision}
        </span>
      ))}
    </section>
  );
}
