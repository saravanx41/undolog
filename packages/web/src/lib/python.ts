import { execFile } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

export interface RollbackItem {
  entry_id: string | null;
  seq: number;
  tool_name: string;
  entry_class: string;
  action: string;
  reason?: string | null;
  idempotency_key?: string | null;
}

export interface RollbackReport {
  thread_id: string;
  to_seq: number;
  dry_run: boolean;
  complete: boolean;
  restored: RollbackItem[];
  compensated: RollbackItem[];
  blast_radius: RollbackItem[];
  failed: RollbackItem[];
  summary: {
    restored: number;
    compensated: number;
    irreversible: number;
    failed: number;
  };
}

// Walk up from the web package (or CWD) to the repo root, identified by
// the docker-compose file, so this works from dev, build, or start.
function repoRoot(): string {
  let dir = path.resolve(process.cwd());
  for (;;) {
    if (fs.existsSync(path.join(dir, "docker-compose.yml"))) return dir;
    const parent = path.dirname(dir);
    if (parent === dir) return path.resolve(process.cwd());
    dir = parent;
  }
}

function pythonBin(): string {
  if (process.env.UNDOLOG_PYTHON) return process.env.UNDOLOG_PYTHON;
  const venv = path.join(repoRoot(), ".venv", "bin", "python");
  return fs.existsSync(venv) ? venv : "python3";
}

function runBridge(
  command: "rollback-preview" | "rollback-execute",
  threadId: string,
  toSeq: number,
): Promise<RollbackReport> {
  return new Promise((resolve, reject) => {
    execFile(
      pythonBin(),
      [
        "-m",
        "undolog_torture.api",
        command,
        "--thread",
        threadId,
        "--to-seq",
        String(toSeq),
        "--json",
      ],
      {
        cwd: repoRoot(),
        timeout: 120_000,
        env: {
          ...process.env,
          DATABASE_URL:
            process.env.DATABASE_URL ??
            "postgresql://undolog:undolog@localhost:5432/undolog",
        },
        maxBuffer: 8 * 1024 * 1024,
      },
      (error, stdout, stderr) => {
        if (error) {
          reject(new Error(`bridge ${command} failed: ${stderr || error.message}`));
          return;
        }
        try {
          resolve(JSON.parse(stdout) as RollbackReport);
        } catch {
          reject(new Error(`bridge returned non-JSON output: ${stdout.slice(0, 500)}`));
        }
      },
    );
  });
}

// -- cached previews --------------------------------------------------------

const PREVIEW_TTL_MS = 15_000;
const previewCache = new Map<string, { at: number; report: RollbackReport }>();

export async function previewRollback(
  threadId: string,
  toSeq: number,
): Promise<RollbackReport> {
  const key = `${threadId}:${toSeq}`;
  const hit = previewCache.get(key);
  if (hit && Date.now() - hit.at < PREVIEW_TTL_MS) {
    return hit.report;
  }
  const report = await runBridge("rollback-preview", threadId, toSeq);
  previewCache.set(key, { at: Date.now(), report });
  return report;
}

export async function executeRollback(
  threadId: string,
  toSeq: number,
): Promise<RollbackReport> {
  const report = await runBridge("rollback-execute", threadId, toSeq);
  // Executing changes the ledger; drop every cached preview for the thread.
  for (const key of previewCache.keys()) {
    if (key.startsWith(`${threadId}:`)) previewCache.delete(key);
  }
  return report;
}

// -- demo seeding -----------------------------------------------------------

export interface DemoResult {
  thread_id: string;
  entry_count: number;
}

export function runDemo(): Promise<DemoResult> {
  return new Promise((resolve, reject) => {
    execFile(
      pythonBin(),
      ["-m", "undolog_torture.api", "run-demo", "--json"],
      {
        cwd: repoRoot(),
        timeout: 120_000,
        env: {
          ...process.env,
          DATABASE_URL:
            process.env.DATABASE_URL ??
            "postgresql://undolog:undolog@localhost:5432/undolog",
        },
        maxBuffer: 8 * 1024 * 1024,
      },
      (error, stdout, stderr) => {
        if (error) {
          reject(new Error(`bridge run-demo failed: ${stderr || error.message}`));
          return;
        }
        try {
          resolve(JSON.parse(stdout) as DemoResult);
        } catch {
          reject(new Error(`bridge returned non-JSON output: ${stdout.slice(0, 500)}`));
        }
      },
    );
  });
}
