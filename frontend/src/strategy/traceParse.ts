/**
 * 检索链路 traces 解析器 —— 把后端返回的字符串数组还原成结构化过程。
 *
 * 后端的 `QueryResult.traces` 混了两类内容：
 * - **阶段耗时**：`"search:123.45ms"`（`RetrievalPipeline._add_span` 与
 *   `TraceContext.as_list()` 产出，格式固定）
 * - **过程说明**：自由文本，多为降级原因（如 `"graph 无命中，降级 hybrid"`）
 *
 * 三个必须处理的坑（均来自后端实现，不是猜测）：
 *
 * 1. **阶段耗时会重复出现**。检索管线既往自己的 traces 列表里追加一份，
 *    `trace.as_list()` 又带一份；二轮检索时还会各再来一次。因此这里先按
 *    **完整字符串**去重——单轮时两份完全相同会被去成一条；二轮时耗时不同
 *    会被保留，再按阶段名累加，得到该阶段的总耗时。
 * 2. **`verify` 是父级 span**，包住 `verify_l2` / `verify_l3`。三者都计入
 *    总耗时会重复计算，所以父级进耗时条、子级只作明细。
 * 3. **部分 span 无条件记录**（`graph` / `rerank` / `diversity` 等，关闭时
 *    也记 0ms），因此不能用「span 是否存在」判断策略是否生效，必须看降级
 *    说明。可选策略（hyde / subqueries / stepback / sentence_window）的
 *    span 才是条件记录的。
 */

/** 阶段耗时 */
export interface TraceStage {
  key: string;
  label: string;
  ms: number;
  /** 子阶段（如 verify 下的 L2 / L3），不计入总耗时 */
  children?: TraceStage[];
}

export type StrategyState = "active" | "degraded" | "off";

/** 某个可选策略在本次查询里的实际状态 */
export interface StrategyStatus {
  key: string;
  label: string;
  state: StrategyState;
  /** 降级 / 关闭的具体原因（取自 traces 里的说明） */
  reason?: string;
}

export interface ParsedTrace {
  stages: TraceStage[];
  totalMs: number;
  strategies: StrategyStatus[];
  /** 未被识别为阶段耗时的过程说明（已去重） */
  notes: string[];
  /** 是否执行过二轮检索补救 */
  retried: boolean;
}

/** `name:12.34ms` */
const SPAN_RE = /^([A-Za-z_][A-Za-z0-9_.]*):([\d.]+)ms$/;

/** 阶段名 -> 中文标签（未知阶段回退原名） */
const STAGE_LABELS: Record<string, string> = {
  gate: "复杂度门控",
  rewrite: "查询改写",
  route: "意图路由",
  hyde: "假设文档生成",
  embed: "查询向量化",
  search: "混合检索",
  subqueries: "子查询扇出",
  stepback: "后退式提问",
  diversity: "来源多样性",
  graph: "图谱检索",
  rerank: "结果精排",
  sentence_window: "父块回取",
  generate: "答案生成",
  verify: "引用验证",
  verify_l2: "文本哈希校验",
  verify_l3: "蕴含判定",
};

/** 阶段展示顺序（与管线执行顺序一致；未列出的排在最后） */
const STAGE_ORDER = [
  "gate",
  "rewrite",
  "route",
  "hyde",
  "embed",
  "search",
  "subqueries",
  "stepback",
  "diversity",
  "graph",
  "rerank",
  "sentence_window",
  "generate",
  "verify",
];

/** verify 的子阶段：计入明细但不计入总耗时（避免与父级重复） */
const CHILD_STAGES: Record<string, string> = {
  verify_l2: "verify",
  verify_l3: "verify",
};

interface StrategyRule {
  key: string;
  label: string;
  /** 该策略的条件性 span 名；无则说明 span 是无条件记录的 */
  span?: string;
  /** 命中任一即视为「已生效」（用于无条件 span 的策略） */
  activeNotes?: RegExp;
  /** 命中任一即视为「降级」 */
  degradedNotes?: RegExp;
  /** 命中任一即视为「已关闭」 */
  offNotes?: RegExp;
}

const STRATEGY_RULES: StrategyRule[] = [
  {
    key: "hybrid_search",
    label: "混合检索",
    offNotes: /hybrid_search 关闭/,
  },
  {
    key: "hyde",
    label: "假设文档检索",
    span: "hyde",
    degradedNotes: /hyde 生成失败|hyde 未产出/,
  },
  {
    key: "subqueries",
    label: "子查询拆解",
    span: "subqueries",
    degradedNotes: /subqueries .*(失败|回退|不符)/,
  },
  {
    key: "stepback",
    label: "后退式提问",
    span: "stepback",
    degradedNotes: /stepback .*(失败|跳过)/,
  },
  {
    key: "graph",
    label: "图谱检索",
    activeNotes: /graph 分支合并/,
    // 注意顺序：后端「开关关闭」那句里也带「降级」二字
    // （"graph 开关关闭或未注入图检索器，降级 hybrid"），
    // 若不先匹配 offNotes，会被误判成「已降级」——那是"尝试过但失败了"
    // 的意思，与"压根没启用"完全是两回事。
    offNotes: /graph 开关关闭|未注入图检索器/,
    degradedNotes: /graph .*(降级|失败|无命中)/,
  },
  {
    key: "rerank",
    label: "结果精排",
    offNotes: /rerank 关闭/,
    degradedNotes: /rerank 失败/,
  },
  {
    key: "sentence_window",
    label: "父块回取",
    span: "sentence_window",
    degradedNotes: /sentence_window .*(失败|异常)/,
  },
];

/** 从说明列表里找出第一条命中正则的，作为原因展示；命中的记入 consumed */
function findNote(
  notes: string[],
  re: RegExp | undefined,
  consumed: Set<string>,
): string | undefined {
  if (!re) return undefined;
  const hit = notes.find((n) => re.test(n));
  if (hit !== undefined) consumed.add(hit);
  return hit;
}

/**
 * 解析 traces。
 *
 * @param traces 后端 `QueryResult.traces`
 */
export function parseTraces(traces: string[]): ParsedTrace {
  // 坑 1：先按完整字符串去重（同一轮的重复份会被消掉，不同轮的保留）
  const unique = Array.from(new Set(traces ?? []));

  const msByStage = new Map<string, number>();
  const notes: string[] = [];

  for (const line of unique) {
    const m = SPAN_RE.exec(line.trim());
    if (m) {
      const key = m[1];
      const ms = Number(m[2]);
      if (Number.isFinite(ms)) {
        msByStage.set(key, (msByStage.get(key) ?? 0) + ms);
      }
      continue;
    }
    notes.push(line);
  }

  // 组装阶段列表：父级进主列表，子级挂到父级下（坑 2）
  const childrenOf = new Map<string, TraceStage[]>();
  for (const [key, parent] of Object.entries(CHILD_STAGES)) {
    const ms = msByStage.get(key);
    if (ms === undefined) continue;
    const list = childrenOf.get(parent) ?? [];
    list.push({ key, label: STAGE_LABELS[key] ?? key, ms });
    childrenOf.set(parent, list);
  }

  const stages: TraceStage[] = [];
  for (const [key, ms] of msByStage) {
    if (key in CHILD_STAGES) continue;
    stages.push({
      key,
      label: STAGE_LABELS[key] ?? key,
      ms,
      children: childrenOf.get(key),
    });
  }
  stages.sort((a, b) => {
    const ia = STAGE_ORDER.indexOf(a.key);
    const ib = STAGE_ORDER.indexOf(b.key);
    return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
  });

  const totalMs = stages.reduce((sum, s) => sum + s.ms, 0);

  // 策略状态（坑 3：无条件 span 的策略靠说明判断）
  // consumed 记录已被策略徽章展示过的说明，避免同一条在下方「过程说明」里重复出现
  const consumed = new Set<string>();
  const strategies: StrategyStatus[] = [];
  for (const rule of STRATEGY_RULES) {
    const offReason = findNote(notes, rule.offNotes, consumed);
    if (offReason) {
      strategies.push({ key: rule.key, label: rule.label, state: "off", reason: offReason });
      continue;
    }
    const degradedReason = findNote(notes, rule.degradedNotes, consumed);
    if (degradedReason) {
      strategies.push({
        key: rule.key,
        label: rule.label,
        state: "degraded",
        reason: degradedReason,
      });
      continue;
    }
    const activeReason = findNote(notes, rule.activeNotes, consumed);
    if (activeReason) {
      strategies.push({
        key: rule.key,
        label: rule.label,
        state: "active",
        reason: activeReason,
      });
      continue;
    }
    // 条件性 span 存在 = 该策略这次真的跑了
    if (rule.span && msByStage.has(rule.span)) {
      strategies.push({ key: rule.key, label: rule.label, state: "active" });
      continue;
    }
    // 无条件 span 的策略（混合检索 / 精排）默认视为生效；
    // 条件性 span 缺失则说明这次没启用，不展示以免噪声。
    if (!rule.span) {
      strategies.push({ key: rule.key, label: rule.label, state: "active" });
    }
  }

  return {
    stages,
    totalMs,
    strategies,
    // 只保留没被策略徽章消费掉的，名副其实的「其余说明」
    notes: notes.filter((n) => !consumed.has(n)),
    // 只认「已执行」那条：另外两条 "二轮检索失败…" / "二轮检索无新增证据…"
    // 说的恰恰是补救没成功，不该显示为「含二轮检索」
    retried: notes.some((n) => n.includes("二轮检索已执行")),
  };
}

/** 耗时着色：让最慢的阶段一眼可辨 */
export function stageColor(key: string): string {
  switch (key) {
    case "generate":
      return "var(--color-graph)";
    case "verify":
      return "var(--color-warning)";
    case "search":
    case "subqueries":
    case "stepback":
      return "var(--color-primary)";
    case "rerank":
      return "var(--color-secondary)";
    default:
      return "var(--color-border-strong)";
  }
}
