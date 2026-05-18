import type { ValidateCommandResponse } from "@/lib/api";
import { formatVerificationLevel } from "@/lib/api";
import { RiskBadge } from "./risk-badge";

export function VerdictCard({
  verdict,
}: {
  verdict: ValidateCommandResponse;
}) {
  const safe = verdict.safe_to_auto_execute;
  // Card border colour signals the headline answer at a glance.
  const borderColour = safe
    ? "border-emerald-300"
    : verdict.risk_level === "critical"
      ? "border-red-400"
      : verdict.risk_level === "high"
        ? "border-orange-400"
        : "border-zinc-300";

  return (
    <div className={`border-2 rounded-lg bg-white ${borderColour} overflow-hidden`}>
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-zinc-200 bg-zinc-50">
        <div className="flex items-center gap-3">
          <span className="font-mono text-sm text-zinc-500">
            {verdict.tool_id}
          </span>
          <span className="font-mono text-sm">{verdict.command}</span>
        </div>
        <RiskBadge risk={verdict.risk_level} size="lg" />
      </div>

      {/* Headline verdict */}
      <div className="px-5 py-4 border-b border-zinc-100">
        <div className="flex items-baseline justify-between gap-4">
          <div>
            <div className="text-xs uppercase tracking-wide text-zinc-500">
              Safe to auto-execute
            </div>
            <div className="text-2xl font-semibold mt-1">
              {safe ? (
                <span className="text-emerald-700">Yes</span>
              ) : (
                <span className="text-red-700">No</span>
              )}
            </div>
          </div>
          <div className="text-right">
            <div className="text-xs uppercase tracking-wide text-zinc-500">
              Requires human
            </div>
            <div className="text-2xl font-semibold mt-1">
              {verdict.requires_human_confirmation ? "Yes" : "No"}
            </div>
          </div>
        </div>
      </div>

      {/* Metadata grid */}
      <div className="grid grid-cols-3 gap-px bg-zinc-200 border-b border-zinc-200">
        <Cell label="Match" value={verdict.match_method} />
        <Cell
          label="Verification level"
          value={formatVerificationLevel(verdict.verification_level)}
        />
        <Cell
          label="Confidence"
          value={`${verdict.confidence.toFixed(2)} · ${verdict.confidence_band}`}
        />
      </div>

      {/* Reasons */}
      {verdict.reasons.length > 0 && (
        <div className="px-5 py-4 border-b border-zinc-100">
          <div className="text-xs uppercase tracking-wide text-zinc-500 mb-2">
            Reasons
          </div>
          <ul className="space-y-1 text-sm text-zinc-800">
            {verdict.reasons.map((reason, i) => (
              <li key={i} className="flex gap-2">
                <span className="text-zinc-400">•</span>
                <span>{reason}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Evidence */}
      {verdict.evidence.length > 0 && (
        <div className="px-5 py-4">
          <div className="text-xs uppercase tracking-wide text-zinc-500 mb-2">
            Cited evidence ({verdict.evidence.length})
          </div>
          <ul className="space-y-2 text-sm">
            {verdict.evidence.map((e, i) => (
              <li
                key={i}
                className="flex flex-col gap-0.5 border-l-2 border-zinc-200 pl-3"
              >
                <span className="text-xs text-zinc-500">
                  {e.evidence_type} · trust: {e.trust_level}
                </span>
                <code className="text-xs break-all text-zinc-700">
                  {e.source_uri}
                </code>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function Cell({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-white px-4 py-3">
      <div className="text-xs uppercase tracking-wide text-zinc-500">{label}</div>
      <div className="text-sm font-mono mt-1 text-zinc-900">{value}</div>
    </div>
  );
}
