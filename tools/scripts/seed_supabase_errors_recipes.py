"""Direct-insert supabase error + recipe claims."""

from datetime import datetime, timezone
from hashlib import sha256

from app.schemas.claim import KnowledgeClaim
from app.schemas.enums import (
    ClaimType,
    EvidenceType,
    RiskLevel,
    TrustLevel,
)
from app.schemas.evidence import Evidence
from app.services.claim_store import get_claim_store
from app.services.embedding_service import (
    EmbeddingService,
    claim_embedding_text,
    serialize_embedding,
)
from app.services.orchestrator import CanonOrchestrator

BASE = "https://supabase.com/docs"

CLAIMS = [
    # ============================================================
    # supabase-errors — 30 common errors
    # ============================================================
    (
        "supabase-errors", "supabase error: Docker daemon not running (required for local stack)",
        "`supabase start` needs Docker (or Podman) running locally. "
        "Fix: (1) start Docker Desktop (macOS/Windows) or `sudo systemctl start docker` (Linux); "
        "(2) verify: `docker ps` should not error; "
        "(3) for Podman: `supabase start --debug` shows which engine — supports Docker, Colima, Podman; "
        "(4) for Docker Desktop alternatives: OrbStack works well on macOS; "
        "(5) check disk: Docker needs ~5GB free for Supabase images.",
        f"{BASE}/guides/local-development/cli/getting-started",
    ),
    (
        "supabase-errors", "supabase error: supabase: command not found",
        "CLI not installed or not on PATH. "
        "Fix: (1) macOS: `brew install supabase/tap/supabase`; "
        "(2) npm (cross-platform): `npm install -g supabase` (or run via `npx supabase`); "
        "(3) standalone binary: download from github.com/supabase/cli/releases; "
        "(4) verify: `supabase --version`; "
        "(5) for older /local installs that conflict: `which supabase` to see which one is being picked up.",
        f"{BASE}/reference/cli/installing-and-updating",
    ),
    (
        "supabase-errors", "supabase error: bind: address already in use (port 54321 / 54322 / 54323)",
        "Default Supabase ports in use by another process. "
        "Fix: (1) check what's holding: `lsof -i :54321`; "
        "(2) stop other Supabase: `supabase stop --no-backup` then restart; "
        "(3) change ports in `supabase/config.toml`: `[api] port = 54322`, `[db] port = 54323`; "
        "(4) for stale containers: `docker ps -a | grep supabase` then `docker rm -f <id>`; "
        "(5) other defaults: 54321 API, 54322 db, 54323 Studio, 54324 Inbucket (email), 54325 Edge runtime.",
        f"{BASE}/guides/local-development/cli/config",
    ),
    (
        "supabase-errors", "supabase error: failed to start postgres container",
        "Postgres container can't boot — usually disk space, port conflict, or corrupted volume. "
        "Fix: (1) check disk: `df -h`; "
        "(2) stop + remove old volumes: `supabase stop --no-backup` + `docker volume prune -f` (DESTROYS LOCAL DATA); "
        "(3) check Docker logs: `docker logs supabase_db_<project>`; "
        "(4) for stale state: `rm -rf supabase/.branches` (preview branches cache); "
        "(5) try `supabase start --debug` for verbose output.",
        f"{BASE}/reference/cli/supabase-start",
    ),
    (
        "supabase-errors", "supabase error: project ref not found / invalid project reference",
        "`supabase link --project-ref <ref>` failed to resolve. "
        "Fix: (1) project ref is the subdomain of your project URL: `https://<ref>.supabase.co`; "
        "(2) find it: Dashboard → Settings → API → 'Project URL'; "
        "(3) re-link: `supabase link --project-ref abc123xyz` (no `https://`, just the ref); "
        "(4) requires login first: `supabase login`; "
        "(5) check permissions: you need access to the project.",
        f"{BASE}/reference/cli/supabase-link",
    ),
    (
        "supabase-errors", "supabase error: access token expired / authentication required",
        "Login token (in `~/.supabase/access-token`) expired. "
        "Fix: (1) re-login: `supabase login` (opens browser); "
        "(2) for non-interactive (CI): `SUPABASE_ACCESS_TOKEN=sbp_...` env var (generate at Dashboard → Account → Access Tokens); "
        "(3) tokens are scoped — confirm token has needed permissions; "
        "(4) for self-hosted: different auth — direct DB credentials.",
        f"{BASE}/reference/cli/supabase-login",
    ),
    (
        "supabase-errors", "supabase error: migration conflict / migration X already applied",
        "Local migrations don't match what's on remote — usually drift from someone applying SQL directly. "
        "Fix: (1) sync from remote: `supabase db pull` (creates a new migration for any drift); "
        "(2) reset local: `supabase db reset` (replays all migrations locally — DESTROYS local data); "
        "(3) for specific repair: `supabase migration repair --status applied <version>` marks a migration as applied without running; "
        "(4) verify state: `supabase migration list` shows local vs remote.",
        f"{BASE}/reference/cli/supabase-migration-repair",
    ),
    (
        "supabase-errors", "supabase error: invalid migration filename / non-timestamp prefix",
        "Migration files must follow `<timestamp>_<name>.sql` (timestamp = 14 digits YYYYMMDDHHmmSS). "
        "Fix: (1) create properly: `supabase migration new add_users_table` (auto-prefixes); "
        "(2) manual: filename must be `20260601120000_add_users.sql` (or similar timestamp); "
        "(3) timestamps must be unique + chronological; "
        "(4) for renames: edit file then `supabase migration list` to verify it's still recognized.",
        f"{BASE}/reference/cli/supabase-migration-new",
    ),
    (
        "supabase-errors", "supabase error: RLS policy causes infinite recursion / stack overflow",
        "Row Level Security policy references the same table it protects in a way that triggers itself. "
        "Fix: (1) the policy's USING/WITH CHECK can't query the protected table — use SECURITY DEFINER function instead; "
        "(2) for self-join in RLS: create a SECURITY DEFINER function: `CREATE FUNCTION my_user_id() RETURNS uuid SECURITY DEFINER AS $$ SELECT id FROM users WHERE auth_id = auth.uid() $$;` then use in policy: `USING (user_id = my_user_id())`; "
        "(3) common antipattern: `USING (user_id IN (SELECT id FROM users WHERE ...))` on `users` table itself; "
        "(4) inspect policies: `SELECT * FROM pg_policies WHERE tablename='users';`.",
        f"{BASE}/reference/cli/supabase-db-lint",
    ),
    (
        "supabase-errors", "supabase error: JWT verification failed (Edge Functions)",
        "Function rejected the JWT — invalid, expired, or wrong key. "
        "Fix: (1) check Authorization header: `Bearer <jwt>` (not just `<jwt>`); "
        "(2) JWT must be signed with project's JWT secret — get from Dashboard → Settings → API; "
        "(3) for service-role usage: use service_role key (not anon); "
        "(4) for public functions (no auth): mark in invocation: `supabase functions deploy <name> --no-verify-jwt`; "
        "(5) test JWT validity: jwt.io to decode and check expiry.",
        f"{BASE}/reference/cli/supabase-functions-deploy",
    ),
    (
        "supabase-errors", "supabase error: CORS errors in Edge Functions",
        "Browser refused fetch because function didn't return CORS headers. "
        "Fix: (1) handle preflight + add CORS headers: ```ts\\nconst corsHeaders = {\\n  'Access-Control-Allow-Origin': '*',\\n  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',\\n}\\nDeno.serve(async (req) => {\\n  if (req.method === 'OPTIONS') return new Response('ok', { headers: corsHeaders })\\n  // ... your logic\\n  return new Response(JSON.stringify(result), { headers: { ...corsHeaders, 'Content-Type': 'application/json' } })\\n})\\n```; "
        "(2) for production: pin Origin to your domain instead of `*`.",
        f"{BASE}/reference/cli/supabase-functions-deploy",
    ),
    (
        "supabase-errors", "supabase error: function execution timeout (Edge runtime limit)",
        "Free tier: 1.5s wall clock for functions. Pro: ~150s. "
        "Fix: (1) optimize: use database functions / SQL instead of multi-query loops; "
        "(2) batch external API calls; "
        "(3) for long jobs: use a background worker (Vercel Cron, GitHub Actions, or pg_cron extension); "
        "(4) for streaming: return early response, finish work async (but still bounded by wall clock); "
        "(5) check function execution time in Dashboard → Functions logs.",
        f"{BASE}/reference/cli/supabase-functions-serve",
    ),
    (
        "supabase-errors", "supabase error: function deploy fails — import map / module resolution",
        "Edge runtime is Deno — Node-style imports don't work. "
        "Fix: (1) use `import_map.json` if relying on npm modules: ```json\\n{ \"imports\": { \"@supabase/supabase-js\": \"https://esm.sh/@supabase/supabase-js@2\" } }\\n```; "
        "(2) deploy with map: `supabase functions deploy <name> --import-map ./supabase/import_map.json`; "
        "(3) prefer URL imports: `import { createClient } from 'https://esm.sh/@supabase/supabase-js@2'`; "
        "(4) for Node-only packages without Deno equivalent: rewrite or use Vercel/Cloudflare instead.",
        f"{BASE}/reference/cli/supabase-functions-deploy",
    ),
    (
        "supabase-errors", "supabase error: db push fails — local + remote schema diverged",
        "Migrations on remote that aren't in your local `supabase/migrations/`. "
        "Fix: (1) sync first: `supabase db pull` creates a new migration capturing remote-only changes; "
        "(2) review the new migration before committing — may include other devs' changes; "
        "(3) for diverged: list both `supabase migration list` and reconcile by hand; "
        "(4) for safety: `supabase db push --dry-run` shows what would be applied; "
        "(5) tag releases / branches in git to track what was deployed.",
        f"{BASE}/reference/cli/supabase-db-push",
    ),
    (
        "supabase-errors", "supabase error: database password missing / can't connect",
        "Direct DB operations (`db dump`, raw psql) need the DB password. "
        "Fix: (1) find in Dashboard → Settings → Database; "
        "(2) one-off prompt: `supabase db dump --password '<pwd>' -f schema.sql`; "
        "(3) env var: `PGPASSWORD=...` for psql calls; "
        "(4) connection URI for psql: `psql 'postgres://postgres:<pwd>@db.<ref>.supabase.co:5432/postgres?sslmode=require'`; "
        "(5) for pooled: use `aws-0-<region>.pooler.supabase.com:6543` (Supavisor).",
        f"{BASE}/reference/cli/supabase-db-dump",
    ),
    (
        "supabase-errors", "supabase error: row-level security policy blocks query (silent zero rows)",
        "RLS returns empty result for unauthorized queries — not an error, just unexpected. "
        "Fix: (1) check if RLS is enabled: `SELECT relname, relrowsecurity FROM pg_class WHERE relname = 'mytable';`; "
        "(2) list policies: `\\dp mytable` in psql or query `pg_policies`; "
        "(3) test as service role: use service_role key (bypasses RLS) to confirm data exists; "
        "(4) for testing as a user: use the user's JWT and inspect what `auth.uid()` returns; "
        "(5) common: anon key + no SELECT policy = no rows visible (intentional).",
        f"{BASE}/reference/cli/supabase-db-lint",
    ),
    (
        "supabase-errors", "supabase error: anon key vs service role confusion",
        "Two keys: `anon` (public, RLS applies) vs `service_role` (bypasses RLS — never expose in browser). "
        "Fix: (1) browser/frontend: ONLY use anon key; "
        "(2) server-side: use service_role for admin tasks (migrations, bulk ops); "
        "(3) NEVER ship service_role in client bundles — anyone could read all data; "
        "(4) for app-level role distinction: use Postgres roles + RLS policies based on `auth.uid()` / `auth.jwt()->>'role'`.",
        f"{BASE}/reference/cli/supabase-secrets-set",
    ),
    (
        "supabase-errors", "supabase error: env vars not loaded in function",
        "Function can't read env var. "
        "Fix: (1) set with: `supabase secrets set MY_KEY=value` (for cloud); "
        "(2) for local dev: `supabase/.env` file picked up by `supabase functions serve`; "
        "(3) read in Deno: `Deno.env.get('MY_KEY')`; "
        "(4) verify list: `supabase secrets list`; "
        "(5) common: secrets are per-project, not per-function — all functions share the env.",
        f"{BASE}/reference/cli/supabase-secrets-set",
    ),
    (
        "supabase-errors", "supabase error: invalid storage bucket policy / file blocked",
        "Storage RLS-like policy blocking upload/download. "
        "Fix: (1) check bucket: `supabase storage ls`; "
        "(2) public vs private: public buckets allow anon access to known URLs; private requires policy; "
        "(3) policy on `storage.objects` table: `CREATE POLICY ... ON storage.objects FOR SELECT TO authenticated USING (bucket_id = 'avatars' AND auth.uid()::text = (storage.foldername(name))[1]);`; "
        "(4) for testing: temporarily enable public read to confirm path is right; "
        "(5) MIME-type restriction: policy can check `metadata->>'mimetype'`.",
        f"{BASE}/reference/cli/supabase-storage-ls",
    ),
    (
        "supabase-errors", "supabase error: type generation fails (gen types)",
        "`supabase gen types typescript` errored. "
        "Fix: (1) needs login + project link or `--project-id`: `supabase gen types typescript --project-id <ref>`; "
        "(2) for local: `--local` flag uses local Supabase; "
        "(3) save: `supabase gen types typescript --local > types/supabase.ts`; "
        "(4) for schemas other than public: `--schema public --schema auth`; "
        "(5) regenerate after every migration to stay in sync.",
        f"{BASE}/reference/cli/supabase-gen-types",
    ),
    (
        "supabase-errors", "supabase error: failed to apply migration (already applied)",
        "Migration timestamp conflicts with one already on remote. "
        "Fix: (1) `supabase migration list` shows status (local-only, applied, etc.); "
        "(2) mark as applied without running: `supabase migration repair --status applied <version>`; "
        "(3) for unapplied: `supabase migration repair --status reverted <version>` then re-push; "
        "(4) DO NOT manually edit `supabase_migrations.schema_migrations` table — use the repair command.",
        f"{BASE}/reference/cli/supabase-migration-repair",
    ),
    (
        "supabase-errors", "supabase error: too many database connections",
        "Direct connections to Postgres maxed out (default ~60 on Pro). "
        "Fix: (1) use connection pooling — connection string for pooler: `aws-0-<region>.pooler.supabase.com:6543` (transaction mode) or 5432 (session mode); "
        "(2) for serverless / Lambda: USE the pooler — direct connections leak; "
        "(3) check current: `SELECT count(*) FROM pg_stat_activity;`; "
        "(4) kill idle: `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state = 'idle' AND state_change < now() - interval '1 hour';`; "
        "(5) for high-concurrency apps: upgrade compute plan.",
        f"{BASE}/reference/cli/supabase-link",
    ),
    (
        "supabase-errors", "supabase error: project paused (free tier inactivity)",
        "Free-tier projects pause after 7 days of inactivity. "
        "Fix: (1) Dashboard → 'Restore' button; takes a few minutes; "
        "(2) to prevent: ping the project regularly (cron job hitting your API); "
        "(3) for production: upgrade to Pro (no pausing); "
        "(4) data is preserved — you just need to unpause.",
        f"{BASE}/reference/cli/supabase-projects-list",
    ),
    (
        "supabase-errors", "supabase error: realtime not connecting / postgres_changes silently fail",
        "Realtime subscriptions need the replication slot + publication. "
        "Fix: (1) check publication exists: `SELECT * FROM pg_publication WHERE pubname = 'supabase_realtime';`; "
        "(2) add a table to publication: `ALTER PUBLICATION supabase_realtime ADD TABLE my_table;`; "
        "(3) RLS applies to realtime — the receiving user must be able to SELECT the row; "
        "(4) realtime quota: limited concurrent connections per project — Dashboard shows usage; "
        "(5) for debugging: check Dashboard → Logs → Realtime.",
        f"{BASE}/reference/cli/supabase-status",
    ),
    (
        "supabase-errors", "supabase error: branch / preview env quota exceeded",
        "Free tier doesn't include database branches. Pro has limits. "
        "Fix: (1) check current: `supabase branches list`; "
        "(2) delete old preview branches: `supabase branches delete <name>`; "
        "(3) upgrade plan for more branches; "
        "(4) GitHub integration auto-creates preview branches per PR — disable for repos that don't need it.",
        f"{BASE}/reference/cli/supabase-projects-list",
    ),
    (
        "supabase-errors", "supabase error: storage file size limit exceeded",
        "Default upload limit: 50 MB. "
        "Fix: (1) raise in Dashboard → Storage → Settings → 'File size limit'; "
        "(2) for resumable uploads: use TUS protocol via supabase-js: `supabase.storage.from(bucket).upload(path, file, { duplex: 'half' })`; "
        "(3) for very large: split into chunks or use direct S3 (Supabase Storage is S3-compatible); "
        "(4) plan limits: free 1GB total, pro 100GB included + pay-as-you-go beyond.",
        f"{BASE}/reference/cli/supabase-storage-cp",
    ),
    (
        "supabase-errors", "supabase error: pgvector / extension not enabled",
        "Tried to use an extension that isn't enabled. "
        "Fix: (1) enable via Dashboard → Database → Extensions, OR via SQL: `CREATE EXTENSION IF NOT EXISTS pgvector;`; "
        "(2) list available: `SELECT * FROM pg_available_extensions;`; "
        "(3) common Supabase extensions: pgvector (embeddings), pg_cron (scheduled jobs), pg_net (HTTP from SQL), pg_graphql, postgis; "
        "(4) some extensions need approval — check Dashboard for the toggle.",
        f"{BASE}/reference/cli/supabase-db-push",
    ),
    (
        "supabase-errors", "supabase error: link references wrong project",
        "Was linked to project A but commands now target project B. "
        "Fix: (1) `supabase status` shows current link; "
        "(2) re-link to correct: `supabase link --project-ref <correct-ref>`; "
        "(3) unlink (clean): `rm -rf supabase/.temp` and re-link; "
        "(4) verify before destructive ops: `supabase migration list` should show expected state.",
        f"{BASE}/reference/cli/supabase-link",
    ),
    (
        "supabase-errors", "supabase error: db reset destroys local data (no confirmation in CI)",
        "`supabase db reset` wipes local DB without prompt in non-interactive mode. "
        "Fix: (1) NEVER run in CI against linked cloud project; "
        "(2) always confirm intent: `--debug` to see what'd happen; "
        "(3) backup first: `supabase db dump -f backup.sql`; "
        "(4) for safer 'wipe and restart' workflow: `supabase stop --no-backup` (keeps schema) vs `supabase stop` (clean).",
        f"{BASE}/reference/cli/supabase-db-reset",
    ),

    # ============================================================
    # supabase-recipes — 36 common workflows
    # ============================================================
    (
        "supabase-recipes", "supabase recipe: install + initialize a project",
        "Install: `brew install supabase/tap/supabase` (macOS) or `npm install -g supabase`. "
        "Verify: `supabase --version`. "
        "Init in existing project: `supabase init` creates `supabase/` folder with `config.toml`, `migrations/`, `functions/`. "
        "First-time login: `supabase login` (opens browser, saves token to `~/.supabase`). "
        "Link to cloud project: `supabase link --project-ref <ref>`.",
        f"{BASE}/reference/cli/supabase-init",
    ),
    (
        "supabase-recipes", "supabase recipe: run the local stack",
        "`supabase start` boots Docker containers: Postgres, GoTrue (auth), PostgREST (API), Realtime, Storage, Studio. "
        "First run pulls images (~5GB). "
        "Output shows URLs: API (54321), Studio (54323), Inbucket (54324 — local email). "
        "Stop: `supabase stop` (keeps data) or `supabase stop --no-backup` (wipes). "
        "Status: `supabase status` shows running URLs + keys.",
        f"{BASE}/reference/cli/supabase-start",
    ),
    (
        "supabase-recipes", "supabase recipe: create a new migration",
        "`supabase migration new add_users_table` creates `supabase/migrations/<timestamp>_add_users_table.sql`. "
        "Edit the file with raw SQL: `CREATE TABLE users (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), email text NOT NULL UNIQUE);`. "
        "Apply locally: `supabase db reset` (wipes + replays all migrations). "
        "Or apply incrementally: `supabase migration up`. "
        "Push to cloud: `supabase db push`.",
        f"{BASE}/reference/cli/supabase-migration-new",
    ),
    (
        "supabase-recipes", "supabase recipe: diff to generate a migration from schema changes",
        "Make schema changes in Studio (or psql against local DB). "
        "Generate migration: `supabase db diff -f add_users` writes the SQL to `supabase/migrations/<timestamp>_add_users.sql`. "
        "Review the generated SQL — it captures only the deltas. "
        "Apply: `supabase db push` to deploy. "
        "Alternative: schema-only diff against another DB: `supabase db diff --schema public,auth`.",
        f"{BASE}/reference/cli/supabase-db-diff",
    ),
    (
        "supabase-recipes", "supabase recipe: pull remote schema to local",
        "`supabase db pull` queries remote schema + generates a migration capturing any drift. "
        "Use when: cloud has been edited (by another dev or directly in Studio). "
        "Output: a new migration file with the differences. "
        "Review before committing — may include unexpected changes. "
        "After pull: `supabase db reset` to replay locally.",
        f"{BASE}/reference/cli/supabase-db-pull",
    ),
    (
        "supabase-recipes", "supabase recipe: push migrations to cloud",
        "`supabase db push` applies pending migrations to the linked cloud project. "
        "Dry run first: `supabase db push --dry-run`. "
        "For branch / preview env: `supabase db push --linked` (current branch). "
        "After push: `supabase migration list` shows applied state. "
        "For CI: pin the CLI version + use `--debug` for verbose logging.",
        f"{BASE}/reference/cli/supabase-db-push",
    ),
    (
        "supabase-recipes", "supabase recipe: generate TypeScript types",
        "From cloud: `supabase gen types typescript --project-id <ref> > types/supabase.ts`. "
        "From local: `supabase gen types typescript --local > types/supabase.ts`. "
        "Specific schemas: `--schema public --schema auth`. "
        "Use in code: ```ts\\nimport { createClient } from '@supabase/supabase-js'\\nimport { Database } from './types/supabase'\\nconst supabase = createClient<Database>(url, key)\\n// Now type-safe queries:\\nconst { data } = await supabase.from('users').select('id,email')\\n```. "
        "Regenerate after each schema change.",
        f"{BASE}/reference/cli/supabase-gen-types",
    ),
    (
        "supabase-recipes", "supabase recipe: write + serve an Edge Function locally",
        "Create: `supabase functions new hello`. "
        "Edit `supabase/functions/hello/index.ts`: ```ts\\nDeno.serve(async (req) => {\\n  const { name } = await req.json()\\n  return new Response(JSON.stringify({ message: `Hello ${name}!` }), { headers: { 'Content-Type': 'application/json' } })\\n})\\n```. "
        "Serve locally: `supabase functions serve hello`. "
        "Test: `curl -X POST http://localhost:54321/functions/v1/hello -H 'Authorization: Bearer <anon-key>' -d '{\"name\":\"Alice\"}'`.",
        f"{BASE}/reference/cli/supabase-functions-serve",
    ),
    (
        "supabase-recipes", "supabase recipe: deploy an Edge Function to cloud",
        "Deploy: `supabase functions deploy hello`. "
        "Skip JWT verification (public function): `--no-verify-jwt`. "
        "With import map: `--import-map ./supabase/import_map.json`. "
        "Region (where it runs): inherited from project. "
        "After: function URL is `https://<ref>.supabase.co/functions/v1/hello`. "
        "List: `supabase functions list`. "
        "Delete: `supabase functions delete hello`.",
        f"{BASE}/reference/cli/supabase-functions-deploy",
    ),
    (
        "supabase-recipes", "supabase recipe: set + use function secrets",
        "Set: `supabase secrets set STRIPE_KEY=sk_live_...`. "
        "From .env file: `supabase secrets set --env-file .env.production`. "
        "List: `supabase secrets list`. "
        "Unset: `supabase secrets unset STRIPE_KEY`. "
        "Read in function: `const key = Deno.env.get('STRIPE_KEY')`. "
        "Secrets are project-wide (all functions share them) — namespace with prefixes if needed.",
        f"{BASE}/reference/cli/supabase-secrets-set",
    ),
    (
        "supabase-recipes", "supabase recipe: set up Row Level Security (RLS)",
        "Enable on table: `ALTER TABLE posts ENABLE ROW LEVEL SECURITY;`. "
        "Select policy (users see their own posts): `CREATE POLICY \"users see own posts\" ON posts FOR SELECT TO authenticated USING (auth.uid() = user_id);`. "
        "Insert: `CREATE POLICY \"users insert own posts\" ON posts FOR INSERT TO authenticated WITH CHECK (auth.uid() = user_id);`. "
        "Service role bypasses RLS. "
        "Test as a user: get their JWT, set in PostgREST request, query.",
        f"{BASE}/guides/local-development/cli/getting-started",
    ),
    (
        "supabase-recipes", "supabase recipe: connect with psql to the cloud database",
        "Direct connection: `psql 'postgres://postgres:<password>@db.<ref>.supabase.co:5432/postgres?sslmode=require'`. "
        "Via pooler (transaction mode, best for serverless): `aws-0-<region>.pooler.supabase.com:6543`. "
        "Via pooler (session mode, for tools that need stickyness): `aws-0-<region>.pooler.supabase.com:5432`. "
        "Get URLs in Dashboard → Settings → Database → Connection string. "
        "For tools: pgAdmin, DataGrip, DBeaver all work.",
        f"{BASE}/reference/cli/supabase-link",
    ),
    (
        "supabase-recipes", "supabase recipe: backup the database",
        "Schema + data: `supabase db dump -f backup.sql`. "
        "Schema only: `--schema-only`. "
        "Data only: `--data-only`. "
        "Specific schemas: `--schema public --schema auth`. "
        "Restore: `psql 'postgres://postgres:<pwd>@db...' -f backup.sql`. "
        "For production: combine with cron + S3 upload + retention policy. "
        "Dashboard also provides automatic daily backups (Pro plan: 7-day retention; Team: 14-day; Enterprise: longer).",
        f"{BASE}/reference/cli/supabase-db-dump",
    ),
    (
        "supabase-recipes", "supabase recipe: upload file to storage",
        "From CLI: `supabase storage cp ./photo.jpg ss:///avatars/me.jpg --linked`. "
        "From SDK (JS): ```ts\\nconst { data, error } = await supabase.storage\\n  .from('avatars')\\n  .upload('user-123/avatar.png', file, { cacheControl: '3600', upsert: true })\\n```. "
        "Public bucket URL: `https://<ref>.supabase.co/storage/v1/object/public/<bucket>/<path>`. "
        "Signed URL for private (1 hour expiry): `supabase.storage.from('avatars').createSignedUrl('path', 3600)`.",
        f"{BASE}/reference/cli/supabase-storage-cp",
    ),
    (
        "supabase-recipes", "supabase recipe: enable Postgres extensions",
        "List available: `SELECT * FROM pg_available_extensions WHERE name IN ('pgvector', 'pg_cron', 'pg_net', 'postgis', 'pg_graphql');`. "
        "Enable: `CREATE EXTENSION IF NOT EXISTS pgvector;`. "
        "Via Dashboard: Database → Extensions → toggle. "
        "Add to migrations: `CREATE EXTENSION IF NOT EXISTS pgvector WITH SCHEMA extensions;`. "
        "Common: pgvector (AI embeddings), pg_cron (scheduled jobs), pg_net (HTTP from SQL), pg_graphql, postgis.",
        f"{BASE}/reference/cli/supabase-db-push",
    ),
    (
        "supabase-recipes", "supabase recipe: vector search with pgvector",
        "Enable: `CREATE EXTENSION pgvector;`. "
        "Table: `CREATE TABLE docs (id bigserial PRIMARY KEY, content text, embedding vector(1536));`. "
        "Index for fast cosine: `CREATE INDEX ON docs USING hnsw (embedding vector_cosine_ops);`. "
        "Insert: `INSERT INTO docs (content, embedding) VALUES ('text', '[0.1, 0.2, ...]');`. "
        "Search: `SELECT *, 1 - (embedding <=> '[0.1, ...]'::vector) AS similarity FROM docs ORDER BY embedding <=> '[0.1, ...]'::vector LIMIT 10;`. "
        "Use with OpenAI embeddings via Edge Function for RAG.",
        f"{BASE}/reference/cli/supabase-db-push",
    ),
    (
        "supabase-recipes", "supabase recipe: scheduled jobs with pg_cron",
        "Enable: `CREATE EXTENSION pg_cron;`. "
        "Schedule: `SELECT cron.schedule('cleanup-old-logs', '0 3 * * *', 'DELETE FROM logs WHERE created_at < now() - interval ''30 days'';');`. "
        "List jobs: `SELECT * FROM cron.job;`. "
        "Unschedule: `SELECT cron.unschedule('cleanup-old-logs');`. "
        "Run history: `SELECT * FROM cron.job_run_details ORDER BY start_time DESC;`. "
        "Use case: nightly cleanup, daily summaries, periodic stats refresh.",
        f"{BASE}/reference/cli/supabase-db-push",
    ),
    (
        "supabase-recipes", "supabase recipe: realtime subscriptions",
        "From JS SDK: ```ts\\nconst channel = supabase\\n  .channel('orders')\\n  .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'orders' },\\n    (payload) => console.log('new order!', payload.new))\\n  .subscribe()\\n```. "
        "Enable for a table: `ALTER PUBLICATION supabase_realtime ADD TABLE orders;`. "
        "RLS applies — user only sees rows they could SELECT. "
        "Filter events: `{ event: 'INSERT', schema: 'public', table: 'orders', filter: 'user_id=eq.123' }`. "
        "Unsubscribe: `channel.unsubscribe()`.",
        f"{BASE}/reference/cli/supabase-start",
    ),
    (
        "supabase-recipes", "supabase recipe: auth — email/password login (JS SDK)",
        "Sign up: `await supabase.auth.signUp({ email, password })`. "
        "Sign in: `await supabase.auth.signInWithPassword({ email, password })`. "
        "Get session: `await supabase.auth.getSession()`. "
        "Listen to changes: `supabase.auth.onAuthStateChange((event, session) => { ... })`. "
        "Sign out: `await supabase.auth.signOut()`. "
        "For tokens to refresh automatically: persistent session enabled by default.",
        f"{BASE}/reference/cli/supabase-status",
    ),
    (
        "supabase-recipes", "supabase recipe: auth — OAuth provider (Google, GitHub, etc.)",
        "(1) Configure in Dashboard → Authentication → Providers → enable Google/GitHub; "
        "(2) Set redirect URL: `https://<ref>.supabase.co/auth/v1/callback`; "
        "(3) JS sign-in: `await supabase.auth.signInWithOAuth({ provider: 'google', options: { redirectTo: 'https://myapp.com/callback' } })`; "
        "(4) Handle callback in your `/callback` route — supabase-js extracts the session; "
        "(5) For self-hosted: configure provider credentials via env vars (GOTRUE_EXTERNAL_GOOGLE_CLIENT_ID etc).",
        f"{BASE}/reference/cli/supabase-link",
    ),
    (
        "supabase-recipes", "supabase recipe: CI/CD pipeline for migrations",
        "GitHub Actions example: ```yaml\\nname: Deploy migrations\\non:\\n  push:\\n    branches: [main]\\njobs:\\n  deploy:\\n    runs-on: ubuntu-latest\\n    steps:\\n      - uses: actions/checkout@v4\\n      - uses: supabase/setup-cli@v1\\n        with: { version: latest }\\n      - run: supabase db push\\n        env:\\n          SUPABASE_ACCESS_TOKEN: ${{ secrets.SUPABASE_ACCESS_TOKEN }}\\n          SUPABASE_DB_PASSWORD: ${{ secrets.SUPABASE_DB_PASSWORD }}\\n          SUPABASE_PROJECT_ID: <ref>\\n```. "
        "For previews: deploy on PR open, destroy on close.",
        f"{BASE}/reference/cli/supabase-db-push",
    ),
    (
        "supabase-recipes", "supabase recipe: monitor function logs",
        "Dashboard → Functions → click function → Logs. "
        "Live tail: `supabase functions list` then `supabase functions inspect <name>` (where supported). "
        "From SQL: `SELECT * FROM edge_logs ORDER BY timestamp DESC LIMIT 100;` (Logflare backend). "
        "Filter by status: `WHERE status >= 400`. "
        "For local: `supabase functions serve` logs to stdout.",
        f"{BASE}/reference/cli/supabase-functions-deploy",
    ),
    (
        "supabase-recipes", "supabase recipe: use Studio for ad-hoc queries",
        "Local: `supabase start` then open http://localhost:54323. "
        "Cloud: Dashboard → SQL Editor. "
        "Features: schema browser, table editor, SQL editor with autocomplete, saved queries. "
        "Trade-offs: bypasses migration workflow — changes won't be tracked. "
        "For permanent changes: use Studio to prototype, then `supabase db diff` to capture as migration.",
        f"{BASE}/reference/cli/supabase-status",
    ),
    (
        "supabase-recipes", "supabase recipe: branch / preview environments",
        "Pro plan feature. Enable in Dashboard → Project Settings → Branching. "
        "Create branch: `supabase branches create my-feature`. "
        "Switch to branch: `supabase branches switch my-feature`. "
        "Each branch is a separate Postgres database. "
        "GitHub integration: auto-creates branch per PR with full database snapshot. "
        "Delete: `supabase branches delete my-feature`.",
        f"{BASE}/reference/cli/supabase-projects-list",
    ),
    (
        "supabase-recipes", "supabase recipe: config.toml common settings",
        "```toml\\nproject_id = \"my-project\"\\n\\n[api]\\nenabled = true\\nport = 54321\\nschemas = [\"public\", \"storage\"]\\n\\n[db]\\nport = 54322\\nmajor_version = 15\\n\\n[auth]\\nenabled = true\\nsite_url = \"http://localhost:3000\"\\njwt_expiry = 3600\\nenable_signup = true\\n\\n[functions.hello]\\nverify_jwt = false\\nimport_map = \"./functions/hello/import_map.json\"\\n```. "
        "Local-only settings — push to cloud requires Dashboard.",
        f"{BASE}/guides/local-development/cli/config",
    ),
    (
        "supabase-recipes", "supabase recipe: connection pooling for serverless",
        "Use the pooler for stateless functions to avoid connection exhaustion. "
        "Connection string: `postgres://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres?pgbouncer=true`. "
        "Transaction mode (port 6543): each transaction grabs a connection, releases on commit — best for short queries. "
        "Session mode (port 5432): connection sticky per session — needed for advisory locks, prepared statements. "
        "From Edge Functions: use the pooler URL.",
        f"{BASE}/reference/cli/supabase-link",
    ),
    (
        "supabase-recipes", "supabase recipe: storage image transformations on the fly",
        "Built-in transforms: ?width=300&height=200&resize=cover&quality=80. "
        "Public URL: `https://<ref>.supabase.co/storage/v1/render/image/public/<bucket>/<path>?width=300`. "
        "From SDK: `supabase.storage.from('photos').getPublicUrl('cat.jpg', { transform: { width: 300 } })`. "
        "Cached at the edge. "
        "Pro plan: webp/avif format conversion. "
        "Use case: responsive images without separate thumbnail pipeline.",
        f"{BASE}/reference/cli/supabase-storage-cp",
    ),
    (
        "supabase-recipes", "supabase recipe: webhooks from database events",
        "Dashboard → Database → Webhooks. "
        "Create: trigger on INSERT/UPDATE/DELETE on a table, sends HTTP POST to your URL with the row payload. "
        "Authentication: include headers (Bearer token). "
        "Retry on failure: built-in. "
        "Alternative: use `pg_net` extension + trigger to fire HTTP from SQL: ```sql\\nCREATE TRIGGER on_user_create AFTER INSERT ON users FOR EACH ROW EXECUTE FUNCTION ...\\n```.",
        f"{BASE}/reference/cli/supabase-functions-deploy",
    ),
    (
        "supabase-recipes", "supabase recipe: send transactional email via Resend / SMTP",
        "Configure SMTP in Dashboard → Authentication → SMTP Settings: host, port, user, pass, sender. "
        "For Resend integration: Resend API key as SMTP password, host `smtp.resend.com`. "
        "Customize templates: Dashboard → Authentication → Email Templates (confirm sign-up, recover, magic link, change email, invite). "
        "For non-auth emails: use Resend SDK from Edge Function.",
        f"{BASE}/reference/cli/supabase-functions-deploy",
    ),
    (
        "supabase-recipes", "supabase recipe: production deployment checklist",
        "(1) Pin CLI version in CI: `supabase --version` matches dev env. "
        "(2) Migrations in git, applied via `supabase db push` in CI on main. "
        "(3) Backup automation: cron `supabase db dump` to S3 + retention. "
        "(4) Monitoring: Dashboard → Reports + integrate Sentry/Logflare. "
        "(5) Auth callbacks: ensure production redirect URLs are listed. "
        "(6) RLS: enable on every table before going live. "
        "(7) Service role key: only in server-side secrets, NEVER in client bundles.",
        f"{BASE}/reference/cli/supabase-db-push",
    ),
    (
        "supabase-recipes", "supabase recipe: import data from CSV",
        "Via Studio: Table editor → Import Data from CSV (small files). "
        "Via psql + COPY (large files): `psql 'postgres://...' -c \"\\copy users (name, email) FROM 'users.csv' CSV HEADER\"`. "
        "From storage bucket via Edge Function: read file with Deno, parse, insert in batches. "
        "For huge migrations from another DB: use pgloader or pg_dump | psql.",
        f"{BASE}/reference/cli/supabase-db-push",
    ),
    (
        "supabase-recipes", "supabase recipe: migrate from Firebase",
        "(1) Export Firestore: Firebase console → Cloud Firestore → Export to GCS bucket; "
        "(2) Transform Firestore docs → relational rows (JSON → tables); "
        "(3) Import via psql or Edge Function; "
        "(4) Map Firebase Auth users: export users CSV → import via `auth.users` admin API; "
        "(5) Realtime: replace Firestore `onSnapshot` with Supabase `postgres_changes` subscriptions. "
        "Tools: Firebase-to-Supabase migration scripts on GitHub (community).",
        f"{BASE}/reference/cli/supabase-init",
    ),
    (
        "supabase-recipes", "supabase recipe: local CLI quickstart end-to-end",
        "```bash\\n# 1. New project\\nmkdir my-app && cd my-app\\nsupabase init\\n# 2. Start local\\nsupabase start\\n# 3. Open Studio: http://localhost:54323\\n# 4. Create migration\\nsupabase migration new initial_schema\\n# Edit supabase/migrations/<ts>_initial_schema.sql\\n# 5. Apply locally\\nsupabase db reset\\n# 6. Link to cloud (later)\\nsupabase link --project-ref <ref>\\nsupabase db push\\n# 7. Generate types\\nsupabase gen types typescript --local > types/db.ts\\n```",
        f"{BASE}/guides/local-development",
    ),
    (
        "supabase-recipes", "supabase recipe: use Supabase JS client with types",
        "```ts\\nimport { createClient } from '@supabase/supabase-js'\\nimport type { Database } from './types/supabase'\\n\\nconst supabase = createClient<Database>(\\n  import.meta.env.VITE_SUPABASE_URL!,\\n  import.meta.env.VITE_SUPABASE_ANON_KEY!,\\n)\\n\\n// Type-safe query\\nconst { data, error } = await supabase\\n  .from('users')\\n  .select('id, email, posts(title)')\\n  .eq('id', userId)\\n  .single()\\n\\nif (error) throw error\\nconsole.log(data.email, data.posts[0]?.title)\\n```",
        f"{BASE}/reference/cli/supabase-gen-types",
    ),
    (
        "supabase-recipes", "supabase recipe: test migrations locally before push",
        "(1) Make changes via Studio or write migration; "
        "(2) Reset local: `supabase db reset` (full replay from scratch); "
        "(3) Re-run app's test suite against local; "
        "(4) Diff against linked remote: `supabase db diff --linked` shows what would change; "
        "(5) Dry-run push: `supabase db push --dry-run`; "
        "(6) For brave: apply: `supabase db push`. "
        "Always test in staging branch first if available.",
        f"{BASE}/reference/cli/supabase-db-push",
    ),
]


def main() -> None:
    store = get_claim_store()
    orchestrator = CanonOrchestrator(store)
    svc = EmbeddingService.get_default()
    now = datetime.now(timezone.utc)
    ok = 0
    failed = 0
    for i, (tool_id, subject, statement, evidence_url) in enumerate(CLAIMS):
        suffix = f"{i:03d}"
        body_for_hash = f"{subject}\n{statement}"
        evidence = Evidence(
            evidence_id=f"ev-sup-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-sup-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="supabase-catalog",
            evidence=[evidence],
            risk_level=RiskLevel.LOW,
            created_at=now,
        )
        try:
            store.create(claim)
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ #{suffix} create: {type(exc).__name__}: {exc}")
            failed += 1
            continue
        try:
            verification = orchestrator.verify_claim(claim)
            store.save_verification_result(verification)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠ #{suffix} verify: {type(exc).__name__}: {exc}")
        text = claim_embedding_text(subject=subject, statement=statement)
        vec = svc.embed_one(text)
        store.set_embedding(claim.claim_id, serialize_embedding(vec))
        ok += 1
        print(f"  ✓ #{suffix} [{tool_id}] {subject[:70]}")
    print(f"\nDone: {ok} created / {failed} failed")


if __name__ == "__main__":
    main()
