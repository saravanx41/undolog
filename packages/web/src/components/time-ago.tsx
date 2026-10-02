"use client";

import { useEffect, useState } from "react";

function formatRelative(iso: string, now: number): string {
  const then = new Date(iso).getTime();
  const diff = Math.max(0, now - then);
  const s = Math.floor(diff / 1000);
  if (s < 45) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} hr ago`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d} day${d === 1 ? "" : "s"} ago`;
  return new Date(iso).toLocaleDateString();
}

export function TimeAgo({ iso }: { iso: string }) {
  // Re-render once shortly after mount so SSR and client agree on "now".
  const [now, setNow] = useState(0);
  useEffect(() => {
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);

  const exact = new Date(iso).toISOString().replace("T", " ").replace("Z", " UTC");
  if (!now) return <span title={exact}>{exact}</span>;
  return <span title={exact}>{formatRelative(iso, now)}</span>;
}
