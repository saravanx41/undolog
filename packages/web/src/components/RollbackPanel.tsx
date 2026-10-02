"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import type { RollbackItem, RollbackReport } from "@/lib/python";

function ItemList({ title, items, empty }: { title: string; items: RollbackItem[]; empty: string }) {
  return (
    <div>
      <strong>
        {title} ({items.length})
      </strong>
      {items.length === 0 ? (
        <p className="muted">{empty}</p>
      ) : (
        <ul>
          {items.map((item) => (
            <li key={`${item.entry_id ?? item.seq}-${item.action}`}>
              seq {item.seq} · {item.tool_name} · {item.entry_class}
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
      setResult(body as RollbackReport);
      router.refresh();
    } catch (e) {
      setRunError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }

  return (
    <section className="rollback-panel">
      <h2>Rollback — drag to checkpoint</h2>
      <p className="muted">
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
      />
      <div className="scrub-labels">
        <span>undo everything ({minSeq})</span>
        <span>keep through seq {target}</span>
        <span>undo nothing ({maxSeq})</span>
      </div>

      <div className="preview-box">
        {previewError && <p className="error">{previewError}</p>}
        {!previewError && loading && !preview && <p className="muted">computing dry run…</p>}
        {preview && !previewError && (
          <>
            <p>
              Dry run to seq <strong>{preview.to_seq}</strong>: will restore{" "}
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
        <p>
          <button className="primary" disabled={running} onClick={confirm}>
            {running ? "rolling back…" : `Confirm rollback to seq ${target}`}
          </button>{" "}
          {loading && preview && <span className="muted">refreshing preview…</span>}
        </p>
        {running && (
          <p className="running">
            Rollback running — the thread stays frozen until the engine finishes and
            releases the freeze…
          </p>
        )}
        {runError && <p className="error">{runError}</p>}
        {result && !running && (
          <div className={result.complete ? "" : "error"}>
            <p>
              Rollback {result.complete ? "complete" : "HALTED (partial)"} to seq{" "}
              {result.to_seq}: {result.summary.restored} restored,{" "}
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
