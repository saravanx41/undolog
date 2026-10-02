import Link from "next/link";
import { Suspense } from "react";
import { TimelineSkeleton, TimelineView } from "@/components/timeline-view";

export const dynamic = "force-dynamic";

type Params = { params: Promise<{ threadId: string }> };

export default async function ThreadDetailPage({ params }: Params) {
  const { threadId } = await params;

  return (
    <div className="space-y-6">
      <div>
        <nav className="text-sm text-muted-foreground" aria-label="Breadcrumb">
          <Link href="/threads" className="hover:text-foreground hover:underline">
            Threads
          </Link>
          <span className="mx-1.5 text-border">/</span>
          <span className="font-mono text-foreground">{threadId}</span>
        </nav>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">{threadId}</h1>
      </div>
      <Suspense fallback={<TimelineSkeleton />}>
        <TimelineView threadId={threadId} />
      </Suspense>
    </div>
  );
}
