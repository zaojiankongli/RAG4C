import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import { fetchHealth } from "../api/client";
import { DEMO_HEALTH } from "../api/mock";
import type { HealthInfo } from "../types/rag";

interface ConnectionContextValue {
  /** null=探测中, true=在线（真实链路）, false=离线（演示数据） */
  online: boolean | null;
  checking: boolean;
  health: HealthInfo | null;
  refresh: () => Promise<void>;
}

const ConnectionContext = createContext<ConnectionContextValue | null>(null);

/**
 * 桥服务连接状态全局共享：
 * 启动时探测一次 /api/health；各页面统一从这里读取在线状态，
 * 不再各自重复「探测 → 降级」逻辑。
 */
export function ConnectionProvider({ children }: { children: ReactNode }) {
  const [online, setOnline] = useState<boolean | null>(null);
  const [checking, setChecking] = useState(true);
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const requestRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setChecking(true);
    try {
      const h = await fetchHealth(controller.signal);
      if (controller.signal.aborted) return;
      setHealth(h);
      setOnline(true);
    } catch {
      if (controller.signal.aborted) return;
      setHealth(DEMO_HEALTH);
      setOnline(false);
    } finally {
      if (requestRef.current === controller) {
        requestRef.current = null;
        setChecking(false);
      }
    }
  }, []);
  useEffect(() => {
    void refresh();
    return () => requestRef.current?.abort();
  }, [refresh]);

  const value = useMemo(
    () => ({ online, checking, health, refresh }),
    [online, checking, health, refresh],
  );

  return <ConnectionContext.Provider value={value}>{children}</ConnectionContext.Provider>;
}

export function useConnection(): ConnectionContextValue {
  const ctx = useContext(ConnectionContext);
  if (!ctx) {
    throw new Error("useConnection 必须在 ConnectionProvider 内使用");
  }
  return ctx;
}
