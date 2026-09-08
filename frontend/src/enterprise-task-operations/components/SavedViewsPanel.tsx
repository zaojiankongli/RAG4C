import { Button, Empty, Tag } from "tdesign-react";
import { StarIcon, TimeIcon } from "tdesign-icons-react";

import type { TaskSavedViewsController } from "./taskOperationsTypes";

export interface SavedViewsPanelProps {
  controller: TaskSavedViewsController;
  readOnly?: boolean;
}

export default function SavedViewsPanel({ controller, readOnly = false }: SavedViewsPanelProps) {
  return (
    <section className="task-operations__side-panel" aria-labelledby="task-saved-views-title">
      <div className="task-operations__side-heading">
        <div>
          <span className="task-operations__eyebrow">OPERATOR SHORTCUTS</span>
          <h2 id="task-saved-views-title">Saved Views</h2>
        </div>
        <Tag theme="primary" variant="light-outline">
          <StarIcon aria-hidden="true" /> {controller.items.length}
        </Tag>
      </div>
      {controller.status === "unavailable" ? (
        <div role="alert">
          <Tag theme="warning">Saved Views 暂不可用</Tag>
        </div>
      ) : null}
      {controller.status === "partial" ? (
        <div role="alert">
          <Tag theme="warning">部分 Saved Views 无法读取</Tag>
        </div>
      ) : null}
      {controller.items.length ? (
        <div className="task-operations__saved-view-list">
          {controller.items.map((view) => (
            <div
              className={`task-operations__saved-view ${view.id === controller.activeId ? "is-active" : ""}`}
              key={view.id}
            >
              <Button
                variant="text"
                size="small"
                icon={<TimeIcon />}
                aria-label={`打开视图 ${view.label}`}
                onClick={() => controller.onSelect?.(view)}
              >
                {view.label}
              </Button>
              {view.pinned ? (
                <Tag theme="warning" variant="light-outline" size="small">
                  PINNED
                </Tag>
              ) : null}
            </div>
          ))}
        </div>
      ) : controller.status === "ready" || controller.status === "empty" ? (
        <Empty type="empty" title="暂无保存视图" description="把常用的运行筛选固定到这里。" />
      ) : null}
      <span className="task-operations__side-note">
        {readOnly ? "只读模式下不能保存或删除视图。" : "视图只控制展示筛选，不改变任务权威。"}
      </span>
    </section>
  );
}
