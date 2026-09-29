import { useEffect, useId, useRef, useState } from "react";
import { useConnection } from "../context/ConnectionContext";
import "./service-status-notice.css";
import { getServiceStatus } from "./serviceStatusModel";

/** One persistent status entry; supporting details never take up a content row. */
export default function ServiceStatusNotice({ onConfigure }: { onConfigure: () => void }) {
  const { online, checking, health, refresh } = useConnection();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const id = useId();
  const { state, label, tone, description, issues } = getServiceStatus({
    online,
    checking,
    health,
  });

  useEffect(() => {
    if (!open) return;
    panel.current?.focus();
    const dismiss = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [open]);

  const close = () => {
    setOpen(false);
    trigger.current?.focus();
  };
  return (
    <div
      className="service-status"
      ref={root}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node)) setOpen(false);
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape" && open) {
          event.preventDefault();
          event.stopPropagation();
          close();
        }
      }}
    >
      <button
        type="button"
        ref={trigger}
        className={`service-status__trigger is-${tone}`}
        data-state={state}
        aria-label={`服务健康状态：${label}，查看详情`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen(!open)}
      >
        <span className="service-status__dot" aria-hidden="true">
          {state === "ready" ? "✓" : state === "down" ? "!" : ""}
        </span>
        <span className="service-status__label">{label}</span>
        <span className="service-status__chevron" aria-hidden="true">
          ⌄
        </span>
      </button>
      <span
        className="service-status__announcement"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        服务状态：{label}
      </span>
      {open && (
        <div
          className="service-status__panel"
          ref={panel}
          id={id}
          role="dialog"
          aria-label="连接与配置状态"
          tabIndex={-1}
        >
          <div className="service-status__heading">
            <strong>连接与配置</strong>
            <button type="button" aria-label="关闭连接与配置详情" onClick={close}>
              ×
            </button>
          </div>
          <p>{description}</p>
          {issues.length > 0 && (
            <ul className="service-status__issues">
              {issues.map((issue) => (
                <li key={issue.key}>
                  <span>{issue.label}</span>
                  <span>{issue.statusLabel}</span>
                </li>
              ))}
            </ul>
          )}
          <div className="service-status__actions">
            <button type="button" disabled={checking} onClick={() => void refresh()}>
              {checking ? "检查中…" : "重新检查"}
            </button>
            <button
              type="button"
              className="service-status__configure"
              onClick={() => {
                close();
                onConfigure();
              }}
            >
              {state === "unconfigured"
                ? "补全配置"
                : state === "offline"
                  ? "连接设置"
                  : "查看配置"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
