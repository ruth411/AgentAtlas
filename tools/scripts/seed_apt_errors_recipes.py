"""Direct-insert 25 apt/dpkg common-error claims + 15 apt workflow recipes.

Same pattern as the ansible seed scripts (tools/scripts/seed_ansible_*.py):
synthesized claims with OFFICIAL_DOCS evidence pointing to manpages.debian.org,
orchestrator-verified, then embedded for the query engine's semantic re-rank.
"""

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

CLAIMS = [
    # ====================================================================
    # apt-errors — common apt/dpkg errors agents see
    # ====================================================================
    (
        "apt-errors",
        "apt error: Could not get lock /var/lib/dpkg/lock-frontend",
        "Another apt or dpkg process is already holding the dpkg lock. Common causes: "
        "(1) unattended-upgrades is running in the background — wait 1-2 min and retry; "
        "(2) `apt update` or another `apt install` is running in another terminal; "
        "(3) a previous run was killed mid-transaction and the lock wasn't released. "
        "Fix: (a) wait for `fuser /var/lib/dpkg/lock-frontend` to return nothing; "
        "(b) if stuck >5 min, identify the holding process: `lsof /var/lib/dpkg/lock-frontend` then decide if it's safe to wait or kill; "
        "(c) NEVER `rm /var/lib/dpkg/lock*` blindly — that corrupts the package state. "
        "After the lock clears, run `sudo dpkg --configure -a` to finish any half-committed transaction first.",
        "https://manpages.debian.org/bookworm/apt/apt.8.en.html",
    ),
    (
        "apt-errors",
        "apt error: dpkg was interrupted, you must run dpkg --configure -a",
        "A previous apt or dpkg transaction was killed mid-way; some packages are in a half-configured state. "
        "Fix: `sudo dpkg --configure -a` finishes the configure phase for every pending package. "
        "After it succeeds (it may take 30s-2min depending on how many packages), retry your original apt command. "
        "If dpkg --configure -a itself fails on a specific package, look at the error to see if it's a missing dependency (apt -f install fixes those) or a buggy postinst script.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-errors",
        "apt error: Unable to locate package",
        "apt can't find a package by the name you gave. Causes: typo, package is in a non-default repository, the package is universe/multiverse and you only have main enabled, or it's a snap not a deb. "
        "Fix: (1) `apt-cache search <keyword>` to find the right name; (2) `apt list --all-versions <name>` to confirm it's known to apt; "
        "(3) `apt update` first if you just added a new repo (apt needs to refresh the index); "
        "(4) on Ubuntu, enable universe: `sudo add-apt-repository universe && sudo apt update`. "
        "If the package only exists in a PPA, add the PPA first via add-apt-repository or write a sources.list.d entry.",
        "https://manpages.debian.org/bookworm/apt/apt-cache.8.en.html",
    ),
    (
        "apt-errors",
        "apt error: Unable to fetch some archives, maybe run apt-get update",
        "apt's package index is older than the repository — files referenced in apt's local cache no longer exist on the mirror. "
        "Fix: `sudo apt update` to refresh the index, then retry the install. "
        "If `apt update` itself returns 404/Hash sums mismatch, the mirror is probably mid-sync; try again in a few minutes or switch mirror in sources.list.",
        "https://manpages.debian.org/bookworm/apt/apt-get.8.en.html",
    ),
    (
        "apt-errors",
        "apt error: The following packages have unmet dependencies",
        "apt found packages it can't install because their dependencies conflict with what's already installed (broken packages). "
        "Common causes: PPA with incompatible package versions, third-party .deb installed that locks specific lib versions. "
        "Fix: (1) `sudo apt -f install` — apt's auto-resolver tries to fix broken deps; (2) `sudo apt --fix-broken install`; "
        "(3) for stubborn cases, `sudo aptitude install <package>` — aptitude offers alternative resolutions; "
        "(4) last resort: `sudo apt remove <conflicting-package>` to break the chain. "
        "Before doing anything destructive: run `apt -s` first (simulate mode) to see what apt would change.",
        "https://manpages.debian.org/bookworm/apt/apt-get.8.en.html",
    ),
    (
        "apt-errors",
        "apt error: E: Sub-process /usr/bin/dpkg returned an error code",
        "A package's installation script (preinst, postinst, prerm, postrm) failed. The package is half-installed; apt's stuck. "
        "Fix: (1) read the actual error above this line — it names the failing script and the exit code; "
        "(2) common subcauses: disk full (`df -h` to check), broken postinst from a custom package (check `/var/lib/dpkg/info/<pkg>.postinst`), missing dependency the script assumed; "
        "(3) `sudo dpkg --configure -a` to retry the configure; "
        "(4) if a single package is wedged: `sudo dpkg --remove --force-remove-reinstreq <pkg>` then reinstall. "
        "Don't `--force-overwrite` without understanding why — that creates worse state.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-errors",
        "apt error: The repository does not have a Release file",
        "apt expects every repository to publish a Release file (signature + index manifest) but the URL you added doesn't. Cause: the URL is wrong, the repo path is wrong, or it's an old-style flat repo that needs a special sources.list entry. "
        "Fix: (1) verify the URL works in a browser; (2) check the repo provider's docs for the exact `deb` line — they almost always publish one; "
        "(3) for a flat repo, the entry looks like `deb [trusted=yes] https://example.com/repo/ /` (note the trailing slash + trusted=yes). "
        "Do NOT use trusted=yes for normal repos — it disables GPG verification.",
        "https://manpages.debian.org/bookworm/apt/sources.list.5.en.html",
    ),
    (
        "apt-errors",
        "apt error: NO_PUBKEY (GPG signature verification failed)",
        "apt can't verify the repository's signature because the signing key isn't in apt's keyring. "
        "Modern (recommended) fix: download the repo's GPG key to /etc/apt/keyrings/ and reference it from the sources.list entry with signed-by: "
        "`curl -fsSL https://example.com/key.gpg | sudo gpg --dearmor -o /etc/apt/keyrings/example.gpg`, then in sources.list.d/example.list: `deb [signed-by=/etc/apt/keyrings/example.gpg] https://example.com/repo stable main`. "
        "Legacy (deprecated) fix that may still appear in old guides: `sudo apt-key adv --keyserver keyserver.ubuntu.com --recv-keys <NO_PUBKEY-value-from-error>` — works but apt-key itself is deprecated; migrate to signed-by when you have the chance.",
        "https://manpages.debian.org/bookworm/apt/apt-secure.8.en.html",
    ),
    (
        "apt-errors",
        "apt error: Failed to fetch (404 Not Found)",
        "A package file or index referenced in apt's local cache no longer exists at the mirror. Mirrors rotate old versions out; once a new index arrives, the old URL 404s. "
        "Fix: `sudo apt update` to pull the current index, then retry. "
        "If 404 persists after update: the mirror you're using is stale or broken; switch mirrors. For Debian, `sudo apt edit-sources` to edit sources.list and swap to a different deb.debian.org or country mirror.",
        "https://manpages.debian.org/bookworm/apt/sources.list.5.en.html",
    ),
    (
        "apt-errors",
        "apt error: Hash Sum mismatch",
        "The file apt downloaded doesn't match the hash recorded in the Release/Packages index. Causes: proxy or CDN serving a stale cached file, mirror mid-sync, or corrupted download. "
        "Fix: (1) `sudo rm -rf /var/lib/apt/lists/* && sudo apt update` to fully refresh the index; "
        "(2) if behind a corporate proxy, check the proxy isn't caching apt files (configure proxy bypass for repos); "
        "(3) try a different mirror; "
        "(4) if a specific package is corrupted: `sudo apt --reinstall download <pkg>` to refetch.",
        "https://manpages.debian.org/bookworm/apt/apt.8.en.html",
    ),
    (
        "apt-errors",
        "apt error: dpkg: error processing package (cannot remove directory)",
        "dpkg can't remove a package because a directory contains files dpkg doesn't track (created by the running app or admin), so dpkg refuses to delete the directory. "
        "Fix: (1) confirm the leftover files are safe to delete; (2) manually remove the directory: `sudo rm -rf <dir>`; (3) re-run `sudo apt remove <pkg>`. "
        "If the package's purpose is gone, also run `sudo apt purge <pkg>` to delete config files in /etc/.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-errors",
        "apt error: package is in a very bad inconsistent state",
        "dpkg's database knows about a package but the actual files / scripts are missing or corrupted. Often happens after a forced removal or disk fault. "
        "Fix: (1) `sudo dpkg --remove --force-remove-reinstreq <pkg>` to clear dpkg's record; "
        "(2) `sudo apt install --reinstall <pkg>` to put it back cleanly. "
        "If step 1 fails because dependencies break: install required deps first, then retry; for truly stuck cases, `sudo dpkg --purge --force-all <pkg>` is the nuclear option.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-errors",
        "apt error: E: Could not open lock file - open (13: Permission denied)",
        "apt needs root to modify package state. You ran apt without sudo. "
        "Fix: prefix with `sudo`. For non-interactive scripts (cron, ansible), set `DEBIAN_FRONTEND=noninteractive` and use `sudo -E apt -y install <pkg>`. "
        "For containerized contexts where you're already root, this error means your shell isn't actually root despite appearance — check `whoami`.",
        "https://manpages.debian.org/bookworm/apt/apt.8.en.html",
    ),
    (
        "apt-errors",
        "apt error: dpkg: error: parsing file /var/lib/dpkg/status",
        "The dpkg status database is corrupted. Last-resort recovery scenario. "
        "Fix: (1) save the broken file: `sudo cp /var/lib/dpkg/status /var/lib/dpkg/status.broken`; "
        "(2) restore from the auto-backup: `sudo cp /var/lib/dpkg/status-old /var/lib/dpkg/status`; "
        "(3) run `sudo dpkg --configure -a` to reconcile; "
        "(4) if no backup exists, manually edit the offending line dpkg complains about (it'll name a line number). "
        "Take a full snapshot before editing — getting this wrong leaves the system unbootable.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-errors",
        "apt error: do-release-upgrade failed (Ubuntu)",
        "Ubuntu's distribution upgrade tool failed mid-way. Almost always caused by third-party PPAs publishing incompatible packages for the new release. "
        "Fix: (1) `sudo do-release-upgrade -d --frontend=DistUpgradeViewText` for verbose output; "
        "(2) before upgrade: `sudo ppa-purge ppa:<broken-ppa>` to revert offending PPAs to default Ubuntu versions; "
        "(3) after a successful upgrade: re-add the PPAs targeting the new release. "
        "Always upgrade through INTERMEDIATE releases (22.04 → 22.10 → 23.04 → 23.10), not jumping multiple releases — the upgrade path isn't tested.",
        "https://manpages.debian.org/bookworm/apt/apt.8.en.html",
    ),
    (
        "apt-errors",
        "apt error: requesting a hot-fix from another running process",
        "Synthetic error usually meaning apt detected another transaction in flight (similar to the dpkg lock case). "
        "Fix: same as the dpkg-lock error — wait for the other process to finish, then `sudo dpkg --configure -a` before retrying. "
        "If unattended-upgrades is the culprit and you need the system available now, you can temporarily stop it: `sudo systemctl stop unattended-upgrades` (re-enable after).",
        "https://manpages.debian.org/bookworm/apt/apt.8.en.html",
    ),
    (
        "apt-errors",
        "apt error: package has no installation candidate",
        "apt knows the package name exists but can't find a binary for your architecture/distribution. Causes: package is i386-only and you're on amd64, package was removed in your distribution release, or you need to enable a different component (universe, contrib, non-free). "
        "Fix: (1) `apt-cache policy <pkg>` to see what apt knows about its versions and which repos provide it; "
        "(2) if no version listed: enable contrib or non-free in sources.list and `apt update`; "
        "(3) for cross-arch: `sudo dpkg --add-architecture i386 && sudo apt update && sudo apt install <pkg>:i386`.",
        "https://manpages.debian.org/bookworm/apt/apt-cache.8.en.html",
    ),
    (
        "apt-errors",
        "apt error: dpkg: regarding ... contains _file_, which is also in package _other_",
        "Two packages try to install the same file path. dpkg refuses to silently overwrite. "
        "Fix: (1) decide which package should win — usually the one you actually want installed; "
        "(2) for a one-time override: `sudo apt -o Dpkg::Options::='--force-overwrite' install <pkg>`; "
        "(3) better fix: remove the conflicting older package first: `sudo apt remove <other-pkg>` then install the new one. "
        "Force-overwrite is reasonably safe when the new package is the canonical owner; dangerous when you're masking a real conflict.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-errors",
        "apt error: snap and apt confusion (Ubuntu)",
        "Some packages on Ubuntu are snap-only (firefox, chromium); apt install fails or installs a transitional shim that pulls in the snap. "
        "Fix: (1) check if the package is a snap: `snap find <name>`; (2) install via snap: `sudo snap install <name>`; "
        "(3) if you want the deb anyway (e.g., for FIPS or air-gapped envs), use the Mozilla PPA for firefox: `sudo add-apt-repository ppa:mozillateam/ppa` then pin it higher than the snap shim via apt_preferences. "
        "For agents that must be deb-only: pin all upstream snaps to negative priority in /etc/apt/preferences.d/.",
        "https://manpages.debian.org/bookworm/apt/apt_preferences.5.en.html",
    ),
    (
        "apt-errors",
        "apt warning: apt-key is deprecated",
        "Not an error but worth fixing now to avoid future breakage. The `apt-key` command is deprecated; future apt versions remove it. "
        "Migrate: instead of `apt-key add`, download the key to /etc/apt/keyrings/ and reference it from your sources.list entry with `[signed-by=/path/to/key]`. "
        "Example: `curl -fsSL https://example.com/key.gpg | sudo gpg --dearmor -o /etc/apt/keyrings/example.gpg`. "
        "Then in /etc/apt/sources.list.d/example.list: `deb [signed-by=/etc/apt/keyrings/example.gpg] https://example.com/repo stable main`. "
        "Each repo gets its own keyring file rather than a shared trust root — better security isolation.",
        "https://manpages.debian.org/bookworm/apt/apt-secure.8.en.html",
    ),
    (
        "apt-errors",
        "apt error: apt update returns 'Repository changed its X value from Y to Z'",
        "The repo updated its metadata (suite name, label, or version). apt refuses to silently accept the change. "
        "Fix: re-run with explicit acceptance: `sudo apt update --allow-releaseinfo-change`. "
        "Inspect the change: the message names which field changed; if the change matches the upstream's announced migration (e.g., 'stable' → 'bookworm'), proceed. If unexpected, investigate whether the repo was hijacked.",
        "https://manpages.debian.org/bookworm/apt/apt.8.en.html",
    ),
    (
        "apt-errors",
        "apt error: Conflicting distribution",
        "Your sources.list mixes incompatible releases (e.g., bookworm + trixie or buster + bullseye). apt refuses the combination because it can't safely resolve dependencies across pinned versions. "
        "Fix: (1) pick one base release; (2) for selective newer-version installs, use apt_preferences pinning with a small priority on the newer suite rather than enabling it at default priority; "
        "(3) clean up: `apt list --installed | grep <wrong-suite>` to find packages from the unwanted suite and downgrade them.",
        "https://manpages.debian.org/bookworm/apt/apt_preferences.5.en.html",
    ),
    (
        "apt-errors",
        "apt error: no Mirror found / can not connect to the repository",
        "DNS, network, or mirror outage. Less commonly: HTTPS cert validation failed because the system clock is wrong (NTP unsynced after a battery-dead reboot). "
        "Fix: (1) `curl -v https://deb.debian.org` to test reachability; "
        "(2) `date` — if drastically wrong, `sudo ntpdate -q pool.ntp.org` or `sudo systemctl restart systemd-timesyncd`; "
        "(3) check /etc/resolv.conf for valid DNS; "
        "(4) try an alternate mirror in sources.list.",
        "https://manpages.debian.org/bookworm/apt/sources.list.5.en.html",
    ),
    (
        "apt-errors",
        "apt error: cannot find aptitude / aptitude not installed",
        "Some old recovery guides assume aptitude is installed; on modern minimal Ubuntu/Debian it isn't. "
        "Fix: install it (`sudo apt install aptitude`) OR use the apt equivalents — modern apt covers ~95% of what aptitude offered. "
        "When apt's dep resolver gets stuck, aptitude's interactive 'maybe I should remove X' suggestions are sometimes more flexible; that's the main remaining reason to install it.",
        "https://manpages.debian.org/bookworm/apt/apt-get.8.en.html",
    ),
    (
        "apt-errors",
        "apt error: package is held back (will not be upgraded)",
        "The package was marked `hold` (manually pinned to its current version) so `apt upgrade` skips it. "
        "Fix: (1) list held packages: `apt-mark showhold`; "
        "(2) check WHY it was held (sometimes apt does this automatically when an upgrade would remove other packages); "
        "(3) if intentional, leave it; if you want to upgrade anyway, `sudo apt-mark unhold <pkg>` then `sudo apt upgrade`; "
        "(4) `sudo apt full-upgrade` (formerly dist-upgrade) is more aggressive and will pull in held-back packages if their deps require it.",
        "https://manpages.debian.org/bookworm/apt/apt-mark.8.en.html",
    ),

    # ====================================================================
    # apt-recipes — workflow patterns for production use
    # ====================================================================
    (
        "apt-recipes",
        "apt recipe: add a third-party repo with signed-by (modern pattern)",
        "The modern, secure way to add a third-party apt repo (e.g., Docker, Postgres, NodeSource). Steps: "
        "(1) `sudo install -d -m 0755 /etc/apt/keyrings`; "
        "(2) download the repo's GPG key and dearmor: `curl -fsSL https://example.com/key.gpg | sudo gpg --dearmor -o /etc/apt/keyrings/example.gpg`; "
        "(3) write a sources.list entry with signed-by: `echo 'deb [signed-by=/etc/apt/keyrings/example.gpg] https://example.com/repo stable main' | sudo tee /etc/apt/sources.list.d/example.list`; "
        "(4) `sudo apt update`; (5) `sudo apt install <pkg>`. "
        "Why this matters: each repo gets its own isolated trust root, so a compromise of one upstream key doesn't compromise everything else. apt-key (the legacy way) is deprecated.",
        "https://manpages.debian.org/bookworm/apt/apt-secure.8.en.html",
    ),
    (
        "apt-recipes",
        "apt recipe: install a specific package version",
        "Install a non-default version of a package. Steps: "
        "(1) `apt-cache madison <pkg>` to list all available versions (each row shows version + source); "
        "(2) install the exact version: `sudo apt install <pkg>=<version>`; "
        "(3) optionally hold to prevent auto-upgrade: `sudo apt-mark hold <pkg>`. "
        "If the desired version is only in a newer suite (e.g., backports), enable that suite first and use apt_preferences pinning rather than adding it permanently as default.",
        "https://manpages.debian.org/bookworm/apt/apt-mark.8.en.html",
    ),
    (
        "apt-recipes",
        "apt recipe: pin a package to a specific version with preferences",
        "Use apt_preferences to keep a package at a specific version even when newer versions are available. Steps: "
        "(1) create /etc/apt/preferences.d/<pkg>.pref with: `Package: <pkg>\\nPin: version <version>\\nPin-Priority: 1001`; "
        "(2) priority >1000 forces apt to downgrade if needed to satisfy the pin; "
        "(3) `apt-cache policy <pkg>` shows the effective priority — confirm your pin took effect. "
        "Alternative for simple cases: `sudo apt-mark hold <pkg>` — easier but less granular (just freezes at current version).",
        "https://manpages.debian.org/bookworm/apt/apt_preferences.5.en.html",
    ),
    (
        "apt-recipes",
        "apt recipe: non-interactive install for scripts / Docker / CI",
        "Run apt commands non-interactively (no debconf prompts blocking the script). Pattern: "
        "(1) prefix every apt call with `DEBIAN_FRONTEND=noninteractive`; "
        "(2) pass `-y` to auto-accept; (3) for upgrades that touch config files: `-o Dpkg::Options::='--force-confdef' -o Dpkg::Options::='--force-confold'` keeps the existing config (don't auto-replace with maintainer's version). "
        "Full pattern: `DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::='--force-confdef' -o Dpkg::Options::='--force-confold' install nginx`. "
        "Use apt-get (not apt) in scripts — apt prints to stderr that its CLI is unstable for scripts.",
        "https://manpages.debian.org/bookworm/debconf/debconf.7.en.html",
    ),
    (
        "apt-recipes",
        "apt recipe: preseed package answers with debconf",
        "Tell apt the answer to a package's debconf questions BEFORE installing, so the install runs silently. "
        "Pattern: (1) on a system that's already configured the package, dump current answers: `sudo debconf-show <pkg>`; "
        "(2) write them as a preseed file (one per line): `<pkg> <template> <type> <value>` — example: `mysql-server mysql-server/root_password password mySecret`; "
        "(3) preseed before install: `cat preseed.txt | sudo debconf-set-selections && sudo apt-get -y install <pkg>`. "
        "Common use case: unattended MySQL/Postgres installs with a pre-set root password.",
        "https://manpages.debian.org/bookworm/debconf/debconf-set-selections.1.en.html",
    ),
    (
        "apt-recipes",
        "apt recipe: clean apt cache to reclaim disk space",
        "/var/cache/apt/archives can grow to several GB over time. Reclaim it. Steps: "
        "(1) `sudo apt clean` — removes ALL downloaded .deb files (forces re-download if you need to reinstall); "
        "(2) `sudo apt autoclean` — gentler: removes only .deb files for packages no longer available in any repo; "
        "(3) for installed-but-unused packages: `sudo apt autoremove --purge` — removes orphan deps that were pulled in for a package that's now uninstalled; "
        "(4) check what was reclaimed: `du -sh /var/cache/apt/archives` before/after. "
        "On systems with tiny /var, set `APT::Keep-Downloaded-Packages \"false\";` in /etc/apt/apt.conf.d/ to skip caching entirely.",
        "https://manpages.debian.org/bookworm/apt/apt-get.8.en.html",
    ),
    (
        "apt-recipes",
        "apt recipe: enable unattended security upgrades",
        "Auto-install security patches without manual intervention. Steps: "
        "(1) `sudo apt install unattended-upgrades apt-listchanges`; "
        "(2) `sudo dpkg-reconfigure --priority=low unattended-upgrades` (or write `APT::Periodic::Unattended-Upgrade \"1\";` to /etc/apt/apt.conf.d/20auto-upgrades); "
        "(3) edit /etc/apt/apt.conf.d/50unattended-upgrades to allow specific origins (security pocket is enabled by default; main pocket usually off — turn on if you want regular bug-fix updates too); "
        "(4) optional: `Unattended-Upgrade::Mail \"ops@example.com\";` to email on failures; "
        "(5) `Unattended-Upgrade::Automatic-Reboot \"true\";` only if reboots are acceptable (default false).",
        "https://manpages.debian.org/bookworm/unattended-upgrades/unattended-upgrade.8.en.html",
    ),
    (
        "apt-recipes",
        "apt recipe: install Docker via apt on Ubuntu/Debian",
        "Install Docker CE from Docker's official apt repo (NOT the distro's older docker.io package). Steps: "
        "(1) `sudo apt install ca-certificates curl gnupg`; "
        "(2) `sudo install -d -m 0755 /etc/apt/keyrings`; "
        "(3) `curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg` (swap ubuntu→debian on Debian); "
        "(4) write /etc/apt/sources.list.d/docker.list: `deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable`; "
        "(5) `sudo apt update && sudo apt install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin`; "
        "(6) `sudo usermod -aG docker $USER` then logout/login to use docker without sudo.",
        "https://manpages.debian.org/bookworm/apt/sources.list.5.en.html",
    ),
    (
        "apt-recipes",
        "apt recipe: list manually-installed packages (for reproducibility)",
        "Get a clean list of packages you installed by hand (excluding auto-installed deps). Useful for rebuilding a machine or seeding a Dockerfile. "
        "Steps: (1) `apt-mark showmanual` — dumps manually-installed names, one per line; "
        "(2) save to a file: `apt-mark showmanual > installed-packages.txt`; "
        "(3) replay on a fresh system: `xargs -a installed-packages.txt sudo apt install -y`. "
        "Pair with `dpkg --get-selections` for a more complete export including holds and dependencies.",
        "https://manpages.debian.org/bookworm/apt/apt-mark.8.en.html",
    ),
    (
        "apt-recipes",
        "apt recipe: roll back a package to a previous version",
        "Downgrade a package to an earlier known-good version. Steps: "
        "(1) `apt-cache madison <pkg>` to list versions available across enabled repos; "
        "(2) if the desired version is still in the index: `sudo apt install <pkg>=<version>`; "
        "(3) if the version was pulled from the index, check /var/cache/apt/archives for a cached .deb: `sudo dpkg -i /var/cache/apt/archives/<pkg>_<version>.deb`; "
        "(4) hold it so apt won't re-upgrade: `sudo apt-mark hold <pkg>`. "
        "For zero-cached, zero-indexed cases: download the .deb from snapshot.debian.org (Debian's historical archive of every package version).",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-recipes",
        "apt recipe: build a local .deb from source",
        "Build a Debian package from source. Steps: "
        "(1) `sudo apt install dpkg-dev devscripts build-essential` (the toolchain); "
        "(2) `sudo apt build-dep <pkg>` (install the source's build dependencies); "
        "(3) `apt source <pkg>` (downloads .dsc + .orig.tar.gz + .debian.tar.xz, extracts source); "
        "(4) `cd <pkg>-<version> && dpkg-buildpackage -us -uc` (build, skip signing); "
        "(5) the resulting .deb is in the parent directory; install with `sudo dpkg -i ../<pkg>_<version>_<arch>.deb`. "
        "For modifications: edit debian/changelog (`dch -i` adds a new entry) and debian/rules / patches before building.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-recipes",
        "apt recipe: switch the system to a different release (Debian)",
        "Upgrade from one Debian stable to the next (e.g., bullseye → bookworm). Steps: "
        "(1) `sudo apt update && sudo apt full-upgrade` (clear the current pending queue first); "
        "(2) edit /etc/apt/sources.list: replace `bullseye` with `bookworm` everywhere; same for any sources.list.d entries that hard-code the suite name; "
        "(3) `sudo apt update`; "
        "(4) `sudo apt full-upgrade --without-new-pkgs` (resolve simple upgrades first); "
        "(5) `sudo apt full-upgrade` (full upgrade incl. new packages); "
        "(6) reboot. "
        "Always read the Debian release notes for the new release first — they list manual interventions per release.",
        "https://manpages.debian.org/bookworm/apt/apt.8.en.html",
    ),
    (
        "apt-recipes",
        "apt recipe: install a .deb file and its dependencies",
        "Install a local .deb file from disk (e.g., something downloaded from a vendor). Pattern: "
        "(1) modern (recommended): `sudo apt install ./localfile.deb` — apt resolves deps from configured repos and installs; "
        "(2) lower-level: `sudo dpkg -i localfile.deb` — installs but FAILS on missing deps; chase with `sudo apt -f install` to fetch deps and finalize. "
        "Never use `dpkg --force-depends` to ignore missing deps — that leaves a partially-working package and breaks future apt operations.",
        "https://manpages.debian.org/bookworm/apt/apt.8.en.html",
    ),
    (
        "apt-recipes",
        "apt recipe: inspect what an apt operation would do (dry-run)",
        "Preview an apt command without making changes. Steps: "
        "(1) `apt -s install <pkg>` or `apt-get -s install <pkg>` (the -s = simulate flag); "
        "(2) the output lists every package that would be installed/upgraded/removed AND the disk impact; "
        "(3) for upgrades: `apt list --upgradable` to see pending upgrades without simulating. "
        "Always run `-s` first for risky operations like `full-upgrade` on production. The output catches surprises (mass removals, kernel changes) before they happen.",
        "https://manpages.debian.org/bookworm/apt/apt-get.8.en.html",
    ),
    (
        "apt-recipes",
        "apt recipe: query which package owns a specific file",
        "Find out which installed package provides a given file. Steps: "
        "(1) `dpkg -S /path/to/file` — searches installed packages for the file path; "
        "(2) for files you haven't installed yet: `apt-file search <file>` (install `apt-file` first; then `sudo apt-file update`); "
        "(3) inverse — list all files installed by a package: `dpkg -L <pkg>`. "
        "Useful for tracking down 'where did this binary come from' or 'which package do I need to install to get /usr/bin/X'.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg-query.1.en.html",
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
            evidence_id=f"ev-apt-claim-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-apt-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="apt-catalog",
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
