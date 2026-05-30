"""Direct-insert gh (GitHub CLI) error + recipe claims."""

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

BASE = "https://cli.github.com/manual"

CLAIMS = [
    # ============================================================
    # gh-errors — 30 common errors
    # ============================================================
    (
        "gh-errors", "gh error: command not found / gh not installed",
        "The gh CLI isn't installed or isn't on PATH. "
        "Fix: (1) macOS: `brew install gh`; "
        "(2) Linux apt: see https://cli.github.com/manual/installation for the official apt repo (don't use distro package — usually outdated); "
        "(3) Windows: `winget install --id GitHub.cli` or `scoop install gh`; "
        "(4) verify: `gh --version`; "
        "(5) check PATH: `which gh` (should print install path).",
        f"{BASE}/gh",
    ),
    (
        "gh-errors", "gh error: You are not logged into any GitHub hosts / authentication required",
        "Most gh commands need auth. "
        "Fix: (1) `gh auth login` — interactive: pick GitHub.com vs Enterprise, HTTPS vs SSH, login with browser or token; "
        "(2) check state: `gh auth status`; "
        "(3) non-interactive (CI): `echo $GH_TOKEN | gh auth login --with-token`; "
        "(4) explicit token via env var: `export GH_TOKEN=ghp_...` (works without `auth login`).",
        f"{BASE}/gh_auth_login",
    ),
    (
        "gh-errors", "gh error: could not determine HOST / no current repository / specify with -R",
        "gh needs to know which repo. Not in a git repo, or no `origin` remote set. "
        "Fix: (1) `cd` into a repo first; "
        "(2) explicit: `gh pr list -R owner/repo` (the `-R` flag works on every command); "
        "(3) set default: `gh repo set-default owner/repo`; "
        "(4) check what gh sees: `gh repo view` (errors if it can't tell).",
        f"{BASE}/gh",
    ),
    (
        "gh-errors", "gh error: HTTP 401 / Bad credentials / token expired",
        "Your token is invalid or expired. "
        "Fix: (1) re-auth: `gh auth login` (or `gh auth refresh`); "
        "(2) if you set GH_TOKEN env var, that overrides `gh auth login` — `unset GH_TOKEN` to use stored creds; "
        "(3) check token via `gh auth token` (then verify it on https://github.com/settings/tokens); "
        "(4) PAT tokens have explicit expiry — fine-grained tokens default to 30 days.",
        f"{BASE}/gh_auth_status",
    ),
    (
        "gh-errors", "gh error: HTTP 403 / Resource not accessible by integration",
        "Your token doesn't have the scope for this operation. Common when GH_TOKEN is a workflow's GITHUB_TOKEN with limited perms. "
        "Fix: (1) add scope: `gh auth refresh -s repo,workflow,read:org`; "
        "(2) in Actions, grant the workflow more perms via `permissions:` block in yml; "
        "(3) for fine-grained PAT: edit on GitHub, add resource access; "
        "(4) `gh auth status` shows current scopes.",
        f"{BASE}/gh_auth_refresh",
    ),
    (
        "gh-errors", "gh error: HTTP 403 / API rate limit exceeded",
        "Unauthenticated: 60 req/hr per IP. Authenticated: 5000/hr (15000 for Enterprise). "
        "Fix: (1) make sure you're authed — unauth users blow through 60 fast; "
        "(2) check remaining: `gh api rate_limit`; "
        "(3) wait until reset (response header `X-RateLimit-Reset`); "
        "(4) for high-volume: use a GitHub App or split across multiple PATs; "
        "(5) some endpoints (search) have their own lower limits — 30/min.",
        f"{BASE}/gh_api",
    ),
    (
        "gh-errors", "gh error: HTTP 404 / Could not resolve to a Repository",
        "Repo doesn't exist, OR your token doesn't have access. (404 vs 403 is intentional — hides private repo existence.) "
        "Fix: (1) check spelling: `owner/repo` exactly; "
        "(2) verify access in browser: `https://github.com/owner/repo`; "
        "(3) for private: ensure your token has `repo` scope; "
        "(4) for SAML-protected org: authorize the token at the org's SSO page.",
        f"{BASE}/gh_repo_view",
    ),
    (
        "gh-errors", "gh error: pull request create failed: no commits between base and head",
        "The branch you're creating a PR from has no new commits compared to the base. "
        "Fix: (1) commit something: `git commit -m '...'`; "
        "(2) push first: `git push -u origin <branch>`; "
        "(3) check base branch is right: `gh pr create --base main --head <branch>`; "
        "(4) for cross-fork: `--head user:branch`.",
        f"{BASE}/gh_pr_create",
    ),
    (
        "gh-errors", "gh error: must be in a git repository OR specify --repo",
        "Same root cause as 'could not determine HOST' but specific to PR/issue commands. "
        "Fix: (1) `cd` into a repo and `git remote add origin <url>` if missing; "
        "(2) one-shot: `gh pr view 42 -R owner/repo`; "
        "(3) set persistent default: `gh repo set-default owner/repo`.",
        f"{BASE}/gh_pr_view",
    ),
    (
        "gh-errors", "gh error: failed to authenticate via web flow (browser-blocked / device flow stuck)",
        "Web auth couldn't complete — common over SSH session, in headless CI, or behind a proxy. "
        "Fix: (1) use device-code flow: pick 'Login with a web browser' but copy the code into another browser; "
        "(2) use token flow: paste a PAT from https://github.com/settings/tokens; "
        "(3) for CI: `echo $GH_TOKEN | gh auth login --with-token`; "
        "(4) proxy: `export HTTPS_PROXY=...` before `gh auth login`.",
        f"{BASE}/gh_auth_login",
    ),
    (
        "gh-errors", "gh error: cannot prompt for token in non-interactive mode",
        "Running in CI/script but trying to use the interactive auth flow. "
        "Fix: (1) `echo $GH_TOKEN | gh auth login --with-token`; "
        "(2) just set env: `export GH_TOKEN=ghp_...` (gh picks it up automatically, no login needed); "
        "(3) in GitHub Actions: GH_TOKEN is auto-set from `secrets.GITHUB_TOKEN` if you wire it up via `env:`.",
        f"{BASE}/gh_auth_login",
    ),
    (
        "gh-errors", "gh error: Resource protected by organization SAML enforcement",
        "Org requires SAML SSO authorization for tokens. "
        "Fix: (1) go to https://github.com/settings/tokens, find the token; "
        "(2) click 'Configure SSO' → authorize for the relevant org; "
        "(3) for SSH keys: `https://github.com/settings/keys` → SSO authorize per key; "
        "(4) for org-managed PAT (fine-grained): admin must approve.",
        f"{BASE}/gh_auth_status",
    ),
    (
        "gh-errors", "gh error: a repository for the current owner already exists",
        "You tried `gh repo create` with a name that exists in your account. "
        "Fix: (1) use different name: `gh repo create new-name`; "
        "(2) clone the existing one: `gh repo clone owner/existing-name`; "
        "(3) check what you have: `gh repo list`.",
        f"{BASE}/gh_repo_create",
    ),
    (
        "gh-errors", "gh error: could not clone repository: not found",
        "Repo doesn't exist OR no access. "
        "Fix: (1) verify URL: open in browser; "
        "(2) for private: must be authed with `repo` scope; "
        "(3) for org repo: SSO authorize your token; "
        "(4) typos: `gh repo clone owner/repo` (owner first); "
        "(5) for SSH issues: `gh auth setup-git` to configure git's credential helper.",
        f"{BASE}/gh_repo_clone",
    ),
    (
        "gh-errors", "gh error: workflow file is invalid / not found",
        "`gh workflow run` referenced a workflow that doesn't exist in the default branch, or yml is invalid. "
        "Fix: (1) list available: `gh workflow list`; "
        "(2) reference by filename: `gh workflow run ci.yml` (must exist in default branch); "
        "(3) reference by name: `gh workflow run 'CI'`; "
        "(4) validate yml locally: use `actionlint` or run a syntax check; "
        "(5) for non-default branch: workflow_dispatch must exist in default branch first.",
        f"{BASE}/gh_workflow_run",
    ),
    (
        "gh-errors", "gh error: workflow_dispatch input validation failed",
        "Required input missing or wrong type. "
        "Fix: (1) check inputs: `gh workflow view <workflow>` shows expected inputs; "
        "(2) pass with -f: `gh workflow run ci.yml -f environment=prod -f version=1.2.3`; "
        "(3) JSON via stdin: `echo '{\"environment\":\"prod\"}' | gh workflow run ci.yml --json`; "
        "(4) workflow yml must declare `on: workflow_dispatch: inputs:` block.",
        f"{BASE}/gh_workflow_run",
    ),
    (
        "gh-errors", "gh error: release tag already exists",
        "You ran `gh release create v1.0.0` but tag v1.0.0 already exists. "
        "Fix: (1) use a new tag: `gh release create v1.0.1`; "
        "(2) update existing release (don't create): `gh release edit v1.0.0 --notes 'updated'`; "
        "(3) delete tag + recreate: `git tag -d v1.0.0 && git push origin :refs/tags/v1.0.0 && gh release delete v1.0.0 -y`; "
        "(4) `gh release list` shows what exists.",
        f"{BASE}/gh_release_create",
    ),
    (
        "gh-errors", "gh error: invalid token format / token validation failed",
        "Token doesn't match expected prefix (ghp_, gho_, ghs_, github_pat_). "
        "Fix: (1) classic PAT: starts with `ghp_`; (2) OAuth: `gho_`; (3) server-to-server: `ghs_`; (4) fine-grained PAT: `github_pat_`; "
        "(5) regenerate at https://github.com/settings/tokens (classic) or .../tokens?type=beta (fine-grained); "
        "(6) common mistake: pasted with surrounding whitespace — strip quotes/newlines.",
        f"{BASE}/gh_auth_login",
    ),
    (
        "gh-errors", "gh error: context deadline exceeded / network timeout",
        "API request took too long. "
        "Fix: (1) retry — usually transient; "
        "(2) check status: https://www.githubstatus.com; "
        "(3) corporate proxy: `export HTTPS_PROXY=http://proxy:8080`; "
        "(4) DNS: `nslookup api.github.com` should work; "
        "(5) for large operations (release downloads), use `gh release download` with `--pattern` to limit assets.",
        f"{BASE}/gh_api",
    ),
    (
        "gh-errors", "gh error: branch protection rule prevents merge",
        "PR can't be merged because required checks/reviews aren't satisfied. "
        "Fix: (1) check what's blocking: `gh pr checks <number>` shows CI; `gh pr view` shows review state; "
        "(2) ask for required reviews: `gh pr review <number> --approve` (if you're a reviewer); "
        "(3) update branch with base: `gh pr merge --update-branch` or button on web; "
        "(4) admin override: web UI shows 'Merge without waiting for requirements' for admins.",
        f"{BASE}/gh_pr_merge",
    ),
    (
        "gh-errors", "gh error: at least one approving review is required",
        "Branch protection rule requires N approvals. "
        "Fix: (1) request reviewers: `gh pr edit <number> --add-reviewer <user>`; "
        "(2) check status: `gh pr view <number>` lists requested vs approved; "
        "(3) for solo work on protected branch: temporarily relax rule (admin only) or merge to a non-protected branch first.",
        f"{BASE}/gh_pr_merge",
    ),
    (
        "gh-errors", "gh error: gist update failed / not authorized",
        "Token lacks `gist` scope, or you don't own the gist. "
        "Fix: (1) add scope: `gh auth refresh -s gist`; "
        "(2) check ownership: `gh gist view <id>` shows owner; "
        "(3) for forking gist: gist UI on github.com → Fork button.",
        f"{BASE}/gh_gist_create",
    ),
    (
        "gh-errors", "gh error: extension already installed",
        "You tried `gh extension install` for one that exists. "
        "Fix: (1) update: `gh extension upgrade <name>` (or `--all`); "
        "(2) reinstall: `gh extension remove <name> && gh extension install <owner/repo>`; "
        "(3) list installed: `gh extension list`.",
        f"{BASE}/gh_extension_install",
    ),
    (
        "gh-errors", "gh error: unknown command / command not found in gh",
        "Subcommand typo. "
        "Fix: (1) `gh --help` shows top-level; `gh <cmd> --help` shows sub-subs; "
        "(2) common confusions: `gh checks` doesn't exist — it's `gh pr checks`; "
        "(3) for extensions, install first: `gh extension install owner/ext-name`; "
        "(4) search: `gh search repos topic:gh-extension` shows community extensions.",
        f"{BASE}/gh",
    ),
    (
        "gh-errors", "gh error: could not update: please verify that the ref exists",
        "You tried `gh release edit <tag>` or similar but the tag/ref doesn't exist. "
        "Fix: (1) list refs: `gh release list` or `git ls-remote --tags origin`; "
        "(2) for branch refs in PRs: ensure the branch exists locally + pushed; "
        "(3) refresh local view: `git fetch --all --tags`.",
        f"{BASE}/gh_release_view",
    ),
    (
        "gh-errors", "gh error: EOF when reading from API",
        "Server hung up mid-response — network or GitHub temporary issue. "
        "Fix: (1) retry immediately; "
        "(2) for batch operations: add backoff (`sleep 2` between calls); "
        "(3) for large pagination: use `gh api --paginate` instead of building your own loop; "
        "(4) check https://www.githubstatus.com.",
        f"{BASE}/gh_api",
    ),
    (
        "gh-errors", "gh error: expected one of ... got '...' (for --json fields)",
        "Field name typo in `--json` flag. "
        "Fix: (1) see valid fields: `gh pr view --help` lists them; or run without --json to see the keys; "
        "(2) common fields: `gh pr view --json number,title,state,author`; "
        "(3) use `--jq` for filtering: `gh pr view --json title,state --jq '.title'`; "
        "(4) for arbitrary fields: drop --json, use `gh api` with a custom query.",
        f"{BASE}/gh_pr_view",
    ),
    (
        "gh-errors", "gh error: pr checkout failed (current branch has changes)",
        "`gh pr checkout` would overwrite uncommitted work. "
        "Fix: (1) stash: `git stash && gh pr checkout 42 && git stash pop`; "
        "(2) commit first: `git commit -am 'wip'`; "
        "(3) checkout into a new branch: `gh pr checkout 42 -b pr-42`; "
        "(4) for forks: use `--recurse-submodules` if needed.",
        f"{BASE}/gh_pr_checkout",
    ),
    (
        "gh-errors", "gh error: HTTP 422 / Validation Failed",
        "API rejected the request — usually bad input. "
        "Fix: (1) error message lists which fields failed; "
        "(2) common: invalid label name, non-existent assignee, malformed milestone; "
        "(3) for `gh pr create --reviewer user`: user must be a collaborator; "
        "(4) for issue/PR creation with milestone/project: use exact name (case-sensitive).",
        f"{BASE}/gh_pr_create",
    ),
    (
        "gh-errors", "gh error: cannot prompt - non-interactive shell + no required flags",
        "Running `gh pr create` in CI without `--title`/`--body`. "
        "Fix: (1) pass everything: `gh pr create --title 'feat: X' --body 'desc' --base main --head feature`; "
        "(2) `--fill` uses commit messages for title/body: `gh pr create --fill`; "
        "(3) read body from file: `gh pr create --body-file pr-body.md`; "
        "(4) for issues: `gh issue create --title '...' --body '...'`.",
        f"{BASE}/gh_pr_create",
    ),

    # ============================================================
    # gh-recipes — 36 common workflows
    # ============================================================
    (
        "gh-recipes", "gh recipe: first-time authentication",
        "`gh auth login` — interactive walkthrough: pick host (github.com / Enterprise), protocol (HTTPS / SSH), then either browser flow or paste a PAT. "
        "Verify: `gh auth status` shows token + scopes. "
        "Print token (for use in other tools): `gh auth token`. "
        "For CI: `echo $GH_TOKEN | gh auth login --with-token` OR just `export GH_TOKEN=...`.",
        f"{BASE}/gh_auth_login",
    ),
    (
        "gh-recipes", "gh recipe: refresh auth with additional scopes",
        "Add scopes without re-running full login. "
        "`gh auth refresh -s repo,workflow,write:packages,read:org`. "
        "Common scopes: `repo` (full private repo), `workflow` (Actions), `gist`, `read:org`, `write:packages`. "
        "Check current: `gh auth status` — lists scopes per host.",
        f"{BASE}/gh_auth_refresh",
    ),
    (
        "gh-recipes", "gh recipe: create a repo from a local directory",
        "`gh repo create my-project --public --source=. --remote=origin --push`. "
        "Flags: `--public` / `--private` / `--internal`; `--source=.` uses current dir; `--remote=origin` names the remote; `--push` does the initial push. "
        "Add description: `--description 'cool tool'`. "
        "License + gitignore: `--license MIT --gitignore Python`. "
        "For a clean GitHub repo (no local source): drop `--source`/`--push`.",
        f"{BASE}/gh_repo_create",
    ),
    (
        "gh-recipes", "gh recipe: fork + clone in one step",
        "`gh repo fork owner/repo --clone=true`. "
        "Creates fork in your account, clones locally, sets `origin` to your fork, sets `upstream` to original. "
        "For just fork (no clone): drop `--clone`. "
        "Stay synced: `gh repo sync owner/forked-repo -b main` (one-command upstream sync).",
        f"{BASE}/gh_repo_fork",
    ),
    (
        "gh-recipes", "gh recipe: open a pull request from current branch",
        "Quick: `gh pr create --fill` (auto-fills title + body from commit messages). "
        "Full control: `gh pr create --title 'feat: add X' --body 'closes #42' --base main --reviewer alice,bob --label feature`. "
        "Draft: add `--draft`. "
        "Web (open in browser to finish): `gh pr create --web`. "
        "From a fork: gh detects upstream automatically.",
        f"{BASE}/gh_pr_create",
    ),
    (
        "gh-recipes", "gh recipe: open a draft PR for early review",
        "`gh pr create --draft --title 'WIP: feature X' --body 'in progress, getting early feedback'`. "
        "Convert to ready when done: `gh pr ready <number>` or the 'Ready for review' button on the PR. "
        "Draft PRs don't notify reviewers automatically.",
        f"{BASE}/gh_pr_create",
    ),
    (
        "gh-recipes", "gh recipe: merge a PR (squash / rebase / merge commit)",
        "Squash (clean main): `gh pr merge 42 --squash --delete-branch`. "
        "Rebase (linear): `--rebase --delete-branch`. "
        "Merge commit: `--merge` (default). "
        "Auto-merge (wait for CI): `--auto`. "
        "Always combine with `--delete-branch` to clean up. "
        "Without args, `gh pr merge` prompts interactively.",
        f"{BASE}/gh_pr_merge",
    ),
    (
        "gh-recipes", "gh recipe: review a PR (approve / request changes / comment)",
        "Approve: `gh pr review 42 --approve --body 'LGTM'`. "
        "Request changes: `gh pr review 42 --request-changes --body 'please fix X'`. "
        "Comment without verdict: `gh pr review 42 --comment --body 'question on line 12'`. "
        "Body from file: `--body-file review.md`. "
        "Or open in browser for inline line comments: `gh pr view 42 --web`.",
        f"{BASE}/gh_pr_review",
    ),
    (
        "gh-recipes", "gh recipe: check out someone's PR locally",
        "`gh pr checkout 42` — switches to a local branch tracking the PR. Works for forks too. "
        "Into new branch name: `gh pr checkout 42 -b review-42`. "
        "After review, return to main: `git switch main`. "
        "Clean up local branch: `git branch -D pr/42-branch-name` (gh names them based on PR title).",
        f"{BASE}/gh_pr_checkout",
    ),
    (
        "gh-recipes", "gh recipe: see PR diff / CI status before merging",
        "Diff: `gh pr diff 42` (or pipe to less / a pager). "
        "CI status: `gh pr checks 42`. "
        "Combined live-view: `gh pr view 42` shows status, reviews, checks. "
        "Watch CI live: `gh run watch <run-id>` (find via `gh run list`).",
        f"{BASE}/gh_pr_view",
    ),
    (
        "gh-recipes", "gh recipe: list PRs assigned to you / waiting on you",
        "`gh pr status` — your most relevant PRs (open, draft, review-requested). "
        "Just yours: `gh pr list --author '@me'`. "
        "Review requests: `gh pr list --search 'review-requested:@me'`. "
        "Specific filters: `gh pr list --state all --label bug --limit 50`. "
        "JSON for scripts: `gh pr list --json number,title,author --jq '.[] | .title'`.",
        f"{BASE}/gh_pr_status",
    ),
    (
        "gh-recipes", "gh recipe: open an issue from CLI",
        "`gh issue create --title 'bug: foo crashes' --body 'steps to reproduce: ...'`. "
        "With assignee + label + milestone: `--assignee alice --label bug --milestone v2.0`. "
        "From a template: `--template bug_report.md`. "
        "Body from stdin: `echo 'description' | gh issue create --title 'X' --body-file -`. "
        "Web form: `--web`.",
        f"{BASE}/gh_issue_create",
    ),
    (
        "gh-recipes", "gh recipe: close + comment on issue in one go",
        "`gh issue close 123 --comment 'fixed in #145'`. "
        "Reopen: `gh issue reopen 123 --comment 'reproduces in v1.2.1'`. "
        "Add comment without state change: `gh issue comment 123 --body '...'`. "
        "Pipe long body: `cat report.md | gh issue comment 123 --body-file -`.",
        f"{BASE}/gh_issue_close",
    ),
    (
        "gh-recipes", "gh recipe: create a release with binary assets",
        "`gh release create v1.2.3 ./dist/* --title 'v1.2.3' --notes 'changelog here'`. "
        "Auto-generated notes from commits: `--generate-notes`. "
        "Notes from file: `--notes-file CHANGELOG.md`. "
        "Pre-release: `--prerelease`. "
        "Draft: `--draft` (review before publishing). "
        "Target specific commit: `--target abc123`.",
        f"{BASE}/gh_release_create",
    ),
    (
        "gh-recipes", "gh recipe: download release assets",
        "All assets: `gh release download v1.2.3`. "
        "Pattern: `gh release download v1.2.3 --pattern '*.tar.gz'`. "
        "To specific dir: `gh release download v1.2.3 --dir ./downloads`. "
        "Latest release: `gh release download` (no version = latest). "
        "For a private repo: must be authed with `repo` scope.",
        f"{BASE}/gh_release_download",
    ),
    (
        "gh-recipes", "gh recipe: trigger a workflow with inputs",
        "`gh workflow run deploy.yml -f environment=prod -f version=1.2.3 --ref main`. "
        "Pre-req: workflow yml must have `on: workflow_dispatch: inputs:`. "
        "JSON inputs: `gh workflow run deploy.yml --json < inputs.json`. "
        "List workflows: `gh workflow list`. "
        "Workflow must exist in default branch for `workflow_dispatch` to be available.",
        f"{BASE}/gh_workflow_run",
    ),
    (
        "gh-recipes", "gh recipe: watch a CI workflow run live",
        "`gh run watch` — interactive picker of in-progress runs, then live-tails. "
        "Specific run: `gh run watch <run-id>`. "
        "Find run-id: `gh run list --workflow ci.yml --limit 5` or `gh run list --status in_progress`. "
        "Open in browser instead: `gh run view <run-id> --web`. "
        "View completed run logs: `gh run view <run-id> --log`.",
        f"{BASE}/gh_run_watch",
    ),
    (
        "gh-recipes", "gh recipe: re-run a failed workflow",
        "All jobs: `gh run rerun <run-id>`. "
        "Failed jobs only: `gh run rerun <run-id> --failed`. "
        "With debug logging enabled: `gh run rerun <run-id> --debug`. "
        "Find recent failures: `gh run list --status failure --limit 10`.",
        f"{BASE}/gh_run_rerun",
    ),
    (
        "gh-recipes", "gh recipe: cancel a stuck workflow run",
        "`gh run cancel <run-id>`. "
        "Find runs: `gh run list --status in_progress`. "
        "Cancel multiple by id: shell loop over IDs. "
        "Stopping all runs of a workflow: `gh run list --workflow ci.yml --status in_progress --json databaseId --jq '.[].databaseId' | xargs -I {} gh run cancel {}`.",
        f"{BASE}/gh_run_cancel",
    ),
    (
        "gh-recipes", "gh recipe: set a repository secret",
        "Single value: `echo -n 'my-secret-value' | gh secret set DEPLOY_KEY`. "
        "From file: `gh secret set DEPLOY_KEY < ./key.pem`. "
        "All repos in org: `gh secret set TOKEN -o my-org --visibility all`. "
        "Specific repos in org: `--visibility selected --repos repo1,repo2`. "
        "Dependabot secret: `--app dependabot`. "
        "Codespaces: `--app codespaces`.",
        f"{BASE}/gh_secret_set",
    ),
    (
        "gh-recipes", "gh recipe: set a repository variable",
        "Like secrets but readable. `gh variable set NODE_VERSION --body '20'`. "
        "From env: `gh variable set NODE_VERSION --body \"$NODE_VERSION\"`. "
        "Org-wide: `-o my-org`. "
        "List: `gh variable list`. "
        "Use case: non-sensitive config (region, image tag) that workflows read via `vars.NODE_VERSION`.",
        f"{BASE}/gh_variable_set",
    ),
    (
        "gh-recipes", "gh recipe: make an authenticated API call (gh api)",
        "REST: `gh api repos/owner/repo` (handles auth + base URL). "
        "POST/PATCH/DELETE: `gh api -X POST repos/owner/repo/issues -f title='bug' -f body='...'`. "
        "Pagination: `gh api repos/owner/repo/issues --paginate` (follows Link headers automatically). "
        "JQ filter: `gh api repos/owner/repo --jq '.stargazers_count'`. "
        "Headers: `-H 'X-Foo: bar'`. "
        "Methods + content-type: handled automatically by gh.",
        f"{BASE}/gh_api",
    ),
    (
        "gh-recipes", "gh recipe: use GraphQL via gh api",
        "`gh api graphql -f query='query { viewer { login } }'`. "
        "With variables: `gh api graphql -F owner='octocat' -F name='hello' -f query='query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { stargazerCount } }'`. "
        "JQ extract: `--jq '.data.repository.stargazerCount'`. "
        "Full GraphQL docs: https://docs.github.com/en/graphql/reference.",
        f"{BASE}/gh_api",
    ),
    (
        "gh-recipes", "gh recipe: search code across GitHub",
        "`gh search code 'function login' --language=python --limit 20`. "
        "Repo-scoped: `--repo owner/repo`. "
        "Owner-scoped: `--owner microsoft`. "
        "Path filter: `--filename '*.py' --path 'src/'`. "
        "Caveat: GitHub code search has lower rate limit (30/min) and excludes some forks/archived. "
        "For broader: switch to the web-only advanced search at https://github.com/search.",
        f"{BASE}/gh_search_code",
    ),
    (
        "gh-recipes", "gh recipe: open repo or PR in browser",
        "Repo home: `gh browse`. "
        "Specific file: `gh browse path/to/file.py`. "
        "Specific line: `gh browse path/to/file.py:42`. "
        "Specific PR: `gh pr view 42 --web` or `gh browse 42` (if number is unambiguous). "
        "Specific issue: `gh issue view 99 --web`. "
        "Just print URL (don't open): `gh browse --no-browser`.",
        f"{BASE}/gh_browse",
    ),
    (
        "gh-recipes", "gh recipe: use --json for scripting",
        "Almost every list/view command supports `--json`. "
        "Example: `gh pr list --json number,title,author,createdAt`. "
        "Filter with --jq: `gh pr list --json number,title --jq '.[] | select(.title | test(\"feat\")) | .number'`. "
        "For complex shaping, pipe to `jq`: `gh pr list --json title,labels | jq -r '.[] | [.title, (.labels | map(.name) | join(\",\"))] | @tsv'`. "
        "See fields: `gh pr list --json help` (lists all available fields).",
        f"{BASE}/gh",
    ),
    (
        "gh-recipes", "gh recipe: set up aliases for common commands",
        "Shortcuts. "
        "`gh alias set co 'pr checkout'` — then `gh co 42` checks out PR #42. "
        "`gh alias set prs 'pr list --author @me --state open'`. "
        "Shell aliases (with `-s`): `gh alias set bugs --shell 'gh issue list --label bug'`. "
        "List: `gh alias list`. "
        "Delete: `gh alias delete co`.",
        f"{BASE}/gh_alias_set",
    ),
    (
        "gh-recipes", "gh recipe: install + use a gh extension",
        "Extensions add new commands. "
        "Install: `gh extension install dlvhdr/gh-dash` (popular: dashboard). "
        "Search: `gh extension search` or browse https://github.com/topics/gh-extension. "
        "List installed: `gh extension list`. "
        "Update all: `gh extension upgrade --all`. "
        "Remove: `gh extension remove gh-dash`. "
        "Useful ones: gh-dash (dashboard), gh-poi (clean merged branches), gh-copilot (AI assist).",
        f"{BASE}/gh_extension_install",
    ),
    (
        "gh-recipes", "gh recipe: create + connect to a codespace",
        "Create: `gh codespace create -r owner/repo -b main`. "
        "List: `gh codespace list`. "
        "Connect via SSH: `gh codespace ssh -c <codespace-name>`. "
        "Open in VS Code: `gh codespace code -c <name>`. "
        "Stop (saves state): `gh codespace stop -c <name>`. "
        "Delete: `gh codespace delete -c <name>`. "
        "Free quota: 60 hours/month on personal account.",
        f"{BASE}/gh_codespace_create",
    ),
    (
        "gh-recipes", "gh recipe: switch between GitHub.com and Enterprise hosts",
        "Multiple hosts in one config. "
        "Login to both: `gh auth login -h github.com` and `gh auth login -h github.enterprise.example.com`. "
        "Status: `gh auth status` lists both. "
        "Per-command override: `gh repo view -R enterprise.example.com/owner/repo`. "
        "Switch default: `gh config set -h <host> ...` or set `GH_HOST=github.enterprise.example.com`.",
        f"{BASE}/gh_auth_login",
    ),
    (
        "gh-recipes", "gh recipe: configure SSH for gh clone (instead of HTTPS)",
        "By default `gh repo clone` uses HTTPS. To switch: "
        "(1) During auth: `gh auth login` → pick 'SSH' as protocol; "
        "(2) Set per-config: `gh config set git_protocol ssh -h github.com`; "
        "(3) gh uses your existing `~/.ssh/id_ed25519`. "
        "Verify: `gh repo clone owner/repo` should produce a `git@github.com:` remote, not `https://`.",
        f"{BASE}/gh_config_set",
    ),
    (
        "gh-recipes", "gh recipe: stay synced with upstream (fork workflow)",
        "After fork+clone with `gh repo fork --clone`: "
        "Sync your fork's main: `gh repo sync owner/forked-repo -b main`. "
        "From your local: `git fetch upstream && git rebase upstream/main && git push origin main`. "
        "When PRs go stale: `gh pr update-branch <number>` (merges base into PR branch).",
        f"{BASE}/gh_repo_sync",
    ),
    (
        "gh-recipes", "gh recipe: bulk-close stale issues",
        "Close all issues with label 'stale' older than 30 days. "
        "`gh issue list --label stale --state open --limit 100 --json number --jq '.[].number' | xargs -I {} gh issue close {} --comment 'Closing stale issue. Reopen if still relevant.'`. "
        "Always test the list query first without piping to xargs. "
        "For automation: GitHub's `actions/stale` workflow does this on schedule.",
        f"{BASE}/gh_issue_list",
    ),
    (
        "gh-recipes", "gh recipe: list PRs you need to review",
        "`gh pr list --search 'review-requested:@me'`. "
        "With CI status: `gh pr list --search 'review-requested:@me' --json number,title,headRefName,statusCheckRollup --jq '.[] | [.number, .title, .statusCheckRollup[0].state] | @tsv'`. "
        "All organization PRs needing review: `gh search prs --review-requested @me --state open`.",
        f"{BASE}/gh_pr_list",
    ),
    (
        "gh-recipes", "gh recipe: automate release notes from merged PRs",
        "`gh release create v1.2.3 --generate-notes` — GitHub builds release notes from PRs/commits since last release. "
        "Custom format via `.github/release.yml` in your repo (categorize by label). "
        "Combine with auto-tag: `git tag v1.2.3 && git push origin v1.2.3 && gh release create v1.2.3 --generate-notes`. "
        "For semver bumping, tools like `release-please` (GitHub Action) work alongside.",
        f"{BASE}/gh_release_create",
    ),
    (
        "gh-recipes", "gh recipe: run gh in GitHub Actions",
        "GitHub Actions auto-provides `secrets.GITHUB_TOKEN`. Pass to gh via env: "
        "```yaml\n"
        "- run: gh pr list\n  env:\n    GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n```\n"
        "GITHUB_TOKEN is scoped to current repo. For cross-repo access, use a PAT in a repo secret: `GH_TOKEN: ${{ secrets.PAT }}`. "
        "Adjust permissions in workflow yml via `permissions:` block (issues: write, pull-requests: write, etc).",
        f"{BASE}/gh",
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
            evidence_id=f"ev-gh-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-gh-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="gh-catalog",
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
