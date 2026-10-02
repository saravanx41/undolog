import { listEntries, threadFrozen } from "@/lib/db";
import { RollbackPanel } from "@/components/RollbackPanel";
import { TimelineRow, type TimelineEntry } from "@/components/TimelineRow";
import { Skeleton } from "@/components/ui/skeleton";

export async function TimelineView({ threadId }: { threadId: string }) {
  let entries;
  let frozenAt: string | null = null;
  try {
    [entries, frozenAt] = await Promise.all([
      listEntries(threadId),
      threadFrozen(threadId),
    ]);
  } catch (e) {
    return (
      <p className="rounded-md border border-red-500/50 bg-red-500/10 p-4 text-sm text-red-400">
        Failed to read ledger: {e instanceof Error ? e.message : String(e)}
      </p>
    );
  }

  const timeline: TimelineEntry[] = entries.map((e) => ({
    id: e.id,
    ts: typeof e.ts === "string" ? e.ts : new Date(e.ts).toISOString(),
    seq: e.seq,
    tool_name: e.tool_name,
    status: e.status,
    entry_class: e.class,
    before_jsonb: e.before_jsonb,
    after_jsonb: e.after_jsonb,
    args_hash: e.args_hash,
    idempotency_key: e.idempotency_key,
    compensation_ref: e.compensation_ref,
    prompt_context_ref: e.prompt_context_ref,
    log_only: e.log_only,
  }));

  const seqs = timeline.map((t) => t.seq);
  const minSeq = seqs.length ? Math.min(...seqs) - 1 : 0;
  const maxSeq = seqs.length ? Math.max(...seqs) : 0;

  return (
    <div className="space-y-6">
      {frozenAt && (
        <div className="rounded-md border border-sky-500/50 bg-sky-500/10 p-4 text-sm text-sky-400">
          Thread is FROZEN since {new Date(frozenAt).toISOString()} — side effects
          are refused; a rollback may be in progress.
        </div>
      )}

      {timeline.length > 0 && (
        <RollbackPanel threadId={threadId} minSeq={Math.max(0, minSeq)} maxSeq={maxSeq} />
      )}

      <div>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Timeline
        </h2>
        {timeline.length === 0 ? (
          <p className="text-sm text-muted-foreground">No entries for this thread.</p>
        ) : (
          <table className="w-full text-sm">
            <tbody>
              {timeline.map((entry) => (
                <TimelineRow key={entry.id} entry={entry} />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

export function TimelineSkeleton() {
  return (
    <div className="space-y-3">
      <Skeleton className="h-36 w-full" />
      <Skeleton className="h-9 w-full" />
      <Skeleton className="h-9 w-full" />
      <Skeleton className="h-9 w-full" />
      <Skeleton className="h-9 w-full" />
      <Skeleton className="h-9 w-full" />
    </div>
  );
}
