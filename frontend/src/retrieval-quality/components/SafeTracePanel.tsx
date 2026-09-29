import { Collapse } from "../../ui";

export default function SafeTracePanel({ traces }: { traces: string[] }) {
  if (!traces.length) return null;
  return (
    <Collapse
      borderless
      defaultValue={[]}
      items={[
        {
          key: "trace",
          label: `安全执行 Trace（${traces.length}）`,
          children: (
            <ol className="rq-traces">
              {traces.map((trace, index) => (
                <li key={`${index}-${trace}`}>{trace}</li>
              ))}
            </ol>
          ),
        },
      ]}
    />
  );
}
