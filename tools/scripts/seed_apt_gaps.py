"""Close the long-tail gaps in apt coverage — 80+ synthesized claims.

Targets the failure modes my honest assessment surfaced:
- Per-flag claims (--no-install-recommends, --reinstall, dpkg --force-* family)
- Less-common subcommands (apt-listbugs, apt-show-versions, apt-rdepends, deborphan)
- More error variations (broken hold, vendor signature, lock variants)
- Deep configuration (APT::Default-Release, Acquire::* hierarchy, hooks, log formats)
- Build toolchain (debhelper, dh_make, lintian, sbuild, debian/* files)
- Mirror operations (reprepro, apt-mirror, apt-cacher-ng)
- Performance + proxy edge cases
- Distribution quirks (Ubuntu snap shim, Devuan, multi-arch)
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
    # ============================================================
    # apt-cli — per-flag + less-common subcommands (~30 claims)
    # ============================================================
    (
        "apt-cli", "apt install --no-install-recommends flag",
        "Tells apt to install only strict (Depends:) dependencies, skipping Recommends:. Saves disk space and reduces attack surface. "
        "Example: `sudo apt install --no-install-recommends nginx` skips packages like nginx-doc that are nice-to-have but not required. "
        "Make it the default systemwide by adding `APT::Install-Recommends \"false\";` to /etc/apt/apt.conf.d/99no-recommends. "
        "Trade-off: some packages assume their Recommends are present; you may need to install missing recommends manually if a feature is missing.",
        "https://manpages.debian.org/bookworm/apt/apt-get.8.en.html",
    ),
    (
        "apt-cli", "apt install --reinstall flag",
        "Re-install a package that's already installed. Useful when files have been deleted, corrupted, or you want to restore default config. "
        "Example: `sudo apt install --reinstall coreutils` re-extracts every file the package owns. "
        "Config files that have been modified (conffiles) are NOT overwritten by default — dpkg detects the change and asks. To force-overwrite configs: add `-o Dpkg::Options::='--force-confnew'`. "
        "For multiple packages: `sudo apt install --reinstall $(dpkg -l | grep '^ii' | awk '{print $2}')` re-installs everything.",
        "https://manpages.debian.org/bookworm/apt/apt-get.8.en.html",
    ),
    (
        "apt-cli", "apt install --simulate / -s / --dry-run flag",
        "Show what apt WOULD do without making changes. Critical for production: previews mass removals, kernel upgrades, surprising dep chains. "
        "Example: `apt -s install postgresql` lists every package that would be installed/removed/upgraded plus disk impact. "
        "Same as `-s` and `--dry-run` (all aliases). Combines with any apt command — `apt -s remove`, `apt -s full-upgrade`. "
        "Always run before destructive operations on shared/prod machines.",
        "https://manpages.debian.org/bookworm/apt/apt-get.8.en.html",
    ),
    (
        "apt-cli", "apt install --download-only / -d flag",
        "Download .deb files into /var/cache/apt/archives without installing them. Useful for staging upgrades, building offline package sets, or pre-downloading large updates before a maintenance window. "
        "Example: `sudo apt -d install large-package` downloads the .deb but doesn't run dpkg. "
        "Later: install the cached .debs with `sudo apt install large-package` (apt sees them in cache and skips re-download).",
        "https://manpages.debian.org/bookworm/apt/apt-get.8.en.html",
    ),
    (
        "apt-cli", "apt install --fix-missing flag",
        "Tells apt: 'install what you can; skip packages that fail to download'. Use when transient mirror issues block specific packages but you want to continue with the rest. "
        "Example: `sudo apt install --fix-missing -y full-upgrade` proceeds even if some packages return 404. "
        "After the run finishes: `sudo apt install -f` to retry the skipped packages once the mirror recovers.",
        "https://manpages.debian.org/bookworm/apt/apt-get.8.en.html",
    ),
    (
        "apt-cli", "apt install --fix-broken / -f flag",
        "Tells apt's resolver to fix broken dependencies in the current install state. Common after a failed manual `dpkg -i localfile.deb` or a botched force-overwrite. "
        "Example: `sudo apt --fix-broken install` (no package name) — apt scans for broken deps and proposes a resolution (install missing deps, possibly remove conflicting packages). "
        "Run `-s` first to preview. If the resolution involves removing important packages, investigate the underlying conflict before accepting.",
        "https://manpages.debian.org/bookworm/apt/apt-get.8.en.html",
    ),
    (
        "apt-cli", "apt install --only-upgrade flag",
        "Install a package ONLY if it's already installed (upgrades it); if not installed, skip. Inverse of normal install behavior. "
        "Use case: in scripts where you want to upgrade a package set without accidentally installing unwanted ones. "
        "Example: `sudo apt install --only-upgrade openssl libssl3` upgrades openssl+libssl3 if present, no-op otherwise.",
        "https://manpages.debian.org/bookworm/apt/apt-get.8.en.html",
    ),
    (
        "apt-cli", "apt list --upgradable flag",
        "Show packages that have a newer version available in any enabled repository — without performing the upgrade. "
        "Example: `apt list --upgradable` lists pending upgrades; `apt list --upgradable | grep -i security` filters security updates. "
        "Useful in monitoring scripts: `apt list --upgradable 2>/dev/null | wc -l` returns the pending-upgrade count.",
        "https://manpages.debian.org/bookworm/apt/apt.8.en.html",
    ),
    (
        "apt-cli", "apt list --installed flag",
        "List currently installed packages. Faster than `dpkg -l` for pure-name output. "
        "Example: `apt list --installed | wc -l` counts installed packages. "
        "Variants: `--manual-installed` shows only packages installed by user (not auto-pulled deps); `--all-versions` shows every version known to apt for each package.",
        "https://manpages.debian.org/bookworm/apt/apt.8.en.html",
    ),
    (
        "apt-cli", "dpkg --force-confnew flag",
        "When a package's config file (conffile) has been changed both by the user and the maintainer, dpkg prompts. `--force-confnew` answers: always replace with the maintainer's NEW version. "
        "Example: `sudo apt -o Dpkg::Options::='--force-confnew' install nginx`. "
        "Use case: bulk reconfiguration after a major upgrade where you want maintainer defaults. DANGEROUS for /etc files you've customized — back up first.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-cli", "dpkg --force-confold flag",
        "Inverse of confnew: always keep the user's OLD config file, ignore the maintainer's new version. Safe default for non-interactive scripts that should not disturb existing configuration. "
        "Example: `sudo apt-get -o Dpkg::Options::='--force-confdef' -o Dpkg::Options::='--force-confold' -y upgrade` — combined with confdef this is the canonical 'unattended upgrade preserving local config' pattern.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-cli", "dpkg --force-confdef flag",
        "Defer to dpkg's default if it can decide automatically; only prompt when truly ambiguous. Almost always paired with `--force-confold` for unattended runs. "
        "Combined: `apt-get -o Dpkg::Options::='--force-confdef' -o Dpkg::Options::='--force-confold' -y upgrade` — apt makes the decision for cases dpkg can resolve, falls back to keeping the old user config when not.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-cli", "dpkg --force-overwrite flag",
        "Allow dpkg to overwrite a file already provided by a different installed package (the classic 'trying to overwrite, also in package X' error). "
        "Use SPARINGLY — masks real conflicts that usually indicate something is wrong (old package version, vendored binary, manual file). "
        "Better fix: identify the conflicting package with `dpkg -S /path/to/file` and decide which should win.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-cli", "dpkg-query -W (--show) flag",
        "Show installed package metadata in a parseable format. The agent-facing tool for 'tell me everything about installed packages'. "
        "Examples: `dpkg-query -W -f='${Package} ${Version}\\n'` lists name+version; "
        "`dpkg-query -W -f='${Package}\\t${Installed-Size}\\n' | sort -k2 -n -r | head` finds the biggest installed packages.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg-query.1.en.html",
    ),
    (
        "apt-cli", "dpkg-query -L (--listfiles) flag",
        "List every file installed by a specific package. Useful for 'what did this package put on disk?'. "
        "Example: `dpkg -L nginx` shows every file nginx owns. Pipe to grep for specific paths: `dpkg -L nginx | grep bin/`. "
        "Inverse query: `dpkg -S /path/to/file` says which package owns a file.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg-query.1.en.html",
    ),
    (
        "apt-cli", "dpkg-query -s (--status) flag",
        "Show the full dpkg status entry for a package: version, dependencies, conffiles, maintainer, description. "
        "Example: `dpkg -s nginx` outputs the package's status block from /var/lib/dpkg/status. "
        "Use case: agent debugging — see what apt's actual installed state thinks the package looks like, including conffile hashes for change detection.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg-query.1.en.html",
    ),
    (
        "apt-cli", "dpkg --get-selections and --set-selections",
        "Export the full set of installed-package selections (and import on another machine). Pattern: "
        "(1) on source machine: `dpkg --get-selections > selections.txt`; "
        "(2) on target: `sudo apt update && sudo dpkg --set-selections < selections.txt && sudo apt-get -y dselect-upgrade`. "
        "Result: target installs (or removes) packages to exactly match the source selections. Good for cloning machine state.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-cli", "apt-cache policy command",
        "Show how apt would prioritize package versions across all enabled repositories — the truth source for 'why is apt installing version X?'. "
        "Example: `apt-cache policy nginx` shows: installed version, candidate version, version table with each repo + priority. "
        "Pinning surfaces here: the priority column lets you confirm an apt_preferences pin took effect.",
        "https://manpages.debian.org/bookworm/apt/apt-cache.8.en.html",
    ),
    (
        "apt-cli", "apt-cache madison command",
        "Like policy but more compact: lists every version of a package across all repos with their source. "
        "Example: `apt-cache madison openssl` → table of (version, suite, repo). Useful when planning a downgrade or finding which suite has a specific version.",
        "https://manpages.debian.org/bookworm/apt/apt-cache.8.en.html",
    ),
    (
        "apt-cli", "apt-cache depends and rdepends",
        "Show package dependency relationships. `apt-cache depends <pkg>` lists what the package depends ON; `apt-cache rdepends <pkg>` shows reverse: what depends on this package. "
        "Use case: 'what would break if I removed X?' → `apt-cache rdepends --installed --recurse X`. "
        "Tooling alternative: `aptitude why X` gives a human-readable dependency chain.",
        "https://manpages.debian.org/bookworm/apt/apt-cache.8.en.html",
    ),
    (
        "apt-cli", "apt show command",
        "Display detailed information about a package: description, dependencies, size, source repo. The agent-friendly version of `apt-cache show`. "
        "Example: `apt show postgresql-15` outputs everything apt knows about that package. "
        "Add `-a` (`apt show -a <pkg>`) to show every available version, not just the installation candidate.",
        "https://manpages.debian.org/bookworm/apt/apt.8.en.html",
    ),
    (
        "apt-cli", "apt search command",
        "Search package names AND descriptions. Different from dpkg -l (only installed) — apt search scans the full package index. "
        "Example: `apt search ssh client` finds packages whose name or description mentions both 'ssh' and 'client'. "
        "Regex: `apt search '^nginx-' --names-only` constrains to package names beginning with 'nginx-'.",
        "https://manpages.debian.org/bookworm/apt/apt.8.en.html",
    ),
    (
        "apt-cli", "apt edit-sources command",
        "Open /etc/apt/sources.list in $EDITOR with syntax validation on save. Safer than direct text editing because apt re-parses before committing. "
        "Example: `sudo apt edit-sources` opens the main file; `sudo apt edit-sources example.list` opens /etc/apt/sources.list.d/example.list. "
        "If your edit produces invalid syntax, apt warns and offers to revert.",
        "https://manpages.debian.org/bookworm/apt/apt.8.en.html",
    ),
    (
        "apt-cli", "apt full-upgrade vs upgrade",
        "Two different upgrade modes. `apt upgrade` upgrades packages but won't install NEW packages or remove existing ones. `apt full-upgrade` (formerly dist-upgrade) does install/remove packages if needed to satisfy upgrades. "
        "Use upgrade for routine patching; use full-upgrade for major version transitions and when held-back packages need new deps. "
        "Always run `-s` first on full-upgrade — it can remove packages.",
        "https://manpages.debian.org/bookworm/apt/apt-get.8.en.html",
    ),
    (
        "apt-cli", "apt autoremove command",
        "Remove packages that were auto-installed as dependencies but are no longer needed by any installed package. "
        "Example: `sudo apt autoremove` cleans up orphan deps; `sudo apt autoremove --purge` also deletes their config files in /etc/. "
        "Run periodically after major package removal to recover disk space.",
        "https://manpages.debian.org/bookworm/apt/apt-get.8.en.html",
    ),
    (
        "apt-cli", "apt-mark auto vs manual vs hold",
        "Three states a package can be in. `manual` = explicitly installed by user (won't be auto-removed). `auto` = installed as a dependency (subject to autoremove if no longer needed). `hold` = locked, won't be upgraded by `apt upgrade`. "
        "Commands: `sudo apt-mark manual <pkg>` to promote; `sudo apt-mark auto <pkg>` to demote; `sudo apt-mark hold <pkg>` to freeze. Show current state: `apt-mark showmanual`, `apt-mark showauto`, `apt-mark showhold`.",
        "https://manpages.debian.org/bookworm/apt/apt-mark.8.en.html",
    ),
    (
        "apt-cli", "apt-file command",
        "Search for files in packages that AREN'T installed yet. Counterpart to `dpkg -S` (which only searches installed packages). "
        "Setup: `sudo apt install apt-file && sudo apt-file update`. "
        "Use: `apt-file search /usr/bin/missing-binary` returns the package(s) providing that file across all enabled repos. Useful for resolving 'command not found' errors.",
        "https://manpages.debian.org/bookworm/apt/apt-cache.8.en.html",
    ),
    (
        "apt-cli", "deborphan command",
        "Find installed libraries with no other package depending on them — orphans, leftovers from removed software. "
        "Setup: `sudo apt install deborphan`. Run: `deborphan` lists candidates; `sudo apt remove --purge $(deborphan)` removes them in bulk. "
        "Safer alternative for most cases: `sudo apt autoremove` handles the same scenario via apt's own tracking.",
        "https://manpages.debian.org/bookworm/apt/apt-get.8.en.html",
    ),
    (
        "apt-cli", "apt-listbugs command",
        "Show open Debian bugs for packages about to be installed/upgraded. Stops the install for confirmation if there are serious bugs. "
        "Setup: `sudo apt install apt-listbugs`. Once installed, runs automatically before every apt operation. "
        "Configure severity threshold in /etc/apt/listbugs.conf. Worth it for production Debian servers — prevents installing packages with known data-loss bugs.",
        "https://manpages.debian.org/bookworm/apt/apt-get.8.en.html",
    ),
    (
        "apt-cli", "apt-show-versions command",
        "Show installed-version vs latest-available-in-each-repo for every installed package (or one named). "
        "Useful for auditing: `apt-show-versions | grep 'upgradeable'` shows pending updates with their source repo. "
        "Setup: `sudo apt install apt-show-versions`. Output is more concise than `apt list --upgradable` for scripting.",
        "https://manpages.debian.org/bookworm/apt/apt.8.en.html",
    ),

    # ============================================================
    # apt-errors — 25 more error scenarios
    # ============================================================
    (
        "apt-errors", "apt error: The following packages have unmet dependencies (held)",
        "Variant of unmet-deps where a held package is blocking the upgrade chain. "
        "Fix: (1) `apt-mark showhold` to see what's held; (2) decide whether to unhold (`sudo apt-mark unhold <pkg>`) or work around the hold by upgrading other things first; (3) if the hold was set automatically by apt, investigate WHY — usually it's because the new version would remove other packages.",
        "https://manpages.debian.org/bookworm/apt/apt-mark.8.en.html",
    ),
    (
        "apt-errors", "apt error: Unable to correct problems, you have held broken packages",
        "apt's resolver gave up. Held packages are pinning versions that conflict with each other. "
        "Fix: (1) `sudo apt-mark showhold`; (2) temporarily unhold the conflicting package: `sudo apt-mark unhold <pkg>`; (3) retry the install — let apt resolve; (4) re-hold if needed: `sudo apt-mark hold <pkg>`. "
        "If you need both versions simultaneously, that's not what apt is for — use a container or chroot.",
        "https://manpages.debian.org/bookworm/apt/apt-mark.8.en.html",
    ),
    (
        "apt-errors", "apt error: GPG error: BADSIG (signature is bad)",
        "apt downloaded the Release file but its GPG signature doesn't verify. Causes: corruption mid-download, MITM attack (rare with HTTPS), or the maintainer rotated keys without you updating. "
        "Fix: (1) `sudo rm /var/lib/apt/lists/partial/*` then `sudo apt update` — refresh the cache; (2) verify the public key is still current at the upstream's docs; (3) if BADSIG persists, the repo has either been compromised or moved — do NOT --allow-insecure.",
        "https://manpages.debian.org/bookworm/apt/apt-secure.8.en.html",
    ),
    (
        "apt-errors", "apt error: Method http has died unexpectedly",
        "apt's http transport process crashed. Usually transient — apt restarts it on the next operation. "
        "If persistent: (1) check if /usr/lib/apt/methods/http has been corrupted (`dpkg -V apt`); (2) `sudo apt install --reinstall apt`; (3) check for OOM kills: `dmesg | grep -i killed`.",
        "https://manpages.debian.org/bookworm/apt/apt.8.en.html",
    ),
    (
        "apt-errors", "apt error: Some index files failed to download",
        "Subset of repositories failed to update; apt continues with the old index for those. "
        "Fix: (1) the message names which URL failed — open it in a browser to see if the path moved; (2) if the repo is intentionally offline (you don't need it anymore), remove the sources.list.d entry; (3) for transient mirror issues, just retry `sudo apt update` later.",
        "https://manpages.debian.org/bookworm/apt/sources.list.5.en.html",
    ),
    (
        "apt-errors", "apt error: dpkg: dependency problems prevent configuration of",
        "Mid-transaction failure: dpkg installed package A but its postinst depends on package B that wasn't installed yet. "
        "Fix: (1) `sudo apt -f install` — apt's resolver pulls B and finishes configuring A; (2) if that fails, the order is wrong in the install chain: install B explicitly first, then retry A.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-errors", "apt error: dpkg: warning: 'X' missing",
        "dpkg expected a file in the package but it wasn't there. Usually means the .deb is corrupted or was assembled wrong. "
        "Fix: (1) re-download: `sudo apt --reinstall download <pkg>`; (2) if the file was on disk and someone deleted it, `sudo apt install --reinstall <pkg>` restores; (3) if from a third-party PPA, report upstream — the package is broken.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-errors", "apt error: Could not configure 'X'",
        "Package install completed (files extracted) but the configure step failed. Most common: the postinst script tried to start a service that's already running differently, or it's a debconf prompt awaiting input on a non-interactive run. "
        "Fix: (1) `sudo dpkg --configure -a` to retry; (2) if interactive prompt needed, run with proper TTY: `sudo apt install --reinstall <pkg>`; (3) check /var/log/dpkg.log and the package's postinst script for the actual error.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-errors", "apt error: Internal Error, ordering was unable to handle",
        "apt's dependency resolver got into a state it couldn't sort. Rare but real, usually after big dist-upgrades. "
        "Fix: (1) clean up: `sudo apt clean && sudo apt update`; (2) retry with `--fix-broken` and `-o APT::Get::FixMissing=true`; (3) for stubborn cases, try `aptitude` interactively — its resolver is more flexible: `sudo aptitude install <pkg>`.",
        "https://manpages.debian.org/bookworm/apt/apt-get.8.en.html",
    ),
    (
        "apt-errors", "apt error: not enough free disk space",
        "Install can't complete because /var (or wherever apt downloads to) is full. "
        "Fix: (1) `sudo apt clean` to drop downloaded .debs; (2) `sudo apt autoremove --purge` to drop orphan packages; (3) `du -sh /var/cache/apt/archives /var/lib/apt/lists` to confirm what's there; (4) for one-off relief, move /var/cache/apt/archives to a different disk and symlink; (5) on Docker, increase the container's disk allocation.",
        "https://manpages.debian.org/bookworm/apt/apt-get.8.en.html",
    ),
    (
        "apt-errors", "apt error: Cache file is corrupt or unreadable",
        "/var/cache/apt/pkgcache.bin or srcpkgcache.bin is broken. apt regenerates them on next update. "
        "Fix: `sudo rm /var/cache/apt/*.bin && sudo apt update` rebuilds the cache from sources. Takes 30-60s; safe operation.",
        "https://manpages.debian.org/bookworm/apt/apt.8.en.html",
    ),
    (
        "apt-errors", "apt error: dpkg-divert error",
        "dpkg-divert is the mechanism to re-route a file path (so two packages can both provide the same path without conflict). Errors usually mean a stale diversion record. "
        "Fix: list diversions: `dpkg-divert --list`; remove a stale one: `sudo dpkg-divert --remove /path/to/file`; for full cleanup: `dpkg-divert --truename /path/to/file` shows what the path was diverted to.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg-divert.1.en.html",
    ),
    (
        "apt-errors", "apt error: update-alternatives in error state",
        "The alternatives system (which symlinks /usr/bin/python → /usr/bin/python3, etc.) is confused. "
        "Fix: (1) `sudo update-alternatives --display <name>` shows the current state; (2) `sudo update-alternatives --config <name>` opens an interactive picker; (3) `sudo update-alternatives --auto <name>` reverts to automatic priority-based selection; (4) for stale entries: `sudo update-alternatives --remove <name> /path/to/old/binary`.",
        "https://manpages.debian.org/bookworm/dpkg/update-alternatives.1.en.html",
    ),
    (
        "apt-errors", "apt error: The Public Key for the following IDs could not be verified",
        "Same root cause as NO_PUBKEY but the error message differs. apt can't find the signing key for a repo. "
        "Fix: same as NO_PUBKEY — download the upstream's GPG key to /etc/apt/keyrings/<repo>.gpg, reference it from sources.list with `[signed-by=...]`. Don't use apt-key (deprecated).",
        "https://manpages.debian.org/bookworm/apt/apt-secure.8.en.html",
    ),
    (
        "apt-errors", "apt warning: --force-yes is deprecated",
        "Old `--force-yes` is renamed; modern equivalent depends on intent. "
        "Replacements: `--allow-downgrades` if you're letting apt downgrade packages; `--allow-remove-essential` if removing essential packages; `--allow-change-held-packages` for held; `--allow-unauthenticated` for unsigned (DANGEROUS — only for trusted local mirrors). Pick the minimal one for what you actually need.",
        "https://manpages.debian.org/bookworm/apt/apt-get.8.en.html",
    ),
    (
        "apt-errors", "apt error: Package indexes are corrupt",
        "Local apt cache files don't parse. Usually after a disk corruption or interrupted apt operation. "
        "Fix: (1) `sudo rm -rf /var/lib/apt/lists/* && sudo apt update` regenerates; (2) if that fails, `sudo rm /var/cache/apt/*.bin` to drop binary caches; (3) for truly corrupt state, `sudo apt install --reinstall apt` reinstalls apt itself.",
        "https://manpages.debian.org/bookworm/apt/apt.8.en.html",
    ),
    (
        "apt-errors", "apt error: file already exists in filesystem",
        "Same family as 'contains _file_ which is also in package' but without naming the other package. Usually a file installed manually that now conflicts with a package install. "
        "Fix: (1) `dpkg -S /path/to/file` to see if anything claims it; (2) if it's a manual leftover, delete it; (3) if you want the existing file kept, `-o Dpkg::Options::='--force-confold'` for config files; (4) only use `--force-overwrite` as last resort.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-errors", "apt error: Sources lock could not be acquired",
        "/var/cache/apt/srcpkgcache.lock is held by another apt process (variant of the dpkg lock error but for source operations like `apt-get source`). "
        "Fix: same as other lock errors — wait for the holding process or `lsof` to identify it. After clearing: `sudo rm /var/cache/apt/srcpkgcache.lock` is safe IF you've confirmed no apt is running.",
        "https://manpages.debian.org/bookworm/apt/apt-get.8.en.html",
    ),
    (
        "apt-errors", "apt error: Insecure repositories are deprecated",
        "Repos without GPG signatures generate this warning (and may refuse to update in newer apt). "
        "Fix: (1) ideal — get the upstream to sign their repo, or switch to a signed repo; (2) emergency override: `--allow-insecure-repositories` flag or `Acquire::AllowInsecureRepositories \"true\";` in apt.conf.d (NEVER do this for public repos); (3) for local apt-mirror or reprepro mirrors of trusted upstream, sign your local mirror with `dpkg-sig` or `reprepro`.",
        "https://manpages.debian.org/bookworm/apt/apt-secure.8.en.html",
    ),
    (
        "apt-errors", "apt error: failed to verify signature of release file",
        "Distinct from BADSIG: the Release file is signed but apt's keyring doesn't have a matching public key. Often happens when a repo rotates its signing key without you re-importing. "
        "Fix: (1) get the new public key from the upstream's docs; (2) install it into /etc/apt/keyrings/ following the signed-by pattern; (3) `sudo apt update`.",
        "https://manpages.debian.org/bookworm/apt/apt-secure.8.en.html",
    ),
    (
        "apt-errors", "apt error: Conflicting values set for option",
        "apt.conf.d has two .conf files setting the same option to different values. "
        "Fix: (1) `apt-config dump | grep <option>` shows what's effectively set; (2) `ls /etc/apt/apt.conf.d/` and grep for the option to find the conflicting files; (3) consolidate: pick one file as authoritative, remove the duplicate.",
        "https://manpages.debian.org/bookworm/apt/apt.conf.5.en.html",
    ),
    (
        "apt-errors", "apt warning: Skipping acquire of configured file",
        "An entry in sources.list is malformed or apt can't reach it; apt skips it but logs a warning. "
        "Fix: (1) the message names the file/line; check the URL is reachable and the format is correct; (2) for typos, `sudo apt edit-sources` validates on save; (3) for genuinely unreachable repos, comment them out until they're back.",
        "https://manpages.debian.org/bookworm/apt/sources.list.5.en.html",
    ),
    (
        "apt-errors", "apt error: dpkg: too-old-version error",
        "Tried to install a .deb whose declared package version is older than what's installed. dpkg refuses to downgrade by default. "
        "Fix: (1) explicit downgrade: `sudo apt install <pkg>=<old-version>` with `--allow-downgrades`; (2) for .deb files: `sudo dpkg --force-downgrade -i old-version.deb`. Use sparingly — downgrades often leave dangling deps.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-errors", "apt error: E: 'X' is not a deb package",
        "You ran `dpkg -i` on a file that isn't a .deb (wrong extension, source tarball, corrupted). "
        "Fix: (1) `file <name>.deb` to confirm magic bytes; (2) `dpkg-deb -I <name>.deb` to inspect; (3) if download was incomplete, re-download.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg-deb.1.en.html",
    ),

    # ============================================================
    # apt-recipes — 15 more workflow patterns
    # ============================================================
    (
        "apt-recipes", "apt recipe: set up a private apt mirror with reprepro",
        "Run your own internal apt repo. Steps: "
        "(1) `sudo apt install reprepro`; "
        "(2) create dir structure: `mkdir -p /srv/apt/{conf,db,dists,pool,incoming}`; "
        "(3) write /srv/apt/conf/distributions with Codename, Components (main contrib non-free), Architectures, SignWith: <gpg-id>; "
        "(4) `gpg --gen-key` to create signing key; export public key to clients; "
        "(5) add packages: `reprepro -b /srv/apt includedeb bookworm /path/to/pkg.deb`; "
        "(6) serve via nginx: `location /apt { autoindex on; root /srv; }`; "
        "(7) clients add: `deb [signed-by=/etc/apt/keyrings/yourkey.gpg] https://apt.internal/apt bookworm main`.",
        "https://manpages.debian.org/bookworm/apt/sources.list.5.en.html",
    ),
    (
        "apt-recipes", "apt recipe: configure apt behind an HTTP proxy",
        "When the machine is behind a corporate proxy. Steps: "
        "(1) write /etc/apt/apt.conf.d/99proxy: `Acquire::http::Proxy \"http://proxy.example.com:8080\";` (and `Acquire::https::Proxy` for HTTPS); "
        "(2) for proxy auth: `Acquire::http::Proxy \"http://user:pass@proxy.example.com:8080\";` — better, put creds in apt_auth.conf instead of apt.conf; "
        "(3) per-host bypass: `Acquire::http::Proxy::deb.debian.org \"DIRECT\";` skips proxy for that mirror; "
        "(4) verify: `sudo apt update` should succeed; `apt-config dump | grep -i proxy` confirms the setting is loaded.",
        "https://manpages.debian.org/bookworm/apt/apt.conf.5.en.html",
    ),
    (
        "apt-recipes", "apt recipe: set up apt-cacher-ng for shared local cache",
        "Cache .debs across a LAN so multiple machines share a single download. Steps: "
        "(1) on cache server: `sudo apt install apt-cacher-ng`; service runs on port 3142 by default; "
        "(2) on client machines: write /etc/apt/apt.conf.d/00proxy: `Acquire::http::Proxy \"http://cacheserver:3142\";`; "
        "(3) admin UI: http://cacheserver:3142 shows stats + cache contents; "
        "(4) saves bandwidth + speeds up large fleets; first download fetches from upstream, subsequent fetches return from cache. "
        "Doesn't cache HTTPS by default; configure `PassThroughPattern` for HTTPS repos.",
        "https://manpages.debian.org/bookworm/apt/apt.conf.5.en.html",
    ),
    (
        "apt-recipes", "apt recipe: install an i386 package on amd64 (multi-arch)",
        "Enable a foreign architecture and install packages from it. Steps: "
        "(1) `sudo dpkg --add-architecture i386`; "
        "(2) `sudo apt update` — apt fetches i386 indexes from existing repos; "
        "(3) `sudo apt install <pkg>:i386` — the :arch suffix is required to disambiguate; "
        "(4) verify: `dpkg --print-foreign-architectures` lists added archs. "
        "Common use cases: legacy 32-bit software (Steam, Wine, vendor binaries) on 64-bit hosts.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-recipes", "apt recipe: audit installed packages for CVEs with debsecan",
        "Cross-reference installed packages against Debian's security tracker. Steps: "
        "(1) `sudo apt install debsecan`; "
        "(2) `debsecan --suite bookworm` lists currently-installed packages with open CVEs in the configured suite; "
        "(3) for actionable output: `debsecan --suite bookworm --only-fixed` filters to CVEs with a fixed version available; "
        "(4) email reports: `debsecan --suite bookworm --format report | mail -s 'CVE report' security@example.com`; "
        "(5) cron-able for daily monitoring.",
        "https://manpages.debian.org/bookworm/apt/apt.8.en.html",
    ),
    (
        "apt-recipes", "apt recipe: replicate a system's installed packages to another machine",
        "Clone installed packages from source to target. Steps: "
        "(1) on source: `dpkg --get-selections | grep -v deinstall > selections.txt && cp /etc/apt/sources.list selections-sources.list`; "
        "(2) transfer both files to target; "
        "(3) on target: `sudo cp selections-sources.list /etc/apt/sources.list && sudo apt update`; "
        "(4) `sudo dpkg --set-selections < selections.txt`; "
        "(5) `sudo apt-get -y dselect-upgrade` — apt installs missing packages to match the selections. "
        "Variants: for full replication including config files, also rsync /etc and /var/lib/dpkg.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-recipes", "apt recipe: configure apt-listchanges to email upgrade summaries",
        "Get an email with package changelogs before every upgrade. Steps: "
        "(1) `sudo apt install apt-listchanges`; "
        "(2) `sudo dpkg-reconfigure apt-listchanges` walks through setup (frontend = mail; email = admin@example.com; show = news, changelogs); "
        "(3) config lives at /etc/apt/listchanges.conf; "
        "(4) for noninteractive: set `confirm=0` (the upgrade proceeds after email instead of prompting). "
        "Combined with unattended-upgrades, this gives you an audit trail of what changed and why.",
        "https://manpages.debian.org/bookworm/apt-listchanges/apt-listchanges.1.en.html",
    ),
    (
        "apt-recipes", "apt recipe: install a package from Debian backports",
        "Backports give you newer software on stable Debian without major-version upgrade. Steps: "
        "(1) enable: `echo 'deb http://deb.debian.org/debian bookworm-backports main' | sudo tee /etc/apt/sources.list.d/backports.list`; "
        "(2) `sudo apt update`; "
        "(3) backports aren't installed by default — pinning keeps them at low priority. To pull a specific package: `sudo apt -t bookworm-backports install <pkg>`; "
        "(4) to pin a specific package to always come from backports: write /etc/apt/preferences.d/<pkg>.pref with `Package: <pkg>` + `Pin: release n=bookworm-backports` + `Pin-Priority: 990`.",
        "https://manpages.debian.org/bookworm/apt/apt_preferences.5.en.html",
    ),
    (
        "apt-recipes", "apt recipe: convert an RPM package to .deb with alien",
        "Last-resort conversion for vendor-only-RPM software. Steps: "
        "(1) `sudo apt install alien`; "
        "(2) `sudo alien --to-deb package-1.0-1.rpm` produces package_1.0-2_amd64.deb; "
        "(3) `sudo dpkg -i package_1.0-2_amd64.deb && sudo apt -f install` to satisfy deps; "
        "(4) NOT recommended for production — conversion can miss arch-specific scripts. Prefer a vendor-provided .deb or a manual installation tracked outside apt. "
        "Use only when there's no alternative, and audit the resulting .deb with `dpkg -c` before installing.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-recipes", "apt recipe: pre-download all upgrades during a maintenance window",
        "Stage upgrades for a later install (e.g., download today, apply during tonight's maintenance window). Steps: "
        "(1) `sudo apt update`; "
        "(2) `sudo apt -d -y upgrade` (download-only); "
        "(3) at maintenance time, the .debs are in /var/cache/apt/archives — `sudo apt -y upgrade` runs without network access; "
        "(4) verify caching: `sudo apt -s upgrade` (simulate) shows 0 downloads. "
        "For dist-upgrades, same pattern with `apt full-upgrade`.",
        "https://manpages.debian.org/bookworm/apt/apt-get.8.en.html",
    ),
    (
        "apt-recipes", "apt recipe: troubleshoot slow apt operations",
        "Diagnose why apt is taking forever. Steps: "
        "(1) `time sudo apt update` to measure; "
        "(2) check mirror health: `sudo apt -o Debug::pkgAcquire::Worker=1 update` shows per-mirror timing; "
        "(3) switch to a faster mirror: edit /etc/apt/sources.list to use a country mirror or netselect-apt's recommendation; "
        "(4) enable parallel downloads: add `Acquire::Queue-Mode \"host\";` and `Acquire::http::Pipeline-Depth 5;` to apt.conf.d; "
        "(5) trim sources: remove unused PPAs/repos in /etc/apt/sources.list.d/ — each one is a separate index download.",
        "https://manpages.debian.org/bookworm/apt/apt.conf.5.en.html",
    ),
    (
        "apt-recipes", "apt recipe: hold the kernel during upgrades (server stability)",
        "Prevent kernel upgrades during regular apt upgrades on long-running servers. Steps: "
        "(1) identify current kernel: `uname -r` (e.g., 6.1.0-13-amd64); "
        "(2) `sudo apt-mark hold linux-image-6.1.0-13-amd64 linux-image-amd64 linux-headers-amd64` — pins the metapackage too; "
        "(3) verify: `apt-mark showhold`; "
        "(4) when you DO want a kernel upgrade (planned reboot window): `sudo apt-mark unhold linux-image-amd64 linux-headers-amd64` then `sudo apt upgrade && sudo reboot`. "
        "Trade-off: you miss security kernel patches until the planned window — make sure 'planned' actually happens monthly.",
        "https://manpages.debian.org/bookworm/apt/apt-mark.8.en.html",
    ),
    (
        "apt-recipes", "apt recipe: extract a .deb file's contents without installing",
        "Inspect or extract a .deb without modifying the system. Steps: "
        "(1) inspect metadata: `dpkg-deb -I <pkg>.deb` (control info); "
        "(2) list files: `dpkg-deb -c <pkg>.deb`; "
        "(3) extract to a directory: `dpkg-deb -x <pkg>.deb /tmp/extract` puts the package's filesystem layout under /tmp/extract; "
        "(4) extract control scripts (preinst, postinst, etc.): `dpkg-deb -e <pkg>.deb /tmp/extract/DEBIAN`. "
        "Use case: rebuilding .debs (modify files, re-pack with `dpkg-deb -b`), auditing what a third-party .deb contains.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg-deb.1.en.html",
    ),
    (
        "apt-recipes", "apt recipe: revert a package configuration to defaults",
        "Restore a package's config files to maintainer-supplied defaults. Steps: "
        "(1) backup current config: `sudo cp -r /etc/<pkg> /etc/<pkg>.bak`; "
        "(2) `sudo apt install --reinstall -o Dpkg::Options::='--force-confnew' <pkg>` — confnew forces maintainer's version to overwrite local; "
        "(3) compare your backup vs the now-clean config to figure out which customizations you want to re-apply. "
        "Alternative: `sudo dpkg-reconfigure <pkg>` re-runs debconf without resetting files — works for packages that use debconf.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-recipes", "apt recipe: build a Docker image with minimal apt cache",
        "Best practice for Dockerfiles using apt. Pattern: "
        "(1) combine update + install + cleanup in ONE RUN to share layer: `RUN apt-get update && apt-get install -y --no-install-recommends pkg1 pkg2 && rm -rf /var/lib/apt/lists/*`; "
        "(2) `--no-install-recommends` shrinks image; `rm -rf /var/lib/apt/lists/*` strips the index cache (10-50MB); "
        "(3) set `DEBIAN_FRONTEND=noninteractive` for any debconf prompts; "
        "(4) for multi-stage builds, only the final stage needs the runtime packages — earlier stages can install bigger toolchains then discard. "
        "Avoid `apt clean` alone — it leaves /var/lib/apt/lists; use the rm above instead.",
        "https://manpages.debian.org/bookworm/apt/apt-get.8.en.html",
    ),

    # ============================================================
    # apt-config — 15 deep configuration claims
    # ============================================================
    (
        "apt-config", "APT::Default-Release setting",
        "Tell apt which release to prefer when multiple are enabled (e.g., stable + testing). Set in /etc/apt/apt.conf.d/99default-release: `APT::Default-Release \"bookworm\";`. "
        "Effect: apt prefers packages from bookworm even if testing has newer versions. To pull from a non-default: `sudo apt -t bookworm-backports install <pkg>`. "
        "Pairs with apt_preferences for finer pinning. Confirm: `apt-config dump APT::Default-Release`.",
        "https://manpages.debian.org/bookworm/apt/apt_preferences.5.en.html",
    ),
    (
        "apt-config", "APT::Get::Show-Upgraded setting",
        "Make `apt-get upgrade` show which packages WOULD be upgraded before asking for confirmation. Default is hidden. "
        "Set in apt.conf.d: `APT::Get::Show-Upgraded \"true\";`. "
        "Useful for operators who want to eyeball every upgrade run.",
        "https://manpages.debian.org/bookworm/apt/apt-get.8.en.html",
    ),
    (
        "apt-config", "Acquire::http::Proxy hierarchy",
        "Multi-level proxy config. (1) global: `Acquire::http::Proxy \"http://proxy:8080\";` (all HTTP repos); "
        "(2) per-scheme: `Acquire::https::Proxy` differs from http; "
        "(3) per-host: `Acquire::http::Proxy::deb.debian.org \"DIRECT\";` overrides global for a specific repo; "
        "(4) credentials: `Acquire::http::Proxy \"http://user:pass@proxy:8080\";` but BETTER to use /etc/apt/auth.conf for creds. "
        "Effective values: `apt-config dump | grep -i proxy` shows the resolved config.",
        "https://manpages.debian.org/bookworm/apt/apt.conf.5.en.html",
    ),
    (
        "apt-config", "Acquire::Languages setting",
        "Control which package-description translations apt downloads. By default apt fetches the local locale's translations + English (~50MB across all locales). "
        "To download none (save bandwidth on slim Docker images): `Acquire::Languages \"none\";` in apt.conf.d. "
        "To force a specific list: `Acquire::Languages \"en,fr,de\";`.",
        "https://manpages.debian.org/bookworm/apt/apt.conf.5.en.html",
    ),
    (
        "apt-config", "Acquire::Retries setting",
        "How many times apt retries a failed download before giving up. Default 0 (no retries — useful for fast-fail CI). "
        "For unreliable networks: `Acquire::Retries \"3\";` retries up to 3 times. Combined with `Acquire::http::Timeout \"30\";` for per-request timeout.",
        "https://manpages.debian.org/bookworm/apt/apt.conf.5.en.html",
    ),
    (
        "apt-config", "DPkg::Pre-Install-Pkgs hook",
        "Run a script BEFORE dpkg installs/removes packages, with the list of .deb files passed on stdin. "
        "Common use cases: log everything dpkg is about to do; snapshot the filesystem before install; trigger external automation. "
        "Set in apt.conf.d: `DPkg::Pre-Install-Pkgs {\"/usr/local/bin/log-pkg-changes\";};`. "
        "The script receives one .deb path per line on stdin. apt-listchanges and apt-listbugs use this mechanism.",
        "https://manpages.debian.org/bookworm/apt/apt.conf.5.en.html",
    ),
    (
        "apt-config", "DPkg::Post-Invoke hook",
        "Run a command AFTER dpkg finishes. Common for cleanup, cache invalidation, monitoring updates. "
        "Set: `DPkg::Post-Invoke {\"systemctl restart prometheus-node-exporter || true\";};` (the `|| true` prevents apt failing if the script does). "
        "Use case: refresh AppArmor profiles, regenerate ldconfig cache, notify monitoring of changes.",
        "https://manpages.debian.org/bookworm/apt/apt.conf.5.en.html",
    ),
    (
        "apt-config", "/var/lib/dpkg/status file format",
        "The canonical record of installed package state on a Debian system. Each block is one package's full metadata: Package, Version, Status, Architecture, Maintainer, Depends, Conffiles (with md5), Description. "
        "Status field encodes 3-state: 'install ok installed' = normal; 'install ok unpacked' = files extracted but not configured; 'deinstall ok config-files' = removed but config remains. "
        "Read directly: `grep -A1 '^Package: nginx' /var/lib/dpkg/status`. dpkg-query is the safe API; don't edit /var/lib/dpkg/status by hand unless recovering.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-config", "/var/log/apt/history.log format",
        "Append-only log of every apt operation, with timestamps. Each block has Start-Date, Commandline, Install/Upgrade/Remove lists, End-Date. "
        "Use case: audit what changed when. Example query: `grep -A4 'Install: nginx' /var/log/apt/history.log` shows when nginx was installed. "
        "Rotated by logrotate; .gz files in same directory have older history.",
        "https://manpages.debian.org/bookworm/apt/apt.conf.5.en.html",
    ),
    (
        "apt-config", "/var/log/apt/term.log",
        "Records the full terminal output of every apt run (stdout + stderr of dpkg). "
        "Use case: debugging a failed install after the fact. `grep -A30 'Sub-process /usr/bin/dpkg returned' /var/log/apt/term.log` shows the actual dpkg error output. "
        "Rotated alongside history.log.",
        "https://manpages.debian.org/bookworm/apt/apt.conf.5.en.html",
    ),
    (
        "apt-config", "apt.conf.d directory loading order",
        "apt reads all .conf files in /etc/apt/apt.conf.d/ alphabetically. Later files override earlier ones on the same key. "
        "Convention: prefix with 2-digit number to control order. Common files: 50unattended-upgrades, 99proxy, 99local-overrides. "
        "Debug: `apt-config dump` shows the final merged config; `apt-config dump --no-empty` skips defaults.",
        "https://manpages.debian.org/bookworm/apt/apt.conf.5.en.html",
    ),
    (
        "apt-config", "apt_preferences pin priority levels (numeric meaning)",
        "Priority values mean specific things, not arbitrary numbers: "
        "1 = ignore unless explicitly requested; "
        "100 = installed-only (the version of this package that's already installed); "
        "500 = default for the configured Default-Release; "
        "990 = default for Target-Release (--target-release); "
        "1000 = upgrade is forbidden; "
        ">1000 = force downgrade if needed. "
        "Setting Pin-Priority: 990 to a non-default repo makes apt prefer it as if it were the default release.",
        "https://manpages.debian.org/bookworm/apt/apt_preferences.5.en.html",
    ),
    (
        "apt-config", "sources.list deb-src lines",
        "`deb-src http://deb.debian.org/debian bookworm main` enables source-package downloads. Required for `apt source <pkg>` and `apt build-dep <pkg>`. "
        "If you never build packages, comment these out — they slow down `apt update`. "
        "For modern deb822 format: in .sources files, set `Types: deb deb-src` to enable both.",
        "https://manpages.debian.org/bookworm/apt/sources.list.5.en.html",
    ),
    (
        "apt-config", "/etc/apt/auth.conf for repo credentials",
        "Per-repo HTTP basic-auth credentials, kept OUT of sources.list. Format (one entry per line): `machine apt.example.com login myuser password mypass`. "
        "Mode must be 0600 (chmod 600 /etc/apt/auth.conf). Multiple machines on multiple lines. "
        "Use case: internal apt mirrors behind auth; commercial repos like Canonical UA, RHEL via Spacewalk for deb.",
        "https://manpages.debian.org/bookworm/apt/apt_auth.conf.5.en.html",
    ),
    (
        "apt-config", "sources.list deb822 (.sources) format",
        "Modern multi-line sources.list format. Each source as a paragraph in /etc/apt/sources.list.d/<name>.sources: "
        "`Types: deb` + `URIs: https://deb.debian.org/debian` + `Suites: bookworm bookworm-updates` + `Components: main contrib non-free` + `Signed-By: /etc/apt/keyrings/debian.gpg`. "
        "Advantages over one-line: clearer, supports multiple suites + components per entry, explicit Signed-By. "
        "Recommended for new sources; one-line format still supported for legacy.",
        "https://manpages.debian.org/bookworm/apt/sources.list.5.en.html",
    ),

    # ============================================================
    # apt-build — package authoring + lintian + builds (~15)
    # ============================================================
    (
        "apt-build", "Debian package basics: what files are in debian/",
        "A Debian source package has a debian/ directory describing how to build it: "
        "control (Package metadata, deps); changelog (version history, dch -i adds entries); rules (Makefile-style build instructions); compat (debhelper compat level); copyright (license info per source file); install (file → install path mapping); .dirs (directories to create); .postinst, .preinst, .postrm, .prerm (maintainer scripts). "
        "Modern packages use debhelper to generate boilerplate; debian/rules is often 4 lines.",
        "https://manpages.debian.org/bookworm/dpkg/deb-control.5.en.html",
    ),
    (
        "apt-build", "dh_make command basics",
        "Bootstrap a debian/ directory from an upstream tarball. Steps: "
        "(1) cd into the upstream source tree (e.g., myapp-1.0/); "
        "(2) `dh_make -s -y --createorig` (`-s` single binary package; `-y` yes to defaults; `--createorig` symlinks the source as .orig.tar.gz); "
        "(3) edit debian/control to set package name, maintainer, description, deps; "
        "(4) edit debian/rules if non-standard build; "
        "(5) `dpkg-buildpackage -us -uc` to build (skip signing).",
        "https://manpages.debian.org/bookworm/dpkg/dpkg-buildpackage.1.en.html",
    ),
    (
        "apt-build", "debian/rules file format",
        "debian/rules is a Makefile that dpkg-buildpackage invokes. Minimum modern form (debhelper-driven): "
        "`#!/usr/bin/make -f\\n%:\\n\\tdh $@` — that's it. dh handles every build target via debhelper commands. "
        "Override a step for custom logic: `override_dh_auto_install:\\n\\tdh_auto_install --buildsystem=cmake`. "
        "Common overrides: skip tests (`override_dh_auto_test:`), customize install paths, run codegen.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg-buildpackage.1.en.html",
    ),
    (
        "apt-build", "debian/control file format",
        "Declares the Source package + binary packages produced from it. Two sections: Source paragraph (Source name, Section, Priority, Maintainer, Build-Depends, Standards-Version) and one or more Binary paragraphs (Package, Architecture, Depends, Description). "
        "Build-Depends lists what you need to BUILD; Depends lists what the installed package needs at RUNTIME. "
        "Use `dpkg-checkbuilddeps` to verify your build environment satisfies Build-Depends.",
        "https://manpages.debian.org/bookworm/dpkg/deb-control.5.en.html",
    ),
    (
        "apt-build", "debian/changelog format and dch command",
        "Each package release gets a changelog entry with version, distribution, urgency, change bullets. Format: "
        "`mypkg (1.0-1) unstable; urgency=medium\\n\\n  * Initial release.\\n\\n -- Maintainer Name <email> Date`. "
        "Use `dch` (devscripts) to manage: `dch -i` adds a new entry; `dch -r` finalizes the current entry for release; `dch -v 2.0-1` sets a specific version. "
        "Failed builds often blame an empty or wrong changelog — start there when debugging.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-build", "lintian command for Debian package QA",
        "Static analyzer for .deb packages. Catches policy violations, common bugs, missing copyright info, security issues. "
        "Run: `lintian package.deb` or `lintian --pedantic --info package.deb` for verbose. "
        "Common warnings: missing-debian-copyright (need debian/copyright), shlibs-declares-non-existing-soname (build issue), executable-not-elf-or-script. "
        "Required-for-Debian-archive: fix all errors before uploading to debian.org.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-build", "dpkg-buildpackage common flags",
        "Build a Debian package from source. Common invocations: "
        "(1) `dpkg-buildpackage -us -uc` — skip signing for local testing; "
        "(2) `dpkg-buildpackage -b` — binary only (skip source package); "
        "(3) `dpkg-buildpackage -j8` — parallel build with 8 threads; "
        "(4) `dpkg-buildpackage -d` — skip Build-Depends check (when you know your env is right); "
        "(5) for cross-builds: `dpkg-buildpackage -aarmhf` (target architecture). "
        "Output goes to the parent directory of the source tree.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg-buildpackage.1.en.html",
    ),
    (
        "apt-build", "pbuilder / sbuild for clean-room builds",
        "Build packages in a chroot/container to ensure reproducibility (no leakage from your dev env). "
        "(1) pbuilder: `sudo pbuilder create --distribution bookworm`; build: `sudo pbuilder build mypkg.dsc`. "
        "(2) sbuild (used by Debian buildds): `sudo sbuild-update bookworm; sbuild -d bookworm mypkg.dsc`. "
        "Both produce .debs in /var/cache/pbuilder/result or ~/sbuild/result. Required for official Debian uploads (proves the package builds from declared Build-Depends only).",
        "https://manpages.debian.org/bookworm/dpkg/dpkg-buildpackage.1.en.html",
    ),
    (
        "apt-build", "apt source command (download source package)",
        "Pull the source package (not the binary .deb). Requires deb-src lines in sources.list. "
        "(1) `apt source <pkg>` downloads .dsc, .orig.tar.gz, .debian.tar.xz and extracts into <pkg>-<version>/; "
        "(2) modify, then `cd <pkg>-<version> && dpkg-buildpackage -us -uc`; "
        "(3) install with `sudo dpkg -i ../<pkg>_*.deb`. "
        "Use case: patching upstream behavior, rebuilding with custom flags.",
        "https://manpages.debian.org/bookworm/apt/apt-get.8.en.html",
    ),
    (
        "apt-build", "apt build-dep command",
        "Install the build dependencies of a package (everything needed to BUILD it from source). "
        "Example: `sudo apt build-dep nginx` pulls every Build-Depends listed in nginx's debian/control. "
        "Pair with `apt source` to set up a build env quickly. For local source trees: `sudo apt build-dep .` reads ./debian/control.",
        "https://manpages.debian.org/bookworm/apt/apt-get.8.en.html",
    ),
    (
        "apt-build", "debhelper compat levels",
        "debhelper has compatibility levels that change behavior across versions. Set in debian/compat (older) or debian/control as `X-DH-Compat: 13` (modern). "
        "Current recommended: compat 13 for modern Debian. "
        "Each level enables/disables certain auto-behaviors (hardening flags, parallel build, output suppression). Higher = more modern defaults but potentially breaking changes if you upgrade. "
        "Check current level: `head debian/compat` or grep `X-DH-Compat` in debian/control.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-build", "dpkg-source command for source package management",
        "Build (-b) or extract (-x) source packages. `dpkg-source -b <source-dir>` produces .dsc + .tar files; `dpkg-source -x <pkg>.dsc` extracts. "
        "Format encoded in debian/source/format: most common is `3.0 (quilt)` (separate orig + debian tarballs, quilt-managed patches in debian/patches/). "
        "Format 1.0 is legacy; 3.0 (native) is for packages where upstream IS the maintainer (no orig.tar).",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-build", "autopkgtest for integration testing",
        "Run integration tests against the built package in a clean env. Pattern: "
        "(1) write tests in debian/tests/control (declares test scripts + their deps); "
        "(2) put test scripts in debian/tests/<name>; "
        "(3) run: `autopkgtest mypkg_1.0-1.dsc -- schroot bookworm` (or lxc, qemu, null). "
        "Use case: confirm the package actually works post-install, not just that it builds. Required for some QA pipelines.",
        "https://manpages.debian.org/bookworm/dpkg/dpkg.1.en.html",
    ),
    (
        "apt-build", "apt-mirror for full repository mirroring",
        "Mirror entire repos locally (not just packages you use). Useful for air-gapped environments. "
        "(1) `sudo apt install apt-mirror`; (2) edit /etc/apt/mirror.list with `deb http://deb.debian.org/debian bookworm main contrib non-free`; (3) `sudo apt-mirror` syncs to /var/spool/apt-mirror/. "
        "Disk usage: full Debian bookworm main = ~400GB. Plan accordingly. "
        "Lighter alternatives for partial mirrors: debmirror (more selective), aptly (managed repos with snapshots).",
        "https://manpages.debian.org/bookworm/apt/sources.list.5.en.html",
    ),
    (
        "apt-build", "aptly for managed repository snapshots",
        "Modern alternative to reprepro for serving custom + mirrored repos with snapshot/promotion workflows. "
        "(1) `sudo apt install aptly` (or download static binary); "
        "(2) create a mirror: `aptly mirror create bookworm http://deb.debian.org/debian bookworm main`; "
        "(3) update: `aptly mirror update bookworm`; "
        "(4) create a snapshot: `aptly snapshot create bookworm-2024-01 from mirror bookworm`; "
        "(5) publish: `aptly publish snapshot bookworm-2024-01`. "
        "Snapshots are immutable — perfect for promoting tested package sets through environments (dev → staging → prod).",
        "https://manpages.debian.org/bookworm/apt/sources.list.5.en.html",
    ),
]


def main() -> None:
    store = get_claim_store()
    orchestrator = CanonOrchestrator(store)
    svc = EmbeddingService.get_default()
    now = datetime.now(timezone.utc)
    ok = 0
    failed = 0
    # Use a different id prefix from the previous apt script
    for i, (tool_id, subject, statement, evidence_url) in enumerate(CLAIMS):
        suffix = f"{i:03d}"
        body_for_hash = f"{subject}\n{statement}"
        evidence = Evidence(
            evidence_id=f"ev-apt-gap-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-apt-gap-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="apt-gaps-catalog",
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
