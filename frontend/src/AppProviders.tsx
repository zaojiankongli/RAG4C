import { ConfigProvider } from "tdesign-react";
import zhCN from "tdesign-react/es/locale/zh_CN";
import App from "./App";
import { ConnectionProvider } from "./context/ConnectionContext";
import { KnowledgeWorkspaceProvider } from "./knowledge/KnowledgeWorkspaceContext";
import { KnowledgeBaseDrawerCoordinatorProvider } from "./enterprise-knowledge-base-shell/KnowledgeBaseDrawerCoordinator";
import { RunMonitorProvider } from "./run/RunMonitorContext";
import { useThemeMode } from "./theme/useThemeMode";

export default function AppProviders() {
  const { mode, toggle, setMode } = useThemeMode();
  return (
    <ConfigProvider globalConfig={zhCN}>
      <ConnectionProvider>
        <KnowledgeWorkspaceProvider>
          <KnowledgeBaseDrawerCoordinatorProvider>
            <RunMonitorProvider>
              <App themeMode={mode} onToggleTheme={toggle} onSetTheme={setMode} />
            </RunMonitorProvider>
          </KnowledgeBaseDrawerCoordinatorProvider>
        </KnowledgeWorkspaceProvider>
      </ConnectionProvider>
    </ConfigProvider>
  );
}
