import { useState } from "react";
import { Button } from "../../ui";
import { CopyOutlined } from "../../ui/icons";
import { shortenReference } from "../model/sourceProjection";

export default function ReferenceText({ value, label = "引用" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const reference = shortenReference(value);
  const copy = async () => { await navigator.clipboard?.writeText(reference.full); setCopied(true); setTimeout(() => setCopied(false), 1200); };
  return <span className="source-reference"><code>{reference.display}</code><Button type="text" size="small" aria-label={`复制${label}`} onClick={() => void copy()} icon={<CopyOutlined />} />{copied ? <span role="status" className="source-copy-status">已复制</span> : null}</span>;
}
