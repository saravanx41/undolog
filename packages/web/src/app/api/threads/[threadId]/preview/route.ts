import { NextResponse } from "next/server";
import { previewRollback } from "@/lib/python";

export const dynamic = "force-dynamic";

type Params = { params: Promise<{ threadId: string }> };

export async function GET(request: Request, { params }: Params) {
  const { threadId } = await params;
  const seq = Number(new URL(request.url).searchParams.get("seq"));
  if (!Number.isInteger(seq) || seq < 0) {
    return NextResponse.json({ error: "seq must be a non-negative integer" }, { status: 400 });
  }
  try {
    const report = await previewRollback(threadId, seq);
    return NextResponse.json(report);
  } catch (e) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : String(e) },
      { status: 500 },
    );
  }
}
