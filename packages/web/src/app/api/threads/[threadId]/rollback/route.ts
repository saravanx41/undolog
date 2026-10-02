import { NextResponse } from "next/server";
import { executeRollback } from "@/lib/python";

export const dynamic = "force-dynamic";

type Params = { params: Promise<{ threadId: string }> };

export async function POST(request: Request, { params }: Params) {
  const { threadId } = await params;
  let seq: unknown;
  try {
    seq = (await request.json())?.seq;
  } catch {
    return NextResponse.json({ error: "JSON body required" }, { status: 400 });
  }
  if (!Number.isInteger(seq) || (seq as number) < 0) {
    return NextResponse.json({ error: "seq must be a non-negative integer" }, { status: 400 });
  }
  try {
    const report = await executeRollback(threadId, seq as number);
    return NextResponse.json(report);
  } catch (e) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : String(e) },
      { status: 500 },
    );
  }
}
