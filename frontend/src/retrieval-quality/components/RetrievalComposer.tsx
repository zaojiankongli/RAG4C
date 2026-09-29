import { useState, type CSSProperties } from "react";
import { Alert, Button, Card, Input, Space } from "../../ui";
import { AddIcon, PlayCircleIcon } from "tdesign-icons-react";
import type { ComposerDraft, RetrievalVariantDraft, RunRetrievalRequest } from "../model/contracts";
import { validateComposer, type ComposerErrors } from "../model/validation";
import StrategyCard from "./StrategyCard";

const SAMPLE_QUESTIONS = [
  "公司的报销流程是什么？",
  "员工年假制度是怎么规定的？",
  "系统如何处理资料不足的问题？",
];
interface Props {
  value: ComposerDraft;
  running: boolean;
  onChange: (value: ComposerDraft) => void;
  onRun: (payload: RunRetrievalRequest) => void | Promise<void>;
}
let cardSequence = 0;
function nextId() {
  cardSequence += 1;
  return `rq-variant-${cardSequence}`;
}
function inputValue(event: { target?: { value?: unknown } }) {
  return String(event?.target?.value ?? "");
}

export default function RetrievalComposer({ value, running, onChange, onRun }: Props) {
  const [errors, setErrors] = useState<ComposerErrors>({});
  const updateVariant = (index: number, next: RetrievalVariantDraft) =>
    onChange({
      ...value,
      variants: value.variants.map((item, itemIndex) => (itemIndex === index ? next : item)),
    });
  const duplicate = (index: number) => {
    if (value.variants.length >= 4) return;
    const source = value.variants[index];
    onChange({
      ...value,
      variants: [
        ...value.variants.slice(0, index + 1),
        { ...source, clientId: nextId() },
        ...value.variants.slice(index + 1),
      ],
    });
  };
  const remove = (index: number) => {
    if (value.variants.length <= 1) return;
    onChange({ ...value, variants: value.variants.filter((_, itemIndex) => itemIndex !== index) });
    requestAnimationFrame(() =>
      (
        document.querySelectorAll<HTMLElement>(".rq-strategy-card input")[Math.max(0, index - 1)] ??
        document.querySelector<HTMLElement>(".rq-add-strategy")
      )?.focus(),
    );
  };
  const add = () => {
    if (value.variants.length >= 4) return;
    const number = value.variants.length + 1;
    onChange({
      ...value,
      variants: [
        ...value.variants,
        {
          ...value.variants[0],
          clientId: nextId(),
          name: `策略 ${String.fromCharCode(64 + number)}`,
        },
      ],
    });
  };
  const submit = () => {
    const result = validateComposer(value);
    if (!result.ok) {
      setErrors(result.errors);
      requestAnimationFrame(() =>
        document.querySelector<HTMLElement>('.rq-composer [aria-invalid="true"]')?.focus(),
      );
      return;
    }
    setErrors({});
    void onRun(result.value);
  };

  return (
    <section className="rq-composer" aria-labelledby="rq-composer-title">
      <Card bordered>
        <div className="rq-section-heading">
          <div>
            <span className="rq-eyebrow">PURE RETRIEVAL / NO GENERATION</span>
            <h2 id="rq-composer-title">配置检索对比</h2>
            <p>只执行召回与重排，不生成答案。</p>
          </div>
        </div>
        {errors.variantsRoot ? (
          <Alert theme="error" title="策略配置无效" message={errors.variantsRoot} />
        ) : null}
        <label className="rq-query-field">
          <span>检索问题</span>
          <Input.TextArea
            aria-label="检索问题"
            aria-invalid={Boolean(errors.query)}
            value={value.query}
            maxLength={20000}
            autoSize={{ minRows: 3, maxRows: 8 }}
            onChange={(event: { target?: { value?: unknown } }) =>
              onChange({ ...value, query: inputValue(event) })
            }
          />
          {errors.query ? <small role="alert">{errors.query}</small> : null}
        </label>
        <div className="rq-samples" aria-label="样例问题">
          {SAMPLE_QUESTIONS.map((question) => (
            <Button
              key={question}
              type="text"
              size="small"
              aria-label={`样例：${question}`}
              onClick={() => onChange({ ...value, query: question })}
            >
              {question}
            </Button>
          ))}
        </div>
        <label className="rq-acl-field">
          <span>ACL（逗号分隔，可选）</span>
          <Input
            aria-label="检索 ACL"
            aria-invalid={Boolean(errors.acl && Object.keys(errors.acl).length)}
            value={value.acl.join(", ")}
            onChange={(event: { target?: { value?: unknown } }) =>
              onChange({ ...value, acl: inputValue(event).split(",") })
            }
          />
          {errors.acl ? <small role="alert">ACL 包含无效项</small> : null}
        </label>
      </Card>
      <div
        className="rq-strategy-grid"
        style={{ "--rq-variant-count": value.variants.length } as CSSProperties}
      >
        {value.variants.map((item, index) => (
          <StrategyCard
            key={item.clientId}
            value={item}
            index={index}
            errors={errors.variants?.[index]}
            canRemove={value.variants.length > 1}
            canDuplicate={value.variants.length < 4}
            onChange={(next) => updateVariant(index, next)}
            onDuplicate={() => duplicate(index)}
            onRemove={() => remove(index)}
          />
        ))}
      </div>
      <div className="rq-composer-actions">
        <Button
          className="rq-add-strategy"
          icon={<AddIcon />}
          disabled={value.variants.length >= 4}
          onClick={add}
        >
          添加策略
        </Button>
        <Space />
        <Button
          type="primary"
          size="large"
          loading={running}
          icon={<PlayCircleIcon />}
          onClick={submit}
        >
          运行纯检索对比（不生成答案）
        </Button>
      </div>
    </section>
  );
}
