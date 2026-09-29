import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { MENU_ITEMS } from "../run/appNav";
import { mainNavigationKey, type PageKey } from "../run/appRoute";
import "./workspace-navigation.css";

export interface WorkspaceNavigationProps {
  page: PageKey;
  collapsed: boolean;
  onNavigate: (key: PageKey) => void;
}

interface NavigationGroup {
  label: string;
  children: { key: PageKey; label: string; icon: ReactNode }[];
}

// appNav's compatibility MenuProps uses any[]. Narrow its shape here, not its routes.
const groups = (MENU_ITEMS ?? []) as NavigationGroup[];

export function WorkspaceNavigation({ page, collapsed, onNavigate }: WorkspaceNavigationProps) {
  const id = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const activeKey = mainNavigationKey(page);
  const routeGroup =
    groups.find((group) => group.children.some((item) => item.key === activeKey))?.label ??
    groups[0]?.label;
  const [selectedGroup, setSelectedGroup] = useState(routeGroup);

  // Browsing a workspace is not navigation. Only an actual route change resets it,
  // including changes between routes belonging to the same workspace or parent entry.
  useEffect(() => {
    setSelectedGroup(routeGroup);
  }, [page, routeGroup]);

  // An invisible search must never hide destinations in the icon-only sidebar.
  const terms = collapsed ? [] : query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const searching = terms.length > 0;
  const visibleGroups = groups
    .filter((group) => collapsed || searching || group.label === selectedGroup)
    .map((group) => ({
      ...group,
      children: group.children.filter((item) => {
        const text = `${group.label} ${item.label} ${item.key}`.toLocaleLowerCase();
        return terms.every((term) => text.includes(term));
      }),
    }))
    .filter((group) => group.children.length > 0);
  const resultCount = visibleGroups.reduce((count, group) => count + group.children.length, 0);

  function clearSearch() {
    setQuery("");
    inputRef.current?.focus();
  }

  return (
    <nav
      aria-label="工作区功能导航"
      className={`workspace-navigation${collapsed ? " workspace-navigation--collapsed" : ""}`}
    >
      {!collapsed && (
        <>
          <div
            className="workspace-navigation__workspaces"
            role="group"
            aria-labelledby={`${id}-title`}
          >
            <h2 id={`${id}-title`} className="workspace-navigation__title">
              功能分区
            </h2>
            <div className="workspace-navigation__sections">
              {groups.map((group, index) => (
                <button
                  key={group.label}
                  type="button"
                  className="workspace-navigation__section"
                  aria-label={group.label}
                  aria-pressed={selectedGroup === group.label}
                  aria-controls={`${id}-features`}
                  aria-describedby={`${id}-count-${index}`}
                  title={group.label}
                  onClick={() => setSelectedGroup(group.label)}
                >
                  <span className="workspace-navigation__icon" aria-hidden="true">
                    {group.children[0]?.icon}
                  </span>
                  <span id={`${id}-count-${index}`} className="workspace-navigation__count">
                    <span className="workspace-navigation__sr-only">共 </span>
                    {group.children.length}
                    <span className="workspace-navigation__sr-only"> 个功能</span>
                  </span>
                  <span className="workspace-navigation__section-label">{group.label}</span>
                </button>
              ))}
            </div>
          </div>
          <div className="workspace-navigation__search">
            <label className="workspace-navigation__sr-only" htmlFor={`${id}-search`}>
              功能搜索
            </label>
            <div className="workspace-navigation__search-row">
              <input
                ref={inputRef}
                id={`${id}-search`}
                type="search"
                placeholder="搜索全部功能"
                aria-describedby={`${id}-search-hint`}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
              {query && (
                <button type="button" className="workspace-navigation__clear" onClick={clearSearch}>
                  清除
                </button>
              )}
            </div>
            <p id={`${id}-search-hint`} className="workspace-navigation__sr-only">
              搜索所有分区的功能入口，不搜索知识内容
            </p>
            {searching && (
              <p role="status" className="workspace-navigation__hint">
                {resultCount ? `找到 ${resultCount} 个功能` : "未找到匹配功能，请清除或更换关键词"}
              </p>
            )}
          </div>
        </>
      )}
      <div id={`${id}-features`} className="workspace-navigation__groups">
        {visibleGroups.map((group) => (
          <div className="workspace-navigation__group" key={group.label}>
            {!collapsed && (
              <h3 className="workspace-navigation__heading">
                <span>{group.label}</span>
                <span className="workspace-navigation__count" aria-hidden="true">
                  {group.children.length}
                </span>
              </h3>
            )}
            <ul aria-label={group.label} className="workspace-navigation__list">
              {group.children.map((item) => (
                <li key={item.key}>
                  <button
                    type="button"
                    className="workspace-navigation__item"
                    aria-label={item.label}
                    title={item.label}
                    aria-current={item.key === activeKey ? "page" : undefined}
                    onClick={() => onNavigate(item.key)}
                  >
                    <span className="workspace-navigation__icon" aria-hidden="true">
                      {item.icon}
                    </span>
                    {!collapsed && (
                      <span className="workspace-navigation__label">{item.label}</span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </nav>
  );
}

export default WorkspaceNavigation;
