import { useEffect, useState } from "react";
import { Button, Progress, Space, Typography } from "../ui/index";
import { ApartmentOutlined, CheckOutlined } from "../ui/icons";
import { FONT_SIZE } from "../theme/tokens";
import { phaseToStep } from "./phaseModel";

const { Text } = Typography;

/** 阶段推进时间点（毫秒）：无事件时按耗时近似推进（离线演示 / 非流式回退） */
const PHASES = [
  { title: "查找资料", desc: "正在寻找和问题相关的资料", at: 1_200 },
  { title: "整理回答", desc: "正在根据资料组织答案", at: 4_000 },
  { title: "核对依据", desc: "正在确认答案有资料支持", at: 8_000 },
];

interface Props {
  /** 流式阶段事件（/api/query/stream 的 phase 事件）；无事件时按耗时近似推进 */
  phase?: string | null;
}

/**
 * 请求阶段状态指示（检索 → 生成 → 验证）+ 实时计时。
 *
 * - 流式模式：phase 事件驱动（真实链路阶段）；
 * - 非流式 / 离线演示：按 elapsed 时间近似推进（阶段时间点按真实链路
 *   耗时比例设置：检索快、生成与验证占大头）。
 */
export default function PhaseStatus({ phase }: Props) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const t0 = performance.now();
    const timer = setInterval(() => setElapsed(performance.now() - t0), 200);
    return () => clearInterval(timer);
  }, []);

  const step =
    phase != null
      ? phaseToStep(phase)
      : PHASES.reduce((acc, p, i) => (elapsed >= p.at ? i + 1 : acc), 0);
  const percent = Math.min(96, Math.round((elapsed / PHASES[PHASES.length - 1].at) * 100));

  const announcement =
    phase === "retrieving_again"
      ? "正在进行第二次资料检索"
      : (PHASES[Math.min(step, PHASES.length - 1)]?.desc ?? "正在处理问题");

  return (
    <div className="phase-status">
      <style>{`
        .phase-status .phase-step.is-done {
          color: var(--color-primary);
        }
        .phase-dot {
          display: inline-block;
          width: 8px;
          height: 8px;
          border-radius: 50%;
          background: var(--color-primary);
          flex-shrink: 0;
          animation: phase-dot-breathe 1.4s ease-in-out infinite;
        }
        @keyframes phase-dot-breathe {
          0%, 100% {
            opacity: 1;
            transform: scale(1);
          }
          50% {
            opacity: 0.35;
            transform: scale(0.65);
          }
        }
        @media (prefers-reduced-motion: reduce) {
          .phase-dot {
            animation: none;
          }
        }
      `}</style>
      <Space size={14} wrap style={{ marginBottom: 4 }}>
        {PHASES.map((p, i) => (
          <span
            key={p.title}
            className={"phase-step" + (i === step ? " is-active" : i < step ? " is-done" : "")}
          >
            {i < step ? <CheckOutlined /> : i === step ? <span className="phase-dot" aria-hidden="true" /> : null}
            <span>{p.title}</span>
          </span>
        ))}
        <Text
          type="secondary"
          className="tabular-nums"
          aria-hidden="true"
          style={{ fontSize: FONT_SIZE.sm }}
        >
          已耗时 {(elapsed / 1000).toFixed(1)}s
        </Text>
        <Button
          type="link"
          size="small"
          href="#/visualize"
          icon={<ApartmentOutlined />}
          className="phase-details-link"
        >
          查看完整过程
        </Button>
      </Space>
      <span className="sr-only" role="status" aria-live="polite">
        {announcement}
      </span>
      <Progress
        aria-label="回答处理进度"
        percent={percent}
        showInfo={false}
        size="small"
        strokeColor="var(--color-primary)"
      />
    </div>
  );
}
