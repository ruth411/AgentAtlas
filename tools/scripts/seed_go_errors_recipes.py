"""Direct-insert go error + recipe claims."""

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

GODEV = "https://go.dev"
PKG = "https://pkg.go.dev"

CLAIMS = [
    # ============================================================
    # go-errors — 30 common errors
    # ============================================================
    (
        "go-errors", "go error: cannot find module providing package <name>",
        "`go` couldn't resolve an import. Either the module isn't required in go.mod, the path is wrong, or the proxy can't reach it. "
        "Fix: (1) `go mod tidy` adds missing requires + removes unused; "
        "(2) explicit: `go get github.com/foo/bar@latest` (or `@v1.2.3`); "
        "(3) typo? double-check the import path on pkg.go.dev; "
        "(4) private repo? configure `GOPRIVATE`: `go env -w GOPRIVATE=github.com/myorg/*`; "
        "(5) check proxy reachability: `GOPROXY=direct go get ...` to bypass proxy.",
        f"{GODEV}/ref/mod",
    ),
    (
        "go-errors", "go error: go.mod file not found in current directory or any parent directory",
        "Not in a Go module — every Go project needs go.mod (or you're in GOPATH-mode for legacy code). "
        "Fix: (1) initialize: `go mod init github.com/user/project` (path should match where it lives); "
        "(2) `cd` to a directory inside a module; "
        "(3) for legacy GOPATH workflow (rare now): `go env -w GO111MODULE=off` and put code under `$GOPATH/src/...` (NOT recommended for new code).",
        f"{GODEV}/doc/modules/managing-dependencies",
    ),
    (
        "go-errors", "go error: package X is not in std / not in standard library",
        "You imported a name that doesn't exist in stdlib (typo, removed package, or third-party that needs to be `go get`'d). "
        "Fix: (1) check name on https://pkg.go.dev/std; "
        "(2) common typos: `math/rand` (not `math.rand`), `encoding/json` (not `json`), `net/http` (not `http`); "
        "(3) external pkg: `go get example.com/pkg && go mod tidy`; "
        "(4) deprecated stdlib (e.g. `io/ioutil`): replace with modern equivalents (`os.ReadFile`, `io.ReadAll`).",
        f"{PKG}",
    ),
    (
        "go-errors", "go error: ambiguous import: found package X in multiple modules",
        "Two requires in go.mod provide the same import path — usually after a fork rename. "
        "Fix: (1) check: `go mod why -m <pkg>` shows why each module is required; "
        "(2) add a `replace` directive: `replace example.com/old => example.com/new v1.2.3`; "
        "(3) `go mod tidy` to clean up; "
        "(4) for monorepo: use `go work` (workspaces).",
        f"{GODEV}/ref/mod",
    ),
    (
        "go-errors", "go error: checksum mismatch / verifying X: checksum mismatch",
        "go.sum content disagrees with what go just downloaded — either tampered module, hash drift after force-push, or stale sum. "
        "Fix: (1) safe: `go mod verify` confirms scope; "
        "(2) if you trust upstream's change: delete the offending lines in go.sum and `go mod tidy`; "
        "(3) for transient: `GOFLAGS=-insecure` (DO NOT use in CI); "
        "(4) check GOPROXY: `go env GOPROXY` — sum-checking goes through sumdb (`go env GOSUMDB`).",
        f"{GODEV}/ref/mod",
    ),
    (
        "go-errors", "go error: cgo: C compiler not found / exec: gcc: not found",
        "Code uses cgo but no C compiler is installed (or CGO_ENABLED=0 in a context that needs it). "
        "Fix: (1) install: macOS `xcode-select --install`; Linux `apt install build-essential`; "
        "(2) pin compiler: `CC=clang go build`; "
        "(3) disable cgo (pure-Go build): `CGO_ENABLED=0 go build`; "
        "(4) for static linking: `CGO_ENABLED=0 GOOS=linux go build -ldflags='-extldflags=-static'`.",
        f"{GODEV}/cmd/go/",
    ),
    (
        "go-errors", "go panic: runtime error: invalid memory address or nil pointer dereference",
        "You dereferenced a nil pointer — most common Go runtime panic. "
        "Fix: (1) check pointers before use: `if x != nil { x.Method() }`; "
        "(2) for map of pointers: assign to a variable first: `if v, ok := m[k]; ok && v != nil { v.Method() }`; "
        "(3) returned from a function? check error first: `if r, err := f(); err == nil { r.Use() }`; "
        "(4) interface nil-trap: `var x *T = nil; var i interface{} = x` — `i != nil` is TRUE (the interface has a type tag).",
        f"{PKG}/errors",
    ),
    (
        "go-errors", "go panic: runtime error: index out of range",
        "Slice/array index ≥ len(). "
        "Fix: (1) check `if i < len(s) { ... }` before access; "
        "(2) use `range` instead of indexed loops where possible; "
        "(3) for slice modification: `if i >= len(s) { s = append(s, ...) }`; "
        "(4) common after `strings.Split`: the result is len ≥ 1, but the parts might be empty strings — index into substring carefully.",
        f"{PKG}/builtin",
    ),
    (
        "go-errors", "go panic: runtime error: integer divide by zero",
        "Self-explanatory but easy to miss. "
        "Fix: (1) check denominator: `if b != 0 { return a / b }`; "
        "(2) for float math (always returns ±Inf or NaN, never panics): conversion only panics on int; "
        "(3) modulo: `a % 0` also panics.",
        f"{PKG}/builtin",
    ),
    (
        "go-errors", "go fatal error: all goroutines are asleep - deadlock!",
        "Main goroutine is blocked waiting on a channel/mutex/wg that will never unblock. Detected by Go runtime only when ALL goroutines are blocked. "
        "Fix: (1) classic: forgot `close(ch)` after writer is done so receiver hangs; "
        "(2) buffered channel full + no reader: close + drain; "
        "(3) WaitGroup never reaches 0 — missing `wg.Done()` or wrong wg.Add count; "
        "(4) for debugging: print goroutine stacks with `kill -ABRT <pid>` or `runtime.Stack()`.",
        f"{PKG}/sync",
    ),
    (
        "go-errors", "go fatal error: concurrent map writes",
        "Maps in Go are NOT safe for concurrent use. Two goroutines writing the same map → fatal. "
        "Fix: (1) protect with `sync.Mutex`; "
        "(2) read-mostly: `sync.RWMutex` (RLock for readers); "
        "(3) high-throughput: `sync.Map` (specialized, not always faster — measure); "
        "(4) for per-shard sharded maps: split by key hash; "
        "(5) `go run -race` detects this BEFORE production.",
        f"{PKG}/sync",
    ),
    (
        "go-errors", "go panic: send on closed channel / close of closed channel",
        "Channel was closed by one goroutine while another tried to send/close it. "
        "Fix: (1) golden rule: ONLY the sender closes — never the receiver; "
        "(2) for multiple senders: use a separate done-channel + sync.Once for close; "
        "(3) check before sending: `select { case ch <- v: default: }` is NOT safe alone; "
        "(4) idiomatic pattern: producer closes when its work is done, consumers loop on `for v := range ch`.",
        f"{PKG}/builtin",
    ),
    (
        "go-errors", "go compile error: cannot use X (type Y) as type Z in argument to ...",
        "Type mismatch. Go is strict — no implicit conversion between named types, even if underlying types match. "
        "Fix: (1) explicit convert: `MyType(x)` if underlying types match; "
        "(2) for int/float: explicit `int(f)` or `float64(i)`; "
        "(3) for strings/bytes: `[]byte(s)` or `string(b)`; "
        "(4) interface satisfaction: must implement ALL methods exactly (including pointer vs value receiver).",
        f"{GODEV}/ref/spec",
    ),
    (
        "go-errors", "go compile error: declared and not used / declared but not used",
        "Go refuses to compile if a variable is declared but never read. Imports too. "
        "Fix: (1) use it; "
        "(2) intentionally unused: `_ = x` (assign to underscore) or rename declaration to `_, foo := f()`; "
        "(3) for unused import (during refactor): use `_ \"package\"` for side-effect imports (database drivers, init); "
        "(4) `go vet` and `goimports` will clean these up.",
        f"{GODEV}/ref/spec",
    ),
    (
        "go-errors", "go compile error: imported and not used: \"X\"",
        "Same rule as unused variables — Go won't compile with unused imports. "
        "Fix: (1) remove the line; "
        "(2) `goimports -w .` does this automatically; "
        "(3) side-effect import (init only, never used directly): `import _ \"github.com/lib/pq\"`; "
        "(4) configure editor to format on save with goimports.",
        f"{GODEV}/cmd/go/",
    ),
    (
        "go-errors", "go compile error: cannot assign to struct field X in map / unaddressable",
        "Map values aren't addressable — you can't mutate fields in-place. "
        "Fix: (1) replace whole value: `m[k] = T{Field: newValue}`; "
        "(2) use map of POINTERS: `map[string]*T` then `m[k].Field = ...` (but watch nil); "
        "(3) read-modify-write: `v := m[k]; v.Field = ...; m[k] = v`; "
        "(4) same applies to string indices — strings are immutable.",
        f"{GODEV}/ref/spec",
    ),
    (
        "go-errors", "go runtime: assignment to entry in nil map",
        "You wrote to a map that's nil (never initialized — `var m map[string]int` defaults to nil). "
        "Fix: (1) initialize: `m := map[string]int{}` or `m := make(map[string]int)`; "
        "(2) for struct field maps: initialize in constructor; "
        "(3) reading from nil map is OK (returns zero value) — only writes panic.",
        f"{PKG}/builtin",
    ),
    (
        "go-errors", "go compile error: multiple-value X in single-value context",
        "A function returns multiple values but you're using it in a context that expects one. "
        "Fix: (1) accept all returns: `a, b := f()` (or use _ for ones you skip: `a, _ := f()`); "
        "(2) error pattern: `if v, err := f(); err == nil { use(v) }`; "
        "(3) inside expression: store result in a var first.",
        f"{GODEV}/ref/spec",
    ),
    (
        "go-errors", "go compile error: missing return at end of function",
        "Function declares a return type but a code path doesn't return. "
        "Fix: (1) add explicit return at end; "
        "(2) common cause: `for` loops with no return below — even infinite loops need a hint; "
        "(3) for non-returning paths: `panic(\"unreachable\")` makes intent clear; "
        "(4) Go does NOT infer 'this loop runs forever' — be explicit.",
        f"{GODEV}/ref/spec",
    ),
    (
        "go-errors", "go error: flag provided but not defined: -<name>",
        "Using `flag.Parse()` but the flag wasn't registered. "
        "Fix: (1) register: `var v string; flag.StringVar(&v, \"name\", \"default\", \"usage\")` BEFORE `flag.Parse()`; "
        "(2) typo check; "
        "(3) for unknown flags accepted: use `flag.CommandLine.Parse()` with custom `ErrorHandling = flag.ContinueOnError`; "
        "(4) for multiple positional args after flags: use `flag.Args()`.",
        f"{PKG}/flag",
    ),
    (
        "go-errors", "go error: context deadline exceeded",
        "An operation outlasted its `context.WithTimeout`/`WithDeadline`. "
        "Fix: (1) extend the timeout if legitimately slow; "
        "(2) inspect: `if errors.Is(err, context.DeadlineExceeded) { ... }`; "
        "(3) propagate context through call stack — don't use `context.Background()` deep in code; "
        "(4) HTTP clients: `client := &http.Client{Timeout: 30*time.Second}` is per-request total; "
        "(5) for partial work: check `ctx.Err()` between iterations.",
        f"{PKG}/context",
    ),
    (
        "go-errors", "go error: context canceled",
        "Parent context was canceled (caller used cancel func) before operation completed. Usually expected — not always an 'error'. "
        "Fix: (1) check with `errors.Is(err, context.Canceled)` to handle differently from real errors; "
        "(2) for HTTP servers: client disconnected — usually fine to log + return; "
        "(3) for graceful shutdown: contexts cancel together — propagate `ctx` everywhere.",
        f"{PKG}/context",
    ),
    (
        "go-errors", "go error: tls: handshake error / x509: certificate signed by unknown authority",
        "HTTPS cert verification failed — usually wrong root CA, self-signed cert, or stale system trust store. "
        "Fix: (1) add CA to system trust store: macOS `Keychain Access → System → Certificates`; "
        "(2) per-request (dev only): `http.Client{Transport: &http.Transport{TLSClientConfig: &tls.Config{InsecureSkipVerify: true}}}` — NEVER prod; "
        "(3) custom CA pool: load PEM into `x509.CertPool` and use `tls.Config{RootCAs: pool}`; "
        "(4) hostname mismatch: SAN must include the hostname.",
        f"{PKG}/crypto/tls",
    ),
    (
        "go-errors", "go error: dial tcp: connection refused",
        "Nothing listening on the address/port. "
        "Fix: (1) verify server is running on that port: `lsof -i :8080` or `netstat -an | grep 8080`; "
        "(2) typo in address (localhost vs 127.0.0.1 vs hostname); "
        "(3) firewall blocking; "
        "(4) for Docker: container needs to expose the port: `docker run -p 8080:8080`; "
        "(5) wait for service to be ready: retry with backoff in tests.",
        f"{PKG}/net",
    ),
    (
        "go-errors", "go error: read: connection reset by peer / EOF unexpected",
        "Remote closed the TCP connection unexpectedly (often during long requests, idle timeouts, or server crash). "
        "Fix: (1) implement retry with backoff (common in HTTP clients); "
        "(2) check server's `IdleTimeout` / `ReadHeaderTimeout` — extend if too aggressive; "
        "(3) for keepalive issues: set `Transport.IdleConnTimeout`; "
        "(4) load balancer between you + server may be dropping idle conns — match its timeout.",
        f"{PKG}/net/http",
    ),
    (
        "go-errors", "go error: json: cannot unmarshal X into Go value of type Y",
        "JSON shape doesn't match your struct. "
        "Fix: (1) check field types: `int` won't accept `\"42\"`; use `json.Number` or `string` field then convert; "
        "(2) optional fields: use pointer (`*int`) — nil means absent; "
        "(3) flexible types: `interface{}` or `json.RawMessage` to defer parsing; "
        "(4) date format: `time.Time` expects RFC3339 by default — custom: implement `UnmarshalJSON`; "
        "(5) for arrays vs objects: union with `interface{}` and type-switch.",
        f"{PKG}/encoding/json",
    ),
    (
        "go-errors", "go error: sql: no rows in result set",
        "`db.QueryRow().Scan(...)` returns this when 0 rows match. "
        "Fix: (1) check: `if errors.Is(err, sql.ErrNoRows) { return nil, ErrNotFound }` (treat as 'not found'); "
        "(2) for 'zero is OK' queries (e.g. COUNT): use `Query()` + iterate, not `QueryRow`; "
        "(3) for batch queries: `Query` + `rows.Next()` loop; rows.Err() catches scan errors.",
        f"{PKG}/database/sql",
    ),
    (
        "go-errors", "go error: fork/exec: no such file or directory",
        "`os/exec` couldn't find the binary. "
        "Fix: (1) use absolute path: `exec.Command(\"/usr/bin/git\", ...)`; "
        "(2) for PATH lookup: `exec.LookPath(\"git\")` returns the resolved path; "
        "(3) check PATH from your program: `os.Getenv(\"PATH\")` might differ from shell PATH; "
        "(4) shebang scripts: must be executable (`chmod +x`).",
        f"{PKG}/os/exec",
    ),
    (
        "go-errors", "go error: imported package is a main package (not a library)",
        "You tried to import a `package main` from another module. Only libraries can be imported. "
        "Fix: (1) move shared code to a separate package (e.g. `internal/foo`); "
        "(2) leave `main` for the binary entry point only; "
        "(3) for re-exporting in monorepos: factor the code into a `pkg/<name>` subdirectory.",
        f"{GODEV}/ref/spec",
    ),
    (
        "go-errors", "go panic: send on closed channel during shutdown",
        "Producer didn't notice consumer signaled stop. "
        "Fix: (1) pattern: producer reads from a done-channel (`select { case <-done: return; case ch <- v: }`); "
        "(2) close the work-channel from PRODUCER side only; "
        "(3) for fan-out: use `errgroup` (`golang.org/x/sync/errgroup`) — handles cancellation + error propagation cleanly.",
        f"{PKG}/sync",
    ),

    # ============================================================
    # go-recipes — 36 idiomatic patterns
    # ============================================================
    (
        "go-recipes", "go recipe: initialize a new module",
        "`go mod init github.com/user/project` (path should match where the repo lives). "
        "Creates go.mod with the module path + go version. "
        "Then write your code and `go run .`. "
        "For a CLI: `go build -o myapp .`. "
        "Multiple commands in one repo? Put each `package main` in `cmd/<name>/` subdirs.",
        f"{GODEV}/doc/tutorial/create-module",
    ),
    (
        "go-recipes", "go recipe: add / update / remove dependencies",
        "Add: `go get example.com/pkg` (latest) or `go get example.com/pkg@v1.2.3` (specific). "
        "Update all to latest: `go get -u ./...`. "
        "Patch updates only: `go get -u=patch ./...`. "
        "Major version: must explicitly opt in: `go get example.com/pkg/v2`. "
        "Remove unused (after deleting imports): `go mod tidy`. "
        "Inspect: `go list -m all` (all transitive).",
        f"{GODEV}/doc/modules/managing-dependencies",
    ),
    (
        "go-recipes", "go recipe: vendor dependencies for reproducible builds",
        "`go mod vendor` copies all deps into `vendor/` directory. "
        "Subsequent builds use vendor by default (Go 1.14+). "
        "Useful for: corporate compliance, offline builds, slow proxies. "
        "Trade-off: bloats repo. "
        "Switch back to module mode: `rm -rf vendor && go build -mod=mod`. "
        "Force vendor: `go build -mod=vendor`.",
        f"{GODEV}/ref/mod",
    ),
    (
        "go-recipes", "go recipe: run tests (single, all, package, with verbose)",
        "All tests in current package: `go test`. "
        "All recursively: `go test ./...`. "
        "One test: `go test -run TestFoo`. "
        "Regex: `go test -run 'TestFoo.*'`. "
        "Verbose: `-v`. "
        "Short mode (skip slow ones): `-short`. "
        "Specific package: `go test github.com/me/proj/pkg`. "
        "Watch logs in real-time: `go test -v ./...`.",
        f"{PKG}/testing",
    ),
    (
        "go-recipes", "go recipe: run with race detector",
        "`go run -race main.go` or `go test -race ./...`. "
        "Catches data races at runtime. ~5-10x slower + uses more memory — use in dev/CI, not prod. "
        "Race detector reports stack traces of conflicting accesses. "
        "Combine with `-count=N` to repeatedly run tests (catches flaky races).",
        f"{GODEV}/cmd/go/",
    ),
    (
        "go-recipes", "go recipe: run benchmarks",
        "Write `func BenchmarkX(b *testing.B) { for i := 0; i < b.N; i++ { ... } }` in a `_test.go` file. "
        "Run: `go test -bench=. -benchmem`. "
        "Specific: `-bench=BenchmarkFoo`. "
        "Compare runs: `go test -bench=. -count=5 > old.txt`, change code, `go test -bench=. -count=5 > new.txt`, then `benchstat old.txt new.txt`. "
        "Profile during bench: `-cpuprofile cpu.out -memprofile mem.out`.",
        f"{PKG}/testing",
    ),
    (
        "go-recipes", "go recipe: measure code coverage",
        "`go test -cover` shows percentage. "
        "Coverage profile: `go test -coverprofile=c.out ./...`. "
        "HTML view: `go tool cover -html=c.out -o cov.html && open cov.html`. "
        "Per-function: `go tool cover -func=c.out`. "
        "For multi-package: `go test -coverpkg=./... -coverprofile=c.out ./...`.",
        f"{PKG}/testing",
    ),
    (
        "go-recipes", "go recipe: cross-compile for different OS / ARCH",
        "`GOOS=linux GOARCH=amd64 go build -o app-linux .` (Linux on x86_64). "
        "macOS Apple Silicon: `GOOS=darwin GOARCH=arm64`. "
        "Windows: `GOOS=windows GOARCH=amd64`. "
        "List all: `go tool dist list`. "
        "Static binary (no dynamic linking): `CGO_ENABLED=0 GOOS=linux go build -ldflags='-s -w' -o app .` — `-s -w` strips symbols + DWARF.",
        f"{GODEV}/cmd/go/",
    ),
    (
        "go-recipes", "go recipe: reduce binary size",
        "Strip symbols + DWARF: `go build -ldflags='-s -w' .`. "
        "Disable cgo: `CGO_ENABLED=0` (also removes glibc dependency). "
        "Trim paths: `-trimpath` removes absolute paths from build. "
        "Further: `upx app` (external compressor, 50-70% smaller, slight startup cost). "
        "Combined: `CGO_ENABLED=0 go build -ldflags='-s -w' -trimpath -o app .`.",
        f"{GODEV}/cmd/go/",
    ),
    (
        "go-recipes", "go recipe: embed files into a binary",
        "Go 1.16+ embed package. "
        "```go\nimport _ \"embed\"\n//go:embed config.json\nvar configJSON []byte\n//go:embed templates/*.html\nvar tmplFS embed.FS\n```\n"
        "Directory: `//go:embed assets` + `embed.FS`. "
        "Multiple patterns: `//go:embed *.txt *.md`. "
        "Access: `data, _ := tmplFS.ReadFile(\"templates/index.html\")`.",
        f"{PKG}/embed",
    ),
    (
        "go-recipes", "go recipe: use generics (type parameters)",
        "Go 1.18+. "
        "```go\nfunc Map[T, U any](s []T, f func(T) U) []U {\n    r := make([]U, len(s))\n    for i, v := range s { r[i] = f(v) }\n    return r\n}\n// Usage: lengths := Map(words, func(s string) int { return len(s) })\n```\n"
        "Constraints: `comparable` (==, !=), `any` (interface{}), or custom interface like `interface{ ~int | ~float64 }`. "
        "Stdlib generics: `slices.Sort`, `maps.Keys`, `cmp.Less`.",
        f"{GODEV}/ref/spec",
    ),
    (
        "go-recipes", "go recipe: launch goroutines + wait with WaitGroup",
        "```go\nvar wg sync.WaitGroup\nfor _, task := range tasks {\n    wg.Add(1)\n    go func(t Task) {\n        defer wg.Done()\n        t.Run()\n    }(task)  // capture by value\n}\nwg.Wait()\n```\n"
        "CRITICAL: pass loop var into goroutine (`func(t Task)`), don't close over loop var (Go 1.22+ fixed this, but be explicit). "
        "For errors: use `errgroup.Group` instead — collects first error and supports cancellation.",
        f"{PKG}/sync",
    ),
    (
        "go-recipes", "go recipe: use context for timeout",
        "```go\nctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)\ndefer cancel()  // ALWAYS defer cancel — releases resources\nreq, _ := http.NewRequestWithContext(ctx, \"GET\", url, nil)\nresp, err := http.DefaultClient.Do(req)\nif errors.Is(err, context.DeadlineExceeded) { /* timed out */ }\n```\n"
        "Propagate: pass ctx as FIRST argument to any func that might block. "
        "Inherit deadline: `context.WithTimeout(parentCtx, ...)`.",
        f"{PKG}/context",
    ),
    (
        "go-recipes", "go recipe: use context for cancellation",
        "```go\nctx, cancel := context.WithCancel(parent)\ndefer cancel()\ngo work(ctx)\n// later, to stop work:\ncancel()\n```\n"
        "Inside the worker: `select { case <-ctx.Done(): return ctx.Err(); case ... }`. "
        "For graceful shutdown of HTTP server: `srv.Shutdown(ctx)` where ctx has deadline for grace period.",
        f"{PKG}/context",
    ),
    (
        "go-recipes", "go recipe: use select for timeout / multiple channels",
        "```go\nselect {\ncase msg := <-ch:\n    handle(msg)\ncase <-time.After(5 * time.Second):\n    return errors.New(\"timeout\")\ncase <-ctx.Done():\n    return ctx.Err()\n}\n```\n"
        "Default case (non-blocking): `default: /* try next */`. "
        "Caveat: `time.After` leaks the timer until it fires — for hot loops use `time.NewTimer` + `timer.Stop()`.",
        f"{PKG}/time",
    ),
    (
        "go-recipes", "go recipe: protect shared state with sync.Mutex / RWMutex",
        "```go\ntype Counter struct {\n    mu sync.Mutex\n    n  int\n}\nfunc (c *Counter) Inc() {\n    c.mu.Lock()\n    defer c.mu.Unlock()\n    c.n++\n}\n```\n"
        "Read-heavy: use `sync.RWMutex` — `RLock()`/`RUnlock()` for readers. "
        "Always pair Lock/Unlock with `defer`. "
        "For lock-free counters: `sync/atomic.Int64.Add(...)`.",
        f"{PKG}/sync",
    ),
    (
        "go-recipes", "go recipe: lazy initialization with sync.Once",
        "```go\nvar (\n    once sync.Once\n    instance *DB\n)\nfunc GetDB() *DB {\n    once.Do(func() {\n        instance = connect()\n    })\n    return instance\n}\n```\n"
        "Guaranteed to run exactly once even with concurrent calls. "
        "Modern (Go 1.21+): `sync.OnceValue[T any]` and `sync.OnceFunc` cleaner alternatives.",
        f"{PKG}/sync",
    ),
    (
        "go-recipes", "go recipe: parse JSON into a struct",
        "```go\ntype User struct {\n    Name  string `json:\"name\"`\n    Email string `json:\"email,omitempty\"`\n    Age   int    `json:\"age\"`\n}\nvar u User\nif err := json.Unmarshal(data, &u); err != nil { return err }\n```\n"
        "Unknown fields silently dropped — to error: `dec := json.NewDecoder(r); dec.DisallowUnknownFields()`. "
        "From file: `data, _ := os.ReadFile(p); json.Unmarshal(data, &u)`. "
        "From HTTP body: `json.NewDecoder(r.Body).Decode(&u)`.",
        f"{PKG}/encoding/json",
    ),
    (
        "go-recipes", "go recipe: marshal struct to JSON",
        "`b, err := json.Marshal(u)` produces compact JSON. "
        "Pretty: `b, _ := json.MarshalIndent(u, \"\", \"  \")`. "
        "To file: `os.WriteFile(\"out.json\", b, 0644)`. "
        "To HTTP response: `json.NewEncoder(w).Encode(u)` (don't forget `w.Header().Set(\"Content-Type\", \"application/json\")`). "
        "Custom marshaling: implement `MarshalJSON() ([]byte, error)`.",
        f"{PKG}/encoding/json",
    ),
    (
        "go-recipes", "go recipe: make an HTTP GET / POST request",
        "GET: `resp, err := http.Get(url); defer resp.Body.Close(); body, _ := io.ReadAll(resp.Body)`. "
        "POST JSON: ```go\nbody, _ := json.Marshal(payload)\nresp, err := http.Post(url, \"application/json\", bytes.NewReader(body))\ndefer resp.Body.Close()\n```. "
        "Custom: ```go\nreq, _ := http.NewRequestWithContext(ctx, \"POST\", url, bytes.NewReader(body))\nreq.Header.Set(\"Authorization\", \"Bearer \"+token)\nclient := &http.Client{Timeout: 30 * time.Second}\nresp, err := client.Do(req)\n```. "
        "ALWAYS `defer resp.Body.Close()` and drain or you'll leak conns.",
        f"{PKG}/net/http",
    ),
    (
        "go-recipes", "go recipe: build a basic HTTP server",
        "```go\nhttp.HandleFunc(\"/hello\", func(w http.ResponseWriter, r *http.Request) {\n    fmt.Fprintln(w, \"hi\")\n})\nlog.Fatal(http.ListenAndServe(\":8080\", nil))\n```\n"
        "Production: configure timeouts. ```go\nsrv := &http.Server{\n    Addr: \":8080\",\n    Handler: mux,\n    ReadHeaderTimeout: 5 * time.Second,\n    ReadTimeout: 30 * time.Second,\n    WriteTimeout: 30 * time.Second,\n    IdleTimeout: 120 * time.Second,\n}\nsrv.ListenAndServe()\n```",
        f"{PKG}/net/http",
    ),
    (
        "go-recipes", "go recipe: write HTTP middleware",
        "```go\nfunc logging(next http.Handler) http.Handler {\n    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {\n        start := time.Now()\n        next.ServeHTTP(w, r)\n        log.Printf(\"%s %s %s\", r.Method, r.URL.Path, time.Since(start))\n    })\n}\n// Wire: srv.Handler = logging(authentication(mux))\n```\n"
        "Stack multiple by wrapping. For richer routing/middleware: chi, gin, echo.",
        f"{PKG}/net/http",
    ),
    (
        "go-recipes", "go recipe: connect to a database with database/sql",
        "```go\nimport _ \"github.com/lib/pq\"  // driver registers itself\ndb, err := sql.Open(\"postgres\", \"host=localhost user=u dbname=d sslmode=disable\")\ndb.SetMaxOpenConns(25)\ndb.SetMaxIdleConns(25)\ndb.SetConnMaxLifetime(5 * time.Minute)\nif err := db.PingContext(ctx); err != nil { return err }\n```\n"
        "Drivers: `lib/pq` (postgres), `mysql`, `sqlite3` (cgo), `modernc.org/sqlite` (pure-Go). "
        "ALWAYS configure connection pool size + lifetime.",
        f"{PKG}/database/sql",
    ),
    (
        "go-recipes", "go recipe: run a SQL query (with placeholders)",
        "Single row: ```go\nvar name string\nif err := db.QueryRowContext(ctx, \"SELECT name FROM users WHERE id=$1\", id).Scan(&name); errors.Is(err, sql.ErrNoRows) { return ErrNotFound } else if err != nil { return err }\n```. "
        "Multiple: ```go\nrows, err := db.QueryContext(ctx, \"SELECT id, name FROM users\")\ndefer rows.Close()\nfor rows.Next() { rows.Scan(&id, &name) }\nrows.Err()\n```. "
        "Insert with return: PostgreSQL `RETURNING id` then Scan.",
        f"{PKG}/database/sql",
    ),
    (
        "go-recipes", "go recipe: read a file line-by-line",
        "```go\nf, _ := os.Open(path)\ndefer f.Close()\nscanner := bufio.NewScanner(f)\nfor scanner.Scan() {\n    line := scanner.Text()\n    process(line)\n}\nif err := scanner.Err(); err != nil { return err }\n```\n"
        "Default line limit: 64 KiB. For longer: `scanner.Buffer(make([]byte, 1024*1024), 10*1024*1024)`. "
        "Whole file: `data, err := os.ReadFile(path)` (Go 1.16+).",
        f"{PKG}/bufio",
    ),
    (
        "go-recipes", "go recipe: execute an external command",
        "```go\ncmd := exec.CommandContext(ctx, \"git\", \"log\", \"--oneline\")\nout, err := cmd.Output()  // stdout only; CombinedOutput() merges stderr\nif err != nil {\n    var ee *exec.ExitError\n    if errors.As(err, &ee) {\n        log.Printf(\"exit %d: %s\", ee.ExitCode(), ee.Stderr)\n    }\n    return err\n}\n```\n"
        "For streaming: `cmd.Stdout = os.Stdout; cmd.Stderr = os.Stderr; cmd.Run()`. "
        "Pass env: `cmd.Env = append(os.Environ(), \"FOO=bar\")`.",
        f"{PKG}/os/exec",
    ),
    (
        "go-recipes", "go recipe: parse command-line flags",
        "Stdlib `flag`. ```go\nvar (\n    port = flag.Int(\"port\", 8080, \"listen port\")\n    name = flag.String(\"name\", \"\", \"required name\")\n)\nflag.Parse()\nif *name == \"\" { flag.Usage(); os.Exit(2) }\nfor _, arg := range flag.Args() { /* positional */ }\n```. "
        "Subcommands / richer flags: cobra (de facto), pflag. "
        "Env-var-aware: kelseyhightower/envconfig.",
        f"{PKG}/flag",
    ),
    (
        "go-recipes", "go recipe: structured logging with slog",
        "Go 1.21+ stdlib. ```go\nimport \"log/slog\"\nlogger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))\nlogger.Info(\"request handled\", \"method\", r.Method, \"path\", r.URL.Path, \"ms\", elapsed)\nlogger.Error(\"db query\", \"err\", err)\n```. "
        "Set as global: `slog.SetDefault(logger)` then `slog.Info(...)` anywhere. "
        "Attach context: `logger.With(\"request_id\", id).Info(\"...\")`.",
        f"{PKG}/log/slog",
    ),
    (
        "go-recipes", "go recipe: error wrapping with errors.Is / errors.As",
        "Wrap: `return fmt.Errorf(\"reading config: %w\", err)`. "
        "Check sentinel: `if errors.Is(err, io.EOF) { ... }`. "
        "Extract type: ```go\nvar perr *os.PathError\nif errors.As(err, &perr) { log.Printf(\"path: %s\", perr.Path) }\n```. "
        "Custom sentinel: `var ErrNotFound = errors.New(\"not found\")` then `errors.Is(err, ErrNotFound)`.",
        f"{PKG}/errors",
    ),
    (
        "go-recipes", "go recipe: write a unit test (with table-driven cases)",
        "```go\nfunc TestAdd(t *testing.T) {\n    cases := []struct{\n        name string\n        a, b, want int\n    }{\n        {\"positive\", 1, 2, 3},\n        {\"zero\", 0, 0, 0},\n        {\"negative\", -1, 1, 0},\n    }\n    for _, c := range cases {\n        t.Run(c.name, func(t *testing.T) {\n            if got := Add(c.a, c.b); got != c.want {\n                t.Errorf(\"got %d, want %d\", got, c.want)\n            }\n        })\n    }\n}\n```",
        f"{PKG}/testing",
    ),
    (
        "go-recipes", "go recipe: fuzz testing",
        "Go 1.18+. ```go\nfunc FuzzParse(f *testing.F) {\n    f.Add(\"hello\")  // seed corpus\n    f.Fuzz(func(t *testing.T, s string) {\n        v, err := Parse(s)\n        if err == nil {\n            if Reformat(v) != s { t.Error(\"round-trip broken\") }\n        }\n    })\n}\n```. "
        "Run: `go test -fuzz=FuzzParse -fuzztime=30s`. "
        "Failing inputs saved to `testdata/fuzz/`.",
        f"{PKG}/testing",
    ),
    (
        "go-recipes", "go recipe: profile with pprof",
        "Add: `import _ \"net/http/pprof\"` and a `go func() { http.ListenAndServe(\":6060\", nil) }()` in main. "
        "CPU profile: `go tool pprof http://localhost:6060/debug/pprof/profile?seconds=30`. "
        "Heap: `go tool pprof http://localhost:6060/debug/pprof/heap`. "
        "Goroutines: `curl http://localhost:6060/debug/pprof/goroutine?debug=2`. "
        "Inside pprof: `top10`, `list FuncName`, `web` (opens SVG).",
        f"{PKG}/net/http/pprof",
    ),
    (
        "go-recipes", "go recipe: graceful HTTP server shutdown",
        "```go\nsrv := &http.Server{Addr: \":8080\", Handler: mux}\ngo func() { srv.ListenAndServe() }()\n\nsigCh := make(chan os.Signal, 1)\nsignal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)\n<-sigCh\n\nctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)\ndefer cancel()\nif err := srv.Shutdown(ctx); err != nil {\n    log.Printf(\"forced shutdown: %v\", err)\n}\n```\n"
        "Drains in-flight requests, refuses new ones. Combine with `signal.NotifyContext` for context-driven shutdown.",
        f"{PKG}/net/http",
    ),
    (
        "go-recipes", "go recipe: idiomatic project layout",
        "Common (not enforced): `cmd/<name>/main.go` for binaries; `internal/<pkg>` for code that shouldn't be importable by other modules; `pkg/<name>` for libraries you want exposed; `Makefile` or `Taskfile.yml` for build automation. "
        "Avoid: deep `pkg/` directories (no benefit over just a flat layout for small projects). "
        "Tests live alongside source: `foo.go` + `foo_test.go`. "
        "Example: https://github.com/golang-standards/project-layout (informal community guide, not official).",
        f"{GODEV}/doc/modules/layout",
    ),
    (
        "go-recipes", "go recipe: use slices/maps generic helpers (Go 1.21+)",
        "stdlib `slices` and `maps` packages. "
        "`slices.Sort(s)` (sorts in place, requires `cmp.Ordered`). "
        "`slices.Contains(s, v)`. "
        "`slices.IndexFunc(s, func(t T) bool { ... })`. "
        "`slices.SortFunc(s, cmp.Compare)`. "
        "`maps.Keys(m)`, `maps.Values(m)`. "
        "`maps.Clone(m)`. "
        "These replaced a lot of manual loops and `sort.Slice`/`sort.SliceStable`.",
        f"{PKG}/slices",
    ),
    (
        "go-recipes", "go recipe: use workspaces for multi-module dev",
        "Go 1.18+. For multi-module monorepo OR cross-repo dev. "
        "`go work init ./moduleA ./moduleB` creates go.work. "
        "From any module inside: `go run` / `go test` uses local code for the other module. "
        "Add more later: `go work use ./moduleC`. "
        "DON'T commit go.work (gitignore it) — it's a local dev tool, not for distribution. "
        "Cleaner than `replace` directives for active multi-module work.",
        f"{GODEV}/ref/mod",
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
            evidence_id=f"ev-go-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-go-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="go-catalog",
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
