import { useId, useState, type ReactNode } from "react";
import { Card, Tooltip } from "tdesign-react";

export type MetricStripTone = "primary" | "success" | "warning" | "danger" | "neutral";

export interface MetricStripItem {
  id: string;
  label: ReactNode;
  value: ReactNode;
  unit?: ReactNode;
  delta?: ReactNode;
  tone?: MetricStripTone;
  hint?: string;
  icon?: ReactNode;
}

export interface MetricStripProps {
  metrics: MetricStripItem[];
  ariaLabel?: string;
  columns?: 2 | 3 | 4 | 5 | 6;
}

function MetricLabel({ label, hint }: Pick<MetricStripItem, "label" | "hint">) {
  const [visible, setVisible] = useState(false);
  const tooltipId = useId();
  const labelNode = <span className="enterprise-metric-strip__label">{label}</span>;

  if (!hint) return labelNode;

  return (
    <Tooltip
      content={<span id={tooltipId} role="tooltip">{hint}</span>}
      visible={visible}
      trigger="hover"
      delay={0}
      destroyOnClose
      onVisibleChange={setVisible}
    >
      <span
        className="enterprise-metric-strip__hint"
        tabIndex={0}
        aria-describedby={visible ? tooltipId : undefined}
        onMouseEnter={() => setVisible(true)}
        onMouseLeave={() => setVisible(false)}
        onFocus={() => setVisible(true)}
        onBlur={() => setVisible(false)}
      >
        {labelNode}
      </span>
    </Tooltip>
  );
}

/** 紧凑、高密度的企业指标带，用于页面首屏概览而非营销式大数字卡片。 */
export default function MetricStrip({
  metrics,
  ariaLabel = "核心运营指标",
  columns = 4,
}: MetricStripProps) {
  return (
    <section
      className="enterprise-metric-strip"
      role="region"
      aria-label={ariaLabel}
      data-columns={columns}
      style={{ "--enterprise-metric-columns": columns } as React.CSSProperties}
    >
      {metrics.map((metric) => {
        const tone = metric.tone ?? "neutral";
        return (
          <Card
            key={metric.id}
            className={`enterprise-metric-strip__item is-${tone}`}
            bordered
            size="small"
          >
            <dl>
              <dt>
                {metric.icon ? (
                  <span className="enterprise-metric-strip__icon" aria-hidden="true">
                    {metric.icon}
                  </span>
                ) : null}
                <MetricLabel label={metric.label} hint={metric.hint} />
              </dt>
              <dd>
                <span className="enterprise-metric-strip__value">{metric.value}</span>
                {metric.unit !== undefined ? (
                  <span className="enterprise-metric-strip__unit">{metric.unit}</span>
                ) : null}
              </dd>
              {metric.delta !== undefined ? (
                <dd className="enterprise-metric-strip__delta">{metric.delta}</dd>
              ) : null}
            </dl>
          </Card>
        );
      })}
    </section>
  );
}
