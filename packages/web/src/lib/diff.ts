export type DiffOp = "equal" | "added" | "removed";

export interface DiffLine {
  op: DiffOp;
  text: string;
}

function pretty(value: unknown): string[] {
  if (value === null || value === undefined) return ["(none)"];
  return JSON.stringify(value, null, 2).split("\n");
}

// Simple line-based diff (LCS) of pretty-printed JSON. Above the size cap
// we degrade to "all old lines removed, all new lines added" so the UI
// stays responsive on huge proofs.
const MAX_LINES = 500;

export function diffJson(before: unknown, after: unknown): DiffLine[] {
  const a = pretty(before);
  const b = pretty(after);
  if (a.length > MAX_LINES || b.length > MAX_LINES) {
    return [
      ...a.map((text) => ({ op: "removed" as DiffOp, text })),
      ...b.map((text) => ({ op: "added" as DiffOp, text })),
    ];
  }

  const n = a.length;
  const m = b.length;
  // LCS lengths table.
  const lcs: number[][] = Array.from({ length: n + 1 }, () =>
    new Array<number>(m + 1).fill(0),
  );
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i][j] =
        a[i] === b[j]
          ? lcs[i + 1][j + 1] + 1
          : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }

  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ op: "equal", text: a[i] });
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      out.push({ op: "removed", text: a[i] });
      i++;
    } else {
      out.push({ op: "added", text: b[j] });
      j++;
    }
  }
  while (i < n) out.push({ op: "removed", text: a[i++] });
  while (j < m) out.push({ op: "added", text: b[j++] });
  return out;
}
