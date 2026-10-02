import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "undolog — ledger timeline",
  description: "Audit ledger timeline and rollback console",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <header className="site-header">
          <a href="/">undolog</a>
          <span className="site-sub">ledger timeline</span>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
