/* eslint-disable @typescript-eslint/no-explicit-any, react-refresh/only-export-components -- typed compatibility boundary over TDesign APIs */
import React, { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  Alert as TAlert,
  Button as TButton,
  Card as TCard,
  Col as TCol,
  Collapse as TCollapse,
  ConfigProvider as TConfigProvider,
  Dialog as TDialog,
  Drawer as TDrawer,
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
// TDesign supplies the design tokens, locale, layout shell, menu, and icon system.
// Data-heavy kept-alive pages use the app-owned facade below: direct TDesign
// popup/form widgets currently enter a browser render loop with this architecture.
const canRenderTDesign = () => false;

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
  if (!canRenderTDesign())
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
  { children, bodyStyle, styles, size, hoverable, title, extra, ...props },
  ref,
) {
  const body = bodyStyle ?? styles?.body;
  if (!canRenderTDesign())
    return (
      <div ref={ref} {...props} className={["rag-card", props.className].filter(Boolean).join(" ")}>
        {(title || extra) && (
          <div className="rag-card-header">
            <strong>{title}</strong>
            <span>{extra}</span>
          </div>
        )}
        <div className="rag-card-body" style={body}>
          {children}
        </div>
      </div>
    );
  return (
    <TCard
      ref={ref}
      {...props}
      title={title}
      actions={extra}
      bodyStyle={body}
      size={sizeOf(size)}
      hoverShadow={hoverable}
    >
      {children}
    </TCard>
  );
});

export const Alert: any = ({
  type,
  message: title,
  description,
  showIcon: _showIcon = true,
  ...props
}: any) =>
  !canRenderTDesign() ? (
    <div
      role="alert"
      className={["rag-alert", `is-${type ?? "info"}`, props.className].filter(Boolean).join(" ")}
    >
      {title}
      {description ? <div>{description}</div> : null}
    </div>
  ) : (
    <TAlert
      {...props}
      icon={typeof document === "undefined" ? false : undefined}
      theme={type === "error" ? "error" : type}
      title={description ? title : undefined}
      message={description ?? title}
      maxLine={0}
    />
  );
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
  if (canRenderTDesign())
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
  !canRenderTDesign() ? (
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
  if (canRenderTDesign())
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
  { onChange, onPressEnter, autoSize, className, ...props },
  ref,
) {
  if (!canRenderTDesign())
    return (
      <textarea
        ref={ref}
        {...props}
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
      onChange={(value, context) => onChange?.({ target: { value }, nativeEvent: context?.e })}
      onKeydown={(_value, context) => {
        if (context?.e?.key === "Enter") onPressEnter?.(context.e);
      }}
    />
  );
});
const CompatSearch = React.forwardRef<any, any>(function CompatSearch(
  { enterButton, onSearch, onChange, className, ...props },
  ref,
) {
  if (!canRenderTDesign())
    return (
      <input
        ref={ref}
        {...props}
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
      suffixIcon={enterButton}
      onChange={(value, context) => onChange?.({ target: { value }, nativeEvent: context?.e })}
      onEnter={(value, context) => onSearch?.(value, context)}
    />
  );
});
export const Input: any = Object.assign(
  React.forwardRef<any, any>(function Input(
    { allowClear, prefix, onPressEnter, onChange, className, ...props },
    ref,
  ) {
    if (!canRenderTDesign())
      return (
        <input
          ref={ref}
          {...props}
          className={["rag-input", className].filter(Boolean).join(" ")}
          onChange={onChange}
          onKeyDown={(event) => {
            if (event.key === "Enter") onPressEnter?.(event);
          }}
        />
      );
    return (
      <TInput
        ref={ref}
        {...props}
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
  if (!canRenderTDesign()) {
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
export const InputNumber: any = TInputNumber;
export const Switch: any = ({ checked, onChange, ...props }: any) =>
  !canRenderTDesign() ? (
    <input
      type="checkbox"
      checked={Boolean(checked)}
      onChange={(event) => onChange?.(event.target.checked)}
    />
  ) : (
    <TSwitch {...props} value={checked} onChange={onChange} />
  );
export const Spin: any = ({ spinning = true, children, tip, size, ...props }: any) =>
  !canRenderTDesign() ? (
    <div>
      {spinning && tip ? <span>{tip}</span> : null}
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
  if (!canRenderTDesign())
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
export const Skeleton: any = ({ active: _active, title, paragraph, ...props }: any) => (
  <TSkeleton
    {...props}
    animation="gradient"
    rowCol={[
      ...(title === false ? [] : [{}]),
      ...Array.from({ length: paragraph?.rows ?? 3 }, () => ({})),
    ]}
  />
);

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
export const Tag: any = ({ color, icon, children, bordered, ...props }: any) => {
  const theme = COLOR_THEME[color] ?? "default";
  if (!canRenderTDesign())
    return (
      <span
        {...props}
        className={["rag-tag", `is-${theme}`, props.className].filter(Boolean).join(" ")}
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
      variant={bordered === false ? "light" : "light-outline"}
    >
      {children}
    </TTag>
  );
};
const Text: any = ({ type, strong, style, children, ...props }: any) => {
  const mergedStyle = strong ? { ...style, fontWeight: 600 } : style;
  return !canRenderTDesign() ? (
    <span {...props} style={mergedStyle}>
      {children}
    </span>
  ) : (
    <TTypography.Text {...props} style={mergedStyle} theme={type === "danger" ? "error" : type}>
      {children}
    </TTypography.Text>
  );
};
const Title: any = ({ level = 1, children, ...props }: any) =>
  !canRenderTDesign() ? (
    React.createElement(`h${level}`, props, children)
  ) : (
    <TTypography.Title {...props} level={`h${level}` as any} />
  );
const Paragraph: any = ({ type, ...props }: any) =>
  !canRenderTDesign() ? (
    <p {...props} />
  ) : (
    <TTypography.Paragraph {...props} theme={type === "danger" ? "error" : type} />
  );
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
  !canRenderTDesign() ? (
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
  !canRenderTDesign() ? (
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
  !canRenderTDesign() ? (
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
export const Statistic: any = TStatistic;
export const ConfigProvider: any = TConfigProvider;

const CompatRadioButton: any = ({ children, ...props }: any) =>
  canRenderTDesign() ? <TRadio.Button {...props}>{children}</TRadio.Button> : <>{children}</>;

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
  if (canRenderTDesign()) {
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
export const Collapse: any = ({ items = [], ...props }: any) => (
  <TCollapse {...props}>
    {items.map((item: any) => (
      <TCollapse.Panel key={item.key} value={item.key} header={item.label}>
        {item.children}
      </TCollapse.Panel>
    ))}
  </TCollapse>
);
export const Segmented: any = ({ options = [], value, onChange, disabled, ...props }: any) =>
  !canRenderTDesign() ? (
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
export const Tabs: any = ({ items = [], activeKey, defaultActiveKey, onChange, ...props }: any) => {
  const current = activeKey ?? defaultActiveKey ?? items[0]?.key;
  if (!canRenderTDesign())
    return (
      <div className={props.className}>
        <div role="tablist">
          {items.map((item: any) => (
            <button
              type="button"
              role="tab"
              data-key={item.key}
              aria-selected={current === item.key}
              key={item.key}
              onClick={() => onChange?.(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>
        {items.find((item: any) => item.key === current)?.children}
      </div>
    );
  return (
    <TTabs {...props} value={activeKey} defaultValue={defaultActiveKey} onChange={onChange}>
      {items.map((item: any) => (
        <TTabs.TabPanel key={item.key} value={item.key} label={item.label} disabled={item.disabled}>
          {item.children}
        </TTabs.TabPanel>
      ))}
    </TTabs>
  );
};
export const Drawer: any = ({
  open,
  title,
  onClose,
  width,
  destroyOnClose,
  children,
  ...props
}: any) =>
  !canRenderTDesign() ? (
    open ? (
      <div role="dialog" className={props.className}>
        {title}
        {children}
      </div>
    ) : null
  ) : (
    <TDrawer
      {...props}
      visible={open}
      header={title}
      onClose={onClose}
      size={width}
      destroyOnClose={destroyOnClose}
    >
      {children}
    </TDrawer>
  );
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
  !canRenderTDesign() ? (
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

  return (
    <div
      className={["rag-table-shell", className].filter(Boolean).join(" ")}
      aria-busy={Boolean(loading)}
    >
      <div
        className="rag-table-scroll"
        style={
          scroll?.x ? { minWidth: typeof scroll.x === "number" ? scroll.x : undefined } : undefined
        }
      >
        <table className="rag-table">
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
