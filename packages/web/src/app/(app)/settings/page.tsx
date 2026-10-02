export const dynamic = "force-dynamic";

export default function SettingsPage() {
  return (
    <div className="space-y-6">
      <div>
        <nav className="text-sm text-muted-foreground" aria-label="Breadcrumb">
          Settings
        </nav>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">Settings</h1>
      </div>
      <div className="rounded-lg border bg-card p-5">
        <h2 className="text-base font-semibold">Engine configuration</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          Engine configuration lives in environment variables — there is
          nothing to edit here yet.
        </p>
        <dl className="mt-4 grid grid-cols-[200px_1fr] gap-x-4 gap-y-1.5 text-sm">
          <dt className="text-xs text-muted-foreground">DATABASE_URL</dt>
          <dd className="font-mono text-xs">PostgreSQL connection string</dd>
          <dt className="text-xs text-muted-foreground">UNDOLOG_SCHEMA</dt>
          <dd className="font-mono text-xs">schema the engine searches (default public)</dd>
          <dt className="text-xs text-muted-foreground">UNDOLOG_PYTHON</dt>
          <dd className="font-mono text-xs">path to the Python bridge interpreter</dd>
        </dl>
      </div>
    </div>
  );
}
