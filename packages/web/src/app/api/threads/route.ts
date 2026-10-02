import { NextResponse } from "next/server";
import { listThreads } from "@/lib/db";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const threads = await listThreads();
    return NextResponse.json({
      threads: threads.map((t) => ({
        thread_id: t.thread_id,
        entry_count: Number(t.entry_count),
        last_ts: t.last_ts,
        frozen: t.frozen_at !== null,
        frozen_at: t.frozen_at,
      })),
    });
  } catch (e) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : String(e) },
      { status: 500 },
    );
  }
}
