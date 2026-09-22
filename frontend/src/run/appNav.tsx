import type { MenuProps } from "../ui/index";
import {
  ApartmentOutlined,
  BellOutlined,
  BranchesOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  DeploymentUnitOutlined,
  FileSearchOutlined,
  FolderOpenOutlined,
  LineChartOutlined,
  MessageOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
  TagsOutlined,
  TeamOutlined,
} from "../ui/icons";
import type { PageKey } from "./appRoute";

/**
 * 主导航清单。单独成一份模块，是为了让可达性守卫（`appRoute.reachability.test.ts`）
 * 直接读这张表，而不必把整个 App 连同每个懒加载页面一起拖进测试。
 */
export const MENU_ITEMS: MenuProps["items"] = [
  {
    type: "group",
    label: "智能问答",
    children: [
      { key: "query", icon: <MessageOutlined />, label: "知识问答" },
      { key: "visualize", icon: <ApartmentOutlined />, label: "回答过程" },
      { key: "retrieval-lab", icon: <FileSearchOutlined />, label: "检索调试" },
    ],
  },
  {
    type: "group",
    label: "知识库",
    children: [
      { key: "overview", icon: <DatabaseOutlined />, label: "知识概览" },
      { key: "documents", icon: <FolderOpenOutlined />, label: "文档管理" },
      { key: "parse-intervention", icon: <FileSearchOutlined />, label: "解析干预" },
      { key: "taxonomy", icon: <TagsOutlined />, label: "知识组织" },
      { key: "sources", icon: <CloudServerOutlined />, label: "数据来源" },
      { key: "knowledge-bases", icon: <ApartmentOutlined />, label: "知识库注册表" },
      { key: "recycle-bin", icon: <DeleteOutlined />, label: "回收站" },
    ],
  },
  {
    type: "group",
    label: "质量与运维",
    children: [
      { key: "eval", icon: <SafetyCertificateOutlined />, label: "质量评测" },
      { key: "monitor", icon: <LineChartOutlined />, label: "运行监控" },
      { key: "consistency", icon: <BranchesOutlined />, label: "一致性控制台" },
      { key: "governance", icon: <DeploymentUnitOutlined />, label: "内容治理" },
      { key: "tasks", icon: <CloudServerOutlined />, label: "任务中心" },
      { key: "automations", icon: <BranchesOutlined />, label: "自动化中心" },
    ],
  },
  {
    type: "group",
    label: "系统",
    children: [
      { key: "enterprise", icon: <TeamOutlined />, label: "组织与权限" },
      { key: "notifications", icon: <BellOutlined />, label: "通知中心" },
      { key: "config", icon: <SettingOutlined />, label: "系统设置" },
    ],
  },
];

/**
 * 主导航能一步到达的一等页面。从 MENU_ITEMS 派生，不是第二份要人记得维护的清单 ——
 * 可达性守卫靠它判断"有没有页面只剩 URL 能进"。
 */
type NavGroup = { children?: { key?: string | number }[] };
export const NAV_PAGE_KEYS: PageKey[] = ((MENU_ITEMS ?? []) as NavGroup[]).flatMap((group) =>
  (group?.children ?? []).map((item) => String(item?.key) as PageKey),
);
