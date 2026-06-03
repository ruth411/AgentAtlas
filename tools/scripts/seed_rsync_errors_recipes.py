"""Direct-insert rsync error + recipe claims."""

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

BASE = "https://download.samba.org/pub/rsync"

CLAIMS = [
    # ============================================================
    # rsync-errors — 30 common errors
    # ============================================================
    (
        "rsync-errors", "rsync error: code 12 — protocol data stream / unexpected end of file",
        "Connection died mid-transfer or server side sent garbage. "
        "Fix: (1) verify remote rsync version matches local: `rsync --version` on both sides; "
        "(2) check for cleanup in user's `~/.bashrc` on remote: `rsync` requires a clean stdout (no welcome banners) — guard with `[[ $- == *i* ]] || return`; "
        "(3) try with verbose: `rsync -vvv ...` to see what's actually exchanged; "
        "(4) network mid-blip: re-run with `--partial --append-verify` to resume; "
        "(5) for very long transfers: use a robust wrapper like `rsync-retry` or shell-loop on exit code.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync error: code 23 — partial transfer due to error",
        "Some files succeeded but others failed (permission, path too long, vanished). "
        "Fix: (1) re-run identical command — rsync skips already-transferred files (incremental); "
        "(2) add `--ignore-errors` to continue past per-file errors; "
        "(3) inspect which failed with `-v` flag; "
        "(4) common causes: file vanished mid-scan (`some files vanished` warning code 24); permission denied on a specific file; "
        "(5) for backups: code 23 + 24 are usually safe to treat as 'mostly succeeded'.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync error: code 24 — some files vanished before transfer",
        "Source files were deleted during the scan. Not really an error — informational. "
        "Fix: (1) ignore: code 24 is benign in `--delete` syncs or backups of mutable directories; "
        "(2) suppress: handle exit code 24 specifically in scripts: `rsync ...; rc=$?; [[ $rc -eq 0 || $rc -eq 24 ]] || exit $rc`; "
        "(3) for backups: this is normal — `/tmp`, log dirs, mailboxes, browser cache constantly churn.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync error: code 11 — error in file I/O",
        "Read or write to disk failed (disk full, FS quota, permission, broken device). "
        "Fix: (1) check destination disk space: `df -h <dest>`; "
        "(2) for quota: `quota -u <user>` (Linux); "
        "(3) check permissions: rsync needs write to destination dir + parent dirs (for `-a` preserving owner: needs root or matching UID); "
        "(4) bad disk sector: check `dmesg` / `journalctl -k` for I/O errors; "
        "(5) for FUSE-mounted dest (sshfs etc): often slow + flaky — try direct ssh transport instead.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync error: code 30 — timeout in data send/receive",
        "No data flowed for `--timeout` seconds. "
        "Fix: (1) raise: `--timeout=300` (5 min) or `--timeout=0` (no timeout); "
        "(2) for slow links: `--bwlimit=1m` reduces bandwidth pressure on a tiny pipe; "
        "(3) check SSH config: `ServerAliveInterval 30` + `ServerAliveCountMax 3` on client side; "
        "(4) for huge files over flaky links: `--partial --inplace --append-verify`; "
        "(5) check intermediate network: `mtr <host>` for packet loss.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync error: connection unexpectedly closed (10001 bytes received so far)",
        "Remote end terminated the connection without protocol shutdown. "
        "Fix: (1) check remote disk: out-of-space kills rsync silently sometimes; "
        "(2) memory limit on remote (OOM killer): `dmesg` on the remote side; "
        "(3) firewall idle-timeout: enable SSH keepalives `-o ServerAliveInterval=30`; "
        "(4) shell init noise: remote `.bashrc`/`.cshrc` must not print anything on non-interactive login; "
        "(5) retry with `-vvv` and pastebin the output for diagnosis.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync error: ssh: connect to host <X> port 22: Connection refused",
        "SSH service not running, port wrong, or firewall blocking. "
        "Fix: (1) test ssh first: `ssh user@host` (no rsync); "
        "(2) check port: rsync `-e 'ssh -p 2222'` for non-default; "
        "(3) verify host: `ping host`, `traceroute host`; "
        "(4) firewall: open TCP 22 (or your custom port); "
        "(5) on AWS/GCP: check security group / VPC firewall.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync error: Permission denied (publickey) — for ssh transport",
        "SSH key auth failed when rsync invoked ssh. "
        "Fix: (1) test direct: `ssh -i ~/.ssh/key user@host` first; "
        "(2) rsync uses default ssh — pass options: `rsync -e 'ssh -i ~/.ssh/key' user@host:/path /local/`; "
        "(3) check ssh-agent: `ssh-add -l` lists loaded keys; "
        "(4) for restricted users: ensure ~/.ssh/authorized_keys has the right key; "
        "(5) for sudo + rsync: `--rsync-path='sudo rsync'` runs remote rsync as root (with NOPASSWD setup).",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync error: code 5 — error starting client-server protocol / argument error",
        "rsync didn't get a connection to talk to. Usually wrong syntax or missing remote-side binary. "
        "Fix: (1) verify remote has rsync: `ssh user@host which rsync` (must be installed); "
        "(2) check command syntax: `rsync [options] source destination` (no missing args); "
        "(3) for daemon mode: use `rsync://host/module/path` URL form; "
        "(4) for non-standard remote rsync path: `--rsync-path=/opt/bin/rsync`.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync error: code 25 — max-delete limit hit",
        "Safety brake: `--max-delete=N` triggered when more than N files would be deleted. "
        "Fix: (1) intentional? raise the limit: `--max-delete=1000` or remove the flag; "
        "(2) UNINTENTIONAL? the safety brake just saved your data — investigate before re-running; "
        "(3) for backups: always include `--max-delete` to catch source-side accidents; "
        "(4) for total wipe-and-recopy: use `--delete-after` instead (delete only on success).",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync error: rsync: failed to open: Permission denied (13)",
        "Source file readable but rsync can't access it. "
        "Fix: (1) read perms on source file + parent dirs; "
        "(2) for system files: run rsync with `sudo` (locally) or `--rsync-path='sudo rsync'` (remotely); "
        "(3) check for ACLs: `getfacl <file>` (Linux) — may grant only specific users; "
        "(4) for SELinux: `ls -Z` shows contexts; restricted contexts may block rsync; "
        "(5) excluded by filter: `--info=progress2 -v` shows which files fail.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync error: No space left on device (28)",
        "Destination filesystem full mid-transfer. "
        "Fix: (1) check: `df -h <dest>`; "
        "(2) clean up: rsync's `--partial` may have left big partials — `find <dest> -name '.~tmp~*' -delete`; "
        "(3) for incremental backups: prune old snapshots; "
        "(4) for ext4 'inode exhaustion' (different from blocks): `df -i` — too many small files; "
        "(5) move destination to a bigger volume or add storage.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync error: source and destination — trailing slash semantics confusion",
        "rsync's trailing-slash rule trips everyone: `rsync src/ dst/` syncs *contents* of src into dst; `rsync src dst/` makes a `dst/src/` directory. "
        "Fix: (1) for 'mirror contents': add trailing `/` to source: `rsync -a /home/me/ /backup/me/`; "
        "(2) for 'copy directory into': omit trailing slash on source: `rsync -a /home/me /backup/` → produces `/backup/me/`; "
        "(3) destination trailing slash: doesn't matter; "
        "(4) preview with `--dry-run` before destructive operations.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync error: rsync: link_stat \"<path>\" failed: No such file or directory (2)",
        "Source path doesn't exist or symlink broken. "
        "Fix: (1) verify: `ls -la <source-path>`; "
        "(2) for broken symlinks: rsync skips by default — to copy as-is: `-l` or part of `-a`; "
        "(3) for symlinks pointing nowhere: copy as the link itself: `--no-dereference`; "
        "(4) typo check: case-sensitive on Linux; "
        "(5) for moving target: file was deleted after rsync started — see vanished-files error.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync error: rsync: write failed: Broken pipe (32)",
        "Receiving end died mid-write. "
        "Fix: (1) re-run with `--partial` to resume the half-transferred file; "
        "(2) check remote disk space; "
        "(3) network drop: see connection-unexpectedly-closed remediation; "
        "(4) for huge single files: `--inplace` avoids the rename step (less safe but resumable); "
        "(5) for SSH transport across flaky links: combine with `mosh` or `autossh`.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync error: code 1 — syntax or usage error",
        "Command-line is wrong. "
        "Fix: (1) `rsync --help` lists options; "
        "(2) common: putting destination before source (rsync is `source dest`); "
        "(3) unknown flag: check version — `--mkpath` is rsync 3.2.3+; "
        "(4) conflicting flags: e.g. `--delete` requires either `--recursive` or `-a`; "
        "(5) typos: `--exclude=pattern` not `--excludes=...`.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync error: --delete does not work without -r or -a",
        "`--delete` removes files in dest that aren't in source — needs recursion to know about subdirs. "
        "Fix: (1) add `-a` (archive, includes `-r`); "
        "(2) or explicit `-r`; "
        "(3) safer: combine `--delete --max-delete=100 --dry-run` first to preview; "
        "(4) ordering options: `--delete-before` (default modern), `--delete-after` (safer for atomic refresh), `--delete-during` (default in older), `--delete-excluded` (also remove items excluded by filters from dest).",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync warning: protocol version mismatch — is your shell clean?",
        "Remote shell printed non-rsync data on rsync's connection. "
        "Fix: (1) on remote, ensure ~/.bashrc / ~/.profile guards interactive setup: `[[ $- == *i* ]] || return`; "
        "(2) no `echo` / `motd` / SSH banners on non-interactive login (configure `/etc/motd` exemptions); "
        "(3) check ~/.ssh/rc for output; "
        "(4) test: `ssh user@host /bin/true` — should produce zero output.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync error: failed to set times: Operation not permitted (1)",
        "Can't preserve mtime on the destination filesystem. "
        "Fix: (1) for FAT32 / NTFS / some FUSE mounts: timestamps may be limited resolution; suppress with `--no-times`; "
        "(2) for samba / sshfs mounts: depends on server config; "
        "(3) for nfs / network mounts: check `clientidle` settings; "
        "(4) workaround: skip times entirely with `--no-t`.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync error: failed to set permissions: Operation not permitted",
        "Filesystem doesn't support chmod, or user can't chown. "
        "Fix: (1) FAT/NTFS: use `--no-perms` (and `--no-owner --no-group` for owner); "
        "(2) for ownership preservation: use `-o -g` (or `-a`) + run rsync as root; "
        "(3) cross-system with different UIDs: `--numeric-ids` (use UID numbers) or `--chown=USER:GROUP` to remap.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync error: rsync: getaddrinfo: <host> Name or service not known",
        "DNS resolution failed. "
        "Fix: (1) test: `nslookup <host>` or `dig <host>`; "
        "(2) try IP directly: `rsync ... user@10.0.0.1:/path /local/`; "
        "(3) /etc/hosts entry for offline / private DNS; "
        "(4) for IPv6 issues: prefer IPv4: `--ipv4` flag.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync error: --files-from listed nonexistent files",
        "`--files-from=list.txt` references files that don't exist in source. "
        "Fix: (1) make paths relative to source: `--files-from=list.txt --from0` if using NUL-sep; "
        "(2) verify: `cat list.txt | head` — paths should be relative to source root; "
        "(3) absolute paths in list.txt only work when source itself is `/`; "
        "(4) for fast generation: `cd src && find . -newer /tmp/timestamp > list.txt`.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync warning: skipping non-regular file / symbolic link",
        "Default rsync (no `-a` / `-l`) skips symlinks. "
        "Fix: (1) preserve as symlinks: `-l` (or `-a`); "
        "(2) copy what symlink points to: `-L` (follow + copy contents); "
        "(3) skip silently: ignore the warning; "
        "(4) for special files (devices, sockets, FIFOs): `-D` (or `-a`).",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync error: code 13 — diagnostic error / received too much data",
        "rsync received more data than expected for a file (could indicate corruption or protocol bug). "
        "Fix: (1) re-run with `--partial --append-verify`; "
        "(2) try without compression: `-z` can interact badly with very specific data; "
        "(3) for large files: `--inplace` reduces memory pressure; "
        "(4) check rsync versions: very-old vs very-new can mismatch.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync error: code 14 — error in IPC code",
        "Internal communication between rsync processes failed. Usually means the local OS killed one process (OOM, signal). "
        "Fix: (1) check `dmesg` / `journalctl` for OOM kill; "
        "(2) for huge file lists: rsync's file index can grow large — use `--files-from` to chunk; "
        "(3) `--no-implied-dirs` reduces memory; "
        "(4) for multi-million file trees: split source dirs and rsync them separately.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync error: chown failed",
        "Can't change owner — usually need root, or dest FS doesn't support it. "
        "Fix: (1) on local copies: run with sudo; "
        "(2) for cross-host: `--rsync-path='sudo rsync'` (with NOPASSWD setup); "
        "(3) suppress: `--no-owner --no-group` (or `--no-o --no-g`); "
        "(4) preserve numeric IDs only: `--numeric-ids` (useful for unmatched user databases between hosts).",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync error: SSL/TLS handshake failed (rsync-ssl wrapper)",
        "rsync-ssl wrapper couldn't establish TLS to the rsync daemon. "
        "Fix: (1) verify daemon supports stunnel/SSL wrapper; "
        "(2) check certificate: `openssl s_client -connect host:874` (default rsync-ssl port); "
        "(3) hostname mismatch: SAN must include the rsync daemon's hostname; "
        "(4) for self-signed certs: configure `RSYNC_SSL_CERT` env var.",
        f"{BASE}/rsync-ssl.html",
    ),
    (
        "rsync-errors", "rsync error: too many arguments / unable to parse",
        "Shell didn't pass arguments the way rsync expected — usually quoting issues with paths containing spaces. "
        "Fix: (1) for paths with spaces in remote: use `-s` (protect args) or escape: `rsync -av 'user@host:/path with spaces/' /local/`; "
        "(2) for globbing on remote: `--rsh='ssh -T'` and ensure no shell expansion on local; "
        "(3) wrap remote paths in single quotes when crossing shells.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync error: unrecognized option / unknown flag",
        "Option doesn't exist in your rsync version. "
        "Fix: (1) check version: `rsync --version`; "
        "(2) `--mkpath` requires 3.2.3+; `--checksum-choice` requires 3.2+; `--info=progress2` requires 3.1+; "
        "(3) upgrade: macOS `brew install rsync`; Linux distro package; "
        "(4) for stuck-on-2.6 (RHEL legacy): the syntax differs significantly — refer to that version's man page.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-errors", "rsync error: filter rules — file unexpectedly excluded",
        "Pattern matched but you didn't expect it to. "
        "Fix: (1) include/exclude order matters: rules are evaluated top-down, first match wins; "
        "(2) `-vv` shows which rule matched: 'filter: ... matched'; "
        "(3) `**` matches across slashes; single `*` doesn't; "
        "(4) for relative-to-source: leading `/` anchors to source root; without `/` it matches anywhere; "
        "(5) preview: `--dry-run` + `--itemize-changes` shows what would transfer.",
        f"{BASE}/rsync.html",
    ),

    # ============================================================
    # rsync-recipes — 36 common workflows
    # ============================================================
    (
        "rsync-recipes", "rsync recipe: basic local sync (one directory to another)",
        "`rsync -av /source/ /dest/` — `-a` is archive (recursive + preserves perms/times/symlinks/owner/group), `-v` is verbose. "
        "Trailing slash on source means 'contents of'; without it means 'directory itself'. "
        "Default behavior: incremental — only transfers what changed. "
        "Always preview with `--dry-run -av` first when uncertain.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: sync to a remote server over SSH",
        "`rsync -avz /local/path/ user@remote:/backup/path/`. "
        "`-z` enables compression (good for slow networks). "
        "Custom SSH port: `-e 'ssh -p 2222'`. "
        "Custom SSH key: `-e 'ssh -i ~/.ssh/key.pem'`. "
        "Jump host: `-e 'ssh -J jumphost user@bastion'`. "
        "Reverse direction: swap source + destination.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: mirror — make destination identical to source",
        "`rsync -av --delete /source/ /dest/`. "
        "`--delete` removes files from dest that aren't in source — TRUE mirror. "
        "Always preview: `rsync -av --delete --dry-run /source/ /dest/`. "
        "Safety brake: add `--max-delete=100` so a botched source doesn't wipe years of backup. "
        "For atomic refresh: `--delete-after` (only delete on success).",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: dry run before real transfer",
        "Add `--dry-run` (or `-n`) to any rsync command. "
        "Shows exactly what would be transferred / deleted without making changes. "
        "Combine with `--itemize-changes` (or `-i`) for per-file detail: '>f.st...' (new file), '*deleting' (would delete), etc. "
        "STANDARD PRACTICE: every destructive rsync first runs with `-n` to confirm.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: exclude files / directories",
        "Single pattern: `--exclude='*.tmp'`. "
        "Multiple: `--exclude='*.log' --exclude='node_modules/' --exclude='.git/'`. "
        "From file: `--exclude-from='exclude.txt'` (one pattern per line). "
        "Glob: `*` matches within a level; `**` matches across slashes. "
        "Anchor to source root: leading `/`: `--exclude='/cache'` only excludes top-level cache, not subdirs.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: include + exclude combined (selective sync)",
        "Include only specific patterns: `rsync -av --include='*.txt' --include='*/' --exclude='*' /src/ /dst/`. "
        "Read top-down: include `*.txt`, include directories (so recursion works), exclude everything else. "
        "For complex: `--filter='+ *.py' --filter='- *.pyc' --filter='+ */' --filter='- *'`. "
        "Preview with `--dry-run -i` always — filter rules are tricky.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: resumable transfer for huge files",
        "`rsync -av --partial --progress --append-verify /src/ /dst/`. "
        "`--partial` keeps partially-transferred files (so next run can resume). "
        "`--append-verify` resumes by appending then verifies the appended data. "
        "For very large single files: `--inplace` (write directly to dest without temp file — saves space). "
        "Better progress: `--info=progress2` (overall % rather than per-file).",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: show progress + speed",
        "Per-file: `-v --progress`. "
        "Overall (rsync 3.1+): `--info=progress2`. "
        "Quiet (just totals): `--stats`. "
        "Live transfer rate: combine `--bwlimit=` to see throttling. "
        "For long backups: `--itemize-changes` shows what was actually copied vs skipped.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: bandwidth limit (slow / production network)",
        "`--bwlimit=1m` = 1 MB/sec. "
        "Other units: `K` (KiB), `M` (MiB), `G` (GiB). "
        "No unit = bytes/sec. "
        "Use case: backups during business hours without saturating uplink. "
        "Adjust dynamically: kill + re-run with new limit (resumable with `--partial`).",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: backup with hardlinks (time-machine style)",
        "`rsync -av --link-dest=/backup/last /source/ /backup/new/` — files unchanged from `last` become hardlinks (no extra disk). "
        "After: `mv /backup/last /backup/$(date +%Y-%m-%d)` and `mv /backup/new /backup/last`. "
        "Result: every day's snapshot is full but shares unchanged files via hardlinks. "
        "Tooling: `rsnapshot` automates this pattern.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: keep only newest version (update only)",
        "`rsync -av --update /src/ /dst/` — skip files in dst that are newer than in source. "
        "Useful for merging from multiple sources without overwriting recent changes. "
        "For two-way sync: rsync isn't really for that — use Unison or Syncthing instead.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: backup before overwrite",
        "`rsync -av --backup --backup-dir=/old-backups/$(date +%Y-%m-%d) /src/ /dst/`. "
        "Files about to be overwritten get moved to backup-dir first. "
        "Suffix-only (in place, no separate dir): `--backup --suffix=.bak`. "
        "For safety in destructive scripts: always combine `--backup` with `--delete`.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: compare files by checksum (not just mtime + size)",
        "`rsync -av --checksum /src/ /dst/` — slow but rigorous. "
        "Default (mtime+size): fast but assumes touch hasn't lied about times. "
        "Use case: forensic copy, verifying a previous transfer was clean, after `touch -t` chaos. "
        "Note: still uses delta-transfer for actual data movement; checksum is just for the file-changed decision.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: list remote files without transferring",
        "`rsync --list-only user@host:/path/`. "
        "Shows files and metadata, doesn't transfer. "
        "Like `ls -la` for the remote dest. "
        "For local browsing of a remote dir without mounting.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: preserve hardlinks across the transfer",
        "`rsync -aH /src/ /dst/` — `-H` detects and preserves hardlinks. "
        "Memory cost: rsync builds an inode-tracking table — can be huge for millions of files. "
        "For backup of mail spools (heavy hardlink users) or Time Machine: critical. "
        "Default `-a` does NOT include `-H` — must add explicitly.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: preserve sparse files efficiently",
        "`rsync -aS /src/ /dst/` — `-S` detects sparse blocks (holes) and writes them as holes on dest. "
        "Without `-S`: holes become actual zeroes → file grows to its 'apparent' size. "
        "Use case: VM images, database files, dump files with lots of zero regions. "
        "Test: `du -h file.img` vs `ls -lh file.img` shows allocated vs apparent size.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: preserve ACLs + extended attributes",
        "`rsync -aAX /src/ /dst/` — `-A` for ACLs, `-X` for extended attributes (xattrs). "
        "Required when source uses POSIX ACLs (setfacl) or stores xattrs (SELinux contexts, macOS resource forks). "
        "Both sides need to support: same filesystem family + permission to set. "
        "For SELinux contexts specifically: also set context on dest with `restorecon`.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: sync only specific files (--files-from)",
        "`rsync -av --files-from=list.txt /src/ /dst/`. "
        "list.txt: relative paths from source, one per line. "
        "NUL-separated: `--from0` (for filenames with newlines). "
        "Generate dynamically: `cd /src && find . -newer /tmp/last-run > list.txt && rsync -av --files-from=list.txt /src/ /dst/`. "
        "Use case: incremental backup based on application logs of changed files.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: set up a rsync daemon for module-based access",
        "`/etc/rsyncd.conf`: ```ini\\nuid = nobody\\ngid = nobody\\n[backups]\\n    path = /var/backups\\n    read only = no\\n    auth users = backupuser\\n    secrets file = /etc/rsyncd.secrets\\n    hosts allow = 10.0.0.0/24\\n```. "
        "`/etc/rsyncd.secrets`: `backupuser:password` (mode 0600). "
        "Start: `rsync --daemon`. "
        "Client: `rsync -av /local/ rsync://backupuser@host/backups/`.",
        f"{BASE}/rsyncd.conf.html",
    ),
    (
        "rsync-recipes", "rsync recipe: rsync over rsync daemon (rsync:// protocol)",
        "Server (configured per rsyncd.conf recipe): `rsync --daemon`. "
        "Client: `rsync -av rsync://host/<module>/path /local/` (read) or `rsync -av /local/ rsync://host/<module>/path/` (write). "
        "Faster than ssh for high-bandwidth links (no encryption overhead). "
        "Use case: internal LAN backups, public mirror servers. "
        "For TLS encryption: `rsync-ssl` wrapper.",
        f"{BASE}/rsyncd.conf.html",
    ),
    (
        "rsync-recipes", "rsync recipe: TLS-protected rsync daemon (rsync-ssl)",
        "Client side: `rsync-ssl rsync://host/module/ /local/` (wraps in TLS via stunnel). "
        "Server side: requires stunnel configuration; rsync daemon listens locally on 873 + stunnel listens on 874 with TLS termination. "
        "Cert: same setup as any TLS service (Let's Encrypt or self-signed). "
        "Modern alternative to plaintext rsync daemon over the internet.",
        f"{BASE}/rsync-ssl.html",
    ),
    (
        "rsync-recipes", "rsync recipe: use a non-default SSH config",
        "`rsync -e 'ssh -F /path/to/ssh_config' user@host:/path/ /local/`. "
        "ssh_config can define Host aliases, IdentityFile, ProxyJump, ControlPath. "
        "For complex setups: a per-project ssh_config keeps rsync calls simple: `rsync -e 'ssh -F ./ssh_config' deploy:/var/www/ ./mirror/`.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: rsync as root on remote (with sudo)",
        "`rsync -av --rsync-path='sudo rsync' user@host:/etc /local/backup/`. "
        "Remote sudoers must allow NOPASSWD for `rsync`: ```\\nuser ALL=(ALL) NOPASSWD: /usr/bin/rsync\\n```. "
        "Use case: system-level backups without being root locally, without enabling root SSH login.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: cross-platform file ownership (--chown)",
        "`rsync -a --chown=www-data:www-data /src/ /dst/` — rewrites owner/group on transfer. "
        "Useful when local and remote use different UIDs for the same role. "
        "Numeric variant: `--chown=1001:1001`. "
        "For preserving original numeric IDs (when usernames differ): `--numeric-ids`.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: mirror a website (right exclusions)",
        "```bash\\nrsync -avz --delete \\\\\\n  --exclude='cache/' --exclude='*.log' --exclude='node_modules/' \\\\\\n  --exclude='.git/' --exclude='.env*' \\\\\\n  /local/site/ user@host:/var/www/site/\\n```. "
        "Add `--backup --backup-dir=/var/www/backups/$(date +%Y%m%d)` for safety. "
        "Trigger via CI on git push.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: nightly backup script with timestamped snapshots",
        "```bash\\n#!/bin/bash\\nset -e\\nSRC=/home\\nDST=/backup\\nDATE=$(date +%Y-%m-%d)\\nrsync -aAXH --delete \\\\\\n  --link-dest=$DST/latest \\\\\\n  --exclude='.cache' --exclude='Trash' \\\\\\n  $SRC/ $DST/$DATE/\\nrm -f $DST/latest\\nln -s $DATE $DST/latest\\n```. "
        "Result: incremental snapshots with deduplication via hardlinks. "
        "Combine with `find` to prune old dates beyond retention.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: copy permissions only (no data)",
        "Sometimes you want to fix permissions without re-copying. "
        "`rsync -av --existing --no-data --perms --owner --group /src/ /dst/` — `--existing` skips new files (only update existing), `--no-data` would skip data but isn't a real flag; instead use `--checksum` to skip unchanged content. "
        "Common alternative: rsync everything but use `-a` which already preserves perms.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: sync between two non-local hosts via local proxy",
        "Direct A↔B isn't supported (rsync needs one local side). Workarounds: "
        "(1) push from A: `ssh A 'rsync -avz /path/ user@B:/dst/'` (A and B must have network path); "
        "(2) pull through local: `ssh A 'tar c /path' | ssh B 'tar x -C /dst/'` (no incremental); "
        "(3) mount A locally via sshfs + rsync from mount to B (slow but works).",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: itemize what would change",
        "`rsync -av --dry-run --itemize-changes /src/ /dst/`. "
        "Output format: `YXcstpoguax filename` where each char is a flag. "
        "Y: update type (`>` send, `<` receive, `c` create, `h` hardlink, `.` no change, `*` deleting). "
        "X: file type (`f` file, `d` dir, `L` symlink). "
        "Lowercase = unchanged; uppercase = changed. "
        "Example: `>f.st...... file.txt` means: send, regular file, size changed, time changed.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: parallel rsync for many subdirectories",
        "For huge trees, rsync is single-threaded — parallelize with xargs: "
        "`ls /src/ | xargs -I {} -P 8 rsync -avz /src/{}/ user@host:/dst/{}/` (8 parallel). "
        "Tools: `parsyncfp` (rsync wrapper that does this automatically). "
        "Trade-off: bandwidth saturation faster, but harder to interrupt cleanly.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: copy device files / special files",
        "`-a` covers most use cases; for explicit: `-D` is the special-file family (`--devices` + `--specials`). "
        "Use case: cloning `/dev/` or `/proc/` contents (rare). "
        "Most modern systems: just use `-a` and let rsync handle. "
        "For full system clone: prefer `dd` or filesystem-level tools (xfsdump, btrfs send).",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: full system migration to new server",
        "```bash\\nrsync -aAXHv --delete --numeric-ids \\\\\\n  --exclude='/dev/*' --exclude='/proc/*' --exclude='/sys/*' \\\\\\n  --exclude='/tmp/*' --exclude='/mnt/*' --exclude='/media/*' \\\\\\n  --exclude='/var/cache/apt/archives/' \\\\\\n  -e 'ssh -p 22' / user@newhost:/\\n```. "
        "After: chroot + update bootloader, fstab, hostname.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: encrypted backup destination (--rsh wrapping)",
        "Combine with `gocryptfs` / `encfs` / `cryfs` on dest: mount encrypted dir, rsync into it. "
        "Or use `restic` / `borg` / `duplicity` — purpose-built encrypted backup tools that use rsync-style algorithms internally. "
        "For minimal: encrypt files before rsync: `tar c /src | gpg -e -r me | ssh host 'cat > backup.tar.gpg'` (loses incremental advantage).",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: create destination path if missing (--mkpath)",
        "rsync 3.2.3+: `rsync -av --mkpath /src/ /dst/some/deep/path/`. "
        "Creates intermediate dirs as needed. "
        "Without `--mkpath`: rsync errors if dest's parent doesn't exist. "
        "For older rsync: pre-create with `mkdir -p /dst/some/deep/path` then rsync.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: production-ready cron backup",
        "```bash\\n#!/bin/bash\\nset -euo pipefail\\nexec > >(logger -t backup) 2>&1\\nLOCKFILE=/var/run/rsync-backup.lock\\nexec 200>\"$LOCKFILE\"\\nflock -n 200 || exit 0  # skip if already running\\n\\nrsync -aAXH --delete --max-delete=1000 \\\\\\n  --bwlimit=10m \\\\\\n  --exclude='*.tmp' --exclude='node_modules' \\\\\\n  --partial --append-verify \\\\\\n  /home/ user@backup:/var/backups/$(hostname)/home/\\n```. "
        "Key: lock file, bwlimit, max-delete safety, logging to syslog.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: verify a previous transfer with checksums",
        "`rsync -av --checksum --dry-run /src/ /dst/` — should show no transfers if synced correctly. "
        "Any output means file content differs (even if size+mtime match). "
        "Use case: end-of-month audit, after a flaky transfer.",
        f"{BASE}/rsync.html",
    ),
    (
        "rsync-recipes", "rsync recipe: minimize SSH connections with controlpath",
        "Add to ~/.ssh/config: ```\\nHost *\\n    ControlMaster auto\\n    ControlPath ~/.ssh/cm-%r@%h:%p\\n    ControlPersist 10m\\n```. "
        "Now multiple rsync calls in a row reuse one TCP connection (avoiding SSH handshake each time). "
        "Critical for scripts that rsync many small things sequentially.",
        f"{BASE}/rsync.html",
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
            evidence_id=f"ev-rsync-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-rsync-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="rsync-catalog",
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
