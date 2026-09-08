/**
 * RAG 策略编排规格 —— 把后端的可插拔策略体系声明成数据。
 *
 * 依据：`docs/RAG策略矩阵.md`（由通读后端源码得出）。该文档记录了每个
 * 策略的执行阶段、配置开关、互斥/共存关系与前置依赖；本文件是它在前端
 * 的可执行表达，供设置页渲染与校验使用。
 *
 * 三条建模规则：
 * - **互斥**（同一维度只能选一个）-> `kind: "choice"`，渲染为单选；
 * - **可共存**（彼此独立）-> `kind: "switch"`，渲染为开关；
 * - **依赖 / 冲突** -> `requires` / `conflicts`，在界面上显式提示，
 *   前者禁用控件（开了也没用），后者只告警（能开，但会互相削弱）。
 *
 * 修改后端策略后，请同步本文件与上述文档。
 */

/** 读取某个配置项的当前值（已合并未保存的编辑） */
export type ValueGetter = (path: string) => string;

export type StrategyKind = "switch" | "choice" | "number";

/** 开销标记：让「这个开关要花多少钱」在界面上一眼可见 */
export type StrategyCost = "none" | "llm" | "llm-heavy";

export interface StrategyOption {
  value: string;
  label: string;
  /** 选中该项时展示的补充说明 */
  desc?: string;
}

export interface StrategyRule {
  /** 条件成立 = 满足要求 / 存在冲突 */
  when: (get: ValueGetter) => boolean;
  hint: string;
}

export interface StrategyItem {
  /** 后端配置路径，如 pipeline.hyde_on */
  path: string;
  label: string;
  desc: string;
  kind: StrategyKind;
  cost?: StrategyCost;
  /** kind=choice 时的可选值（互斥） */
  options?: StrategyOption[];
  /** kind=number 时的取值范围与单位 */
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  /**
   * 前置依赖：`when` 不成立时该策略开了也不会生效。
   * 界面上禁用控件并展示 hint。
   */
  requires?: StrategyRule;
  /**
   * 冲突：`when` 成立时两个策略会互相削弱。
   * 界面上照常可操作，但展示警告 hint。
   */
  conflicts?: StrategyRule;
  /**
   * 仅在 `when` 成立时才有实际意义（如 MMR 参数只在选了 group_mmr 时生效）。
   * 不成立时控件灰显但不禁用。
   */
  activeWhen?: StrategyRule;
}

export interface StrategyStage {
  key: string;
  label: string;
  /** 该阶段在管线中的一句话定位 */
  desc: string;
  groups: { key: string; label: string; items: StrategyItem[] }[];
}

// ---------------------------------------------------------------------------
// 取值助手
// ---------------------------------------------------------------------------

const isOn = (get: ValueGetter, path: string): boolean => get(path) === "true";

// ---------------------------------------------------------------------------
// 阶段定义（顺序即管线执行顺序）
// ---------------------------------------------------------------------------

export const STRATEGY_STAGES: StrategyStage[] = [
  {
    key: "ingest",
    label: "入库阶段",
    desc: "文档如何被解析、切分并写入向量库。改动只影响此后新入库或重新索引的文档。",
    groups: [
      {
        key: "chunking",
        label: "文档切分",
        items: [
          {
            path: "pipeline.chunking_mode",
            label: "切分方式",
            desc: "长文档需切成片段才能检索。不同方式决定片段的边界与是否保留父子结构。",
            kind: "choice",
            options: [
              { value: "auto", label: "自动", desc: "表格类走逐行；短文档按固定长度；其余按章节结构" },
              { value: "recursive", label: "固定长度", desc: "按字符数递归切分，扁平片段，无父子关系" },
              { value: "parent_child", label: "章节结构", desc: "按标题层级切分，生成父块与子块，支持父块回取" },
              { value: "qa", label: "表格逐行", desc: "表格每行转成一条问答，适合结构化数据" },
            ],
          },
          {
            path: "pipeline.simple_doc_max_chars",
            label: "短文档阈值",
            desc: "自动方式下，低于该字数且无版面结构的文档按固定长度切分。",
            kind: "number",
            min: 100,
            max: 100000,
            step: 100,
            unit: "字符",
            activeWhen: {
              when: (get) => get("pipeline.chunking_mode") === "auto",
              hint: "仅「自动」切分方式下生效",
            },
          },
        ],
      },
      {
        key: "ingest-enhance",
        label: "入库增强",
        items: [
          {
            path: "pipeline.clean_on",
            label: "入库清洗",
            desc: "切分前剥离页码、版权行、裸 URL 与控制字符等噪声，减少无意义片段。",
            kind: "switch",
            cost: "none",
          },
          {
            path: "pipeline.contextual_on",
            label: "片段上下文增强",
            desc: "为每个片段生成文档级定位上下文，拼在正文前一起向量化，缓解片段脱离上下文导致的召回漂移。",
            kind: "switch",
            cost: "llm",
          },
          {
            path: "pipeline.graph_index_on",
            label: "构建知识图谱",
            desc: "抽取实体与关系三元组并向量化，为「图谱辅助检索」提供数据。不开启则图谱检索无数据可查。",
            kind: "switch",
            cost: "llm-heavy",
          },
        ],
      },
    ],
  },
  {
    key: "retrieval",
    label: "检索阶段",
    desc: "一次提问如何被理解、扩展，以及如何召回并筛选资料片段。",
    groups: [
      {
        key: "query",
        label: "查询预处理",
        items: [
          {
            path: "pipeline.complexity_gate_on",
            label: "复杂度门控",
            desc: "查询已足够具体时跳过改写与意图路由，降低延迟与模型调用成本。",
            kind: "switch",
            cost: "none",
            conflicts: {
              when: (get) => isOn(get, "pipeline.graph_retrieval_on"),
              hint: "与「图谱辅助检索」冲突：门控短路时会强制走混合检索，图谱分支不会被触发。若以图谱为主，建议关闭门控。",
            },
          },
        ],
      },
      {
        key: "enhance",
        label: "查询增强（可同时启用）",
        items: [
          {
            path: "pipeline.hyde_on",
            label: "假设文档检索（HyDE）",
            desc: "先让模型写一段「假想的答案」，用它的向量去检索，弥合提问与文档的用词差异。仅影响语义检索分支。",
            kind: "switch",
            cost: "llm",
          },
          {
            path: "pipeline.subqueries_on",
            label: "子查询拆解",
            desc: "把包含多个主题的复合问题拆成若干子问题分别检索，再合并去重，提升多主题问题的召回。",
            kind: "switch",
            cost: "llm",
            conflicts: {
              when: (get) => get("pipeline.source_diversity") === "group_mmr",
              hint: "与「分组 + MMR」多样性策略部分抵消：增强刚扩大的候选会被 MMR 立即截断，召回增益受限。",
            },
          },
          {
            path: "pipeline.stepback_on",
            label: "后退式提问",
            desc: "额外生成一个更宏观的背景问题补充检索，适合需要前置知识才能回答的提问。",
            kind: "switch",
            cost: "llm",
            conflicts: {
              when: (get) => get("pipeline.source_diversity") === "group_mmr",
              hint: "与「分组 + MMR」多样性策略部分抵消：增强刚扩大的候选会被 MMR 立即截断，召回增益受限。",
            },
          },
          {
            path: "pipeline.enhance_candidate_k",
            label: "增强检索候选数",
            desc: "每个子问题 / 后退问题各自召回的片段数量，合并后仍按参考片段数裁剪。",
            kind: "number",
            min: 1,
            max: 50,
            activeWhen: {
              when: (get) =>
                isOn(get, "pipeline.hyde_on") ||
                isOn(get, "pipeline.subqueries_on") ||
                isOn(get, "pipeline.stepback_on"),
              hint: "需至少启用一项查询增强",
            },
          },
        ],
      },
      {
        key: "recall",
        label: "召回",
        items: [
          {
            path: "pipeline.hybrid_search_on",
            label: "混合检索",
            desc: "关键词（BM25）与语义两路并行召回后融合排序。关闭后仅做语义检索，对专有名词、编号类查询命中率会下降。",
            kind: "switch",
            cost: "none",
          },
          {
            path: "pipeline.top_k",
            label: "参考片段数",
            desc: "最终送入答案生成的片段数量。过大会引入噪声并增加生成成本。",
            kind: "number",
            min: 1,
            max: 50,
          },
          {
            path: "pipeline.acl_filter_on",
            label: "访问权限过滤",
            desc: "按提问时指定的资料范围过滤召回结果。租户隔离始终生效，不受此开关影响。",
            kind: "switch",
            cost: "none",
          },
        ],
      },
      {
        key: "postprocess",
        label: "结果处理",
        items: [
          {
            path: "pipeline.source_diversity",
            label: "来源多样性",
            desc: "避免所有引用都来自同一份文档。",
            kind: "choice",
            options: [
              { value: "off", label: "不限制", desc: "只按相关度排序" },
              { value: "group_only", label: "按文档分组", desc: "每份文档最多贡献若干片段" },
              { value: "group_mmr", label: "分组 + MMR", desc: "在分组基础上再做相关度与多样性的权衡" },
            ],
          },
          {
            path: "pipeline.group_size",
            label: "每来源保留数",
            desc: "同一份文档最多贡献多少个片段。",
            kind: "number",
            min: 1,
            max: 20,
            activeWhen: {
              when: (get) => get("pipeline.source_diversity") !== "off",
              hint: "需先选择一种多样性策略",
            },
          },
          {
            path: "pipeline.mmr_lambda",
            label: "相关度 / 多样性权衡",
            desc: "取值越大越偏重相关度，越小越偏重结果多样。",
            kind: "number",
            min: 0,
            max: 1,
            step: 0.05,
            activeWhen: {
              when: (get) => get("pipeline.source_diversity") === "group_mmr",
              hint: "仅「分组 + MMR」策略下生效",
            },
          },
          {
            path: "pipeline.rerank_on",
            label: "结果精排",
            desc: "用重排模型对召回候选做二次打分，把最贴题的片段提到前面。关闭可省下一次模型调用，但准确率通常明显下降。",
            kind: "switch",
            cost: "none",
          },
          {
            path: "pipeline.sentence_window_on",
            label: "父块回取",
            desc: "命中细粒度子片段时，回取它所属的完整章节作为证据，兼顾定位精度与上下文完整性。",
            kind: "switch",
            cost: "none",
            requires: {
              when: (get) => {
                const mode = get("pipeline.chunking_mode");
                return mode === "parent_child" || mode === "auto";
              },
              hint: "需要切分方式为「章节结构」（或「自动」且判定为章节结构）：只有该方式会生成父子片段关系，其余方式下本策略不会有任何效果。",
            },
          },
        ],
      },
      {
        key: "graph",
        label: "知识图谱检索",
        items: [
          {
            path: "pipeline.graph_retrieval_on",
            label: "图谱辅助检索",
            desc: "先在实体与关系图上定位相关节点，再回取对应片段，适合「谁在什么时间做了什么」这类关系型提问。",
            kind: "switch",
            cost: "llm",
            requires: {
              when: (get) => isOn(get, "pipeline.graph_index_on"),
              hint: "需先在入库阶段开启「构建知识图谱」：没有建过图时实体与关系集合为空，本策略只会做一次无效查询后降级为普通检索。",
            },
          },
          {
            path: "graph.use_llm_rerank",
            label: "图谱结果精排",
            desc: "用大模型对候选关系再筛一轮，精度更高、耗时更长。",
            kind: "switch",
            cost: "llm",
            activeWhen: {
              when: (get) => isOn(get, "pipeline.graph_retrieval_on"),
              hint: "需先开启图谱辅助检索",
            },
          },
          {
            path: "graph.expansion_degree",
            label: "关系扩展跳数",
            desc: "从命中实体沿关系向外扩展几层。跳数越大召回越广，噪声也越多。",
            kind: "number",
            min: 1,
            max: 3,
            activeWhen: {
              when: (get) => isOn(get, "pipeline.graph_retrieval_on"),
              hint: "需先开启图谱辅助检索",
            },
          },
        ],
      },
    ],
  },
  {
    key: "generation",
    label: "生成与验证阶段",
    desc: "答案如何生成、引用如何核验，以及在证据不足时如何选择不回答。",
    groups: [
      {
        key: "verify",
        label: "引用验证强度",
        items: [
          {
            path: "verify.entailment_mode",
            label: "蕴含判定",
            desc: "逐条核验答案里的结论是否真被引用原文支撑，是抑制幻觉的核心环节，也是整条链路最大的一笔模型开销。",
            kind: "choice",
            cost: "llm",
            options: [
              {
                value: "llm",
                label: "调用裁判模型",
                desc: "每条声明调用一次裁判模型判定，最严格；开销随答案长度线性增长",
              },
              {
                value: "skip",
                label: "跳过",
                desc: "只保留引用存在性与文本哈希校验，省下全部裁判调用；同时会关闭验证阶段的弃权判定，幻觉风险上升",
              },
            ],
          },
          {
            path: "verify.strict",
            label: "全量评审",
            desc: "对全部引用逐条判定。关闭后按下方比例抽样，用精度换成本。",
            kind: "switch",
            activeWhen: {
              when: (get) => get("verify.entailment_mode") === "llm",
              hint: "仅蕴含判定为「调用裁判模型」时生效",
            },
          },
          {
            path: "verify.sample_ratio",
            label: "抽样比例",
            desc: "非全量评审时，实际送去判定的引用占比。确定性均匀取样，同样输入结果可复现。",
            kind: "number",
            min: 0.05,
            max: 1,
            step: 0.05,
            activeWhen: {
              when: (get) =>
                get("verify.entailment_mode") === "llm" && !isOn(get, "verify.strict"),
              hint: "需关闭「全量评审」后才生效",
            },
          },
        ],
      },
      {
        key: "abstention",
        label: "弃权阈值",
        items: [
          {
            path: "pipeline.retrieval_score_threshold",
            label: "资料相关度下限",
            desc: "召回结果的最高分低于该值时，判定知识库中没有相关内容，直接拒答。调高会更谨慎，也更容易拒答本可回答的问题。",
            kind: "number",
            min: 0,
            max: 1,
            step: 0.05,
          },
          {
            path: "pipeline.entailment_score_threshold",
            label: "答案支撑度下限",
            desc: "生成的结论若得不到足够的原文支撑，则放弃作答而不是硬答。这是抑制幻觉的最后一道闸。",
            kind: "number",
            min: 0,
            max: 1,
            step: 0.05,
            requires: {
              when: (get) => get("verify.entailment_mode") === "llm",
              hint: "需要「蕴含判定」为「调用裁判模型」：跳过该层时不会产出支撑度分数，本阈值不再参与判定。",
            },
          },
        ],
      },
      {
        key: "engine",
        label: "执行方式",
        items: [
          {
            path: "pipeline.graph_engine_on",
            label: "图编排引擎",
            desc: "以状态图方式编排问答流程，便于观察各节点流转；不可用时自动回退顺序执行，结果一致。这是少数几个改完立即生效、无需重启的配置。",
            kind: "switch",
            cost: "none",
          },
        ],
      },
    ],
  },
];

/** 策略规格覆盖到的配置路径（高级配置表格据此去重，避免同一项出现两次） */
export const STRATEGY_PATHS: ReadonlySet<string> = new Set(
  STRATEGY_STAGES.flatMap((stage) =>
    stage.groups.flatMap((group) => group.items.map((item) => item.path)),
  ),
);
