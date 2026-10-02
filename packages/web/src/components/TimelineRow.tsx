"use client";

import { useMemo, useState } from "react";
import { diffJson } from "@/lib/diff";

export interface TimelineEntry {
  id: string;
  ts: string;
  seq: number;
  tool_name: string;
  status: string;
  entry_class: string;
  before_jsonb: unknown;
  after_jsonb: unknown;
  args_hash: string;
  idempotency_key: string;
  compensation_ref: string | null;
  prompt_context_ref: string | null;
  log_only: boolean;
}

export function TimelineRow({ entry }: { entry: TimelineEntry }) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"diff" | "why">("diff");

  const diff = useMemo(
    () => (open && tab === "diff" ? diffJson(entry.before_jsonb, entry.after_jsonb) : null),
    [open, tab, entry.before_jsonb, entry.after_jsonb],
  );

  return (
    <>
      <tr className="tl-row" onClick={() => setOpen((v) => !v)}>
        <td>{open ? "▾" : "▸"}</td>
        <td>{entry.seq}</td>
        <td>{entry.tool_name}</td>
        <td>{new Date(entry.ts).toISOString()}</td>
        <td>
          <span className={`badge badge-${entry.status}`}>{entry.status}</span>
        </td>
        <td className={`cls-${entry.entry_class}`}>{entry.entry_class}</td>
      </tr>
      {open && (
        <tr>
          <td colSpan={6}>
            <div className="tab-bar">
              <button onClick={(e) => { e.stopPropagation(); setTab("diff"); }}>
                before / after diff
              </button>{" "}
              <button onClick={(e) => { e.stopPropagation(); setTab("why"); }}>
                why
              </button>
            </div>
            {tab === "diff" &&
              (diff && diff.length > 0 ? (
                <div className="diff">
                  {diff.map((line, i) => (
                    <div key={i} className={line.op}>
                      <span className="diff-sign">
                        {line.op === "added" ? "+" : line.op === "removed" ? "−" : " "}
                      </span>
                      {line.text}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="muted">no before/after proof captured (log-only row)</p>
              ))}
            {tab === "why" && (
              <div className="why">
                <dl>
                  <dt>entry id</dt>
                  <dd>{entry.id}</dd>
                  <dt>idempotency key</dt>
                  <dd>{entry.idempotency_key}</dd>
                  <dt>args hash (sha256)</dt>
                  <dd>{entry.args_hash}</dd>
                  <dt>compensation ref</dt>
                  <dd>{entry.compensation_ref ?? "—"}</dd>
                  <dt>prompt context ref</dt>
                  <dd>{entry.prompt_context_ref ?? "—"}</dd>
                  <dt>log only</dt>
                  <dd>{entry.log_only ? "true (proof capture failed, class escalated)" : "false"}</dd>
                </dl>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}
