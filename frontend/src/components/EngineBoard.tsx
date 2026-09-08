/* eslint-disable @typescript-eslint/no-explicit-any -- compatibility callback types during TDesign migration */
import {
  Alert,
  Button,
  Card,
  Col,
  InputNumber,
  Row,
  Segmented,
  Space,
  Switch,
  Tag,
  Tooltip,
  Typography,
} from "../ui/index";
import { ApiOutlined, ThunderboltOutlined, WarningOutlined } from "../ui/icons";
import type { ConfigField, ConfigSection, EnginePluginInfo, EnginesInfo } from "../types/rag";
import { FONT_SIZE } from "../theme/tokens";

const { Text, Paragraph } = Typography;

/** 模式文案（引擎插件通过 modes 声明，未知模式回退原名） */
const MODE_LABELS: Record<string, string> = {
  free: "免费额度",
  paid: "付费服务",
  local: "本地模型（免费）",
  api: "云端 API（付费）",
};

/** 需要管理员补充连接信息的解析引擎。 */
const CREDENTIALS: Record<string, { paths: string[]; section: string; note: string }> = {
  mineru: {
    section: "mineru",
    paths: ["mineru.api_key"],
    note: "HTTP 模式需配置 RAG4C_MINERU_API_KEY；CLI 模式需先执行 mineru-open-api login 完成授权",
  },
  docling: {
    section: "docling",
    paths: ["docling.api_base", "docling.api_key"],
    note: "需配置 RAG4C_DOCLING_API_BASE 与 RAG4C_DOCLING_API_KEY",
  },
};

interface EngineBoardProps {
  sections: Record<string, ConfigSection>;
  engines: EnginesInfo;
  edits: Record<string, string>;
  onEdit: (path: string, value: string) => void;
  onJumpSection: (key: string) => void;
}

/**
 * 文档处理设置：保留后端的插件化能力，但把日常操作解释成可理解的任务。
 */
export default function EngineBoard({ sections, engines, edits, onEdit, onJumpSection }: EngineBoardProps) {
  // 全量字段索引（值读取 / 编辑回显统一入口）
  const fieldMap: Record<string, ConfigField> = {};
  for (const sec of Object.values(sections)) {
    for (const f of sec.fields) fieldMap[f.path] = f;
  }
  const val = (path: string): string => {
    if (edits[path] !== undefined) return edits[path];
    const f = fieldMap[path];
    return f === undefined ? "" : String(f.value ?? "");
  };
  const boolVal = (path: string): boolean => val(path) === "true";
  const setBool = (path: string, v: boolean) => onEdit(path, String(v));

  const { choice, plugins } = engines;

  const choiceOptions = [
    { label: "自动选择（推荐）", value: "auto" },
    ...plugins.map((p) => ({ label: p.name, value: p.name })),
  ];

  /** 引擎三字段（编辑态优先于服务端快照） */
  const pluginEnabled = (p: EnginePluginInfo): boolean => {
    const path = `parsers.${p.name}.enabled`;
    return edits[path] !== undefined ? edits[path] === "true" : p.enabled;
  };
  const pluginMode = (p: EnginePluginInfo): string => {
    const path = `parsers.${p.name}.mode`;
    return edits[path] !== undefined ? edits[path] : p.mode;
  };
  const pluginPriority = (p: EnginePluginInfo): number | null => {
    const path = `parsers.${p.name}.priority`;
    if (edits[path] !== undefined) return Number(edits[path]);
    return p.priority;
  };

  // 自动选择时，数字越小越优先使用。
  const sorted = [...plugins].sort(
    (a, b) =>
      (pluginPriority(a) ?? 1e9) - (pluginPriority(b) ?? 1e9) || a.name.localeCompare(b.name),
  );

  // 当前生效引擎（客户端推导，编辑即时反馈）
  let activeName: string | null = null;
  if (choice === "auto") {
    for (const p of sorted) {
      if (p.configured && pluginEnabled(p)) {
        activeName = p.name;
        break;
      }
    }
  } else {
    const target = plugins.find((p) => p.name === choice);
    if (target && target.configured && pluginEnabled(target)) activeName = choice;
  }

  /** 付费服务未填写连接信息时，直接给出可行动提示。 */
  const credentialMissing = (
    name: string,
    mode: string,
  ): { missing: string[]; note: string } | null => {
    const cred = CREDENTIALS[name];
    if (!cred || (mode !== "paid" && mode !== "api")) return null;
    const missing = cred.paths.filter((p) => {
      const f = fieldMap[p];
      return !f || !String(f.value ?? "").trim();
    });
    return missing.length ? { missing, note: cred.note } : null;
  };

  const renderEngineCard = (p: EnginePluginInfo) => {
    const enabled = pluginEnabled(p);
    const mode = pluginMode(p);
    const priority = pluginPriority(p);
    const isActive = activeName === p.name;
    const cred = credentialMissing(p.name, mode);
    return (
      <Col xs={24} md={12} key={p.name}>
        <Card
          size="small"
          type="inner"
          title={
            <Space size={6}>
              <Text strong>{p.name}</Text>
              {isActive && <Tag color="green">当前生效</Tag>}
              {!isActive && enabled && p.configured && <Tag color="blue">备选</Tag>}
              {!enabled && p.configured && <Tag>未启用</Tag>}
              {!p.configured && <Tag color="orange">未配置连接</Tag>}
            </Space>
          }
          extra={
            <Space size={4}>
              <Text type="secondary" style={{ fontSize: FONT_SIZE.sm }}>
                启用
              </Text>
              <Switch
                size="small"
                checked={enabled}
                disabled={!p.configured}
                onChange={(v: any) => setBool(`parsers.${p.name}.enabled`, v)}
                aria-label={`启用 ${p.name}`}
              />
            </Space>
          }
        >
          <Paragraph type="secondary" style={{ fontSize: FONT_SIZE.sm, marginBottom: 10 }}>
            {p.describe}
          </Paragraph>
          <Space wrap size={12}>
            <Space size={4}>
              <Text type="secondary" style={{ fontSize: FONT_SIZE.sm }}>运行模式</Text>
              <Segmented
                size="small"
                value={mode}
                aria-label={p.name + " 运行模式"}
                options={(p.modes ?? []).map((m) => ({ label: MODE_LABELS[m] ?? m, value: m }))}
                onChange={(v: any) => onEdit(`parsers.${p.name}.mode`, String(v))}
              />
            </Space>
            <Space size={4}>
              <Text type="secondary" style={{ fontSize: FONT_SIZE.sm }}>
                优先级
              </Text>
              <InputNumber
                size="small"
                min={1}
                max={9999}
                style={{ width: 74 }}
                aria-label={p.name + " 优先级"}
                value={priority ?? undefined}
                disabled={choice !== "auto"}
                onChange={(v: any) => {
                  if (v != null) onEdit(`parsers.${p.name}.priority`, String(v));
                }}
              />
              {choice !== "auto" && (
                <Text type="secondary" style={{ fontSize: FONT_SIZE.xs }}>
                  仅自动选择时生效
                </Text>
              )}
            </Space>
          </Space>
          {cred && (
            <Alert
              style={{ marginTop: 10 }}
              type="warning"
              showIcon
              icon={<WarningOutlined />}
              message="缺少连接信息"
              description={
                <Space direction="vertical" size={2}>
                  <Text style={{ fontSize: FONT_SIZE.sm }}>{cred.note}</Text>
                  <Button
                    size="small"
                    type="link"
                    style={{ padding: 0 }}
                    onClick={() => onJumpSection(CREDENTIALS[p.name]?.section ?? "mineru")}
                  >
                    前往连接配置
                  </Button>
                </Space>
              }
            />
          )}
          {choice === p.name && !enabled && p.configured && (
            <Alert
              style={{ marginTop: 10 }}
              type="error"
              showIcon
              message="已指定该引擎，但其当前未启用"
              description="文档解析将无法执行。请开启上方「启用」开关，或将引擎选择改回「自动选择」。"
            />
          )}
        </Card>
      </Col>
    );
  };

  return (
    <Card
      size="small"
      className="engine-board"
      style={{ marginBottom: 14 }}
      styles={{ body: { padding: 14 } }}
      title={
        <Space size={6}>
          <ThunderboltOutlined style={{ color: "var(--color-primary)" }} />
          <span>文档解析引擎</span>
          <Text type="secondary" style={{ fontSize: FONT_SIZE.sm, fontWeight: 400 }}>
            决定 PDF、扫描件与表格如何转换为可检索的资料片段。
          </Text>
        </Space>
      }
      extra={
        <Space size={4}>
          <Text type="secondary" style={{ fontSize: FONT_SIZE.sm }}>
            引擎选择
          </Text>
          <Segmented
            size="small"
            value={choice}
            aria-label="选择解析引擎"
            options={choiceOptions}
            onChange={(v: any) => onEdit("parsers.engine", String(v))}
          />
        </Space>
      }
    >
      <Row gutter={[12, 12]}>{sorted.map(renderEngineCard)}</Row>

      {activeName === null && (
        <Alert
          style={{ marginTop: 12 }}
          type="error"
          showIcon
          message="当前没有可用的解析引擎"
          description="扫描版 PDF、图片与 Office 文档将无法解析入库。请至少启用一个引擎；首次使用可选择 MinerU 的免费额度模式。"
        />
      )}

      <Card size="small" type="inner" style={{ marginTop: 12 }} title="解析增强">
        <Space wrap size={18}>
          {[
            { path: "parsers.router_on", label: "解析分流", tip: "自动区分文本型与扫描型 PDF，分别进入快速通道或 OCR 引擎。" },
            { path: "parsers.pdf_inspector_on", label: "快速通道", tip: "对含文字层的 PDF 直接提取文本，无需 OCR，建议保持开启。" },
            { path: "parsers.tsr_on", label: "表格结构还原", tip: "识别合并单元格与层级表头，提升表格内容的检索准确性。" },
            { path: "parsers.rotation_on", label: "方向校正", tip: "对方向异常的扫描页在 0 / 90 / 180 / 270 度中择优校正。" },
          ].map((item) => (
            <Space key={item.path} size={6}>
              <Tooltip title={item.tip} trigger={["hover", "focus"]}>
                {/* tabIndex + focus 触发：仅 hover 的说明键盘用户看不到 */}
                <Text
                  tabIndex={0}
                  style={{
                    fontSize: FONT_SIZE.md,
                    cursor: "help",
                    borderBottom: "1px dashed var(--color-border-strong)",
                  }}
                >
                  {item.label}
                </Text>
              </Tooltip>
              <Switch
                size="small"
                checked={boolVal(item.path)}
                onChange={(v: any) => setBool(item.path, v)}
                aria-label={item.label}
              />
            </Space>
          ))}
        </Space>
      </Card>

      <Paragraph type="secondary" style={{ fontSize: FONT_SIZE.xs, marginTop: 10, marginBottom: 0 }}>
        <ApiOutlined /> 付费模式需管理员预先配置连接信息；未完成配置时系统会明确提示，不会自动切换到其他引擎。
        文档切分方式在上方「策略编排」的入库阶段设置。
      </Paragraph>
    </Card>
  );
}
