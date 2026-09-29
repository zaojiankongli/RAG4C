/**
 * 新手引导弹窗（八奈见杏菜主题）。
 *
 * 首次使用显示引导；完成/跳过后写入 localStorage["rag4c.onboarding.v1"]，
 * 不再自动弹出。兼容旧键 rag4c.onboarding_done。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { MessageCircle, Database, BarChart3, Sparkles, X } from "lucide-react";

const STORAGE_KEY = "rag4c.onboarding.v1";
const LEGACY_STORAGE_KEY = "rag4c.onboarding_done";

const STEPS = [
  {
    icon: Sparkles,
    title: "欢迎来到 RAG4C",
    body: "你的企业知识库 AI 问答助手。我已经为你准备好二次元主题，开始探索吧！",
  },
  {
    icon: MessageCircle,
    title: "提问试试",
    body: "在「知识问答」页输入问题，AI 会基于知识库内容回答，并附上引用来源。",
  },
  {
    icon: Database,
    title: "管理知识库",
    body: "在「文档管理」上传文档、在「知识概览」查看入库情况，知识越多回答越准。",
  },
  {
    icon: BarChart3,
    title: "关注质量",
    body: "「运行监控」页展示问答耗时与验证质量，「质量评测」页可跑评估集检验效果。",
  },
];

function isOnboardingDone(): boolean {
  return (
    localStorage.getItem(STORAGE_KEY) === "1" ||
    localStorage.getItem(LEGACY_STORAGE_KEY) === "1"
  );
}

export function shouldShowOnboarding(): boolean {
  // 测试环境不自动弹引导（避免遮住被测内容）；localStorage 持久化已引导标记
  if (import.meta.env.MODE === "test") return false;
  return !isOnboardingDone();
}

export function dismissOnboarding(): void {
  localStorage.setItem(STORAGE_KEY, "1");
  localStorage.setItem(LEGACY_STORAGE_KEY, "1");
}

export function OnboardingTour({
  onNavigate,
  defaultOpen = false,
  onClose,
}: {
  onNavigate: (page: string) => void;
  /** 手动打开（设置页/顶部栏"重开引导"用）；默认自动检测首次使用 */
  defaultOpen?: boolean;
  /** 关闭回调（手动打开场景用于复位父级 state） */
  onClose?: () => void;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [step, setStep] = useState(0);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (defaultOpen) {
      setOpen(true);
    } else if (shouldShowOnboarding()) {
      setOpen(true);
    }
  }, [defaultOpen]);

  const close = useCallback(() => {
    dismissOnboarding();
    setOpen(false);
    onClose?.();
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    const dialog = dialogRef.current;
    if (!dialog) return;
    closeButtonRef.current?.focus();
    const handleDialogKeydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      );
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) return;
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !dialog.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || !dialog.contains(active))) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", handleDialogKeydown);
    return () => window.removeEventListener("keydown", handleDialogKeydown);
  }, [close, open]);

  const next = useCallback(() => {
    if (step < STEPS.length - 1) {
      setStep((s) => s + 1);
    } else {
      close();
    }
  }, [step, close]);

  const goto = useCallback(
    (page: string) => {
      onNavigate(page);
      setStep(0);
      close();
    },
    [onNavigate, close],
  );

  if (!open) return null;
  const current = STEPS[step];
  const Icon = current.icon;
  const isLast = step === STEPS.length - 1;

  return (
    <div
      ref={dialogRef}
      className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/30 backdrop-blur-sm p-4"
      role="dialog"
      aria-modal="true"
      aria-label="新手引导"
    >
      <div className="w-full max-w-md rounded-[24px] bg-white p-6 shadow-[0_16px_48px_rgba(255,127,165,0.25)] border border-pink-100 relative">
        <button
          ref={closeButtonRef}
          type="button"
          onClick={close}
          autoFocus
          aria-label="关闭引导"
          className="absolute top-4 right-4 p-1.5 rounded-full text-pink-300 hover:bg-pink-50 hover:text-pink-500 transition-colors"
        >
          <X size={18} />
        </button>

        <div
          className="w-14 h-14 rounded-[18px] flex items-center justify-center text-white mb-4"
          style={{
            background: "linear-gradient(135deg, #e85d7a, #d6456a)",
            color: "var(--color-on-primary, #ffffff)",
          }}
        >
          <Icon size={26} />
        </div>
        <h2 className="text-xl font-bold text-gray-800 mb-2" style={{ color: "#3d2c35" }}>
          {current.title}
        </h2>
        <p className="text-sm leading-relaxed mb-6" style={{ color: "#7a6470" }}>
          {current.body}
        </p>

        {/* 步骤指示 */}
        <div className="flex gap-1.5 mb-5" aria-hidden="true">
          {STEPS.map((_, i) => (
            <span
              key={i}
              className="h-1.5 rounded-full transition-all duration-300"
              style={{
                width: i === step ? 20 : 8,
                background: i === step ? "linear-gradient(90deg, #e85d7a, #d6456a)" : "#ffe0e8",
              }}
            />
          ))}
        </div>

        <div className="flex items-center justify-between">
          <div className="flex gap-2">
            {step === 1 && (
              <button
                type="button"
                onClick={() => goto("query")}
                className="px-3 py-1.5 text-xs rounded-full border border-pink-200 text-pink-500 hover:bg-pink-50 transition-colors"
              >
                去提问
              </button>
            )}
            {step === 2 && (
              <button
                type="button"
                onClick={() => goto("documents")}
                className="px-3 py-1.5 text-xs rounded-full border border-pink-200 text-pink-500 hover:bg-pink-50 transition-colors"
              >
                去文档管理
              </button>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={close}
              aria-label="跳过引导"
              className="px-4 py-2 text-sm text-pink-400 hover:text-pink-600 transition-colors"
            >
              跳过
            </button>
            <button
              type="button"
              onClick={next}
              aria-label={isLast ? "开始使用" : "下一步"}
              className="px-5 py-2 text-sm font-medium rounded-full shadow-md transition-all hover:shadow-lg"
              style={{
                background: "var(--color-primary, #3164f4)",
                color: "var(--color-on-primary, #ffffff)",
              }}
            >
              {isLast ? "开始使用" : "下一步"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
