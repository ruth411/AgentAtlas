"use client";

import { useState } from "react";
import {
  ApiError,
  client,
  type ValidateCommandResponse,
} from "@/lib/api";
import { VerdictCard } from "@/components/verdict-card";

const EXAMPLES: { tool_id: string; command: string; description: string }[] = [
  {
    tool_id: "github-cli",
    command: "gh repo delete my-org/critical-prod --yes",
    description: "the headline destructive case",
  },
  {
    tool_id: "git",
    command: "git status",
    description: "a safe read-only command",
  },
  {
    tool_id: "vercel-cli",
    command: "vercel --prod",
    description: "production deploy — high risk",
  },
];

export function TryQuery() {
  const [verdict, setVerdict] = useState<ValidateCommandResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function run(toolId: string, command: string) {
    setLoading(true);
    setError(null);
    try {
      const result = await client.validateCommand(toolId, command);
      setVerdict(result);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(`${err.status}: ${err.message}`);
      } else {
        setError(String(err));
      }
      setVerdict(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {EXAMPLES.map((ex) => (
          <button
            key={ex.command}
            onClick={() => run(ex.tool_id, ex.command)}
            disabled={loading}
            className="text-left rounded-lg border border-zinc-200 bg-white p-4 hover:border-zinc-400 hover:shadow-sm transition-shadow disabled:opacity-50"
          >
            <div className="text-xs text-zinc-500 mb-1">{ex.description}</div>
            <code className="font-mono text-sm text-zinc-900 block">
              {ex.command}
            </code>
            <div className="text-xs text-zinc-400 mt-2">
              tool_id: {ex.tool_id}
            </div>
          </button>
        ))}
      </div>

      {loading && (
        <div className="text-sm text-zinc-500 font-mono">querying…</div>
      )}

      {error && (
        <div className="rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800">
          <div className="font-semibold mb-1">Couldn&apos;t reach AgentAtlas.</div>
          <div className="font-mono text-xs">{error}</div>
          <div className="mt-2 text-xs text-red-700">
            Make sure the backend is running:{" "}
            <code className="font-mono">uvicorn app.main:app --reload</code>
          </div>
        </div>
      )}

      {verdict && !loading && <VerdictCard verdict={verdict} />}
    </div>
  );
}
