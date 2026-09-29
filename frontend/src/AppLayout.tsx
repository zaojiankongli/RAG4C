import type { ReactNode, RefObject } from "react";
import { Button, Layout, Tooltip } from "./ui/index";
import {
  DeploymentUnitOutlined,
  InfoCircleOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MoonOutlined,
  ReloadOutlined,
  SunOutlined,
} from "./ui/icons";
import ServiceStatusNotice from "./components/ServiceStatusNotice";
import { WorkspaceScopeBar } from "./ui/enterprise";
import type { WorkspaceScopeBarProps } from "./ui/enterprise/WorkspaceScopeBar";
import type { ThemeMode } from "./theme/tokens";
import { type PageKey } from "./run/appRoute";
import WorkspaceNavigation from "./shell/WorkspaceNavigation";
import ThemePicker from "./theme/ThemePicker";
import { getThemeModeSpec, nextThemeMode } from "./theme/themeModeRegistry";

const { Sider } = Layout;
const PRIMARY_NAV_ID = "primary-navigation";

interface ConnectionStatus {
  tooltip: string;
  label: string;
  dotClass: string;
  checking: boolean;
  onRefresh: () => void;
}

interface Props {
  page: PageKey;
  isMobile: boolean;
  mobileNavOpen: boolean;
  siderCollapsed: boolean;
  themeMode: ThemeMode;
  mobileNavToggleRef: RefObject<HTMLButtonElement>;
  mobileNavRef: RefObject<HTMLElement>;
  appContentRef: RefObject<HTMLDivElement>;
  connection: ConnectionStatus;
  workspaceScopeBar: WorkspaceScopeBarProps;
  onNavigate: (key: PageKey) => void;
  onBreakpoint: (broken: boolean) => void;
  onCollapse: (next: boolean) => void;
  onToggleSider: () => void;
  onToggleTheme: () => void;
  onSetTheme?: (mode: ThemeMode) => void;
  onOpenOnboarding: () => void;
  onOpenMobileNavigation: () => void;
  onCloseMobileNavigation: () => void;
  children: ReactNode;
  overlays?: ReactNode;
}

/** Responsive application shell; all route and authority state remains in App. */
export default function AppLayout({
  page,
  isMobile,
  mobileNavOpen,
  siderCollapsed,
  themeMode,
  mobileNavToggleRef,
  mobileNavRef,
  appContentRef,
  connection,
  workspaceScopeBar,
  onNavigate,
  onBreakpoint,
  onCollapse,
  onToggleSider,
  onToggleTheme,
  onSetTheme,
  onOpenOnboarding,
  onOpenMobileNavigation,
  onCloseMobileNavigation,
  children,
  overlays,
}: Props) {
  return (
    <Layout className="app-layout workspace-layout">
      <a
        className="skip-link"
        href="#main-content"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById("main-content")?.focus();
        }}
      >
        跳到主内容
      </a>
      <Sider
        width={256}
        theme="light"
        className={isMobile ? "app-sider is-mobile" : "app-sider"}
        aria-hidden={isMobile && !mobileNavOpen ? true : undefined}
        inert={isMobile && !mobileNavOpen ? "" : undefined}
        collapsible
        breakpoint="md"
        collapsedWidth={isMobile ? 0 : 72}
        collapsed={siderCollapsed}
        onBreakpoint={onBreakpoint}
        onCollapse={onCollapse}
        trigger={null}
      >
        <div className="sider-brand">
          <div className="brand-logo" aria-hidden="true">
            <span className="brand-default-icon">
              <DeploymentUnitOutlined />
            </span>
            <span className="brand-yanami-icon" />
          </div>
          {!siderCollapsed && (
            <div className="brand-content">
              {/* 这里原本也是 <h1>，与 PageTopbar 的页面标题构成同页两个 h1，
                  读屏的标题大纲会出现两个并列的一级标题。品牌名不是页面主题，
                  降级为普通元素，h1 只留给 PageTopbar。 */}
              <div className="brand-title">RAG4C</div>
              <div className="brand-sub">
                {themeMode === "anime" ? "八奈见 · 知识放课后" : "企业知识库平台"}
              </div>
            </div>
          )}
        </div>

        <nav
          ref={mobileNavRef}
          id={PRIMARY_NAV_ID}
          className="app-nav"
          aria-label="主导航"
          tabIndex={0}
        >
          <WorkspaceNavigation page={page} collapsed={siderCollapsed} onNavigate={onNavigate} />
        </nav>

        <div className="sider-foot">
          {onSetTheme && (
            <ThemePicker mode={themeMode} collapsed={siderCollapsed} onChange={onSetTheme} />
          )}
          {!siderCollapsed && (
            <Tooltip title={connection.tooltip}>
              <div className="conn-pill">
                <span className={"status-dot " + connection.dotClass} aria-hidden="true" />
                <span className="conn-text">{connection.label}</span>
                <Button
                  type="text"
                  size="small"
                  aria-label="重新检查服务连接"
                  icon={<ReloadOutlined />}
                  loading={connection.checking}
                  onClick={connection.onRefresh}
                />
              </div>
            </Tooltip>
          )}
          <div className={siderCollapsed ? "sider-actions is-collapsed" : "sider-actions"}>
            <Tooltip title={siderCollapsed ? "展开侧栏" : "收起侧栏"} placement="right">
              <Button
                type="text"
                size="small"
                aria-label={siderCollapsed ? "展开侧栏" : "收起侧栏"}
                icon={siderCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
                onClick={onToggleSider}
              />
            </Tooltip>
            {!onSetTheme && (
              <Tooltip
                title={`切换到${getThemeModeSpec(nextThemeMode(themeMode)).label}主题`}
                placement="right"
              >
                <Button
                  type="text"
                  size="small"
                  aria-label={`切换到${getThemeModeSpec(nextThemeMode(themeMode)).label}主题`}
                  icon={themeMode === "light" ? <MoonOutlined /> : <SunOutlined />}
                  onClick={onToggleTheme}
                />
              </Tooltip>
            )}
            <Tooltip title={siderCollapsed ? "新手引导" : "重看新手引导"} placement="right">
              <Button
                type="text"
                size="small"
                aria-label="重看新手引导"
                icon={<InfoCircleOutlined />}
                onClick={onOpenOnboarding}
              />
            </Tooltip>
          </div>
        </div>
      </Sider>
      {isMobile && mobileNavOpen && (
        <button
          type="button"
          className="mobile-nav-backdrop"
          aria-label="关闭导航菜单"
          aria-controls={PRIMARY_NAV_ID}
          tabIndex={-1}
          onClick={() => onCloseMobileNavigation()}
        />
      )}

      <div ref={appContentRef} className="app-content">
        {isMobile && (
          <Button
            ref={mobileNavToggleRef}
            className={mobileNavOpen ? "mobile-nav-toggle is-open" : "mobile-nav-toggle"}
            type="text"
            size="small"
            aria-label={mobileNavOpen ? "关闭导航" : "打开导航"}
            aria-controls={PRIMARY_NAV_ID}
            aria-expanded={mobileNavOpen}
            icon={mobileNavOpen ? <MenuFoldOutlined /> : <MenuUnfoldOutlined />}
            onClick={() => (mobileNavOpen ? onCloseMobileNavigation() : onOpenMobileNavigation())}
          />
        )}
        <div className="enterprise-shell-header">
          <WorkspaceScopeBar
            {...workspaceScopeBar}
            healthControl={<ServiceStatusNotice onConfigure={() => onNavigate("config")} />}
          />
        </div>
        <main id="main-content" className="page-slot" tabIndex={-1}>
          {children}
        </main>
      </div>
      {overlays}
    </Layout>
  );
}
