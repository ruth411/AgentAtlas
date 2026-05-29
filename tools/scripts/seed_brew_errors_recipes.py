"""Direct-insert ~25 brew error claims + ~25 brew recipe claims."""

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

BASE = "https://docs.brew.sh"

CLAIMS = [
    # ============================================================
    # brew-errors
    # ============================================================
    (
        "brew-errors", "brew error: Permission denied @ apply2files - /usr/local/...",
        "Homebrew can't write to its prefix because file ownership is wrong (often after a system upgrade or a manual sudo brew). "
        "Fix on Intel Mac (prefix /usr/local): `sudo chown -R $(whoami) /usr/local/Cellar /usr/local/Homebrew /usr/local/var/homebrew`. "
        "On Apple Silicon (prefix /opt/homebrew): `sudo chown -R $(whoami) /opt/homebrew`. "
        "Avoid going forward: NEVER `sudo brew` — Homebrew refuses to run with sudo for exactly this reason.",
        f"{BASE}/Common-Issues",
    ),
    (
        "brew-errors", "brew error: command not found brew (after install)",
        "brew is installed but not in PATH. Typical on Apple Silicon where the installer prints PATH-setup commands but you missed them. "
        "Fix on Apple Silicon: add to ~/.zshrc (or ~/.bash_profile): `eval \"$(/opt/homebrew/bin/brew shellenv)\"` then `source ~/.zshrc`. "
        "On Intel Mac, /usr/local/bin is usually already in PATH; if not, add similar shellenv eval. "
        "Verify: `which brew` should return /opt/homebrew/bin/brew (M-series) or /usr/local/bin/brew (Intel).",
        f"{BASE}/Installation",
    ),
    (
        "brew-errors", "brew error: Error: Cannot install in Homebrew on ARM processor in Intel default prefix",
        "You ran the Intel installer on Apple Silicon, or vice-versa. Each arch has its own prefix and brew binary. "
        "Fix: (1) uninstall the wrong one: `/usr/local/bin/brew` on M-series, follow uninstall.sh; (2) re-run the correct installer from https://brew.sh which detects your arch. "
        "Power-user split-install (running both Intel-Rosetta brew + native arm64 brew): use `arch -x86_64 /usr/local/bin/brew` to invoke the Intel one explicitly.",
        f"{BASE}/Homebrew-on-Apple-Silicon",
    ),
    (
        "brew-errors", "brew error: SHA256 mismatch (sha256 of expected file doesn't match)",
        "Downloaded file's hash doesn't match what the formula says. Usually means the upstream changed the tarball or the download was corrupted. "
        "Fix: (1) `brew cleanup` then retry — clears stale cached downloads; (2) `brew update` to pull formula updates (the formula may have been bumped); "
        "(3) for casks installing apps from a vendor, the vendor may have rotated the binary — wait for the cask maintainer to update OR `brew install --no-quarantine --cask <name>` after verifying the new hash yourself.",
        f"{BASE}/Common-Issues",
    ),
    (
        "brew-errors", "brew error: Your CLT does not support macOS Sequoia (or your macOS version)",
        "Command Line Tools for Xcode is too old for your current macOS. Many formulae need a recent CLT to build. "
        "Fix: (1) reinstall: `sudo rm -rf /Library/Developer/CommandLineTools && xcode-select --install` (this opens a GUI installer); "
        "(2) `brew doctor` should now pass; (3) alternatively install full Xcode from the App Store if you also need iOS dev tools.",
        f"{BASE}/Common-Issues",
    ),
    (
        "brew-errors", "brew error: No available formula or cask with the name",
        "The formula or cask name doesn't exist in any tapped repo. "
        "Fix: (1) `brew search <pattern>` to find similar names — case matters; (2) for casks, prefix with `--cask`: `brew install --cask discord`; "
        "(3) if it's from a third-party tap: `brew tap <user>/<repo> && brew install <user>/<repo>/<formula>`; "
        "(4) maybe it was renamed — check `brew search --desc <keyword>` for description-based search.",
        f"{BASE}/Querying-Brew",
    ),
    (
        "brew-errors", "brew error: cask 'X' is unavailable: No Cask with this name exists",
        "Specific to casks. Usually means you forgot `--cask` for a GUI app: `brew install firefox` looks for the formula (CLI) — `brew install --cask firefox` looks for the GUI app. "
        "Fix: try with `--cask`. If still missing, the cask may have been moved/removed — `brew tap homebrew/cask-versions` for older versions, or `brew tap-info homebrew/cask` to see what's in the official cask tap.",
        f"{BASE}/Acceptable-Casks",
    ),
    (
        "brew-errors", "brew warning: It seems you have an outdated copy of Xcode",
        "Xcode is installed but its CLT subset doesn't match the CLT brew needs. "
        "Fix: (1) `sudo xcode-select -s /Library/Developer/CommandLineTools` to point at the standalone CLT; "
        "(2) OR `sudo xcode-select -s /Applications/Xcode.app/Contents/Developer` to use Xcode's bundled tools; "
        "(3) verify: `xcode-select -p` shows the active path; `brew doctor` then runs cleanly.",
        f"{BASE}/Common-Issues",
    ),
    (
        "brew-errors", "brew error: Could not symlink (because target file exists)",
        "brew tries to symlink /opt/homebrew/bin/<binary> but /usr/local/bin/<binary> already exists from a non-brew install. "
        "Fix: (1) `brew link --overwrite <formula>` — force-replace the existing symlink; "
        "(2) BUT confirm what owns the existing file first: `ls -la /usr/local/bin/<binary>` shows the actual file location; "
        "(3) if you want to keep both: `brew link --overwrite --dry-run` shows what would change.",
        f"{BASE}/Common-Issues",
    ),
    (
        "brew-errors", "brew warning: Homebrew's `bin` is not in your PATH",
        "`brew doctor` detected /opt/homebrew/bin (or /usr/local/bin) is missing from PATH. "
        "Fix: add to ~/.zshrc: `export PATH=\"/opt/homebrew/bin:$PATH\"` (Apple Silicon) or `export PATH=\"/usr/local/bin:$PATH\"` (Intel). "
        "Modern install uses `eval \"$(/opt/homebrew/bin/brew shellenv)\"` which sets PATH + MANPATH + INFOPATH all at once. "
        "Source the rc file: `source ~/.zshrc`.",
        f"{BASE}/Installation",
    ),
    (
        "brew-errors", "brew warning: You have unlinked kegs in your Cellar",
        "Formulae are installed but not symlinked into bin/. Usually after a `brew unlink` or interrupted upgrade. "
        "Fix: `brew link <formula>` for each named one. For all: `brew list --formula | xargs brew link` (skip casks). "
        "Reason brew might unlink intentionally: version conflict (e.g., two versions of openssl can't both be in /usr/local/bin/openssl).",
        f"{BASE}/Manpage",
    ),
    (
        "brew-errors", "brew error: Don't run `brew update` as root",
        "Homebrew refuses sudo. Period. "
        "Fix: run `brew update` (no sudo). If you genuinely need to run brew commands from a non-owner user, transfer ownership: `sudo chown -R newuser $(brew --prefix)` then run as newuser. "
        "For shared servers with multiple users, each user should have their own Homebrew install under their home directory.",
        f"{BASE}/FAQ",
    ),
    (
        "brew-errors", "brew error: Error downloading (curl: error 6 — Could not resolve host)",
        "Network issue: DNS, proxy, or upstream outage. "
        "Fix: (1) `ping github.com` — if it fails, fix DNS; (2) corporate proxy? set HOMEBREW_HTTP_PROXY and HOMEBREW_HTTPS_PROXY env vars; "
        "(3) for self-signed cert proxies: `HOMEBREW_FORCE_BREWED_CURL=1 HOMEBREW_FORCE_BREWED_GIT=1` uses brew's own curl/git with custom CA setup; "
        "(4) cache the downloads with HOMEBREW_DOWNLOAD_CONCURRENCY=1 to slow things down for flaky links.",
        f"{BASE}/Common-Issues",
    ),
    (
        "brew-errors", "brew error: Cannot delete /usr/local/Homebrew/.git/index.lock",
        "A previous `brew update` was interrupted, leaving git's lock file. "
        "Fix: `rm /usr/local/Homebrew/.git/index.lock` (or /opt/homebrew/Homebrew/.git/index.lock on Apple Silicon), then retry `brew update`.",
        f"{BASE}/Common-Issues",
    ),
    (
        "brew-errors", "brew error: Failure while executing; `git fetch` exited with 128",
        "Git's clone/fetch into Homebrew's repos failed — usually credential issues, network, or repo URL changed. "
        "Fix: (1) `cd $(brew --repo) && git status` — see what's wrong; (2) for credential issues, clear cached creds with `git credential reject`; "
        "(3) reset the repo: `cd $(brew --repo) && git reset --hard origin/master`. Same for core: `cd $(brew --repo homebrew/core) && git reset --hard origin/master`.",
        f"{BASE}/Common-Issues",
    ),
    (
        "brew-errors", "brew warning: You have not agreed to the Xcode license",
        "Apple's Xcode license must be accepted before its CLT works. "
        "Fix: `sudo xcodebuild -license accept`. Press space to scroll through the EULA, type `agree` at the prompt.",
        f"{BASE}/Common-Issues",
    ),
    (
        "brew-errors", "brew error: Cellar/<formula>/<version> already exists",
        "A previous install attempt left a half-installed Cellar entry. "
        "Fix: `brew uninstall --force <formula>` then `brew install <formula>`. The --force removes ALL versions of the formula even if some are linked elsewhere.",
        f"{BASE}/Manpage",
    ),
    (
        "brew-errors", "brew error: rosetta required for x86_64 formula on Apple Silicon",
        "An Intel-only formula tried to install on arm64 without Rosetta available. "
        "Fix: (1) install Rosetta: `softwareupdate --install-rosetta --agree-to-license`; (2) wait for arm64 build (most popular formulae have native arm64 bottles now); "
        "(3) explicitly install via Intel brew: `arch -x86_64 /usr/local/bin/brew install <formula>` if you have a split install.",
        f"{BASE}/Homebrew-on-Apple-Silicon",
    ),
    (
        "brew-errors", "brew warning: This formula is keg-only",
        "Not really an error — keg-only formulae aren't symlinked into bin by default, usually to avoid clashing with macOS system versions (openssl, ruby, python). "
        "Use them: (1) `brew --prefix <formula>` returns the install path; (2) add to PATH manually: `export PATH=\"$(brew --prefix openssl)/bin:$PATH\"`; "
        "(3) force-link if you really want it shadowing the system version: `brew link --force <formula>`. Most macOS dev workflows: leave keg-only, add to per-project rc files.",
        f"{BASE}/FAQ",
    ),
    (
        "brew-errors", "brew error: brew autoupdate failed",
        "Auto-update runs before most brew commands; if your taps are in a bad state, every brew operation fails. "
        "Fix: (1) `HOMEBREW_NO_AUTO_UPDATE=1 brew <whatever>` skips auto-update so you can run things; "
        "(2) reset taps: `cd $(brew --repository) && git fetch && git reset --hard origin/master`; "
        "(3) skip auto-update permanently in env: `export HOMEBREW_NO_AUTO_UPDATE=1` in your shell rc.",
        f"{BASE}/Manpage",
    ),
    (
        "brew-errors", "brew warning: Unbrewed dylibs were found in /usr/local/lib",
        "Manually-installed shared libs are in the prefix where brew expects only its own. `brew doctor` flags these. "
        "Not strictly an error; can cause linker confusion in builds. "
        "Fix: (1) remove unrelated libs: `ls /usr/local/lib/*.dylib` — anything you don't recognize is suspect; "
        "(2) if you can't delete (something depends on it), set HOMEBREW_NO_INSECURE_REDIRECT=1 and live with the warning; "
        "(3) for libraries you NEED outside brew, move them to /usr/local/opt/<your-pkg>/lib/ and link from there.",
        f"{BASE}/Common-Issues",
    ),
    (
        "brew-errors", "brew error: cask requires admin password (quarantine attribute)",
        "macOS quarantine flag on downloaded apps. brew normally handles this but enterprise security policies may block. "
        "Fix: (1) `brew install --cask --no-quarantine <name>` — skips quarantine; (2) for orgs with config profiles enforcing Gatekeeper, you can't bypass — work with IT; "
        "(3) for one-off use after install: `xattr -d com.apple.quarantine /Applications/AppName.app`.",
        f"{BASE}/Cask-Cookbook",
    ),
    (
        "brew-errors", "brew warning: `brew cask` is no longer a subcommand",
        "Old syntax. `brew cask install` was renamed to `brew install --cask` years ago. "
        "Fix: replace `brew cask install <X>` with `brew install --cask <X>`. Same for `brew cask uninstall` → `brew uninstall --cask`, `brew cask upgrade` → `brew upgrade --cask`. "
        "Or just `brew install <X>` — brew auto-detects if it's a formula or cask in most cases.",
        f"{BASE}/Manpage",
    ),
    (
        "brew-errors", "brew error: `brew services` command not found",
        "brew services is a third-party tap that may not be installed by default in some configurations. "
        "Fix: `brew tap homebrew/services` then `brew services list` works. On modern Homebrew it's bundled; you only need to tap if you somehow opted out.",
        f"{BASE}/Manpage",
    ),
    (
        "brew-errors", "brew error: out of disk space (Cellar growing huge)",
        "brew's Cellar accumulates every installed version forever. After years, /opt/homebrew/Cellar can hit 50+ GB. "
        "Fix: (1) `brew cleanup` removes old versions of installed formulae (run periodically — saves several GB); "
        "(2) `brew cleanup --prune=all` aggressive cleanup including logs/caches; "
        "(3) `brew autoremove` removes orphaned dependencies; "
        "(4) check disk usage: `du -sh $(brew --cellar)/*` shows per-formula size — uninstall hogs you don't need.",
        f"{BASE}/Manpage",
    ),

    # ============================================================
    # brew-recipes
    # ============================================================
    (
        "brew-recipes", "brew recipe: install Homebrew on a fresh macOS",
        "Standard install. Steps: "
        "(1) `/bin/bash -c \"$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"` — the canonical installer; "
        "(2) accept the Xcode CLT prompt that appears; "
        "(3) on Apple Silicon, the installer prints two `eval` commands at the end — paste them into ~/.zshrc + run them in current shell; "
        "(4) verify: `brew doctor` should say 'Your system is ready to brew'; "
        "(5) install common dev tools: `brew install git curl wget jq tree`.",
        f"{BASE}/Installation",
    ),
    (
        "brew-recipes", "brew recipe: install Homebrew on Apple Silicon (M1/M2/M3)",
        "Same installer but the prefix is /opt/homebrew (not /usr/local). Steps: "
        "(1) standard `/bin/bash -c \"$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"`; "
        "(2) at the end the installer prints: `echo 'eval \"$(/opt/homebrew/bin/brew shellenv)\"' >> ~/.zprofile` and `eval \"$(/opt/homebrew/bin/brew shellenv)\"` — RUN these; "
        "(3) optional: for Intel-only formulae (rare), install Rosetta + Intel brew: `arch -x86_64 /bin/bash -c \"$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"`. "
        "Use `arch -x86_64 /usr/local/bin/brew install <X>` for Intel-only.",
        f"{BASE}/Homebrew-on-Apple-Silicon",
    ),
    (
        "brew-recipes", "brew recipe: install Linuxbrew (Homebrew on Linux)",
        "Same installer; prefix is /home/linuxbrew/.linuxbrew. Steps: "
        "(1) install build deps first: `sudo apt install build-essential procps curl file git`; "
        "(2) `/bin/bash -c \"$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"`; "
        "(3) add to shell rc: `echo 'eval \"$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)\"' >> ~/.bashrc && source ~/.bashrc`; "
        "(4) `brew install gcc` recommended (avoids using system gcc for brew-built formulae). "
        "Use case: install consistent CLI tools across Linux servers without sudo.",
        f"{BASE}/Homebrew-on-Linux",
    ),
    (
        "brew-recipes", "brew recipe: manage services with brew services",
        "Auto-start daemons like postgres/redis/nginx. Steps: "
        "(1) `brew install postgresql@15`; (2) `brew services start postgresql@15` — installs a launchd plist + starts the service; "
        "(3) `brew services list` shows status; (4) `brew services stop postgresql@15`; (5) `brew services restart postgresql@15`. "
        "Plist location: `~/Library/LaunchAgents/homebrew.mxcl.<formula>.plist`. Customize there for non-standard ports, env vars, log paths.",
        f"{BASE}/FAQ",
    ),
    (
        "brew-recipes", "brew recipe: declare and install a dev environment with Brewfile",
        "Use a Brewfile to reproducibly declare brew packages. Steps: "
        "(1) generate from current install: `brew bundle dump --file=~/Brewfile`; (2) commit Brewfile to your dotfiles repo; "
        "(3) on a new machine: `brew bundle --file=~/Brewfile` — installs everything from the file; "
        "(4) syntax: `brew \"git\"`, `cask \"firefox\"`, `tap \"homebrew/cask-fonts\"`, `mas \"Xcode\", id: 497799835` (for Mac App Store via `brew install mas` first). "
        "Lock: `brew bundle list --file=~/Brewfile` shows what would be installed without running.",
        f"{BASE}/Brew-Bundle-and-Brewfile",
    ),
    (
        "brew-recipes", "brew recipe: upgrade everything safely",
        "Routine upgrade pattern. Steps: "
        "(1) `brew update` — refresh the formula index (NOT a package upgrade); "
        "(2) `brew outdated` — list what's pending upgrade (preview); "
        "(3) `brew upgrade` — upgrade all formulae; "
        "(4) `brew upgrade --cask --greedy` — upgrade GUI apps too (greedy ignores auto-update flag); "
        "(5) `brew cleanup` — remove old versions to reclaim disk; "
        "(6) `brew doctor` — sanity-check post-upgrade. "
        "For selective upgrade: `brew upgrade <formula>` upgrades just that one.",
        f"{BASE}/Manpage",
    ),
    (
        "brew-recipes", "brew recipe: install a specific older version",
        "brew doesn't support arbitrary version pinning, but offers two paths. "
        "(1) Versioned formula: many packages have `foo@<version>` variants — `brew install postgresql@14` installs 14 (vs latest `brew install postgresql`); list available: `brew search 'postgresql@'`. "
        "(2) Build from a specific commit (rare/expert): `cd $(brew --repository homebrew/core) && git log --oneline -- Formula/<f>.rb | grep <version>; git checkout <sha> -- Formula/<f>.rb; brew install <f>; git checkout HEAD -- Formula/<f>.rb`. Brittle — only for one-off use. "
        "Real version management for dev: use mise, asdf, or pyenv/nvm for languages.",
        f"{BASE}/Versions",
    ),
    (
        "brew-recipes", "brew recipe: tap a third-party formula repo",
        "Use external formulae not in homebrew/core. Steps: "
        "(1) `brew tap user/repo` — installs the tap (clones the GitHub repo); "
        "(2) `brew install user/repo/<formula>` — explicitly namespaced; or just `brew install <formula>` if the name isn't ambiguous; "
        "(3) `brew tap` lists installed taps; (4) `brew untap user/repo` removes the tap. "
        "Useful taps: `homebrew/cask-fonts` (programming fonts), `mongodb/brew` (MongoDB Community), `wez/wezterm` (terminal).",
        f"{BASE}/Taps",
    ),
    (
        "brew-recipes", "brew recipe: build a custom formula from a tarball",
        "Quick formula scaffold. Steps: "
        "(1) `brew create https://example.com/foo-1.0.tar.gz` — generates a starter formula in Formula/foo.rb in your tap repo; "
        "(2) edit the formula: fill in description, homepage, license, install method (`make install` or `cmake` or `cargo build`), test block; "
        "(3) `brew install --build-from-source foo` to test; "
        "(4) `brew audit --strict foo` to check style; "
        "(5) `brew test foo` runs the test block. "
        "For pure-Python: see `Python-for-Formula-Authors` doc.",
        f"{BASE}/Formula-Cookbook",
    ),
    (
        "brew-recipes", "brew recipe: build a brew cask for a GUI app",
        "Casks are for macOS .app/.pkg/.dmg distributions. Skeleton: "
        "`cask \"myapp\" do; version \"1.2.3\"; sha256 \"...\"; url \"https://example.com/myapp-#{version}.dmg\"; name \"MyApp\"; desc \"...\"; homepage \"https://example.com\"; app \"MyApp.app\"; end`. "
        "Steps: (1) `brew create --cask <url>` scaffolds; (2) place under `homebrew-cask` or your own tap; (3) `brew install --cask myapp` to test; (4) `brew style --fix --cask myapp` for style. "
        "Read `Acceptable-Casks` to understand what gets accepted into the official cask tap.",
        f"{BASE}/Cask-Cookbook",
    ),
    (
        "brew-recipes", "brew recipe: pin a formula to prevent upgrade",
        "Block specific formulae from being upgraded by `brew upgrade`. Steps: "
        "(1) `brew pin <formula>` — pins; (2) `brew list --pinned` shows pinned ones; (3) `brew unpin <formula>` to release. "
        "Use case: production-deployed binary that shouldn't change between updates. "
        "Note: pinning doesn't prevent dependency upgrades that affect the pinned formula's behavior. For true reproducibility use Brewfile + version control.",
        f"{BASE}/Manpage",
    ),
    (
        "brew-recipes", "brew recipe: search and explore formulae",
        "Discovery commands. "
        "(1) `brew search <pattern>` — fuzzy name search; (2) `brew search --desc <keyword>` — search descriptions too; "
        "(3) `brew info <formula>` — show description, version, deps, install size, caveats; (4) `brew home <formula>` — opens upstream homepage in browser; "
        "(5) `brew deps --tree <formula>` — dependency tree; (6) `brew uses <formula>` — what depends on this; "
        "(7) `brew leaves` — installed formulae no one depends on (top-level installs).",
        f"{BASE}/Querying-Brew",
    ),
    (
        "brew-recipes", "brew recipe: uninstall a formula and its orphaned deps",
        "Clean removal. Steps: "
        "(1) `brew uninstall <formula>` — removes the formula itself; "
        "(2) `brew autoremove` — removes deps that are now orphans (no one depends on them); "
        "(3) `brew cleanup` — drops old downloads/cache; "
        "(4) for hard reset: `brew uninstall --force <formula>` removes all versions. "
        "Casks: `brew uninstall --cask <name>` — adds `--zap` to also delete app preferences (in ~/Library): `brew uninstall --cask --zap firefox`.",
        f"{BASE}/Manpage",
    ),
    (
        "brew-recipes", "brew recipe: configure brew analytics opt-out",
        "Homebrew anonymously collects install counts. To opt out: "
        "`export HOMEBREW_NO_ANALYTICS=1` in ~/.zshrc, source it, and verify with `brew analytics` (should report disabled). "
        "What's collected (when on): install counts per formula, OS version, brew version. NO personal info, NO file paths beyond the formula name. "
        "Why opt in: helps Homebrew maintainers prioritize popular formulae's bottle builds. Why opt out: corporate policy, privacy preference.",
        f"{BASE}/Anonymous-Aggregate-User-Behaviour-Analytics",
    ),
    (
        "brew-recipes", "brew recipe: install Mac App Store apps via brew",
        "Use mas (Mac App Store CLI) to scriptably install paid/free App Store apps. Steps: "
        "(1) `brew install mas`; (2) `mas signin <apple-id>` (only needed once; uses Apple ID); "
        "(3) `mas search Xcode` to find app IDs; (4) `mas install 497799835` for Xcode; "
        "(5) put in Brewfile: `mas \"Xcode\", id: 497799835` so `brew bundle` covers App Store apps too. "
        "Limitation: doesn't work for App Store apps purchased on a different Apple ID — must be on this Mac's signed-in account.",
        f"{BASE}/Brew-Bundle-and-Brewfile",
    ),
    (
        "brew-recipes", "brew recipe: install Python with brew (managing PATH correctly)",
        "brew's Python is keg-only (won't shadow system Python). Steps: "
        "(1) `brew install python@3.12`; (2) brew installs to /opt/homebrew/opt/python@3.12/bin/python3 (Apple Silicon); "
        "(3) symlinks: `python3` is in /opt/homebrew/bin/python3 by default — verify with `which python3`; "
        "(4) for specific version control across projects, use pyenv or mise instead of brew; "
        "(5) pip3 packages install per-user to `$HOME/Library/Python/<v>/bin` — add to PATH for `pip3 install --user` workflows.",
        f"{BASE}/Homebrew-and-Python",
    ),
    (
        "brew-recipes", "brew recipe: install GNU coreutils (gnu-utils) on macOS",
        "macOS ships BSD versions of common tools (sed, awk, ls) that differ subtly from GNU/Linux. Install GNU variants. Steps: "
        "(1) `brew install coreutils gnu-sed gnu-tar gawk findutils grep`; "
        "(2) by default the GNU versions install with `g` prefix (gsed, gawk) to avoid shadowing the BSD versions; "
        "(3) to use without prefix, add to ~/.zshrc: `PATH=\"$(brew --prefix coreutils)/libexec/gnubin:$PATH\"` etc.; "
        "(4) tradeoff: scripts written assuming GNU now work; scripts assuming BSD may break. Be deliberate per project.",
        f"{BASE}/FAQ",
    ),
    (
        "brew-recipes", "brew recipe: build a bottle (binary package) for distribution",
        "For tap maintainers wanting to ship pre-compiled binaries. Steps: "
        "(1) compile from source: `brew install --build-bottle <formula>`; "
        "(2) generate bottle metadata: `brew bottle <formula>`; "
        "(3) upload the resulting .bottle.tar.gz to a release on GitHub; "
        "(4) update the formula with `bottle do; sha256 ...end` block referencing the URL. "
        "Why: end users get binary install instead of compiling — saves minutes to hours per formula. Required for official homebrew/core.",
        f"{BASE}/Bottles",
    ),
    (
        "brew-recipes", "brew recipe: run brew doctor and interpret common warnings",
        "Diagnostic. `brew doctor` checks dozens of things. Common warnings + meanings: "
        "(1) 'unbrewed dylibs' — manually-installed libs in brew's prefix; usually harmless; "
        "(2) 'outdated Xcode' — install CLT update; "
        "(3) 'Homebrew's bin is not in PATH' — fix shell rc; "
        "(4) 'unbrewed kegs' — pre-brew installs; review and delete or move; "
        "(5) 'analytics disabled by env' — fine if intentional. "
        "Doctor is a first stop when anything misbehaves — even 'all clean' is reassuring.",
        f"{BASE}/Common-Issues",
    ),
    (
        "brew-recipes", "brew recipe: install fonts via brew cask",
        "Programming fonts (Fira Code, JetBrains Mono, etc.) via the cask-fonts tap. Steps: "
        "(1) `brew tap homebrew/cask-fonts`; "
        "(2) `brew install --cask font-fira-code`; (3) brew drops .otf/.ttf into ~/Library/Fonts (or /Library/Fonts for systemwide). "
        "Search: `brew search --cask 'font-'` lists available fonts. Bundle: `cask \"font-fira-code\"` in Brewfile. "
        "Notable picks: font-fira-code (ligatures), font-jetbrains-mono, font-cascadia-code, font-hack-nerd-font (programming icons).",
        f"{BASE}/Cask-Cookbook",
    ),
    (
        "brew-recipes", "brew recipe: cleanup and prune disk usage",
        "Reclaim space brew accumulates. Steps: "
        "(1) `brew cleanup` — removes old versions of installed formulae + cached downloads; "
        "(2) `brew cleanup --prune=all` — aggressive: drops everything including bottle logs; "
        "(3) `brew autoremove` — removes orphan deps; "
        "(4) `brew doctor` after to confirm clean state. "
        "Before/after: `du -sh $(brew --cellar) $(brew --cache)` shows recovered space. Run quarterly on dev machines; saves 1-10 GB typically.",
        f"{BASE}/Manpage",
    ),
    (
        "brew-recipes", "brew recipe: pin to specific brew version (avoid auto-update)",
        "Stop the implicit `brew update` that runs before most commands. "
        "Temporary: `HOMEBREW_NO_AUTO_UPDATE=1 brew install <X>` skips for one command. "
        "Permanent: add `export HOMEBREW_NO_AUTO_UPDATE=1` to ~/.zshrc. "
        "Use case: production-like dev box where you control upgrades manually + want speed (auto-update can take 30s+); CI runners where you upgrade only at known checkpoints. "
        "When you want to update: `brew update` explicitly, then run brew commands.",
        f"{BASE}/Manpage",
    ),
    (
        "brew-recipes", "brew recipe: use brew on a corporate proxy",
        "When behind enterprise proxy. Set env vars: "
        "`export HOMEBREW_HTTP_PROXY=http://proxy.corp:8080`, `export HOMEBREW_HTTPS_PROXY=http://proxy.corp:8080` (in ~/.zshrc). "
        "For proxy auth: `http://user:pass@proxy.corp:8080`. For corporate self-signed CA: `export HOMEBREW_CURLRC=1 && add `cacert <path-to-ca-bundle>` to ~/.curlrc`. "
        "If your proxy also intercepts GitHub TLS: same CA setup PLUS `export HOMEBREW_NO_GITHUB_API=1` (skips GitHub API auth which can fail through some proxies).",
        f"{BASE}/Common-Issues",
    ),
    (
        "brew-recipes", "brew recipe: migrate Homebrew from Intel to Apple Silicon",
        "After buying a new M-series Mac. Steps: "
        "(1) DON'T just copy /usr/local — Intel binaries won't run; (2) generate a Brewfile on the old machine: `brew bundle dump --file=~/Brewfile`; "
        "(3) copy Brewfile to the new Mac; (4) install Homebrew on the new Mac per the Apple Silicon recipe; (5) `brew bundle --file=~/Brewfile` to reinstall everything; "
        "(6) for Intel-only formulae (rare): install via Rosetta + Intel brew at /usr/local/. "
        "Bonus: review the Brewfile first — opportunity to drop stuff you don't actually use.",
        f"{BASE}/Brew-Bundle-and-Brewfile",
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
            evidence_id=f"ev-brew-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-brew-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="brew-catalog",
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
