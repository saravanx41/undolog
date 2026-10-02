import { NextResponse } from "next/server";
import { listEntries } from "@/lib/db";

export const dynamic = "force-dynamic";

type Params = { params: Promise<{ threadId: string }> };

export async function GET(_request: Request, { params }: Params) {
  const { threadId } = await params;
  try {
    const entries = await listEntries(threadId);
    return NextResponse.json({ thread_id: threadId, entries });
  } catch (e) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : String(e) },
      { status: 500 },
    );
  }
}
