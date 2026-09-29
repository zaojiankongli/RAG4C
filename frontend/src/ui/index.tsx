/* eslint-disable @typescript-eslint/no-explicit-any, react-refresh/only-export-components -- typed compatibility boundary over TDesign APIs */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  Alert as TAlert,
  Button as TButton,
  Card as TCard,
  Checkbox as TCheckbox,
  Col as TCol,
  Collapse as TCollapse,
  ConfigProvider as TConfigProvider,
  Dialog as TDialog,
  Drawer as TDrawer,
  Empty as TEmpty,
  Input as TInput,
  InputNumber as TInputNumber,
  Layout as TLayout,
  Loading,
  Menu as TMenu,
  Popconfirm as TPopconfirm,
  Popup,
  Progress as TProgress,
  Radio as TRadio,
  Row as TRow,
  Select as TSelect,
  Skeleton as TSkeleton,
  Space as TSpace,
  Statistic as TStatistic,
  Switch as TSwitch,
  Tabs as TTabs,
  Tag as TTag,
  Textarea,
  Tooltip as TTooltip,
  Typography as TTypography,
} from "tdesign-react";
import { message } from "./feedback";
import { uiRendererAdapter } from "./rendererPolicy";

const sizeOf = (size: any): any => (size === "middle" ? "medium" : size);
const ariaDataProps = (props: Record<string, unknown>): Record<string, unknown> =>
  Object.fromEntries(
    Object.entries(props).filter(([key]) => key.startsWith("aria-") || key.startsWith("data-")),
  );
const spaceGap = (size: unknown): string | number | undefined => {
  if (typeof size === "number") return size;
  if (Array.isArray(size)) return size.map((item) => spaceGap(item) ?? 0).join("px ") + "px";
  if (size === "small") return 8;
  if (size === "large") return 24;
  if (size === "middle" || size === "medium") return 16;
  return undefined;
};
const isCardRootProp = (key: string): boolean =>
  key === "id" ||
  key === "role" ||
  key === "tabIndex" ||
  key.startsWith("aria-") ||
  key.startsWith("data-") ||
  /^on[A-Z]/.test(key);
const splitCardRootProps = (
  props: Record<string, unknown>,
): { root: Record<string, unknown>; component: Record<string, unknown> } => {
  const root: Record<string, unknown> = {};
  const component: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(props)) {
    (isCardRootProp(key) ? root : component)[key] = value;
  }
  return { root, component };
};
export const Button: any = React.forwardRef<any, any>(function Button(
  { type, danger, size, htmlType, block, ghost, loading, className, children, icon, ...props },
  ref,
) {
  const theme = danger ? "danger" : type === "primary" ? "primary" : "default";
  const variant =
    type === "text" || type === "link"
      ? "text"
      : ghost
        ? "outline"
        : type === "dashed"
          ? "dashed"
          : type === "primary"
            ? "base"
            : "outline";
  if (!uiRendererAdapter.useTDesign("button"))
    return (
      <button
        ref={ref}
        {...props}
        disabled={props.disabled || loading}
        type={htmlType ?? "button"}
        className={[
          "rag-button",
          `is-${type ?? "default"}`,
          danger ? "is-danger" : "",
          size ? `is-${size}` : "",
          block ? "is-block" : "",
          className,
        ]
          .filter(Boolean)
          .join(" ")}
      >
        {loading ? <span className="rag-button-spinner" /> : icon}
        {children}
      </button>
    );
  return (
    <TButton
      ref={ref}
      {...props}
      className={className}
      loading={loading}
      type={htmlType}
      block={block}
      size={sizeOf(size)}
      theme={theme}
      variant={variant}
      icon={icon}
    >
      {children}
    </TButton>
  );
});

export const Card: any = React.forwardRef<any, any>(function Card(
  {
    children,
    bodyStyle,
    styles,
    size,
    hoverable,
    title,
    header,
    extra,
    bordered = true,
    className,
    style,
    ...props
  },
  ref,
) {
  const body = bodyStyle ?? styles?.body;
  const cardHeader = header || title;
  if (!uiRendererAdapter.useTDesign("card"))
    return (
      <div
        ref={ref}
        {...props}
        className={[
          "rag-card",
          bordered === false ? "is-borderless" : "",
          className,
        ]
          .filter(Boolean)
          .join(" ")}
        style={style}
      >
        {(cardHeader || extra) && (
          <div className="rag-card-header">
            {header ? header : <strong>{title}</strong>}
            <span>{extra}</span>
          </div>
        )}
        <div className="rag-card-body" style={body}>
          {children}
        </div>
      </div>
    );
  const { root: rootProps, component: componentProps } = splitCardRootProps(props);
  const tdesignCard = (
    <TCard
      ref={Object.keys(rootProps).length ? undefined : ref}
      {...componentProps}
      title={header ? undefined : title}
      header={header}
      actions={extra}
      bordered={bordered}
      bodyStyle={body}
      size={sizeOf(size)}
      hoverShadow={hoverable}
      className={className}
      style={style}
    >
      {children}
    </TCard>
  );
  if (Object.keys(rootProps).length)
    return (
      <div ref={ref} {...rootProps}>
        {tdesignCard}
      </div>
    );
  return (
    tdesignCard
  );
});

export const Alert: any = ({
  type,
  theme: themeProp,
  title: titleProp,
  message: messageProp,
  description,
  operation,
  showIcon: _showIcon = true,
  className,
  id,
  role,
  style,
  ...props
}: any) => {
  const alertTheme = type ?? themeProp ?? "info";
  const alertTitle =
    titleProp !== undefined && titleProp !== null ? titleProp : messageProp;
  const alertDescription =
    titleProp !== undefined && titleProp !== null ? messageProp ?? description : description;
  if (!uiRendererAdapter.useTDesign("alert"))
    return (
    <div
      {...ariaDataProps(props)}
      id={id}
      role={role ?? "alert"}
      style={style}
      className={["rag-alert", `is-${alertTheme}`, className].filter(Boolean).join(" ")}
    >
      {titleProp !== undefined && titleProp !== null ? <strong>{alertTitle}</strong> : alertTitle}
      {alertDescription ? <div>{alertDescription}</div> : null}
      {operation ? <div className="rag-alert-operation">{operation}</div> : null}
    </div>
    );
  return (
    <TAlert
      {...props}
      id={id}
      role={role ?? "alert"}
      style={style}
      className={className}
      icon={typeof document === "undefined" ? false : undefined}
      theme={alertTheme === "error" ? "error" : alertTheme}
      title={
        titleProp !== undefined && titleProp !== null
          ? titleProp
          : description
            ? messageProp
            : undefined
      }
      message={description ?? messageProp ?? titleProp}
      operation={operation}
      maxLine={0}
    />
  );
};
export const Empty: any = ({
  type,
  title,
  description,
  image,
  children,
  className,
  id,
  role,
  tabIndex,
  style,
  ...props
}: any) => {
  if (!uiRendererAdapter.useTDesign("empty"))
    return (
      <div
        {...ariaDataProps(props)}
        id={id}
        role={role}
        tabIndex={tabIndex}
        className={["rag-empty", className].filter(Boolean).join(" ")}
        style={style}
      >
        {image ? <div className="rag-empty-image">{image}</div> : null}
        {title ? <strong className="rag-empty-title">{title}</strong> : null}
        {description ? <p className="rag-empty-description">{description}</p> : null}
        {children}
      </div>
    );
  const rootProps = {
    ...ariaDataProps(props),
    ...(id === undefined ? {} : { id }),
    ...(role === undefined ? {} : { role }),
    ...(tabIndex === undefined ? {} : { tabIndex }),
  };
  const tdesignProps = { ...props };
  for (const key of Object.keys(rootProps)) delete tdesignProps[key];
  const tdesignEmpty = (
    <TEmpty
      {...tdesignProps}
      type={type}
      title={title}
      description={description}
      image={image}
      className={className}
      style={style}
    >
      {children}
    </TEmpty>
  );
  return Object.keys(rootProps).length ? <div {...rootProps}>{tdesignEmpty}</div> : tdesignEmpty;
};
export const Checkbox: any = ({
  checked = false,
  onChange,
  disabled = false,
  children,
  className,
  id,
  name,
  value,
  title,
  tabIndex,
  required,
  autoFocus,
  readOnly,
  onClick,
  ...props
}: any) => {
  if (!uiRendererAdapter.useTDesign("checkbox"))
    return (
      <label className={["rag-checkbox", className].filter(Boolean).join(" ")}>
        <input
          {...ariaDataProps(props)}
          id={id}
          name={name}
          value={value}
          title={title}
          type="checkbox"
          checked={Boolean(checked)}
          disabled={Boolean(disabled)}
          tabIndex={tabIndex}
          required={required}
          autoFocus={autoFocus}
          readOnly={readOnly}
          onClick={onClick}
          onChange={(event) => onChange?.(event.target.checked)}
        />
        <span>{children}</span>
      </label>
    );
  return (
    <TCheckbox
      {...props}
      id={id}
      name={name}
      value={value}
      title={title}
      checked={checked}
      disabled={disabled}
      tabIndex={tabIndex}
      required={required}
      autoFocus={autoFocus}
      readOnly={readOnly}
      onClick={onClick}
      className={className}
      onChange={(value: boolean) => onChange?.(value)}
    >
      {children}
    </TCheckbox>
  );
};
export const Tooltip: any = ({ title, children, trigger = "hover", ...props }: any) => {
  const [visible, setVisible] = useState(false);
  const tooltipId = React.useId();
  const triggers = Array.isArray(trigger) ? trigger : [trigger];
  const triggerProps = {
    className: "rag-tooltip-trigger",
    "aria-describedby": visible ? tooltipId : undefined,
    onMouseEnter: () => {
      if (triggers.includes("hover")) setVisible(true);
    },
    onMouseLeave: () => {
      if (triggers.includes("hover")) setVisible(false);
    },
    onFocus: () => {
      if (triggers.includes("focus")) setVisible(true);
    },
    onBlur: () => {
      if (triggers.includes("focus")) setVisible(false);
    },
  };
  if (uiRendererAdapter.useTDesign("tooltip"))
    return (
      <TTooltip
        {...props}
        visible={visible}
        onVisibleChange={setVisible}
        trigger="hover"
        content={
          <span id={tooltipId} role="tooltip">
            {title}
          </span>
        }
      >
        <span {...triggerProps}>{children}</span>
      </TTooltip>
    );
  return (
    <span {...triggerProps}>
      {children}
      {visible && title ? (
        <span id={tooltipId} role="tooltip" className="rag-tooltip-content">
          {title}
        </span>
      ) : null}
    </span>
  );
};
export const Popover: any = ({ content, children, trigger = "hover", ...props }: any) =>
  !uiRendererAdapter.useTDesign("popover") ? (
    <>{children}</>
  ) : (
    <Popup {...props} content={content} trigger={trigger}>
      {children}
    </Popup>
  );
function CompatPopconfirm({
  title,
  description,
  okText = "确定",
  cancelText = "取消",
  onConfirm,
  children,
  ...props
}: any) {
  const [open, setOpen] = useState(false);
  if (uiRendererAdapter.useTDesign("popconfirm"))
    return (
      <TPopconfirm
        {...props}
        content={
          <>
            <strong>{title}</strong>
            {description ? <div>{description}</div> : null}
          </>
        }
        confirmBtn={okText}
        cancelBtn={cancelText}
        onConfirm={onConfirm}
      >
        {children}
      </TPopconfirm>
    );
  return (
    <>
      <span
        className="rag-confirm-trigger"
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          setOpen(true);
        }}
      >
        {children}
      </span>
      {open &&
        typeof document !== "undefined" &&
        createPortal(
          <div className="rag-confirm-mask" onMouseDown={() => setOpen(false)}>
            <div
              role="dialog"
              aria-modal="true"
              className="rag-confirm"
              onMouseDown={(event) => event.stopPropagation()}
            >
              <div className="rag-confirm-icon">!</div>
              <div>
                <h3>{title}</h3>
                {description && <p>{description}</p>}
                <div className="rag-confirm-actions">
                  <button type="button" className="rag-button" onClick={() => setOpen(false)}>
                    {cancelText}
                  </button>
                  <button
                    type="button"
                    className="rag-button is-primary is-danger"
                    onClick={() => {
                      setOpen(false);
                      onConfirm?.();
                    }}
                  >
                    {okText}
                  </button>
                </div>
              </div>
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}
export const Popconfirm: any = CompatPopconfirm;

const CompatTextArea = React.forwardRef<any, any>(function CompatTextArea(
  { onChange, onPressEnter, autoSize, className, maxLength, ...props },
  ref,
) {
  if (!uiRendererAdapter.useTDesign("input-text-area"))
    return (
      <textarea
        ref={ref}
        {...props}
        maxLength={maxLength}
        className={["rag-textarea", className].filter(Boolean).join(" ")}
        style={{
          ...props.style,
          minHeight: autoSize?.minRows ? `${autoSize.minRows * 24}px` : props.style?.minHeight,
        }}
        onChange={(event) => onChange?.(event)}
        onKeyDown={(event) => {
          if (event.key === "Enter") onPressEnter?.(event);
        }}
      />
    );
  return (
    <Textarea
      ref={ref}
      {...props}
      maxlength={maxLength}
      autosize={autoSize}
      onChange={(value, context) => onChange?.({ target: { value }, nativeEvent: context?.e })}
      onKeydown={(_value, context) => {
        if (context?.e?.key === "Enter") onPressEnter?.(context.e);
      }}
    />
  );
});
const CompatSearch = React.forwardRef<any, any>(function CompatSearch(
  { enterButton, onSearch, onChange, className, maxLength, ...props },
  ref,
) {
  if (!uiRendererAdapter.useTDesign("input-search"))
    return (
      <input
        ref={ref}
        {...props}
        maxLength={maxLength}
        className={["rag-input", className].filter(Boolean).join(" ")}
        onChange={onChange}
        onKeyDown={(event) => {
          if (event.key === "Enter") onSearch?.(event.currentTarget.value, event);
        }}
      />
    );
  return (
    <TInput
      ref={ref}
      {...props}
      maxlength={maxLength}
      suffixIcon={enterButton}
      onChange={(value, context) => onChange?.({ target: { value }, nativeEvent: context?.e })}
      onEnter={(value, context) => onSearch?.(value, context)}
    />
  );
});
export const Input: any = Object.assign(
  React.forwardRef<any, any>(function Input(
    { allowClear, prefix, onPressEnter, onChange, className, maxLength, ...props },
    ref,
  ) {
    const tdesignRef = React.useRef<any>(null);
    const previousInnerProps = React.useRef<Record<string, unknown>>({});
    const assignTDesignRef = (value: any) => {
      tdesignRef.current = value;
      if (typeof ref === "function") ref(value);
      else if (ref) ref.current = value;
    };
    const { id, ...tdesignProps } = props;
    const innerDomProps = {
      ...ariaDataProps(tdesignProps),
      ...(id === undefined ? {} : { id }),
    };
    const tdesignComponentProps = { ...tdesignProps };
    for (const key of Object.keys(innerDomProps)) delete tdesignComponentProps[key];
    React.useLayoutEffect(() => {
      if (!uiRendererAdapter.useTDesign("input")) return;
      const input = tdesignRef.current?.inputElement as HTMLInputElement | undefined;
      if (!input) return;
      for (const key of Object.keys(previousInnerProps.current)) input.removeAttribute(key);
      for (const [key, value] of Object.entries(innerDomProps)) {
        if (value === undefined || value === null || value === false) continue;
        input.setAttribute(key, String(value));
      }
      previousInnerProps.current = innerDomProps;
    });
    if (!uiRendererAdapter.useTDesign("input"))
      return (
        <input
          ref={ref}
          {...props}
          maxLength={maxLength}
          className={["rag-input", className].filter(Boolean).join(" ")}
          onChange={onChange}
          onKeyDown={(event) => {
            if (event.key === "Enter") onPressEnter?.(event);
          }}
        />
      );
    return (
      <TInput
        ref={assignTDesignRef}
        {...tdesignComponentProps}
        maxlength={maxLength}
        clearable={allowClear}
        prefixIcon={prefix}
        onChange={(value, context) => onChange?.({ target: { value }, nativeEvent: context?.e })}
        onEnter={(value, context) => onPressEnter?.(context?.e ?? value)}
      />
    );
  }),
  { TextArea: CompatTextArea, Search: CompatSearch },
);

export const Select: any = React.forwardRef<any, any>(function Select(
  {
    allowClear,
    showSearch,
    onChange,
    mode,
    options = [],
    className,
    value,
    defaultValue,
    disabled,
    id,
    name,
    title,
    style,
    tabIndex,
    required,
    autoFocus,
    onBlur,
    onFocus,
    onKeyDown,
    onKeyUp,
    loading: _loading,
    placeholder: _placeholder,
    maxTagCount: _maxTagCount,
    popupProps: _popupProps,
    filterOption: _filterOption,
    ...props
  },
  ref,
) {
  const multiple = mode === "multiple" || props.multiple === true;
  if (!uiRendererAdapter.useTDesign("select")) {
    const nativeValue =
      value === undefined
        ? undefined
        : multiple
          ? (Array.isArray(value) ? value : [value]).map(String)
          : String(value);
    const nativeDefaultValue =
      defaultValue === undefined
        ? undefined
        : multiple
          ? (Array.isArray(defaultValue) ? defaultValue : [defaultValue]).map(String)
          : String(defaultValue);
    return (
      <select
        ref={ref}
        {...ariaDataProps(props)}
        id={id}
        name={name}
        title={title}
        style={style}
        tabIndex={tabIndex}
        required={required}
        autoFocus={autoFocus}
        disabled={disabled}
        multiple={multiple}
        className={["rag-select", className].filter(Boolean).join(" ")}
        value={nativeValue}
        defaultValue={nativeDefaultValue}
        onBlur={onBlur}
        onFocus={onFocus}
        onKeyDown={onKeyDown}
        onKeyUp={onKeyUp}
        onChange={(event) =>
          onChange?.(
            multiple
              ? Array.from(event.currentTarget.selectedOptions, (option) => option.value)
              : event.currentTarget.value,
          )
        }
      >
        {options.map((option: any) => (
          <option key={String(option.value)} value={String(option.value)} disabled={option.disabled}>
            {option.label}
          </option>
        ))}
      </select>
    );
  }
  return (
    <TSelect
      ref={ref}
      {...props}
      className={className}
      value={value}
      defaultValue={defaultValue}
      disabled={disabled}
      options={options}
      multiple={multiple}
      clearable={allowClear}
      filterable={showSearch}
      onChange={(nextValue, context) => onChange?.(nextValue, context)}
    />
  );
});
/* InputNumber 原先是 TDesign 的直连别名，也是本文件里唯一一个绕过 facade 的表单控件
   （另两个直连别名 Statistic / ConfigProvider 不是表单控件，不受影响）。
   后果与下面 Switch 同族，但更隐蔽、且无法靠"转发 props"修好：
   TDesign 把 restProps 摊到外层 div（tdesign-react/esm/input/Input.js:407-411），
   而内层 `input.t-input__inner` 的属性表是写死闭合的（同文件 233-253：无 aria-*、无 id、无展开），
   整个 input/ 目录 grep "aria" 命中数为 0——调用方写的 `aria-label` 物理上到不了真正的输入框。
   视觉门禁实测：/config 残留的 81 个无可访问名控件全部是 `input.t-input__inner`，
   即 9 个实例 × 9 次页面加载（3 主题 × 3 视口）。
   兜底分支必须沿用 TDesign 的 `onChange(value)` 签名，不能退回原生 `onChange(event)`；
   `size="small"` 靠 ariaDataProps 过滤掉——原生 input 的 size 是字符宽度，会直接改坏布局。 */
export const InputNumber: any = ({
  value,
  onChange,
  min,
  max,
  step,
  disabled,
  style,
  className,
  id,
  inputProps,
  ...props
}: any) => {
  const tdesignRef = React.useRef<any>(null);
  const previousInnerProps = React.useRef<Record<string, unknown>>({});
  const inputPropsRecord =
    inputProps && typeof inputProps === "object" ? (inputProps as Record<string, unknown>) : {};
  const forwardedTopLevelProps = ariaDataProps(props);
  const innerDomProps = {
    ...ariaDataProps(inputPropsRecord),
    ...forwardedTopLevelProps,
    ...(id === undefined ? {} : { id }),
  };
  const tdesignProps = { ...props };
  for (const key of Object.keys(forwardedTopLevelProps)) delete tdesignProps[key];
  React.useLayoutEffect(() => {
    if (!uiRendererAdapter.useTDesign("input-number")) return;
    const nestedInputRef = tdesignRef.current?.inputElement;
    const input = (nestedInputRef?.inputElement ?? nestedInputRef) as
      | HTMLInputElement
      | undefined;
    if (!input || typeof input.setAttribute !== "function") return;
    for (const key of Object.keys(previousInnerProps.current)) input.removeAttribute(key);
    for (const [key, current] of Object.entries(innerDomProps)) {
      if (current === undefined || current === null || current === false) continue;
      input.setAttribute(key, String(current));
    }
    previousInnerProps.current = innerDomProps;
  });
  if (!uiRendererAdapter.useTDesign("input-number")) {
    return (
      <input
        type="number"
        {...forwardedTopLevelProps}
        id={id}
        className={["rag-input", className].filter(Boolean).join(" ")}
        style={style}
        min={min}
        max={max}
        step={step}
        disabled={Boolean(disabled)}
        value={value ?? ""}
        onChange={(event) => {
          const raw = event.target.value;
          onChange?.(raw === "" ? undefined : Number(raw));
        }}
      />
    );
  }
  return (
    <TInputNumber
      ref={tdesignRef}
      {...tdesignProps}
      className={className}
      style={style}
      min={min}
      max={max}
      step={step}
      disabled={disabled}
      value={value}
      inputProps={inputProps}
      onChange={onChange}
    />
  );
};
export const Switch: any = ({ checked, onChange, disabled, ...props }: any) =>
  !uiRendererAdapter.useTDesign("switch") ? (
    /* 兜底分支原先只取 checked/onChange，其余全部静默丢弃，两个后果：
       1) `aria-label` 丢失 → 视觉门禁实测 /config 270 个里的 189 个"无可访问名控件"全部来自这里
          （调用方写的是 `<Switch aria-label="启用 xxx" />`，作者做对了但被吞掉）；
       2) `disabled` 丢失 → EngineBoard 的 `disabled={!p.configured}` 失效，
          未配置的服务提供者开关其实可以点。
       只转发 aria 与 data 两类前缀属性：`size="small"` 这类不能直接落到原生 checkbox 上。 */
    <input
      type="checkbox"
      {...ariaDataProps(props)}
      disabled={Boolean(disabled)}
      checked={Boolean(checked)}
      onChange={(event) => onChange?.(event.target.checked)}
    />
  ) : (
    <TSwitch {...props} disabled={disabled} value={checked} onChange={onChange} />
  );
export const Spin: any = ({ spinning = true, children, tip, size, ...props }: any) =>
  !uiRendererAdapter.useTDesign("spin") ? (
    <div
      {...ariaDataProps(props)}
      className={["rag-spin", size ? `is-${size}` : ""].filter(Boolean).join(" ")}
      role={spinning ? "status" : undefined}
      aria-busy={spinning}
      aria-live="polite"
    >
      {spinning ? <span className="rag-spin-indicator" aria-hidden="true" /> : null}
      {spinning && tip ? <span className="rag-spin-tip">{tip}</span> : null}
      {children}
    </div>
  ) : (
    <Loading {...props} loading={spinning} text={tip} size={sizeOf(size)}>
      {children}
    </Loading>
  );
export const Progress: any = ({
  percent = 0,
  status,
  className,
  showInfo: _showInfo,
  strokeColor,
  trailColor,
  format: _format,
  type: _type,
  size: _size,
  strokeWidth,
  success: _success,
  steps: _steps,
  width: _width,
  gapDegree: _gapDegree,
  gapPosition: _gapPosition,
  style,
  ...props
}: any) => {
  const safePercent = Math.max(0, Math.min(100, Number(percent) || 0));
  const progressStatus =
    status === "exception" ? "error" : status === "success" ? "success" : "active";
  if (!uiRendererAdapter.useTDesign("progress"))
    return (
      <div
        {...ariaDataProps(props)}
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={safePercent}
        className={["rag-progress", `is-${progressStatus}`, className].filter(Boolean).join(" ")}
        style={{
          ...style,
          backgroundColor: typeof trailColor === "string" ? trailColor : style?.backgroundColor,
          height: typeof strokeWidth === "number" ? strokeWidth : style?.height,
        }}
      >
        <span
          style={{
            width: `${safePercent}%`,
            backgroundColor: typeof strokeColor === "string" ? strokeColor : undefined,
          }}
        />
      </div>
    );
  return (
    <TProgress
      {...props}
      className={className}
      style={style}
      percentage={safePercent}
      status={progressStatus}
      color={strokeColor}
      trackColor={trailColor}
    />
  );
};
const skeletonRows = (paragraph: unknown): number => {
  if (paragraph === false) return 0;
  const rows =
    paragraph !== null && typeof paragraph === "object"
      ? (paragraph as { rows?: unknown }).rows
      : undefined;
  const parsed = Number(rows);
  return Number.isFinite(parsed) ? Math.max(0, Math.floor(parsed)) : 3;
};

export const Skeleton: any = ({
  active = true,
  title = true,
  paragraph,
  className,
  style,
  id,
  role,
  tabIndex,
  ...props
}: any) => {
  const rows = skeletonRows(paragraph);
  if (!uiRendererAdapter.useTDesign("skeleton")) {
    return (
      <div
        {...ariaDataProps(props)}
        id={id}
        role={role}
        tabIndex={tabIndex}
        className={["rag-skeleton", active === false ? "" : "is-active", className]
          .filter(Boolean)
          .join(" ")}
        style={style}
        aria-busy="true"
      >
        {title !== false ? <span className="rag-skeleton-title rag-skeleton-line" /> : null}
        {Array.from({ length: rows }, (_, index) => (
          <span key={index} className="rag-skeleton-line" />
        ))}
      </div>
    );
  }
  return (
    <TSkeleton
      {...props}
      id={id}
      role={role}
      tabIndex={tabIndex}
      animation="gradient"
      rowCol={[
        ...(title === false ? [] : [{}]),
        ...Array.from({ length: rows }, () => ({})),
      ]}
      className={className}
      style={style}
    />
  );
};

const COLOR_THEME: Record<string, string> = {
  success: "success",
  error: "danger",
  warning: "warning",
  processing: "primary",
  blue: "primary",
  green: "success",
  red: "danger",
  orange: "warning",
};
const TAG_THEMES = new Set(["default", "primary", "success", "warning", "danger"]);
export const Tag: any = ({
  color,
  theme: themeProp,
  variant,
  icon,
  children,
  bordered,
  size,
  className,
  ...props
}: any) => {
  const requestedTheme = COLOR_THEME[color] ?? themeProp ?? "default";
  const theme = TAG_THEMES.has(requestedTheme) ? requestedTheme : "default";
  const nativeVariant =
    variant === "light" || variant === "light-outline" || variant === "outline"
      ? variant
      : "";
  if (!uiRendererAdapter.useTDesign("tag"))
    return (
      <span
        {...props}
        className={[
          "rag-tag",
          `is-${theme}`,
          nativeVariant ? `is-${nativeVariant}` : "",
          size ? `is-${size}` : "",
          bordered === false ? "is-borderless" : "",
          className,
        ]
          .filter(Boolean)
          .join(" ")}
      >
        {icon}
        {children}
      </span>
    );
  return (
    <TTag
      {...props}
      icon={icon}
      theme={theme as any}
      variant={variant ?? (bordered === false ? "light" : "light-outline")}
      size={size}
      className={className}
    >
      {children}
    </TTag>
  );
};
// TDesign Typography 的**私有** props。fallback 分支直接 `{...props}` 会把它们当 DOM
// 属性透传，React 对布尔值报 "Received `true` for a non-boolean attribute `code`"。
// 真机复现：`node scripts/shoot.mjs` 在 2 主题 × 3 视口下**每一张**截图都报同一条
// （6 条去重后仍 6 条），来源是 EvalPage 的 `<Text code>`。
// 这里统一把它们摘出来：给了 TDesign 就原样转发，走 fallback 就只在 **有语义映射** 的
// 情况下译为原生元素（code → <code>、delete → line-through），其余丢弃。
const TEXT_INLINE_PROPS = [
  "code",
  "delete",
  "underline",
  "mark",
  "keyboard",
  "italic",
  "disabled",
] as const;

function splitInlineProps(props: any): { inline: any; dom: any } {
  const inline: any = {};
  const dom: any = {};
  for (const [key, value] of Object.entries(props)) {
    if ((TEXT_INLINE_PROPS as readonly string[]).includes(key)) inline[key] = value;
    else dom[key] = value;
  }
  return { inline, dom };
}

const Text: any = ({ type, strong, style, children, ...props }: any) => {
  const mergedStyle = strong ? { ...style, fontWeight: 600 } : style;
  const { inline, dom } = splitInlineProps(props);
  if (!uiRendererAdapter.useTDesign("text")) {
    // code 用真 <code>：既拿到原生等宽语义（读屏器也会念作代码），也不会透传布尔属性。
    // 类名 rag-inline-code 让 fallback 的 <code> 与 TDesign 分支的 `.t-typography code`
    // 共用同一条样式（styles.css 里两者并列），否则同一个 code 语义在两条分支下长得不一样。
    const forcedClass = inline.code
      ? ["rag-inline-code", dom.className].filter(Boolean).join(" ")
      : dom.className;
    const Tag: any = inline.code ? "code" : "span";
    const fallbackStyle = inline.delete
      ? { ...mergedStyle, textDecoration: "line-through" }
      : mergedStyle;
    return (
      <Tag {...dom} className={forcedClass} style={fallbackStyle}>
        {children}
      </Tag>
    );
  }
  return (
    <TTypography.Text
      {...dom}
      {...inline}
      style={mergedStyle}
      theme={type === "danger" ? "error" : type}
    >
      {children}
    </TTypography.Text>
  );
};
const Title: any = ({ level = 1, children, ...props }: any) => {
  if (uiRendererAdapter.useTDesign("title"))
    return <TTypography.Title {...props} level={`h${level}` as any} />;
  const { inline, dom } = splitInlineProps(props);
  return React.createElement(`h${level}`, { ...dom, ...(inline.delete ? { style: { textDecoration: "line-through" } } : {}) }, children);
};
const Paragraph: any = ({ type, ...props }: any) => {
  if (uiRendererAdapter.useTDesign("paragraph")) {
    return <TTypography.Paragraph {...props} theme={type === "danger" ? "error" : type} />;
  }
  return <p {...splitInlineProps(props).dom} />;
};
export const Typography: any = { ...TTypography, Text, Title, Paragraph };
export const Space: any = ({
  children,
  wrap,
  align,
  direction = "horizontal",
  size,
  split: _split,
  className,
  style,
  id,
  role,
  title,
  tabIndex,
  onClick,
  onKeyDown,
  ...props
}: any) =>
  !uiRendererAdapter.useTDesign("space") ? (
    <div
      {...ariaDataProps(props)}
      id={id}
      role={role}
      title={title}
      tabIndex={tabIndex}
      onClick={onClick}
      onKeyDown={onKeyDown}
      className={[
        "rag-space",
        direction === "vertical" ? "is-vertical" : "is-horizontal",
        wrap ? "is-wrap" : "",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      style={{
        display: "flex",
        flexDirection: direction === "vertical" ? "column" : "row",
        flexWrap: wrap ? "wrap" : undefined,
        alignItems: align,
        gap: spaceGap(size),
        ...style,
      }}
    >
      {children}
    </div>
  ) : (
    <TSpace
      {...props}
      className={className}
      style={style}
      align={align}
      direction={direction}
      size={size}
      breakLine={wrap}
    >
      {children}
    </TSpace>
  );
export const Col: any = ({ xs = 24, md = 24, children, className, ...props }: any) =>
  !uiRendererAdapter.useTDesign("col") ? (
    <div
      {...props}
      className={["rag-col", className].filter(Boolean).join(" ")}
      style={{ ...props.style, "--rag-col-xs": xs, "--rag-col-md": md }}
    >
      {children}
    </div>
  ) : (
    <TCol xs={xs} md={md} className={className} {...props}>
      {children}
    </TCol>
  );
export const Row: any = ({ children, gutter, className, ...props }: any) =>
  !uiRendererAdapter.useTDesign("row") ? (
    <div
      {...props}
      className={["rag-row", className].filter(Boolean).join(" ")}
      style={{ ...props.style, gap: Array.isArray(gutter) ? gutter[0] : gutter }}
    >
      {children}
    </div>
  ) : (
    <TRow gutter={gutter} className={className} {...props}>
      {children}
    </TRow>
  );
const CompatStatistic: any = ({
  title,
  value,
  prefix,
  suffix,
  precision,
  formatter,
  valueStyle,
  className,
  id,
  role,
  tabIndex,
  style,
  ...props
}: any) => {
  const tdesignValueStyle =
    valueStyle && typeof valueStyle === "object" ? valueStyle : undefined;
  const tdesignColor =
    tdesignValueStyle && typeof tdesignValueStyle.color === "string"
      ? tdesignValueStyle.color
      : undefined;
  const displayValue =
    typeof formatter === "function"
      ? formatter(value)
      : typeof value === "number" && typeof precision === "number"
        ? value.toFixed(precision)
        : value;
  if (!uiRendererAdapter.useTDesign("statistic")) {
    return (
      <div
        {...ariaDataProps(props)}
        id={id}
        role={role}
        tabIndex={tabIndex}
        className={["rag-statistic", className].filter(Boolean).join(" ")}
        style={style}
      >
        {title !== undefined && title !== null ? (
          <div className="rag-statistic-title">{title}</div>
        ) : null}
        <div className="rag-statistic-content" style={valueStyle}>
          {prefix !== undefined && prefix !== null ? (
            <span className="rag-statistic-prefix">{prefix}</span>
          ) : null}
          <span className="rag-statistic-value">{displayValue}</span>
          {suffix !== undefined && suffix !== null ? (
            <span className="rag-statistic-suffix">{suffix}</span>
          ) : null}
        </div>
      </div>
    );
  }
  return (
    <TStatistic
      {...props}
      title={title}
      value={value}
      prefix={prefix}
      suffix={suffix}
      decimalPlaces={precision}
      format={formatter}
      color={tdesignColor}
      id={id}
      role={role}
      tabIndex={tabIndex}
      style={{ ...style, ...tdesignValueStyle }}
      className={className}
    />
  );
};
export const Statistic: any = CompatStatistic;
export const ConfigProvider: any = TConfigProvider;

const CompatRadioButton: any = ({ children, ...props }: any) =>
  uiRendererAdapter.useTDesign("radio-button") ? (
    <TRadio.Button {...props}>{children}</TRadio.Button>
  ) : (
    <>{children}</>
  );

const CompatRadioGroup: any = ({
  children,
  options = [],
  onChange,
  value,
  disabled,
  className,
  name,
  variant: _variant,
  theme: _theme,
  size: _size,
  ...props
}: any) => {
  const generatedName = React.useId();
  if (uiRendererAdapter.useTDesign("radio-group")) {
    return (
      <TRadio.Group
        {...props}
        className={className}
        value={value}
        disabled={disabled}
        options={options.length ? options : undefined}
        onChange={(nextValue, context) =>
          onChange?.({ target: { value: nextValue }, nativeEvent: context?.e })
        }
      >
        {children}
      </TRadio.Group>
    );
  }
  const items = options.length
    ? options
    : React.Children.toArray(children).flatMap((child) =>
        React.isValidElement<{
          value?: unknown;
          children?: React.ReactNode;
          disabled?: boolean;
        }>(child) && child.props.value !== undefined
          ? [
              {
                value: child.props.value,
                label: child.props.children,
                disabled: child.props.disabled,
              },
            ]
          : [],
      );
  return (
    <div
      {...props}
      role={props.role ?? "radiogroup"}
      className={["rag-radio-group", className].filter(Boolean).join(" ")}
    >
      {items.map((item: any) => (
        <label key={String(item.value)} className="rag-radio-option">
          <input
            type="radio"
            name={name ?? generatedName}
            value={String(item.value)}
            checked={item.value === value}
            disabled={disabled || item.disabled}
            onChange={(event) => {
              if (!event.target.checked) return;
              onChange?.({ target: { value: item.value }, nativeEvent: event });
            }}
          />
          <span>{item.label}</span>
        </label>
      ))}
    </div>
  );
};
export const Radio: any = Object.assign(TRadio, {
  Group: CompatRadioGroup,
  Button: CompatRadioButton,
});
const isSafeCollapseKey = (value: unknown): value is string | number =>
  (typeof value === "string" || typeof value === "number") &&
  String(value).length > 0 &&
  !["__proto__", "prototype", "constructor", "toString"].includes(String(value));

const normalizeCollapseItems = (items: unknown): any[] => {
  if (!Array.isArray(items)) return [];
  const safeItems: any[] = [];
  const seenKeys = new Set<string>();
  for (const candidate of items) {
    try {
      if (
        candidate === null ||
        typeof candidate !== "object" ||
        !isSafeCollapseKey((candidate as { key?: unknown }).key)
      ) {
        continue;
      }
      const item = candidate as { key: string | number };
      const normalizedKey = String(item.key);
      if (seenKeys.has(normalizedKey)) continue;
      seenKeys.add(normalizedKey);
      safeItems.push({
        key: normalizedKey,
        label: (candidate as { label?: unknown }).label,
        children: (candidate as { children?: React.ReactNode }).children,
        disabled: Boolean((candidate as { disabled?: unknown }).disabled),
      });
    } catch {
      // A hostile/untrusted item must not break the entire disclosure surface.
    }
  }
  return safeItems;
};

const normalizeCollapseValue = (
  candidate: unknown,
  validKeys: ReadonlySet<string>,
  accordion: boolean,
): string[] | undefined => {
  if (candidate === undefined) return undefined;
  const values = Array.isArray(candidate) ? candidate : candidate == null ? [] : [candidate];
  const normalized = values.map(String).filter((key) => validKeys.has(key));
  return accordion ? normalized.slice(0, 1) : Array.from(new Set(normalized));
};

const nextCollapseValue = (activeKeys: string[], key: string, accordion: boolean): string[] =>
  activeKeys.includes(key)
    ? activeKeys.filter((activeKey) => activeKey !== key)
    : accordion
      ? [key]
      : [...activeKeys, key];

const CompatCollapse: any = ({
  items = [],
  value,
  defaultValue,
  onChange,
  accordion = false,
  ghost = false,
  borderless = false,
  size,
  disabled = false,
  className,
  style,
  id,
  role,
  ...props
}: any) => {
  const safeItems = normalizeCollapseItems(items);
  const validKeys = new Set(safeItems.map((item: any) => String(item.key)));
  const normalizeKeys = (candidate: unknown): string[] => {
    return normalizeCollapseValue(candidate, validKeys, accordion) ?? [];
  };
  const controlled = value !== undefined;
  const [uncontrolledKeys, setUncontrolledKeys] = useState<string[]>(() =>
    normalizeKeys(defaultValue),
  );
  const activeKeys = normalizeKeys(controlled ? value : uncontrolledKeys);
  const toggle = (key: string) => {
    const next = nextCollapseValue(activeKeys, key, accordion);
    if (!controlled) setUncontrolledKeys(next);
    onChange?.(next);
  };
  const uid = React.useId().replace(/[^A-Za-z0-9_-]/g, "");
  return (
    <div
      {...ariaDataProps(props)}
      id={id}
      role={role}
      style={style}
      className={[
        "rag-collapse",
        ghost ? "is-ghost" : "",
        borderless ? "is-borderless" : "",
        size ? `is-${size}` : "",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      {safeItems.map((item: any, index: number) => {
        const key = String(item.key);
        const triggerId = `rag-collapse-${uid}-trigger-${index}`;
        const panelId = `rag-collapse-${uid}-panel-${index}`;
        const expanded = activeKeys.includes(key);
        return (
          <div className="rag-collapse-item" key={key}>
            <button
              type="button"
              id={triggerId}
              className="rag-collapse-trigger"
              aria-expanded={expanded}
              aria-controls={panelId}
              disabled={Boolean(disabled || item.disabled)}
              onClick={() => toggle(key)}
            >
              <span>{item.label}</span>
              <span className="rag-collapse-chevron" aria-hidden="true">
                {expanded ? "−" : "+"}
              </span>
            </button>
            <div
              id={panelId}
              className="rag-collapse-panel"
              role="region"
              aria-labelledby={triggerId}
              hidden={!expanded}
            >
              {item.children}
            </div>
          </div>
        );
      })}
    </div>
  );
};

const CompatTDesignCollapse: any = ({
  safeItems,
  validKeys,
  value,
  defaultValue,
  onChange,
  accordion,
  disabled,
  ...props
}: any) => {
  const controlled = value !== undefined;
  const [uncontrolledKeys, setUncontrolledKeys] = useState<string[]>(
    () => normalizeCollapseValue(defaultValue, validKeys, accordion) ?? [],
  );
  const activeKeys =
    normalizeCollapseValue(controlled ? value : uncontrolledKeys, validKeys, accordion) ?? [];
  const uid = React.useId().replace(/[^A-Za-z0-9_-]/g, "");
  const handleChange = (nextValue: unknown, context: unknown) => {
    const normalized = normalizeCollapseValue(nextValue, validKeys, accordion) ?? [];
    if (!controlled) setUncontrolledKeys(normalized);
    onChange?.(nextValue, context);
  };

  return (
    <TCollapse
      {...props}
      disabled={disabled}
      expandMutex={accordion}
      value={activeKeys}
      onChange={handleChange}
    >
      {safeItems.map((item: any, index: number) => {
        const key = String(item.key);
        const triggerId = `rag-collapse-${uid}-trigger-${index}`;
        const panelId = `rag-collapse-${uid}-panel-${index}`;
        const expanded = activeKeys.includes(key);
        const itemDisabled = Boolean(disabled || item.disabled);
        return (
          <TCollapse.Panel
            key={key}
            value={key}
            header={
              <span
                id={triggerId}
                role="button"
                tabIndex={itemDisabled ? -1 : 0}
                aria-expanded={expanded}
                aria-controls={panelId}
                aria-disabled={itemDisabled}
                onKeyDown={(event: React.KeyboardEvent<HTMLSpanElement>) => {
                  if (event.key !== "Enter" && event.key !== " ") return;
                  event.preventDefault();
                  event.currentTarget.click();
                }}
              >
                {item.label}
              </span>
            }
            disabled={item.disabled}
          >
            <div id={panelId} role="region" aria-labelledby={triggerId} hidden={!expanded}>
              {item.children}
            </div>
          </TCollapse.Panel>
        );
      })}
    </TCollapse>
  );
};

export const Collapse: any = ({
  items = [],
  value,
  defaultValue,
  onChange,
  accordion = false,
  disabled = false,
  ...props
}: any) => {
  const safeItems = normalizeCollapseItems(items);
  const validKeys = new Set(safeItems.map((item: any) => String(item.key)));
  const normalizedValue = normalizeCollapseValue(value, validKeys, accordion);
  const normalizedDefaultValue = normalizeCollapseValue(defaultValue, validKeys, accordion);
  if (uiRendererAdapter.useTDesign("collapse")) {
    return (
      <CompatTDesignCollapse
        {...props}
        safeItems={safeItems}
        validKeys={validKeys}
        value={normalizedValue}
        defaultValue={normalizedDefaultValue}
        disabled={disabled}
        accordion={accordion}
        onChange={(nextValue: unknown, context: unknown) =>
          onChange?.(normalizeCollapseValue(nextValue, validKeys, accordion) ?? [], context)
        }
      />
    );
  }
  return (
    <CompatCollapse
      items={safeItems}
      value={normalizedValue}
      defaultValue={normalizedDefaultValue}
      onChange={onChange}
      accordion={accordion}
      disabled={disabled}
      {...props}
    />
  );
};
export const Segmented: any = ({ options = [], value, onChange, disabled, ...props }: any) =>
  !uiRendererAdapter.useTDesign("segmented") ? (
    <div className="rag-segmented">
      {options.map((option: any) => {
        const item = typeof option === "string" ? { label: option, value: option } : option;
        return (
          <button
            type="button"
            key={String(item.value)}
            className={item.value === value ? "is-active" : ""}
            disabled={disabled}
            onClick={() => onChange?.(item.value)}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  ) : (
    <TRadio.Group
      {...props}
      className={["rag-segmented", props.className].filter(Boolean).join(" ")}
      variant="default-filled"
      value={value}
      disabled={disabled}
      onChange={onChange}
    >
      {options.map((option: any) => {
        const item = typeof option === "string" ? { label: option, value: option } : option;
        return (
          <TRadio.Button key={String(item.value)} value={item.value}>
            {item.icon}
            {item.label}
          </TRadio.Button>
        );
      })}
    </TRadio.Group>
  );
/**
 * TDesign 的子元素写法（`<Tabs><Tabs.TabPanel value label>内容</Tabs.TabPanel></Tabs>`）
 * 在企业模块里有 13 处，而 `items` 数组写法只有 1 处。兼容层若只认 `items`，
 * 任何"迁到兼容层"的改造都得逐页搬上百行 JSX——回归面太大，没人敢做。
 * 所以这里同时认两种写法：children 里的 TabPanel 只作数据载体（占位组件自身不渲染），
 * 由 Tabs 解析成 items。迁移一个页面因此只是换一行 import。
 */
const TabPanelPlaceholder: any = () => null;
const collectTabItems = (children: any) =>
  React.Children.toArray(children)
    .filter((child: any) => child?.type === TabPanelPlaceholder)
    .map((child: any) => ({
      key: child.props.value ?? child.key,
      label: child.props.label,
      children: child.props.children,
      disabled: child.props.disabled,
    }));

export const Tabs: any = ({
  items = [],
  children,
  activeKey,
  defaultActiveKey,
  onChange,
  keepAlive,
  ...props
}: any) => {
  const resolved = items.length ? items : collectTabItems(children);
  const current = activeKey ?? defaultActiveKey ?? resolved[0]?.key;
  // React 的 useId 带冒号，冒号在 CSS 选择器里需要转义；这里只经 getElementById 用，去掉更省心。
  const uid = React.useId().replace(/[^A-Za-z0-9_-]/g, "");
  const tabId = (key: any) => `rag-tab-${uid}-${key}`;
  const panelId = `rag-tabpanel-${uid}`;
  const keys = resolved.map((item: any) => item.key);
  // TDesign 的 TabPanel 是"首次激活才挂载、之后留在 DOM 里 display:none"
  // （tdesign-react/esm/tabs/TabPanel.js:34-43）。keepAlive 让迁过来的页面不改掉这个行为，
  // 否则切走即卸载 → 切回重新发请求、丢滚动与筛选态。默认关，保住既有调用点。
  const [visited, setVisited] = useState<any[]>(() => [current]);
  if (keepAlive && !visited.includes(current)) {
    // 渲染期更新：React 会立刻用新 state 重跑本组件，不会先把"面板还没挂载"这一帧提交出去。
    setVisited([...visited, current]);
  }
  if (!uiRendererAdapter.useTDesign("tabs"))
    return (
      <div className={props.className}>
        <div role="tablist" {...ariaDataProps(props)}>
          {resolved.map((item: any) => (
            <button
              type="button"
              role="tab"
              id={tabId(item.key)}
              data-key={item.key}
              aria-selected={current === item.key}
              // 只渲染一份面板（内容随 current 变），所以**所有** tab 都指向同一个 panelId。
              // 按 key 各造一个 panel id 会让未选中的 tab 指向不存在的元素。
              aria-controls={panelId}
              // roving tabindex：一组 tablist 在读屏里只应有一个 Tab 停留点。
              tabIndex={current === item.key ? 0 : -1}
              // TDesign 分支一直支持 item.disabled，兜底分支原先漏了，两分支对齐。
              disabled={Boolean(item.disabled)}
              key={item.key}
              onClick={() => onChange?.(item.key)}
              onKeyDown={(event) => {
                const index = keys.indexOf(item.key);
                const last = keys.length - 1;
                let nextIndex: number | null = null;
                if (event.key === "ArrowRight") nextIndex = index >= last ? 0 : index + 1;
                else if (event.key === "ArrowLeft") nextIndex = index <= 0 ? last : index - 1;
                else if (event.key === "Home") nextIndex = 0;
                else if (event.key === "End") nextIndex = last;
                if (nextIndex === null) return;
                event.preventDefault();
                const nextKey = keys[nextIndex];
                onChange?.(nextKey);
                // 焦点得跟着"当前 tab"走，否则 roving tabindex 会把焦点掉回 body。
                requestAnimationFrame(() => document.getElementById(tabId(nextKey))?.focus());
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
        <div role="tabpanel" id={panelId} aria-labelledby={tabId(current)} tabIndex={0}>
          {keepAlive
            ? resolved
                .filter((item: any) => visited.includes(item.key))
                .map((item: any) => (
                  <div key={item.key} hidden={item.key !== current}>
                    {item.children}
                  </div>
                ))
            : resolved.find((item: any) => item.key === current)?.children}
        </div>
      </div>
    );
  return (
    <TTabs {...props} value={activeKey} defaultValue={defaultActiveKey} onChange={onChange}>
      {resolved.map((item: any) => (
        <TTabs.TabPanel key={item.key} value={item.key} label={item.label} disabled={item.disabled}>
          {item.children}
        </TTabs.TabPanel>
      ))}
    </TTabs>
  );
};
Tabs.TabPanel = TabPanelPlaceholder;

const trapDrawerTabKey = (event: KeyboardEvent, drawer: HTMLElement) => {
  const focusable = Array.from(
    drawer.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((element) => !element.closest("[hidden], [inert], [aria-hidden='true']"));
  const active = document.activeElement;
  if (!focusable.length) {
    event.preventDefault();
    drawer.focus();
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && (active === drawer || active === first || !drawer.contains(active))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (active === drawer || active === last || !drawer.contains(active))) {
    event.preventDefault();
    first.focus();
  }
};
const isTopmostModal = (modal: HTMLElement): boolean => {
  const modals = Array.from(
    document.querySelectorAll<HTMLElement>('[role="dialog"][aria-modal="true"]'),
  ).filter(
    (dialog) => !dialog.hidden && dialog.getAttribute("aria-hidden") !== "true",
  );
  return modals[modals.length - 1] === modal;
};

const CompatDrawer = ({
  open,
  visible,
  title,
  header,
  onClose,
  onCancel,
  onConfirm,
  width,
  height,
  size,
  placement = "right",
  destroyOnClose = false,
  footer = false,
  closeBtn = false,
  showOverlay = true,
  closeOnOverlayClick = true,
  closeOnEscKeydown = true,
  attach,
  children,
  className,
  style,
  ...props
}: any) => {
  const resolvedOpen = open ?? visible ?? false;
  const resolvedTitle = title ?? header;
  const safePlacement = ["left", "right", "top", "bottom"].includes(placement)
    ? placement
    : "right";
  const resolvedSize =
    safePlacement === "top" || safePlacement === "bottom" ? height ?? size : width ?? size;
  const [mounted, setMounted] = useState(Boolean(resolvedOpen));
  const drawerRef = useRef<HTMLDivElement | null>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const previousOpenRef = useRef(false);
  const titleId = React.useId();
  const ariaProps = Object.fromEntries(
    Object.entries(props).filter(([key]) => key === "id" || key.startsWith("aria-")),
  );
  const hasExplicitAriaLabel = Object.prototype.hasOwnProperty.call(ariaProps, "aria-label");
  const explicitAriaLabelledby =
    typeof ariaProps["aria-labelledby"] === "string" ? ariaProps["aria-labelledby"] : undefined;
  const dataProps = Object.fromEntries(
    Object.entries(props).filter(([key]) => key.startsWith("data-")),
  );

  useEffect(() => {
    if (resolvedOpen) setMounted(true);
    else if (destroyOnClose) setMounted(false);
  }, [destroyOnClose, resolvedOpen]);

  React.useLayoutEffect(() => {
    if (resolvedOpen && !previousOpenRef.current) {
      returnFocusRef.current =
        typeof document !== "undefined" && document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null;
      drawerRef.current?.focus();
    } else if (!resolvedOpen && previousOpenRef.current) {
      returnFocusRef.current?.focus();
      returnFocusRef.current = null;
    }
    previousOpenRef.current = resolvedOpen;
  }, [resolvedOpen]);

  useEffect(() => {
    if (!resolvedOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Tab") {
        if (drawerRef.current && isTopmostModal(drawerRef.current))
          trapDrawerTabKey(event, drawerRef.current);
        return;
      }
      if (event.key !== "Escape") return;
      if (!closeOnEscKeydown) return;
      if (!drawerRef.current || !isTopmostModal(drawerRef.current)) return;
      event.stopPropagation();
      onClose?.({ e: event, trigger: "keydown-esc" });
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [closeOnEscKeydown, onClose, resolvedOpen]);

  if (!resolvedOpen && !mounted) return null;

  const drawerStyle = {
    ...style,
    ...(safePlacement === "left" || safePlacement === "right"
      ? resolvedSize === undefined
        ? {}
        : { width: typeof resolvedSize === "number" ? `${resolvedSize}px` : resolvedSize }
      : resolvedSize === undefined
        ? {}
        : { height: typeof resolvedSize === "number" ? `${resolvedSize}px` : resolvedSize }),
  };
  const closeControl =
    closeBtn && React.isValidElement(closeBtn) ? (
      closeBtn
    ) : closeBtn ? (
      <button
        type="button"
        className="rag-drawer-close"
        aria-label="关闭抽屉"
        onClick={(event) => onClose?.({ e: event, trigger: "click-close-btn" })}
      >
        {typeof closeBtn === "string" ? closeBtn : "×"}
      </button>
    ) : null;
  const footerContent =
    footer === true ? (
      <>
        <Button onClick={onCancel}>取消</Button>
        <Button type="primary" onClick={onConfirm}>
          确认
        </Button>
      </>
    ) : (
      footer
    );

  const drawerContent = (
    <div
      {...dataProps}
      className={["rag-drawer-shell", resolvedOpen ? "is-open" : "is-closed"].join(" ")}
      hidden={!resolvedOpen}
      aria-hidden={!resolvedOpen}
    >
      {showOverlay ? (
        <div
          className="rag-drawer-mask"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && closeOnOverlayClick)
              onClose?.({ e: event, trigger: "click-overlay" });
          }}
        />
      ) : null}
      <div
        {...ariaProps}
        ref={drawerRef}
        role="dialog"
        aria-modal={resolvedOpen ? true : undefined}
        aria-labelledby={
          explicitAriaLabelledby ??
          (hasExplicitAriaLabel && explicitAriaLabelledby === undefined
            ? undefined
            : resolvedTitle
              ? titleId
              : undefined)
        }
        tabIndex={-1}
        className={["rag-drawer", `is-${safePlacement}`, className].filter(Boolean).join(" ")}
        style={drawerStyle}
      >
        {closeControl}
        {resolvedTitle !== false && resolvedTitle !== undefined ? (
          <div id={titleId} className="rag-drawer-header">
            {resolvedTitle}
          </div>
        ) : null}
        <div className="rag-drawer-body">{children}</div>
        {footerContent ? <div className="rag-drawer-footer">{footerContent}</div> : null}
      </div>
    </div>
  );
  if (attach === "body" && typeof document !== "undefined")
    return createPortal(drawerContent, document.body);
  return drawerContent;
};

export const Drawer: any = (props: any) => {
  const {
    open,
    visible,
    title,
    header,
    onClose,
    width,
    height,
    size,
    placement = "right",
    destroyOnClose,
    children,
    ...rest
  } = props;
  const resolvedOpen = open ?? visible ?? false;
  const resolvedTitle = title ?? header;
  const resolvedSize =
    placement === "top" || placement === "bottom" ? height ?? size : width ?? size;
  const facadeId = React.useId().replace(/[^A-Za-z0-9_-]/g, "");
  const facadeClassName = `rag-drawer-facade-${facadeId}`;
  const usesTDesign = uiRendererAdapter.useTDesign("drawer");
  const explicitAriaLabel = typeof rest["aria-label"] === "string" ? rest["aria-label"] : "";
  const explicitAriaLabelledby =
    typeof rest["aria-labelledby"] === "string" ? rest["aria-labelledby"] : "";
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const previousOpenRef = useRef(false);

  React.useLayoutEffect(() => {
    if (!usesTDesign) return;
    if (resolvedOpen && !previousOpenRef.current) {
      returnFocusRef.current =
        typeof document !== "undefined" && document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null;
    } else if (!resolvedOpen && previousOpenRef.current) {
      returnFocusRef.current?.focus();
      returnFocusRef.current = null;
    }
    previousOpenRef.current = resolvedOpen;
  }, [resolvedOpen, usesTDesign]);

  useEffect(() => {
    if (!usesTDesign || typeof document === "undefined") return;
    const sync = () => {
      const drawer = document.querySelector<HTMLElement>(`.${facadeClassName}`);
      if (!drawer) return false;
      drawer.setAttribute("role", "dialog");
      if (resolvedOpen) drawer.setAttribute("aria-modal", "true");
      else drawer.removeAttribute("aria-modal");

      if (explicitAriaLabel) {
        drawer.setAttribute("aria-label", explicitAriaLabel);
        drawer.removeAttribute("aria-labelledby");
        return true;
      }
      if (explicitAriaLabelledby) {
        drawer.removeAttribute("aria-label");
        drawer.setAttribute("aria-labelledby", explicitAriaLabelledby);
        return true;
      }
      drawer.removeAttribute("aria-label");
      if (resolvedTitle !== false && resolvedTitle !== undefined && resolvedTitle !== null) {
        const heading = drawer.querySelector<HTMLElement>(".t-drawer__header");
        if (heading) {
          heading.id = `rag-drawer-title-${facadeId}`;
          drawer.setAttribute("aria-labelledby", heading.id);
        }
      } else {
        drawer.removeAttribute("aria-labelledby");
      }
      return true;
    };
    if (sync()) return;
    const observer = new MutationObserver(() => {
      if (sync()) observer.disconnect();
    });
    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [
    explicitAriaLabel,
    explicitAriaLabelledby,
    facadeClassName,
    facadeId,
    resolvedOpen,
    resolvedTitle,
    usesTDesign,
  ]);

  useEffect(() => {
    if (!usesTDesign || !resolvedOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const drawer = document.querySelector<HTMLElement>(`.${facadeClassName}`);
      if (drawer && isTopmostModal(drawer)) trapDrawerTabKey(event, drawer);
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [facadeClassName, resolvedOpen, usesTDesign]);

  if (!usesTDesign)
    return <CompatDrawer {...props} />;
  return (
    <TDrawer
      {...rest}
      visible={resolvedOpen}
      header={resolvedTitle}
      onClose={onClose}
      placement={placement}
      size={resolvedSize}
      destroyOnClose={destroyOnClose}
      className={[rest.className, facadeClassName].filter(Boolean).join(" ")}
    >
      {children}
    </TDrawer>
  );
};

const dialogButtonConfig = (button: any, fallback: string): any => {
  if (button === false) return null;
  if (typeof button === "string") return { content: button };
  return button ?? { content: fallback };
};

const CompatDialog = ({
  open,
  visible,
  title,
  header,
  onClose,
  onCancel,
  onConfirm,
  confirmBtn,
  cancelBtn,
  confirmLoading = false,
  closeBtn = false,
  closeOnOverlayClick = true,
  closeOnEscKeydown = true,
  destroyOnClose = false,
  attach,
  footer,
  children,
  className,
  style,
  ...props
}: any) => {
  const resolvedOpen = open ?? visible ?? false;
  const resolvedTitle = title ?? header;
  const [mounted, setMounted] = useState(Boolean(resolvedOpen));
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const previousOpenRef = useRef(false);
  const titleId = React.useId();
  const explicitAriaLabel = typeof props["aria-label"] === "string" ? props["aria-label"] : "";
  const explicitAriaLabelledby =
    typeof props["aria-labelledby"] === "string" ? props["aria-labelledby"] : "";
  const ariaProps = Object.fromEntries(
    Object.entries(props).filter(
      ([key]) => key === "id" || key === "role" || key.startsWith("aria-"),
    ),
  );
  const dataProps = Object.fromEntries(
    Object.entries(props).filter(([key]) => key.startsWith("data-")),
  );
  const confirmConfig = dialogButtonConfig(confirmBtn, "确定");
  const cancelConfig = dialogButtonConfig(cancelBtn, "取消");
  const closeDialog = useCallback((event?: unknown) => onClose?.(event), [onClose]);

  useEffect(() => {
    if (resolvedOpen) setMounted(true);
    else if (destroyOnClose) setMounted(false);
  }, [destroyOnClose, resolvedOpen]);

  React.useLayoutEffect(() => {
    if (resolvedOpen && !previousOpenRef.current) {
      returnFocusRef.current =
        typeof document !== "undefined" && document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null;
      dialogRef.current?.focus();
    } else if (!resolvedOpen && previousOpenRef.current) {
      returnFocusRef.current?.focus();
      returnFocusRef.current = null;
    }
    previousOpenRef.current = resolvedOpen;
  }, [resolvedOpen]);

  useEffect(() => {
    if (!resolvedOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Tab") {
        if (dialogRef.current && isTopmostModal(dialogRef.current))
          trapDrawerTabKey(event, dialogRef.current);
        return;
      }
      if (event.key !== "Escape" || !closeOnEscKeydown) return;
      if (!dialogRef.current || !isTopmostModal(dialogRef.current)) return;
      event.stopPropagation();
      closeDialog({ e: event, trigger: "keydown-esc" });
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [closeDialog, closeOnEscKeydown, resolvedOpen]);

  if (!resolvedOpen && !mounted) return null;

  const closeControl =
    closeBtn && React.isValidElement(closeBtn) ? (
      closeBtn
    ) : closeBtn ? (
      <button
        type="button"
        className="rag-dialog-close"
        aria-label="关闭对话框"
        onClick={closeDialog}
      >
        ×
      </button>
    ) : null;
  const footerContent =
    footer !== undefined ? (
      footer
    ) : (
      <>
        {cancelConfig ? (
          <Button
            type="default"
            disabled={Boolean(cancelConfig.disabled)}
            onClick={onCancel ?? closeDialog}
          >
            {cancelConfig.content ?? "取消"}
          </Button>
        ) : null}
        {confirmConfig ? (
          <Button
            type={
              confirmConfig.theme === "primary" || confirmConfig.theme === "danger"
                ? "primary"
                : "default"
            }
            danger={confirmConfig.theme === "danger"}
            loading={Boolean(confirmLoading || confirmConfig.loading)}
            disabled={Boolean(confirmConfig.disabled)}
            onClick={onConfirm}
          >
            {confirmConfig.content ?? "确定"}
          </Button>
        ) : null}
      </>
    );
  const dialogContent = (
    <div
      {...dataProps}
      className={["rag-dialog-shell", resolvedOpen ? "is-open" : "is-closed"].join(" ")}
      hidden={!resolvedOpen}
      aria-hidden={!resolvedOpen}
    >
      <div
        className="rag-dialog-mask"
        onMouseDown={(event) => {
          if (event.target === event.currentTarget && closeOnOverlayClick) closeDialog(event);
        }}
      />
      <div
        {...ariaProps}
        ref={dialogRef}
        role={typeof ariaProps.role === "string" ? ariaProps.role : "dialog"}
        aria-modal={resolvedOpen ? true : undefined}
        aria-label={explicitAriaLabel || undefined}
        aria-labelledby={
          explicitAriaLabel || explicitAriaLabelledby
            ? explicitAriaLabelledby || undefined
            : resolvedTitle
              ? titleId
              : undefined
        }
        tabIndex={-1}
        className={["rag-dialog", className].filter(Boolean).join(" ")}
        style={style}
      >
        {closeControl}
        {resolvedTitle !== false && resolvedTitle !== undefined ? (
          <div id={titleId} className="rag-dialog-header">
            {resolvedTitle}
          </div>
        ) : null}
        <div className="rag-dialog-body">{children}</div>
        {footerContent ? <div className="rag-dialog-footer">{footerContent}</div> : null}
      </div>
    </div>
  );
  if (attach === "body" && typeof document !== "undefined")
    return createPortal(dialogContent, document.body);
  return dialogContent;
};

export const Dialog: any = (props: any) => {
  const {
    open,
    visible,
    title,
    header,
    onClose,
    onCancel,
    onConfirm,
    confirmBtn,
    cancelBtn,
    confirmLoading,
    closeBtn,
    ...rest
  } = props;
  const resolvedOpen = open ?? visible ?? false;
  const resolvedTitle = title ?? header;
  const sameCloseHandler = Boolean(onClose && onCancel && onClose === onCancel);
  const closeGuardRef = useRef(false);
  const guardCloseHandler = (handler: any) => {
    if (!handler || !sameCloseHandler) return handler;
    return (event: unknown) => {
      if (closeGuardRef.current) return;
      closeGuardRef.current = true;
      handler(event);
      Promise.resolve().then(() => {
        closeGuardRef.current = false;
      });
    };
  };
  if (!uiRendererAdapter.useTDesign("dialog")) return <CompatDialog {...props} />;
  return (
    <TDialog
      {...rest}
      visible={resolvedOpen}
      header={resolvedTitle}
      onClose={guardCloseHandler(onClose)}
      onCancel={guardCloseHandler(onCancel)}
      onConfirm={onConfirm}
      confirmBtn={confirmBtn}
      cancelBtn={cancelBtn}
      confirmLoading={confirmLoading}
      closeBtn={closeBtn}
    />
  );
};
export const Modal: any = ({
  open,
  title,
  onCancel,
  onOk,
  okText,
  cancelText,
  confirmLoading,
  okButtonProps,
  cancelButtonProps,
  children,
  ...props
}: any) =>
  !uiRendererAdapter.useTDesign("modal") ? (
    open ? (
      <div className="rag-modal-mask">
        <div role="dialog" className="rag-modal">
          <h2>{title}</h2>
          {children}
          <div>
            <button
              type="button"
              className="rag-button"
              disabled={cancelButtonProps?.disabled}
              onClick={onCancel}
            >
              {cancelText}
            </button>
            <button
              type="button"
              className="rag-button is-primary"
              disabled={okButtonProps?.disabled || confirmLoading}
              onClick={onOk}
            >
              {okText}
            </button>
          </div>
        </div>
      </div>
    ) : null
  ) : (
    <TDialog
      {...props}
      visible={open}
      header={title}
      onClose={onCancel}
      onConfirm={onOk}
      confirmBtn={{ content: okText, loading: confirmLoading, ...okButtonProps }}
      cancelBtn={{ content: cancelText, ...cancelButtonProps }}
    >
      {children}
    </TDialog>
  );

function CompatTable({
  dataSource = [],
  columns = [],
  rowKey = "key",
  pagination,
  rowSelection,
  expandable,
  locale,
  loading,
  className,
  scroll,
  scrollContainerProps,
  size,
  verticalAlign = "middle",
  hover = true,
  bordered = true,
}: any) {
  const [page, setPage] = useState(1);
  const [expandedKeys, setExpandedKeys] = useState<Array<string | number>>([]);
  const pageSize =
    pagination === false ? Math.max(1, dataSource.length) : Number(pagination?.pageSize ?? 10);
  const totalPages = Math.max(1, Math.ceil(dataSource.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const rows = useMemo(
    () => dataSource.slice((safePage - 1) * pageSize, safePage * pageSize),
    [dataSource, pageSize, safePage],
  );
  const getKey = (row: any) => (typeof rowKey === "function" ? rowKey(row) : row[rowKey]);
  const getCellValue = (row: any, dataIndex: string | number | Array<string | number>) => {
    const path = Array.isArray(dataIndex)
      ? dataIndex
      : typeof dataIndex === "string"
        ? dataIndex.split(".")
        : [dataIndex];
    return path.reduce((value: any, key) => (value == null ? undefined : value[key]), row);
  };
  const selected = new Set<string | number>(rowSelection?.selectedRowKeys ?? []);
  const selectableRows = rows.filter(
    (row: any) => !rowSelection?.getCheckboxProps?.(row)?.disabled,
  );
  const allSelected =
    selectableRows.length > 0 && selectableRows.every((row: any) => selected.has(getKey(row)));
  const setSelection = (keys: Array<string | number>) =>
    rowSelection?.onChange?.(
      keys,
      dataSource.filter((row: any) => keys.includes(getKey(row))),
    );
  const toggleRow = (row: any) => {
    const key = getKey(row);
    const keys = selected.has(key)
      ? [...selected].filter((item) => item !== key)
      : [...selected, key];
    setSelection(keys);
  };
  const toggleAll = () => {
    const pageKeys = selectableRows.map(getKey);
    const keys = allSelected
      ? [...selected].filter((key) => !pageKeys.includes(key))
      : Array.from(new Set([...selected, ...pageKeys]));
    setSelection(keys);
  };
  const toggleExpanded = (key: string | number) =>
    setExpandedKeys((current) =>
      current.includes(key) ? current.filter((item) => item !== key) : [...current, key],
    );
  const {
    className: scrollContainerClassName,
    style: scrollContainerStyle,
    tabIndex: scrollContainerTabIndex,
    onKeyDown: scrollContainerOnKeyDown,
    ...rawScrollContainerProps
  } = scrollContainerProps ?? {};
  const safeVerticalAlign = ["top", "middle", "bottom"].includes(verticalAlign)
    ? verticalAlign
    : "middle";
  const tableClassName = [
    "rag-table",
    size === "small" ? "is-small" : "",
    `is-align-${safeVerticalAlign}`,
    hover === false ? "is-hover-disabled" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const tableStyle = scroll?.x
    ? { minWidth: typeof scroll.x === "number" ? scroll.x : undefined }
    : undefined;

  return (
    <div
      className={[
        "rag-table-shell",
        bordered === false ? "is-borderless" : "",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      aria-busy={Boolean(loading)}
    >
      <div
        {...ariaDataProps(rawScrollContainerProps)}
        className={["rag-table-scroll", scrollContainerClassName].filter(Boolean).join(" ")}
        style={scrollContainerStyle}
        tabIndex={scrollContainerTabIndex}
        onKeyDown={scrollContainerOnKeyDown}
      >
        <table className={tableClassName} style={tableStyle}>
          <thead>
            <tr>
              {rowSelection && (
                <th className="rag-table-check">
                  <input
                    type="checkbox"
                    aria-label="选择当前页"
                    checked={allSelected}
                    onChange={toggleAll}
                  />
                </th>
              )}
              {expandable && <th className="rag-table-expand" aria-label="展开" />}
              {columns.map((column: any) => (
                <th
                  key={column.key ?? column.dataIndex ?? column.title}
                  style={{ width: column.width, textAlign: column.align }}
                >
                  {column.title}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length ? (
              rows.flatMap((row: any, rowIndex: number) => {
                const key = getKey(row);
                const canExpand = expandable?.rowExpandable?.(row) ?? Boolean(expandable);
                const main = (
                  <tr key={String(key)}>
                    {rowSelection && (
                      <td className="rag-table-check">
                        <input
                          type="checkbox"
                          aria-label={`选择 ${row.name ?? key}`}
                          checked={selected.has(key)}
                          disabled={Boolean(rowSelection.getCheckboxProps?.(row)?.disabled)}
                          onChange={() => toggleRow(row)}
                        />
                      </td>
                    )}
                    {expandable && (
                      <td className="rag-table-expand">
                        {canExpand && (
                          <button
                            type="button"
                            aria-label={expandedKeys.includes(key) ? "收起详情" : "展开详情"}
                            onClick={() => toggleExpanded(key)}
                          >
                            {expandedKeys.includes(key) ? "−" : "+"}
                          </button>
                        )}
                      </td>
                    )}
                    {columns.map((column: any) => {
                      const cellValue = getCellValue(row, column.dataIndex);
                      return (
                        <td
                          key={column.key ?? String(column.dataIndex ?? column.title)}
                          style={{ textAlign: column.align }}
                        >
                          {column.render ? column.render(cellValue, row, rowIndex) : cellValue}
                        </td>
                      );
                    })}
                  </tr>
                );
                const detail =
                  expandedKeys.includes(key) && canExpand ? (
                    <tr key={`${key}-detail`} className="rag-table-detail">
                      <td colSpan={columns.length + (rowSelection ? 1 : 0) + (expandable ? 1 : 0)}>
                        {expandable.expandedRowRender(row)}
                      </td>
                    </tr>
                  ) : (
                    []
                  );
                return [main, detail];
              })
            ) : (
              <tr>
                <td
                  colSpan={columns.length + (rowSelection ? 1 : 0) + (expandable ? 1 : 0)}
                  className="rag-table-empty"
                >
                  {locale?.emptyText ?? "暂无数据"}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {pagination !== false && dataSource.length > pageSize && (
        <div className="rag-table-pagination">
          <span>{pagination?.showTotal?.(dataSource.length) ?? `共 ${dataSource.length} 条`}</span>
          <Button
            size="small"
            disabled={safePage <= 1}
            onClick={() => setPage((value: number) => Math.max(1, value - 1))}
          >
            上一页
          </Button>
          <b>
            {safePage} / {totalPages}
          </b>
          <Button
            size="small"
            disabled={safePage >= totalPages}
            onClick={() => setPage((value: number) => Math.min(totalPages, value + 1))}
          >
            下一页
          </Button>
        </div>
      )}
      {loading && <div className="rag-table-loading">加载中…</div>}
    </div>
  );
}
export const Table: any = CompatTable;

const Sider: any = ({
  children,
  width = 232,
  collapsedWidth = 72,
  collapsed,
  breakpoint,
  onBreakpoint,
  onCollapse: _onCollapse,
  collapsible: _collapsible,
  theme: _theme,
  className,
  ...props
}: any) => {
  const onBreakpointRef = useRef(onBreakpoint);
  onBreakpointRef.current = onBreakpoint;
  useEffect(() => {
    if (!breakpoint) return;
    const media = window.matchMedia("(max-width: 768px)");
    const notify = () => onBreakpointRef.current?.(media.matches);
    notify();
    media.addEventListener?.("change", notify);
    return () => media.removeEventListener?.("change", notify);
  }, [breakpoint]);
  const resolvedWidth = collapsed ? collapsedWidth : width;
  return (
    <TLayout.Aside {...props} width={resolvedWidth} className={className} style={props.style}>
      {children}
    </TLayout.Aside>
  );
};
const CompatLayout: any = ({ children, className, ...props }: any) => (
  <TLayout {...props} direction="horizontal" className={className}>
    {children}
  </TLayout>
);
export const Layout: any = Object.assign(CompatLayout, { Sider, Content: TLayout.Content });
export type MenuProps = { items?: any[] };
export const Menu: any = ({ items = [], selectedKeys = [], onClick, ...props }: any) => (
  <TMenu {...props} value={selectedKeys[0]} onChange={(value) => onClick?.({ key: value })}>
    {items.map((group: any) =>
      group.type === "group" ? (
        <React.Fragment key={String(group.label)}>
          <li className="t-menu-group__title">{group.label}</li>
          {group.children?.map((item: any) => (
            <TMenu.MenuItem key={item.key} value={item.key} icon={item.icon}>
              {item.label}
            </TMenu.MenuItem>
          ))}
        </React.Fragment>
      ) : (
        <TMenu.MenuItem key={group.key} value={group.key} icon={group.icon}>
          {group.label}
        </TMenu.MenuItem>
      ),
    )}
  </TMenu>
);
export const App: any = Object.assign(({ children }: any) => <>{children}</>, {
  useApp: () => ({ message }),
});
export { message };
