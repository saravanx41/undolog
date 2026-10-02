import { revalidatePath } from "next/cache";
import { NextResponse } from "next/server";
import { runDemo } from "@/lib/python";

export const dynamic = "force-dynamic";

function friendlyError(raw: string): string {
  if (/connection refused|could not connect|no route to host|timeout/i.test(raw)) {
    return "Could not reach Postgres — is `docker compose up` running?";
  }
  return raw.length > 400 ? `${raw.slice(0, 400)}…` : raw;
}

export async function POST() {
  try {
    const result = await runDemo();
    revalidatePath("/threads");
    revalidatePath(`/threads/${encodeURIComponent(result.thread_id)}`);
    return NextResponse.json(result);
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: friendlyError(message) }, { status: 500 });
  }
}
