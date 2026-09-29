import { useState } from "react";
import "./query-welcome.css";

const SAMPLE_QUESTIONS = [
  { question: "公司的报销流程是什么？" },
  { question: "员工年假制度是怎么规定的？" },
  { question: "入职需要准备哪些材料？" },
  { question: "去年 Q4 的毛利率是多少？" },
];

const EVIDENCE_STEPS = [
  { title: "检索资料", description: "查找已授权的相关资料" },
  { title: "核对出处", description: "结论关联原文依据" },
  { title: "组织回答", description: "依据不足时明确说明" },
];

export default function QueryWelcome({ onPick }: { onPick: (question: string) => void }) {
  const [questionSet, setQuestionSet] = useState(0);
  const questions = SAMPLE_QUESTIONS.slice(questionSet * 2, questionSet * 2 + 2);
  return (
    <section className="query-welcome" aria-labelledby="query-welcome-title">
      <div className="query-welcome-hero">
        <div className="query-welcome-intro">
          <div className="query-welcome-eyebrow">
            <span className="query-welcome-eyebrow-mark" aria-hidden="true" />
            <span>RAG4C · 知识问答</span>
          </div>
          <h2 id="query-welcome-title">今天，想弄明白什么？</h2>
          <p className="query-welcome-description">从已授权的资料中找答案，为关键结论标注出处。</p>
        </div>
        <div className="yanami-welcome-art" aria-hidden="true">
          <span className="yanami-art-character" />
        </div>
      </div>
      <div className="query-welcome-prompts">
        <div className="query-welcome-prompts-heading">
          <h3>也可以试着问</h3>
          <button
            type="button"
            className="query-welcome-shuffle"
            onClick={() => setQuestionSet((previous) => (previous + 1) % 2)}
          >
            换一组
          </button>
        </div>
        <div className="query-welcome-prompt-grid">
          {questions.map(({ question }) => (
            <button
              key={question}
              type="button"
              className="query-welcome-prompt"
              aria-label={question}
              onClick={() => onPick(question)}
            >
              <span>{question}</span>
              <span className="query-welcome-prompt-arrow" aria-hidden="true">
                ↗
              </span>
            </button>
          ))}
        </div>
      </div>
      <details className="query-evidence-panel">
        <summary>回答会怎样使用资料？</summary>
        <ol className="query-evidence-steps" aria-label="回答依据路径">
          {EVIDENCE_STEPS.map((step, index) => (
            <li className="query-evidence-step" key={step.title}>
              <span className="query-evidence-index" aria-hidden="true">
                0{index + 1}
              </span>
              <span className="query-evidence-copy">
                <strong>{step.title}</strong>
                <span>{step.description}</span>
              </span>
            </li>
          ))}
        </ol>
        <span className="sr-only">资料范围由页面顶部选择器控制</span>
      </details>
    </section>
  );
}
