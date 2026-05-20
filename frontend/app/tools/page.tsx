import Link from "next/link";
import { server, type ToolMatchSummary, formatVerificationLevel } from "@/lib/api";
import { RiskBadge } from "@/components/risk-badge";

export default async function ToolsPage() {
  let tools: ToolMatchSummary[] = [];
  let errorMessage: string | null = null;

  try {
    const response = await server.searchTools("", 200, 0);
    tools = response.matches;
  } catch (err) {
    errorMessage = err instanceof Error ? err.message : String(err);
  }

  return (
    <div>
      <header className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight">Tools</h1>
        <p className="mt-2 text-zinc-600">
          Every tool with a published canonical ToolSpec. Tap a row to see its
          full capability surface, risk profile, and provenance.
        </p>
      </header>

      {errorMessage ? (
        <ErrorBanner message={errorMessage} />
      ) : tools.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="space-y-2">
          {tools.map((tool) => (
            <Link
              key={tool.tool_id}
              href={`/tools/${encodeURIComponent(tool.tool_id)}`}
              className="block rounded-lg border border-zinc-200 bg-white px-5 py-4 hover:border-zinc-400 hover:shadow-sm transition-shadow"
            >
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex items-baseline gap-3">
                    <h2 className="text-lg font-semibold tracking-tight font-mono">
                      {tool.tool_id}
                    </h2>
                    <span className="text-sm text-zinc-500 truncate">
                      {tool.name}
                    </span>
                  </div>
                  <div className="mt-1 text-xs text-zinc-500 font-mono">
                    {tool.interfaces.join(" · ")}
                  </div>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <RiskBadge risk={tool.highest_risk_level} />
                  <span className="text-xs font-mono text-zinc-500 px-2 py-0.5 rounded ring-1 ring-zinc-200">
                    {formatVerificationLevel(tool.verification_level)}
                  </span>
                </div>
              </div>
              <div className="mt-3 flex items-center gap-5 text-xs text-zinc-500">
                <Counter label="capabilities" value={tool.capability_count} />
                <Counter
                  label="verified commands"
                  value={tool.verified_command_count}
                />
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function Counter({ label, value }: { label: string; value: number }) {
  return (
    <span>
      <span className="font-mono text-zinc-900 font-medium">{value}</span>{" "}
      <span>{label}</span>
    </span>
  );
}

function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800">
      <div className="font-semibold mb-1">Couldn&apos;t reach Ayiru.</div>
      <div className="font-mono text-xs">{message}</div>
      <div className="mt-2 text-xs">
        Start the backend:{" "}
        <code className="font-mono">uvicorn app.main:app --reload</code>
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="rounded-lg border border-dashed border-zinc-300 bg-white p-10 text-center">
      <h2 className="text-lg font-semibold">No published tools yet</h2>
      <p className="mt-2 text-sm text-zinc-600 max-w-md mx-auto">
        Seed the demo graph so this page has content:
      </p>
      <pre className="mt-4 text-xs bg-zinc-100 rounded p-3 inline-block text-left">
        python scripts/seed_examples.py --reset
      </pre>
    </div>
  );
}
