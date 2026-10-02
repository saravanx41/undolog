import { Pool, type QueryResultRow } from "pg";

const DEFAULT_DATABASE_URL =
  "postgresql://undolog:undolog@localhost:5432/undolog";

let pool: Pool | null = null;

function schema(): string {
  const s = process.env.UNDOLOG_SCHEMA ?? "public";
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(s)) {
    throw new Error(`UNDOLOG_SCHEMA must be a simple identifier, got ${JSON.stringify(s)}`);
  }
  return s;
}

export function getPool(): Pool {
  if (!pool) {
    pool = new Pool({
      connectionString: process.env.DATABASE_URL ?? DEFAULT_DATABASE_URL,
    });
  }
  return pool;
}

export async function query<T extends QueryResultRow>(text: string, params?: unknown[]) {
  // Schema comes from env and is validated as a plain identifier above;
  // table/column names are fixed literals in this file only.
  const searchPath = `SET search_path TO "${schema()}"`;
  const client = await getPool().connect();
  try {
    await client.query(searchPath);
    return await client.query<T>(text, params);
  } finally {
    client.release();
  }
}

export interface ThreadSummaryRow {
  thread_id: string;
  entry_count: string;
  last_ts: string;
  frozen_at: string | null;
}

export async function listThreads(): Promise<ThreadSummaryRow[]> {
  const { rows } = await query<ThreadSummaryRow>(
    `SELECT e.thread_id,
            count(*) AS entry_count,
            max(e.ts) AS last_ts,
            f.frozen_at
       FROM ledger_entries e
       LEFT JOIN thread_freezes f ON f.thread_id = e.thread_id
      GROUP BY e.thread_id, f.frozen_at
      ORDER BY last_ts DESC`,
  );
  return rows;
}

export interface LedgerEntryRow {
  id: string;
  ts: string;
  thread_id: string;
  seq: number;
  tool_name: string;
  args_hash: string;
  idempotency_key: string;
  before_jsonb: unknown;
  after_jsonb: unknown;
  class: string;
  compensation_ref: string | null;
  status: string;
  prompt_context_ref: string | null;
  log_only: boolean;
}

export async function listEntries(threadId: string): Promise<LedgerEntryRow[]> {
  const { rows } = await query<LedgerEntryRow>(
    `SELECT id, ts, thread_id, seq, tool_name, args_hash,
            idempotency_key, before_jsonb, after_jsonb, class,
            compensation_ref, status, prompt_context_ref, log_only
       FROM ledger_entries
      WHERE thread_id = $1
      ORDER BY seq ASC`,
    [threadId],
  );
  return rows;
}

export async function threadFrozen(threadId: string): Promise<string | null> {
  const { rows } = await query<{ frozen_at: string | null }>(
    `SELECT frozen_at FROM thread_freezes WHERE thread_id = $1`,
    [threadId],
  );
  return rows[0]?.frozen_at ?? null;
}
