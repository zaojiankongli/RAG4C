export interface AccessibleTabLabelProps {
  label: string;
  active: boolean;
  onActivate: () => void;
}

export default function AccessibleTabLabel({ label, active, onActivate }: AccessibleTabLabelProps) {
  return (
    <span
      role="tab"
      aria-selected={active}
      tabIndex={active ? 0 : -1}
      onClick={onActivate}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onActivate();
        }
      }}
    >
      {label}
    </span>
  );
}
