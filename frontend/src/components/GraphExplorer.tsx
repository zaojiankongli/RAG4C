/* eslint-disable @typescript-eslint/no-explicit-any -- compatibility callback types during TDesign migration */
import { useCallback, useMemo, useRef, useState } from "react";
import { App as UIApp, Button, Input, Space, Spin, Tag, Tooltip, Typography } from "../ui/index";
import { DownloadOutlined, ExpandOutlined, SearchOutlined, UndoOutlined } from "../ui/icons";
import type { EChartsType } from "echarts/core";
import EChart from "../charts/EChart";
import type { EChartsOption } from "../charts/EChart";
import PageState from "./PageState";
import { CHART_FONT, chartPalette } from "../charts/chartTheme";
import { FONT_SIZE } from "../theme/tokens";
import { useIsDark } from "../theme/useIsDark";
import type { GraphEntityNode, GraphRelationEdge, GraphSubgraph } from "../types/rag";

const { Text } = Typography;

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + "…" : s;
}

/** 模块级空数组常量：保持引用稳定，避免 useMemo 依赖抖动 */
const EMPTY_ENTITIES: GraphEntityNode[] = [];
const EMPTY_RELATIONS: GraphRelationEdge[] = [];

interface Props {
  /** 图谱数据（实体 + 关系）；离线时传入演示数据 */
  data: GraphSubgraph | null;
  loading?: boolean;
  /** 搜索图谱（在线时调用桥服务） */
  onSearch?: (query: string) => void;
  /** 点击实体扩展子图（在线时调用桥服务） */
  onExpand?: (entityId: string) => void;
}

/**
 * 图谱浏览器（ECharts 力导向）—— 知识图谱实体-关系图（vector-graph-rag 可视化）。
 * 节点 = 实体（大小按检索分数），边 = 三元组关系（predicate 标签，颜色按置信度）。
 * 支持缩放平移、节点拖拽、邻接高亮、PNG 导出。
 */
export default function GraphExplorer({ data, loading, onSearch, onExpand }: Props) {
  const dark = useIsDark();
  const { message } = UIApp.useApp();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const chartRef = useRef<EChartsType | null>(null);

  const entities = data?.entities ?? EMPTY_ENTITIES;
  const relations = data?.relations ?? EMPTY_RELATIONS;

  const neighbors = useMemo(() => {
    if (!selected) return new Set<string>();
    const set = new Set<string>();
    relations.forEach((r: any) => {
      if (r.entity_ids?.includes(selected)) {
        r.entity_ids.forEach((id: string) => set.add(id));
      }
    });
    return set;
  }, [selected, relations]);

  const selectedEntity = entities.find((e: any) => e.id === selected);

  const option = useMemo<EChartsOption>(() => {
    const pal = chartPalette(dark);

    const nodes = entities.map((e: GraphEntityNode) => {
      const isSel = selected === e.id;
      const isNbr = selected != null && neighbors.has(e.id);
      const score = e.score ?? 0.5;
      const size = 18 + Math.min(18, Math.max(0, score - 0.5) * 70);
      return {
        id: e.id,
        name: e.text,
        value: score,
        symbolSize: isSel ? size + 6 : size,
        itemStyle: {
          color: isSel ? pal.primary : isNbr ? pal.neighborFill : pal.entityFill,
          borderColor: isSel ? pal.primaryStrong : isNbr ? pal.primary : pal.borderStrong,
          borderWidth: isSel ? 2.5 : 1.4,
          shadowBlur: isSel ? 12 : 0,
          shadowColor: pal.primary,
          opacity: selected && !isSel && !isNbr ? 0.35 : 1,
        },
        label: {
          show: true,
          position: "inside" as const,
          formatter: "{n|" + truncate(e.text, 8) + "}\n{s|" + score.toFixed(2) + "}",
          rich: {
            n: {
              fontSize: CHART_FONT.axis,
              color: isSel ? "#ffffff" : pal.text,
              fontWeight: isSel ? 700 : 500,
              lineHeight: 14,
            },
            s: {
              fontSize: CHART_FONT.annotation,
              color: isSel ? "rgba(255,255,255,0.85)" : pal.textSub,
              lineHeight: 11,
            },
          },
        },
      };
    });

    const links = relations
      .filter((r: GraphRelationEdge) => r.entity_ids && r.entity_ids.length >= 2)
      .map((r: GraphRelationEdge) => {
        const aId = r.entity_ids![0];
        const bId = r.entity_ids![1];
        const s = r.score ?? 0;
        const active = selected != null && (aId === selected || bId === selected);
        return {
          source: aId,
          target: bId,
          name: r.predicate ?? "",
          lineStyle: {
            color: active ? pal.warning : s >= 0.8 ? pal.primary : s >= 0.7 ? pal.graph : pal.border,
            width: active ? 2.6 : 1.4,
            opacity: selected && !active ? 0.2 : 0.9,
            curveness: 0.08,
          },
          label: {
            show: r.predicate ? true : false,
            formatter: "{n}",
            fontSize: CHART_FONT.annotation,
            color: pal.textSub,
            backgroundColor: "rgba(0,0,0,0)",
            padding: 0,
          },
        };
      });

    return {
      backgroundColor: pal.bg,
      animationDurationUpdate: 400,
      tooltip: {
        trigger: "item",
        confine: true,
        formatter: (params: unknown) => {
          const p = params as {
            dataType?: string;
            data?: { name?: string; value?: number };
          };
          if (p.dataType === "edge") {
            return p.data?.name ? "关系：" + p.data.name : "关系";
          }
          const d = p.data ?? {};
          return (
            "<b>" + (d.name ?? "") + "</b><br/>与当前搜索的匹配程度：" +
            ((d.value ?? 0) as number).toFixed(3)
          );
        },
      },
      series: [
        {
          type: "graph",
          layout: "force",
          roam: true,
          draggable: true,
          force: {
            repulsion: 320,
            edgeLength: [90, 180],
            gravity: 0.08,
            layoutAnimation: true,
          },
          data: nodes,
          links,
          edgeSymbol: ["none", "arrow"],
          edgeSymbolSize: 7,
          emphasis: {
            focus: "adjacency",
            lineStyle: { width: 2.5 },
          },
        },
      ],
    };
  }, [entities, relations, selected, neighbors, dark]);

  /** 点击节点：选中/取消选中（配合「扩展选中实体」）；useCallback 保持事件绑定稳定 */
  const handleNodeClick = useCallback((params: unknown) => {
    const p = params as { dataType?: string; data?: { id?: string } };
    if (p.dataType === "node" && p.data?.id) {
      const id = p.data.id;
      setSelected((prev) => (prev === id ? null : id));
    }
  }, []);

  const handleExport = () => {
    const chart = chartRef.current;
    if (!chart) return;
    const url = chart.getDataURL({
      type: "png",
      pixelRatio: 2,
      backgroundColor: chartPalette(dark).bg,
    });
    const a = document.createElement("a");
    a.href = url;
    a.download = "rag4c-graph.png";
    a.click();
    message.success("资料关联图已导出 PNG");
  };

  const handleResetView = () => {
    chartRef.current?.setOption(option, { notMerge: true });
  };

  return (
    <div>
      {/* 工具条 */}
      <Space style={{ marginBottom: 10 }} wrap>
        <Input
          placeholder="搜索人物、事项或概念，例如：图灵"
          value={query}
          onChange={(e: any) => setQuery(e.target.value)}
          onPressEnter={() => onSearch?.(query.trim())}
          style={{ width: 240 }}
          prefix={<SearchOutlined />}
          allowClear
          aria-label="搜索资料关联"
        />
        <Button type="primary" onClick={() => onSearch?.(query.trim())} disabled={!query.trim()}>
          查找
        </Button>
        <Tooltip title={selected ? "显示与选中内容直接相关的更多资料" : "先在图中选中一个人物、事项或概念"}>
          <Button
            icon={<ExpandOutlined />}
            onClick={() => selected && onExpand?.(selected)}
            disabled={!selected || !onExpand}
          >
            展开关联信息
          </Button>
        </Tooltip>
        <Button icon={<UndoOutlined />} onClick={handleResetView} disabled={entities.length === 0}>
          重置视图
        </Button>
        <Button icon={<DownloadOutlined />} onClick={handleExport} disabled={entities.length === 0}>
          导出 PNG
        </Button>
        <Text type="secondary" style={{ fontSize: FONT_SIZE.sm }}>
          找到 {entities.length} 个内容 · {relations.length} 条关联
          {selected ? " · 已选中：" + (selectedEntity?.text ?? selected) : ""}
        </Text>
      </Space>

      <Spin spinning={!!loading}>
        {entities.length === 0 ? (
          <PageState
            status="empty"
            title="还没有资料关联"
            description="先完成一次提问，系统会从资料中整理人物、事项和概念之间的联系。"
          />
        ) : (
          <div
            style={{
              border: "1px solid var(--color-border)",
              borderRadius: 10,
              overflow: "hidden",
              background: "var(--color-bg-elevated)",
            }}
          >
            <EChart
              option={option}
              height={520}
              onReady={(c) => {
                chartRef.current = c;
              }}
              onEvents={{ click: handleNodeClick }}
            />
          </div>
        )}
      </Spin>

      {entities.length > 0 ? (
        <div className="graph-entity-picker">
          <div id="graph-entity-picker-label" className="graph-entity-picker-title">
            键盘选择实体
          </div>
          <div className="graph-entity-picker-list" role="group" aria-labelledby="graph-entity-picker-label">
            {entities.map((entity) => (
              <button
                key={entity.id}
                type="button"
                className={`graph-entity-choice${selected === entity.id ? " is-selected" : ""}`}
                aria-pressed={selected === entity.id}
                title={entity.text}
                onClick={() => setSelected((previous) => (previous === entity.id ? null : entity.id))}
              >
                <span>{entity.text}</span>
                <span className="tabular-nums">{(entity.score ?? 0.5).toFixed(2)}</span>
              </button>
            ))}
          </div>
        </div>
      ) : null}

      <Text type="secondary" style={{ fontSize: FONT_SIZE.sm, display: "block", marginTop: 8 }}>
        可用滚轮缩放、拖拽移动，节点也可以拖动。点击一个内容后，可展开它的关联信息。
        连线上的文字说明两者关系，例如「发明了」或「任职于」；颜色越深，表示关系越可信。
      </Text>

      {/* 选中实体详情 */}
      {selectedEntity && (
        <div
          style={{
            marginTop: 10,
            background: "var(--color-primary-bg)",
            border: "1px solid var(--color-primary-border)",
            borderRadius: 8,
            padding: "8px 12px",
          }}
        >
          <Space size={8} wrap>
            <Tag color="geekblue">{selectedEntity.text}</Tag>
            <Text type="secondary" style={{ fontSize: FONT_SIZE.sm }}>
              找到 {(selectedEntity.relation_ids ?? []).length} 条关联 · 出现于 {(selectedEntity.passage_ids ?? []).length} 段资料
            </Text>
          </Space>
        </div>
      )}
    </div>
  );
}

