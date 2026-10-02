"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useToast } from "@/components/ui/toast";
import type { RollbackItem, RollbackReport } from "@/lib/python";

function ItemList({ title, items, empty }: { title: string; items: RollbackItem[]; empty: string }) {
  return (
    <div className="mt-3">
      <p className="text-sm font-medium">
        {title} <Badge variant="muted" className="ml-1">{items.length}</Badge>
      </p>
      {items.length === 0 ? (
        <p className="mt-1 text-sm text-muted-foreground">{empty}</p>
      ) : (
        <ul className="mt-1 space-y-1">
          {items.map((item) => (
            <li key={`${item.entry_id ?? item.seq}-${item.action}`} className="text-sm text-muted-foreground">
              <span className="font-mono text-xs">seq {item.seq}</span> · {item.tool_name} · {item.entry_class}
              {item.reason ? ` — ${item.reason}` : ""}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function RollbackPanel({
  threadId,
  minSeq,
  maxSeq,
}: {
  threadId: string;
  minSeq: number;
  maxSeq: number;
}) {
  const router = useRouter();
  const { toast } = useToast();
  const [target, setTarget] = useState(maxSeq);
  const [preview, setPreview] = useState<RollbackReport | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<RollbackReport | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = setTimeout(() => {
      setLoading(true);
      setPreviewError(null);
      fetch(`/api/threads/${encodeURIComponent(threadId)}/preview?seq=${target}`)
        .then(async (res) => {
          const body = await res.json();
          if (!res.ok) throw new Error(body.error ?? res.statusText);
          setPreview(body as RollbackReport);
        })
        .catch((e: Error) => setPreviewError(e.message))
        .finally(() => setLoading(false));
    }, 250);
    return () => {
      if (debounce.current) clearTimeout(debounce.current);
    };
  }, [target, threadId, result]);

  async function confirm() {
    setRunning(true);
    setRunError(null);
    setResult(null);
    try {
      const res = await fetch(`/api/threads/${encodeURIComponent(threadId)}/rollback`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ seq: target }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.error ?? res.statusText);
      const report = body as RollbackReport;
      setResult(report);
      router.refresh();
      if (report.complete) {
        toast({
          title: "Rollback complete",
          description: `${report.summary.restored} restored, ${report.summary.compensated} refunded${report.summary.irreversible > 0 ? `, ${report.summary.irreversible} irreversible in blast radius` : ""}`,
        });
      } else {
        toast({
          title: "Rollback halted (partial)",
          description: `${report.summary.failed} operation(s) failed — see the report below.`,
          variant: "destructive",
        });
      }
    } catch (e) {
      setRunError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }

  return (
    <section className="rounded-lg border bg-card p-5">
      <h2 className="text-base font-semibold">Rollback — drag to checkpoint</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        Undo every <em>applied</em> action with seq &gt; {target}. Preview below is a
        dry run produced by the real rollback engine.
      </p>
      <input
        type="range"
        min={minSeq}
        max={maxSeq}
        value={target}
        disabled={running}
        onChange={(e) => setTarget(Number(e.target.value))}
        className="mt-4"
      />
      <div className="flex justify-between text-xs text-muted-foreground">
        <span>undo everything ({minSeq})</span>
        <span>keep through seq {target}</span>
        <span>undo nothing ({maxSeq})</span>
      </div>

      <div className="mt-4 border-t border-dashed pt-4">
        {previewError && <p className="text-sm text-red-400">{previewError}</p>}
        {!previewError && loading && !preview && <p className="text-sm text-muted-foreground">computing dry run…</p>}
        {preview && !previewError && (
          <>
            <p className="text-sm">
              Dry run to seq <strong className="font-mono">{preview.to_seq}</strong>: will restore{" "}
              <strong>{preview.summary.restored}</strong>, compensate{" "}
              <strong>{preview.summary.compensated}</strong>,{" "}
              <strong>{preview.summary.irreversible}</strong> irreversible — nothing
              executed.
            </p>
            <ItemList title="Restored (reversible)" items={preview.restored} empty="nothing to restore" />
            <ItemList
              title="Compensated (refunds / side-effect undo)"
              items={preview.compensated}
              empty="nothing to compensate"
            />
            <ItemList
              title="Irreversible — blast radius"
              items={preview.blast_radius}
              empty="no irreversible effects in range"
            />
            {preview.summary.failed > 0 && (
              <ItemList title="Failed" items={preview.failed} empty="" />
            )}
          </>
        )}
        <div className="mt-4">
          <Button onClick={confirm} disabled={running}>
            {running ? "rolling back…" : `Confirm rollback to seq ${target}`}
          </Button>{" "}
          {loading && preview && <span className="text-xs text-muted-foreground">refreshing preview…</span>}
        </div>
        {running && (
          <p className="mt-3 text-sm text-sky-400">
            Rollback running — the thread stays frozen until the engine finishes and
            releases the freeze…
          </p>
        )}
        {runError && <p className="mt-3 text-sm text-red-400">{runError}</p>}
        {result && !running && (
          <div className={result.complete ? "mt-3" : "mt-3 text-red-400"}>
            <p className="text-sm">
              Rollback {result.complete ? "complete" : "HALTED (partial)"} to seq{" "}
              <span className="font-mono">{result.to_seq}</span>: {result.summary.restored} restored,{" "}
              {result.summary.compensated} compensated, {result.summary.irreversible}{" "}
              irreversible, {result.summary.failed} failed.
            </p>
            <ItemList title="Restored" items={result.restored} empty="none" />
            <ItemList title="Compensated" items={result.compensated} empty="none" />
            <ItemList title="Irreversible — blast radius" items={result.blast_radius} empty="none" />
          </div>
        )}
      </div>
    </section>
  );
}
