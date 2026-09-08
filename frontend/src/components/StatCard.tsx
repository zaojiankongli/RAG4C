import { useId, useState, type ReactNode } from "react";
import { Card, Tooltip } from "tdesign-react";

export type StatTone = "primary" | "success" | "warning" | "danger" | "graph";

interface Props {
  /** 指标名 */
  label: ReactNode;
  /** 主数值 */
  value: ReactNode;
  /** 单位或分母，例如 `ms` / `/ 最多 64 条`；渲染成 .stat-unit */
  unit?: ReactNode;
  /** 数值前的内联图标（监控页样式） */
  icon?: ReactNode;
  /** 指标名前的圆角图标徽标（文档页样式）；与 icon 互不冲突 */
  labelIcon?: ReactNode;
  /** 图标色调 */
  tone?: StatTone;
  /** 悬停说明；有它时指标名可聚焦，键盘用户也能看到 */
  hint?: string;
}

/**
 * RAG4C 领域指标卡。
 *
 * 外壳、悬浮反馈使用 TDesign 原生组件，指标名、徽标、数值和单位仍由
 * RAG4C 自己定义，避免把知识运营语义退化为通用 Statistic 模板。
 */
export default function StatCard({
  label,
  value,
  unit,
  icon,
  labelIcon,
  tone = "primary",
  hint,
}: Props) {
  const [hintVisible, setHintVisible] = useState(false);
  const hintId = useId();
  const labelNode = (
    <div className="stat-label" style={hint ? { cursor: "help" } : undefined}>
      {labelIcon && <span className={`stat-icon is-${tone}`}>{labelIcon}</span>}
      {label}
    </div>
  );

  const labelWithHint = hint ? (
    <Tooltip
      content={<span id={hintId} role="tooltip">{hint}</span>}
      visible={hintVisible}
      trigger="hover"
      delay={0}
      destroyOnClose
      onVisibleChange={setHintVisible}
    >
      <span
        className="stat-hint-trigger"
        tabIndex={0}
        aria-describedby={hintVisible ? hintId : undefined}
        onMouseEnter={() => setHintVisible(true)}
        onMouseLeave={() => setHintVisible(false)}
        onFocus={() => setHintVisible(true)}
        onBlur={() => setHintVisible(false)}
      >
        {labelNode}
      </span>
    </Tooltip>
  ) : (
    labelNode
  );

  return (
    <Card size="small" className="stat-card" bordered hoverShadow>
      {labelWithHint}
      <div className="stat-value">
        {icon && <span className={`stat-value-icon is-${tone}`}>{icon}</span>}
        {value}
        {unit !== undefined && <span className="stat-unit">{unit}</span>}
      </div>
    </Card>
  );
}
