import Link from "next/link";
import { listEntries, threadFrozen } from "@/lib/db";
import { RollbackPanel } from "@/components/RollbackPanel";
import { TimelineRow, type TimelineEntry } from "@/components/TimelineRow";

export const dynamic = "force-dynamic";

type Params = { params: Promise<{ threadId: string }> };

export default async function ThreadDetailPage({ params }: Params) {
  const { threadId } = await params;
  let entries: Awaited<ReturnType<typeof listEntries>> = [];
  let frozenAt: string | null = null;
  let error: string | null = null;
  try {
    [entries, frozenAt] = await Promise.all([
      listEntries(threadId),
      threadFrozen(threadId),
    ]);
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
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
    <>
      <p>
        <Link href="/">← threads</Link>
      </p>
      <h1>Thread: {threadId}</h1>
      <p className="muted">
        {timeline.length} ledger {timeline.length === 1 ? "entry" : "entries"}
      </p>
      {error && <p className="error">Failed to read ledger: {error}</p>}

      {frozenAt && (
        <div className="frozen-banner">
          Thread is FROZEN since {new Date(frozenAt).toISOString()} — side effects
          are refused; a rollback may be in progress.
        </div>
      )}

      {timeline.length > 0 && (
        <RollbackPanel threadId={threadId} minSeq={Math.max(0, minSeq)} maxSeq={maxSeq} />
      )}

      <h2>Timeline</h2>
      <table>
        <thead>
          <tr>
            <th></th>
            <th>seq</th>
            <th>tool</th>
            <th>ts</th>
            <th>status</th>
            <th>class</th>
          </tr>
        </thead>
        <tbody>
          {timeline.map((entry) => (
            <TimelineRow key={entry.id} entry={entry} />
          ))}
        </tbody>
      </table>
    </>
  );
}
