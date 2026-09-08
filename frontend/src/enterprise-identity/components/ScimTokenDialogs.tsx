import { useEffect, useMemo, useState } from "react";
import dayjs from "dayjs";
import { Alert, Button, DatePicker, Dialog, Form, Input, Select, Textarea } from "tdesign-react";
import { CopyIcon } from "tdesign-icons-react";
import type {
  IssueScimTokenInput,
  ScimTokenDelivery,
  ScimTokenMutationResult,
} from "../enterpriseIdentityModel";
import type { IdentityMutationError } from "../hooks/useEnterpriseIdentityMutations";

export function ScimTokenIssueDialog({
  visible,
  saving,
  error,
  onClose,
  onRetry,
  onSubmit,
  dataPlaneReady = false,
}: {
  visible: boolean;
  saving: boolean;
  error: IdentityMutationError | null;
  onClose: () => void;
  onRetry: () => Promise<unknown>;
  onSubmit: (input: IssueScimTokenInput) => Promise<ScimTokenMutationResult | null>;
  dataPlaneReady?: boolean;
}) {
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["users:read"]);
  const [expiry, setExpiry] = useState("");
  const [reason, setReason] = useState("");
  const [validation, setValidation] = useState("");
  useEffect(() => {
    if (visible) {
      setName("");
      setScopes(["users:read"]);
      setExpiry(dayjs().add(30, "day").format("YYYY-MM-DD"));
      setReason("");
      setValidation("");
    }
  }, [visible]);
  if (!visible) return null;
  const submit = async () => {
    const days = dayjs(expiry).startOf("day").diff(dayjs().startOf("day"), "day");
    if (!name.trim() || !reason.trim() || !scopes.length || days < 1) {
      setValidation("Token 名称、scope、有效期和签发原因不能为空。");
      return;
    }
    const result = await onSubmit({
      name: name.trim(),
      scopes,
      expires_in_days: days,
      reason: reason.trim(),
    });
    if (result) onClose();
  };
  return (
    <Dialog
      visible
      header="签发 SCIM token"
      width={580}
      destroyOnClose
      confirmBtn={{ content: "确认签发", theme: "primary" }}
      cancelBtn={{ content: "取消", disabled: saving }}
      confirmLoading={saving}
      onClose={onClose}
      onConfirm={() => void submit()}
      {...({ role: "dialog", "aria-modal": "true", "aria-label": "签发 SCIM token" } as Record<
        string,
        unknown
      >)}
    >
      {error ? (
        <div className="identity-mutation-error" role="alert">
          <Alert theme="error" title="SCIM token 未签发" message={error.message} />
          {error.retryAvailable ? (
            <Button variant="text" onClick={() => void onRetry()}>
              使用同一请求重试
            </Button>
          ) : null}
        </div>
      ) : null}
      {validation ? <Alert theme="warning" title="请补齐签发信息" message={validation} /> : null}
      <Alert
        theme={dataPlaneReady ? "success" : "info"}
        title={dataPlaneReady ? "scim_data_plane_ready" : "scim_data_plane_not_connected"}
        message={
          dataPlaneReady
            ? "0022 数据面已就绪；签发的 token 可按所选 scope 调用 /scim/v2。"
            : "尚未完成 0022 数据面迁移；当前仅提供 token 控制面。"
        }
      />
      <Form className="identity-scim-form" labelAlign="top">
        <Form.FormItem>
          <label className="identity-form-field">
            <span>Token 名称</span>
            <Input value={name} onChange={(value) => setName(String(value))} />
          </label>
        </Form.FormItem>
        <Form.FormItem>
          <label className="identity-form-field">
            <span>Scopes</span>
            <Select
              multiple
              value={scopes}
              options={[
                { label: "users:read", value: "users:read" },
                { label: "users:write", value: "users:write" },
                { label: "groups:read", value: "groups:read" },
                { label: "groups:write", value: "groups:write" },
              ]}
              onChange={(value) => setScopes((value as string[]) ?? [])}
            />
          </label>
        </Form.FormItem>
        <Form.FormItem>
          <label className="identity-form-field">
            <span>到期日期</span>
            <DatePicker value={expiry} onChange={(value) => setExpiry(String(value))} />
          </label>
        </Form.FormItem>
        <Form.FormItem className="identity-form-wide">
          <label className="identity-form-field">
            <span>签发原因</span>
            <Textarea
              value={reason}
              autosize={{ minRows: 3, maxRows: 5 }}
              onChange={(value) => setReason(String(value))}
            />
          </label>
        </Form.FormItem>
      </Form>
    </Dialog>
  );
}

export function ScimTokenDeliveryDialog({
  delivery,
  onClose,
  dataPlaneReady = false,
}: {
  delivery: ScimTokenDelivery | null;
  onClose: () => void;
  dataPlaneReady?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const [revealed, setRevealed] = useState(false);
  const raw = delivery?.scim_token ?? "";
  const masked = useMemo(() => (raw ? `${raw.slice(0, 8)}${"•".repeat(24)}` : ""), [raw]);
  useEffect(() => {
    setCopied(false);
    setRevealed(false);
  }, [raw]);
  if (!delivery) return null;
  const copy = async () => {
    if (!raw) return;
    await navigator.clipboard.writeText(raw);
    setCopied(true);
  };
  return (
    <Dialog
      visible
      header="一次性 SCIM token"
      width={620}
      destroyOnClose
      cancelBtn={null}
      confirmBtn={{ content: "关闭" }}
      onClose={onClose}
      onConfirm={onClose}
      {...({ role: "dialog", "aria-modal": "true", "aria-label": "一次性 SCIM token" } as Record<
        string,
        unknown
      >)}
    >
      <Alert
        theme="warning"
        title="token_returned_once"
        message="原始 token 仅在本次响应中返回；关闭后前端立即清除。"
      />
      <Alert
        theme={dataPlaneReady ? "success" : "info"}
        title={dataPlaneReady ? "scim_data_plane_ready" : "scim_data_plane_not_connected"}
        message={
          dataPlaneReady
            ? "将 token 存入外部 IdP 后，可按 scope 调用 /scim/v2 Users/Groups。"
            : "当前 token 还不能用于 Users/Groups provisioning，因为 0022 数据面尚未就绪。"
        }
      />
      <div className="identity-one-time-token">
        <Input
          type={revealed ? "text" : "password"}
          value={revealed ? raw : masked}
          readOnly
          aria-label="一次性 SCIM token（已隐藏）"
        />
        <Button variant="outline" onClick={() => setRevealed((value) => !value)}>
          {revealed ? "隐藏" : "显示"}
        </Button>
        <Button theme="primary" icon={<CopyIcon />} onClick={() => void copy()}>
          复制 SCIM token
        </Button>
      </div>
      {copied ? (
        <Alert
          theme="success"
          title="SCIM token 已复制"
          message="请存入企业密钥管理系统；不要粘贴到日志、工单或聊天记录。"
        />
      ) : null}
    </Dialog>
  );
}
