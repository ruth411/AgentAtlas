# Security Policy

Ayiru exists to help AI agents act more safely. Its own security posture is therefore taken seriously.

## Supported versions

| Version | Supported |
|---|---|
| `0.1.x` | ✅ |

## Reporting a vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Instead, email **security@ayiru.dev** (or the maintainer listed in the repo's `pyproject.toml`) with:

1. A description of the issue and the threat model it breaks.
2. Steps to reproduce — minimal repro is appreciated.
3. The affected version and install path (repo checkout vs. Docker vs. PyPI).
4. Any suggested fix or mitigation.

You will receive an acknowledgement within **72 hours** and a status update within **7 days**. We aim to publish a fix and a coordinated disclosure within **30 days** of the report, sooner for critical issues.

## What we treat as a vulnerability

- **SSRF / network-policy bypass.** Any way to make an ingestion lane fetch a URL the contract should refuse (internal IPs, file://, redirects past the allowlist).
- **Subprocess / argv injection.** Any path that lets an attacker influence the argv of a CLI ingestion run beyond the contract's allowlist.
- **Evidence trust elevation.** Any way to get a claim accepted at L2+ without satisfying the evidence-trust policy.
- **Safety policy bypass.** Any input that produces `safe_to_auto_execute=True` for a command the deterministic risk engine would classify as `high` or `critical`.
- **Audit log tampering.** Any path that mutates an existing `AuditEvent` row through public service surface (or deletes one).
- **Auth bypass.** When the API-key auth is enabled, any path that reaches a protected endpoint (write routes or audit-log reads) without a valid Bearer token.
- **Standard web vulns** in the FastAPI surface: SQL injection, XSS through reflected responses, CSRF on state-changing routes (when auth is added), denial-of-service through unbounded payloads.

## What we do *not* treat as a vulnerability

- Hallucinated content from a *consumer* LLM acting on Ayiru verdicts. Ayiru serves structured, cited verdicts; what the consumer agent does with them is its problem.
- Unverified-but-claimed-true output. The system surfaces `verification_level=L0_unverified` for unverified claims by design.
- Demo data being out of date. Seed artifacts are pre-captured; rerun the live ingestion lanes for fresh data.
- Anything that requires `AYIRU_DATABASE_URL` pointing at an attacker-controlled database. The system trusts its own database.

## Defense-in-depth measures already in place

- HTTPS-only fetches with per-lane contract-driven allowlists. Documented in `docs/trust_contract.md`.
- SSRF guard rejects private IPv4 / IPv6 ranges, link-local, loopback, multicast, and reserved blocks.
- Outbound HTTPS fetches pin the TCP connect step to the exact public IP already vetted by the SSRF guard, closing the DNS-rebinding gap between validation and connect.
- All redirects are re-checked against the SSRF guard (Stage 8 audit fix).
- Subprocess sandbox uses argv allowlist + content filter; no shell.
- MCP server stderr is routed to `DEVNULL` to prevent pipe-fill deadlocks (Stage 7d audit fix).
- 1 MiB request body limit; oversized bodies surface as structured `REQUEST_BODY_TOO_LARGE`.
- Append-only audit log; no service path mutates an existing event (Stage 13 + introspection test).
- API-key auth (`AYIRU_API_KEY`) gates all state-changing HTTP requests plus audit-log reads with a timing-safe `hmac.compare_digest` check; query / lookup reads stay public.
- Optional trusted-host enforcement (`AYIRU_TRUSTED_HOSTS`) rejects inbound requests whose `Host` header is not on the operator-provided allowlist.
- MCP stdio can be gated independently with `AYIRU_MCP_SHARED_SECRET`, which requires `initialize.params.ayiru_shared_secret` before any other MCP method is allowed.
- Optional per-client ask() rate limiting (`AYIRU_ASK_RATE_LIMIT_REQUESTS`) gives operators a built-in abuse brake on the public query surface without closing it entirely.
- Baseline response security headers (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`) are stamped on every HTTP response.
- `ayiru serve` auto-migrates before boot and exits non-zero on migration failure, so operators do not silently run against a stale schema.
- The Docker image defaults `AYIRU_STRICT_TOOL_LOCK=1`, so network-exposed container deployments reject unknown `tool_id`s unless the operator explicitly overrides the env var.

## Known residual risks (acknowledged, scheduled, not yet mitigated)

### MCP stdio is open unless `AYIRU_MCP_SHARED_SECRET` is set

`ayiru mcp` speaks JSON-RPC over stdin/stdout, so the HTTP `Authorization`
header model does not apply. When `AYIRU_MCP_SHARED_SECRET` is unset, the
assumption remains that any caller already has local exec rights on the
process (Claude Desktop / Cursor / Cline configurations spawn the server as a
subprocess they own).

Implication: if you expose `ayiru mcp` to a remote caller (for example by
piping it across SSH), set `AYIRU_MCP_SHARED_SECRET` and require the caller
to send `initialize.params.ayiru_shared_secret` before any other MCP method
is allowed. For network-exposed deployments the HTTP API with `AYIRU_API_KEY`
is still the stronger default surface.

## Coordinated disclosure timeline

We follow the 90-day disclosure window. If we have not shipped a fix and coordinated a public advisory within 90 days of your report, you are free to disclose. We will work with you to extend if more time is genuinely needed.

Thank you for helping keep Ayiru safe.
