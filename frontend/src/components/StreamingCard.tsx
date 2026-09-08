import { Card, Skeleton } from "../ui/index";

interface Props {
  /** 已流出的答案文本 */
  text: string;
  /** 当前流式阶段（生成中显示光标） */
  phase?: string | null;
}

/**
 * 流式回答卡片：生成阶段逐 token 渲染 + 光标动画。
 * 完成前与 AnswerCard 同构（Card 容器），完成后被最终 AnswerCard 替换
 * （最终答案经后处理 + Markdown 渲染）。
 */
export default function StreamingCard({ text, phase }: Props) {
  const generating = phase === "generating" || phase === "retrieving_again";
  return (
    <Card size="small" className="answer-card" styles={{ body: { paddingTop: 14 } }}>
      <div className="answer-text markdown-body streaming-answer" aria-live="polite">
        {text ? (
          <div className="streaming-text">{text}</div>
        ) : (
          <Skeleton active title={false} paragraph={{ rows: 2 }} />
        )}
        {generating && text && <span className="stream-cursor" aria-hidden="true" />}
      </div>
    </Card>
  );
}
