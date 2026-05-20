import Link from "next/link";
import { TryQuery } from "./_try-query";

export default function HomePage() {
  return (
    <div className="space-y-16">
      {/* Hero */}
      <section className="text-center pt-6 pb-2">
        <h1 className="text-4xl sm:text-5xl font-semibold tracking-tight">
          A verified knowledge layer for AI agents.
        </h1>
        <p className="mt-5 max-w-2xl mx-auto text-lg text-zinc-600">
          Wikipedia tells humans what things are. Ayiru tells AI agents
          what tools can do, how to use them, and{" "}
          <span className="text-zinc-900 font-medium">
            whether they&apos;re safe.
          </span>
        </p>
        <div className="mt-7 flex flex-wrap items-center justify-center gap-3 text-sm text-zinc-500">
          <span className="font-mono">no LLM in the safety path</span>
          <span>·</span>
          <span className="font-mono">evidence required for every claim</span>
          <span>·</span>
          <span className="font-mono">L0 → L3 verification levels</span>
        </div>
      </section>

      {/* The interactive playground */}
      <section>
        <h2 className="text-2xl font-semibold tracking-tight">
          Ask Ayiru if a command is safe
        </h2>
        <p className="mt-2 text-zinc-600 max-w-3xl">
          Every Ayiru verdict is backed by cited evidence. The three
          examples below hit a fresh-seeded graph; the
          <Link href="/query" className="text-zinc-900 underline mx-1">
            full query playground
          </Link>
          lets you try any tool + command pair.
        </p>
        <div className="mt-6">
          <TryQuery />
        </div>
      </section>

      {/* What's inside */}
      <section className="grid grid-cols-1 md:grid-cols-3 gap-5">
        <FeatureCard
          title="Six MCP tools"
          body="Plug Ayiru into Claude Desktop or Cursor with one config block. The agent then asks Ayiru before acting."
        />
        <FeatureCard
          title="Deterministic risk classifier"
          body="No LLM judgement in the safety path. Risk is computed from a versioned contract of rules; the same input always produces the same verdict."
        />
        <FeatureCard
          title="Byte-level provenance"
          body="Every claim cites the exact source URI, JSON Pointer, or SDL location it came from. Audit a verdict by walking back to its evidence."
        />
      </section>
    </div>
  );
}

function FeatureCard({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-5">
      <h3 className="font-semibold tracking-tight">{title}</h3>
      <p className="mt-2 text-sm text-zinc-600 leading-relaxed">{body}</p>
    </div>
  );
}
