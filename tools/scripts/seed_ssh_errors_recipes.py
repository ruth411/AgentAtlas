"""Direct-insert ssh error + recipe claims."""

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

BASE = "https://man.openbsd.org"

CLAIMS = [
    # ============================================================
    # ssh-errors — 30 common errors
    # ============================================================
    (
        "ssh-errors", "ssh error: Permission denied (publickey)",
        "Server only accepts public key auth and your key wasn't accepted (or no key was offered). "
        "Fix: (1) test which keys are tried: `ssh -v user@host` (look for 'Offering public key' lines); "
        "(2) verify the right key is in ssh-agent: `ssh-add -l`; load if missing: `ssh-add ~/.ssh/id_ed25519`; "
        "(3) ensure public key is in `~/.ssh/authorized_keys` on the server (one line, no extra whitespace); "
        "(4) check key file perms: `chmod 600 ~/.ssh/id_ed25519`, `chmod 644 ~/.ssh/id_ed25519.pub`; "
        "(5) check authorized_keys perms on server: `chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys`; "
        "(6) for restricted user (no shell): may need `command=` workaround in authorized_keys.",
        f"{BASE}/ssh.1",
    ),
    (
        "ssh-errors", "ssh error: Permission denied (publickey,password)",
        "Server accepts both, but neither succeeded. "
        "Fix: (1) same key-auth checks as above; "
        "(2) try password: `ssh -o PreferredAuthentications=password user@host` (forces password prompt); "
        "(3) wrong username — common when LDAP usernames differ from local: `ssh corp.user@host`; "
        "(4) account may be locked / expired: ask admin to check `passwd -S <user>` on the server; "
        "(5) for keyboard-interactive (2FA): `-o PreferredAuthentications=keyboard-interactive`.",
        f"{BASE}/ssh.1",
    ),
    (
        "ssh-errors", "ssh error: Host key verification failed / WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED",
        "Server's host key changed from what's in `~/.ssh/known_hosts`. Could be legitimate (server rebuilt) or MITM. "
        "Fix: (1) VERIFY OUT-OF-BAND first — ask server admin for current fingerprint, compare with `ssh-keyscan -t ed25519 host | ssh-keygen -lf -`; "
        "(2) if legit: remove old entry: `ssh-keygen -R hostname` (also try by IP); "
        "(3) re-add: connect again and accept prompt, OR `ssh-keyscan -H host >> ~/.ssh/known_hosts`; "
        "(4) for cloud-VM rebuilds: scripted automation; "
        "(5) NEVER `StrictHostKeyChecking=no` blindly — that defeats the protection.",
        f"{BASE}/ssh.1",
    ),
    (
        "ssh-errors", "ssh error: connect to host <X> port 22: Connection refused",
        "TCP connection refused. SSH service not listening. "
        "Fix: (1) verify port: `nc -zv host 22` (or `nmap -p 22 host`); "
        "(2) is sshd running on the server? `systemctl status sshd` (Linux), `launchctl list | grep ssh` (macOS); "
        "(3) firewall on server: `ufw status`, `iptables -L`, or cloud security group; "
        "(4) non-default port: `ssh -p 2222 user@host`; "
        "(5) for sshd not started: `sudo systemctl start sshd`.",
        f"{BASE}/sshd.8",
    ),
    (
        "ssh-errors", "ssh error: Connection timed out",
        "TCP connection didn't complete — firewall silently drops, network path broken, or host down. "
        "Fix: (1) ping host: `ping host`; "
        "(2) traceroute: `mtr host` or `traceroute host` shows where it stops; "
        "(3) firewall: cloud security group / WAF / on-prem firewall blocking port 22; "
        "(4) DNS resolution: `dig host` — IP might be stale; "
        "(5) try the IP directly: `ssh user@10.0.0.5` (bypasses DNS).",
        f"{BASE}/ssh.1",
    ),
    (
        "ssh-errors", "ssh error: No route to host / Network is unreachable",
        "Network layer can't reach the destination. "
        "Fix: (1) `ip route` to see your routing table; "
        "(2) VPN required? Many corporate hosts are only reachable through VPN; "
        "(3) host on different network with no router between: check IP plan; "
        "(4) for IPv6 issues: force IPv4: `ssh -4 user@host`; force IPv6: `ssh -6`; "
        "(5) Linux: `ip a` to see your own addresses + interfaces — physical link must be up.",
        f"{BASE}/ssh.1",
    ),
    (
        "ssh-errors", "ssh error: Could not resolve hostname",
        "DNS lookup failed. "
        "Fix: (1) `nslookup host` or `dig host`; "
        "(2) check /etc/resolv.conf has valid nameservers; "
        "(3) typo check: `host` vs `hostname.fqdn.com`; "
        "(4) use IP if DNS is broken: `ssh user@10.0.0.5`; "
        "(5) /etc/hosts entry for hardcoded mappings.",
        f"{BASE}/ssh.1",
    ),
    (
        "ssh-errors", "ssh error: UNPROTECTED PRIVATE KEY FILE / Permissions 0644 for '...' are too open",
        "ssh refuses to use a key with permissions readable by group or others. "
        "Fix: (1) `chmod 600 ~/.ssh/id_ed25519` (read/write owner only); "
        "(2) on directory: `chmod 700 ~/.ssh`; "
        "(3) ownership: `chown $USER:$USER ~/.ssh ~/.ssh/*`; "
        "(4) for Windows hosts: ACLs work differently — see Windows OpenSSH docs; "
        "(5) for shared CI runners: secret manager > on-disk key.",
        f"{BASE}/ssh.1",
    ),
    (
        "ssh-errors", "ssh warning: Pseudo-terminal will not be allocated because stdin is not a terminal",
        "Not really an error — informational. When ssh runs in a pipeline (`ssh host cmd | grep`), no TTY is allocated. "
        "Fix: (1) ignore if intentional (command-mode); "
        "(2) force TTY when needed: `ssh -t user@host 'sudo ...'`; "
        "(3) force two TTYs (for full interactive): `ssh -tt`; "
        "(4) for scripts where you don't want a TTY: `ssh -T user@host 'cmd'`.",
        f"{BASE}/ssh.1",
    ),
    (
        "ssh-errors", "ssh error: bind: Address already in use (port forward)",
        "Local port for `-L` / `-D` already bound by another process. "
        "Fix: (1) find what's holding it: `lsof -i :8080` or `netstat -tulpn | grep 8080`; "
        "(2) kill the conflicting process OR pick a different port: `-L 8081:remote:80`; "
        "(3) for ControlMaster sockets leaking: clean up: `rm ~/.ssh/cm-*`; "
        "(4) bind to specific interface: `-L 127.0.0.1:8080:remote:80` (default is loopback only — safer).",
        f"{BASE}/ssh.1",
    ),
    (
        "ssh-errors", "ssh error: Too many authentication failures",
        "ssh-agent offered too many keys (default limit 6 attempts before disconnect). "
        "Fix: (1) tell ssh which specific key: `ssh -i ~/.ssh/specific_key user@host`; "
        "(2) prevent agent from offering all: `ssh -o IdentitiesOnly=yes -i ~/.ssh/key user@host`; "
        "(3) in ~/.ssh/config: per-Host `IdentitiesOnly yes` + explicit `IdentityFile`; "
        "(4) clear excess agent keys: `ssh-add -D` then `ssh-add ~/.ssh/specific_key`.",
        f"{BASE}/ssh.1",
    ),
    (
        "ssh-errors", "ssh error: ssh_exchange_identification: Connection closed by remote host",
        "Server closed connection during the SSH protocol handshake. Many causes. "
        "Fix: (1) server's `/etc/hosts.deny` blocks you (TCP Wrappers): check + edit `/etc/hosts.allow`; "
        "(2) MaxStartups limit reached: server overloaded — wait or raise the limit in sshd_config; "
        "(3) sshd resource exhaustion: check server `dmesg`/`journalctl -u sshd`; "
        "(4) wrong protocol: client trying SSH-1, server is SSH-2 only — modern clients default to SSH-2, but check; "
        "(5) for a misbehaving server: check `journalctl -u sshd -e` on the server.",
        f"{BASE}/sshd.8",
    ),
    (
        "ssh-errors", "ssh error: agent refused operation",
        "ssh-agent rejected the requested operation. "
        "Fix: (1) list loaded keys: `ssh-add -l` — `Could not open a connection to your authentication agent` means agent not running; "
        "(2) start agent: `eval $(ssh-agent -s)`; "
        "(3) check SSH_AUTH_SOCK env var: `echo $SSH_AUTH_SOCK` (must point to a socket); "
        "(4) add key: `ssh-add ~/.ssh/id_ed25519` (prompts for passphrase); "
        "(5) for system-managed: keychain / Gnome Keyring / macOS Keychain may interfere.",
        f"{BASE}/ssh-agent.1",
    ),
    (
        "ssh-errors", "ssh error: no matching host key type found (key exchange failure)",
        "Client and server can't agree on a host key algorithm. Common with old OpenSSH < 8.0 talking to modern servers (or vice versa). "
        "Fix: (1) allow specific (less secure): `ssh -o HostKeyAlgorithms=+ssh-rsa user@host` (for legacy server); "
        "(2) update OpenSSH on the older side: `brew install openssh` (macOS), distro update (Linux); "
        "(3) for OpenSSH 9+ dropping older sigs: client needs `-o PubkeyAcceptedKeyTypes=+ssh-rsa` for old RSA keys; "
        "(4) generate a modern key: `ssh-keygen -t ed25519`.",
        f"{BASE}/ssh_config.5",
    ),
    (
        "ssh-errors", "ssh error: no matching MAC algorithm / cipher / kex",
        "Same root as host-key but for MAC / cipher / key-exchange. "
        "Fix: (1) negotiate: `ssh -vv ...` shows offered + accepted algorithms; "
        "(2) allow specific: `-o MACs=+hmac-sha1` (less secure — only for legacy); "
        "(3) check sshd_config on the server: `Ciphers`, `MACs`, `KexAlgorithms` may be restrictive; "
        "(4) for modern preferred: `aes256-gcm@openssh.com`, `chacha20-poly1305@openssh.com`.",
        f"{BASE}/ssh_config.5",
    ),
    (
        "ssh-errors", "ssh error: broken pipe / Connection reset by peer",
        "Connection died mid-session — server/network closed it. "
        "Fix: (1) keepalive on client: `~/.ssh/config` add `ServerAliveInterval 30` + `ServerAliveCountMax 3`; "
        "(2) server-side keepalive: `sshd_config` `ClientAliveInterval 30` + `ClientAliveCountMax 3`; "
        "(3) for unreliable networks: use `mosh` (mobile shell — handles disconnects/reconnects); "
        "(4) for IP changes: tmux/screen on the server lets you reconnect to the session.",
        f"{BASE}/ssh_config.5",
    ),
    (
        "ssh-errors", "ssh error: ControlMaster: cannot create socket / socket already exists",
        "Stale control socket from a previous SSH process. "
        "Fix: (1) clean stale: `rm ~/.ssh/cm-*` or `ssh -O exit user@host` to gracefully close; "
        "(2) `~/.ssh/config` Path: `ControlPath ~/.ssh/cm-%r@%h:%p`; "
        "(3) check master is alive: `ssh -O check user@host`; "
        "(4) ControlPersist auto-cleanup: `ControlPersist 10m` ends socket after 10 min idle.",
        f"{BASE}/ssh_config.5",
    ),
    (
        "ssh-errors", "ssh error: Could not open user configuration file '~/.ssh/config'",
        "Config file has permission or syntax problems. "
        "Fix: (1) ensure exists: `ls -la ~/.ssh/config`; create if missing: `touch ~/.ssh/config && chmod 600 ~/.ssh/config`; "
        "(2) perms: must be 600 or 644 — `chmod 600 ~/.ssh/config`; "
        "(3) syntax errors: try `ssh -vG user@host` to test config parsing; "
        "(4) check for tabs/spaces issues: ssh_config requires whitespace after directive keyword.",
        f"{BASE}/ssh_config.5",
    ),
    (
        "ssh-errors", "ssh error: agent forwarding refused / SSH_AUTH_SOCK not set on remote",
        "Agent forwarding (`-A`) didn't work or remote sshd disallows it. "
        "Fix: (1) check client: `ssh -A user@host` (or `ForwardAgent yes` in ~/.ssh/config); "
        "(2) server sshd_config: `AllowAgentForwarding yes` required; "
        "(3) on remote check env: `echo $SSH_AUTH_SOCK` (must be set); "
        "(4) for jump host: use `ProxyJump` instead (more secure — keys stay on client); "
        "(5) SECURITY: agent forwarding lets the remote host use your keys — only forward to fully-trusted hosts.",
        f"{BASE}/ssh.1",
    ),
    (
        "ssh-errors", "ssh error: Operation timed out (during key exchange or auth)",
        "Some operation took too long. Often network delay or server overload. "
        "Fix: (1) longer connect timeout: `ssh -o ConnectTimeout=30 user@host`; "
        "(2) for slow DNS reverses on server (default): server `sshd_config` set `UseDNS no`; "
        "(3) check server load: `uptime` (high load can delay auth); "
        "(4) for SSH-over-TOR or other slow transports: bump everything: `ConnectTimeout` + `ServerAliveInterval`.",
        f"{BASE}/ssh_config.5",
    ),
    (
        "ssh-errors", "ssh error: Could not create directory '/home/.ssh' (No such file or directory)",
        "User's home directory doesn't exist or process running as wrong user. "
        "Fix: (1) check `$HOME`: `echo $HOME` (should be `/home/<user>` or similar); "
        "(2) for system user (no home): create home: `sudo mkhomedir_helper <user>` (Linux); "
        "(3) for ssh-as-root inside container: HOME may not be set: `HOME=/root ssh ...`; "
        "(4) ensure user has write to parent dir.",
        f"{BASE}/ssh.1",
    ),
    (
        "ssh-errors", "ssh error: kex_exchange_identification: Connection closed by remote host",
        "Server immediately closes connection after TCP handshake. "
        "Fix: (1) server's `MaxStartups` limit (sshd_config) — too many unauthenticated connections; "
        "(2) `hosts.deny` blocking your IP; "
        "(3) fail2ban or sshguard temp-banned you for failed attempts; "
        "(4) load balancer in front of sshd that doesn't speak SSH; "
        "(5) verify sshd is actually answering: `nc host 22` should print SSH banner like `SSH-2.0-OpenSSH_9.x`.",
        f"{BASE}/sshd.8",
    ),
    (
        "ssh-errors", "ssh error: agent_genkey failed: No pinentry (when generating with -o passphrase)",
        "Some advanced ssh-keygen workflows interact with gpg-agent / pinentry. "
        "Fix: (1) for basic key gen: `ssh-keygen -t ed25519` doesn't need pinentry — it prompts itself; "
        "(2) for FIDO2: hardware key requires libfido2 + may need PIN entry; "
        "(3) for gpg-agent as ssh-agent: `enable-ssh-support` in ~/.gnupg/gpg-agent.conf + pinentry-program path; "
        "(4) for batch: `-N ''` for empty passphrase, `-N 'secret'` for explicit passphrase.",
        f"{BASE}/ssh-keygen.1",
    ),
    (
        "ssh-errors", "ssh error: scp: <file>: Permission denied",
        "Either file unreadable on source OR destination dir unwritable. "
        "Fix: (1) on source: `ls -la <file>`; "
        "(2) on destination: `ssh user@host 'ls -la <dest-dir>'`; "
        "(3) for system files: `scp -O` (legacy protocol, supports sudo via `sudoers`) — but better: scp to a writable location then `ssh sudo mv`; "
        "(4) for preserving perms across hosts: scp -p / rsync -p; "
        "(5) modern OpenSSH 9+ uses SFTP under the hood (scp protocol disabled by default) — `scp -O` to use legacy.",
        f"{BASE}/scp.1",
    ),
    (
        "ssh-errors", "ssh error: bad permissions on authorized_keys",
        "Server's sshd refuses to use authorized_keys with bad perms. "
        "Fix: (1) `chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys` (on the SERVER); "
        "(2) ownership: `chown -R <user>:<user> ~/.ssh` (especially after creating via sudo); "
        "(3) `chmod g-w ~` may be required (sshd refuses if home itself is group-writable); "
        "(4) check sshd_config: `StrictModes yes` enforces this — disabling is a security risk.",
        f"{BASE}/sshd.8",
    ),
    (
        "ssh-errors", "ssh error: PTY allocation request failed",
        "Server refused TTY allocation (often due to PermitTTY no or restricted shell). "
        "Fix: (1) try without TTY: drop `-t`; "
        "(2) on server, check sshd_config: `PermitTTY yes` (default); "
        "(3) for command-only execution (no shell): server's authorized_keys may have `no-pty` restriction; "
        "(4) for restricted users: this may be intentional (only allow specific commands).",
        f"{BASE}/sshd_config.5",
    ),
    (
        "ssh-errors", "ssh error: signature algorithm not supported (libcrypto)",
        "OpenSSL / libcrypto can't handle the cert/key signature algorithm. "
        "Fix: (1) for old SHA-1 keys on modern client: `ssh -o PubkeyAcceptedKeyTypes=+ssh-rsa,rsa-sha2-256 ...`; "
        "(2) regenerate key with modern algorithm: `ssh-keygen -t ed25519`; "
        "(3) update OpenSSL on the older system; "
        "(4) for FIPS-restricted builds: signature algorithm may be disabled.",
        f"{BASE}/ssh_config.5",
    ),
    (
        "ssh-errors", "ssh error: stdin: is not a tty (when using su / sudo over ssh)",
        "Trying to run a command requiring TTY without -t. "
        "Fix: (1) `ssh -t user@host 'sudo command'` (allocate TTY); "
        "(2) for non-interactive sudo: `sudo -n` (fail if password needed) — useful for scripts; "
        "(3) sudoers `NOPASSWD` for specific commands lets you skip password; "
        "(4) `ssh -tt` for nested TTY (when sudo'ing to another user).",
        f"{BASE}/ssh.1",
    ),
    (
        "ssh-errors", "ssh error: client_loop: send disconnect: Broken pipe (idle disconnect)",
        "Server sent disconnect because session was idle. "
        "Fix: (1) client keepalive: `~/.ssh/config` Host * → `ServerAliveInterval 60`; "
        "(2) server side `ClientAliveInterval 60` + `ClientAliveCountMax 3` keeps the session alive (but doesn't prevent idle disconnect by intermediate NAT); "
        "(3) for resilience: use `tmux` or `screen` on the server — survives disconnects.",
        f"{BASE}/ssh_config.5",
    ),
    (
        "ssh-errors", "ssh error: identity file not accessible / open failed",
        "ssh-keygen-style key file not found or unreadable. "
        "Fix: (1) check path: `ssh -i ~/.ssh/specific_key user@host` (path expanded by shell); "
        "(2) absolute path: avoid `~` issues in scripts: `ssh -i $HOME/.ssh/key`; "
        "(3) key format may not be supported: `ssh-keygen -y -f key` should print public key — if fails, key is unreadable; "
        "(4) for PuTTY .ppk format: convert with `puttygen key.ppk -O private-openssh -o id_rsa`.",
        f"{BASE}/ssh.1",
    ),

    # ============================================================
    # ssh-recipes — 36 common workflows
    # ============================================================
    (
        "ssh-recipes", "ssh recipe: generate an ed25519 key pair (modern default)",
        "`ssh-keygen -t ed25519 -C 'your_email@example.com'`. "
        "Prompts for file path (`~/.ssh/id_ed25519` default) + optional passphrase. "
        "ed25519 is faster + smaller + safer than RSA. "
        "For RSA fallback (legacy servers): `ssh-keygen -t rsa -b 4096`. "
        "For hardware key (FIDO2 YubiKey): `ssh-keygen -t ed25519-sk`. "
        "Show fingerprint: `ssh-keygen -lf ~/.ssh/id_ed25519.pub`.",
        f"{BASE}/ssh-keygen.1",
    ),
    (
        "ssh-recipes", "ssh recipe: copy public key to remote server",
        "Easy: `ssh-copy-id user@host` (non-OpenBSD distros usually ship this). "
        "Manual: `cat ~/.ssh/id_ed25519.pub | ssh user@host 'mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys'`. "
        "Specific key: `ssh-copy-id -i ~/.ssh/custom_key user@host`. "
        "After: test passwordless: `ssh user@host`. "
        "Verify on server: `cat ~/.ssh/authorized_keys` should have your public key.",
        f"{BASE}/ssh.1",
    ),
    (
        "ssh-recipes", "ssh recipe: ~/.ssh/config for common hosts",
        "```\\nHost prod\\n    HostName db.prod.example.com\\n    User deploy\\n    Port 2222\\n    IdentityFile ~/.ssh/prod_key\\n    IdentitiesOnly yes\\n\\nHost *.internal\\n    User admin\\n    ProxyJump bastion\\n\\nHost bastion\\n    HostName bastion.example.com\\n    User jumpuser\\n    IdentityFile ~/.ssh/jump_key\\n```. "
        "Now `ssh prod` works without flags. Wildcards + `Match` clauses for advanced routing. "
        "File mode: `chmod 600 ~/.ssh/config`.",
        f"{BASE}/ssh_config.5",
    ),
    (
        "ssh-recipes", "ssh recipe: jump through a bastion host (ProxyJump / -J)",
        "Direct: `ssh -J bastion user@internal-host`. "
        "Chained: `ssh -J bastion1,bastion2 user@final-host`. "
        "In ~/.ssh/config: `Host internal-host ProxyJump bastion`. "
        "Why ProxyJump > agent forwarding: keys stay on client (more secure); each hop is a separate SSH session. "
        "For non-standard port on bastion: `-J user@bastion:2222`.",
        f"{BASE}/ssh_config.5",
    ),
    (
        "ssh-recipes", "ssh recipe: local port forward (-L)",
        "`ssh -L 8080:db.internal:5432 bastion` — connects to bastion, forwards local 8080 to db.internal:5432 (via bastion). "
        "Then on local: `psql -h localhost -p 8080 ...`. "
        "Bind to all interfaces (default is loopback): `-L 0.0.0.0:8080:db:5432`. "
        "In ~/.ssh/config: `LocalForward 8080 db.internal:5432`. "
        "Background-only: `ssh -fNL ...` (`-f` background, `-N` no command).",
        f"{BASE}/ssh.1",
    ),
    (
        "ssh-recipes", "ssh recipe: remote port forward (-R)",
        "`ssh -R 8080:localhost:80 user@public-server` — opens 8080 on public-server, forwards to localhost:80 on your machine. "
        "Use case: expose dev server behind NAT. "
        "By default binds to public-server's loopback. To bind to all (must set `GatewayPorts yes` in server's sshd_config): `-R 0.0.0.0:8080:...`. "
        "Modern tool alternative: ngrok / cloudflared tunnel.",
        f"{BASE}/ssh.1",
    ),
    (
        "ssh-recipes", "ssh recipe: dynamic SOCKS proxy (-D)",
        "`ssh -D 1080 user@gateway` — local SOCKS proxy on 1080 routes through ssh. "
        "Configure browser SOCKS5: localhost:1080. "
        "Use case: route specific traffic through remote network without full VPN. "
        "Combine with `-fN`: `ssh -fND 1080 user@host` (background).",
        f"{BASE}/ssh.1",
    ),
    (
        "ssh-recipes", "ssh recipe: connection multiplexing (ControlMaster)",
        "~/.ssh/config: ```\\nHost *\\n    ControlMaster auto\\n    ControlPath ~/.ssh/cm-%r@%h:%p\\n    ControlPersist 10m\\n```. "
        "First ssh opens master + tunnel. Subsequent SSH calls to same host reuse the existing TCP connection (way faster — no auth round-trip). "
        "Manage: `ssh -O check host` (status), `ssh -O exit host` (close cleanly). "
        "Critical for scripts that ssh many times.",
        f"{BASE}/ssh_config.5",
    ),
    (
        "ssh-recipes", "ssh recipe: keep connection alive over flaky network",
        "Client side ~/.ssh/config: ```\\nHost *\\n    ServerAliveInterval 30\\n    ServerAliveCountMax 3\\n    TCPKeepAlive yes\\n```. "
        "Client sends 'are you alive?' every 30s; after 3 missed (90s), disconnects. "
        "Server side `sshd_config`: `ClientAliveInterval 30` + `ClientAliveCountMax 3`. "
        "For truly resilient: use `mosh` (handles roaming + reconnect) or `tmux` on server.",
        f"{BASE}/ssh_config.5",
    ),
    (
        "ssh-recipes", "ssh recipe: run a single command remotely",
        "Simple: `ssh user@host 'uptime'`. "
        "Multiple: `ssh user@host 'cd /tmp && tar xzf data.tgz'`. "
        "Force TTY: `ssh -t user@host 'sudo apt update'`. "
        "Background: `ssh user@host 'long-job &> log &'` (use nohup/disown for true detached). "
        "Capture exit code: `ssh user@host 'cmd'; echo \"exit: $?\"`.",
        f"{BASE}/ssh.1",
    ),
    (
        "ssh-recipes", "ssh recipe: run a script remotely via heredoc",
        "```bash\\nssh user@host << 'EOF'\\n  set -e\\n  cd /opt/app\\n  git pull\\n  systemctl restart app\\nEOF\\n```. "
        "`'EOF'` (quoted) prevents local variable expansion. "
        "For local variable substitution: unquoted `EOF` (lets `$VAR` expand locally). "
        "Better for long scripts: scp + execute: `scp deploy.sh host:/tmp/ && ssh host /tmp/deploy.sh`.",
        f"{BASE}/ssh.1",
    ),
    (
        "ssh-recipes", "ssh recipe: scp to copy files",
        "Push: `scp local-file user@host:/remote/path/`. "
        "Pull: `scp user@host:/remote/file ./local/`. "
        "Recursive directory: `scp -r local-dir user@host:/remote/`. "
        "Preserve perms + times: `scp -p ...`. "
        "Non-default port: `scp -P 2222 ...` (note: capital P, vs ssh's lowercase). "
        "OpenSSH 9.0+ disabled the legacy `scp` protocol — use SFTP under the hood or `scp -O` for legacy.",
        f"{BASE}/scp.1",
    ),
    (
        "ssh-recipes", "ssh recipe: SFTP interactive transfer",
        "`sftp user@host` enters interactive prompt. "
        "Common: `cd`, `ls`, `get file.txt`, `put local.txt`, `mkdir`, `bye`. "
        "Resume interrupted transfer: `reget file.txt`. "
        "Recursive: `get -r remote-dir`. "
        "Batch mode: `sftp -b commands.txt user@host`. "
        "For one-off without prompt: scp is simpler.",
        f"{BASE}/sftp.1",
    ),
    (
        "ssh-recipes", "ssh recipe: mount remote dir via sshfs (FUSE)",
        "Install: `apt install sshfs` (Linux) or `brew install macfuse sshfs` (macOS). "
        "Mount: `mkdir -p ~/remote && sshfs user@host:/remote/path ~/remote`. "
        "Unmount: `fusermount -u ~/remote` (Linux) or `umount ~/remote` (macOS). "
        "Useful for IDE editing of remote files. "
        "Slow for many small files (each is a separate SFTP roundtrip).",
        f"{BASE}/sftp.1",
    ),
    (
        "ssh-recipes", "ssh recipe: restrict authorized_keys (force command, no-pty, from)",
        "Tight restrictions on key in `authorized_keys`: ```\\nfrom=\"10.0.0.0/8,bastion.example.com\",no-pty,no-port-forwarding,no-agent-forwarding,command=\"/usr/local/bin/backup.sh\" ssh-ed25519 AAAAC3... user@machine\\n```. "
        "`from=`: only allow these IPs/hosts. "
        "`command=`: run this on login (ignores user's requested command). "
        "`no-pty`: no terminal. "
        "Critical for non-interactive deploy/backup keys.",
        f"{BASE}/sshd.8",
    ),
    (
        "ssh-recipes", "ssh recipe: ssh-agent for key passphrase caching",
        "Start: `eval $(ssh-agent -s)` (output sets env vars). "
        "Add: `ssh-add ~/.ssh/id_ed25519` (prompts for passphrase, caches in agent). "
        "List: `ssh-add -l`. "
        "Remove: `ssh-add -d ~/.ssh/id_ed25519`. "
        "Clear all: `ssh-add -D`. "
        "macOS Keychain: `ssh-add --apple-use-keychain ~/.ssh/key` (passphrase stored persistently). "
        "Auto-start on login: most distros do this via systemd-user.",
        f"{BASE}/ssh-agent.1",
    ),
    (
        "ssh-recipes", "ssh recipe: ssh-keyscan to populate known_hosts safely",
        "`ssh-keyscan -t ed25519 host >> ~/.ssh/known_hosts` (adds host key for ed25519). "
        "All algos: `ssh-keyscan host >> ~/.ssh/known_hosts`. "
        "Multiple hosts: `ssh-keyscan -t ed25519 host1 host2 host3`. "
        "Hashed entries (safer — doesn't reveal hostnames): `-H`. "
        "ALWAYS verify fingerprint out-of-band before trusting.",
        f"{BASE}/ssh-keyscan.1",
    ),
    (
        "ssh-recipes", "ssh recipe: verify a host key fingerprint",
        "Get fingerprint of a known host key: `ssh-keygen -lf <keyfile>` or `ssh-keyscan host | ssh-keygen -lf -`. "
        "Compare with what server admin advertised (out-of-band). "
        "Modern format: SHA256 base64. "
        "Old MD5 hex: `ssh-keygen -E md5 -lf key.pub`. "
        "On first connection: ssh shows fingerprint — match before typing 'yes'.",
        f"{BASE}/ssh-keygen.1",
    ),
    (
        "ssh-recipes", "ssh recipe: forward X11 (GUI from remote)",
        "`ssh -X user@host` (trusted forward — fast, less safe). "
        "Safer (untrusted): `ssh -Y user@host`. "
        "Server side: `sshd_config` `X11Forwarding yes`. "
        "Then run remote GUI: `xclock`, `xeyes`. "
        "macOS: requires XQuartz running locally. "
        "For modern: VNC / NoMachine for full desktop; X11 for single apps.",
        f"{BASE}/ssh.1",
    ),
    (
        "ssh-recipes", "ssh recipe: debug a failing SSH connection",
        "`ssh -v user@host` (one v: basic). "
        "More: `ssh -vv` or `ssh -vvv` (most verbose). "
        "Specific subsystem: `ssh -o LogLevel=DEBUG3 ...`. "
        "Server side: `journalctl -u sshd -f` watches sshd logs live. "
        "For specific config trace: `ssh -G user@host` prints final resolved config (no connection). "
        "Per-host probing: `ssh-keyscan -v host` shows handshake details.",
        f"{BASE}/ssh.1",
    ),
    (
        "ssh-recipes", "ssh recipe: harden sshd_config (production server)",
        "/etc/ssh/sshd_config: ```\\nPort 22                       # or change for obscurity\\nPermitRootLogin no            # never allow direct root\\nPasswordAuthentication no     # keys only\\nPubkeyAuthentication yes\\nChallengeResponseAuthentication no\\nUsePAM yes                    # for OS-level account features\\nMaxAuthTries 3\\nMaxStartups 10:30:60\\nLoginGraceTime 30\\nAllowUsers alice bob          # explicit allowlist\\nClientAliveInterval 300\\nClientAliveCountMax 2\\n```. "
        "Test: `sshd -t` (config syntax). Restart: `sudo systemctl restart sshd`.",
        f"{BASE}/sshd_config.5",
    ),
    (
        "ssh-recipes", "ssh recipe: use SSH for git",
        "(1) Generate key: `ssh-keygen -t ed25519 -C 'git@github.com'`; "
        "(2) Add public key to GitHub/GitLab: Settings → SSH keys → paste contents of `~/.ssh/id_ed25519.pub`; "
        "(3) Test: `ssh -T git@github.com`; should print 'Hi <user>'; "
        "(4) Clone: `git clone git@github.com:user/repo.git`; "
        "(5) For multiple accounts: ~/.ssh/config with Host aliases + `IdentityFile` per account.",
        f"{BASE}/ssh.1",
    ),
    (
        "ssh-recipes", "ssh recipe: SSH for rsync (custom port, key)",
        "`rsync -avz -e 'ssh -p 2222 -i ~/.ssh/key' /local/ user@host:/remote/`. "
        "Standard: rsync uses ssh by default. "
        "For jump host: `rsync -e 'ssh -J bastion' ...`. "
        "Reuse a multiplexed connection (super fast): set up ControlMaster first, then rsync uses it automatically.",
        f"{BASE}/ssh.1",
    ),
    (
        "ssh-recipes", "ssh recipe: SSH certificates (CA-signed instead of authorized_keys)",
        "Server CA setup: ```bash\\nssh-keygen -t ed25519 -f ssh_ca  # generate CA key\\ncat ssh_ca.pub > /etc/ssh/ca.pub\\n# sshd_config: TrustedUserCAKeys /etc/ssh/ca.pub\\n```. "
        "User certificate: `ssh-keygen -s ssh_ca -I 'alice@laptop' -n alice -V +1d ~/.ssh/id_ed25519.pub` (produces id_ed25519-cert.pub). "
        "User connects: ssh automatically presents the cert. "
        "Benefits: short-lived (`-V +1d`), centrally revocable, no per-server authorized_keys updates. "
        "Tools: smallstep step-ca, Vault SSH backend, Teleport.",
        f"{BASE}/ssh-keygen.1",
    ),
    (
        "ssh-recipes", "ssh recipe: reverse SSH tunnel for behind-NAT access",
        "From the NAT'd box: `ssh -fNR 2222:localhost:22 jumpuser@public-host` (opens port 2222 on public-host that tunnels to local 22). "
        "From elsewhere: `ssh -p 2222 user@public-host` connects through to the NAT'd box. "
        "For persistent: `autossh -M 0 -fNR 2222:localhost:22 jumpuser@public-host` (autossh restarts on disconnect). "
        "Modern alternative: cloudflared / Tailscale for NAT-traversal without manual tunnels.",
        f"{BASE}/ssh.1",
    ),
    (
        "ssh-recipes", "ssh recipe: disable password auth (keys only)",
        "/etc/ssh/sshd_config on server: ```\\nPasswordAuthentication no\\nChallengeResponseAuthentication no\\nUsePAM yes  # still on for account features but not password auth\\nPubkeyAuthentication yes\\n```. "
        "Verify after: `ssh -o PreferredAuthentications=password user@host` should fail. "
        "Test pubkey: `ssh -i ~/.ssh/key user@host` should succeed. "
        "Restart: `sudo systemctl restart sshd`. "
        "Test BEFORE locking yourself out: keep one session open while restarting.",
        f"{BASE}/sshd_config.5",
    ),
    (
        "ssh-recipes", "ssh recipe: SSH over port 443 (corporate firewall workaround)",
        "Server sshd_config: `Port 443` (or alongside 22). "
        "Restart sshd. "
        "Connect: `ssh -p 443 user@host`. "
        "Caveat: port 443 normally TLS — many firewalls deep-inspect. "
        "For TLS-wrapped SSH (looks more like HTTPS): use `sslh` (multiplex SSH + HTTPS on same port).",
        f"{BASE}/sshd_config.5",
    ),
    (
        "ssh-recipes", "ssh recipe: SSH key for CI / non-interactive",
        "(1) Generate deploy key locally: `ssh-keygen -t ed25519 -f deploy_key -N ''` (no passphrase); "
        "(2) Add public key (deploy_key.pub) to target server's authorized_keys; "
        "(3) Add private key as CI secret (GitHub: settings → secrets); "
        "(4) In CI: `echo \"$SSH_KEY\" > key && chmod 600 key && ssh -i key -o StrictHostKeyChecking=accept-new user@host 'deploy'`; "
        "(5) For known_hosts pinning: `ssh-keyscan -t ed25519 host` once, save output, add as another secret + write to known_hosts in CI.",
        f"{BASE}/ssh-keygen.1",
    ),
    (
        "ssh-recipes", "ssh recipe: convert key formats",
        "PEM → OpenSSH: `ssh-keygen -p -m PEM -f key` (converts in place). "
        "OpenSSH → PEM: `ssh-keygen -p -m PEM -f key` (already PEM after this). "
        "Extract public from private: `ssh-keygen -y -f private_key > public_key.pub`. "
        "PuTTY .ppk → OpenSSH: `puttygen key.ppk -O private-openssh -o id_rsa`. "
        "Change passphrase: `ssh-keygen -p -f key`.",
        f"{BASE}/ssh-keygen.1",
    ),
    (
        "ssh-recipes", "ssh recipe: SSH agent forwarding (-A) with caution",
        "`ssh -A user@host` forwards local ssh-agent socket. "
        "Server now can use your keys to ssh elsewhere (e.g., for git on remote dev box). "
        "SECURITY: root on the remote can hijack your agent — only forward to FULLY TRUSTED hosts. "
        "Safer alternative: ProxyJump (keys stay local). "
        "Per-host config: `Host trusted-jump-box ForwardAgent yes`. "
        "Verify on remote: `ssh-add -l` should list your keys.",
        f"{BASE}/ssh.1",
    ),
    (
        "ssh-recipes", "ssh recipe: limit user shells / forced commands",
        "Authorized_keys: `command=\"rsync --server -av .\" ssh-ed25519 AAAA...` — backup-only user, can only run rsync. "
        "Restrict source IPs: `from=\"203.0.113.0/24\"`. "
        "Stack restrictions: `from=\"...\",no-pty,no-X11-forwarding,no-agent-forwarding,no-port-forwarding,command=\"...\"`. "
        "User shell-level: in passwd set shell to `/bin/false` for non-interactive users (still allows scp/sftp via authorized_keys command).",
        f"{BASE}/sshd.8",
    ),
    (
        "ssh-recipes", "ssh recipe: monitor active SSH sessions",
        "On the server: `w` (who is logged in + what they're doing); `who`; `last -n 20`. "
        "From systemd: `journalctl -u sshd -e` (recent sshd events including logins). "
        "Specific user: `loginctl list-sessions` then `loginctl show-session <id>`. "
        "Kill stuck session: `pkill -KILL -t pts/2` (kill all processes attached to pts/2 — careful).",
        f"{BASE}/sshd.8",
    ),
    (
        "ssh-recipes", "ssh recipe: rate-limit SSH brute force (fail2ban)",
        "Install: `apt install fail2ban` (Linux). "
        "Edit `/etc/fail2ban/jail.local`: ```ini\\n[sshd]\\nenabled = true\\nport = ssh\\nfilter = sshd\\nmaxretry = 3\\nbantime = 1h\\nfindtime = 10m\\n```. "
        "Restart: `systemctl restart fail2ban`. "
        "Check status: `fail2ban-client status sshd`. "
        "Modern alternative: CrowdSec (community-driven IP reputation).",
        f"{BASE}/sshd.8",
    ),
    (
        "ssh-recipes", "ssh recipe: ssh into Docker container",
        "If container runs sshd: `ssh -p <mapped-port> root@host`. "
        "Without sshd (preferred): `docker exec -it <container> bash`. "
        "For remote Docker daemon: `DOCKER_HOST=ssh://user@host docker ps` (uses ssh to talk to remote dockerd). "
        "For docker-machine style: `docker context use my-remote`.",
        f"{BASE}/ssh.1",
    ),
    (
        "ssh-recipes", "ssh recipe: production-grade ssh CLI invocation",
        "```bash\\nssh \\\\\\n  -o BatchMode=yes \\\\                # no prompts (CI)\\n  -o ConnectTimeout=10 \\\\\\n  -o StrictHostKeyChecking=accept-new \\\\\\n  -o UserKnownHostsFile=~/.ssh/known_hosts \\\\\\n  -o ServerAliveInterval=30 \\\\\\n  -o IdentitiesOnly=yes \\\\\\n  -i ~/.ssh/deploy_key \\\\\\n  -p 22 \\\\\\n  deploy@prod-host 'systemctl reload nginx'\\n```. "
        "BatchMode prevents hanging on prompts. accept-new is safer than `no`. IdentitiesOnly prevents 'Too many auth failures'.",
        f"{BASE}/ssh_config.5",
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
            evidence_id=f"ev-ssh-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-ssh-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="ssh-catalog",
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
