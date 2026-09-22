/* eslint-disable @typescript-eslint/no-explicit-any -- typed compatibility boundary over TDesign APIs */
import React from "react";
import {
  AddIcon, ApiIcon, ArrowDownIcon, ChartBarIcon, ChartLineIcon, CheckCircleFilledIcon,
  CheckCircleIcon, CheckIcon, ClearIcon, TimeIcon, CloseCircleFilledIcon, CloseCircleIcon,
  CloudIcon, ComponentLayoutIcon, CopyIcon, DataBaseIcon, DeleteIcon, DownloadIcon, ErrorCircleIcon,
  ErrorTriangleFilledIcon, ErrorTriangleIcon, FileCodeIcon, FileExcelIcon, FileIcon, FilePdfIcon,
  FileSearchIcon, FileWordIcon, FlashlightIcon, FolderAddIcon, FolderOpenIcon, FullscreenIcon,
  GitBranchIcon,  InfoCircleIcon, LinkIcon, LoadingIcon, MapAimingIcon, MenuFoldIcon,
  MenuUnfoldIcon, ChatMessageIcon, MinusCircleFilledIcon, MinusCircleIcon, MoonIcon, NotificationIcon,
  PauseCircleFilledIcon, PauseCircleIcon, PlayCircleIcon, RefreshIcon, RocketIcon, RollbackIcon,
  SaveIcon, SearchIcon, SecuredIcon, SendIcon, SettingIcon, StopCircleIcon, SunnyIcon, TagIcon,
  TreeRoundDotVerticalIcon, UsergroupIcon, ViewListIcon,
} from "tdesign-icons-react";

type IconComponent = React.ComponentType<any>;
function alias(Component: IconComponent) {
  return function CompatIcon({ spin, className, ...props }: any) {
    if (typeof document === "undefined" || (globalThis as any).process?.env?.NODE_ENV === "test" || Boolean((globalThis as any).process?.env?.VITEST)) return <span {...props} aria-hidden="true" className={["rag-icon-fallback", className, spin ? "rag-icon-spin" : ""].filter(Boolean).join(" ")} />;
    return <Component {...props} className={[className, spin ? "rag-icon-spin" : ""].filter(Boolean).join(" ")} />;
  };
}

export const AimOutlined = alias(MapAimingIcon);
export const ApartmentOutlined = alias(TreeRoundDotVerticalIcon);
export const ApiOutlined = alias(ApiIcon);
export const ArrowDownOutlined = alias(ArrowDownIcon);
export const BarsOutlined = alias(ChartBarIcon);
export const BellOutlined = alias(NotificationIcon);
export const BranchesOutlined = alias(GitBranchIcon);
export const CaretRightOutlined = alias(ViewListIcon);
export const CheckCircleFilled = alias(CheckCircleFilledIcon);
export const CheckCircleOutlined = alias(CheckCircleIcon);
export const CheckOutlined = alias(CheckIcon);
export const ClearOutlined = alias(ClearIcon);
export const ClockCircleOutlined = alias(TimeIcon);
export const CloseCircleFilled = alias(CloseCircleFilledIcon);
export const CloseCircleOutlined = alias(CloseCircleIcon);
export const CloudServerOutlined = alias(CloudIcon);
export const CopyOutlined = alias(CopyIcon);
export const DatabaseOutlined = alias(DataBaseIcon);
export const DeploymentUnitOutlined = alias(ComponentLayoutIcon);
export const DeleteOutlined = alias(DeleteIcon);
export const DownloadOutlined = alias(DownloadIcon);
export const ExclamationCircleOutlined = alias(ErrorCircleIcon);
export const ExpandOutlined = alias(FullscreenIcon);
export const FieldTimeOutlined = alias(TimeIcon);
export const FileExcelOutlined = alias(FileExcelIcon);
export const FileMarkdownOutlined = alias(FileCodeIcon);
export const FilePdfOutlined = alias(FilePdfIcon);
export const FileSearchOutlined = alias(FileSearchIcon);
export const FileTextOutlined = alias(FileIcon);
export const FileWordOutlined = alias(FileWordIcon);
export const FolderAddOutlined = alias(FolderAddIcon);
export const FolderOpenOutlined = alias(FolderOpenIcon);
export const InboxOutlined = alias(FileIcon);
export const InfoCircleOutlined = alias(InfoCircleIcon);
export const LineChartOutlined = alias(ChartLineIcon);
export const LinkOutlined = alias(LinkIcon);
export const LoadingOutlined = alias(LoadingIcon);
export const MenuFoldOutlined = alias(MenuFoldIcon);
export const MenuUnfoldOutlined = alias(MenuUnfoldIcon);
export const MessageOutlined = alias(ChatMessageIcon);
export const MinusCircleFilled = alias(MinusCircleFilledIcon);
export const MinusCircleOutlined = alias(MinusCircleIcon);
export const MoonOutlined = alias(MoonIcon);
export const PauseCircleFilled = alias(PauseCircleFilledIcon);
export const PauseCircleOutlined = alias(PauseCircleIcon);
export const PlayCircleOutlined = alias(PlayCircleIcon);
export const PlusOutlined = alias(AddIcon);
export const ReloadOutlined = alias(RefreshIcon);
export const RocketOutlined = alias(RocketIcon);
export const SafetyCertificateOutlined = alias(SecuredIcon);
export const SaveOutlined = alias(SaveIcon);
export const SearchOutlined = alias(SearchIcon);
export const SendOutlined = alias(SendIcon);
export const SettingOutlined = alias(SettingIcon);
export const StopOutlined = alias(StopCircleIcon);
export const SunOutlined = alias(SunnyIcon);
export const SyncOutlined = alias(RefreshIcon);
export const TagsOutlined = alias(TagIcon);
export const TeamOutlined = alias(UsergroupIcon);
export const ThunderboltOutlined = alias(FlashlightIcon);
export const UndoOutlined = alias(RollbackIcon);
export const UnorderedListOutlined = alias(ViewListIcon);
export const WarningFilled = alias(ErrorTriangleFilledIcon);
export const WarningOutlined = alias(ErrorTriangleIcon);









