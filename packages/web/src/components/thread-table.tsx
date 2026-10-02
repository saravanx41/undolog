import Link from "next/link";
import { listThreads } from "@/lib/db";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { TimeAgo } from "@/components/time-ago";
import { EmptyState } from "@/components/empty-state";

export async function ThreadTable() {
  let threads;
  try {
    threads = await listThreads();
  } catch (e) {
    return (
      <p className="rounded-md border border-red-500/50 bg-red-500/10 p-4 text-sm text-red-400">
        Failed to read ledger: {e instanceof Error ? e.message : String(e)}
      </p>
    );
  }

  if (threads.length === 0) {
    return <EmptyState />;
  }

  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
          <th className="py-2 pr-4 font-medium">thread</th>
          <th className="py-2 pr-4 font-medium">entries</th>
          <th className="py-2 pr-4 font-medium">last activity</th>
          <th className="py-2 font-medium">status</th>
        </tr>
      </thead>
      <tbody>
        {threads.map((t) => (
          <tr key={t.thread_id} className="border-b last:border-0 hover:bg-accent/40">
            <td className="py-2.5 pr-4">
              <Link
                href={`/threads/${encodeURIComponent(t.thread_id)}`}
                className="font-medium text-foreground underline-offset-4 hover:underline"
              >
                {t.thread_id}
              </Link>
            </td>
            <td className="py-2.5 pr-4">{t.entry_count}</td>
            <td className="py-2.5 pr-4 text-muted-foreground">
              <TimeAgo iso={new Date(t.last_ts).toISOString()} />
            </td>
            <td className="py-2.5">
              {t.frozen_at ? (
                <Badge variant="red">frozen</Badge>
              ) : (
                <Badge variant="green">active</Badge>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ThreadTableSkeleton() {
  return (
    <div className="space-y-2.5">
      <Skeleton className="h-8 w-full" />
      <Skeleton className="h-10 w-full" />
      <Skeleton className="h-10 w-full" />
      <Skeleton className="h-10 w-full" />
    </div>
  );
}
