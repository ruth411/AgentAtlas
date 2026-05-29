"""Direct-insert ~25 dnf error claims + ~25 dnf recipe claims."""

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

BASE = "https://dnf.readthedocs.io/en/latest"
RPM_GUIDE = "https://rpm-packaging-guide.github.io"

CLAIMS = [
    # ============================================================
    # dnf-errors
    # ============================================================
    (
        "dnf-errors", "dnf error: Error: Unable to find a match: package-name",
        "dnf can't find a package by that name. Causes: typo, package not in enabled repos, EPEL/CRB component disabled, name changed between releases. "
        "Fix: (1) `dnf search <keyword>` for fuzzy match; (2) `dnf repolist` to see what's enabled; (3) enable EPEL: `dnf install epel-release` (RHEL/Rocky/Alma); "
        "(4) enable CodeReady Builder (CRB) for some dev libs: `dnf config-manager --set-enabled crb`; "
        "(5) `dnf provides <command>` finds which package provides a binary or file.",
        f"{BASE}/command_ref.html",
    ),
    (
        "dnf-errors", "dnf error: Could not retrieve mirrorlist",
        "Network or mirror metadata problem. dnf couldn't reach the mirrorlist URL. "
        "Fix: (1) `ping mirrors.fedoraproject.org` to test network; (2) `dnf clean all && dnf makecache` to refresh; "
        "(3) for corporate proxy: edit /etc/dnf/dnf.conf and add `proxy=http://proxy:8080`; "
        "(4) if a specific repo's mirrorlist is dead, disable it temporarily: `dnf --disablerepo=<broken-repo> install <pkg>`; "
        "(5) switch to a specific baseurl in /etc/yum.repos.d/<repo>.repo if you need a particular mirror.",
        f"{BASE}/conf_ref.html",
    ),
    (
        "dnf-errors", "dnf error: Transaction check error",
        "dnf detected file conflicts between packages during the install/upgrade transaction. Two packages try to install the same file path. "
        "Fix: (1) read the message — it names both packages and the conflicting file; "
        "(2) decide which package should provide the file; "
        "(3) for replacement scenarios: `dnf swap <old> <new>`; "
        "(4) for legitimate cross-package files, the maintainers should have used Conflicts: in spec files — file a bug; "
        "(5) emergency: `rpm -i --force <pkg>.rpm` overwrites, but this masks the real issue.",
        f"{BASE}/command_ref.html",
    ),
    (
        "dnf-errors", "dnf error: failed loading plugin",
        "A dnf plugin (Python) failed to import. Common after dnf upgrades or Python version changes. "
        "Fix: (1) the error names the plugin file; check its Python compatibility; "
        "(2) temporarily disable plugins: `dnf --disableplugin=<name> install <pkg>`; "
        "(3) `dnf --noplugins install <pkg>` skips all plugins; "
        "(4) reinstall the plugin's package: `dnf install --reinstall <pkg-providing-plugin>`; "
        "(5) for development plugins, ensure dnf-plugins-core matches dnf version.",
        f"{BASE}/api.html",
    ),
    (
        "dnf-errors", "dnf error: cannot allocate memory",
        "dnf's libsolv resolver ran out of memory. Happens on tiny VMs (< 1GB RAM) with large transactions. "
        "Fix: (1) add swap temporarily: `fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile`; "
        "(2) break the transaction into chunks: `dnf install pkg1 pkg2` then `dnf install pkg3 pkg4`; "
        "(3) for containers: bump the memory limit; for VMs: increase RAM allocation.",
        f"{BASE}/cli_usage.html",
    ),
    (
        "dnf-errors", "dnf error: Error: GPG check FAILED",
        "Package signature doesn't match the imported keys. Either the package is tampered, key is wrong, or repo is misconfigured. "
        "Fix: (1) verify with the upstream: re-download from official source; "
        "(2) import the right key: `rpm --import <url-to-public-key>`; "
        "(3) for development/local repos: `dnf install --nogpgcheck` (DANGEROUS — only for trusted local files); "
        "(4) for permanent disable on a repo (NOT recommended): edit /etc/yum.repos.d/<repo>.repo and set `gpgcheck=0`; "
        "(5) check what keys exist: `rpm -q gpg-pubkey --qf '%{summary}\\n'`.",
        f"{BASE}/conf_ref.html",
    ),
    (
        "dnf-errors", "dnf error: package is excluded",
        "An exclude= line in dnf.conf or a repo file blocks this package. "
        "Fix: (1) check global: `grep -r exclude /etc/dnf/dnf.conf /etc/yum.repos.d/`; "
        "(2) temporary override: `dnf install --disableexcludes=all <pkg>`; "
        "(3) per-repo: `--disableexcludes=<repo>` or `--disableexcludes=main`; "
        "(4) `dnf module list` if it's a modular package — exclusion behavior differs there.",
        f"{BASE}/conf_ref.html",
    ),
    (
        "dnf-errors", "dnf error: protected packages cannot be removed",
        "dnf refuses to remove packages marked as protected (typically essential system packages: dnf itself, glibc, systemd). "
        "Fix: (1) you usually shouldn't override this; (2) check what's protected: `cat /etc/dnf/protected.d/*.conf`; "
        "(3) for legitimate edge case (replacing systemd with a fork): `dnf remove --setopt=protected_packages= <pkg>` strips the protection list — VERY risky; "
        "(4) for replacement scenarios use `dnf swap` instead.",
        f"{BASE}/conf_ref.html",
    ),
    (
        "dnf-errors", "dnf error: module is already enabled",
        "DNF modularity tried to enable a stream that's already active. "
        "Fix: (1) `dnf module list <name>` shows current state; (2) `dnf module reset <name>` clears the enabled stream; "
        "(3) then enable the desired stream: `dnf module enable <name>:<stream>`; "
        "(4) install profiles: `dnf module install <name>:<stream>/<profile>`.",
        f"{BASE}/modularity.html",
    ),
    (
        "dnf-errors", "dnf error: nothing provides X needed by Y",
        "Dependency resolution failed — Y depends on something not available in any enabled repo. "
        "Fix: (1) `dnf provides <X>` to find what could supply it; (2) enable additional repos: EPEL, CRB, RPM Fusion; "
        "(3) check if it's a specific minor-version requirement: `dnf list <X>` lists what versions exist; "
        "(4) use `dnf install --best --allowerasing` to let dnf remove conflicting packages; "
        "(5) for completely unsatisfiable in stable, check if there's a backport via Copr.",
        f"{BASE}/command_ref.html",
    ),
    (
        "dnf-errors", "dnf error: lockfile already exists",
        "Another dnf or rpm process is running. Or one crashed leaving a stale lock. "
        "Fix: (1) `ps aux | grep -E 'dnf|rpm'` shows running processes — wait or kill; "
        "(2) stale lock: `rm -f /var/lib/dnf/dnf.lock /var/lib/rpm/.rpm.lock`; "
        "(3) ALWAYS confirm no real process before removing; (4) for unattended-upgrades (dnf-automatic) conflicts: stop the timer with `systemctl stop dnf-automatic.timer`.",
        f"{BASE}/conf_ref.html",
    ),
    (
        "dnf-errors", "dnf error: rpm: error: cannot get exclusive lock on /var/lib/rpm",
        "Same family as dnf lock errors — rpm's underlying database is locked. "
        "Fix: (1) check for running rpm/dnf; (2) if absolutely sure nothing's running: `rm -f /var/lib/rpm/.rpm.lock`; "
        "(3) rebuild corrupted DB: `rpm --rebuilddb` (safe; takes 1-2 min); "
        "(4) for serious corruption, `cd /var/lib/rpm && rm -f __db* && rpm --rebuilddb`.",
        f"{BASE}/cli_usage.html",
    ),
    (
        "dnf-errors", "dnf warning: package is already installed (skip)",
        "dnf detected the package is already at the same version. Not an error but worth noting in scripts. "
        "Fix: (1) for idempotent install scripts, this is fine — dnf no-ops; "
        "(2) force reinstall: `dnf reinstall <pkg>`; "
        "(3) upgrade only if newer available: `dnf upgrade <pkg>`; "
        "(4) install + upgrade in one shot: `dnf install --best <pkg>`.",
        f"{BASE}/command_ref.html",
    ),
    (
        "dnf-errors", "dnf error: BDB1539 (yum db corrupted, rpm-bdb to rpm-sqlite migration)",
        "Older RHEL/Fedora used Berkeley DB for rpm; newer uses sqlite. Migration can leave stale files. "
        "Fix: (1) `rpm --rebuilddb` typically resolves; "
        "(2) for explicit migration: `rpmdb --rebuilddb` then `cp -p /var/lib/rpm/* /backup/`; "
        "(3) on RHEL 9+ Fedora 36+, sqlite is default — confirm with `rpm -q --queryformat '%{rpmdb_version}\\n' rpm`; "
        "(4) for serious corruption with backed-up rpmdb: restore from backup, retry.",
        f"{BASE}/release_notes.html",
    ),
    (
        "dnf-errors", "dnf error: kernel package upgrade leaves old kernel behind",
        "Each kernel install ADDS a version; dnf doesn't auto-remove old ones. Risk: /boot fills up. "
        "Fix: (1) `dnf remove $(dnf repoquery --installonly --latest-limit=-2)` keeps 2 newest, removes older; "
        "(2) set permanent: `installonly_limit=2` in /etc/dnf/dnf.conf (default 3); "
        "(3) check installed: `rpm -qa kernel`; "
        "(4) for /boot space tight: `dnf clean all && dnf autoremove`.",
        f"{BASE}/conf_ref.html",
    ),
    (
        "dnf-errors", "dnf error: cannot find a valid baseurl for repo",
        "A repo .repo file is missing both a working baseurl AND a working mirrorlist. "
        "Fix: (1) inspect /etc/yum.repos.d/<repo>.repo — `baseurl=` and `mirrorlist=` lines; "
        "(2) test the URL in a browser/curl; (3) if mirrorlist returns 404, fall back to baseurl: edit and uncomment baseurl line; "
        "(4) for old repos: maybe the project moved — search for a successor; "
        "(5) disable the broken repo: `dnf config-manager --set-disabled <name>` if you no longer need it.",
        f"{BASE}/conf_ref.html",
    ),
    (
        "dnf-errors", "dnf error: Curl error (6): Couldn't resolve host name",
        "DNS failure when fetching from a mirror. "
        "Fix: (1) `nslookup <hostname>` to test DNS; (2) check /etc/resolv.conf; "
        "(3) if behind a VPN/corp network, set proxy in /etc/dnf/dnf.conf: `proxy=http://proxy:8080`; "
        "(4) `dnf --setopt=timeout=60 install <pkg>` for slow DNS to give it more time.",
        f"{BASE}/conf_ref.html",
    ),
    (
        "dnf-errors", "dnf error: cannot remove package required by other packages",
        "You tried `dnf remove <pkg>` but other packages depend on it. dnf normally removes them too (with confirmation). "
        "Fix: (1) `dnf remove <pkg>` will show what gets removed in cascade — review carefully; "
        "(2) to remove just the package + auto-remove orphan deps: `dnf remove <pkg> && dnf autoremove`; "
        "(3) to remove ONLY this package even with deps still needing it (dangerous, breaks them): `rpm -e --nodeps <pkg>` — use with extreme caution.",
        f"{BASE}/command_ref.html",
    ),
    (
        "dnf-errors", "dnf error: subscription manager (RHEL)",
        "On RHEL, dnf requires an active subscription to install from official Red Hat repos. "
        "Fix: (1) `sudo subscription-manager register --username=<user> --password=<pass>`; "
        "(2) attach a subscription: `sudo subscription-manager attach --auto`; "
        "(3) for unsubscribed dev systems, use Rocky Linux or AlmaLinux (RHEL-compatible, no subscription); "
        "(4) for RHEL Developer Subscription (free): `sudo subscription-manager register --activationkey=<key>`.",
        f"{BASE}/cli_usage.html",
    ),
    (
        "dnf-errors", "dnf error: signature could not be verified for repo",
        "A repo's repodata signature failed verification. Similar to GPG check but at the metadata level. "
        "Fix: (1) re-import the repo's signing key (often the .repo file has gpgkey= pointing at it); "
        "(2) `dnf clean metadata && dnf makecache`; "
        "(3) for trusted local repos: `repo_gpgcheck=0` in the .repo file (only for fully-trusted internal mirrors).",
        f"{BASE}/conf_ref.html",
    ),
    (
        "dnf-errors", "dnf error: Insufficient space in download directory",
        "/var/cache/dnf is full. "
        "Fix: (1) `dnf clean all` removes downloaded .rpms + metadata; (2) `df -h /var/cache/dnf`; "
        "(3) move cache to a bigger disk: in /etc/dnf/dnf.conf set `cachedir=/path/to/big-disk`; "
        "(4) keepcache=False in /etc/dnf/dnf.conf to drop downloads after install.",
        f"{BASE}/conf_ref.html",
    ),
    (
        "dnf-errors", "dnf error: error: Failed dependencies in rpm",
        "Lower-level rpm error (not dnf). You tried `rpm -i <pkg>.rpm` and dependencies aren't installed. "
        "Fix: (1) ALWAYS prefer dnf for resolving deps: `dnf install ./local.rpm`; "
        "(2) if rpm-only: install deps first manually; "
        "(3) for emergencies: `rpm -i --nodeps <pkg>` (creates broken installs); "
        "(4) to inspect a broken install: `rpm -V <pkg>` lists files that differ.",
        f"{BASE}/cli_usage.html",
    ),
    (
        "dnf-errors", "dnf error: rpmdb: BDB0113 Thread/process",
        "Berkeley DB process died while holding rpm's lock. Common during force-killed dnf/rpm. "
        "Fix: (1) `rpm --rebuilddb` (clean rebuild); (2) for stuck cases: `cd /var/lib/rpm && rm __db* && rpm --rebuilddb`; "
        "(3) on RHEL 9+: rpm uses sqlite, not BDB — this error means a really old system or a containerized RPM DB issue.",
        f"{BASE}/release_notes.html",
    ),
    (
        "dnf-errors", "dnf error: 404 Not Found on package download",
        "The metadata says the .rpm exists at a URL, but the file is gone. Mirror is out of sync. "
        "Fix: (1) `dnf clean metadata` + `dnf makecache` — refresh; (2) `dnf --refresh install <pkg>` forces full refresh; "
        "(3) try a different mirror by editing /etc/yum.repos.d/<repo>.repo's baseurl; "
        "(4) for transient mirror lag, wait a few minutes and retry.",
        f"{BASE}/cli_usage.html",
    ),
    (
        "dnf-errors", "dnf error: Module X profile Y is not installed",
        "DNF modules have profiles (sets of packages). You referenced a profile that wasn't installed. "
        "Fix: (1) `dnf module list <name>` shows available streams + profiles; "
        "(2) install the profile: `dnf module install <name>:<stream>/<profile>`; "
        "(3) for default profile: `dnf module install <name>:<stream>` (no `/profile`).",
        f"{BASE}/modularity.html",
    ),

    # ============================================================
    # dnf-recipes — workflow patterns
    # ============================================================
    (
        "dnf-recipes", "dnf recipe: install a package",
        "Standard install. `sudo dnf install <pkg>` resolves deps and installs. Multiple at once: `sudo dnf install pkg1 pkg2 pkg3`. "
        "Non-interactive: `sudo dnf -y install <pkg>` accepts all prompts (use in scripts). "
        "Install + best-version (replace existing if needed): `sudo dnf install --best <pkg>`. "
        "Install from local .rpm: `sudo dnf install ./local.rpm` (dnf handles deps; better than `rpm -i`). "
        "From URL: `sudo dnf install https://example.com/file.rpm`.",
        f"{BASE}/command_ref.html",
    ),
    (
        "dnf-recipes", "dnf recipe: search for packages",
        "Find packages by name/description. `dnf search <keyword>` searches name + summary. "
        "Description match: `dnf search all <keyword>` includes long descriptions. "
        "Find what provides a file: `dnf provides /usr/bin/missing-command` or `dnf provides '*/binary-name'`. "
        "Find by group: `dnf group list` shows package groups; `dnf group info 'Development Tools'` shows what's in a group; `dnf group install 'Development Tools'` installs the group.",
        f"{BASE}/command_ref.html",
    ),
    (
        "dnf-recipes", "dnf recipe: upgrade the system",
        "Standard upgrade. `sudo dnf upgrade` upgrades all installed packages. `sudo dnf upgrade <pkg>` for one package. "
        "Security-only: `sudo dnf upgrade --security` (RHEL/Fedora). "
        "Bugfix-only: `--bugfix`. Both: `--secseverity=Important` for critical-only. "
        "Distribution upgrade (major version, e.g., Fedora 38 → 39): `sudo dnf --refresh upgrade`. For full release upgrade use the `dnf system-upgrade` plugin.",
        f"{BASE}/command_ref.html",
    ),
    (
        "dnf-recipes", "dnf recipe: enable EPEL on RHEL/Rocky/Alma",
        "EPEL (Extra Packages for Enterprise Linux) provides community packages. Steps: "
        "(1) `sudo dnf install -y epel-release`; "
        "(2) for RHEL also enable CRB (CodeReady Linux Builder): `sudo dnf config-manager --set-enabled crb`; "
        "(3) on Rocky/Alma it's `powertools` (RHEL 8) or `crb` (RHEL 9+); "
        "(4) verify: `dnf repolist | grep epel`. "
        "Common packages now available: htop, jq, nginx, mosh, neofetch, certbot, fail2ban.",
        f"{BASE}/use_cases.html",
    ),
    (
        "dnf-recipes", "dnf recipe: add a third-party repo with signed key",
        "Modern, secure pattern. Steps: "
        "(1) get the upstream's GPG public key URL; "
        "(2) import: `sudo rpm --import https://example.com/RPM-GPG-KEY`; "
        "(3) write a .repo file at /etc/yum.repos.d/example.repo with: `[example]` `name=Example Repo` `baseurl=https://example.com/rpm/$basearch/` `gpgcheck=1` `gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-example` `enabled=1`; "
        "(4) `sudo dnf makecache` to fetch metadata; "
        "(5) `sudo dnf install <pkg>`.",
        f"{BASE}/conf_ref.html",
    ),
    (
        "dnf-recipes", "dnf recipe: clean dnf cache",
        "Reclaim disk. `dnf clean all` removes downloaded .rpms + metadata. "
        "Granular: `dnf clean packages` (just .rpms), `dnf clean metadata` (just repo indexes), `dnf clean expire-cache` (mark metadata as expired so next op re-fetches). "
        "After cleanup: `dnf makecache` proactively re-downloads metadata so next op is fast. "
        "For permanent: `keepcache=False` in /etc/dnf/dnf.conf to never keep .rpms after install.",
        f"{BASE}/conf_ref.html",
    ),
    (
        "dnf-recipes", "dnf recipe: list installed packages",
        "Various filters. `dnf list installed` (all installed). "
        "Recent additions: `dnf history` shows recent transactions; `dnf history info <id>` for details. "
        "User-installed (not pulled in as deps): `dnf repoquery --userinstalled`. "
        "From a specific repo: `dnf list installed --installed-from-repo=<name>` (limited support). "
        "Counting: `rpm -qa | wc -l`.",
        f"{BASE}/command_ref.html",
    ),
    (
        "dnf-recipes", "dnf recipe: install a specific older version",
        "Pin to a version. `dnf install <pkg>-<version>` (full version-release). "
        "Find available versions: `dnf list --showduplicates <pkg>`. "
        "Downgrade: `dnf downgrade <pkg>` (or `dnf downgrade <pkg>-<version>` for specific). "
        "Lock at this version: install dnf-plugin-versionlock then `dnf versionlock add <pkg>` to pin it against upgrades.",
        f"{BASE}/command_ref.html",
    ),
    (
        "dnf-recipes", "dnf recipe: setup dnf-automatic for unattended updates",
        "Auto-install security updates. Steps: "
        "(1) `sudo dnf install dnf-automatic`; "
        "(2) edit /etc/dnf/automatic.conf: `apply_updates = yes` (also `upgrade_type = security` for security-only); "
        "(3) `sudo systemctl enable --now dnf-automatic.timer`; "
        "(4) verify: `systemctl list-timers | grep dnf-automatic`; "
        "(5) for email notifications: configure `[email]` section with sender/SMTP. "
        "Logs in /var/log/dnf.log.",
        f"{BASE}/automatic.html",
    ),
    (
        "dnf-recipes", "dnf recipe: configure dnf modularity",
        "DNF modules let you choose a software stream (e.g., postgresql:13 vs postgresql:15). "
        "(1) list available: `dnf module list`; (2) see streams/profiles for one: `dnf module info <name>`; "
        "(3) enable a stream: `sudo dnf module enable nodejs:18`; "
        "(4) install with profile: `sudo dnf module install nodejs:18/development`; "
        "(5) switch streams (potentially breaks pkg state): `sudo dnf module reset nodejs && sudo dnf module install nodejs:20`. "
        "Note: modularity is reduced/removed in RHEL 10 — check upstream direction before relying heavily.",
        f"{BASE}/modularity.html",
    ),
    (
        "dnf-recipes", "dnf recipe: query rpm database",
        "Inspect installed packages and their files. "
        "`rpm -qa` (all installed). `rpm -q <pkg>` (just this one, version-release). "
        "What files does a package install: `rpm -ql <pkg>`. Which package owns a file: `rpm -qf /path/to/file`. "
        "Package info: `rpm -qi <pkg>` (description, license, build date). Verify integrity: `rpm -V <pkg>` (lists changed files). "
        "Scripts: `rpm -q --scripts <pkg>` shows pre/post-install hooks. "
        "Use case: agent asks 'which RPM provides systemctl' → `rpm -qf $(which systemctl)`.",
        f"{BASE}/cli_usage.html",
    ),
    (
        "dnf-recipes", "dnf recipe: build an RPM package from source (rpmbuild)",
        "Convert source into a .rpm. Steps: "
        "(1) `sudo dnf install rpmdevtools rpm-build`; "
        "(2) `rpmdev-setuptree` creates ~/rpmbuild/ structure (BUILD, BUILDROOT, RPMS, SOURCES, SPECS, SRPMS); "
        "(3) drop source tarball in ~/rpmbuild/SOURCES/; "
        "(4) write a spec file in ~/rpmbuild/SPECS/<name>.spec describing the build; "
        "(5) `rpmbuild -ba ~/rpmbuild/SPECS/<name>.spec` builds source + binary RPMs; "
        "(6) outputs in ~/rpmbuild/RPMS/<arch>/ and ~/rpmbuild/SRPMS/. "
        "Install your custom: `sudo dnf install ~/rpmbuild/RPMS/x86_64/<name>-<ver>.rpm`.",
        f"{RPM_GUIDE}/chapters/practical-software-packaging.html",
    ),
    (
        "dnf-recipes", "dnf recipe: write a basic .spec file",
        "Minimum sections. ```\n%define name myapp\nName: %{name}\nVersion: 1.0.0\nRelease: 1%{?dist}\nSummary: One-line description\nLicense: MIT\nURL: https://example.com\nSource0: %{name}-%{version}.tar.gz\nBuildRequires: gcc, make\nRequires: glibc\n\n%description\nLonger description.\n\n%prep\n%setup -q\n\n%build\nmake\n\n%install\nmake install DESTDIR=%{buildroot}\n\n%files\n/usr/bin/myapp\n\n%changelog\n* Wed Jan 1 2025 Maintainer <name@email> 1.0.0-1\n- Initial release.\n```. "
        "Validate: `rpmlint ~/rpmbuild/SPECS/myapp.spec`.",
        f"{RPM_GUIDE}/chapters/working-with-spec-files.html",
    ),
    (
        "dnf-recipes", "dnf recipe: sign your own RPM",
        "For distributing signed packages. Steps: "
        "(1) create a GPG key: `gpg --gen-key`; "
        "(2) export public key: `gpg --export -a 'Your Name' > RPM-GPG-KEY-yourname`; "
        "(3) add to ~/.rpmmacros: `%_signature gpg` `%_gpg_name Your Name`; "
        "(4) sign existing package: `rpm --addsign <name>.rpm`; "
        "(5) consumers import key: `sudo rpm --import RPM-GPG-KEY-yourname`; "
        "(6) verify: `rpm --checksig <name>.rpm` should say 'OK'.",
        f"{RPM_GUIDE}/chapters/signing-packages.html",
    ),
    (
        "dnf-recipes", "dnf recipe: rollback a transaction (history)",
        "Undo a recent dnf operation. Steps: "
        "(1) `dnf history` lists all transactions with IDs; "
        "(2) `dnf history info <id>` shows what changed; "
        "(3) `sudo dnf history undo <id>` reverts that specific transaction; "
        "(4) for the most recent: `sudo dnf history undo last`; "
        "(5) for a full rollback to a previous state: `sudo dnf history rollback <id>` (reverts to state after that transaction). "
        "Caveat: doesn't restore deleted config files in /etc — those are separate.",
        f"{BASE}/command_ref.html",
    ),
    (
        "dnf-recipes", "dnf recipe: enable Copr personal repos",
        "Copr is Fedora's community build service. Steps: "
        "(1) `sudo dnf install dnf-plugins-core`; "
        "(2) `sudo dnf copr enable <user>/<project>`; (3) `sudo dnf install <pkg>`. "
        "Common Coprs: `peter-bp/cgview-stable`, various tools not in main Fedora. "
        "Caveats: Copr packages are unaudited by Fedora — only enable from sources you trust. List enabled: `dnf copr list`.",
        f"{BASE}/use_cases.html",
    ),
    (
        "dnf-recipes", "dnf recipe: configure dnf behind corporate proxy",
        "Set proxy in /etc/dnf/dnf.conf: `proxy=http://proxy.corp:8080` (and `proxy_username`/`proxy_password` if needed). "
        "For per-repo proxy: in /etc/yum.repos.d/<repo>.repo add `proxy=http://other:8080`. "
        "Test: `sudo dnf clean all && sudo dnf makecache`. "
        "Common alternative: set HTTP_PROXY/HTTPS_PROXY env vars and use `sudo -E dnf` (preserves env).",
        f"{BASE}/conf_ref.html",
    ),
    (
        "dnf-recipes", "dnf recipe: install GNOME / KDE / minimal desktop",
        "Install a desktop environment via groups. "
        "GNOME: `sudo dnf groupinstall 'Workstation Product Environment'` (Fedora) or `'Server with GUI'` (RHEL). "
        "KDE: `sudo dnf groupinstall 'KDE Plasma Workspaces'`. "
        "Minimal X: `sudo dnf groupinstall base-x`. "
        "List all groups: `dnf grouplist -v`. After install: enable graphical target: `sudo systemctl set-default graphical.target && sudo reboot`.",
        f"{BASE}/command_ref.html",
    ),
    (
        "dnf-recipes", "dnf recipe: build minimal container Dockerfile with dnf",
        "Best practices for Dockerfile using dnf. Pattern: ```\nFROM fedora:39\nRUN dnf install -y --setopt=install_weak_deps=False --nodocs \\\n    nginx \\\n  && dnf clean all \\\n  && rm -rf /var/cache/dnf\n```. "
        "Key tactics: `install_weak_deps=False` skips Recommends (smaller image); `--nodocs` skips man pages and docs (saves ~50MB); `clean all` + `rm -rf /var/cache/dnf` drops index cache. "
        "For multi-stage builds: install build deps in stage 1, COPY artifacts to slim stage 2.",
        f"{BASE}/conf_ref.html",
    ),
    (
        "dnf-recipes", "dnf recipe: remove orphaned packages",
        "Clean up packages installed as dependencies but no longer needed. "
        "`sudo dnf autoremove` — automatically removes orphans. "
        "Check what would be removed first: `dnf autoremove --assumeno` (shows but doesn't remove). "
        "Common after major package removal: install nginx + apache → remove nginx → autoremove cleans up nginx-specific deps. "
        "Permanent control: `clean_requirements_on_remove=True` in /etc/dnf/dnf.conf makes regular `dnf remove` more aggressive about removing orphans.",
        f"{BASE}/command_ref.html",
    ),
    (
        "dnf-recipes", "dnf recipe: swap one package for another",
        "Replace a package + its dependents in one transaction. `sudo dnf swap <old> <new>`. "
        "Use cases: switching openssl → libressl, replacing rsyslog with syslog-ng, swapping kernel-core with kernel-debug. "
        "Multi-package swap: dnf shows what gets removed + installed; confirm carefully. "
        "Single-package alternative when no real swap exists: `dnf install --allowerasing <new>` (lets dnf remove conflicting old).",
        f"{BASE}/command_ref.html",
    ),
    (
        "dnf-recipes", "dnf recipe: do a major Fedora release upgrade",
        "Fedora N → N+1. Steps: "
        "(1) `sudo dnf upgrade --refresh` (get current release fully patched); "
        "(2) `sudo dnf install dnf-plugin-system-upgrade`; "
        "(3) `sudo dnf system-upgrade download --releasever=<N+1>`; "
        "(4) `sudo dnf system-upgrade reboot` (system reboots and applies upgrade — takes 30-60min); "
        "(5) after reboot, check: `cat /etc/fedora-release`. "
        "ALWAYS back up first. ALWAYS read the release notes for new version's breaking changes.",
        f"{BASE}/use_cases.html",
    ),
    (
        "dnf-recipes", "dnf recipe: lock packages to specific versions",
        "Prevent specific packages from being upgraded. Steps: "
        "(1) `sudo dnf install python3-dnf-plugin-versionlock`; "
        "(2) `sudo dnf versionlock add <pkg>` locks at current version; "
        "(3) `sudo dnf versionlock list` shows locked; "
        "(4) `sudo dnf versionlock delete <pkg>` unlocks. "
        "Config file: /etc/dnf/plugins/versionlock.list — edit directly if needed. "
        "Use case: keeping a specific Python or PostgreSQL major version even when newer is available.",
        f"{BASE}/use_cases.html",
    ),
    (
        "dnf-recipes", "dnf recipe: check what's pending upgrade",
        "Preview before upgrade. `dnf check-update` lists packages with newer versions available (no changes). Exit code: 0 = nothing to update; 100 = updates available; 1 = error. "
        "Useful in scripts: `if dnf check-update -q; then echo 'No updates'; else echo 'Updates available'; fi`. "
        "Detail: `dnf list --upgrades`. "
        "Filter by repo: `dnf check-update --repo=updates`. "
        "Security-only: `dnf updateinfo list security`.",
        f"{BASE}/command_ref.html",
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
            evidence_id=f"ev-dnf-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-dnf-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="dnf-catalog",
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
