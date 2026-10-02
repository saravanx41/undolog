import { Suspense } from "react";
import { ThreadTable, ThreadTableSkeleton } from "@/components/thread-table";

export const dynamic = "force-dynamic";

export default function ThreadsPage() {
  return (
    <div className="space-y-6">
      <div>
        <nav className="text-sm text-muted-foreground" aria-label="Breadcrumb">
          Threads
        </nav>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">Threads</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          One ledger thread per agent run. Open a thread to inspect the timeline
          and roll back to any checkpoint.
        </p>
      </div>
      <Suspense fallback={<ThreadTableSkeleton />}>
        <ThreadTable />
      </Suspense>
    </div>
  );
}
