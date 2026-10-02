"use client";

import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { diffJson } from "@/lib/diff";
import { Badge } from "@/components/ui/badge";
import { JsonDiff } from "@/components/json-diff";
import { TimeAgo } from "@/components/time-ago";
import { ToolIcon } from "@/components/tool-icon";
import { cn } from "@/lib/utils";

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

const STATUS_VARIANT: Record<string, "green" | "blue" | "red" | "muted"> = {
  applied: "green",
  compensated: "blue",
  failed: "red",
};

const CLASS_COLOR: Record<string, string> = {
  reversible: "text-emerald-400",
  compensatable: "text-amber-400",
  irreversible: "text-red-400",
  unknown: "text-muted-foreground",
};

export function TimelineRow({ entry }: { entry: TimelineEntry }) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"diff" | "why">("diff");

  const diff = useMemo(
    () => (open && tab === "diff" ? diffJson(entry.before_jsonb, entry.after_jsonb) : null),
    [open, tab, entry.before_jsonb, entry.after_jsonb],
  );

  return (
    <>
      <tr
        className="cursor-pointer border-b last:border-0 hover:bg-accent/40"
        onClick={() => setOpen((v) => !v)}
      >
        <td className="w-8 py-2.5 pr-2 text-muted-foreground">
          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </td>
        <td className="py-2.5 pr-4 font-mono text-xs text-muted-foreground">{entry.seq}</td>
        <td className="py-2.5 pr-4">
          <span className="inline-flex items-center gap-2">
            <ToolIcon tool={entry.tool_name} className="h-4 w-4 text-muted-foreground" />
            <span className="font-mono text-[13px]">{entry.tool_name}</span>
          </span>
        </td>
        <td className="py-2.5 pr-4 text-muted-foreground">
          <TimeAgo iso={entry.ts} />
        </td>
        <td className="py-2.5 pr-4">
          <Badge variant={STATUS_VARIANT[entry.status] ?? "muted"}>{entry.status}</Badge>
        </td>
        <td className={cn("py-2.5 text-xs font-medium uppercase tracking-wide", CLASS_COLOR[entry.entry_class])}>
          {entry.entry_class}
        </td>
      </tr>
      {open && (
        <tr className="border-b bg-card/50 last:border-0">
          <td colSpan={6} className="px-4 py-3">
            <div className="mb-2 flex gap-1">
              <button
                onClick={(e) => { e.stopPropagation(); setTab("diff"); }}
                className={cn(
                  "rounded-md px-3 py-1 text-xs font-medium",
                  tab === "diff" ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground",
                )}
              >
                before / after diff
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); setTab("why"); }}
                className={cn(
                  "rounded-md px-3 py-1 text-xs font-medium",
                  tab === "why" ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground",
                )}
              >
                why
              </button>
            </div>
            {tab === "diff" &&
              (diff && diff.length > 0 ? (
                <JsonDiff lines={diff} />
              ) : (
                <p className="py-2 text-sm text-muted-foreground">
                  no before/after proof captured (log-only row)
                </p>
              ))}
            {tab === "why" && (
              <dl className="grid grid-cols-[180px_1fr] gap-x-4 gap-y-1.5 py-1 text-sm">
                <dt className="text-xs text-muted-foreground">entry id</dt>
                <dd className="break-all font-mono text-xs">{entry.id}</dd>
                <dt className="text-xs text-muted-foreground">idempotency key</dt>
                <dd className="break-all font-mono text-xs">{entry.idempotency_key}</dd>
                <dt className="text-xs text-muted-foreground">args hash (sha256)</dt>
                <dd className="break-all font-mono text-xs">{entry.args_hash}</dd>
                <dt className="text-xs text-muted-foreground">compensation ref</dt>
                <dd className="break-all font-mono text-xs">{entry.compensation_ref ?? "—"}</dd>
                <dt className="text-xs text-muted-foreground">prompt context ref</dt>
                <dd className="break-all font-mono text-xs">{entry.prompt_context_ref ?? "—"}</dd>
                <dt className="text-xs text-muted-foreground">log only</dt>
                <dd className="font-mono text-xs">
                  {entry.log_only ? "true (proof capture failed, class escalated)" : "false"}
                </dd>
              </dl>
            )}
          </td>
        </tr>
      )}
    </>
  );
}
