import { useEffect, useMemo, useRef, useState } from "react";
import { App as UIApp, Button, Space, Tag, Typography } from "../ui/index";
import { CaretRightOutlined, DownloadOutlined, ReloadOutlined } from "../ui/icons";
import type { EChartsType } from "echarts/core";
import EChart from "../charts/EChart";
import type { EChartsOption } from "../charts/EChart";
import PageState from "./PageState";
import { CHART_FONT, useChartPalette } from "../charts/chartTheme";
import { FONT_SIZE } from "../theme/tokens";

const { Text } = Typography;

/** 主链节点（hybrid 路径） */
const MAIN_STEPS = [
  { key: "gate", label: "判断问题", sub: "简单问题直接处理" },
  { key: "rewrite", label: "换种问法", sub: "让资料更容易匹配" },
  { key: "route", label: "理解问题", sub: "判断需要哪些资料" },
  { key: "embed", label: "理解关键词", sub: "识别问题和资料的含义" },
  { key: "search", label: "查找资料", sub: "按关键词和含义一起找" },
  { key: "diversity", label: "补充不同资料", sub: "避免信息过于重复" },
  { key: "rerank", label: "挑选相关内容", sub: "优先保留最贴近问题的资料" },
  { key: "generate", label: "整理回答", sub: "在答案中标出资料来源" },
  { key: "verify", label: "核对依据", sub: "确认答案有资料支持" },
  { key: "abstain", label: "判断资料是否足够", sub: "不够时会如实说明" },
];

/** 图谱分支节点（vector_graph_rag / full 路径） */
const GRAPH_STEPS = [
  { key: "graph-entity", label: "找关键人物和事项", sub: "从资料中找相关对象" },
  { key: "graph-expand", label: "展开关联", sub: "补充直接相关的信息" },
  { key: "graph-relation", label: "查看相互关系", sub: "寻找人物和事项的联系" },
  { key: "graph-rerank", label: "挑选相关关联", sub: "保留与问题最有关的关系" },
  { key: "graph-merge", label: "合并参考资料", sub: "和其他资料一起整理" },
];

const STEP_W = 130;
const STEP_H = 60;
const GAP_X = 34;
const GRAPH_W = 118;
const GRAPH_GAP = 20;
const MAIN_Y = 70;
const GRAPH_Y = 220;

const mainX = (i: number) => 40 + i * (STEP_W + GAP_X) + STEP_W / 2;
const graphX = (i: number) => 40 + i * (GRAPH_W + GRAPH_GAP) + GRAPH_W / 2;

interface Props {
  /** 当前查询路由：hybrid / vector_graph_rag / full */
  route?: string;
  /** 该查询的 traces（用于回放逐节点点亮） */
  traces?: string[];
}

const TRACE_KEYWORDS: Record<string, RegExp> = {
  gate: /\[gate\]|gate:|门控/i,
  rewrite: /\[rewrite\]|rewrite:|改写/i,
  route: /\[router\]|\[route\]|route:|路由/i,
  embed: /\[embed\]|embed:|嵌入/i,
  search: /\[retrieval\]|\[search\]|search:|检索/i,
  diversity: /\[diversity\]|diversity|多样性/i,
  "graph-entity": /实体检索|entity/i,
  "graph-expand": /子图扩展|扩展/i,
  "graph-relation": /关系检索|relation/i,
  "graph-rerank": /LLM 重排|重排完成/i,
  "graph-merge": /合并.*passage|passage/i,
  rerank: /\[rerank\]|rerank:|重排/i,
  generate: /\[generation\]|generation|生成/i,
  verify: /\[verify|\[L1|\[L2|\[L3|验证/i,
  abstain: /\[abstention\]|abstention|弃权/i,
};

/**
 * RAG 链路流程图（ECharts）—— hybrid vs vector_graph_rag 双路径可视化。
 * - 主链横向 10 节点；图谱分支（实体检索→子图扩展→关系检索→LLM 重排→合并）
 *   以旁路形式挂在 route 与 rerank 之间，仅 vector_graph_rag / full 路由启用。
 * - traces 回放逐节点点亮；支持缩放平移（roam）、tooltip、PNG 导出。
 */
export default function PipelineFlow({ route, traces }: Props) {
  const { message } = UIApp.useApp();
  const [played, setPlayed] = useState<number>(-1);
  const [playing, setPlaying] = useState(false);
  const chartRef = useRef<EChartsType | null>(null);

  const graphOn = route === "vector_graph_rag" || route === "full";
  const hasTraces = !!traces && traces.length > 0;
  const pal = useChartPalette();

  // 回放：按 trace 关键词匹配节点，每 500ms 点亮一个
  useEffect(() => {
    if (!playing) return;
    if (!traces || traces.length === 0) {
      setPlaying(false);
      return;
    }
    setPlayed(-1);
    const timer = setInterval(() => {
      setPlayed((p) => {
        const next = p + 1;
        if (next >= traces.length) {
          clearInterval(timer);
          setPlaying(false);
        }
        return next;
      });
    }, 480);
    return () => clearInterval(timer);
  }, [playing, traces]);

  const playedTraces = useMemo(() => {
    if (played < 0 || !traces) return new Set<string>();
    const set = new Set<string>();
    for (let i = 0; i <= played && i < traces.length; i++) {
      for (const [key, re] of Object.entries(TRACE_KEYWORDS)) {
        if (re.test(traces[i])) set.add(key);
      }
    }
    return set;
  }, [played, traces]);

  const option = useMemo<EChartsOption>(() => {
    const nodeState = (key: string, isGraph: boolean): "idle" | "active" | "done" => {
      if (playedTraces.has(key)) return "done";
      if (played < 0 && route) {
        if (isGraph) return graphOn ? "active" : "idle";
        if (graphOn && key === "route") return "active";
        if (!graphOn && (key === "route" || key === "search" || key === "rerank")) {
          return "active";
        }
      }
      return "idle";
    };

    type NodeDef = {
      id: string;
      name: string;
      sub: string;
      kind: "main" | "graph";
      x: number;
      y: number;
    };
    const nodes: NodeDef[] = [];
    const links: { source: string; target: string }[] = [];

    MAIN_STEPS.forEach((s, i) => {
      nodes.push({ id: s.key, name: s.label, sub: s.sub, kind: "main", x: mainX(i), y: MAIN_Y });
      if (i > 0) links.push({ source: MAIN_STEPS[i - 1].key, target: s.key });
    });
    if (graphOn) {
      GRAPH_STEPS.forEach((s, i) => {
        nodes.push({
          id: s.key,
          name: s.label,
          sub: s.sub,
          kind: "graph",
          x: graphX(i),
          y: GRAPH_Y,
        });
        if (i > 0) links.push({ source: GRAPH_STEPS[i - 1].key, target: s.key });
      });
      links.push({ source: "route", target: "graph-entity" });
      links.push({ source: "graph-merge", target: "rerank" });
    }

    const data = nodes.map((n) => {
      const st = nodeState(n.id, n.kind === "graph");
      const c =
        st === "done"
          ? pal.done
          : st === "active"
            ? n.kind === "graph"
              ? pal.graphActive
              : pal.active
            : n.kind === "graph"
              ? pal.graphNode
              : pal.node;
      return {
        id: n.id,
        name: n.name,
        sub: n.sub,
        x: n.x,
        y: n.y,
        symbol: "rect",
        symbolSize: n.kind === "graph" ? [GRAPH_W, STEP_H] : [STEP_W, STEP_H],
        itemStyle: {
          color: c.fill,
          borderColor: c.stroke,
          borderWidth: 1.6,
          borderRadius: 10,
        },
        label: {
          formatter: "{name|" + n.name + "}\n{sub|" + n.sub + "}",
          rich: {
            name: {
              fontSize: CHART_FONT.nodeTitle,
              fontWeight: 600,
              color: c.text,
              align: "center" as const,
              lineHeight: 18,
            },
            sub: {
              fontSize: CHART_FONT.dataLabel,
              color: pal.node.sub,
              align: "center" as const,
              lineHeight: 14,
            },
          },
        },
      };
    });

    return {
      backgroundColor: pal.bgSunken,
      animationDurationUpdate: 500,
      tooltip: {
        trigger: "item",
        confine: true,
        formatter: (params: unknown) => {
          const p = params as { dataType?: string; data?: { name?: string; sub?: string } };
          if (p.dataType === "edge") return "";
          const d = p.data ?? {};
          return "<b>" + (d.name ?? "") + "</b><br/>" + (d.sub ?? "");
        },
      },
      series: [
        {
          type: "graph",
          layout: "none",
          roam: true,
          scaleLimit: { min: 0.5, max: 2 },
          data,
          links,
          edgeSymbol: ["none", "arrow"],
          edgeSymbolSize: 9,
          lineStyle: { color: pal.arrow, width: 1.6 },
          emphasis: {
            focus: "adjacency",
            itemStyle: {
              shadowBlur: 6,
              shadowOffsetY: 2,
              shadowColor: pal.shadow,
            },
          },
        },
      ],
    };
  }, [graphOn, pal, played, playedTraces, route]);

  const handleExport = () => {
    const chart = chartRef.current;
    if (!chart) return;
    const url = chart.getDataURL({
      type: "png",
      pixelRatio: 2,
      backgroundColor: pal.bgSunken,
    });
    const a = document.createElement("a");
    a.href = url;
    a.download = "rag4c-pipeline.png";
    a.click();
    message.success("回答过程图已导出 PNG");
  };

  return (
    <div>
      {/* 工具条 */}
      <Space style={{ marginBottom: 12 }} wrap>
        <Tag color={graphOn ? "orange" : "geekblue"}>
          {graphOn ? "本次也参考了资料关联" : "本次按资料内容查找"}
        </Tag>
        <Button
          size="small"
          icon={<CaretRightOutlined />}
          onClick={() => setPlaying((p) => !p)}
          disabled={!traces || traces.length === 0}
        >
          {playing ? "暂停" : "回放这次过程"}
        </Button>
        <Button
          size="small"
          icon={<ReloadOutlined />}
          onClick={() => {
            setPlayed(-1);
            setPlaying(false);
          }}
        >
          重置
        </Button>
        <Button
          size="small"
          icon={<DownloadOutlined />}
          onClick={handleExport}
          disabled={!hasTraces}
        >
          导出 PNG
        </Button>
        <Text type="secondary" style={{ fontSize: FONT_SIZE.sm }}>
          {traces?.length ? "共 " + traces.length + " 个处理步骤" : "先选择一条已经完成的提问"}
        </Text>
      </Space>

      {hasTraces ? (
        <div
          // canvas 对读屏是一块空白：用 role=img + 一句话说清这张图画了什么，
          // 否则整个流程图在无障碍树里等于不存在
          role="img"
          aria-label={
            "回答过程流程图：主链 " +
            MAIN_STEPS.length +
            " 个步骤" +
            (graphOn ? "，并额外经过 " + GRAPH_STEPS.length + " 个资料关联步骤" : "")
          }
          style={{
            border: "1px solid var(--color-border)",
            borderRadius: 10,
            padding: 8,
            background: "var(--color-bg-sunken)",
          }}
        >
          <EChart
            option={option}
            height={graphOn ? 330 : 190}
            onReady={(c) => {
              chartRef.current = c;
            }}
          />
        </div>
      ) : (
        <PageState
          compact
          status="empty"
          title="还没有选中提问"
          description="选择一条最近的提问，即可查看系统如何处理它"
        />
      )}

      {/* 回放进度只在画布里体现，读屏用户听不到；这里用文字实况同步一份 */}
      <span className="sr-only" role="status" aria-live="polite">
        {playing && traces
          ? "正在回放第 " + (played + 1) + " / " + traces.length + " 步"
          : ""}
      </span>

      {/* 图例 + 说明（无轨迹时隐藏，避免空状态旁出现孤立图例） */}
      {hasTraces && (
        <>
          <Space
            size={16}
            wrap
            style={{ marginTop: 8, fontSize: FONT_SIZE.sm, color: "var(--color-text-secondary)" }}
          >
            <span>
              <i
                className="legend-swatch"
                style={{ background: pal.node.fill, borderColor: pal.node.stroke }}
              />
              尚未走到
            </span>
            <span>
              <i
                className="legend-swatch"
                style={{ background: pal.active.fill, borderColor: pal.active.stroke }}
              />
              本次使用
            </span>
            <span>
              <i
                className="legend-swatch"
                style={{ background: pal.done.fill, borderColor: pal.done.stroke }}
              />
              回放已完成
            </span>
            {graphOn && (
              <span>
                <i
                  className="legend-swatch"
                  style={{ background: pal.graphNode.fill, borderColor: pal.graphNode.stroke }}
                />
                参考资料关联
              </span>
            )}
          </Space>

          <Text type="secondary" style={{ fontSize: FONT_SIZE.sm, display: "block", marginTop: 8 }}>
            系统会在需要理解人物、事项或概念关系时，额外参考关联资料；找不到合适关联时，会继续使用普通资料查找。
            可用滚轮缩放、拖拽平移；点击「回放这次过程」可按步骤查看一次提问如何得到回答。
          </Text>
        </>
      )}
    </div>
  );
}
