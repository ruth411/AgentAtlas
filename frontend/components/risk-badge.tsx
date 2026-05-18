import type { RiskLevel } from "@/lib/api";

const RISK_STYLES: Record<string, string> = {
  none: "bg-slate-100 text-slate-700 ring-slate-300",
  low: "bg-emerald-50 text-emerald-800 ring-emerald-300",
  medium: "bg-amber-50 text-amber-800 ring-amber-300",
  high: "bg-orange-50 text-orange-800 ring-orange-400",
  critical: "bg-red-50 text-red-800 ring-red-400",
  unknown: "bg-zinc-100 text-zinc-500 ring-zinc-300",
};

const RISK_LABELS: Record<string, string> = {
  none: "none",
  low: "low",
  medium: "medium",
  high: "high",
  critical: "critical",
  unknown: "unknown",
};

export function RiskBadge({
  risk,
  size = "sm",
}: {
  risk: RiskLevel | null | "unknown";
  size?: "sm" | "lg";
}) {
  const key = risk ?? "unknown";
  const sizeClasses =
    size === "lg"
      ? "px-3 py-1 text-sm"
      : "px-2 py-0.5 text-xs";
  return (
    <span
      className={`inline-flex items-center font-medium ring-1 ring-inset rounded ${RISK_STYLES[key]} ${sizeClasses} uppercase tracking-wide`}
    >
      {RISK_LABELS[key]}
    </span>
  );
}
