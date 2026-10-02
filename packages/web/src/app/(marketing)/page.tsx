import Link from "next/link";
import { buttonVariants } from "@/components/ui/button";
import { CopyButton } from "@/components/copy-button";
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

const GITHUB_URL = "https://github.com/saravanx41/undolog";
const REGISTRY_URL = "https://github.com/saravanx41/tool-safety-registry";

function GithubIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden="true">
      <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.58.11.79-.25.79-.55v-2.15c-3.2.7-3.87-1.36-3.87-1.36-.52-1.33-1.28-1.68-1.28-1.68-1.05-.72.08-.71.08-.71 1.16.08 1.77 1.19 1.77 1.19 1.03 1.77 2.7 1.26 3.36.96.1-.75.4-1.26.73-1.55-2.55-.29-5.23-1.28-5.23-5.68 0-1.26.45-2.28 1.19-3.09-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.18 1.18a11.1 11.1 0 0 1 5.8 0c2.2-1.49 3.17-1.18 3.17-1.18.63 1.59.23 2.76.11 3.05.74.81 1.19 1.83 1.19 3.09 0 4.41-2.69 5.38-5.25 5.67.41.35.78 1.05.78 2.12v3.15c0 .3.21.67.8.55A11.52 11.52 0 0 0 23.5 12C23.5 5.65 18.35.5 12 .5Z" />
    </svg>
  );
}

const TERMINAL_LINES: { text: string; cls?: string }[] = [
  { text: "$ undolog demo --no-rollback", cls: "text-zinc-400" },
  { text: "[agent] 18 tool calls wrapped and logged …", cls: "text-zinc-500" },
  { text: "[alert] rogue behavior detected — thread frozen", cls: "text-amber-400" },
  { text: "[DRY-RUN] rollback preview to seq 12:", cls: "text-zinc-300" },
  { text: "  3 restores, 1 refund, 2 irreversible — nothing executed", cls: "text-emerald-400" },
  { text: "$ ▌", cls: "text-zinc-400 animate-caret-blink" },
];

const TAXONOMY = [
  {
    title: "Reversible",
    color: "text-emerald-400",
    body: "Every write is captured with before/after proof. Undo restores the exact prior state — not an approximation.",
  },
  {
    title: "Compensatable",
    color: "text-sky-400",
    body: "No true undo exists for a sent charge — so the ledger runs the registered compensation (a refund) and proves it landed.",
  },
  {
    title: "Irreversible",
    color: "text-red-400",
    body: "Sent emails stay sent. The engine refuses to pretend otherwise and reports the blast radius, honestly.",
  },
];

export default function LandingPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b">
        <div className="mx-auto flex h-14 max-w-5xl items-center justify-between px-6">
          <div className="flex items-center gap-2.5">
            <span className="flex h-6 w-6 items-center justify-center rounded bg-primary text-[11px] font-bold text-primary-foreground">
              U
            </span>
            <span className="text-sm font-semibold tracking-tight">undolog</span>
          </div>
          <nav className="flex items-center gap-3">
            <a
              href={GITHUB_URL}
              target="_blank"
              rel="noreferrer"
              className={buttonVariants({ variant: "ghost", size: "sm" })}
            >
              <GithubIcon className="h-4 w-4" />
              GitHub
            </a>
            <Link href="/threads" className={buttonVariants({ size: "sm" })}>
              Launch app
            </Link>
          </nav>
        </div>
      </header>

      <main className="mx-auto w-full max-w-5xl flex-1 px-6">
        <section className="grid gap-12 py-20 md:grid-cols-2 md:items-center">
          <div className="animate-fade-in-up">
            <h1 className="text-4xl font-bold leading-tight tracking-tight">
              Undo what your AI agent did to the world
            </h1>
            <p className="mt-4 text-lg text-muted-foreground">
              undolog wraps every tool call in an append-only ledger with
              before/after proof, classifies each effect, and rolls your agent
              back to any checkpoint — with a dry run first, always.
            </p>
            <div className="mt-8 flex gap-3">
              <Link href="/threads" className={buttonVariants({ size: "lg" })}>
                Run the demo
              </Link>
              <a
                href={GITHUB_URL}
                target="_blank"
                rel="noreferrer"
                className={buttonVariants({ size: "lg", variant: "outline" })}
              >
                Read the docs
              </a>
            </div>
          </div>

          <div className="overflow-hidden rounded-lg border bg-[#0d1117] shadow-2xl animate-fade-in-up">
            <div className="flex items-center gap-1.5 border-b border-zinc-800 px-4 py-2.5">
              <span className="h-2.5 w-2.5 rounded-full bg-red-500/70" />
              <span className="h-2.5 w-2.5 rounded-full bg-amber-500/70" />
              <span className="h-2.5 w-2.5 rounded-full bg-emerald-500/70" />
              <span className="ml-2 text-xs text-zinc-500">undolog — rollback preview</span>
            </div>
            <div className="p-4 font-mono text-[13px] leading-7">
              {TERMINAL_LINES.map((l, i) => (
                <div key={i} className={l.cls}>
                  {l.text}
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="grid gap-4 pb-16 md:grid-cols-3">
          {TAXONOMY.map((t) => (
            <Card key={t.title}>
              <CardHeader>
                <CardTitle className={t.color}>{t.title}</CardTitle>
                <CardDescription className="mt-2 leading-relaxed">
                  {t.body}
                </CardDescription>
              </CardHeader>
            </Card>
          ))}
        </section>

        <section className="pb-20">
          <h2 className="text-xl font-semibold tracking-tight">Try it</h2>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <div className="rounded-lg border bg-card">
              <div className="flex items-center justify-between border-b px-4 py-2">
                <span className="text-xs text-muted-foreground">install</span>
                <CopyButton text="pip install undolog" className="h-7 px-2 text-xs" />
              </div>
              <pre className="p-4 font-mono text-sm">pip install undolog</pre>
            </div>
            <div className="rounded-lg border bg-card">
              <div className="flex items-center justify-between border-b px-4 py-2">
                <span className="text-xs text-muted-foreground">run the narrated scenario</span>
                <CopyButton text="undolog demo" className="h-7 px-2 text-xs" />
              </div>
              <pre className="p-4 font-mono text-sm">undolog demo</pre>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-2 px-6 py-6 text-sm text-muted-foreground">
          <span>undolog is MIT licensed.</span>
          <a
            href={REGISTRY_URL}
            target="_blank"
            rel="noreferrer"
            className="underline-offset-4 hover:text-foreground hover:underline"
          >
            Tool Safety Registry
          </a>
        </div>
      </footer>
    </div>
  );
}
