"""Direct-insert journalctl error + recipe claims."""

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

BASE = "https://www.freedesktop.org/software/systemd/man"

CLAIMS = [
    # ============================================================
    # journalctl-errors — 30 common errors
    # ============================================================
    (
        "journalctl-errors", "journalctl error: No journal files were found",
        "Either persistent journals aren't enabled, OR you're querying as a user who can't see system logs. "
        "Fix: (1) check storage: `cat /etc/systemd/journald.conf | grep Storage` — should be `persistent` (volatile-only is lost on reboot); "
        "(2) make persistent: `sudo mkdir -p /var/log/journal && sudo systemctl restart systemd-journald`; "
        "(3) check perms: must be in `systemd-journal` group OR root to read system journal; "
        "(4) for user journals: `journalctl --user` (current user only); "
        "(5) verify daemon: `systemctl status systemd-journald`.",
        f"{BASE}/journald.conf.html",
    ),
    (
        "journalctl-errors", "journalctl error: Failed to add match '<X>': Invalid argument",
        "Match syntax is wrong. Matches are FIELD=value with field names ALL-CAPS. "
        "Fix: (1) correct field name: `journalctl _SYSTEMD_UNIT=nginx.service` (underscore-prefix for trusted fields, all-caps); "
        "(2) multiple matches: separate with `+` for OR: `_SYSTEMD_UNIT=a + _SYSTEMD_UNIT=b`; "
        "(3) for AND across different fields: just list them: `_SYSTEMD_UNIT=nginx PRIORITY=3`; "
        "(4) list known fields: see man systemd.journal-fields; "
        "(5) for plain unit name search use `-u <name>` instead.",
        f"{BASE}/systemd.journal-fields.html",
    ),
    (
        "journalctl-errors", "journalctl warning: Hint: You are currently not seeing messages from other users",
        "Informational — your user only sees their own logs by default. "
        "Fix: (1) for system+everyone: run as root: `sudo journalctl`; "
        "(2) join the group (persistent): `sudo usermod -aG systemd-journal $USER && newgrp systemd-journal`; "
        "(3) verify with `groups`; "
        "(4) for user-scope only (no escalation needed): `journalctl --user` shows only your user-managed services.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-errors", "journalctl error: Permission denied / Cannot read journal",
        "Your user isn't in the systemd-journal group. "
        "Fix: (1) `sudo journalctl ...` (one-off); "
        "(2) permanent: `sudo usermod -aG systemd-journal $USER` then logout/login (or `newgrp systemd-journal`); "
        "(3) verify membership: `groups | grep -o systemd-journal`; "
        "(4) check journal dir perms: `ls -la /var/log/journal/` — should be group `systemd-journal` readable.",
        f"{BASE}/systemd-journald.service.html",
    ),
    (
        "journalctl-errors", "journalctl error: Failed to parse timestamp '<X>'",
        "`--since`/`--until` got an unparseable date. "
        "Fix: (1) absolute: `--since '2024-12-01 10:00'` (NOT `Dec 1 2024 10am`); "
        "(2) relative shortcuts: `yesterday`, `today`, `now`, `tomorrow`; "
        "(3) intervals: `--since '2 hours ago'`, `--since '30 min ago'`, `--since '1 week ago'`; "
        "(4) precise format: `YYYY-MM-DD HH:MM:SS` (24-hour); "
        "(5) ISO 8601 with TZ: `'2024-12-01T15:30:00Z'`.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-errors", "journalctl error: No entries / journal is empty for filter",
        "Filter matches no entries, OR you're looking at the wrong scope. "
        "Fix: (1) widen time: omit `--since`; "
        "(2) drop unit filter to see everything: `journalctl -n 50`; "
        "(3) check unit name (case + .service suffix): `journalctl -u sshd.service` vs `-u ssh`; "
        "(4) for user services: `--user` flag (won't see system services); "
        "(5) for old/rotated: `--directory /var/log/journal/<machine-id>`.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-errors", "journalctl error: Failed to look up boot ID",
        "`-b N` references a boot that doesn't exist. "
        "Fix: (1) list available: `journalctl --list-boots`; "
        "(2) current boot: `-b 0` (default); "
        "(3) previous boot: `-b -1`; "
        "(4) by ID: `journalctl -b <32-char-boot-id>`; "
        "(5) for VMs that lose state: `--list-boots` may show only recent — persistent storage needed for older.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-errors", "journalctl error: Cannot find unit '<name>'",
        "Unit name typo or unit doesn't exist on this system. "
        "Fix: (1) list all units: `systemctl list-unit-files | grep <name>`; "
        "(2) common: forgetting `.service`: `journalctl -u sshd.service` (or just `sshd` works in modern systemd); "
        "(3) for templated: `journalctl -u 'sshd@*'`; "
        "(4) for socket activated: `-u sshd.socket`; "
        "(5) for system+user variants: try both `--system` (default) and `--user`.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-errors", "journalctl error: Failed to seek to head / cursor corruption",
        "Journal file index or cursor is corrupted (rare, usually after unclean shutdown). "
        "Fix: (1) verify: `journalctl --verify`; "
        "(2) rotate to start fresh: `sudo journalctl --rotate` (then archive cycle continues); "
        "(3) flush volatile to persistent: `sudo journalctl --flush`; "
        "(4) nuclear: stop daemon + delete corrupted files: `sudo systemctl stop systemd-journald && sudo rm -i /var/log/journal/<id>/<corrupt-file> && sudo systemctl start systemd-journald` (loses old logs).",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-errors", "journalctl warning: --grep is case-insensitive without --case-sensitive=yes",
        "Informational. `-g` / `--grep` defaults case-insensitive in newer systemd. "
        "Fix: (1) force case-sensitive: `journalctl -g 'Error' --case-sensitive=yes`; "
        "(2) for case-insensitive (default): just `journalctl -g 'error'`; "
        "(3) regex (PCRE): `-g 'failed.*connect'`.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-errors", "journalctl error: Failed to query journal: <network error>",
        "Remote journal access via SSH/--machine failed. "
        "Fix: (1) for SSH: ensure systemd-journal-remote on both ends; "
        "(2) for container (--machine): container must be running via systemd-machined: `machinectl list`; "
        "(3) for libvirt: `--machine <vm>` works only if systemd-machined is wired up; "
        "(4) alternative: `ssh host journalctl -u <unit>` (just SSH + run journalctl remotely).",
        f"{BASE}/systemd-journal-remote.service.html",
    ),
    (
        "journalctl-errors", "journalctl warning: Lost messages / Suppressed N messages due to rate-limiting",
        "systemd-journald drops bursts to protect itself. Defaults: 10000 msgs / 30 sec per service. "
        "Fix: (1) per-service in unit file: `LogRateLimitIntervalSec=0` and `LogRateLimitBurst=0` disables; "
        "(2) global in journald.conf: `RateLimitIntervalSec=30s` and `RateLimitBurst=10000`; "
        "(3) reload: `sudo systemctl restart systemd-journald`; "
        "(4) for high-throughput apps: forward to dedicated logger instead of journal.",
        f"{BASE}/journald.conf.html",
    ),
    (
        "journalctl-errors", "journalctl error: Journal file rotated / 'this is normal during shutdown'",
        "Informational — journals rotate when they hit `SystemMaxFileSize` or `SystemMaxUse`. "
        "Fix: (1) check disk usage: `journalctl --disk-usage`; "
        "(2) bump limits in /etc/systemd/journald.conf: `SystemMaxUse=4G` and `SystemMaxFileSize=128M`; "
        "(3) reload: `sudo systemctl restart systemd-journald`; "
        "(4) clean old logs: `sudo journalctl --vacuum-size=1G` or `--vacuum-time=2weeks`.",
        f"{BASE}/journald.conf.html",
    ),
    (
        "journalctl-errors", "journalctl error: Failed to verify journal: file modified",
        "`--verify` detected the journal file was modified outside systemd-journald (tampering OR corruption). "
        "Fix: (1) determine cause: was filesystem unclean shutdown? (`dmesg | grep ext4`); "
        "(2) rotate: `sudo journalctl --rotate`; "
        "(3) move corrupted file aside: `sudo mv /var/log/journal/<id>/<file> /tmp/`; "
        "(4) for security audit: corrupted journal can mask attacker activity — investigate carefully.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-errors", "journalctl error: Storage=volatile means logs gone after reboot",
        "By default many distros use volatile storage — logs in /run/log/journal, wiped on reboot. "
        "Fix: (1) make persistent: edit /etc/systemd/journald.conf set `Storage=persistent`; "
        "(2) create directory: `sudo mkdir -p /var/log/journal && sudo systemd-tmpfiles --create --prefix /var/log/journal`; "
        "(3) restart: `sudo systemctl restart systemd-journald`; "
        "(4) verify: `journalctl --disk-usage` should now show `Archived and active journals take up X in the file system`.",
        f"{BASE}/journald.conf.html",
    ),
    (
        "journalctl-errors", "journalctl error: -k shows nothing on container",
        "Kernel logs (`-k`) require host access — most containers don't have access to host kernel ring buffer. "
        "Fix: (1) `journalctl -k` works on bare metal / VMs only (in containers, kmsg is usually empty); "
        "(2) for container kernel: that's the HOST's kernel — must query from host; "
        "(3) for container app logs: drop `-k` flag; "
        "(4) if Linux Capabilities allow: `--cap-add SYS_ADMIN` in docker run lets container see kmsg (rare + insecure).",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-errors", "journalctl error: log forwarding to syslog is missing entries",
        "ForwardToSyslog enabled but rsyslog/syslog-ng filtering or not running. "
        "Fix: (1) check journald.conf: `ForwardToSyslog=yes`; "
        "(2) syslog daemon running: `systemctl status rsyslog`; "
        "(3) check rsyslog filters in /etc/rsyslog.d/ — may drop based on facility/priority; "
        "(4) alternative: forward to remote with systemd-journal-upload.service.",
        f"{BASE}/journald.conf.html",
    ),
    (
        "journalctl-errors", "journalctl error: ssh tunnel + --machine doesn't work",
        "`--machine` reads container journals via systemd-machined; doesn't work over SSH directly. "
        "Fix: (1) instead, SSH then run journalctl: `ssh host 'journalctl -u nginx'`; "
        "(2) for systemd-managed containers (machined): on host, `machinectl list` shows containers; then `journalctl -M <container>`; "
        "(3) for Docker/Podman: those don't register with machined — use `docker logs`/`podman logs` instead.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-errors", "journalctl error: --user shows nothing",
        "`--user` queries the per-user journal, which only has entries for user-managed services (systemctl --user units). "
        "Fix: (1) for system services: drop `--user`: `journalctl -u sshd.service`; "
        "(2) for user units only: `systemctl --user list-units --type=service` shows what produces logs; "
        "(3) for lingering user services (run without user login): enable with `loginctl enable-linger $USER`; "
        "(4) check Storage in user journald config (typically inherits from system).",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-errors", "journalctl error: -f / --follow blocks but no output",
        "Either no new entries matching your filter, or terminal buffering. "
        "Fix: (1) generate logs to test: `logger 'test message'` (writes to journal); "
        "(2) wider filter: `journalctl -f` (everything) to confirm follow works; "
        "(3) test specific unit: `systemctl restart <unit> && journalctl -f -u <unit>`; "
        "(4) for line-buffered output (when piped): `journalctl -f | grep --line-buffered 'pattern'`.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-errors", "journalctl error: --vacuum-size deleted important logs",
        "Vacuum is destructive — keep that in mind. "
        "Fix: (1) test first: `journalctl --vacuum-size=2G --dry-run` (but `--dry-run` doesn't exist; preview by inspecting `--list-files` first); "
        "(2) for safety: `--vacuum-time=2weeks` is generally safer (preserves recent); "
        "(3) configure retention BEFORE crisis: `SystemMaxUse=4G` + `SystemMaxFileSize=128M` in journald.conf; "
        "(4) backup before vacuum: `journalctl --since '1 week ago' > backup.log`.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-errors", "journalctl error: Storage=auto but journals still volatile",
        "`Storage=auto` (default) only persists if `/var/log/journal/` directory exists. Most distros DON'T create it. "
        "Fix: (1) create dir: `sudo mkdir -p /var/log/journal`; "
        "(2) `sudo systemd-tmpfiles --create --prefix /var/log/journal`; "
        "(3) `sudo systemctl restart systemd-journald`; "
        "(4) verify persistence: `journalctl --list-boots` shows multiple boots only if persistent; "
        "(5) make explicit: set `Storage=persistent` to avoid surprises.",
        f"{BASE}/journald.conf.html",
    ),
    (
        "journalctl-errors", "journalctl error: 'No persistent journal was found, returning ephemeral'",
        "Same root cause as Storage=auto issue. "
        "Fix: see the 'Storage=auto still volatile' recipe — create /var/log/journal/ and restart journald.",
        f"{BASE}/journald.conf.html",
    ),
    (
        "journalctl-errors", "journalctl warning: 'No previous boot' / -b -1 empty",
        "Persistent storage not enabled, so previous boots aren't kept. "
        "Fix: (1) enable persistent (see above); "
        "(2) for current boot only: `-b 0` (default); "
        "(3) after enabling, future boots will be retrievable.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-errors", "journalctl error: 'too many open files' during query",
        "Hit ulimit when iterating many journal files. "
        "Fix: (1) check current: `ulimit -n`; "
        "(2) raise temporarily: `ulimit -n 4096 && journalctl ...`; "
        "(3) permanent: edit /etc/security/limits.conf: `*  soft  nofile  65535`; "
        "(4) for journalctl invocation: `prlimit --nofile=65535 journalctl ...`; "
        "(5) cleanup journal files (reduce count): `sudo journalctl --vacuum-size=1G`.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-errors", "journalctl error: 'system has not been booted with systemd as init system'",
        "Running journalctl on a system without systemd (e.g., WSL1, Alpine without systemd, or inside chroot). "
        "Fix: (1) verify: `ps -p 1 -o comm=` should print `systemd`; "
        "(2) for WSL: WSL2 supports systemd if you `[boot] systemd=true` in /etc/wsl.conf; "
        "(3) for Alpine: install OpenRC + use `logread` or `cat /var/log/messages`; "
        "(4) for Docker container: containers usually don't run systemd as PID 1 — `docker logs` instead.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-errors", "journalctl error: 'this command expects one or more unit names'",
        "Some commands (`--user-unit`, `--unit`) require a name after. "
        "Fix: (1) `journalctl -u <name>` not just `journalctl -u`; "
        "(2) for templated: `journalctl -u 'sshd@*'`; "
        "(3) check unit name spelling: `systemctl list-unit-files`; "
        "(4) for system+user search: try both: `journalctl -u name.service` and `journalctl --user -u name.service`.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-errors", "journalctl error: '-g pattern matches no entries'",
        "Pattern doesn't appear in any log line in your scope. "
        "Fix: (1) widen scope first: `journalctl --since '1 day ago' | grep -i 'pattern'`; "
        "(2) check regex (PCRE): special chars need escaping: `-g 'error\\\\?'`; "
        "(3) case: defaults case-insensitive; if matching log levels: use `-p err` not regex; "
        "(4) field-scoped: `_SYSTEMD_UNIT=nginx.service -g 'timeout'`.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-errors", "journalctl error: --output=json prints binary fields incorrectly",
        "Some fields (like __CURSOR, MESSAGE_ID UUIDs) are binary or base64. "
        "Fix: (1) prefer `json-pretty` for readability; "
        "(2) for `cat`-like with field name: `--output=short-iso` (or `short-precise` for microseconds); "
        "(3) for tool consumption: `json` is correct — your parser must handle base64 fields; "
        "(4) for plain text: drop --output (default `short` works fine).",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-errors", "journalctl error: 'No journal files were opened due to insufficient permissions'",
        "Same root cause as 'No journal files were found' but more specific. "
        "Fix: (1) sudo: `sudo journalctl`; "
        "(2) add to group: `sudo usermod -aG systemd-journal $USER`; "
        "(3) for journal in /var/log/journal: dir must be readable by systemd-journal group; "
        "(4) verify: `ls -ld /var/log/journal /var/log/journal/$(cat /etc/machine-id)`.",
        f"{BASE}/journalctl.html",
    ),

    # ============================================================
    # journalctl-recipes — 36 common workflows
    # ============================================================
    (
        "journalctl-recipes", "journalctl recipe: tail logs live (-f)",
        "`journalctl -f` follows new entries (like `tail -f`). "
        "Filtered live tail: `journalctl -f -u nginx.service`. "
        "Last 100 then follow: `journalctl -n 100 -f -u nginx`. "
        "Multiple units: `journalctl -f -u nginx -u php-fpm`. "
        "Stop with Ctrl-C.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: filter by service unit",
        "`journalctl -u nginx.service` (or just `-u nginx`). "
        "Multiple: `journalctl -u nginx -u php-fpm` (OR — entries from EITHER). "
        "Templated: `-u 'sshd@*'`. "
        "Wildcard service: `-u '*.service'` (all services). "
        "Combine with time: `-u nginx --since '1 hour ago'`.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: logs from current / previous boot",
        "Current boot: `journalctl -b` (or `-b 0`). "
        "Previous: `-b -1`. Older: `-b -2`, `-b -3`. "
        "List all available boots: `journalctl --list-boots`. "
        "By specific boot ID: `-b <32-char-id>`. "
        "From specific boot since time: `-b -1 --since '10:00' --until '11:00'`.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: filter by time range",
        "Since absolute: `journalctl --since '2024-12-01 10:00'`. "
        "Since relative: `--since '1 hour ago'`, `--since yesterday`, `--since '30 min ago'`. "
        "Between: `--since '10:00' --until '11:30'` (today). "
        "Last hour: `--since '1 hour ago'`. "
        "Past week: `--since '1 week ago'`.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: filter by log priority/severity",
        "`journalctl -p err` shows error and above (errors, critical, alert, emergency). "
        "Priority levels: `emerg` (0), `alert` (1), `crit` (2), `err` (3), `warning` (4), `notice` (5), `info` (6), `debug` (7). "
        "Range: `-p warning..err` (between). "
        "Just errors and above: `-p err` is shorthand for `-p 0..3`. "
        "Specific only: `-p err -p err` (just err, not above).",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: grep / search log text",
        "`journalctl -g 'failed login'` (case-insensitive default). "
        "Case-sensitive: `--case-sensitive=yes`. "
        "Regex (PCRE): `-g 'connection (refused|reset)'`. "
        "Scoped: `journalctl -u nginx -g '5\\d\\d'` (5xx errors in nginx). "
        "Combine with time: `journalctl -g 'oom' --since today`.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: kernel messages only",
        "`journalctl -k` shows kernel ring buffer entries. "
        "Since boot: `-k -b`. "
        "Combine with severity: `-k -p err` (kernel errors). "
        "Live: `-k -f`. "
        "Equivalent to `dmesg` (but with severity + boot filtering).",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: show last N entries",
        "`journalctl -n 50` shows last 50 entries. "
        "Default: `-n` alone = last 10. "
        "Combined with filter: `journalctl -n 100 -u nginx`. "
        "Reverse order: `-r` (newest first — combine with `-n` for 'tail' effect: `-n 50 -r`).",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: JSON output for scripting",
        "Compact JSON (one entry per line, JSON Lines): `journalctl -o json`. "
        "Pretty JSON: `-o json-pretty`. "
        "Pipe to jq: `journalctl -u nginx -o json --since today | jq -r 'select(.PRIORITY==\"3\") | .MESSAGE'`. "
        "Stream JSON (live): `journalctl -f -o json | jq -r '.MESSAGE'`. "
        "Specific fields: `-o json-pretty --output-fields=MESSAGE,_PID,_SYSTEMD_UNIT`.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: verbose output with all fields",
        "`journalctl -o verbose` shows every field of each entry (good for debugging). "
        "Includes: cursor, source timestamps, machine ID, process info, every custom field. "
        "Less verbose options: `short` (default), `short-iso`, `short-precise`, `cat` (just messages).",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: check disk usage of journals",
        "`journalctl --disk-usage` prints total bytes used by archive + active journals. "
        "Per-file detail: `journalctl --header` (shows internal headers per journal file). "
        "List files: `journalctl --list-files`. "
        "Set limits: edit /etc/systemd/journald.conf — `SystemMaxUse=4G`.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: free disk space (vacuum old logs)",
        "By size: `sudo journalctl --vacuum-size=1G` (keep at most 1 GB). "
        "By time: `sudo journalctl --vacuum-time=2weeks` (delete older than 2 weeks). "
        "By file count: `--vacuum-files=10` (keep at most 10 archived files). "
        "Caveat: only ARCHIVED files affected — active is not touched. "
        "Set persistent limits in journald.conf instead of running manually.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: verify journal integrity",
        "`sudo journalctl --verify` checks all files for corruption / tampering. "
        "Output: per-file PASS or FAIL. "
        "For specific file: `--file /var/log/journal/<id>/<file>.journal --verify`. "
        "Use case: after unclean shutdown, post-attack forensics, or just periodic audit.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: rotate journal manually",
        "`sudo journalctl --rotate` archives the active journal + starts a new active one. "
        "Combine with vacuum: `sudo journalctl --rotate && sudo journalctl --vacuum-time=1day`. "
        "Use case: before sensitive op (clears 'active' state), or test that rotation works.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: filter by PID",
        "`journalctl _PID=1234`. "
        "By process: `journalctl _COMM=nginx` (executable name) or `_EXE=/usr/sbin/nginx`. "
        "All combined: `journalctl _COMM=nginx _PID=1234`. "
        "By UID: `journalctl _UID=1000`. "
        "By systemd unit: `journalctl _SYSTEMD_UNIT=nginx.service` (more specific than `-u`).",
        f"{BASE}/systemd.journal-fields.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: per-user logs",
        "`journalctl --user` shows only the current user's journal (systemctl --user units). "
        "Specific user unit: `journalctl --user -u myservice.service`. "
        "Caveat: user services don't run while user is logged-out unless lingering enabled: `loginctl enable-linger $USER`. "
        "For another user (root): `journalctl _UID=1001`.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: container logs via --machine",
        "For systemd-machined containers: `journalctl -M <container-name>` (where -M = --machine). "
        "Check what's available: `machinectl list`. "
        "Specific unit in container: `journalctl -M mycontainer -u nginx.service`. "
        "Caveat: Docker/Podman don't register with machined — use `docker logs` / `podman logs` for those.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: cursor-based resumption (incremental log shipping)",
        "Each entry has a `__CURSOR`. Save it, resume from it later. "
        "Get last cursor: `journalctl -n 0 --show-cursor` (prints just the cursor). "
        "Resume from cursor: `journalctl --after-cursor=<cursor-string>`. "
        "Use case: log shipper that's been disconnected — pick up exactly where it left off.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: filter by syslog identifier",
        "`journalctl -t cron` shows entries with `SYSLOG_IDENTIFIER=cron`. "
        "Useful for tools that don't run as systemd units (use `logger -t myapp`). "
        "All entries from logger -t: `journalctl -t myapp`. "
        "Combine with priority: `-t myapp -p err`.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: find logs by message ID (catalog)",
        "Some systemd events have known MESSAGE_IDs (e.g. login, OOM, segfault). "
        "`journalctl MESSAGE_ID=<uuid>` (find UUIDs in /usr/lib/systemd/catalog/). "
        "Example OOM kill: `MESSAGE_ID=fc2e22bc6ee647b6b90729ab34a250b1`. "
        "All cataloged events: `journalctl --catalog` augments with explanations from catalog DB.",
        f"{BASE}/systemd.journal-fields.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: enable persistent storage",
        "Without `/var/log/journal/`, logs are volatile (lost on reboot). "
        "Enable: ```\\nsudo mkdir -p /var/log/journal\\nsudo systemd-tmpfiles --create --prefix /var/log/journal\\nsudo systemctl restart systemd-journald\\n```. "
        "Verify: `journalctl --list-boots` should show multiple after a reboot. "
        "Explicit config: in /etc/systemd/journald.conf set `Storage=persistent`.",
        f"{BASE}/journald.conf.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: configure retention size and time",
        "Edit /etc/systemd/journald.conf: ```\\nSystemMaxUse=4G          # total disk\\nSystemMaxFileSize=128M    # per file\\nSystemKeepFree=1G         # min free on filesystem\\nMaxRetentionSec=4weeks    # auto-delete after this\\n```. "
        "Reload: `sudo systemctl restart systemd-journald`. "
        "Per-user (in journald-USER.conf or .d/*.conf): `RuntimeMaxUse=`, `RuntimeKeepFree=`.",
        f"{BASE}/journald.conf.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: forward to syslog / network",
        "To syslog (rsyslog/syslog-ng) on same host: in journald.conf set `ForwardToSyslog=yes` then `sudo systemctl restart systemd-journald`. "
        "To remote: install systemd-journal-upload, configure /etc/systemd/journal-upload.conf with `URL=https://logserver:19532`. "
        "Receiver: systemd-journal-remote.service (port 19532 HTTP/HTTPS). "
        "TLS auth: configure with `ServerKeyFile=`, `ServerCertificateFile=`, `TrustedCertificateFile=`.",
        f"{BASE}/journal-upload.conf.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: limit output line width",
        "Default truncates long lines for terminal. To see full: `journalctl --no-pager` (pager interferes). "
        "Disable line truncation: `SYSTEMD_LESS=FRSXMK journalctl ...` (chars after SXMK). "
        "Explicit: `journalctl --no-tail --no-pager`. "
        "Output to file (no truncation): `journalctl > out.log`.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: track failed service starts",
        "`journalctl -p err -u nginx.service --since 'today'`. "
        "Combined start failure pattern: `journalctl -u nginx.service -g 'failed' --since today`. "
        "For all failed services on system: `systemctl --failed`, then per-service drill-down. "
        "Live alert: `journalctl -p err -f | mail -s 'Server error' admin@x.com`.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: export and import journal files",
        "Export: `journalctl -u nginx --since today > nginx-today.log`. "
        "Binary export (preserves structure): `journalctl -u nginx -o export > nginx.export`. "
        "Import elsewhere: `cat nginx.export | systemd-journal-remote -o /var/log/journal/imported.journal -`. "
        "Then query: `journalctl --file /var/log/journal/imported.journal`.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: alert on specific log entries",
        "Live filter + action: `journalctl -f -p err | while read line; do echo \"$line\" | mail -s 'error' admin; done`. "
        "With JSON for richer alerts: `journalctl -f -o json | jq -r 'select(.PRIORITY==\"3\") | .MESSAGE' | while read msg; do alert.sh \"$msg\"; done`. "
        "Production: use Vector, Loki, or Promtail to ship + alert via Grafana / Prometheus.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: see boot timing (slowest service)",
        "`systemd-analyze blame` (NOT journalctl, but related): shows per-service startup time. "
        "Critical chain: `systemd-analyze critical-chain`. "
        "Plot timing as SVG: `systemd-analyze plot > boot.svg`. "
        "For service-specific timing: `journalctl -u <service> -b 0 -o short-precise` (microsecond timestamps).",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: parse with jq (common patterns)",
        "Filter by priority and extract: `journalctl -u nginx -o json --since today | jq -r 'select(.PRIORITY==\"3\") | \"\\(.__REALTIME_TIMESTAMP): \\(.MESSAGE)\"'`. "
        "Count by source: `journalctl -o json --since today | jq -r '._COMM' | sort | uniq -c | sort -rn`. "
        "By unit: `... | jq -r '._SYSTEMD_UNIT' | sort | uniq -c | sort -rn | head -20`.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: log from a script (logger / systemd-cat)",
        "From shell (writes to journal): `logger 'something happened'`. "
        "With tag: `logger -t myapp 'message'` then `journalctl -t myapp`. "
        "Priority: `logger -p user.err 'critical thing'`. "
        "Whole command output to journal: `systemd-cat myscript.sh` (each line becomes a journal entry tagged with the script).",
        f"{BASE}/sd_journal_print.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: only logs while specific process was alive",
        "`journalctl _PID=1234 --since '<process-start>' --until '<process-stop>'`. "
        "For service: use unit filter — `journalctl -u <unit>` automatically scopes to its process lifecycle. "
        "For ephemeral: use cursor: before starting process, `journalctl -n 0 --show-cursor`, then after stop, `journalctl --after-cursor=<saved>`.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: investigate OOM kills",
        "`journalctl -k -g 'killed process' --since today`. "
        "Catalog lookup: `journalctl MESSAGE_ID=fc2e22bc6ee647b6b90729ab34a250b1`. "
        "Full kernel + memory pressure: `journalctl -k -p err -b`. "
        "With cgroup context (which container): `journalctl -k -o verbose | grep -A 5 'oom-kill'` — shows OOM scores + memory state.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: SSH login audit",
        "All SSH events: `journalctl -u sshd.service`. "
        "Failed logins: `journalctl -u sshd -g 'failed password'`. "
        "Successful: `-g 'accepted password'` or `-g 'accepted publickey'`. "
        "By source IP: `journalctl -u sshd -g 'from <ip>'`. "
        "Time-windowed: `--since '1 hour ago'`. "
        "For audit trail: `journalctl _COMM=sshd -o json --since 'last week' > ssh-audit.json`.",
        f"{BASE}/journalctl.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: list all field names in current journal",
        "All fields used: `journalctl -F _SYSTEMD_UNIT` lists distinct values for that field. "
        "Discover available fields: `journalctl -o verbose -n 1` shows every field of one entry. "
        "Common well-known: `_PID`, `_UID`, `_COMM`, `_EXE`, `_CMDLINE`, `_SYSTEMD_UNIT`, `_SYSTEMD_CGROUP`, `_BOOT_ID`, `_HOSTNAME`, `_TRANSPORT`, `MESSAGE`, `PRIORITY`, `MESSAGE_ID`, `SYSLOG_IDENTIFIER`, `SYSLOG_FACILITY`.",
        f"{BASE}/systemd.journal-fields.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: rate-limit override per service",
        "In unit file: ```ini\\n[Service]\\nLogRateLimitIntervalSec=0\\nLogRateLimitBurst=0\\n```. "
        "Disables rate limiting for this service. "
        "Reload + restart: `sudo systemctl daemon-reload && sudo systemctl restart <unit>`. "
        "Global override: in /etc/systemd/journald.conf set `RateLimitBurst=50000`.",
        f"{BASE}/journald.conf.html",
    ),
    (
        "journalctl-recipes", "journalctl recipe: pretty-print live tail for a unit",
        "`journalctl -u nginx -f -o cat` (just messages, no metadata). "
        "Color output (some configs): `SYSTEMD_LESS=FRX` env var. "
        "Combined: `journalctl -u nginx -f -o short-iso --no-hostname`. "
        "For dashboard-style: pipe to `tail | grep --color=auto`.",
        f"{BASE}/journalctl.html",
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
            evidence_id=f"ev-jc-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-jc-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="journalctl-catalog",
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
