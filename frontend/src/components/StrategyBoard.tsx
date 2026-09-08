/* eslint-disable @typescript-eslint/no-explicit-any -- compatibility callback types during TDesign migration */
import { Alert, Card, InputNumber, Segmented, Switch, Tag, Tooltip, Typography } from "../ui/index";
import {
  ExclamationCircleOutlined,
  InfoCircleOutlined,
  ThunderboltOutlined,
} from "../ui/icons";
import { STRATEGY_STAGES } from "../strategy/spec";
import type { StrategyCost, StrategyItem, ValueGetter } from "../strategy/spec";

const { Text } = Typography;

/** 开销标记：让「这个开关要花多少钱」在界面上一眼可见 */
const COST_META: Record<Exclude<StrategyCost, "none">, { label: string; color: string; tip: string }> =
  {
    llm: {
      label: "调用大模型",
      color: "gold",
      tip: "开启后会产生额外的大模型调用，增加耗时与费用",
    },
    "llm-heavy": {
      label: "大模型开销高",
      color: "volcano",
      tip: "每个片段一次大模型调用，入库耗时与费用显著上升",
    },
  };

interface Props {
  /** 读取配置项当前值（已合并未保存的编辑） */
  getValue: ValueGetter;
  onEdit: (path: string, value: string) => void;
  /** 该路径在后端配置里是否存在（不存在说明后端版本较旧） */
  hasPath: (path: string) => boolean;
}

/**
 * 策略编排面板 —— 按管线阶段组织可插拔策略。
 *
 * 与通用配置表格的区别：这里表达的是**策略之间的关系**——互斥项渲染为
 * 单选、可共存项渲染为开关，前置依赖不满足时禁用并说明原因，会互相
 * 削弱的组合给出警告。关系定义在 `strategy/spec.ts`，与
 * `docs/RAG策略矩阵.md` 对应。
 */
export default function StrategyBoard({ getValue, onEdit, hasPath }: Props) {
  const renderControl = (item: StrategyItem, disabled: boolean) => {
    const value = getValue(item.path);

    if (item.kind === "switch") {
      return (
        <Switch
          size="small"
          checked={value === "true"}
          disabled={disabled}
          onChange={(v: any) => onEdit(item.path, String(v))}
          aria-label={item.label}
        />
      );
    }

    if (item.kind === "choice") {
      return (
        <Segmented
          size="small"
          value={value || item.options?.[0]?.value}
          disabled={disabled}
          aria-label={item.label}
          options={(item.options ?? []).map((o) => ({ label: o.label, value: o.value }))}
          onChange={(v: any) => onEdit(item.path, String(v))}
        />
      );
    }

    return (
      <InputNumber
        size="small"
        min={item.min}
        max={item.max}
        step={item.step}
        style={{ width: 108 }}
        disabled={disabled}
        aria-label={item.label}
        value={value === "" ? undefined : Number(value)}
        onChange={(v: any) => {
          if (v !== null && v !== undefined) onEdit(item.path, String(v));
        }}
      />
    );
  };

  const renderItem = (item: StrategyItem) => {
    // 后端没有这个字段：说明后端版本较旧，直接隐藏，避免写入无效配置
    if (!hasPath(item.path)) return null;

    const unmetRequirement = item.requires && !item.requires.when(getValue);
    const hasConflict = item.conflicts?.when(getValue) ?? false;
    const inactive = item.activeWhen ? !item.activeWhen.when(getValue) : false;
    // 依赖不满足 -> 禁用（开了也没用）；仅「当前无意义」-> 灰显但可改
    const disabled = Boolean(unmetRequirement);
    const selectedOption = item.options?.find((o) => o.value === getValue(item.path));

    return (
      <div
        key={item.path}
        className={"strategy-item" + (inactive && !disabled ? " is-inactive" : "")}
      >
        <div className="strategy-item-main">
          <div className="strategy-item-head">
            <Text strong className="strategy-item-label">
              {item.label}
            </Text>
            {item.cost && item.cost !== "none" && (
              <Tooltip title={COST_META[item.cost].tip}>
                <Tag color={COST_META[item.cost].color} className="strategy-cost">
                  <ThunderboltOutlined /> {COST_META[item.cost].label}
                </Tag>
              </Tooltip>
            )}
          </div>
          <div className="strategy-item-desc">{item.desc}</div>
          {selectedOption?.desc && (
            <div className="strategy-item-hint is-neutral">
              <InfoCircleOutlined /> {selectedOption.desc}
            </div>
          )}
          {unmetRequirement && (
            <div className="strategy-item-hint is-blocked">
              <ExclamationCircleOutlined /> {item.requires?.hint}
            </div>
          )}
          {!unmetRequirement && hasConflict && (
            <div className="strategy-item-hint is-warning">
              <ExclamationCircleOutlined /> {item.conflicts?.hint}
            </div>
          )}
          {!unmetRequirement && inactive && item.activeWhen && (
            <div className="strategy-item-hint is-neutral">
              <InfoCircleOutlined /> {item.activeWhen.hint}
            </div>
          )}
        </div>
        <div className="strategy-item-control">{renderControl(item, disabled)}</div>
      </div>
    );
  };

  return (
    <div className="strategy-board">
      <Alert
        type="info"
        showIcon
        className="settings-notice"
        message="策略按管线阶段编排"
        description="同一维度的策略互斥（单选），彼此独立的策略可同时启用（开关）。存在前置依赖时控件会被禁用并说明原因；会相互削弱的组合会给出提示。"
      />

      {STRATEGY_STAGES.map((stage, stageIdx) => (
        <div key={stage.key} className="strategy-stage-wrap">
          <Card
            size="small"
            className="strategy-stage"
            title={
              <div className="strategy-stage-title">
                <span className="strategy-stage-index">{stageIdx + 1}</span>
                <span>{stage.label}</span>
              </div>
            }
          >
            <div className="strategy-stage-desc">{stage.desc}</div>
            {stage.groups.map((group) => {
              const rendered = group.items.map(renderItem).filter(Boolean);
              if (rendered.length === 0) return null;
              return (
                <section key={group.key} className="strategy-group">
                  <div className="strategy-group-label">{group.label}</div>
                  <div className="strategy-group-items">{rendered}</div>
                </section>
              );
            })}
          </Card>
          {stageIdx < STRATEGY_STAGES.length - 1 && (
            <div className="strategy-flow-arrow" aria-hidden="true">
              ↓
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
