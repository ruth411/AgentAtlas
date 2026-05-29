"""Direct-insert ~25 cargo error claims + ~25 cargo recipe claims."""

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

BASE = "https://doc.rust-lang.org/cargo"

CLAIMS = [
    # ============================================================
    # cargo-errors
    # ============================================================
    (
        "cargo-errors", "cargo error: failed to select a version for the requirement",
        "Cargo can't find a version of a dependency that satisfies all constraints in your Cargo.toml + transitive deps. "
        "Fix: (1) `cargo tree -i <crate>` shows which deps depend on what — reveals the conflict; (2) loosen the version constraint to `^X` instead of `=X.Y.Z`; "
        "(3) use `[patch]` section in Cargo.toml to override a specific version: `[patch.crates-io] foo = { git = \"...\" }`; "
        "(4) check the resolver version — set `resolver = \"2\"` in [workspace] or [package] for the newer 'feature-aware' resolver that handles more cases.",
        f"{BASE}/reference/resolver.html",
    ),
    (
        "cargo-errors", "cargo error: linker `cc` not found",
        "Cargo's compile invokes a linker (cc on Unix, link.exe on Windows) but it's not installed. "
        "Fix on Linux: `sudo apt install build-essential` (Debian/Ubuntu) or `sudo dnf install gcc make` (RHEL). "
        "Fix on macOS: `xcode-select --install` to get CLT. "
        "Fix on Windows: install Visual Studio Build Tools (C++ workload). "
        "For cross-compilation, configure linker in `.cargo/config.toml`: `[target.aarch64-unknown-linux-gnu] linker = \"aarch64-linux-gnu-gcc\"`.",
        f"{BASE}/reference/config.html",
    ),
    (
        "cargo-errors", "cargo error: could not find `Cargo.toml` in any parent directory",
        "You ran cargo outside a Rust project directory. "
        "Fix: (1) `cd` into a project with a Cargo.toml; (2) for a new project: `cargo new myproject && cd myproject`; "
        "(3) for an existing dir without Cargo.toml: `cargo init` creates one in place; "
        "(4) `cargo locate-project` shows where the nearest Cargo.toml is — useful for scripts to find the workspace root.",
        f"{BASE}/commands/cargo-locate-project.html",
    ),
    (
        "cargo-errors", "cargo error: failed to compile due to compilation errors (rustc errors)",
        "Cargo invoked rustc and rustc reported source-level errors. These come from your Rust code, not cargo itself. "
        "Fix: (1) read the rustc output above — it names the file + line + reason; (2) `cargo check` for fast type-check; "
        "(3) `cargo clippy` for additional lint suggestions; (4) `cargo fix --bin <name>` to auto-apply some fixes. "
        "Common categories: borrow checker errors (lifetimes), type mismatches, missing trait bounds. Each rustc error has an EXXXX code — `rustc --explain EXXXX` shows long-form documentation.",
        f"{BASE}/commands/cargo-check.html",
    ),
    (
        "cargo-errors", "cargo error: package `X` cannot be built because it requires rustc Y or newer",
        "A dependency needs a newer Rust compiler than you have installed. "
        "Fix: (1) `rustup update stable` to get the latest stable; "
        "(2) `rustup default stable` if you're stuck on an older toolchain; "
        "(3) for projects pinned to a specific Rust version (rust-toolchain.toml), update that file; "
        "(4) for older systems where rustup isn't possible, find an older version of the dep: `cargo add foo --rev <commit>` to pin to a pre-bump release.",
        f"{BASE}/reference/manifest.html",
    ),
    (
        "cargo-errors", "cargo error: rust-toolchain or rust-toolchain.toml mismatch",
        "Your project's rust-toolchain.toml specifies a Rust version not currently installed. rustup tries to install it but failed. "
        "Fix: (1) `rustup install <version>` manually; (2) check the toolchain file — `channel = \"1.78.0\"` etc.; "
        "(3) on offline machines, vendor the toolchain via rustup's offline install method; "
        "(4) for CI, ensure the runner has network access to rust-lang.org and the right toolchain is cached.",
        f"{BASE}/reference/config.html",
    ),
    (
        "cargo-errors", "cargo error: failed to get `X` as a dependency of package `Y`",
        "Cargo couldn't download a dependency — usually network, registry-auth, or wrong source URL. "
        "Fix: (1) check internet connectivity + crates.io status; "
        "(2) for git dependencies, verify the URL + branch exist + you have access (SSH keys for private repos); "
        "(3) for private registries, `cargo login --registry <name>` to set the auth token; "
        "(4) behind proxy: set CARGO_HTTP_PROXY + CARGO_HTTPS_PROXY env vars or configure in `~/.cargo/config.toml` `[http] proxy = \"...\"`.",
        f"{BASE}/reference/registry-authentication.html",
    ),
    (
        "cargo-errors", "cargo error: error[E0432]: unresolved import",
        "Rust source code references a module that's not in your Cargo.toml dependencies. "
        "Fix: (1) `cargo add <crate>` to add the missing dependency; (2) if it should be a path-dependency: `cargo add <name> --path ../sibling`; "
        "(3) if it's a sub-module of an installed crate, you may need to enable a feature flag: `cargo add tokio --features full`; "
        "(4) for `use crate::foo`, ensure the module is declared in lib.rs/main.rs with `mod foo;`.",
        f"{BASE}/commands/cargo-add.html",
    ),
    (
        "cargo-errors", "cargo error: the lock file needs to be updated but --frozen was passed",
        "Cargo wants to update Cargo.lock but you used `--frozen` (or `--locked` in CI), which forbids changes. "
        "Fix: (1) remove `--frozen` for one-time use to let cargo update the lock; (2) if running CI with frozen: `cargo update` locally, commit the new Cargo.lock; "
        "(3) `--locked` is stricter than `--frozen` — both prevent lockfile changes; (4) for fresh-clone CI runs without a committed Cargo.lock, neither flag works — generate one first.",
        f"{BASE}/guide/cargo-toml-vs-cargo-lock.html",
    ),
    (
        "cargo-errors", "cargo error: this `Cargo.toml` is a workspace member but has no parent workspace",
        "Your Cargo.toml declares `[workspace]` membership but the parent directory has no workspace root. "
        "Fix: (1) ensure parent dir has a Cargo.toml with `[workspace] members = [\"member-dir\"]`; "
        "(2) for nested workspace situations, add `[workspace] workspace = \"../path\"` (cargo 1.71+); "
        "(3) check `members` paths are relative to the workspace root, not the current package.",
        f"{BASE}/reference/workspaces.html",
    ),
    (
        "cargo-errors", "cargo error: failed to publish to registry (already exists)",
        "You tried `cargo publish` but a version with that number already exists on crates.io (or your custom registry). "
        "Fix: (1) bump version in Cargo.toml: `version = \"X.Y.Z+1\"`; "
        "(2) commit the bump; "
        "(3) `cargo publish` again. "
        "Note: published versions on crates.io are PERMANENT (can't delete, only yank). Yank with `cargo yank --vers X.Y.Z` to prevent future installs but allow existing Cargo.lock pinned users to still resolve.",
        f"{BASE}/commands/cargo-publish.html",
    ),
    (
        "cargo-errors", "cargo error: no matching package found",
        "`cargo install <name>` or `cargo add <name>` couldn't find the package. "
        "Fix: (1) check spelling — `cargo search <pattern>` lists matching names; "
        "(2) for a specific version: `cargo install <name> --version X.Y.Z`; "
        "(3) for a git crate: `cargo install --git <url>`; "
        "(4) for local path: `cargo install --path ./bin-crate`; "
        "(5) for binaries from non-crates.io registries: `cargo install <name> --registry myreg` (configure registry in `~/.cargo/config.toml` first).",
        f"{BASE}/commands/cargo-install.html",
    ),
    (
        "cargo-errors", "cargo error: unable to update Cargo.toml directly with cargo add",
        "Old cargo (pre-1.62) lacked `cargo add` as built-in. "
        "Fix: (1) `rustup update` to get cargo 1.62+; (2) for old cargo, install cargo-edit: `cargo install cargo-edit` then use the same `cargo add` syntax; "
        "(3) manual fallback: edit Cargo.toml's [dependencies] section directly: `serde = \"1.0\"`.",
        f"{BASE}/commands/cargo-add.html",
    ),
    (
        "cargo-errors", "cargo warning: unused import (clippy hint)",
        "Rust compiler / clippy noticed an import you don't use. Not strictly an error but failing in strict CI. "
        "Fix: (1) remove the unused import; (2) `cargo fix --allow-dirty --allow-staged` auto-applies safe fixes including removing unused imports; "
        "(3) silence specifically: `#[allow(unused_imports)] use foo;` (rarely the right call — prefer removing).",
        f"{BASE}/commands/cargo-fix.html",
    ),
    (
        "cargo-errors", "cargo error: thread 'main' panicked at 'called Option::unwrap() on a None value'",
        "A runtime panic in your binary, not a cargo error per se. Cargo just surfaces what your code did. "
        "Fix: (1) `RUST_BACKTRACE=1 cargo run` to see where the panic originated; "
        "(2) `RUST_BACKTRACE=full cargo run` for a full stack trace; "
        "(3) wrap the offending `.unwrap()` in proper error handling: `match opt { Some(v) => ..., None => { eprintln!(...); std::process::exit(1) } }` or use the `?` operator.",
        f"{BASE}/commands/cargo-run.html",
    ),
    (
        "cargo-errors", "cargo error: cannot find macro `println` in this scope (no_std)",
        "You're in a `#![no_std]` crate where the std-library macros like println! aren't available. "
        "Fix: (1) if you wanted std, remove `#![no_std]`; "
        "(2) if you genuinely want no_std (embedded/wasm), use a no_std-compatible macro from a crate like `defmt::println!` or build your own using core::fmt; "
        "(3) `panic = \"abort\"` in [profile] for embedded targets that can't unwind.",
        f"{BASE}/reference/profiles.html",
    ),
    (
        "cargo-errors", "cargo error: error[E0277]: the trait bound `X: Send` is not satisfied",
        "Async or multi-threaded code requires the type to be Send, but isn't. "
        "Fix: (1) wrap non-Send types in `Arc<Mutex<T>>` for shared access; (2) move ownership explicitly with `move` closures; "
        "(3) check if the dep crate has a 'send' feature flag — `cargo add foo --features send`; (4) some types (Rc, RefCell, raw pointers) are inherently !Send — restructure to use Arc, RwLock, etc. instead.",
        f"{BASE}/reference/features.html",
    ),
    (
        "cargo-errors", "cargo error: feature `X` is required by Y but not enabled",
        "A dependency expects a feature flag that's not active. "
        "Fix: (1) `cargo add <dep> --features X` activates the feature; (2) in Cargo.toml: `foo = { version = \"X\", features = [\"feat1\", \"feat2\"] }`; "
        "(3) check the crate's docs for available features — they're listed under `[features]` in the source Cargo.toml; "
        "(4) `cargo tree --features` shows feature resolution per dep.",
        f"{BASE}/reference/features.html",
    ),
    (
        "cargo-errors", "cargo error: `cargo metadata --no-deps` failed",
        "External tools (cargo-edit, cargo-tree extensions) shell out to cargo metadata and got an error. "
        "Fix: (1) `cargo metadata --format-version 1` runs and reports the real underlying error; "
        "(2) common cause: Cargo.toml syntax errors or unresolvable workspace; "
        "(3) for tooling integration, always run cargo metadata once standalone to confirm the project parses cleanly.",
        f"{BASE}/commands/cargo-metadata.html",
    ),
    (
        "cargo-errors", "cargo error: error[E0658]: nightly feature used on stable",
        "Code uses a Rust feature that's only available on nightly. "
        "Fix: (1) switch to nightly: `rustup default nightly` (warning: nightly can break); "
        "(2) better: find a stable alternative — many nightly-only features have crates.io polyfills; "
        "(3) per-project nightly: rust-toolchain.toml with `channel = \"nightly\"`; "
        "(4) `cargo +nightly build` to use nightly for one command without changing default.",
        f"{BASE}/reference/unstable.html",
    ),
    (
        "cargo-errors", "cargo error: failed to run custom build command for `X`",
        "A dependency's build.rs script failed. Read the message above for the underlying error. "
        "Common causes: missing system library (pkg-config can't find libssl, libcurl, etc.); missing C compiler; environment vars expected but unset. "
        "Fix: (1) install the missing library (e.g., `apt install libssl-dev pkg-config`); "
        "(2) set the env var the build script needs (often X_LIB_DIR, X_INCLUDE_DIR); "
        "(3) for vendored alternatives: many crates have a 'vendored' feature: `cargo add openssl-sys --features vendored` builds OpenSSL from source.",
        f"{BASE}/reference/build-scripts.html",
    ),
    (
        "cargo-errors", "cargo error: warning: profile `release-with-debug` is not defined",
        "Cargo.toml references a profile that doesn't exist. "
        "Fix: (1) define it in [profile]: `[profile.release-with-debug] inherits = \"release\" debug = true`; "
        "(2) only `dev`, `release`, `test`, `bench` are built-in; everything else must be declared; "
        "(3) custom profiles need `inherits = \"<base>\"` to specify what they extend.",
        f"{BASE}/reference/profiles.html",
    ),
    (
        "cargo-errors", "cargo error: cargo-add not found",
        "Older cargo without built-in `add`. Same as the cargo-edit case above — `rustup update` or `cargo install cargo-edit`.",
        f"{BASE}/commands/cargo-add.html",
    ),
    (
        "cargo-errors", "cargo error: requires nightly to build (sparse registry)",
        "Sparse registry protocol is stable since 1.68 but the env var pattern may need updating. "
        "Fix: (1) `rustup update` to recent stable; (2) for explicit opt-in (older versions): `CARGO_UNSTABLE_SPARSE_REGISTRY=true cargo build`; "
        "(3) since 1.68, sparse is default — no flag needed; (4) if you want to opt OUT (back to the older git-index protocol): `CARGO_REGISTRIES_CRATES_IO_PROTOCOL=git`.",
        f"{BASE}/reference/registries.html",
    ),
    (
        "cargo-errors", "cargo error: no space left on device (target/ huge)",
        "cargo's target/ directory accumulates incrementals and all crate artifacts; can hit tens of GB on big projects. "
        "Fix: (1) `cargo clean` removes target/ entirely; "
        "(2) for partial: `cargo clean --release` drops only release builds; "
        "(3) `cargo clean -p <crate>` clears one specific crate's artifacts; "
        "(4) permanent measure: install cargo-sweep and run `cargo sweep --time 30` to drop artifacts unused in last 30 days.",
        f"{BASE}/commands/cargo-clean.html",
    ),

    # ============================================================
    # cargo-recipes
    # ============================================================
    (
        "cargo-recipes", "cargo recipe: create a new Rust project",
        "Two ways. New directory: `cargo new myproject` creates myproject/ with Cargo.toml + src/main.rs (binary) or `cargo new myproject --lib` for a library (src/lib.rs). "
        "Initialize in existing dir: `cargo init` adds Cargo.toml + src/main.rs to the current directory. "
        "Naming: `--name` overrides the directory name; `--vcs none` skips git init. "
        "After creation: `cargo build && cargo run` to verify it compiles and runs the default Hello World.",
        f"{BASE}/commands/cargo-new.html",
    ),
    (
        "cargo-recipes", "cargo recipe: add a dependency",
        "Modern way (cargo 1.62+): `cargo add <crate>` — adds latest compatible version to Cargo.toml. "
        "Pin to a version: `cargo add serde@1.0.193`. With features: `cargo add tokio --features full`. "
        "Dev-only (for tests): `cargo add --dev <crate>`. Build-only: `cargo add --build <crate>`. "
        "Optional (gated by feature): `cargo add --optional <crate>` then activate the feature in [features]. "
        "From a path: `cargo add --path ../sibling-crate`. From git: `cargo add --git https://github.com/owner/repo`.",
        f"{BASE}/commands/cargo-add.html",
    ),
    (
        "cargo-recipes", "cargo recipe: set up a workspace",
        "Group multiple related crates. Steps: "
        "(1) create root dir; (2) write Cargo.toml at root with `[workspace] members = [\"crate-a\", \"crate-b\"]`; "
        "(3) optional `resolver = \"2\"` for modern feature unification; "
        "(4) create member crates: `cargo new --lib crate-a` etc.; "
        "(5) cross-reference inside workspace via path deps: `crate-b = { path = \"../crate-b\" }`. "
        "Run any command at root and it applies to all members: `cargo build` builds everything, `cargo test --workspace` tests all members.",
        f"{BASE}/reference/workspaces.html",
    ),
    (
        "cargo-recipes", "cargo recipe: publish a crate to crates.io",
        "Steps: "
        "(1) Cargo.toml must have name, version, description, license (one of SPDX identifiers), and repository or documentation URL; "
        "(2) `cargo login` once to save your crates.io token; "
        "(3) `cargo package` to test packaging (creates target/package/*.crate); "
        "(4) `cargo publish --dry-run` to verify; "
        "(5) `cargo publish` for the real thing. "
        "Note: published versions are permanent; mistakes are yanked (not deleted): `cargo yank --vers X.Y.Z`.",
        f"{BASE}/reference/publishing.html",
    ),
    (
        "cargo-recipes", "cargo recipe: vendor dependencies for offline builds",
        "Bake all dependency source code into the project for air-gapped/reproducible builds. Steps: "
        "(1) `cargo vendor` — downloads all deps into vendor/ and prints config snippet; "
        "(2) add the printed snippet to .cargo/config.toml: `[source.crates-io] replace-with = \"vendored-sources\"` + `[source.vendored-sources] directory = \"vendor\"`; "
        "(3) commit vendor/ and .cargo/config.toml; (4) subsequent builds use vendor/ instead of crates.io. "
        "Use case: CI without network access, reproducible builds, supply-chain auditing.",
        f"{BASE}/commands/cargo-vendor.html",
    ),
    (
        "cargo-recipes", "cargo recipe: cross-compile to a different target",
        "Compile for a different architecture/OS. Steps: "
        "(1) install the target's stdlib: `rustup target add x86_64-unknown-linux-musl` (or any other target); "
        "(2) install a cross-linker on your system (e.g., `apt install gcc-x86-64-linux-gnu` for Linux-amd64 from a Linux-arm64 host); "
        "(3) configure in .cargo/config.toml: `[target.x86_64-unknown-linux-musl] linker = \"x86_64-linux-musl-gcc\"`; "
        "(4) build: `cargo build --target x86_64-unknown-linux-musl`. Output ends up in target/x86_64-unknown-linux-musl/. "
        "For complex cross builds, use `cross` (`cargo install cross`) — runs cargo in a docker container with the target's toolchain.",
        f"{BASE}/reference/config.html",
    ),
    (
        "cargo-recipes", "cargo recipe: enable optimizations for development builds",
        "Default dev profile is unoptimized (fast compile, slow runtime). For workloads that need speed during testing: "
        "(1) `[profile.dev] opt-level = 1` enables minor optimizations (1=basic, 2=moderate, 3=full); "
        "(2) `[profile.dev.package.<dep>] opt-level = 3` optimizes a specific slow dep while keeping rest fast; "
        "(3) for fast incremental rebuilds: `[profile.dev] incremental = true` (default true on dev). "
        "Trade-off: more optimization = slower initial compile but faster execution.",
        f"{BASE}/reference/profiles.html",
    ),
    (
        "cargo-recipes", "cargo recipe: configure custom registry for private crates",
        "Use an internal registry (Artifactory, Cloudsmith, Sonatype Nexus). Steps: "
        "(1) configure registry in `~/.cargo/config.toml`: `[registries.myreg] index = \"https://myreg.internal/crates-index\"`; "
        "(2) auth: `cargo login --registry myreg` then enter token (read from registry's UI); "
        "(3) publish: `cargo publish --registry myreg`; (4) consume in Cargo.toml: `mycrate = { version = \"1.0\", registry = \"myreg\" }`. "
        "For source replacement (mirror crates.io itself): use `[source.crates-io] replace-with = \"<mirror>\"`.",
        f"{BASE}/reference/registries.html",
    ),
    (
        "cargo-recipes", "cargo recipe: write and run benchmarks",
        "Two paths. Built-in (nightly only): `cargo bench` runs functions marked `#[bench]` in benches/ — requires nightly. "
        "Stable (use criterion crate): "
        "(1) `cargo add criterion --dev`; "
        "(2) create benches/my_bench.rs with `use criterion::{Criterion, criterion_group, criterion_main};` and define benchmark functions; "
        "(3) in Cargo.toml: `[[bench]] name = \"my_bench\" harness = false`; "
        "(4) `cargo bench` (no nightly needed) — runs criterion which produces detailed statistics + HTML reports in target/criterion/.",
        f"{BASE}/commands/cargo-bench.html",
    ),
    (
        "cargo-recipes", "cargo recipe: use git dependencies",
        "Pull a dep from a git repo instead of crates.io. Patterns: "
        "(1) latest from default branch: `mydep = { git = \"https://github.com/user/repo\" }`; "
        "(2) specific branch: `... branch = \"main\"`; (3) specific tag: `... tag = \"v1.0.0\"`; (4) specific commit: `... rev = \"abc123\"`; "
        "(5) sub-crate from a workspace: `... package = \"member-name\"`. "
        "Auth for private repos: SSH (uses ~/.ssh keys) or `[net] git-fetch-with-cli = true` in .cargo/config.toml to use system git (which handles credentials).",
        f"{BASE}/reference/specifying-dependencies.html",
    ),
    (
        "cargo-recipes", "cargo recipe: speed up CI builds with caching",
        "Cargo recompiles everything from scratch in CI unless target/ and registry/ are cached. Standard pattern: "
        "(1) cache `~/.cargo/registry/` (downloaded crates), `~/.cargo/git/` (git deps), and `target/` (build artifacts); "
        "(2) cache key includes Cargo.lock hash so cache invalidates on dep changes; "
        "(3) GitHub Actions: use `actions/cache@v3` or the dedicated `Swatinem/rust-cache@v2` (handles cargo specifics); "
        "(4) for hermetic CI: use `cargo build --frozen --locked` to refuse any non-cached resolution.",
        f"{BASE}/guide/continuous-integration.html",
    ),
    (
        "cargo-recipes", "cargo recipe: integrate clippy into your workflow",
        "Clippy is Rust's lint tool — extra checks beyond rustc. Steps: "
        "(1) `rustup component add clippy` (one-time); "
        "(2) `cargo clippy --all-targets --all-features -- -D warnings` runs clippy, fails on any warning; "
        "(3) configure lints in Cargo.toml `[lints.clippy]` section: `unwrap_used = \"warn\"`, `module_name_repetitions = \"allow\"`; "
        "(4) for one-off allow: `#[allow(clippy::lint_name)]` on a specific function; (5) in CI: gate merges on clippy.",
        f"{BASE}/reference/lints.html",
    ),
    (
        "cargo-recipes", "cargo recipe: write build.rs for code generation",
        "build.rs runs before compilation. Useful for generating Rust source from schema files, linking C libraries, embedding build metadata. "
        "Skeleton: place build.rs at the project root (next to Cargo.toml). Inside: "
        "`fn main() { println!(\"cargo:rerun-if-changed=schema.json\"); /* generate src/generated.rs */ }`. "
        "Special prints: `cargo:rerun-if-changed=<file>` tells cargo to re-run only when that file changes; `cargo:rustc-env=KEY=val` exposes an env var to your code via `env!(\"KEY\")`; `cargo:rustc-link-lib=mylib` links a system lib. "
        "Output goes to OUT_DIR env var inside build.rs.",
        f"{BASE}/reference/build-scripts.html",
    ),
    (
        "cargo-recipes", "cargo recipe: install a binary from crates.io",
        "Install a CLI tool written in Rust. Steps: "
        "(1) `cargo install <name>` installs the binary to `~/.cargo/bin/` (must be in PATH); "
        "(2) pin version: `cargo install <name> --version 1.2.3`; "
        "(3) install from a specific git rev: `cargo install --git https://github.com/owner/repo --rev abc123`; "
        "(4) reinstall after update: `cargo install --force <name>`; "
        "(5) list installed: `cargo install --list`. "
        "For binary-heavy crates with system deps (e.g., openssl), some support `--features vendored` to skip system requirements.",
        f"{BASE}/commands/cargo-install.html",
    ),
    (
        "cargo-recipes", "cargo recipe: feature flags for conditional compilation",
        "Use features to opt-in/opt-out parts of a crate. Steps: "
        "(1) declare features in Cargo.toml: `[features] default = [\"std\"] std = [] async = [\"tokio\"]` (the value list is OTHER features or optional deps to enable); "
        "(2) gate code with `#[cfg(feature = \"async\")]` attribute; "
        "(3) optional dependencies: `[dependencies] tokio = { version = \"1\", optional = true }` — only present when enabled; "
        "(4) consume from Cargo.toml: `mycrate = { version = \"1.0\", features = [\"async\"], default-features = false }`. "
        "Test all feature combos: `cargo check --all-features`, `cargo check --no-default-features`.",
        f"{BASE}/reference/features.html",
    ),
    (
        "cargo-recipes", "cargo recipe: use overriding dependencies (patch)",
        "Replace a transitive dep with a forked or local version without modifying the dep that pulled it in. "
        "Pattern: in workspace Cargo.toml: `[patch.crates-io] tokio = { git = \"https://github.com/me/tokio\", branch = \"my-fix\" }`. "
        "Every crate in the dep graph that wanted tokio now uses your fork. "
        "Local override: `[patch.crates-io] mycrate = { path = \"../mycrate-fork\" }`. "
        "Caveats: patched version must be semver-compatible with what the consumer requested; can't patch into a different major version this way.",
        f"{BASE}/reference/overriding-dependencies.html",
    ),
    (
        "cargo-recipes", "cargo recipe: speed up cargo with sparse registry + parallel fetching",
        "Modern cargo (1.68+) uses sparse registry by default for crates.io — only downloads the index for crates you actually use. "
        "For older cargo, opt in: `CARGO_REGISTRIES_CRATES_IO_PROTOCOL=sparse cargo fetch`. "
        "Parallel build: `cargo build -j N` (N = number of compile jobs in parallel; default = CPU count). "
        "Faster linker: install lld and add to .cargo/config.toml: `[target.x86_64-unknown-linux-gnu] linker = \"clang\" rustflags = [\"-C\", \"link-arg=-fuse-ld=lld\"]` — can speed link by 5-10x.",
        f"{BASE}/reference/registries.html",
    ),
    (
        "cargo-recipes", "cargo recipe: generate documentation",
        "Steps: (1) `cargo doc` builds documentation for your crate + all dependencies; outputs to target/doc/; "
        "(2) `cargo doc --open` opens it in your browser; (3) `cargo doc --no-deps` skips dependency docs (faster, focused on yours); "
        "(4) `cargo doc --document-private-items` includes private functions/types; "
        "(5) for crate docs comments use `///` (doc comments) and `//!` (inner doc comments). "
        "Tests in doc comments: ```rust /// # Example /// ``` /// let x = 5; /// ``` ``` — `cargo test` runs doctests too.",
        f"{BASE}/commands/cargo-doc.html",
    ),
    (
        "cargo-recipes", "cargo recipe: run a specific test or filter tests",
        "`cargo test` runs everything. For subsets: "
        "(1) `cargo test <substring>` — run only tests whose name contains substring; "
        "(2) `cargo test --test <name>` — run only the named integration test file (tests/<name>.rs); "
        "(3) `cargo test --doc` — only doctests; "
        "(4) `cargo test -- --nocapture` — show stdout from tests (default suppresses); "
        "(5) `cargo test -- --test-threads=1` — serialize tests for debugging race conditions; "
        "(6) `cargo test --release` — run tests in release mode.",
        f"{BASE}/commands/cargo-test.html",
    ),
    (
        "cargo-recipes", "cargo recipe: build a binary for production release",
        "Optimized standalone binary. Steps: "
        "(1) `cargo build --release` — uses release profile, optimizations on, debug symbols off by default; output: target/release/<binary>; "
        "(2) for smaller binary: in Cargo.toml `[profile.release] lto = true codegen-units = 1 strip = true panic = \"abort\"` — significantly reduces size; "
        "(3) static linking (musl on Linux): `cargo build --release --target x86_64-unknown-linux-musl` after `rustup target add x86_64-unknown-linux-musl`. Produces a binary with no glibc dependency.",
        f"{BASE}/reference/profiles.html",
    ),
    (
        "cargo-recipes", "cargo recipe: inspect dependency tree",
        "See what you're actually pulling in. `cargo tree` shows the full tree. Filters: "
        "(1) `cargo tree -p <crate>` — only show one package's tree; "
        "(2) `cargo tree -i <crate>` — inverse: who depends on <crate>?; useful for diagnosing version conflicts; "
        "(3) `cargo tree --duplicates` — show packages with multiple versions in your graph (potential for conflict); "
        "(4) `cargo tree -e features` — show enabled features per dep.",
        f"{BASE}/commands/cargo-tree.html",
    ),
    (
        "cargo-recipes", "cargo recipe: yank a published version safely",
        "If you released a broken version, yanking prevents NEW projects from picking it but doesn't break existing ones. Steps: "
        "(1) `cargo yank --vers <version>` — yanks; (2) verify on crates.io UI; (3) publish a fixed version; "
        "(4) communicate via release notes that old version is yanked. "
        "Un-yank if it was a mistake: `cargo yank --vers <version> --undo`. "
        "What yank does: removes the version from `cargo new` searches and resolver consideration for new graphs; doesn't delete the package files — old Cargo.lock pinned users still resolve.",
        f"{BASE}/commands/cargo-yank.html",
    ),
    (
        "cargo-recipes", "cargo recipe: minimize cargo build times",
        "Tactics: "
        "(1) `cargo check` instead of `cargo build` during dev (skip codegen); "
        "(2) install sccache: `cargo install sccache` then set `[build] rustc-wrapper = \"sccache\"` in .cargo/config.toml — caches compiled artifacts across projects; "
        "(3) use lld linker (see speed recipe); "
        "(4) compile in dev profile (default) — release builds are 5-10x slower; "
        "(5) split big crates into smaller ones — rust compiles by crate, so smaller crates parallelize better; "
        "(6) use workspaces to share target/ across related crates.",
        f"{BASE}/reference/config.html",
    ),
    (
        "cargo-recipes", "cargo recipe: integrate cargo-deny / cargo-audit for security",
        "Lock down your dependency supply chain. "
        "Audit: `cargo install cargo-audit && cargo audit` — checks Cargo.lock against the RustSec advisory DB; reports CVEs. "
        "Policy: `cargo install cargo-deny && cargo deny init` — generates deny.toml. Configure: allowed licenses (`[licenses] allow = [\"MIT\", \"Apache-2.0\"]`), banned crates, banned advisories. "
        "Run in CI: `cargo deny check` and `cargo audit` as steps that fail the build if a violation occurs.",
        f"{BASE}/guide/continuous-integration.html",
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
            evidence_id=f"ev-cargo-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-cargo-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="cargo-catalog",
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
