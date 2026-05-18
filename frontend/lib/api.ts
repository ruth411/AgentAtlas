/**
 * Typed AgentAtlas API client.
 *
 * Server components (RSCs) hit the FastAPI backend directly through
 * `API_BASE` (an absolute URL, set at build/run time from
 * `AGENTATLAS_API_URL`, default `http://localhost:8000`). Client components
 * hit `/api/*`, which `next.config.mjs` rewrites to the same backend at
 * request time. This avoids CORS configuration on the FastAPI side.
 *
 * Types are hand-written to mirror the Pydantic response models in
 * `backend/app/schemas/query.py`. We could codegen from the OpenAPI spec
 * (`/openapi.json`), but the response shapes are stable now and the
 * hand-written types let the dashboard build without depending on the
 * backend being running at install time.
 */

const API_BASE =
  process.env.AGENTATLAS_API_URL ?? "http://localhost:8000";

// -----------------------------------------------------------------------------
// Wire types — mirror backend/app/schemas/query.py + tool_spec.py
// -----------------------------------------------------------------------------

export type RiskLevel = "none" | "low" | "medium" | "high" | "critical";
export type VerificationLevel =
  | "L0_unverified"
  | "L1_schema_valid"
  | "L2_source_verified"
  | "L3_runtime_verified"
  | "L4_cross_agent_verified"
  | "L5_human_audited";
export type ConfidenceBand =
  | "none"
  | "low"
  | "moderate"
  | "high"
  | "strong";
export type TrustLevel = "low" | "medium" | "high";
export type MatchMethod = "exact" | "prefix" | "none";

export interface EvidenceCitation {
  evidence_type: string;
  source_uri: string;
  trust_level: TrustLevel;
}

export interface ValidateCommandResponse {
  tool_id: string;
  command: string;
  matched_claim_id: string | null;
  match_method: MatchMethod;
  safe_to_auto_execute: boolean;
  requires_human_confirmation: boolean;
  risk_level: RiskLevel | null;
  verification_level: VerificationLevel;
  confidence: number;
  confidence_band: ConfidenceBand;
  reasons: string[];
  evidence: EvidenceCitation[];
  verdict_generated_at: string;
}

export interface ToolMatchSummary {
  tool_id: string;
  name: string;
  interfaces: string[];
  capability_count: number;
  verified_command_count: number;
  highest_risk_level: RiskLevel | null;
  verification_level: VerificationLevel;
  match_reason: string;
}

export interface SearchToolsResponse {
  query: string;
  matches: ToolMatchSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface CommandSpec {
  name: string;
  summary: string;
  risk_level: RiskLevel;
  side_effects: string[];
  requires_confirmation: boolean;
}

export interface AuthRequirement {
  required: boolean;
  methods: string[];
}

export interface RiskProfile {
  reads_private_data?: boolean | null;
  mutates_local_state?: boolean | null;
  mutates_remote_state?: boolean | null;
  can_delete_resources?: boolean | null;
  can_incur_cost?: boolean | null;
  exposes_secrets?: boolean | null;
  default_risk_level: RiskLevel;
}

export interface Provenance {
  source_claim_ids: string[];
  source_evidence_ids: string[];
  compiled_at: string;
  compiled_by: string;
  verification_level: VerificationLevel;
}

export interface ToolSpec {
  tool_id: string;
  name: string;
  interfaces: string[];
  capabilities: string[];
  commands: CommandSpec[];
  inputs: string[];
  outputs: string[];
  auth: AuthRequirement;
  side_effects: string[];
  risk_profile: RiskProfile;
  examples: Array<{ title: string; example: string }>;
  workflows: string[];
  failure_modes: Array<{ condition: string; recovery_steps: string[] }>;
  recovery_steps: string[];
  provenance: Provenance;
  verification_level: VerificationLevel;
  publication_issues: unknown[];
  content_hash: string | null;
}

// -----------------------------------------------------------------------------
// Fetch helpers
// -----------------------------------------------------------------------------

type FetchOptions = {
  /** When true, prefix with `/api` (client-side via Next rewrite).
   *  When false, hit the absolute API_BASE (server-side direct). */
  viaProxy: boolean;
};

function urlFor(path: string, opts: FetchOptions): string {
  if (opts.viaProxy) {
    return `/api${path.startsWith("/") ? path : `/${path}`}`;
  }
  const trimmedBase = API_BASE.replace(/\/$/, "");
  return `${trimmedBase}${path.startsWith("/") ? path : `/${path}`}`;
}

async function getJson<T>(path: string, opts: FetchOptions): Promise<T> {
  const response = await fetch(urlFor(path, opts), {
    method: "GET",
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!response.ok) {
    throw new ApiError(response.status, await safeReadError(response));
  }
  return (await response.json()) as T;
}

async function postJson<T>(
  path: string,
  body: unknown,
  opts: FetchOptions,
): Promise<T> {
  const response = await fetch(urlFor(path, opts), {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!response.ok) {
    throw new ApiError(response.status, await safeReadError(response));
  }
  return (await response.json()) as T;
}

async function safeReadError(
  response: Response,
): Promise<{ code?: string; message: string } | string> {
  try {
    const payload = await response.json();
    if (payload?.error?.message) {
      return { code: payload.error.code, message: payload.error.message };
    }
    return JSON.stringify(payload);
  } catch {
    return response.statusText || `HTTP ${response.status}`;
  }
}

export class ApiError extends Error {
  status: number;
  payload: { code?: string; message: string } | string;
  constructor(
    status: number,
    payload: { code?: string; message: string } | string,
  ) {
    super(
      typeof payload === "string" ? payload : payload.message,
    );
    this.status = status;
    this.payload = payload;
  }
}

// -----------------------------------------------------------------------------
// Public API surface
// -----------------------------------------------------------------------------

/** Server-side helpers — call from React Server Components only. */
export const server = {
  searchTools(
    query = "",
    limit = 200,
    offset = 0,
  ): Promise<SearchToolsResponse> {
    const params = new URLSearchParams({
      q: query,
      limit: String(limit),
      offset: String(offset),
    });
    return getJson<SearchToolsResponse>(`/query/search-tools?${params}`, {
      viaProxy: false,
    });
  },
  getToolSpec(toolId: string): Promise<ToolSpec> {
    return getJson<ToolSpec>(
      `/query/tools/${encodeURIComponent(toolId)}`,
      { viaProxy: false },
    );
  },
};

/** Client-side helpers — call from `"use client"` components only. */
export const client = {
  validateCommand(
    toolId: string,
    command: string,
  ): Promise<ValidateCommandResponse> {
    return postJson<ValidateCommandResponse>(
      `/query/validate-command`,
      { tool_id: toolId, command },
      { viaProxy: true },
    );
  },
};

// -----------------------------------------------------------------------------
// Misc helpers
// -----------------------------------------------------------------------------

export function formatVerificationLevel(level: VerificationLevel): string {
  // "L2_source_verified" → "L2 · source verified"
  const [code, ...rest] = level.split("_");
  return `${code} · ${rest.join(" ")}`;
}
