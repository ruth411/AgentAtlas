"""Direct-insert rust error + recipe claims."""

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

BOOK = "https://doc.rust-lang.org/book"
STD = "https://doc.rust-lang.org/std"
REF = "https://doc.rust-lang.org/reference"
ERR = "https://doc.rust-lang.org/error_codes"

CLAIMS = [
    # ============================================================
    # rust-errors — 30 common compiler / runtime errors
    # ============================================================
    (
        "rust-errors", "rust error: E0382 — use of moved value / borrow of moved value",
        "You used a value after it was moved (consumed). Rust's ownership rules: assigning or passing a non-`Copy` value moves it. "
        "Fix: (1) clone if you need both: `let b = a.clone(); use(a); use(b);` (cost: deep copy); "
        "(2) borrow instead of move: pass `&a` (read) or `&mut a` (write) — borrowing doesn't consume; "
        "(3) make the type `Copy` (only for small primitives + structs of Copy fields): `#[derive(Copy, Clone)] struct Point { ... }`; "
        "(4) restructure: if you only need one of the two uses, drop the other.",
        f"{BOOK}/ch04-00-understanding-ownership.html",
    ),
    (
        "rust-errors", "rust error: E0502 — cannot borrow as mutable because also borrowed as immutable",
        "Active immutable borrow exists while you try to take a mutable borrow. Rust's borrow checker prevents data races at compile time. "
        "Fix: (1) shorten the immutable borrow's scope: `{ let r = &v; use(r); } // r dropped here. let m = &mut v;`; "
        "(2) collect indices first, mutate after: `let bad_idx: Vec<usize> = v.iter().enumerate().filter(...).map(|(i,_)|i).collect(); for i in bad_idx { v[i] = ...; }`; "
        "(3) use interior mutability: `RefCell<T>` (single-threaded) or `Mutex<T>` (threads) — moves the check to runtime; "
        "(4) for splitting: `v.split_at_mut(i)` returns two non-overlapping mutable slices.",
        f"{BOOK}/ch04-00-understanding-ownership.html",
    ),
    (
        "rust-errors", "rust error: E0507 — cannot move out of a reference",
        "Tried to take ownership of something behind a borrow (e.g. `*v` to consume a `&Vec<T>`). References don't grant ownership. "
        "Fix: (1) clone: `(*v).clone()` (cost: deep copy); "
        "(2) borrow instead: do you really need to consume, or can you work with `&T`?; "
        "(3) `std::mem::replace(target, default)` swaps in a default and returns the old value (when you have `&mut`); "
        "(4) for `Option`: `Option::take()` swaps in `None` and returns the old `Some`.",
        f"{BOOK}/ch04-00-understanding-ownership.html",
    ),
    (
        "rust-errors", "rust error: E0506 — cannot assign to X because it is borrowed",
        "You held a reference to a variable then tried to reassign or mutate the variable through a different path. "
        "Fix: (1) drop the reference before reassigning: end its scope with `{}` blocks or just let it lexically go out of scope; "
        "(2) `drop(r);` explicitly destroys a binding; "
        "(3) re-design the data flow: store the new value somewhere else, swap at the end; "
        "(4) for collections: use indexing instead of iterators when you need to mutate during iteration.",
        f"{BOOK}/ch04-00-understanding-ownership.html",
    ),
    (
        "rust-errors", "rust error: E0277 — trait bound `X: Y` is not satisfied",
        "You used a generic function or type but provided a concrete type that doesn't implement the required trait. "
        "Fix: (1) implement the trait: `impl Y for X { ... }`; "
        "(2) derive if possible: `#[derive(Debug, Clone, PartialEq, Eq, Hash)]`; "
        "(3) for foreign types you don't own: wrap in a newtype struct and implement on the newtype; "
        "(4) check the trait's docs for which other traits to derive (Hash requires Eq, Eq requires PartialEq).",
        f"{BOOK}/ch10-02-traits.html",
    ),
    (
        "rust-errors", "rust error: E0308 — mismatched types",
        "Expression's type doesn't match what's expected. "
        "Fix: (1) read the error — Rust prints expected vs found; "
        "(2) common: `Result<T, E>` where `T` expected — use `?` to propagate or `.unwrap()` (panics on Err) or `.expect(\"msg\")`; "
        "(3) for numeric: `as` cast: `let x: u32 = y as u32;` (truncates); "
        "(4) for `&str` vs `String`: `.to_string()` or `String::from(s)` to convert &str to String; `&my_string` for the reverse; "
        "(5) for `Vec<T>` vs `&[T]`: `&vec[..]` or just `&vec` (auto-deref).",
        f"{BOOK}/ch03-02-data-types.html",
    ),
    (
        "rust-errors", "rust error: E0425 — cannot find value `X` in this scope",
        "Variable or function name not visible. "
        "Fix: (1) typo check; "
        "(2) missing `use`: import the path: `use std::collections::HashMap;`; "
        "(3) wrong scope: variable defined in inner block but used outside; "
        "(4) function defined in another module: `mod::function()` or import; "
        "(5) hint: rustc usually suggests the right path: `did you mean `std::cmp::min`?`.",
        f"{BOOK}/ch07-00-managing-growing-projects-with-packages-crates-and-modules.html",
    ),
    (
        "rust-errors", "rust error: E0432 — unresolved import",
        "Can't find what you're importing. "
        "Fix: (1) verify the crate is in Cargo.toml dependencies; "
        "(2) `cargo build` once to fetch + verify dep tree; "
        "(3) check the path: `use crate::module::Item;` (absolute) vs `use super::Item;` (parent) vs `use self::Item;` (current); "
        "(4) for external crates: `use crate_name::Item;` (no `crate::` prefix); "
        "(5) for feature-gated items: enable the feature in Cargo.toml — `crate = { version = \"...\", features = [\"async\"] }`.",
        f"{BOOK}/ch07-04-bringing-paths-into-scope-with-the-use-keyword.html",
    ),
    (
        "rust-errors", "rust error: E0061 — function takes N arguments but M were supplied",
        "Wrong number of args. "
        "Fix: (1) check function signature — IDE / `cargo doc --open`; "
        "(2) check for `self` consumption: methods on `&self`, `&mut self`, `self`; "
        "(3) for trait methods: arg count depends on the trait's signature; "
        "(4) for variadics (like `println!`): those are macros, not functions — different rules.",
        f"{BOOK}/ch03-03-how-functions-work.html",
    ),
    (
        "rust-errors", "rust error: E0515 — cannot return value referencing local variable",
        "Returning a reference to data that goes out of scope when the function returns. Dangling reference. "
        "Fix: (1) return the owned value: drop the `&` from the return type; "
        "(2) take a reference parameter and return a reference with matching lifetime: `fn longest<'a>(x: &'a str, y: &'a str) -> &'a str`; "
        "(3) for lazy iteration: return an iterator that borrows from input + uses lifetime annotations; "
        "(4) use `String::from(...)` to allocate + return.",
        f"{BOOK}/ch10-03-lifetime-syntax.html",
    ),
    (
        "rust-errors", "rust error: lifetime mismatch / does not live long enough",
        "Compiler can't prove a reference is valid for the required lifetime. "
        "Fix: (1) read the error — it shows where the borrow started + when the borrowed thing dropped; "
        "(2) add lifetime annotations to functions: `fn f<'a>(x: &'a Foo) -> &'a Bar`; "
        "(3) extend the borrowed value's scope (move declaration earlier); "
        "(4) for self-referential structs: use `ouroboros` or `Pin` (rare and advanced); "
        "(5) for cases where you genuinely need it: clone instead of borrow.",
        f"{BOOK}/ch10-03-lifetime-syntax.html",
    ),
    (
        "rust-errors", "rust error: E0282 — type annotations needed",
        "Compiler can't infer the type of a binding (often `collect()` without target type, or generic function with ambiguous return). "
        "Fix: (1) annotate the binding: `let v: Vec<i32> = (0..10).collect();`; "
        "(2) turbofish: `let v = (0..10).collect::<Vec<i32>>();`; "
        "(3) annotate the function return type if generic; "
        "(4) common with `.parse()`: `let n: u32 = \"42\".parse().unwrap();` or `\"42\".parse::<u32>().unwrap();`.",
        f"{REF}/types.html",
    ),
    (
        "rust-errors", "rust error: E0599 — no method named `X` found for type `Y`",
        "Method doesn't exist on the type (or trait isn't imported). "
        "Fix: (1) typo check; "
        "(2) check docs: `cargo doc --open` or rust-lang.org/std; "
        "(3) trait method: import the trait: `use std::io::Write;` to use `.write_all()` on a `File`; "
        "(4) for iterators: `use std::iter::*;` brings adapter methods into scope; "
        "(5) check generic constraints — `Vec<T>::sort()` needs `T: Ord`.",
        f"{BOOK}/ch10-02-traits.html",
    ),
    (
        "rust-errors", "rust panic: thread 'main' panicked at 'index out of bounds'",
        "Indexed a slice/Vec past its end. "
        "Fix: (1) check bounds before: `if i < v.len() { v[i] } else { default }`; "
        "(2) use `.get(i)` which returns `Option<&T>`: `v.get(i).copied().unwrap_or(0)`; "
        "(3) for iteration: prefer `for x in &v` over indexed loops; "
        "(4) for slice ranges: `v.get(start..end)` returns `Option<&[T]>`; "
        "(5) RUST_BACKTRACE=1 cargo run for full trace.",
        f"{STD}/vec/struct.Vec.html",
    ),
    (
        "rust-errors", "rust panic: called `Option::unwrap()` on a `None` value",
        "`.unwrap()` on `None`. The classic Rust panic. "
        "Fix: (1) handle the None case: `match opt { Some(x) => ..., None => default }`; "
        "(2) provide a default: `opt.unwrap_or(default)` or `opt.unwrap_or_else(|| compute_default())`; "
        "(3) use `?` to propagate (if in a function returning `Option<U>`): `let x = opt?;`; "
        "(4) for `expect()` with message: `opt.expect(\"value was loaded from config\")` — still panics but explains why; "
        "(5) for development debugging: panic shows file:line — fix the source.",
        f"{STD}/option/enum.Option.html",
    ),
    (
        "rust-errors", "rust panic: called `Result::unwrap()` on an `Err` value",
        "Same as above but for `Result`. "
        "Fix: (1) propagate with `?` in functions returning `Result`: `let x = some_result?;`; "
        "(2) match: `match r { Ok(x) => ..., Err(e) => handle_error(e) }`; "
        "(3) `r.unwrap_or(default)` for simple recovery; "
        "(4) `r.map_err(|e| MyError::from(e))?` to convert error types; "
        "(5) prefer `expect()` over `unwrap()` for production — the message is logged in the panic.",
        f"{STD}/result/enum.Result.html",
    ),
    (
        "rust-errors", "rust panic: attempt to subtract with overflow",
        "Unsigned arithmetic underflowed (e.g. `0u32 - 1`). In debug builds Rust panics; in release builds it wraps. "
        "Fix: (1) use signed types if negatives are expected: `i32` instead of `u32`; "
        "(2) `checked_sub`: `let n = a.checked_sub(b).unwrap_or(0);` returns `Option<T>`; "
        "(3) `saturating_sub`: clamps to 0/min: `a.saturating_sub(b)`; "
        "(4) `wrapping_sub`: explicit wrap; "
        "(5) for the difference of size + offset: validate first: `if a >= b { a - b } else { 0 }`.",
        f"{REF}/expressions/operator-expr.html",
    ),
    (
        "rust-errors", "rust panic: attempt to divide by zero",
        "Integer division by zero panics (in any build). Float division produces inf/NaN. "
        "Fix: (1) check denominator: `if b != 0 { a / b } else { 0 }`; "
        "(2) `checked_div`: `a.checked_div(b).unwrap_or(0);`; "
        "(3) for float: works fine, produces `inf`/`-inf`/`NaN` — handle `is_nan()` / `is_infinite()` downstream.",
        f"{REF}/expressions/operator-expr.html",
    ),
    (
        "rust-errors", "rust error: closure does not implement Fn / FnMut / FnOnce",
        "Closure captures by move/mut/ref doesn't match the expected trait. "
        "Hierarchy: `Fn: FnMut: FnOnce`. `Fn` is most restrictive (read-only env), `FnOnce` consumes the env. "
        "Fix: (1) for closures called multiple times: must be `Fn` (or `FnMut`); avoid moving captured variables into the closure body; "
        "(2) `move ||` keyword to move captures (use for `'static` closures going to threads); "
        "(3) for callbacks: `Box<dyn Fn()>` (heap) or `impl Fn()` (compile-time monomorphized) in signatures; "
        "(4) clone before capturing if you need both: `let s2 = s.clone(); move || println!(\"{}\", s2);`.",
        f"{BOOK}/ch13-01-closures.html",
    ),
    (
        "rust-errors", "rust error: `<T>` cannot be sent between threads safely (Send not implemented)",
        "`Rc<T>` (single-threaded) was passed to a thread — need `Arc<T>`. "
        "Fix: (1) replace `Rc<T>` with `Arc<T>` for cross-thread sharing; "
        "(2) `RefCell<T>` (interior mutability) is not Send — use `Mutex<T>` or `RwLock<T>`; "
        "(3) raw pointers and some non-Sync types: wrap with `Mutex` or pass as `Arc<Mutex<T>>`; "
        "(4) for closures: `move ||` captures by ownership — captured types must be Send + 'static for `thread::spawn`.",
        f"{STD}/sync/struct.Arc.html",
    ),
    (
        "rust-errors", "rust error: borrowed value does not live long enough (E0597)",
        "Reference outlives the value it points to. "
        "Fix: (1) move the value declaration earlier so it lives at least as long as its reference; "
        "(2) for returning references from functions: see E0515 fix; "
        "(3) for struct holding a reference: add a lifetime: `struct Holder<'a> { r: &'a T }`; "
        "(4) common with temporaries: `let owned = expr; let r = &owned;` instead of `let r = &expr;` for some `expr`.",
        f"{BOOK}/ch10-03-lifetime-syntax.html",
    ),
    (
        "rust-errors", "rust linker error: ld returned 1 exit status / undefined symbol",
        "Linker can't find a symbol. "
        "Fix: (1) for missing C lib: install dev headers — Linux `apt install libpq-dev`, macOS `brew install postgresql`; "
        "(2) check Cargo.toml for `[build-dependencies]` and `build.rs` if you have native deps; "
        "(3) for cross-compile: install target's stdlib: `rustup target add x86_64-unknown-linux-musl`; "
        "(4) Apple Silicon mismatch: check `cargo --version` arch matches your target; "
        "(5) for `+nightly` features: link errors can mean the dep needs nightly.",
        f"{BOOK}/ch01-01-installation.html",
    ),
    (
        "rust-errors", "rust panic: thread 'main' has overflowed its stack",
        "Stack overflow — usually deep recursion or huge stack-allocated data. "
        "Fix: (1) convert recursion to iteration; "
        "(2) box large stack values: `Box<[u8; 1_000_000]>` instead of `[u8; 1_000_000]`; "
        "(3) raise stack size for the main thread: `std::thread::Builder::new().stack_size(8 * 1024 * 1024).spawn(...)`; "
        "(4) for parsers: use a state machine or pratt parser instead of recursive descent on deep trees.",
        f"{BOOK}/ch16-00-concurrency.html",
    ),
    (
        "rust-errors", "rust error: aborting due to N previous errors",
        "Compilation gave up after N errors. Read top-down — the first error is usually the root cause; subsequent errors are often cascading. "
        "Fix: (1) fix the first error first, then recompile; "
        "(2) for huge errors: `cargo build 2>&1 | head -50`; "
        "(3) for incremental learning: tackle one file at a time; "
        "(4) `cargo check` is faster (skips codegen) — use during iteration.",
        f"{BOOK}/ch01-03-hello-cargo.html",
    ),
    (
        "rust-errors", "rust warning: unused import / dead_code",
        "Items defined or imported but not used. Default lint level is `warn`. "
        "Fix: (1) remove the unused import / item; "
        "(2) intentional but unused (e.g. testing-only): prefix with `_` or `#[allow(dead_code)]`; "
        "(3) silence whole module: `#![allow(dead_code, unused_imports)]` at module top (not recommended long-term); "
        "(4) `cargo fix` auto-removes unused imports.",
        f"{REF}/attributes/diagnostics.html",
    ),
    (
        "rust-errors", "rust error: cannot move out of borrowed content (E0507 variant)",
        "Trying to consume something through a reference. Same root as E0507 but specific to pattern matching. "
        "Fix: (1) match by reference: `match &my_thing { Some(x) => ... }` so `x` is a reference, not moved; "
        "(2) clone in arm: `Some(x) => x.clone()`; "
        "(3) for `Vec`: index access returns reference; `into_iter()` consumes the Vec.",
        f"{BOOK}/ch06-02-match.html",
    ),
    (
        "rust-errors", "rust error: cannot infer an appropriate lifetime (E0623)",
        "Multiple lifetimes involved and compiler can't pick one. "
        "Fix: (1) annotate explicitly: `fn f<'a, 'b>(x: &'a T, y: &'b U) -> &'a R`; "
        "(2) for HRTB (higher-ranked trait bounds): `for<'a> Fn(&'a T) -> &'a U`; "
        "(3) often: shorter lifetimes are okay — replace `'static` with explicit `'a`; "
        "(4) tool: `cargo expand` shows what lifetimes the compiler is inferring.",
        f"{BOOK}/ch10-03-lifetime-syntax.html",
    ),
    (
        "rust-errors", "rust error: async function cannot capture / non-Send future",
        "Async function's future captures something not-`Send`, but caller needs `Send` for `tokio::spawn`. "
        "Fix: (1) ensure all captured types are `Send`: use `Arc` not `Rc`, `Mutex` not `RefCell`; "
        "(2) for local-only async: `tokio::task::spawn_local` doesn't require Send (single-thread runtime); "
        "(3) avoid holding `MutexGuard` across `await` points (the guard is not Send) — drop before await; "
        "(4) `Box::pin(async move { ... })` for trait objects that aren't auto-Send.",
        f"{BOOK}/ch20-00-async.html",
    ),
    (
        "rust-errors", "rust error: integer overflow in debug build (panic) vs release (wraps)",
        "Default `+`/`-`/`*` on integers: debug panics on overflow, release silently wraps. Common surprise. "
        "Fix: (1) for explicit wrap: `wrapping_add` / `wrapping_sub` (intent-revealing); "
        "(2) for saturation: `saturating_add` clamps at max; "
        "(3) for checked: `checked_add` returns `Option<T>`; "
        "(4) for never-overflow asserts: enable overflow-checks in release too: `Cargo.toml` `[profile.release] overflow-checks = true` (slower); "
        "(5) for very wide arithmetic: `u128` or the `num` crate.",
        f"{REF}/expressions/operator-expr.html",
    ),
    (
        "rust-errors", "rust error: cannot find macro `X` in this scope",
        "Macro not imported (or crate not in dependencies). "
        "Fix: (1) for stdlib macros (println!, vec!, format!): already in prelude — typo check; "
        "(2) for crate macros (lazy_static, anyhow): `use crate_name::macro_name;` (2018+ edition); "
        "(3) old style: `#[macro_use] extern crate crate_name;` at crate root; "
        "(4) for `derive` macros: `use serde::Serialize;` (the trait + macro share a name).",
        f"{BOOK}/ch19-06-macros.html",
    ),

    # ============================================================
    # rust-recipes — 36 idiomatic patterns
    # ============================================================
    (
        "rust-recipes", "rust recipe: install + verify rustup",
        "`curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`. "
        "Adds `~/.cargo/bin` to PATH — restart shell or `source $HOME/.cargo/env`. "
        "Verify: `rustc --version` + `cargo --version`. "
        "Pin per-project: create `rust-toolchain.toml`: ```toml\\n[toolchain]\\nchannel = \"1.78.0\"\\ncomponents = [\"rustfmt\", \"clippy\"]\\n```. "
        "Update: `rustup update`. Switch channel: `rustup default nightly`.",
        f"https://rust-lang.github.io/rustup/",
    ),
    (
        "rust-recipes", "rust recipe: Option<T> pattern handling",
        "```rust\\n// Default value\\nlet n = maybe_n.unwrap_or(0);\\nlet n = maybe_n.unwrap_or_else(|| expensive_default());\\n\\n// Transform inside\\nlet doubled = maybe_n.map(|n| n * 2);\\nlet stringified = maybe_n.map(|n| n.to_string());\\n\\n// Propagate (in function returning Option<U>)\\nlet n = maybe_n?;\\n\\n// Pattern match\\nmatch maybe_n {\\n    Some(n) if n > 0 => positive(n),\\n    Some(n) => zero_or_negative(n),\\n    None => default(),\\n}\\n```",
        f"{STD}/option/enum.Option.html",
    ),
    (
        "rust-recipes", "rust recipe: Result<T, E> with ? operator",
        "```rust\\nuse std::fs;\\nfn read_config() -> Result<Config, Box<dyn std::error::Error>> {\\n    let raw = fs::read_to_string(\"config.toml\")?;  // io::Error → boxed\\n    let cfg: Config = toml::from_str(&raw)?;       // toml::de::Error → boxed\\n    Ok(cfg)\\n}\\n```. "
        "`?` early-returns on Err. Works in any function returning `Result<_, E>` where the inner error can `From`-convert into `E`. "
        "For ergonomic errors: `anyhow::Result<T>` (apps) or `thiserror` (libraries).",
        f"{BOOK}/ch09-02-recoverable-errors-with-result.html",
    ),
    (
        "rust-recipes", "rust recipe: read + write a file",
        "Read: `let contents = std::fs::read_to_string(\"file.txt\")?;` (whole file as String). "
        "Read bytes: `let bytes = std::fs::read(\"file.bin\")?;`. "
        "Write: `std::fs::write(\"out.txt\", \"hello\")?;` (overwrites). "
        "Append: ```rust\\nuse std::io::Write;\\nlet mut f = std::fs::OpenOptions::new().append(true).open(\"out.txt\")?;\\nwriteln!(f, \"line\")?;\\n```. "
        "For streaming line-by-line: `BufReader::new(f).lines()`.",
        f"{STD}/fs/index.html",
    ),
    (
        "rust-recipes", "rust recipe: parse command-line arguments",
        "Stdlib quick: `let args: Vec<String> = std::env::args().collect();`. "
        "For real CLIs: use `clap` (de facto). "
        "```rust\\nuse clap::Parser;\\n#[derive(Parser)]\\nstruct Args {\\n    #[arg(short, long)]\\n    name: String,\\n    #[arg(short, long, default_value_t = 1)]\\n    count: u32,\\n}\\nfn main() {\\n    let args = Args::parse();\\n    for _ in 0..args.count {\\n        println!(\"Hello {}\", args.name);\\n    }\\n}\\n```",
        f"{STD}/env/fn.args.html",
    ),
    (
        "rust-recipes", "rust recipe: iterate + collect to Vec / HashMap",
        "```rust\\n// Vec\\nlet doubled: Vec<i32> = (1..=5).map(|n| n * 2).collect();\\nlet evens: Vec<i32> = (1..=10).filter(|n| n % 2 == 0).collect();\\n\\n// HashMap\\nuse std::collections::HashMap;\\nlet map: HashMap<&str, i32> = vec![(\"a\", 1), (\"b\", 2)].into_iter().collect();\\n\\n// Sum / product / count\\nlet sum: i32 = (1..=100).sum();\\nlet count = (1..100).filter(|n| n % 7 == 0).count();\\n```",
        f"{STD}/iter/trait.Iterator.html",
    ),
    (
        "rust-recipes", "rust recipe: define struct + impl methods",
        "```rust\\n#[derive(Debug, Clone, PartialEq)]\\nstruct User {\\n    name: String,\\n    age: u32,\\n}\\n\\nimpl User {\\n    fn new(name: impl Into<String>, age: u32) -> Self {\\n        Self { name: name.into(), age }\\n    }\\n    fn greet(&self) -> String {\\n        format!(\"Hello, {}!\", self.name)\\n    }\\n    fn birthday(&mut self) {\\n        self.age += 1;\\n    }\\n}\\n\\nlet mut u = User::new(\"Alice\", 30);\\nu.birthday();\\n```",
        f"{BOOK}/ch05-00-structs.html",
    ),
    (
        "rust-recipes", "rust recipe: implement a trait",
        "```rust\\ntrait Greet {\\n    fn hello(&self) -> String;\\n    fn shout(&self) -> String {\\n        self.hello().to_uppercase()  // default method\\n    }\\n}\\n\\nimpl Greet for User {\\n    fn hello(&self) -> String {\\n        format!(\"Hi, I'm {}\", self.name)\\n    }\\n}\\n\\nfn welcome(g: &impl Greet) {\\n    println!(\"{}\", g.hello());\\n}\\n```. "
        "Use `dyn Greet` for trait objects (heap-allocated, dynamic dispatch).",
        f"{BOOK}/ch10-02-traits.html",
    ),
    (
        "rust-recipes", "rust recipe: generic function with trait bounds",
        "```rust\\nuse std::fmt::Display;\\n\\nfn print_all<T: Display>(items: &[T]) {\\n    for item in items {\\n        println!(\"{}\", item);\\n    }\\n}\\n\\n// `impl Trait` syntax (preferred for single bound)\\nfn print_all_v2(items: &[impl Display]) { ... }\\n\\n// Multiple bounds\\nfn process<T: Display + Clone>(x: T) -> T { x.clone() }\\n\\n// Where clauses (cleaner for complex bounds)\\nfn complex<T, U>(x: T, y: U)\\nwhere T: Display + Clone, U: IntoIterator { ... }\\n```",
        f"{BOOK}/ch10-00-generics.html",
    ),
    (
        "rust-recipes", "rust recipe: derive standard traits",
        "```rust\\n#[derive(Debug, Clone, PartialEq, Eq, Hash, Default)]\\nstruct Config {\\n    name: String,\\n    count: u32,\\n}\\n```. "
        "`Debug`: enables `{:?}` formatting. `Clone`: `.clone()` method. `PartialEq` / `Eq`: equality. `Hash`: HashMap/HashSet key. `Default`: `Config::default()`. "
        "For ordered: also `PartialOrd, Ord`. "
        "`Copy` for tiny POD types (all fields Copy): `#[derive(Copy, Clone)]`.",
        f"{REF}/attributes/derive.html",
    ),
    (
        "rust-recipes", "rust recipe: Box for heap allocation / recursive types",
        "```rust\\n// Recursive type — needs indirection\\nenum List {\\n    Cons(i32, Box<List>),\\n    Nil,\\n}\\nlet list = List::Cons(1, Box::new(List::Cons(2, Box::new(List::Nil))));\\n\\n// Trait object\\nlet shapes: Vec<Box<dyn Shape>> = vec![Box::new(Circle{...}), Box::new(Square{...})];\\n\\n// Force heap allocation for huge value\\nlet big_array: Box<[u8; 1_000_000]> = Box::new([0; 1_000_000]);\\n```",
        f"{STD}/boxed/struct.Box.html",
    ),
    (
        "rust-recipes", "rust recipe: Rc for shared ownership (single-threaded)",
        "```rust\\nuse std::rc::Rc;\\nlet a = Rc::new(vec![1, 2, 3]);\\nlet b = Rc::clone(&a);  // increments refcount (cheap)\\n// Both a and b own the Vec; dropped when last clone goes out of scope\\nprintln!(\"refs: {}\", Rc::strong_count(&a));  // 2\\n```. "
        "For mutation through Rc: combine with `RefCell`: `Rc<RefCell<T>>`. "
        "For multi-threaded: use `Arc` instead.",
        f"{STD}/rc/struct.Rc.html",
    ),
    (
        "rust-recipes", "rust recipe: Arc + Mutex for shared mutable state across threads",
        "```rust\\nuse std::sync::{Arc, Mutex};\\nuse std::thread;\\n\\nlet counter = Arc::new(Mutex::new(0));\\nlet mut handles = vec![];\\nfor _ in 0..10 {\\n    let c = Arc::clone(&counter);\\n    handles.push(thread::spawn(move || {\\n        let mut n = c.lock().unwrap();\\n        *n += 1;\\n    }));\\n}\\nfor h in handles { h.join().unwrap(); }\\nprintln!(\"Final: {}\", *counter.lock().unwrap());  // 10\\n```",
        f"{STD}/sync/struct.Mutex.html",
    ),
    (
        "rust-recipes", "rust recipe: RefCell for interior mutability",
        "When you have a `&T` (immutable reference) but need to mutate `T`. Borrow check moves to runtime. "
        "```rust\\nuse std::cell::RefCell;\\nlet x = RefCell::new(5);\\n*x.borrow_mut() += 1;\\nprintln!(\"{}\", *x.borrow());  // 6\\n```. "
        "Caveat: panics on overlapping borrows (use `try_borrow` to handle gracefully). "
        "Not Sync (single-threaded only) — use `Mutex` for threads.",
        f"{STD}/cell/struct.RefCell.html",
    ),
    (
        "rust-recipes", "rust recipe: spawn threads + return values",
        "```rust\\nuse std::thread;\\n\\nlet handle = thread::spawn(|| {\\n    // do work\\n    expensive_compute()\\n});\\nlet result = handle.join().unwrap();  // wait + retrieve\\n\\n// With captures (use move)\\nlet data = vec![1, 2, 3];\\nlet h = thread::spawn(move || {\\n    data.iter().sum::<i32>()\\n});\\nprintln!(\"sum: {}\", h.join().unwrap());\\n```. "
        "For thread pools: use `rayon` (data-parallel) or `tokio` (async).",
        f"{STD}/thread/index.html",
    ),
    (
        "rust-recipes", "rust recipe: channels (mpsc) for thread communication",
        "```rust\\nuse std::sync::mpsc;\\nuse std::thread;\\n\\nlet (tx, rx) = mpsc::channel();\\nfor i in 0..3 {\\n    let tx = tx.clone();\\n    thread::spawn(move || {\\n        tx.send(i * 10).unwrap();\\n    });\\n}\\ndrop(tx);  // close the sender so rx terminates\\nfor val in rx {  // iterator until all senders dropped\\n    println!(\"got: {}\", val);\\n}\\n```. "
        "`mpsc` = multiple producers, single consumer. For SPSC/MPMC: use `crossbeam-channel`.",
        f"{STD}/sync/mpsc/index.html",
    ),
    (
        "rust-recipes", "rust recipe: async/await with tokio",
        "```rust\\nuse tokio;\\n\\n#[tokio::main]\\nasync fn main() -> Result<(), Box<dyn std::error::Error>> {\\n    let body = reqwest::get(\"https://example.com\").await?.text().await?;\\n    println!(\"{}\", body);\\n    Ok(())\\n}\\n\\n// Concurrent\\nlet (a, b) = tokio::join!(fetch_a(), fetch_b());\\n\\n// Spawn task\\ntokio::spawn(async { /* runs in background */ });\\n```. "
        "For multi-runtime: `async-std` is alternative; `smol` is minimal.",
        f"{BOOK}/ch20-00-async.html",
    ),
    (
        "rust-recipes", "rust recipe: serde for JSON serialization",
        "Cargo.toml: ```toml\\nserde = { version = \"1.0\", features = [\"derive\"] }\\nserde_json = \"1.0\"\\n```. "
        "```rust\\nuse serde::{Serialize, Deserialize};\\n\\n#[derive(Serialize, Deserialize, Debug)]\\nstruct User {\\n    name: String,\\n    age: u32,\\n    #[serde(default)]\\n    role: Option<String>,\\n}\\n\\nlet u = User { name: \"Alice\".into(), age: 30, role: None };\\nlet json = serde_json::to_string(&u)?;\\nlet u2: User = serde_json::from_str(&json)?;\\n```",
        f"{BOOK}/ch10-02-traits.html",
    ),
    (
        "rust-recipes", "rust recipe: HTTP request with reqwest",
        "Cargo.toml: `reqwest = { version = \"0.12\", features = [\"json\", \"blocking\"] }`. "
        "Blocking: ```rust\\nlet body: serde_json::Value = reqwest::blocking::get(\"https://api.example.com\")?.json()?;\\n```. "
        "Async: ```rust\\n#[tokio::main]\\nasync fn main() -> Result<(), Box<dyn std::error::Error>> {\\n    let client = reqwest::Client::new();\\n    let resp = client.post(\"https://api/users\")\\n        .json(&User { name: \"Alice\".into(), age: 30 })\\n        .send().await?;\\n    println!(\"status: {}\", resp.status());\\n    Ok(())\\n}\\n```",
        f"{STD}/io/index.html",
    ),
    (
        "rust-recipes", "rust recipe: error handling with anyhow + thiserror",
        "For libraries (typed errors): ```rust\\nuse thiserror::Error;\\n#[derive(Error, Debug)]\\nenum MyError {\\n    #[error(\"io: {0}\")]\\n    Io(#[from] std::io::Error),\\n    #[error(\"missing config key '{0}'\")]\\n    MissingKey(String),\\n}\\n```. "
        "For apps (ergonomic any-error): ```rust\\nuse anyhow::{Result, Context};\\nfn parse_config() -> Result<Config> {\\n    let raw = std::fs::read_to_string(\"config.toml\").context(\"reading config\")?;\\n    toml::from_str(&raw).context(\"parsing TOML\")\\n}\\n```",
        f"{BOOK}/ch09-02-recoverable-errors-with-result.html",
    ),
    (
        "rust-recipes", "rust recipe: write + run unit tests",
        "```rust\\nfn add(a: i32, b: i32) -> i32 { a + b }\\n\\n#[cfg(test)]\\nmod tests {\\n    use super::*;\\n    #[test]\\n    fn adds_correctly() {\\n        assert_eq!(add(2, 3), 5);\\n    }\\n    #[test]\\n    #[should_panic(expected = \"divide\")]\\n    fn panics_on_zero() {\\n        let _ = 1 / 0;\\n    }\\n}\\n```. "
        "Run: `cargo test`. Single: `cargo test adds_correctly`. Show stdout: `cargo test -- --nocapture`.",
        f"{BOOK}/ch11-00-testing.html",
    ),
    (
        "rust-recipes", "rust recipe: configure logging with tracing",
        "Cargo.toml: ```toml\\ntracing = \"0.1\"\\ntracing-subscriber = { version = \"0.3\", features = [\"env-filter\"] }\\n```. "
        "```rust\\nuse tracing::{info, warn, error, instrument};\\nuse tracing_subscriber::EnvFilter;\\n\\nfn main() {\\n    tracing_subscriber::fmt()\\n        .with_env_filter(EnvFilter::from_default_env())\\n        .init();\\n    info!(user = \"alice\", \"logged in\");\\n}\\n\\n#[instrument]\\nfn handler(id: u64) { info!(\"processing\"); }\\n```. "
        "Run with `RUST_LOG=debug cargo run`.",
        f"{STD}/io/index.html",
    ),
    (
        "rust-recipes", "rust recipe: pattern matching essentials",
        "```rust\\nmatch value {\\n    1 | 2 | 3 => \"low\",                  // or-patterns\\n    n if n > 100 => \"high\",            // guard\\n    Point { x: 0, .. } => \"on y-axis\", // struct destructure\\n    (a, b, _) => \"triple\",             // tuple\\n    [first, .., last] => \"slice\",      // slice with rest\\n    Some(x) => format!(\"some: {}\", x), // enum variant\\n    _ => \"other\",                       // catch-all\\n}\\n```. "
        "`if let Some(x) = opt { ... }` for single-arm match.",
        f"{BOOK}/ch06-02-match.html",
    ),
    (
        "rust-recipes", "rust recipe: format strings + Display impl",
        "```rust\\nlet s = format!(\"{} is {} years old\", name, age);\\nlet hex = format!(\"{:#x}\", 255);          // 0xff\\nlet pad = format!(\"{:>10}\", \"hi\");         // right-align width 10\\nlet truncated = format!(\"{:.3}\", 3.14159);  // 3.142\\nlet debug = format!(\"{:?}\", vec![1,2,3]);   // [1, 2, 3]\\nlet pretty = format!(\"{:#?}\", my_struct);   // multi-line debug\\n\\nimpl std::fmt::Display for User {\\n    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {\\n        write!(f, \"{} ({})\", self.name, self.age)\\n    }\\n}\\n```",
        f"{STD}/fmt/index.html",
    ),
    (
        "rust-recipes", "rust recipe: lifetimes in function signatures",
        "```rust\\n// Single lifetime\\nfn first_word<'a>(s: &'a str) -> &'a str {\\n    s.split_whitespace().next().unwrap_or(\"\")\\n}\\n\\n// Multiple lifetimes\\nfn longer<'a, 'b>(x: &'a str, y: &'b str) -> &'a str\\nwhere 'b: 'a  // y lives at least as long as x\\n{\\n    if x.len() >= y.len() { x } else { x } // returns 'a-tied value\\n}\\n\\n// Struct holding references\\nstruct Holder<'a> {\\n    data: &'a [u8],\\n}\\n```. "
        "Lifetime elision: most cases don't need explicit annotations.",
        f"{BOOK}/ch10-03-lifetime-syntax.html",
    ),
    (
        "rust-recipes", "rust recipe: from / into trait for conversions",
        "```rust\\nstruct UserId(u64);\\n\\nimpl From<u64> for UserId {\\n    fn from(n: u64) -> Self { UserId(n) }\\n}\\n\\n// Now both work:\\nlet id = UserId::from(42);\\nlet id: UserId = 42.into();\\nfn process(id: impl Into<UserId>) { ... }\\nprocess(42);\\n```. "
        "Free Into impl: defining `From<A> for B` automatically gives `Into<B> for A`. "
        "For fallible: `TryFrom` / `TryInto`.",
        f"{STD}/convert/trait.From.html",
    ),
    (
        "rust-recipes", "rust recipe: conditional compilation with cfg",
        "```rust\\n#[cfg(target_os = \"linux\")]\\nfn os_specific() { /* Linux only */ }\\n\\n#[cfg(any(target_os = \"macos\", target_os = \"linux\"))]\\nfn unix_like() { /* Unix */ }\\n\\n#[cfg(test)]\\nmod tests { /* compiled only with cargo test */ }\\n\\n// Feature-gated\\n#[cfg(feature = \"async\")]\\npub mod async_api { /* enabled via Cargo.toml feature */ }\\n\\n// Runtime check\\nif cfg!(target_os = \"windows\") { ... }\\n```",
        f"{REF}/conditional-compilation.html",
    ),
    (
        "rust-recipes", "rust recipe: declarative macro (macro_rules!)",
        "```rust\\nmacro_rules! min {\\n    ($a:expr, $b:expr) => {\\n        if $a < $b { $a } else { $b }\\n    };\\n    ($a:expr, $b:expr, $($rest:expr),+) => {\\n        min!($a, min!($b, $($rest),+))\\n    };\\n}\\nfn main() {\\n    println!(\"{}\", min!(3, 1, 2));  // 1\\n}\\n```. "
        "Common patterns: `$x:expr`, `$x:ident`, `$x:ty`, `$x:literal`, `$x:tt`. "
        "Repetition: `$( ... )*` (zero+), `$( ... )+` (one+).",
        f"{BOOK}/ch19-06-macros.html",
    ),
    (
        "rust-recipes", "rust recipe: run external command (std::process::Command)",
        "```rust\\nuse std::process::Command;\\n\\nlet output = Command::new(\"ls\")\\n    .arg(\"-la\")\\n    .output()?;\\nprintln!(\"status: {}\", output.status);\\nprintln!(\"stdout: {}\", String::from_utf8_lossy(&output.stdout));\\n\\n// Spawn for streaming\\nlet mut child = Command::new(\"long-job\").spawn()?;\\nchild.wait()?;\\n```",
        f"{STD}/process/index.html",
    ),
    (
        "rust-recipes", "rust recipe: parse + format datetime (chrono)",
        "Cargo.toml: `chrono = \"0.4\"`. "
        "```rust\\nuse chrono::{DateTime, Utc, NaiveDate};\\n\\nlet now: DateTime<Utc> = Utc::now();\\nlet iso = now.to_rfc3339();\\nlet parsed: DateTime<Utc> = \"2026-06-01T12:00:00Z\".parse()?;\\nlet date = NaiveDate::from_ymd_opt(2026, 6, 1).unwrap();\\nlet formatted = now.format(\"%Y-%m-%d %H:%M:%S\").to_string();\\n```. "
        "Modern alternative: `time` crate (more conservative API).",
        f"{STD}/time/index.html",
    ),
    (
        "rust-recipes", "rust recipe: workspace setup for monorepo",
        "Root `Cargo.toml`: ```toml\\n[workspace]\\nresolver = \"2\"\\nmembers = [\"crates/*\"]\\n\\n[workspace.dependencies]\\nserde = { version = \"1.0\", features = [\"derive\"] }\\ntokio = { version = \"1\", features = [\"full\"] }\\n```. "
        "In each crate: ```toml\\n[dependencies]\\nserde = { workspace = true }\\n```. "
        "Build all: `cargo build` from root. Build one: `cargo build -p crate-name`.",
        f"https://rust-lang.github.io/rustup/",
    ),
    (
        "rust-recipes", "rust recipe: cross-compile to another target",
        "(1) Install target: `rustup target add x86_64-unknown-linux-musl`; "
        "(2) Build: `cargo build --target x86_64-unknown-linux-musl --release`; "
        "(3) For cross-compile with C deps: install the right linker (`brew install FiloSottile/musl-cross/musl-cross` for macOS→Linux); "
        "(4) `.cargo/config.toml`: ```toml\\n[target.x86_64-unknown-linux-musl]\\nlinker = \"x86_64-linux-musl-gcc\"\\n```. "
        "Static binary on Linux: target musl (no glibc dependency).",
        f"{BOOK}/ch01-01-installation.html",
    ),
    (
        "rust-recipes", "rust recipe: release vs debug profile",
        "Debug (default): `cargo build` — fast compile, no optimizations, overflow checks on. "
        "Release: `cargo build --release` — optimized (`-O3`), no overflow checks (silently wraps), much slower compile. "
        "Customize in Cargo.toml: ```toml\\n[profile.release]\\nopt-level = 3\\nlto = \"thin\"          # link-time optimization\\ncodegen-units = 1     # better optimization, slower compile\\nstrip = true          # strip symbols (smaller binary)\\noverflow-checks = true  # opt back in for safety\\n```",
        f"{REF}/cfg-attribute.html",
    ),
    (
        "rust-recipes", "rust recipe: write + use a custom iterator",
        "```rust\\nstruct Counter { count: u32, max: u32 }\\nimpl Iterator for Counter {\\n    type Item = u32;\\n    fn next(&mut self) -> Option<u32> {\\n        if self.count < self.max {\\n            self.count += 1;\\n            Some(self.count)\\n        } else {\\n            None\\n        }\\n    }\\n}\\n// Get all iterator adapters for free\\nlet sum: u32 = Counter { count: 0, max: 100 }.sum();\\n```",
        f"{STD}/iter/trait.Iterator.html",
    ),
    (
        "rust-recipes", "rust recipe: use clippy for linting",
        "Install: `rustup component add clippy`. "
        "Run: `cargo clippy` (warnings) or `cargo clippy -- -D warnings` (fail on any). "
        "Auto-fix safe ones: `cargo clippy --fix`. "
        "Common useful lints: `clippy::pedantic`, `clippy::nursery`, `clippy::cargo`. "
        "Allow specific: `#[allow(clippy::too_many_arguments)]`. "
        "Run in CI: `cargo clippy --all-targets -- -D warnings`.",
        f"https://rust-lang.github.io/rustup/concepts/components.html",
    ),
    (
        "rust-recipes", "rust recipe: format with rustfmt",
        "Install: `rustup component add rustfmt`. "
        "Format: `cargo fmt`. "
        "Check (CI): `cargo fmt --check`. "
        "Configure: `rustfmt.toml` at repo root: ```toml\\nmax_width = 100\\ntab_spaces = 4\\nimports_granularity = \"Module\"\\ngroup_imports = \"StdExternalCrate\"\\n```. "
        "For unstable options: add `unstable_features = true`.",
        f"https://rust-lang.github.io/rustup/concepts/components.html",
    ),
    (
        "rust-recipes", "rust recipe: production-ready main()",
        "```rust\\nuse anyhow::Result;\\nuse tracing::{info, error};\\nuse tracing_subscriber::EnvFilter;\\n\\n#[tokio::main]\\nasync fn main() -> Result<()> {\\n    tracing_subscriber::fmt()\\n        .with_env_filter(EnvFilter::from_default_env())\\n        .with_target(false)\\n        .json()\\n        .init();\\n    info!(version = env!(\"CARGO_PKG_VERSION\"), \"starting\");\\n    if let Err(e) = run().await {\\n        error!(error = ?e, \"fatal\");\\n        std::process::exit(1);\\n    }\\n    Ok(())\\n}\\nasync fn run() -> Result<()> { /* app logic */ Ok(()) }\\n```",
        f"{BOOK}/ch12-00-an-io-project.html",
    ),
    (
        "rust-recipes", "rust recipe: write a benchmark",
        "Stable: use `criterion` crate. `Cargo.toml`: `[dev-dependencies] criterion = \"0.5\"`. "
        "`benches/my_bench.rs`: ```rust\\nuse criterion::{black_box, criterion_group, criterion_main, Criterion};\\nfn fib(n: u64) -> u64 { if n < 2 { n } else { fib(n-1) + fib(n-2) } }\\nfn bench_fib(c: &mut Criterion) {\\n    c.bench_function(\"fib 20\", |b| b.iter(|| fib(black_box(20))));\\n}\\ncriterion_group!(benches, bench_fib);\\ncriterion_main!(benches);\\n```. "
        "Run: `cargo bench`.",
        f"{BOOK}/ch11-00-testing.html",
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
            evidence_id=f"ev-rust-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-rust-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="rust-catalog",
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
