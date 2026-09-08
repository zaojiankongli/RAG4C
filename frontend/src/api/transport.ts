/**
 * 桥服务 HTTP transport 层。
 *
 * 负责与 FastAPI 桥的底层 HTTP 交互：地址解析、超时/取消、统一错误、
 * JSON 序列化、SSE 流式解析。**不含任何业务领域函数**（query / documents /
 * eval / config / metrics 等 API 在 client.ts 按域组织）。
 *
 * 设计说明：
 * - 从 client.ts 拆出，行为完全等价；client.ts 重新导出本模块，故既有
 *   ``from "../api/client"`` 的调用路径不变（228 测试守护）。
 * - transport 层只持有"如何发请求"的通用能力，不感知业务契约。
 */

import type { BackendRunEvent, QueryResponse, StreamRunEventDesync } from "../types/rag";
import { parseBackendRunEvent } from "./runEventValidation";

/** 后端默认地址；打包成 Tauri 桌面端后同机运行，仍是这个 */
export const DEFAULT_BASE = "http://localhost:8000";

export function getBaseUrl(): string {
  // 允许在浏览器 / Tauri 中通过 localStorage 覆盖后端地址
  const stored = localStorage.getItem("rag4c.base_url");
  return stored && stored.trim() ? stored.trim() : DEFAULT_BASE;
}

/**
 * 覆盖后端地址；传空串表示恢复默认。
 *
 * 缺陷记录：这个函数曾经全仓零调用方——地址只能靠手敲
 * `localStorage.setItem("rag4c.base_url", ...)` 修改。而打包后的 Tauri 窗口
 * 没有开发者工具，用户根本没有执行这行代码的地方，后端只要不在
 * localhost:8000，应用就永远连不上且无从补救。现在由设置页负责暴露。
 */
export function setBaseUrl(url: string): void {
  const next = url.trim();
  if (next) {
    localStorage.setItem("rag4c.base_url", next);
  } else {
    localStorage.removeItem("rag4c.base_url");
  }
}

export type ApiErrorKind = "timeout" | "aborted" | "http" | "network";

/** 结构化 API 错误：调用方可按 kind 分支处理（超时 / 取消 / HTTP 状态 / 网络） */
export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status?: number;
  readonly body?: unknown;
  readonly retryAfter?: string;

  constructor(
    message: string,
    kind: ApiErrorKind,
    status?: number,
    body?: unknown,
    retryAfter?: string,
  ) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
    this.body = body;
    this.retryAfter = retryAfter;
  }
}

export interface RequestOptions extends RequestInit {
  /** 超时毫秒数；默认 15s */
  timeoutMs?: number;
}

const DEFAULT_TIMEOUT_MS = 15_000;

async function responseError(response: Response): Promise<{ message: string; body?: unknown }> {
  const fallback = `HTTP ${response.status}`;
  const text = await response.text();
  if (!text.trim()) return { message: fallback };
  try {
    const body = JSON.parse(text) as { error?: { message?: unknown }; detail?: unknown };
    const detail = body.detail;
    const message =
      body.error?.message ??
      (typeof detail === "object" && detail !== null && "message" in detail
        ? (detail as { message?: unknown }).message
        : detail);
    return { message: typeof message === "string" && message.trim() ? message : fallback, body };
  } catch {
    return { message: `${fallback}: ${text.slice(0, 300)}` };
  }
}

export async function request<T>(path: string, init?: RequestOptions): Promise<T> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, signal: outerSignal, headers, ...rest } = init ?? {};

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  const onOuterAbort = () => ctrl.abort();
  outerSignal?.addEventListener("abort", onOuterAbort, { once: true });
  if (outerSignal?.aborted) ctrl.abort();

  try {
    const res = await fetch(`${getBaseUrl()}${path}`, {
      headers: { "Content-Type": "application/json", ...headers },
      signal: ctrl.signal,
      ...rest,
    });
    if (!res.ok) {
      const error = await responseError(res);
      throw new ApiError(
        error.message,
        "http",
        res.status,
        error.body,
        res.headers.get("Retry-After") ?? undefined,
      );
    }
    if (res.status === 204) return undefined as T;
    return (await res.json()) as Promise<T>;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    if (outerSignal?.aborted) {
      throw new ApiError("请求已取消", "aborted");
    }
    if (ctrl.signal.aborted) {
      throw new ApiError("请求超时（" + timeoutMs + "ms）", "timeout");
    }
    throw new ApiError("网络请求失败：" + String(e), "network");
  } finally {
    clearTimeout(timer);
    outerSignal?.removeEventListener("abort", onOuterAbort);
  }
}

export interface QueryPayload {
  query: string;
  acl?: string[] | null;
  retry?: boolean;
}

export interface StreamHandlers {
  onRunEvent?: (event: BackendRunEvent) => void;
  onRunEventDesync?: (marker: StreamRunEventDesync) => void;
  onRunEventInvalid?: () => void;
  onPhase?: (phase: string, data: Record<string, unknown>) => void;
  onToken?: (text: string) => void;
}

/**
 * SSE 流式问答（/api/query/stream）。
 * 逐 token 回调 onToken，阶段事件回调 onPhase；最终 resolve 为 QueryResponse
 * （done 事件携带，与 /api/query 响应同构）。signal 取消即「停止生成」——
 * 后端会关闭底层 LLM 流，停止在服务端真正生效。
 */
export async function streamAnswer(
  payload: QueryPayload,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<QueryResponse> {
  let res: Response;
  try {
    res = await fetch(getBaseUrl() + "/api/query/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (error) {
    if (signal?.aborted) throw new ApiError("请求已取消", "aborted");
    throw new ApiError("网络请求失败：" + String(error), "network");
  }
  if (!res.ok) {
    const error = await responseError(res);
    throw new ApiError(
      error.message,
      "http",
      res.status,
      error.body,
      res.headers.get("Retry-After") ?? undefined,
    );
  }

  const reader = res.body?.getReader();
  if (!reader) throw new ApiError("流式响应不可读", "network");
  const decoder = new TextDecoder();
  let buffer = "";
  let final: QueryResponse | null = null;

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data:")) continue;
      const data = trimmed.slice(5).trim();
      if (!data || data === "[DONE]") continue;
      let event: { type?: string };
      try {
        event = JSON.parse(data) as { type?: string };
      } catch {
        continue;
      }
      switch (event.type) {
        case "run_event": {
          try {
            const runEvent = parseBackendRunEvent((event as { event?: unknown }).event);
            handlers.onRunEvent?.(runEvent);
          } catch {
            handlers.onRunEventInvalid?.();
          }
          break;
        }
        case "run_event_desync": {
          const marker = event as Partial<StreamRunEventDesync>;
          if (
            typeof marker.run_id === "string" &&
            Number.isInteger(marker.expected_seq) &&
            Number(marker.expected_seq) >= 1 &&
            marker.reason === "typed_buffer_overflow"
          ) {
            handlers.onRunEventDesync?.(marker as StreamRunEventDesync);
          }
          break;
        }
        case "phase":
          handlers.onPhase?.(
            (event as { phase?: string }).phase ?? "",
            event as Record<string, unknown>,
          );
          break;
        case "token":
          handlers.onToken?.((event as { text?: string }).text ?? "");
          break;
        case "done":
          final = (event as { result?: QueryResponse }).result ?? null;
          break;
        case "error":
          throw new ApiError((event as { message?: string }).message ?? "流式错误", "network");
        default:
          break;
      }
    }
  }
  if (!final) throw new ApiError("流式响应未包含最终结果", "network");
  return final;
}
