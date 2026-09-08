import { Collapse } from "tdesign-react";

export default function SafeTracePanel({ traces }: { traces: string[] }) {
  if (!traces.length) return null;
  return <Collapse borderless defaultValue={[]}><Collapse.Panel value="trace" header={`安全执行 Trace（${traces.length}）`}><ol className="rq-traces">{traces.map((trace, index) => <li key={`${index}-${trace}`}>{trace}</li>)}</ol></Collapse.Panel></Collapse>;
}
