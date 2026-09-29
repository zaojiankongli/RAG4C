import { useState, type RefObject } from "react";
import { Button, Card, Input, Select, Tag } from "../../ui";
import type { Experiment, ExperimentStatus, HistoryFilters } from "../model/contracts";
import { projectExperiment } from "../model/projection";

interface Props {
  items: Experiment[];
  status: string;
  error: Error | null;
  paging: boolean;
  hasPrevious: boolean;
  hasNext: boolean;
  onFilters: (filters: HistoryFilters) => void;
  onRefresh: () => void;
  onNext: () => void;
  onPrevious: () => void;
  onSelect: (item: Experiment, opener: RefObject<HTMLButtonElement>) => void;
}

function when(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "—" : date.toLocaleString("zh-CN");
}

function inputValue(event: { target?: { value?: unknown } }) {
  return String(event?.target?.value ?? "");
}

export default function ExperimentHistory(props: Props) {
  const [status, setStatus] = useState<ExperimentStatus | "">("");
  const [runId, setRunId] = useState("");
  const [query, setQuery] = useState("");

  return (
    <section className="rq-history" aria-labelledby="rq-history-title">
      <Card bordered>
        <div className="rq-section-heading">
          <div>
            <span className="rq-eyebrow">IMMUTABLE EXPERIMENT LEDGER</span>
            <h2 id="rq-history-title">实验历史</h2>
            <p>后端 keyset 分页，每页十条。</p>
          </div>
          <Button loading={props.paging} onClick={props.onRefresh}>
            刷新
          </Button>
        </div>
        {props.error ? (
          <div role="alert" className="rq-inline-error">
            {props.error.message}
          </div>
        ) : null}
        <div className="rq-history-filters">
          <label>
            <span>实验状态</span>
            <Select
              aria-label="实验状态"
              value={status}
              options={[
                { label: "全部", value: "" },
                { label: "完成", value: "completed" },
                { label: "失败", value: "failed" },
              ]}
              onChange={(value: unknown) => setStatus(value as ExperimentStatus | "")}
            />
          </label>
          <label>
            <span>运行 ID</span>
            <Input
              aria-label="运行 ID"
              value={runId}
              onChange={(event: { target?: { value?: unknown } }) => setRunId(inputValue(event))}
            />
          </label>
          <label>
            <span>精确查询</span>
            <Input
              aria-label="精确查询"
              value={query}
              onChange={(event: { target?: { value?: unknown } }) => setQuery(inputValue(event))}
            />
          </label>
          <Button
            type="primary"
            onClick={() =>
              props.onFilters({ status: status || undefined, runId: runId.trim() || undefined, query })
            }
          >
            应用筛选
          </Button>
        </div>
        <div className="rq-table-scroll" role="region" tabIndex={0} aria-label="检索实验历史，可横向滚动">
          <table className="rq-history-table" aria-label="检索实验历史，每页十条">
            <caption>检索实验历史，每页十条</caption>
            <thead>
              <tr>
                <th>实验</th>
                <th>查询</th>
                <th>状态</th>
                <th>策略</th>
                <th>耗时</th>
                <th>服务代次</th>
                <th>创建者</th>
                <th>时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {props.status === "loading" ? (
                <tr>
                  <td colSpan={9}>正在加载实验历史…</td>
                </tr>
              ) : props.items.length ? (
                props.items.map((item) => {
                  const view = projectExperiment(item);
                  const opener = { current: null } as RefObject<HTMLButtonElement>;
                  return (
                    <tr key={item.id}>
                      <td>
                        <code>{item.id}</code>
                        <small>{item.run_id ?? "无运行 ID"}</small>
                      </td>
                      <td>
                        <span className="rq-query-cell">{item.query}</span>
                      </td>
                      <td>
                        <Tag theme={item.status === "failed" ? "danger" : "success"}>
                          {item.status === "failed" ? "失败" : "完成"}
                        </Tag>
                        {view.state === "no-hit" ? <small>无命中</small> : null}
                      </td>
                      <td>
                        {view.name}
                        <small>{view.strategy.routeTarget ?? "—"}</small>
                      </td>
                      <td>{item.latency_ms} ms</td>
                      <td>G{view.datasetServingGeneration ?? "—"}</td>
                      <td>{item.created_by}</td>
                      <td>{when(item.created_at)}</td>
                      <td>
                        <Button
                          ref={(node: HTMLButtonElement | null) => {
                            (opener as { current: HTMLButtonElement | null }).current = node;
                          }}
                          type="text"
                          aria-label={`查看实验 ${item.id}`}
                          onClick={() => props.onSelect(item, opener)}
                        >
                          查看
                        </Button>
                      </td>
                    </tr>
                  );
                })
              ) : (
                <tr>
                  <td colSpan={9}>当前筛选没有实验记录。</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="rq-pagination">
          <Button disabled={!props.hasPrevious || props.paging} onClick={props.onPrevious}>
            上一页
          </Button>
          <span>每页 10 条</span>
          <Button disabled={!props.hasNext || props.paging} onClick={props.onNext}>
            下一页
          </Button>
        </div>
      </Card>
    </section>
  );
}
