"""Direct-insert git error + recipe claims."""

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

BASE = "https://git-scm.com/docs"

CLAIMS = [
    # ============================================================
    # git-errors — 30 most common errors
    # ============================================================
    (
        "git-errors", "git error: fatal: not a git repository (or any of the parent directories)",
        "You ran a git command outside any git repo. "
        "Fix: (1) `cd` into a repo, OR (2) `git init` to create one here, OR (3) `git clone <url>` to clone an existing one. "
        "Verify with `git rev-parse --is-inside-work-tree`. "
        "If you ARE inside a repo: `.git/` may be deleted or corrupted — check `ls -la`.",
        f"{BASE}/git-init",
    ),
    (
        "git-errors", "git error: fatal: refusing to merge unrelated histories",
        "Two repos have no common ancestor commit (e.g. you re-init'd or cloned wrong). "
        "Fix: (1) intentional? `git pull --allow-unrelated-histories origin main`; "
        "(2) accidental? double-check you're pulling from the right remote: `git remote -v`; "
        "(3) cleanest reset: `git fetch origin && git reset --hard origin/main` (discards local commits).",
        f"{BASE}/git-pull",
    ),
    (
        "git-errors", "git error: fatal: Authentication failed for 'https://...'",
        "Wrong credentials or expired token. GitHub stopped accepting passwords in 2021 — you need a token. "
        "Fix: (1) create a Personal Access Token: GitHub Settings → Developer settings → Tokens; "
        "(2) use token as password: `git push` and paste token at prompt; "
        "(3) cache it: `git config --global credential.helper osxkeychain` (macOS) / `manager` (Windows); "
        "(4) switch to SSH: `git remote set-url origin git@github.com:user/repo.git` then `ssh-keygen` + add to GitHub.",
        f"{BASE}/git-credential",
    ),
    (
        "git-errors", "git error: fatal: remote origin already exists",
        "You tried `git remote add origin <url>` but origin is already configured. "
        "Fix: (1) update URL instead: `git remote set-url origin <new-url>`; "
        "(2) remove + re-add: `git remote rm origin && git remote add origin <url>`; "
        "(3) see existing: `git remote -v`.",
        f"{BASE}/git-remote",
    ),
    (
        "git-errors", "git error: failed to push some refs (non-fast-forward / remote contains work)",
        "Remote has commits you don't. Pushing would overwrite them. "
        "Fix: (1) pull + merge: `git pull --rebase origin main` (recommended), then `git push`; "
        "(2) if your local IS the truth (rare, e.g. after rebase): `git push --force-with-lease` (safer than --force); "
        "(3) NEVER `--force` on shared branches without coordination — wipes others' work.",
        f"{BASE}/git-push",
    ),
    (
        "git-errors", "git error: Your local changes to the following files would be overwritten by checkout/merge/pull",
        "Uncommitted local edits would conflict with the incoming version. "
        "Fix: (1) stash + reapply: `git stash && <your-command> && git stash pop`; "
        "(2) commit them first: `git add -A && git commit -m 'WIP'`; "
        "(3) discard them: `git checkout -- <file>` (destructive!); "
        "(4) discard all: `git reset --hard HEAD` (very destructive!).",
        f"{BASE}/git-stash",
    ),
    (
        "git-errors", "git error: fatal: The current branch X has no upstream branch",
        "Your local branch isn't linked to a remote branch — push has no target. "
        "Fix: (1) first push, set upstream: `git push -u origin <branch>`; "
        "(2) subsequent pushes just need `git push`; "
        "(3) configure globally: `git config --global push.autoSetupRemote true` (Git 2.37+) — sets upstream automatically.",
        f"{BASE}/git-push",
    ),
    (
        "git-errors", "git error: Pulling is not possible because you have unmerged files",
        "A prior merge/rebase/cherry-pick is still in progress with conflicts. "
        "Fix: (1) resolve conflicts: edit files, then `git add <file>` for each; "
        "(2) finish: `git merge --continue` (or rebase --continue); "
        "(3) bail out: `git merge --abort` (returns to pre-merge state); "
        "(4) check status: `git status` shows what's unmerged.",
        f"{BASE}/git-merge",
    ),
    (
        "git-errors", "git error: detected dubious ownership in repository",
        "Security fix (CVE-2022-24765): the repo is owned by a different user than the one running git. "
        "Fix: (1) trust this specific repo: `git config --global --add safe.directory /path/to/repo`; "
        "(2) trust all: `git config --global --add safe.directory '*'` (only on trusted machines); "
        "(3) fix actual ownership: `sudo chown -R $USER:$USER /path/to/repo`. "
        "Common when working in Docker mounts or shared volumes.",
        f"{BASE}/git-config",
    ),
    (
        "git-errors", "git error: pathspec 'X' did not match any file(s) known to git",
        "File doesn't exist or isn't tracked. Several causes: typo, wrong directory, file is in .gitignore, file was renamed/deleted. "
        "Fix: (1) check spelling + path: `ls <path>`; "
        "(2) is it ignored? `git check-ignore -v <path>`; "
        "(3) for checkout/restore of deleted files: use `git restore --source=HEAD <path>`; "
        "(4) `git status` shows what git sees.",
        f"{BASE}/git-add",
    ),
    (
        "git-errors", "git error: cannot lock ref / Another git process seems to be running",
        "A `.git/index.lock` or `.git/refs/.../*.lock` file exists. Either git is actually running OR a previous run crashed. "
        "Fix: (1) check for running git: `ps aux | grep git`; "
        "(2) if none, remove lock: `rm -f .git/index.lock`; "
        "(3) for ref locks: `rm .git/refs/heads/<branch>.lock`; "
        "(4) NEVER kill a running git process partway — corrupt the index.",
        f"{BASE}/git-config",
    ),
    (
        "git-errors", "git error: src refspec X does not match any",
        "You tried `git push origin <branch>` but the branch doesn't exist locally (typo or never created). "
        "Fix: (1) list branches: `git branch`; "
        "(2) check current: `git branch --show-current`; "
        "(3) create first: `git checkout -b <branch>` then push; "
        "(4) common mistake: pushing `main` when local branch is `master` (or vice versa).",
        f"{BASE}/git-push",
    ),
    (
        "git-errors", "git error: fatal: Need to specify how to reconcile divergent branches",
        "Git 2.27+ requires explicit `pull` strategy when local + remote diverged. "
        "Fix (pick one globally): "
        "(1) merge (default historical): `git config --global pull.rebase false`; "
        "(2) rebase (cleaner history): `git config --global pull.rebase true`; "
        "(3) only fast-forward: `git config --global pull.ff only`; "
        "Or one-off: `git pull --rebase` or `git pull --no-rebase`.",
        f"{BASE}/git-pull",
    ),
    (
        "git-errors", "git error: CONFLICT (content): Merge conflict in <file>",
        "Both branches changed the same lines — git can't auto-merge. "
        "Fix: (1) open the file — git inserts `<<<<<<< HEAD`, `=======`, `>>>>>>> branch` markers; "
        "(2) edit to keep what you want, remove all markers; "
        "(3) stage: `git add <file>`; "
        "(4) finish: `git merge --continue` (or `git commit` for plain merge); "
        "(5) bail: `git merge --abort`. "
        "Use `git mergetool` for a GUI.",
        f"{BASE}/git-merge",
    ),
    (
        "git-errors", "git error: Please tell me who you are / empty ident name",
        "Git needs author identity for commits but no name/email is configured. "
        "Fix: (1) global: `git config --global user.name 'Your Name'` and `git config --global user.email 'you@example.com'`; "
        "(2) per-repo (work vs personal): omit --global inside the repo; "
        "(3) verify: `git config user.name && git config user.email`.",
        f"{BASE}/git-config",
    ),
    (
        "git-errors", "git error: ambiguous argument 'X': unknown revision or path not in the working tree",
        "Git can't tell if 'X' is a branch/tag/commit OR a file path. "
        "Fix: (1) disambiguate with `--`: `git checkout main -- file.txt` (everything after -- is a path); "
        "(2) for revisions: use full SHA or branch name; "
        "(3) verify revision exists: `git rev-parse X` (errors if not); "
        "(4) common when a branch + file share a name.",
        f"{BASE}/git-checkout",
    ),
    (
        "git-errors", "git error: index file corrupt / bad signature in index",
        "The .git/index file is corrupted (often from disk-full mid-write or killed process). "
        "Fix: (1) regenerate from HEAD: `rm .git/index && git reset` (re-stages tracked files at HEAD); "
        "(2) you lose any staged-but-not-committed changes, but commits are safe; "
        "(3) `git status` should work after; "
        "(4) for severe corruption: `git fsck --full` shows extent of damage.",
        f"{BASE}/git-reset",
    ),
    (
        "git-errors", "git error: SSL certificate problem: unable to get local issuer certificate",
        "Git can't validate the HTTPS cert — usually corporate proxy/MITM or stale CA bundle. "
        "Fix: (1) trust your corporate CA: `git config --global http.sslCAInfo /path/to/cert.pem`; "
        "(2) update OS CA bundle: macOS `brew install ca-certificates`; "
        "(3) LAST RESORT (insecure, dev only): `git config --global http.sslVerify false`; "
        "(4) switch to SSH if HTTPS won't work.",
        f"{BASE}/git-config",
    ),
    (
        "git-errors", "git error: Could not read from remote repository / Permission denied (publickey)",
        "SSH auth failed — your key isn't trusted by the remote. "
        "Fix: (1) test connection: `ssh -T git@github.com` (should say 'Hi <user>'); "
        "(2) add key to ssh-agent: `eval $(ssh-agent) && ssh-add ~/.ssh/id_ed25519`; "
        "(3) upload public key to GitHub: copy `~/.ssh/id_ed25519.pub` → Settings → SSH Keys; "
        "(4) generate if missing: `ssh-keygen -t ed25519 -C 'you@example.com'`; "
        "(5) for multiple accounts: ~/.ssh/config with Host entries.",
        f"{BASE}/git-credential",
    ),
    (
        "git-errors", "git error: refusing to update checked out branch",
        "You tried to push to a non-bare remote repo whose currently-checked-out branch matches your push target. "
        "Fix: (1) push to a different branch; "
        "(2) use a bare repo as remote: `git init --bare` on the server; "
        "(3) on the server, allow it: `git config receive.denyCurrentBranch updateInstead` (auto-updates working tree); "
        "(4) usually only seen with self-hosted git setups.",
        f"{BASE}/git-push",
    ),
    (
        "git-errors", "git error: A branch named 'X' already exists",
        "Trying to create a branch that exists. "
        "Fix: (1) just switch to it: `git switch X` (or `git checkout X`); "
        "(2) force-create at new ref (overwrites): `git checkout -B X <ref>`; "
        "(3) delete old + recreate: `git branch -D X && git branch X`; "
        "(4) check existing: `git branch --list 'X*'`.",
        f"{BASE}/git-branch",
    ),
    (
        "git-errors", "git error: fatal: cannot do a partial commit during a merge",
        "Mid-merge, you tried `git commit -- specific-files` instead of committing all. "
        "Fix: (1) finish merge first: stage everything resolved + `git commit` (no file args); "
        "(2) bail out: `git merge --abort` then make your partial commits; "
        "(3) shouldn't normally hit this — `git status` will say 'All conflicts fixed but you are still merging'.",
        f"{BASE}/git-merge",
    ),
    (
        "git-errors", "git error: Untracked working tree file 'X' would be overwritten by merge/checkout",
        "An untracked file in your working tree has the same name as an incoming tracked file. "
        "Fix: (1) move or delete the untracked file: `mv X X.bak`; "
        "(2) or add to .gitignore if it shouldn't be tracked; "
        "(3) common with generated files (build outputs); "
        "(4) DON'T `--force` blindly — you'll lose the local file.",
        f"{BASE}/git-checkout",
    ),
    (
        "git-errors", "git warning: You are in 'detached HEAD' state",
        "Not an error — informational. You've checked out a commit/tag instead of a branch, so new commits won't belong to any branch. "
        "Fix: (1) intentional (just looking): no action — `git switch main` to leave; "
        "(2) want to keep changes on a new branch: `git switch -c new-branch`; "
        "(3) common after `git checkout <sha>` or `git checkout v1.2.3`.",
        f"{BASE}/git-checkout",
    ),
    (
        "git-errors", "git error: fatal: bad object / loose object is corrupt",
        "An object file in .git/objects is missing/corrupted (disk error, bad copy, partial pull). "
        "Fix: (1) scan damage: `git fsck --full`; "
        "(2) if only loose objects corrupt + remote has them: `git fetch --all` then re-resolve refs; "
        "(3) clone fresh + cherry-pick local commits: `git clone <url> fresh && cd fresh && git cherry-pick <sha>...`; "
        "(4) nuclear: backup + re-clone.",
        f"{BASE}/git-config",
    ),
    (
        "git-errors", "git error: error: object file is empty / fatal: pack signature mismatch",
        "Pack file or loose object got zero bytes (interrupted git push/fetch). "
        "Fix: (1) `git fsck` shows which objects; "
        "(2) re-fetch missing objects: `git fetch --all`; "
        "(3) repack: `git gc --aggressive`; "
        "(4) for stubborn corruption: delete the empty object file (path shown by fsck) then `git fetch`.",
        f"{BASE}/git-config",
    ),
    (
        "git-errors", "git error: invalid path 'NUL' / 'COM1' / 'aux' (Windows reserved name)",
        "Windows refuses to create files with reserved DOS names (NUL, CON, AUX, COM1-9, LPT1-9). "
        "Fix: (1) on Linux/macOS: ignore (works fine); "
        "(2) on Windows: enable long-paths + use WSL; "
        "(3) actually rename in source repo if you control it: `git mv NUL.txt placeholder.txt`; "
        "(4) checkout with skip: `git config core.protectNTFS false` (Windows only, lets you check out, but file won't work).",
        f"{BASE}/git-checkout",
    ),
    (
        "git-errors", "git error: there is no tracking information for the current branch",
        "`git pull` without args needs to know which remote/branch to pull from. "
        "Fix: (1) set tracking: `git branch --set-upstream-to=origin/<branch>`; "
        "(2) one-off: `git pull origin <branch>`; "
        "(3) set on push: `git push -u origin <branch>` (creates tracking link).",
        f"{BASE}/git-branch",
    ),
    (
        "git-errors", "git error: fatal: ambiguous reference 'X'",
        "'X' could mean a branch OR a tag with the same name. "
        "Fix: (1) for branch: `refs/heads/X`; "
        "(2) for tag: `refs/tags/X`; "
        "(3) example: `git checkout refs/tags/v1.0` to disambiguate; "
        "(4) better: rename one to avoid collision: `git tag -d X && git tag X-tag`.",
        f"{BASE}/git-checkout",
    ),
    (
        "git-errors", "git error: ! [rejected] master -> master (fetch first)",
        "Push rejected because remote has commits you don't (non-fast-forward, same root cause as 'failed to push some refs'). "
        "Fix: (1) `git fetch origin && git rebase origin/master`, resolve conflicts, then `git push`; "
        "(2) merge instead: `git pull origin master` (creates merge commit); "
        "(3) if rebasing rewrote published commits: `git push --force-with-lease` (safe alternative to --force).",
        f"{BASE}/git-push",
    ),

    # ============================================================
    # git-recipes — 36 common workflows
    # ============================================================
    (
        "git-recipes", "git recipe: first-time setup (identity, editor, default branch)",
        "Configure once per machine. "
        "`git config --global user.name 'Your Name'`. "
        "`git config --global user.email 'you@example.com'`. "
        "`git config --global init.defaultBranch main`. "
        "`git config --global core.editor 'code --wait'` (or vim, nano). "
        "`git config --global pull.rebase true` (cleaner history) or `false` (default merge). "
        "Inspect: `git config --global --list`.",
        f"{BASE}/git-config",
    ),
    (
        "git-recipes", "git recipe: create new repo + push first commit",
        "From scratch. "
        "(1) `git init` (creates .git/); "
        "(2) `git add .` (stage all); "
        "(3) `git commit -m 'initial commit'`; "
        "(4) create empty repo on GitHub/GitLab; "
        "(5) `git remote add origin <url>`; "
        "(6) `git push -u origin main` (the -u sets upstream for future pushes). "
        "Alternative: clone the empty remote first, then work inside.",
        f"{BASE}/git-init",
    ),
    (
        "git-recipes", "git recipe: undo last commit but keep changes",
        "Soft reset. `git reset --soft HEAD~1`. "
        "Effect: the commit is gone from history, but staged changes remain. "
        "Variants: `--mixed` (default) = unstaged but kept; `--hard` = DELETED (destructive). "
        "Then edit and recommit: `git commit -m 'fixed message'`. "
        "Caveat: only safe if commit hasn't been pushed yet.",
        f"{BASE}/git-reset",
    ),
    (
        "git-recipes", "git recipe: amend the last commit",
        "Add forgotten files or fix message. "
        "Add more files: `git add forgotten.txt && git commit --amend --no-edit` (keeps message). "
        "Change message only: `git commit --amend -m 'new message'`. "
        "Edit message interactively: `git commit --amend`. "
        "Caveat: amend creates a NEW commit object — if pushed, you need `git push --force-with-lease`. "
        "Don't amend others' commits or pushed commits without warning.",
        f"{BASE}/git-commit",
    ),
    (
        "git-recipes", "git recipe: stash work-in-progress and come back later",
        "Save uncommitted changes without committing. "
        "`git stash` (or `git stash push -m 'wip on feature X'`). "
        "Resume later: `git stash pop` (apply + drop) or `git stash apply` (apply, keep). "
        "List stashes: `git stash list`. "
        "Apply specific: `git stash apply stash@{2}`. "
        "Include untracked: `git stash -u`. "
        "Stash specific files: `git stash push -- file1 file2`.",
        f"{BASE}/git-stash",
    ),
    (
        "git-recipes", "git recipe: create + switch to new branch",
        "Modern: `git switch -c feature-x` (creates + switches). "
        "Legacy: `git checkout -b feature-x` (same thing). "
        "From specific commit: `git switch -c feature-x abc123`. "
        "From remote branch: `git switch -c local-name origin/remote-name`. "
        "Push the new branch: `git push -u origin feature-x` (sets upstream).",
        f"{BASE}/git-switch",
    ),
    (
        "git-recipes", "git recipe: delete local + remote branch",
        "Local: `git branch -d feature-x` (safe — refuses if unmerged) or `-D` (force). "
        "Remote: `git push origin --delete feature-x`. "
        "Prune stale remote-tracking refs after others delete: `git fetch --prune` or `git remote prune origin`. "
        "Bulk-delete merged: `git branch --merged | grep -v '\\*\\|main' | xargs -n 1 git branch -d`.",
        f"{BASE}/git-branch",
    ),
    (
        "git-recipes", "git recipe: rename a branch",
        "Current branch: `git branch -m new-name`. "
        "Specific branch: `git branch -m old-name new-name`. "
        "If already pushed: also delete old remote + push new: "
        "`git push origin --delete old-name && git push -u origin new-name`. "
        "Update GitHub PRs: GitHub auto-redirects PRs to the new branch name.",
        f"{BASE}/git-branch",
    ),
    (
        "git-recipes", "git recipe: squash merge a feature branch into main",
        "All feature commits become one in main. "
        "On main: `git merge --squash feature-x` then `git commit -m 'feat: feature X'`. "
        "Alternative: GitHub's 'Squash and merge' button does the same. "
        "Trade-off: cleaner main history but loses granular commits; for review-only branches it's ideal.",
        f"{BASE}/git-merge",
    ),
    (
        "git-recipes", "git recipe: interactive rebase to squash/edit/reorder commits",
        "Rewrite history before push. `git rebase -i HEAD~5` (last 5 commits). "
        "In editor: change `pick` to `squash` (combine with above), `reword` (edit msg), `edit` (pause to amend), `drop` (delete), or reorder lines. "
        "Save + close → git replays. "
        "Caveat: rewrites SHAs — if branch was pushed, you'll need `--force-with-lease` and coordinate with collaborators.",
        f"{BASE}/git-rebase",
    ),
    (
        "git-recipes", "git recipe: cherry-pick a commit from another branch",
        "Apply a specific commit elsewhere. `git cherry-pick abc1234`. "
        "Range: `git cherry-pick abc..xyz` (exclusive of abc) or `abc^..xyz` (inclusive). "
        "Without committing (just apply changes): `--no-commit`. "
        "On conflict: resolve + `git cherry-pick --continue` or `--abort`. "
        "Common use: hotfix from main → backport to release branch.",
        f"{BASE}/git-cherry-pick",
    ),
    (
        "git-recipes", "git recipe: revert a published commit (safely)",
        "Create an undo commit (doesn't rewrite history). `git revert abc1234`. "
        "Multiple: `git revert abc..xyz`. "
        "Merge commit: `git revert -m 1 <merge-sha>` (the -m picks which parent to keep). "
        "Without committing (combine multiple reverts): `--no-commit` then commit at end. "
        "Use when commits are public — unlike reset, revert is safe for shared branches.",
        f"{BASE}/git-revert",
    ),
    (
        "git-recipes", "git recipe: force-push safely with --force-with-lease",
        "After rebase or amend on a pushed branch. "
        "`git push --force-with-lease`. "
        "Difference vs `--force`: only succeeds if remote hasn't moved since your last fetch. Protects against overwriting someone else's pushes you don't have. "
        "Even safer: `--force-with-lease=refs/heads/main:abc123` (explicit expected SHA). "
        "NEVER force-push to main/master on shared repos without explicit coordination.",
        f"{BASE}/git-push",
    ),
    (
        "git-recipes", "git recipe: pull with rebase instead of merge",
        "Cleaner linear history. "
        "One-off: `git pull --rebase origin main`. "
        "Per-branch default: `git config branch.<branch>.rebase true`. "
        "Global default: `git config --global pull.rebase true`. "
        "On conflict: resolve, `git add <file>`, then `git rebase --continue` (or `--abort`). "
        "Trade-off: cleaner history, but rewriting local commits — don't do on shared local branches.",
        f"{BASE}/git-pull",
    ),
    (
        "git-recipes", "git recipe: find which commit introduced a bug (bisect)",
        "Binary search through history. "
        "(1) Start: `git bisect start`; "
        "(2) Mark current as bad: `git bisect bad`; "
        "(3) Mark last known good: `git bisect good v1.0`; "
        "(4) Git checks out a midpoint — test it, then `git bisect good` or `git bisect bad`; "
        "(5) Repeat until git says 'X is the first bad commit'. "
        "(6) Exit: `git bisect reset`. "
        "Automate with test script: `git bisect run ./test.sh`.",
        f"{BASE}/git-bisect",
    ),
    (
        "git-recipes", "git recipe: find when a line/function changed (blame + log -L)",
        "Per-line history. `git blame file.py` shows author/SHA per line. "
        "For specific lines: `git blame -L 10,20 file.py`. "
        "Ignore whitespace changes: `git blame -w`. "
        "Track moves/copies: `git blame -M -C file.py`. "
        "Full history of a function: `git log -L :function_name:file.py`. "
        "Skip junk commits (formatter/rename): `git blame --ignore-rev <sha>`.",
        f"{BASE}/git-blame",
    ),
    (
        "git-recipes", "git recipe: search history for a string (pickaxe)",
        "Find when text was added/removed. `git log -S 'function foo' --source --all`. "
        "Difference: `-S` matches lines where occurrence count changed (add/remove); `-G <regex>` matches any matching change. "
        "Limit to file: `git log -S 'foo' -- src/`. "
        "Show patches: `git log -p -S 'foo'`. "
        "Useful for 'who removed this code?' or 'when did this API appear?'.",
        f"{BASE}/git-log",
    ),
    (
        "git-recipes", "git recipe: recover deleted commits with reflog",
        "Reflog tracks every HEAD movement (incl. branch deletes, hard resets). "
        "(1) See history: `git reflog` (last 30 days by default); "
        "(2) Find the lost commit SHA in the list; "
        "(3) Create branch at it: `git checkout -b recovered abc123` or `git branch recovered abc123`; "
        "(4) For a deleted branch: `git reflog` may show last tip — `git branch recovered <last-sha>`. "
        "Reflog is local-only and gc'd after ~30 days.",
        f"{BASE}/git-reflog",
    ),
    (
        "git-recipes", "git recipe: reset to a specific commit (soft / mixed / hard)",
        "Move HEAD to <commit>, optionally affect index + working tree. "
        "`git reset --soft <sha>` = move HEAD only (staged changes kept). "
        "`git reset <sha>` (mixed, default) = HEAD + unstage (working tree kept). "
        "`git reset --hard <sha>` = HEAD + index + working tree all reset (DESTROYS uncommitted changes). "
        "Common: `git reset --hard origin/main` (discard local, match remote). "
        "DON'T `--hard` if you might want recent work back — use stash first.",
        f"{BASE}/git-reset",
    ),
    (
        "git-recipes", "git recipe: ignore a tracked file going forward",
        ".gitignore only ignores UNTRACKED files. For tracked: "
        "(1) Add pattern to .gitignore; "
        "(2) Stop tracking: `git rm --cached file.txt` (keeps on disk); "
        "(3) Commit: `git commit -m 'stop tracking file.txt'`. "
        "Bulk untrack but keep: `git rm -r --cached path/`. "
        "Local-only ignore (not committed): `.git/info/exclude` or `git update-index --skip-worktree file.txt`.",
        f"{BASE}/git-rm",
    ),
    (
        "git-recipes", "git recipe: configure SSH vs HTTPS for a remote",
        "Switch protocols. "
        "Current: `git remote -v`. "
        "Set to SSH: `git remote set-url origin git@github.com:user/repo.git`. "
        "Set to HTTPS: `git remote set-url origin https://github.com/user/repo.git`. "
        "When to use SSH: avoiding tokens, work on home machine. "
        "When to use HTTPS: behind corporate firewall blocking SSH, GitHub PAT setup easier.",
        f"{BASE}/git-remote",
    ),
    (
        "git-recipes", "git recipe: add multiple remotes (fork + upstream)",
        "Standard fork workflow. "
        "(1) Clone your fork: `git clone git@github.com:you/repo.git`; "
        "(2) Add original as 'upstream': `git remote add upstream https://github.com/original/repo.git`; "
        "(3) List: `git remote -v` shows both; "
        "(4) Stay synced: `git fetch upstream && git rebase upstream/main` then `git push origin main`; "
        "(5) Open PR: GitHub auto-suggests from your fork to upstream.",
        f"{BASE}/git-remote",
    ),
    (
        "git-recipes", "git recipe: update fork from upstream",
        "Pull latest from original repo into your fork. "
        "(1) `git fetch upstream`; "
        "(2) `git switch main` (or master); "
        "(3) `git rebase upstream/main` (or `git merge upstream/main`); "
        "(4) `git push origin main` to update your fork on GitHub. "
        "Use `--force-with-lease` if rebase rewrote your commits.",
        f"{BASE}/git-fetch",
    ),
    (
        "git-recipes", "git recipe: resolve a merge conflict",
        "(1) Identify conflicted files: `git status` (or `git diff --name-only --diff-filter=U`); "
        "(2) Open each file — look for `<<<<<<< HEAD ... ======= ... >>>>>>> branch` markers; "
        "(3) Edit to desired result, remove all markers; "
        "(4) Stage: `git add <file>` (for each); "
        "(5) Finish: `git merge --continue` (or `git commit`). "
        "Visual tool: `git mergetool` (configure with `merge.tool = vscode` or similar). "
        "Abort: `git merge --abort`.",
        f"{BASE}/git-merge",
    ),
    (
        "git-recipes", "git recipe: abort an in-progress merge / rebase / cherry-pick",
        "Get back to clean state. "
        "Merge: `git merge --abort`. "
        "Rebase: `git rebase --abort`. "
        "Cherry-pick: `git cherry-pick --abort`. "
        "Revert: `git revert --abort`. "
        "Check what's in progress: `git status` (top line tells you). "
        "If --abort doesn't work (rare): `git reset --hard ORIG_HEAD` (gets you back to pre-operation state).",
        f"{BASE}/git-merge",
    ),
    (
        "git-recipes", "git recipe: clean untracked files",
        "Remove files git isn't tracking. "
        "Preview (dry-run): `git clean -n` (or `-dn` for dirs). "
        "Execute: `git clean -fd` (-f required, -d also removes dirs). "
        "Also remove ignored files: `-x` (e.g., node_modules). "
        "Interactive: `git clean -i`. "
        "DESTRUCTIVE — files are not in any commit and not recoverable.",
        f"{BASE}/git-clean",
    ),
    (
        "git-recipes", "git recipe: show all branches containing a commit",
        "Where is this commit? "
        "Local branches: `git branch --contains abc1234`. "
        "Remote branches: `git branch -r --contains abc1234`. "
        "All branches: `git branch -a --contains abc1234`. "
        "Tags: `git tag --contains abc1234`. "
        "Useful for 'is this fix in release branch?'.",
        f"{BASE}/git-branch",
    ),
    (
        "git-recipes", "git recipe: diff between branches / commits / files",
        "Working tree vs index: `git diff`. "
        "Index vs HEAD: `git diff --cached` (or `--staged`). "
        "Between branches: `git diff main..feature` (or `main...feature` for fork-point). "
        "Specific files: `git diff main feature -- src/file.py`. "
        "Stats only: `git diff --stat`. "
        "Name only: `git diff --name-only`. "
        "Ignore whitespace: `-w`.",
        f"{BASE}/git-diff",
    ),
    (
        "git-recipes", "git recipe: search log by author / date / message",
        "Filter `git log`. "
        "Author: `git log --author='alice'`. "
        "After date: `git log --since='2 weeks ago'` (or `--after='2024-01-01'`). "
        "Before: `--before='2024-12-31'`. "
        "Message: `git log --grep='fix.*memory leak'`. "
        "Combine: `git log --author=alice --since='1 month'`. "
        "Compact: `--oneline`. "
        "Graphical: `--graph --all --decorate --oneline`.",
        f"{BASE}/git-log",
    ),
    (
        "git-recipes", "git recipe: submodule add / update / remove",
        "Nest a repo inside a repo. "
        "Add: `git submodule add <url> path/`. "
        "Initial clone of parent: `git clone --recurse-submodules <parent-url>`. "
        "Update submodules: `git submodule update --init --recursive`. "
        "Pull latest in submodules: `git submodule update --remote`. "
        "Remove: `git submodule deinit -f path/ && git rm -f path/ && rm -rf .git/modules/path/`. "
        "Prefer subtree merging or package manager unless you really need submodules — they trip up most workflows.",
        f"{BASE}/gitsubmodules",
    ),
    (
        "git-recipes", "git recipe: sign commits + tags with GPG/SSH",
        "Proves authorship. "
        "GPG: `gpg --gen-key` → `git config --global user.signingkey <key-id>` → `git config --global commit.gpgsign true`. "
        "SSH (Git 2.34+): `git config --global gpg.format ssh` → `git config --global user.signingkey ~/.ssh/id_ed25519.pub`. "
        "Sign one commit: `git commit -S -m '...'`. "
        "Sign tag: `git tag -s v1.0 -m 'release'`. "
        "Verify: `git log --show-signature`. "
        "On GitHub: upload your GPG/SSH signing key for 'Verified' badge.",
        f"{BASE}/git-commit",
    ),
    (
        "git-recipes", "git recipe: create + apply patch (format-patch + am)",
        "Email-style patches. "
        "Create from last 3 commits: `git format-patch -3 HEAD` (produces 0001-*.patch files). "
        "Single file from a range: `git format-patch master..feature --stdout > feature.patch`. "
        "Apply: `git am feature.patch` (preserves author + message + date). "
        "Conflicts: resolve, `git add`, then `git am --continue`. "
        "Plain diff (no metadata): `git diff > my.diff` and `git apply my.diff`.",
        f"{BASE}/git-config",
    ),
    (
        "git-recipes", "git recipe: configure git aliases for common commands",
        "Shortcuts. "
        "`git config --global alias.co checkout`. "
        "`git config --global alias.br branch`. "
        "`git config --global alias.lg \"log --graph --oneline --decorate --all\"`. "
        "`git config --global alias.unstage 'reset HEAD --'`. "
        "`git config --global alias.last 'log -1 HEAD'`. "
        "Shell-out: `git config --global alias.dad '!curl https://icanhazdadjoke.com'` (the `!` runs raw shell).",
        f"{BASE}/git-config",
    ),
    (
        "git-recipes", "git recipe: speed up clone of large repos (shallow / partial / sparse)",
        "Big-monorepo techniques. "
        "Shallow (just recent commits): `git clone --depth 1 <url>`. "
        "Partial (skip large blobs): `git clone --filter=blob:none <url>` (downloads blobs only when needed). "
        "Sparse checkout (subset of paths): `git clone --no-checkout <url>`, then `git sparse-checkout init --cone && git sparse-checkout set src/ tests/`. "
        "Single branch: `--single-branch --branch main`. "
        "Combine all: `git clone --filter=blob:none --no-checkout --single-branch <url>`.",
        f"{BASE}/git-clone",
    ),
    (
        "git-recipes", "git recipe: list files changed in a commit / between commits",
        "Just the file list. "
        "In one commit: `git show --name-only abc123` (or `--stat` for +/- counts). "
        "Between commits: `git diff --name-only abc..xyz`. "
        "Since branch diverged: `git diff --name-only main...feature`. "
        "Files in entire branch's history: `git log --name-only --pretty=format: feature | sort -u`. "
        "By file type: pipe to `grep '\\.py$'`.",
        f"{BASE}/git-show",
    ),
    (
        "git-recipes", "git recipe: show file content at a specific commit / branch",
        "Time-travel a single file. "
        "`git show <ref>:<path>` prints content. "
        "Example: `git show HEAD~5:src/main.py`. "
        "From another branch: `git show feature:src/main.py`. "
        "Restore to working tree: `git restore --source=<ref> <path>` (modern) or `git checkout <ref> -- <path>` (legacy). "
        "Side-by-side diff: `git difftool <ref> -- <path>`.",
        f"{BASE}/git-show",
    ),
    (
        "git-recipes", "git recipe: hook into git events (pre-commit, pre-push, post-merge)",
        "Local scripts that run on git operations. "
        "Hooks live in `.git/hooks/` (not version-controlled). "
        "Common: `pre-commit` (lint/test before commit), `commit-msg` (enforce message format), `pre-push` (run tests before push). "
        "Make executable: `chmod +x .git/hooks/pre-commit`. "
        "Share across team: use the 'pre-commit' framework (`pip install pre-commit`) — config in `.pre-commit-config.yaml`, installed with `pre-commit install`.",
        f"{BASE}/githooks",
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
            evidence_id=f"ev-git-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-git-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="git-catalog",
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
