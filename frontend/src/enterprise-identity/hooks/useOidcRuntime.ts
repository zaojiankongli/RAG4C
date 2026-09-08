import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import { KNOWLEDGE_ACTOR_TOKEN_STORAGE_KEY } from "../../knowledge/workspaceScope";
import { completeOidcCallback, startOidcLogin } from "../api/enterpriseIdentityApi";
import type { OidcCallbackInput, OidcCallbackResult } from "../enterpriseIdentityModel";
import { clearOidcCallbackLocation } from "../oidcRuntimeRoute";

export interface OidcProviderRef {
  id: string;
  name: string;
}
export interface OidcRuntimeError {
  title: string;
  message: string;
  status?: number;
}
function recordValue(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}
function apiErrorCode(error: ApiError): string | null {
  const body = recordValue(error.body);
  const detail = recordValue(body?.detail);
  const value = detail?.code ?? body?.code;
  return typeof value === "string" && value.trim() ? value.trim() : null;
}
function errorOf(error: unknown): OidcRuntimeError {
  if (error instanceof ApiError) {
    const code = apiErrorCode(error);
    if (code === "oidc_state_expired")
      return {
        title: "OIDC 登录事务已过期",
        message: "登录窗口已过期，请重新启动登录。",
        status: error.status,
      };
    if (code === "oidc_state_consumed")
      return {
        title: "OIDC 登录事务已使用",
        message: "state 已消费或已失效，请重新启动登录。",
        status: error.status,
      };
    if (error.status === 401)
      return {
        title: "OIDC 登录身份校验失败",
        message: "请重新从企业身份中心启动登录。",
        status: 401,
      };
    if (error.status === 403)
      return {
        title: "OIDC 登录未授权",
        message: "签名账户不是当前租户的 active member。",
        status: 403,
      };
    if (error.status === 409)
      return {
        title: "OIDC 登录事务已使用",
        message: "state 已消费或已失效，请重新启动登录。",
        status: 409,
      };
    if (error.status === 410)
      return {
        title: "OIDC 登录事务已过期",
        message: "登录窗口已过期，请重新启动登录。",
        status: 410,
      };
    if (error.status === 503 || error.kind === "network" || error.kind === "timeout")
      return {
        title: "OIDC runtime 暂不可用",
        message: "回调结果未确认；为避免重复消费 code，请重新启动登录。",
        status: error.status,
      };
  }
  return { title: "OIDC 登录未完成", message: "服务端返回了安全的失败状态，请重新启动登录。" };
}
export function useOidcRuntimeStart({
  scope,
  navigate = (url) => window.location.assign(url),
}: {
  scope: EnterpriseScope;
  navigate?: (url: string) => void;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<OidcRuntimeError | null>(null);
  const busy = useRef(false);
  const start = useCallback(
    async (provider: OidcProviderRef, redirectUri: string) => {
      if (busy.current) return null;
      busy.current = true;
      setSaving(true);
      setError(null);
      try {
        const result = await startOidcLogin(scope, {
          provider_id: provider.id,
          redirect_uri: redirectUri,
        });
        navigate(result.authorization_url);
        return result;
      } catch (reason) {
        setError(errorOf(reason));
        return null;
      } finally {
        busy.current = false;
        setSaving(false);
      }
    },
    [navigate, scope],
  );
  return { saving, error, start, clearError: () => setError(null) };
}
export type OidcCallbackStatus = "loading" | "success" | "error";
export function useOidcCallback(credentials: OidcCallbackInput) {
  const [status, setStatus] = useState<OidcCallbackStatus>("loading");
  const [result, setResult] = useState<OidcCallbackResult | null>(null);
  const [error, setError] = useState<OidcRuntimeError | null>(null);
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    clearOidcCallbackLocation();
    const submission = completeOidcCallback(credentials);
    void submission
      .then((delivery) => {
        localStorage.setItem(KNOWLEDGE_ACTOR_TOKEN_STORAGE_KEY, delivery.knowledge_actor_token);
        setResult(delivery.result);
        setStatus("success");
      })
      .catch((reason) => {
        setError(errorOf(reason));
        setStatus("error");
      });
  }, [credentials]);
  return { status, result, error };
}
