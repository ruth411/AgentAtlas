import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Ayiru",
  description:
    "A verified, machine-readable knowledge layer for AI agents. " +
    "Wikipedia tells humans what things are. Ayiru tells AI agents " +
    "what tools can do, how to use them, and whether they're safe.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-zinc-50 text-zinc-900 antialiased">
        <header className="border-b border-zinc-200 bg-white">
          <div className="mx-auto max-w-6xl px-6 py-4 flex items-center justify-between">
            <Link
              href="/"
              className="font-mono text-sm font-semibold tracking-tight hover:opacity-80"
            >
              Ayiru
              <span className="ml-2 text-xs font-normal text-zinc-500">
                v0.1
              </span>
            </Link>
            <nav className="flex items-center gap-1 text-sm">
              <NavLink href="/query">Query</NavLink>
              <NavLink href="/tools">Tools</NavLink>
              <a
                href="https://github.com/ruth411/ayiru"
                target="_blank"
                rel="noreferrer"
                className="px-3 py-1.5 text-zinc-500 hover:text-zinc-900"
              >
                GitHub
              </a>
            </nav>
          </div>
        </header>

        <main className="mx-auto max-w-6xl px-6 py-10">{children}</main>

        <footer className="border-t border-zinc-200 bg-white mt-16">
          <div className="mx-auto max-w-6xl px-6 py-6 text-xs text-zinc-500 flex items-center justify-between">
            <span>
              Ayiru — verified knowledge for AI agents. MIT licensed.
            </span>
            <span className="font-mono">
              No LLM in the safety path. Evidence required.
            </span>
          </div>
        </footer>
      </body>
    </html>
  );
}

function NavLink({
  href,
  children,
}: {
  href: string;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      className="px-3 py-1.5 rounded-md text-zinc-700 hover:bg-zinc-100 hover:text-zinc-900"
    >
      {children}
    </Link>
  );
}
