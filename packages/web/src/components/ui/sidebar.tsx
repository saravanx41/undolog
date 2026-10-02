"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ExternalLink, ListTree, Settings } from "lucide-react";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { href: "/threads", label: "Threads", icon: ListTree },
  {
    href: "https://github.com/saravanx41/tool-safety-registry",
    label: "Registry",
    icon: ExternalLink,
    external: true,
  },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="fixed inset-y-0 left-0 z-40 flex w-60 flex-col border-r bg-card">
      <div className="flex h-14 items-center gap-2.5 border-b px-5">
        <span className="flex h-6 w-6 items-center justify-center rounded bg-primary text-[11px] font-bold text-primary-foreground">
          U
        </span>
        <Link href="/" className="text-sm font-semibold tracking-tight">
          undolog
        </Link>
      </div>
      <nav className="flex-1 space-y-1 px-3 py-4">
        {NAV_ITEMS.map((item) => {
          const active =
            !item.external &&
            (pathname === item.href || pathname.startsWith(`${item.href}/`));
          const className = cn(
            "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium transition-colors",
            active
              ? "bg-accent text-accent-foreground"
              : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
          );
          if (item.external) {
            return (
              <a
                key={item.label}
                href={item.href}
                target="_blank"
                rel="noreferrer"
                className={className}
              >
                <item.icon className="h-4 w-4" />
                {item.label}
              </a>
            );
          }
          return (
            <Link key={item.label} href={item.href} className={className}>
              <item.icon className="h-4 w-4" />
              {item.label}
            </Link>
          );
        })}
      </nav>
      <div className="border-t px-5 py-3 text-xs text-muted-foreground">
        MIT · append-only ledger
      </div>
    </aside>
  );
}
