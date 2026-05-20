"""Stage 14: bundled trust contracts shipped inside the wheel.

The repo-root `contracts/` directory remains the canonical, versioned
source of truth — these files are a build-time copy synced before each
release. Loaders use `app.services.contract_paths.contract_path(name)`
to resolve a contract by name; that helper prefers the source-tree copy
when running from a checkout, and falls back to this package directory
when installed from the wheel.

DO NOT edit files in this directory by hand. Edit `contracts/*.json` at
the repo root and re-run `make sync-contracts` (or copy manually) so
both copies stay byte-identical. A test asserts they match before every
release.
"""
