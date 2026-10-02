import type { DiffLine } from "@/lib/diff";
import { cn } from "@/lib/utils";

// Hand-rolled JSON token coloring: wraps keys, strings, numbers, booleans,
// nulls and punctuation of a single pretty-printed JSON line in spans.
function tokenize(line: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  let i = 0;
  let key = 0;

  const push = (cls: string | null, text: string) => {
    out.push(
      cls ? (
        <span key={key++} className={cls}>
          {text}
        </span>
      ) : (
        <span key={key++}>{text}</span>
      ),
    );
  };

  while (i < line.length) {
    const ch = line[i];
    const rest = line.slice(i);

    // string (possibly a key: check the tail after the closing quote)
    if (ch === '"') {
      const end = rest.indexOf('"', 1);
      const tok = end === -1 ? rest : rest.slice(0, end + 1);
      const after = rest.slice(end + 1).trimStart();
      const isKey = end !== -1 && after.startsWith(":");
      push(isKey ? "tok-key" : "tok-string", tok);
      i += tok.length;
      continue;
    }
    // number
    const numMatch = /^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(rest);
    if (numMatch) {
      push("tok-number", numMatch[0]);
      i += numMatch[0].length;
      continue;
    }
    // literals
    if (rest.startsWith("true") || rest.startsWith("false")) {
      const tok = rest.startsWith("true") ? "true" : "false";
      push("tok-bool", tok);
      i += tok.length;
      continue;
    }
    if (rest.startsWith("null")) {
      push("tok-null", "null");
      i += 4;
      continue;
    }
    // punctuation / whitespace — group a run of it
    const punctMatch = /^[\s,:[\]{}]+/.exec(rest);
    if (punctMatch) {
      push("tok-punct", punctMatch[0]);
      i += punctMatch[0].length;
      continue;
    }
    push(null, ch);
    i++;
  }
  return out;
}

export function JsonDiff({ lines }: { lines: DiffLine[] }) {
  return (
    <div className="overflow-x-auto rounded-md border bg-[#0d1117] py-2 font-mono">
      {lines.map((line, i) => (
        <div
          key={i}
          className={cn(
            "diff-line",
            line.op === "added" && "added",
            line.op === "removed" && "removed",
          )}
        >
          <span className="inline-block w-4 select-none text-zinc-600">
            {line.op === "added" ? "+" : line.op === "removed" ? "−" : " "}
          </span>
          {tokenize(line.text)}
        </div>
      ))}
    </div>
  );
}
