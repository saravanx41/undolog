"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";

export function EmptyState() {
  const router = useRouter();
  const { toast } = useToast();
  const [running, setRunning] = useState(false);

  async function runDemo() {
    setRunning(true);
    try {
      const res = await fetch("/api/demo", { method: "POST" });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.error ?? `request failed (${res.status})`);
      router.push(`/threads/${encodeURIComponent(body.thread_id)}`);
      router.refresh();
    } catch (e) {
      toast({
        title: "Could not run the demo",
        description:
          e instanceof Error ? e.message : String(e),
        variant: "destructive",
      });
    } finally {
      setRunning(false);
    }
  }

  return (
    <div
      className="flex flex-col items-center rounded-lg border border-dashed px-6 py-16 text-center"
      data-testid="welcome-empty-state"
    >
      <h2 className="text-lg font-semibold">Welcome to undolog</h2>
      <p className="mt-2 max-w-md text-sm text-muted-foreground">
        The ledger is empty. Run the PocketOS demo — a rogue-agent scenario —
        to seed a thread with 18 tool calls, then explore the timeline and
        roll it back.
      </p>
      <Button className="mt-6" size="lg" onClick={runDemo} disabled={running}>
        <Play className="h-4 w-4" />
        {running ? "Running demo…" : "Run the demo"}
      </Button>
    </div>
  );
}
