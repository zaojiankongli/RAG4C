/* eslint-disable @typescript-eslint/no-explicit-any -- compatibility callback types during TDesign migration */
import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Input,
  Select,
  Tag,
  Typography,
  message,
} from "../ui/index";
import {
  ReloadOutlined,
  SaveOutlined,
  SearchOutlined,
  SettingOutlined,
} from "../ui/icons";
import PageTopbar from "../components/PageTopbar";
import EngineBoard from "../components/EngineBoard";
import StrategyBoard from "../components/StrategyBoard";
import { STRATEGY_PATHS } from "../strategy/spec";
import { DEFAULT_BASE, fetchConfig, getBaseUrl, setBaseUrl, updateConfig } from "../api/client";
import { DEMO_CONFIG } from "../api/mock";
import { useConnection } from "../context/ConnectionContext";
import type { ConfigField, ConfigSnapshot } from "../types/rag";

const { Text } = Typography;

/** 对象存储后端注册表（catalog/milvus 高级配置之后） */
const StorageBackendsPanel = lazy(
  () => import("../storage-backends/components/StorageBackendsPanel"),
);

const FIELD_LABELS: Record<string, string> = {
  "pipeline.hybrid_search_on": "混合检索（关键词 + 语义）",
  "pipeline.rerank_on": "检索结果精排",
  "pipeline.graph_retrieval_on": "知识图谱辅助检索",
  "pipeline.acl_filter_on": "访问权限过滤",
  "pipeline.source_diversity": "来源多样性策略",
  "pipeline.group_size": "每来源保留片段数",
  "pipeline.group_by_field": "来源分组字段",
  "pipeline.mmr_lambda": "相关性与多样性权衡",
  "pipeline.complexity_gate_on": "复杂度门控",
  "pipeline.retrieval_score_threshold": "检索置信度阈值",
  "pipeline.entailment_score_threshold": "答案支撑度阈值",
  "pipeline.top_k": "单次参考片段数",
  "pipeline.hyde_on": "假设文档检索（HyDE）",
  "pipeline.subqueries_on": "子查询拆解",
  "pipeline.stepback_on": "后退式提问",
  "pipeline.sentence_window_on": "父段落回取",
  "pipeline.graph_engine_on": "图编排执行引擎",
  "pipeline.chunking_mode": "文档切分方式",
  "pipeline.simple_doc_max_chars": "短文档字数阈值",
  "llm.generation.base_url": "生成服务地址",
  "llm.generation.model": "生成模型",
  "llm.generation.temperature": "生成采样温度",
  "llm.generation.max_tokens": "单次生成长度上限",
  "llm.judge.model": "评判模型",
  "llm.judge.temperature": "评判采样温度",
  "llm.router_llm.model": "意图路由模型",
  "llm.rewrite.model": "查询改写模型",
  "llm.triplet.model": "三元组抽取模型",
  "embedding.provider": "向量化服务来源",
  "embedding.model": "向量化模型",
  "embedding.dim": "向量维度",
  "embedding.max_length": "单条文本长度上限",
  "embedding.batch_size": "向量化批大小",
  "embedding.normalize_embeddings": "向量归一化",
  "embedding.api_base_url": "向量化服务地址",
  "embedding.api_timeout": "向量化请求超时",
  "reranker.model": "重排序模型",
  "reranker.device": "重排序推理设备",
  "reranker.use_fp16": "半精度推理",
  "reranker.batch_size": "重排序批大小",
  "reranker.normalize_score": "重排序分数归一化",
  "milvus.uri": "向量库地址",
  "milvus.collection_name": "文档片段集合",
  "milvus.entity_collection": "图谱实体集合",
  "milvus.relation_collection": "图谱关系集合",
  "milvus.dim": "向量维度",
  "milvus.index_type": "向量索引算法",
  "milvus.metric_type": "相似度度量方式",
  "milvus.nlist": "索引分桶数量",
  "milvus.ef_construction": "索引构建候选宽度",
  "milvus.nprobe": "检索探测分桶数",
  "milvus.ef": "检索候选宽度",
  "milvus.bm25_k1": "BM25 词频饱和参数",
  "milvus.bm25_b": "BM25 长度归一化参数",
  "milvus.candidate_factor": "候选预取倍数",
  "milvus.rrf_k": "RRF 融合常数",
  "milvus.timeout": "向量库请求超时",
  "graph.entity_top_k": "实体召回上限",
  "graph.relation_top_k": "关系召回上限",
  "graph.entity_similarity_threshold": "实体相似度阈值",
  "graph.relation_similarity_threshold": "关系相似度阈值",
  "graph.expansion_degree": "关系扩展跳数",
  "graph.final_top_k": "图谱最终片段数",
  "graph.use_llm_rerank": "图谱结果精排",
  "retry.max_attempts": "最大尝试次数",
  "retry.base_delay": "首次重试等待",
  "retry.max_delay": "重试等待上限",
  "retry.jitter": "等待随机抖动比例",
  "retry.backoff_factor": "退避倍增系数",
  "observability.log_level": "日志级别",
  "observability.tracing_enabled": "链路追踪",
  "observability.metrics_enabled": "指标采集",
  "catalog.db_path": "目录数据库路径",
  "catalog.auto_filter_on": "自动生成检索过滤条件",
  "catalog.auto_tag_on": "自动生成文档标签",
  "tenant.enforced": "强制租户隔离",
  "tenant.default_tenant": "默认租户标识",
  "circuit.failure_threshold": "熔断触发阈值",
  "circuit.cooldown_s": "熔断冷却时长",
  "circuit.half_open_probe": "半开探测请求数",
};

const SECTION_LABELS: Record<string, string> = {
  pipeline: "检索与问答",
  parsers: "文档解析",
  embedding: "向量化服务",
  reranker: "重排序服务",
  llm: "生成模型",
  graph: "知识图谱",
  verify: "引用验证",
  milvus: "向量数据库",
  catalog: "目录服务",
  tenant: "多租户隔离",
  retry: "重试策略",
  circuit: "熔断保护",
  observability: "日志与监控",
  mineru: "MinerU 连接",
  docling: "Docling 连接",
};

const SECTION_DESCRIPTIONS: Record<string, string> = {
  pipeline: "检索召回、结果筛选与资料不足时的弃权策略。",
  parsers: "PDF、扫描件与表格的解析引擎和切分方式。",
  embedding: "将问题与资料转为向量的服务配置。",
  reranker: "对召回结果做相关性精排的服务配置。",
  llm: "生成回答所使用的模型与服务地址。",
  graph: "实体与关系的抽取、检索和扩展参数。",
  verify: "引用验证三层防线的强度与成本，主要参数已在上方策略编排中。",
  milvus: "向量库连接与索引参数，通常无需日常调整。",
  catalog: "文档目录与元数据的存储和自动化处理。",
  tenant: "租户之间的数据隔离策略。",
  retry: "依赖服务暂时不可用时的自动重试规则。",
  circuit: "连续故障时的熔断与恢复策略。",
  observability: "日志级别与运行指标采集配置。",
  mineru: "MinerU 解析引擎的连接信息。",
  docling: "Docling 解析引擎的连接信息。",
};

function displayFieldName(field: ConfigField) {
  if (FIELD_LABELS[field.path]) return FIELD_LABELS[field.path];
  if (field.label) return field.label;
  return (
    field.path
      .split(".")
      .pop()
      ?.replace(/_/g, " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase()) ?? field.path
  );
}

function applySnapshotUpdates(
  snapshot: ConfigSnapshot,
  updates: { path: string; value: string }[],
) {
  const values = new Map(updates.map((item) => [item.path, item.value]));
  return {
    ...snapshot,
    sections: Object.fromEntries(
      Object.entries(snapshot.sections).map(([key, section]) => [
        key,
        {
          ...section,
          fields: section.fields.map((field) =>
            values.has(field.path)
              ? { ...field, value: values.get(field.path) ?? field.value }
              : field,
          ),
        },
      ]),
    ),
  };
}

/** 字段值编辑器（bool 用下拉，敏感字段只读） */
function FieldEditor({
  field,
  value,
  onChange,
}: {
  field: ConfigField;
  value: string;
  onChange: (v: string) => void;
}) {
  if (field.sensitive) {
    return (
      <Text type="secondary">{field.value ? "••••••（已配置，出于安全不可在此修改）" : "未配置"}</Text>
    );
  }
  if (field.type === "bool") {
    return (
      <Select
        value={value}
        onChange={onChange}
        aria-label={displayFieldName(field)}
        style={{ width: 90 }}
        options={[
          { value: "true", label: "开" },
          { value: "false", label: "关" },
        ]}
      />
    );
  }
  const opts = enumOptions(field);
  if (opts) {
    return (
      <Select
        value={value}
        onChange={onChange}
        aria-label={displayFieldName(field)}
        style={{ width: 180 }}
        options={opts}
      />
    );
  }
  return (
    <Input
      value={value}
      onChange={(e: any) => onChange(e.target.value)}
      aria-label={displayFieldName(field)}
      style={{ width: "100%", maxWidth: 260 }}
    />
  );
}

/** 枚举字段的可选值（中文显示、原值存储；按 path 后缀匹配） */
const ENUM_OPTIONS: Record<string, { value: string; label: string }[]> = {
  provider: [
    { value: "api", label: "云端 API（免费额度）" },
    { value: "local", label: "本地模型" },
    { value: "cli", label: "本机命令行" },
    { value: "http", label: "官方 API" },
  ],
  mode: [
    { value: "free", label: "免费" },
    { value: "paid", label: "付费" },
    { value: "local", label: "本地（免费）" },
    { value: "api", label: "云服务（付费）" },
  ],
  engine: [
    { value: "auto", label: "自动选择" },
    { value: "mineru", label: "MinerU" },
    { value: "docling", label: "Docling" },
  ],
  chunking_mode: [
    { value: "auto", label: "自动（推荐）" },
    { value: "recursive", label: "按固定长度" },
    { value: "parent_child", label: "按章节结构" },
    { value: "qa", label: "表格逐行问答" },
  ],
  source_diversity: [
    { value: "off", label: "不限制" },
    { value: "group_only", label: "按来源分组" },
    { value: "group_mmr", label: "分组 + 多样性" },
  ],
  metric_type: [
    { value: "COSINE", label: "余弦相似" },
    { value: "IP", label: "内积" },
    { value: "L2", label: "欧氏距离" },
  ],
  index_type: [
    { value: "HNSW", label: "HNSW（推荐）" },
    { value: "FLAT", label: "FLAT（全量比对）" },
    { value: "IVF_FLAT", label: "IVF_FLAT（分桶）" },
  ],
  model_version: [
    { value: "vlm", label: "视觉大模型" },
    { value: "pipeline", label: "管线模型" },
    { value: "MinerU-HTML", label: "HTML 输出" },
  ],
  language: [
    { value: "ch", label: "中文" },
    { value: "en", label: "英文" },
    { value: "japan", label: "日文" },
    { value: "korean", label: "韩文" },
  ],
  log_level: [
    { value: "DEBUG", label: "调试" },
    { value: "INFO", label: "信息" },
    { value: "WARNING", label: "警告" },
    { value: "ERROR", label: "错误" },
  ],
  entailment_mode: [
    { value: "llm", label: "调用裁判模型" },
    { value: "skip", label: "跳过" },
  ],
};

const enumOptions = (f: ConfigField) =>
  Object.entries(ENUM_OPTIONS).find(
    ([suffix]) => f.path.endsWith("." + suffix) || f.path === suffix,
  )?.[1];

/** 配置中心：策略编排 + 解析引擎 + 高级配置（值 / 环境变量 / 来源），支持编辑保存到 .env */
export default function ConfigPage() {
  const { online, refresh } = useConnection();
  const [baseUrl, setBaseUrlInput] = useState(getBaseUrl);
  const [snapshot, setSnapshot] = useState<ConfigSnapshot | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(false);
  const [advSection, setAdvSection] = useState<string>("milvus");
  const [search, setSearch] = useState("");
  const [saveNotice, setSaveNotice] = useState<{
    type: "success" | "warning" | "error";
    title: string;
    description: string;
  } | null>(null);
  const advancedRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setSaveNotice(null);
    // 桥服务已确认离线：直接演示数据，不等 fetch 超时（秒开）
    if (online === false) {
      setSnapshot(DEMO_CONFIG);
      setEdits({});
      setLoading(false);
      return;
    }
    try {
      setSnapshot(await fetchConfig());
      setEdits({});
    } catch {
      setSnapshot(DEMO_CONFIG);
      setEdits({});
    } finally {
      setLoading(false);
    }
  }, [online]);

  useEffect(() => {
    void load();
  }, [load]);

  const fieldValue = (f: ConfigField): string => edits[f.path] ?? String(f.value ?? "");

  /** 全量字段索引：策略面板按路径读值 / 判断后端是否支持该字段 */
  const fieldByPath = useMemo(() => {
    const map: Record<string, ConfigField> = {};
    for (const section of Object.values(snapshot?.sections ?? {})) {
      for (const f of section.fields) map[f.path] = f;
    }
    return map;
  }, [snapshot]);

  /** 策略面板取值：未保存的编辑优先于服务端快照 */
  const getStrategyValue = useCallback(
    (path: string): string => {
      if (edits[path] !== undefined) return edits[path];
      const f = fieldByPath[path];
      return f === undefined ? "" : String(f.value ?? "");
    },
    [edits, fieldByPath],
  );

  const hasStrategyPath = useCallback(
    (path: string): boolean => fieldByPath[path] !== undefined,
    [fieldByPath],
  );

  /** 按日常名称、说明、路径或环境变量名过滤字段。 */
  const filterFields = (fields: ConfigField[]): ConfigField[] => {
    const kw = search.trim().toLowerCase();
    if (!kw) return fields;
    return fields.filter(
      (f) =>
        displayFieldName(f).toLowerCase().includes(kw) ||
        f.hint?.toLowerCase().includes(kw) ||
        f.path.toLowerCase().includes(kw) ||
        f.env.toLowerCase().includes(kw),
    );
  };

  const dirtyCount =
    snapshot == null
      ? 0
      : Object.entries(snapshot.sections)
          .flatMap(([, sec]) => sec.fields)
          .filter(
            (f) =>
              !f.sensitive &&
              edits[f.path] !== undefined &&
              edits[f.path] !== String(f.value ?? ""),
          ).length;

  useEffect(() => {
    if (dirtyCount === 0) return;
    const warnAboutUnsavedChanges = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warnAboutUnsavedChanges);
    return () => window.removeEventListener("beforeunload", warnAboutUnsavedChanges);
  }, [dirtyCount]);

  const handleSave = useCallback(async () => {
    if (!snapshot) return;
    const updates = Object.entries(snapshot.sections)
      .flatMap(([, sec]) => sec.fields)
      .filter(
        (f) =>
          !f.sensitive && edits[f.path] !== undefined && edits[f.path] !== String(f.value ?? ""),
      )
      .map((f) => ({ path: f.path, value: edits[f.path] }));
    if (updates.length === 0) {
      message.info("还没有修改需要保存");
      return;
    }
    setSaving(true);
    try {
      if (online === false) {
        await new Promise((r: any) => setTimeout(r, 600));
        setSnapshot((current) => (current ? applySnapshotUpdates(current, updates) : current));
        setEdits({});
        setSaveNotice({
          type: "success",
          title: "已更新预览设置",
          description:
            "这些调整只用于当前预览，不会改动真实系统。连接后端服务后，可以在这里保存真实设置。",
        });
        message.success("已更新当前预览设置");
        return;
      }
      const resp = await updateConfig(updates);
      const savedPaths = new Set(resp.saved.map((item) => item.path));
      const savedUpdates = updates.filter((item) => savedPaths.has(item.path));
      setSnapshot((current) => (current ? applySnapshotUpdates(current, savedUpdates) : current));
      setEdits((current) =>
        Object.fromEntries(Object.entries(current).filter(([path]) => !savedPaths.has(path))),
      );
      if (resp.rejected.length) {
        setSaveNotice({
          type: "warning",
          title: "部分设置还没有保存",
          description: `已保存 ${resp.saved.length} 项。其余设置暂时无法更新，请确认服务正在运行后再试。`,
        });
        message.warning(`已保存 ${resp.saved.length} 项，还有 ${resp.rejected.length} 项未完成`);
      } else {
        setSaveNotice({
          type: "success",
          title: `已保存 ${resp.saved.length} 项配置`,
          description: resp.hot_reloaded
            ? "管线已按新配置重建，对后续请求立即生效。桥服务自身的并发 / 队列 / 缓存参数（bridge 段）仍需重启后端。"
            : "请重启后端服务让新配置生效，随后点击「重新读取」确认结果。",
        });
        message.success(`已保存 ${resp.saved.length} 项配置`);
      }
    } catch {
      setSaveNotice({
        type: "error",
        title: "设置暂时没有保存成功",
        description: "请检查后端服务是否已经启动、网络是否正常，然后重新尝试保存。",
      });
      message.error("设置暂时没有保存成功，请检查服务后重试");
    } finally {
      setSaving(false);
    }
  }, [edits, online, snapshot]);

  const sections = snapshot?.sections ?? {};

  const jumpToSection = (key: string) => {
    setAdvSection(key);
    window.setTimeout(
      () => advancedRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
      0,
    );
  };

  // 高级配置的分段列表：策略编排已覆盖的项不重复出现；整段被接管（如 verify）时不留空分组
  const advSections = Object.entries(sections)
    .map(([key, sec]) => ({
      key,
      label: SECTION_LABELS[key] ?? sec.label,
      description: SECTION_DESCRIPTIONS[key] ?? sec.description,
      fields: filterFields(sec.fields.filter((f) => !STRATEGY_PATHS.has(f.path))),
    }))
    .filter((s) => s.fields.length > 0);

  // 当前选中分段：选中项被搜索过滤掉时回退到第一个可用分段。
  // 显式标注 | null —— tsconfig 未开 noUncheckedIndexedAccess，
  // advSections[0] 被推成非空类型，会让 TS 把 `?? null` 分支整个吞掉，
  // 于是下方的 `=== null` 判空在类型层面失去保护（运行时仍然正确）。
  const activeAdv: (typeof advSections)[number] | null =
    advSections.find((s) => s.key === advSection) ?? advSections[0] ?? null;

  return (
    <div className="page-slot">
      <PageTopbar
        icon={<SettingOutlined />}
        title="设置"
        subtitle={
          dirtyCount > 0 ? dirtyCount + " 项修改尚未保存" : "常用配置在前，管理员选项在页面下方"
        }
        extra={
          <>
            <Input
              size="small"
              allowClear
              prefix={<SearchOutlined />}
              placeholder="搜索配置项，例如：模型、向量、重试…"
              value={search}
              onChange={(e: any) => setSearch(e.target.value)}
              style={{ width: 220 }}
              aria-label="搜索设置"
            />
            <Button
              size="small"
              icon={<ReloadOutlined spin={loading} />}
              loading={loading}
              onClick={() => void load()}
            >
              重新读取
            </Button>
            <Button
              type="primary"
              size="small"
              icon={<SaveOutlined />}
              loading={saving}
              disabled={dirtyCount === 0}
              onClick={() => void handleSave()}
            >
              保存修改
            </Button>
          </>
        }
      />
      <div className="page-shell">
        <div className="page-shell-inner">

        <Alert
          type={online === false ? "warning" : "info"}
          showIcon
          className="settings-notice"
          message={online === false ? "预览模式" : "修改后需统一保存"}
          description={
            online === false
              ? "当前展示的是演示配置，调整不会写入真实系统。连接后端服务后，此处将显示并保存实际配置。"
              : "调整会先暂存在本页，确认后点击右上角「保存修改」写入 .env。保存后管线会按新配置重建，对后续请求立即生效；桥服务自身的并发 / 队列 / 缓存参数需重启后端。"
          }
        />

        {/*
          后端地址。放在最前面：它是「其他所有配置能不能读到」的前提。
          缺陷记录：这一项此前只能改 localStorage，而打包后的 Tauri 窗口没有
          开发者工具，用户没有任何入口执行那行代码——后端一旦不在默认端口，
          应用就永久失联。地址存在浏览器本地，不属于 .env，因此独立于
          右上角「保存修改」，改完立即生效。
        */}
        <Card size="small" title="后端服务地址" style={{ marginBottom: 16 }}>
          <Text type="secondary" style={{ display: "block", marginBottom: 8 }}>
            桌面端与后端分开部署时在这里改。仅保存在本机浏览器，不写入 .env；留空恢复默认
            {" " + DEFAULT_BASE}。
          </Text>
          <Input.Search
            value={baseUrl}
            placeholder={DEFAULT_BASE}
            enterButton="保存并重新连接"
            aria-label="后端服务地址"
            onChange={(e: any) => setBaseUrlInput(e.target.value)}
            onSearch={(v: any) => {
              setBaseUrl(v);
              // 回填规范化后的结果：留空时要让输入框显示实际生效的默认值
              setBaseUrlInput(getBaseUrl());
              void refresh();
              void load();
              message.success("已切换到 " + getBaseUrl());
            }}
          />
        </Card>

        {saveNotice && (
          <Alert
            type={saveNotice.type}
            showIcon
            closable
            onClose={() => setSaveNotice(null)}
            className="settings-notice settings-save-notice"
            message={saveNotice.title}
            description={saveNotice.description}
          />
        )}

        {snapshot && (
          <StrategyBoard
            getValue={getStrategyValue}
            onEdit={(path, value) => setEdits((prev) => ({ ...prev, [path]: value }))}
            hasPath={hasStrategyPath}
          />
        )}

        {snapshot?.engines && (
          <div id="document-processing">
            <EngineBoard
              sections={sections}
              engines={snapshot.engines}
              edits={edits}
              onEdit={(path, value) => setEdits((prev) => ({ ...prev, [path]: value }))}
              onJumpSection={jumpToSection}
            />
          </div>
        )}

        <div ref={advancedRef} className="settings-advanced">
          <Card
            className="settings-admin-card"
            size="small"
            title="高级配置"
            extra={<Tag>管理员选项</Tag>}
            styles={{ body: { padding: 0 } }}
          >
            {activeAdv === null ? (
              <div className="adv-empty">
                {search.trim() ? "没有匹配的配置项" : "暂无可调整的高级配置"}
              </div>
            ) : (
              <div className="adv-config">
                {/* 左：分段导航（固定宽度，自身滚动） */}
                <nav className="adv-nav" aria-label="高级配置分段">
                  {advSections.map((s) => (
                    <button
                      key={s.key}
                      type="button"
                      className={"adv-nav-item" + (s.key === activeAdv.key ? " is-active" : "")}
                      onClick={() => setAdvSection(s.key)}
                    >
                      <span className="adv-nav-label">{s.label}</span>
                      <span className="adv-nav-count tabular-nums">{s.fields.length}</span>
                    </button>
                  ))}
                </nav>

                {/* 右：内容区（固定高度 + 内部滚动，切换分段时页面不跳） */}
                <div className="adv-panel">
                  <header className="adv-panel-head">
                    <div className="adv-panel-title">
                      {activeAdv.label}
                      <span className="adv-panel-count tabular-nums">
                        {activeAdv.fields.length} 项
                      </span>
                    </div>
                    <div className="adv-panel-desc">{activeAdv.description}</div>
                  </header>
                  <div className="adv-panel-body">
                    {activeAdv.fields.map((f) => (
                      <div key={f.path} className="adv-row">
                        <div className="adv-row-main">
                          <div className="adv-row-head">
                            <span className="adv-row-label" title={f.path}>
                              {displayFieldName(f)}
                            </span>
                            {f.sensitive ? (
                              <Tag color="purple">管理员配置</Tag>
                            ) : f.source === "env" ? (
                              <Tag color="green">已自定义</Tag>
                            ) : (
                              <Tag>默认值</Tag>
                            )}
                            <code className="adv-row-env">{f.env}</code>
                          </div>
                          {f.hint && <div className="adv-row-hint">{f.hint}</div>}
                        </div>
                        <div className="adv-row-control">
                          <FieldEditor
                            field={f}
                            value={fieldValue(f)}
                            onChange={(v: any) => setEdits((prev) => ({ ...prev, [f.path]: v }))}
                          />
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </Card>
        </div>

        {/*
          对象存储后端注册表：catalog / milvus 高级配置之后。
          控制面 CRUD + 测试连接 + 默认/知识库绑定；密钥永不回显明文。
        */}
        <div className="settings-storage-backends" style={{ marginTop: 16 }}>
          <Card
            className="settings-admin-card"
            size="small"
            title="对象存储后端"
            extra={<Tag>控制面</Tag>}
          >
            <Text type="secondary" style={{ display: "block", marginBottom: 12 }}>
              注册对象存储实例，设置租户默认与知识库绑定。API 响应不包含明文密钥。
            </Text>
            <Suspense
              fallback={
                <div className="storage-backend-empty" aria-busy="true">
                  加载对象存储后端面板…
                </div>
              }
            >
              <StorageBackendsPanel />
            </Suspense>
          </Card>
        </div>
        </div>
      </div>
    </div>
  );
}
