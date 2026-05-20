"use client";

import { useState } from "react";
import {
  ApiError,
  client,
  type ValidateCommandResponse,
} from "@/lib/api";
import { VerdictCard } from "@/components/verdict-card";

export default function QueryPage() {
  const [toolId, setToolId] = useState("github-cli");
  const [command, setCommand] = useState("gh repo delete my-org/x --yes");
  const [verdict, setVerdict] = useState<ValidateCommandResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!toolId.trim() || !command.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const result = await client.validateCommand(toolId.trim(), command.trim());
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
    <div className="max-w-3xl mx-auto space-y-8">
      <header>
        <h1 className="text-3xl font-semibold tracking-tight">
          validate_command playground
        </h1>
        <p className="mt-2 text-zinc-600">
          Ask Ayiru about any command. The matcher uses strict
          exact-or-prefix matching against verified claims; no LLM in the
          safety path; no fuzzy fallback.
        </p>
      </header>

      <form
        onSubmit={submit}
        className="space-y-4 rounded-lg border border-zinc-200 bg-white p-5"
      >
        <Field label="tool_id">
          <input
            type="text"
            value={toolId}
            onChange={(e) => setToolId(e.target.value)}
            placeholder="e.g. github-cli"
            className="w-full font-mono text-sm border border-zinc-300 rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-zinc-400"
            spellCheck={false}
            autoCapitalize="off"
            autoCorrect="off"
          />
        </Field>
        <Field label="command">
          <input
            type="text"
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            placeholder="e.g. gh repo delete my-org/x --yes"
            className="w-full font-mono text-sm border border-zinc-300 rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-zinc-400"
            spellCheck={false}
            autoCapitalize="off"
            autoCorrect="off"
          />
        </Field>
        <div className="flex items-center justify-between">
          <div className="text-xs text-zinc-500">
            POST to <code className="font-mono">/query/validate-command</code>
          </div>
          <button
            type="submit"
            disabled={loading || !toolId.trim() || !command.trim()}
            className="bg-zinc-900 text-white text-sm px-4 py-2 rounded font-medium hover:bg-zinc-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? "querying…" : "Get verdict"}
          </button>
        </div>
      </form>

      {error && (
        <div className="rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800">
          <div className="font-semibold mb-1">Error</div>
          <div className="font-mono text-xs">{error}</div>
        </div>
      )}

      {verdict && !loading && <VerdictCard verdict={verdict} />}

      {verdict && !loading && (
        <details className="text-sm">
          <summary className="text-zinc-500 cursor-pointer hover:text-zinc-900">
            Show raw JSON response
          </summary>
          <pre className="mt-2 text-xs bg-zinc-900 text-zinc-100 rounded p-4 overflow-x-auto">
            {JSON.stringify(verdict, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="block text-xs uppercase tracking-wide text-zinc-500 font-semibold mb-1">
        {label}
      </label>
      {children}
    </div>
  );
}
