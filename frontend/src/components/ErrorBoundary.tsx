import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";
import { Alert, Button, Space } from "../ui/index";
import { ReloadOutlined } from "../ui/icons";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * 页面级错误边界：渲染异常时展示可恢复的错误面板，
 * 避免整个控制台白屏（企业级兜底）。
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[ErrorBoundary]", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (error) {
      return (
        <div style={{ padding: 48, maxWidth: 640, margin: "0 auto" }}>
          <Alert
            type="error"
            showIcon
            message="页面渲染出错"
            description={error.name + ": " + error.message + "（详细信息见开发者控制台）"}
          />
          <Space style={{ marginTop: 12 }}>
            <Button icon={<ReloadOutlined />} onClick={() => this.setState({ error: null })}>
              重试渲染
            </Button>
            <Button onClick={() => window.location.reload()}>刷新应用</Button>
          </Space>
        </div>
      );
    }
    return this.props.children;
  }
}
