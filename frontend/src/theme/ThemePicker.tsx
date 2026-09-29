import { MoonOutlined, SunOutlined } from "../ui/icons";
import {
  getThemeModeSpec,
  nextThemeMode,
  themeModeNames,
  type ThemeMode,
} from "./themeModeRegistry";
import "./theme-picker.css";

interface Props {
  mode: ThemeMode;
  collapsed?: boolean;
  onChange: (mode: ThemeMode) => void;
}

export default function ThemePicker({ mode, collapsed = false, onChange }: Props) {
  if (collapsed) {
    const next = nextThemeMode(mode);
    return (
      <button
        className="theme-picker-cycle"
        type="button"
        aria-label={`当前${getThemeModeSpec(mode).label}主题，切换到${getThemeModeSpec(next).label}主题`}
        title={`当前：${getThemeModeSpec(mode).label} · 点击切换主题`}
        onClick={() => onChange(next)}
      >
        {mode === "dark" ? (
          <MoonOutlined />
        ) : mode === "light" ? (
          <SunOutlined />
        ) : (
          <span className="theme-picker-bow" aria-hidden="true" />
        )}
      </button>
    );
  }

  return (
    <div className="theme-picker" role="group" aria-label="外观主题">
      {themeModeNames.map((value) => (
        <button
          key={value}
          type="button"
          className={`theme-picker-option theme-picker-option--${value}`}
          aria-pressed={mode === value}
          aria-label={`使用${getThemeModeSpec(value).label}主题`}
          title={getThemeModeSpec(value).description}
          onClick={() => onChange(value)}
        >
          {value === "light" ? (
            <SunOutlined />
          ) : value === "dark" ? (
            <MoonOutlined />
          ) : (
            <span className="theme-picker-bow" aria-hidden="true" />
          )}
          <span>{getThemeModeSpec(value).label}</span>
        </button>
      ))}
    </div>
  );
}
