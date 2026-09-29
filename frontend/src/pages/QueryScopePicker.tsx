import { useEffect, useRef, useState } from "react";

interface Props {
  value: string[];
  options: { value: string; label: string }[];
  onChange: (value: string[]) => void;
}

/** A compact, keyboard-operable scope menu instead of a native multi-select listbox. */
export default function QueryScopePicker({ value, options, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [open]);
  const label = value.length
    ? value.map((item) => options.find((option) => option.value === item)?.label ?? item).join("、")
    : "全部资料";
  return (
    <div
      className="query-scope-picker"
      ref={root}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node)) setOpen(false);
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape" && open) {
          event.preventDefault();
          setOpen(false);
          trigger.current?.focus();
        }
      }}
    >
      <button
        type="button"
        className="query-scope-trigger"
        ref={trigger}
        aria-label={`限定查询的资料范围：${label}`}
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        title={label}
      >
        <span className="query-scope-label">{label}</span>
        <span aria-hidden="true">⌄</span>
      </button>
      {open && (
        <fieldset className="query-scope-popover">
          <legend>限定查询的资料范围</legend>
          <p>未勾选时，查询全部已授权资料。</p>
          {options.map((option) => (
            <label key={option.value}>
              <input
                type="checkbox"
                checked={value.includes(option.value)}
                onChange={(event) =>
                  onChange(
                    event.target.checked
                      ? [...value, option.value]
                      : value.filter((item) => item !== option.value),
                  )
                }
              />
              {option.label}
            </label>
          ))}
          <button type="button" onClick={() => onChange([])} disabled={!value.length}>
            使用全部资料
          </button>
        </fieldset>
      )}
    </div>
  );
}
