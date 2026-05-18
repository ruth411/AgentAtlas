import Link from "next/link";

export default function ToolNotFound() {
  return (
    <div className="text-center py-16">
      <h1 className="text-2xl font-semibold tracking-tight">
        No canonical ToolSpec published.
      </h1>
      <p className="mt-3 text-zinc-600 max-w-md mx-auto">
        AgentAtlas knows about the tool id you asked for, but no canonical
        spec has been compiled yet. Add evidence-backed claims for the tool
        and publish a spec to populate this page.
      </p>
      <Link
        href="/tools"
        className="inline-block mt-6 text-sm text-zinc-700 hover:text-zinc-900 underline"
      >
        ← back to all tools
      </Link>
    </div>
  );
}
