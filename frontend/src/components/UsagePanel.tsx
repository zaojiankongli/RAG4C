import { Fragment, useMemo } from "react";
import { Tag, Tooltip, Typography } from "../ui/index";
import { WarningOutlined } from "../ui/icons";
import type { QueryUsage } from "../types/rag";
import { FONT_SIZE } from "../theme/tokens";

const { Text } = Typography;

interface Props {
  usage?: QueryUsage;
}

/** 千分位；token 数通常到万级，不加分隔符会读成一堆数字 */
function fmt(n: number): string {
  return n.toLocaleString("zh-CN");
}

/**
 * 单次问答的 LLM 用量与成本（只读展示）。
 *
 * 为什么金额可能显示成"未配置价格"：`cost` 来自后端 `llm.price_table`，
 * 本仓刻意不内置任何价格——价格会变，写死就是在骗人。所以没配价格表时
 * token 照记、金额记 0，并把没定价的 token 如实报在
 * `unpriced_total_tokens` 里（宁可说"算不出钱"，也不编一个数）。
 *
 * 因此这里**不把 cost=0 渲染成"免费"**：那会把"没配价格"误报成"不花钱"。
 */
export default function UsagePanel({ usage }: Props) {
  const rows = useMemo(() => {
    if (!usage) return [];
    return (usage.by_slot ?? []).map((e) => ({
      key: `${e.slot}/${e.model}`,
      slot: e.slot,
      model: e.model,
      calls: e.calls,
      cached: e.cached_calls,
      failures: e.failures,
      total: e.total_tokens,
      saved: e.saved_total_tokens,
      cost: e.cost,
      unpriced: e.unpriced_total_tokens,
    }));
  }, [usage]);

  // 台账没开时后端给空对象：什么都不展示，而不是展示一排 0
  if (!usage || usage.calls === 0) return null;

  const priced = usage.cost_priced;
  const costText = priced ? usage.cost.toFixed(4) : "未配置价格表";

  return (
    <div data-testid="usage-panel">
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: rows.length > 0 ? 8 : 0 }}>
        <Metric label="调用" value={`${fmt(usage.calls)} 次`} />
        <Metric
          label="Token"
          value={`${fmt(usage.total_tokens)}`}
          hint={
            usage.cached_calls > 0
              ? `其中缓存命中 ${fmt(usage.cached_calls)} 次`
              : undefined
          }
        />
        {usage.saved_total_tokens > 0 && (
          <Metric
            label="缓存节省"
            value={`${fmt(usage.saved_total_tokens)}`}
            hint="命中缓存而未实际计费的 token"
          />
        )}
        <Metric
          label="估算成本"
          value={costText}
          hint={priced ? "按 llm.price_table 估算" : "台账照记 token，金额不编造"}
        />
        {usage.failures > 0 && (
          <Metric label="失败调用" value={`${fmt(usage.failures)} 次`} tone="warning" />
        )}
      </div>

      {!priced && usage.unpriced_total_tokens > 0 && (
        <div style={{ marginBottom: 8 }}>
          <Tag color="warning" icon={<WarningOutlined />}>
            有 {fmt(usage.unpriced_total_tokens)} token 未定价，成本被记为 0
          </Tag>
        </div>
      )}

      {rows.length > 0 && (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: FONT_SIZE.sm }}>
          <thead>
            <tr style={{ textAlign: "left", color: "var(--td-text-color-secondary)" }}>
              <th style={{ padding: "4px 8px 4px 0", fontWeight: 400 }}>槽位</th>
              <th style={{ padding: "4px 8px", fontWeight: 400 }}>模型</th>
              <th style={{ padding: "4px 8px", fontWeight: 400, textAlign: "right" }}>调用</th>
              <th style={{ padding: "4px 8px", fontWeight: 400, textAlign: "right" }}>Token</th>
              <th style={{ padding: "4px 0 4px 8px", fontWeight: 400, textAlign: "right" }}>成本</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <Fragment key={r.key}>
                <tr style={{ borderTop: "1px solid var(--td-component-border)" }}>
                  <td style={{ padding: "4px 8px 4px 0" }}>{r.slot}</td>
                  <td style={{ padding: "4px 8px" }}>
                    <Text type="secondary">{r.model}</Text>
                  </td>
                  <td style={{ padding: "4px 8px", textAlign: "right" }}>
                    {fmt(r.calls)}
                    {r.cached > 0 && (
                      <Text type="secondary">（缓存 {fmt(r.cached)}）</Text>
                    )}
                    {r.failures > 0 && (
                      <Text type="warning">（失败 {fmt(r.failures)}）</Text>
                    )}
                  </td>
                  <td style={{ padding: "4px 8px", textAlign: "right" }}>{fmt(r.total)}</td>
                  <td style={{ padding: "4px 0 4px 8px", textAlign: "right" }}>
                    {priced ? r.cost.toFixed(4) : "—"}
                  </td>
                </tr>
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Metric({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "warning";
}) {
  const body = (
    <div>
      <div style={{ fontSize: FONT_SIZE.sm, color: "var(--td-text-color-secondary)" }}>
        {label}
      </div>
      <div
        style={{
          fontSize: FONT_SIZE.md,
          color: tone === "warning" ? "var(--td-error-color)" : undefined,
        }}
      >
        {value}
      </div>
    </div>
  );
  return hint ? <Tooltip content={hint}>{body}</Tooltip> : body;
}
