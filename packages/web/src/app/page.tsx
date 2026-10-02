import Link from "next/link";
import { listThreads } from "@/lib/db";

export const dynamic = "force-dynamic";

export default async function ThreadListPage() {
  let threads: Awaited<ReturnType<typeof listThreads>> = [];
  let error: string | null = null;
  try {
    threads = await listThreads();
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  return (
    <>
      <h1>Threads</h1>
      {error && <p className="error">Failed to read ledger: {error}</p>}
      {!error && threads.length === 0 && (
        <p className="muted">No ledger entries found.</p>
      )}
      {threads.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>thread_id</th>
              <th>entries</th>
              <th>last activity</th>
              <th>status</th>
            </tr>
          </thead>
          <tbody>
            {threads.map((t) => (
              <tr key={t.thread_id}>
                <td>
                  <Link href={`/threads/${encodeURIComponent(t.thread_id)}`}>
                    {t.thread_id}
                  </Link>
                </td>
                <td>{t.entry_count}</td>
                <td>{new Date(t.last_ts).toISOString()}</td>
                <td>
                  {t.frozen_at ? (
                    <span className="badge badge-failed">
                      frozen since {new Date(t.frozen_at).toISOString()}
                    </span>
                  ) : (
                    <span className="badge badge-applied">active</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
