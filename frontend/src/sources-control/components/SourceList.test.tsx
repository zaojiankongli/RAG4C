// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SourceRecord } from "../model/sourceModels";
import SourceList from "./SourceList";

afterEach(cleanup);

const source: SourceRecord = { id: "source-1234567890abcdef", tenant_id: "tenant-a", dataset_id: "dataset-a", name: "Handbook", kind: "local_dir", config: { path: "C:/private/knowledge/handbook", extensions: [".md"], credential_ref: "secret://sources/private-handbook" }, metadata: {}, status: "active", generation: 4, last_cursor: {}, last_result: { fetched: 8, ingested: 7, failed: 1 }, last_error: "token=[REDACTED] upstream", last_sync_at: "2026-08-25T10:00:00Z", created_at: "2026-08-25T00:00:00Z", updated_at: "2026-08-25T00:00:00Z" };

describe("SourceList", () => {
  it("renders a safe generation-fenced source card without exact credential/path leakage", () => {
    render(<SourceList sources={[source]} selectedId={null} mutatingId={null} onSelect={vi.fn()} onEdit={vi.fn()} onToggle={vi.fn()} onSync={vi.fn()} />);
    expect(screen.getByText("G4")).toBeTruthy();
    expect(screen.getByText("secret:// 引用")).toBeTruthy();
    expect(document.body.textContent).not.toContain("private/knowledge");
    expect(document.body.textContent).not.toContain("sources/private-handbook");
    expect(screen.getByText("8 抓取")).toBeTruthy();
  });

  it("requires confirmation before disabling", () => {
    const onToggle = vi.fn();
    render(<SourceList sources={[source]} selectedId={null} mutatingId={null} onSelect={vi.fn()} onEdit={vi.fn()} onToggle={onToggle} onSync={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "停用 Handbook" }));
    expect(onToggle).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认停用" }));
    expect(onToggle).toHaveBeenCalledWith(source, false);
  });
});
