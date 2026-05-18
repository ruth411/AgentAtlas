import { notFound } from "next/navigation";
import Link from "next/link";
import { server, formatVerificationLevel, ApiError } from "@/lib/api";
import { RiskBadge } from "@/components/risk-badge";

interface PageProps {
  params: { tool_id: string };
}

export default async function ToolDetailPage({ params }: PageProps) {
  const toolId = decodeURIComponent(params.tool_id);
  try {
    const spec = await server.getToolSpec(toolId);
    return (
      <div className="space-y-8">
        <header>
          <Link
            href="/tools"
            className="text-sm text-zinc-500 hover:text-zinc-900"
          >
            ← all tools
          </Link>
          <div className="mt-2 flex items-baseline gap-4">
            <h1 className="text-3xl font-semibold tracking-tight font-mono">
              {spec.tool_id}
            </h1>
            <span className="text-zinc-500">{spec.name}</span>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
            <RiskBadge risk={spec.risk_profile.default_risk_level} />
            <span className="font-mono text-zinc-500 px-2 py-0.5 rounded ring-1 ring-zinc-200">
              {formatVerificationLevel(spec.verification_level)}
            </span>
            <span className="text-zinc-500">
              · interfaces: {spec.interfaces.join(", ")}
            </span>
          </div>
        </header>

        <Section title="Capabilities" subtitle={`${spec.capabilities.length} declared`}>
          <ul className="flex flex-wrap gap-2">
            {spec.capabilities.map((cap) => (
              <li
                key={cap}
                className="text-xs font-mono bg-zinc-100 text-zinc-800 px-2 py-1 rounded"
              >
                {cap}
              </li>
            ))}
          </ul>
        </Section>

        {spec.commands.length > 0 && (
          <Section
            title="Commands"
            subtitle={`${spec.commands.length} compiled from accepted claims`}
          >
            <div className="space-y-2">
              {spec.commands.map((cmd) => (
                <div
                  key={cmd.name}
                  className="rounded border border-zinc-200 bg-white px-4 py-3"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <code className="font-mono text-sm font-medium">
                        {cmd.name}
                      </code>
                      <p className="mt-1 text-sm text-zinc-600">{cmd.summary}</p>
                    </div>
                    <div className="flex items-center gap-2 flex-shrink-0">
                      <RiskBadge risk={cmd.risk_level} />
                      {cmd.requires_confirmation && (
                        <span className="text-xs font-mono text-amber-700 bg-amber-50 ring-1 ring-amber-300 rounded px-2 py-0.5">
                          requires confirmation
                        </span>
                      )}
                    </div>
                  </div>
                  {cmd.side_effects.length > 0 && (
                    <div className="mt-2 text-xs text-zinc-500">
                      side effects: {cmd.side_effects.join(", ")}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </Section>
        )}

        <Section title="Auth">
          <div className="rounded border border-zinc-200 bg-white px-4 py-3 text-sm">
            <div>
              <span className="text-zinc-500">Required: </span>
              <span className="font-mono">
                {spec.auth.required ? "yes" : "no"}
              </span>
            </div>
            {spec.auth.methods.length > 0 && (
              <div className="mt-1">
                <span className="text-zinc-500">Methods: </span>
                <span className="font-mono">{spec.auth.methods.join(", ")}</span>
              </div>
            )}
          </div>
        </Section>

        <Section title="Risk profile">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-sm">
            <Flag label="Reads private data" value={spec.risk_profile.reads_private_data} />
            <Flag label="Mutates local state" value={spec.risk_profile.mutates_local_state} />
            <Flag label="Mutates remote state" value={spec.risk_profile.mutates_remote_state} />
            <Flag label="Can delete resources" value={spec.risk_profile.can_delete_resources} />
            <Flag label="Can incur cost" value={spec.risk_profile.can_incur_cost} />
            <Flag label="Exposes secrets" value={spec.risk_profile.exposes_secrets} />
          </div>
        </Section>

        <Section title="Provenance" subtitle="every spec traces back to its source">
          <div className="rounded border border-zinc-200 bg-white px-4 py-3 text-sm space-y-1.5">
            <div>
              <span className="text-zinc-500">Compiled by:</span>{" "}
              <span className="font-mono">{spec.provenance.compiled_by}</span>
            </div>
            <div>
              <span className="text-zinc-500">Compiled at:</span>{" "}
              <span className="font-mono">{spec.provenance.compiled_at}</span>
            </div>
            <div>
              <span className="text-zinc-500">Verification:</span>{" "}
              <span className="font-mono">
                {formatVerificationLevel(spec.provenance.verification_level)}
              </span>
            </div>
            <div>
              <span className="text-zinc-500">Source claims:</span>{" "}
              <span className="font-mono text-xs text-zinc-700">
                {spec.provenance.source_claim_ids.length}
              </span>
            </div>
            <div>
              <span className="text-zinc-500">Source evidence:</span>{" "}
              <span className="font-mono text-xs text-zinc-700">
                {spec.provenance.source_evidence_ids.length}
              </span>
            </div>
          </div>
        </Section>
      </div>
    );
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      notFound();
    }
    return (
      <div className="rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800">
        <div className="font-semibold mb-1">Couldn&apos;t load {toolId}.</div>
        <div className="font-mono text-xs">
          {err instanceof Error ? err.message : String(err)}
        </div>
      </div>
    );
  }
}

function Section({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <section>
      <div className="flex items-baseline justify-between mb-3">
        <h2 className="text-xs uppercase tracking-wider text-zinc-500 font-semibold">
          {title}
        </h2>
        {subtitle && (
          <span className="text-xs text-zinc-500">{subtitle}</span>
        )}
      </div>
      {children}
    </section>
  );
}

function Flag({
  label,
  value,
}: {
  label: string;
  value: boolean | null | undefined;
}) {
  const displayed = value === null || value === undefined ? "?" : value ? "yes" : "no";
  const tone =
    value === true
      ? "text-orange-700"
      : value === false
        ? "text-emerald-700"
        : "text-zinc-400";
  return (
    <div className="flex items-center justify-between rounded border border-zinc-200 bg-white px-3 py-2">
      <span className="text-zinc-700">{label}</span>
      <span className={`font-mono ${tone}`}>{displayed}</span>
    </div>
  );
}
