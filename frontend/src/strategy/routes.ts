/**
 * 查询路由（route）标识 -> 展示文案与标签配色 —— 全站唯一来源。
 *
 * 缺陷记录：这份映射此前存在三份互相分叉的副本 ——
 *   - AnswerCard 一份技术口径（「知识图谱 检索」）；
 *   - VisualizePage 一份面向使用者的白话口径（「同时参考关联资料」）；
 *   - MonitorPage「最近的提问记录」表**一份都没有**，直接把后端内部枚举
 *     `vector_graph_rag` 原样渲染给用户看。
 * 同一次提问在三个页面能显示出三种说法，其中一种还是英文标识符。
 *
 * 统一采用白话口径：这套控制台的文案基调是给业务方看的（「资料不足」
 * 「查找方式」），技术标识只保留在指标明细这类排查视图里。
 * 后端新增路由时只需在这里补一行。
 */

/** route 值取自后端 rag 管线的路由决策（hybrid / vector_graph_rag / full / ...） */
export const ROUTE_LABELS: Record<string, string> = {
  hybrid: "关键词和含义一起找",
  vector: "按含义查找",
  vector_graph_rag: "同时参考关联资料",
  graph: "查看资料关联",
  full: "综合查找资料",
  rewrite: "换种问法再找",
};

/** 未知路由原样回显（比显示「未知」更利于排查后端新增了什么） */
export function routeLabel(route?: string | null): string {
  if (!route) return "—";
  return ROUTE_LABELS[route] ?? route;
}

/** 标签配色：图谱路径橙、全链路紫、其余蓝，三处列表保持一致 */
export function routeTagColor(route?: string | null): string {
  if (route === "vector_graph_rag") return "orange";
  if (route === "full") return "purple";
  return "geekblue";
}
