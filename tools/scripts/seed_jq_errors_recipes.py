"""Direct-insert jq error + recipe claims."""

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

BASE = "https://jqlang.org/manual"
TUT = "https://jqlang.org/tutorial"

CLAIMS = [
    # ============================================================
    # jq-errors — 30 common errors
    # ============================================================
    (
        "jq-errors", "jq error: Cannot index <type> with \"<key>\"",
        "You used `.key` on a value that isn't an object (e.g., on an array or string). "
        "Fix: (1) check input shape: `jq 'type' input.json` shows top-level type; "
        "(2) if input is array, use `.[0].key` (first element) or `.[].key` (all elements); "
        "(3) defensive: `.key // \"default\"` when key may be missing; "
        "(4) for optional path: `.?key` or `.key?` (silently null instead of erroring); "
        "(5) common: forgot `.[]` to iterate, applied object access to array.",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: Cannot iterate over null (null)",
        "You called `.[]` on null. Common when a key doesn't exist. "
        "Fix: (1) check path: `jq '.path.to.key | type'`; "
        "(2) defensive iterate: `.key // [] | .[]` (empty array if null); "
        "(3) optional: `.key?[]` (drops null silently); "
        "(4) walk safely: `try .key.subkey catch \"missing\"`; "
        "(5) inspect raw input first: `jq .` to confirm structure.",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: null (null) has no keys",
        "Used `keys` or `keys_unsorted` on a null value (key was missing). "
        "Fix: (1) defensive: `(.config // {}) | keys`; "
        "(2) optional: `.config? | keys`; "
        "(3) check first: `if .config then .config | keys else [] end`; "
        "(4) for nested: `try .a.b.c | keys catch null`.",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: parse error: Invalid numeric literal at line N",
        "Input isn't valid JSON — likely shell variable wasn't quoted, or you fed a non-JSON string. "
        "Fix: (1) test input is JSON: `cat input.json | jq .` should pretty-print without error; "
        "(2) for shell-built JSON: quote properly: `echo '{\"k\":1}' | jq .` (NOT `echo {k:1}`); "
        "(3) for HTTP API output: `curl ... | jq .` — confirm Content-Type is JSON (not HTML error page); "
        "(4) for JSONL streams: use `-s` (slurp) or process line-by-line.",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: parse error: Expected another value / Unexpected end of input",
        "JSON is truncated or empty. "
        "Fix: (1) check file: `wc -c file.json` — zero-byte file? `cat file.json` shows what's there; "
        "(2) for pipes: confirm the previous command produced output: `cmd | tee /tmp/debug.json | jq .`; "
        "(3) trailing newline OK; missing closing brace NOT OK: `echo '{\"k\":1}' | jq .` (works); `echo '{\"k\":1' | jq .` (fails); "
        "(4) for multi-doc streams: use `--seq` or `-s` flag.",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: parse error: Trailing garbage after value",
        "Multiple JSON values in input without --slurp. "
        "Fix: (1) for JSONL (one JSON per line): `jq -c .` works (default treats stream of values); "
        "(2) to combine all into one array: `jq -s .` (slurp); "
        "(3) for whitespace-separated values: jq handles natively (NDJSON); "
        "(4) for non-standard format (commas between?): pre-process with `sed`.",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: <name>/0 is not defined at <top-level>",
        "Function call with wrong arity or typo. "
        "Fix: (1) function name typo: `lenght` → `length`; "
        "(2) wrong arity: `select` needs an arg: `select(.x > 0)` (not `select`); "
        "(3) custom function not defined: load with `--slurpfile`/`--rawfile` or `import`; "
        "(4) check available functions: jq manual lists builtins.",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: Invalid path expression",
        "Tried to assign to or update something that isn't a valid path (like a function call result). "
        "Fix: (1) for `|=` (update): left side must be a path — `.foo |= ascii_upcase` (✓); "
        "(2) `length |= ...` is NOT a path: re-write as `.field = (.field | length)`; "
        "(3) for nested assignment: `.a.b.c = 5` works; `.a.b | .c = 5` does NOT (the pipe breaks path-ness); "
        "(4) array assign: `.arr[0] = \"new\"` (specific index) or `.arr[] = \"all\"`.",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: Cannot index array with string \"<key>\"",
        "Tried object access on an array. "
        "Fix: (1) iterate first: `.arr[] | .key`; "
        "(2) specific index: `.arr[0].key`; "
        "(3) check input: `jq 'type' input.json` — is the top level actually an array? "
        "(4) for arrays-of-arrays: nested `.arr[][0]`; "
        "(5) inspect: `jq '.arr | type'`.",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: jq: 1 compile error",
        "Syntax error in your filter — jq prints additional context. "
        "Fix: (1) read the error — usually points to the bad char; "
        "(2) common: missing semicolon between function definitions: `def f: ...;  def g: ...;`; "
        "(3) unmatched braces/parens: `{(a)}` vs `{a}`; "
        "(4) string interpolation requires quotes: `\"\\(.x)\"` (inside double quotes); "
        "(5) for complex filters: build incrementally in pipeline — test each piece.",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: number is not a string (or vice versa)",
        "Function expects string but got number (or other type mismatch). "
        "Fix: (1) explicit conversion: `tostring` (any → string), `tonumber` (string → number); "
        "(2) for interpolation: `\"value: \\(.n)\"` (jq stringifies automatically); "
        "(3) for arithmetic: `tonumber + 5`; "
        "(4) common: `split` needs string arg: `.field | tostring | split(\",\")`.",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: Number too large / overflow",
        "Value exceeds jq's 64-bit float precision (~2^53 for integers). "
        "Fix: (1) for IDs that overflow (e.g. 64-bit unsigned IDs from APIs): emit as STRING from upstream; "
        "(2) jq treats all numbers as floats — for arbitrary precision use external tool (Python, awk with bignum); "
        "(3) for representation only (no math): keep as string: `\"\\(.bigid)\"`; "
        "(4) test boundary: `jq -n '9007199254740992 == 9007199254740993'` returns true (precision lost).",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: Could not parse args (Bad --arg / --argjson)",
        "Wrong syntax for command-line args. "
        "Fix: (1) `--arg NAME VALUE`: value treated as STRING — `jq --arg x foo '.[$x]'`; "
        "(2) `--argjson NAME JSON`: parsed as JSON: `jq --argjson n 42 '. + $n'`; "
        "(3) `--rawfile NAME PATH`: load file content as string; "
        "(4) `--slurpfile NAME PATH`: parse file as JSON; "
        "(5) refer to vars with `$name` in filter.",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: Bad JSON in --slurpfile / --argjson",
        "The file or value passed isn't valid JSON. "
        "Fix: (1) validate first: `jq . file.json` should succeed; "
        "(2) `--rawfile` accepts ANY text (loads as one string) — useful for non-JSON input; "
        "(3) common: file has BOM or stray Windows CRLF — `dos2unix file.json` or `sed -i 's/\\r$//' file.json`; "
        "(4) for JSONL: combine first: `jq -s . lines.jsonl > combined.json` then `--slurpfile`.",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: assigning to a non-path",
        "Same root as 'Invalid path expression'. Left side of `=` or `|=` must be a valid path. "
        "Fix: (1) DON'T pipe before assignment: `.a |= (. * 2)` (good) vs `.a | . *= 2` (bad); "
        "(2) for transforms: `map(. * 2)` over arrays; "
        "(3) for adding a field: `. + {newfield: 42}` or `.newfield = 42`; "
        "(4) for deletion: `del(.field)` (a function, not an assignment).",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: SIGPIPE / Broken pipe",
        "Downstream command (e.g., `head`) closed the pipe before jq finished. "
        "Usually harmless but produces error output. "
        "Fix: (1) handle with stderr redirect: `jq . huge.json 2>/dev/null | head`; "
        "(2) avoid: emit only needed entries: `jq '.[0:10]'` (slice) instead of `jq '.[]' | head`; "
        "(3) for stream processing: use `jq` with `--stream` mode for huge inputs; "
        "(4) ignore via `|| true` in scripts.",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: getpath/0 expects an array",
        "`getpath` needs `[\"a\", \"b\", \"c\"]` form, not dot-notation string. "
        "Fix: (1) correct: `getpath([\"a\",\"b\",\"c\"])`; "
        "(2) WRONG: `getpath(.a.b.c)`; "
        "(3) for dynamic paths: `getpath([$key1, $key2])`; "
        "(4) alternative for static: just use dot notation: `.a.b.c`.",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: <name>/2 is not defined (wrong arity)",
        "Built-in function expects different number of args. "
        "Fix: (1) check manual: `select/1` (takes 1 arg: `select(.x > 0)`); `map/1` (takes 1: `map(. * 2)`); "
        "(2) common: `add` takes no args: `[1,2,3] | add` (not `add(...)`); "
        "(3) `reduce` takes 3 components: `reduce .[] as $x (0; . + $x)`; "
        "(4) read error carefully — it names the arity.",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: Date format / unknown time zone",
        "Date parsing failed — input doesn't match jq's expected format. "
        "Fix: (1) `fromdateiso8601` expects RFC3339: `\"2024-12-01T10:30:00Z\" | fromdateiso8601`; "
        "(2) `fromdate` expects same; "
        "(3) for custom formats use `strptime(\"%Y-%m-%d\") | mktime`; "
        "(4) check format: log timestamps are often LOCAL TIME, need TZ suffix; "
        "(5) `now | strftime(\"%Y-%m-%dT%H:%M:%SZ\")` for ISO-style output.",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: cannot index with \"foo\" (using string when expected number)",
        "Confusion between object key access vs array index. "
        "Fix: (1) `.array[0]` (number index for arrays); `.obj.key` or `.obj[\"key\"]` (string for objects); "
        "(2) `[\"key with spaces\"]` works on objects only; "
        "(3) for unknown type: `if type == \"array\" then .[0] else .key end`; "
        "(4) for `keys` on arrays: returns indices (0, 1, 2...).",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: error: 'def' should follow 'as' / variable scope",
        "Custom function definition syntax issue or scope error. "
        "Fix: (1) correct def: `def double: . * 2;` (semicolon terminates); "
        "(2) inline use: `def double: . * 2; .[] | double`; "
        "(3) with arg: `def add($x): . + $x; 5 | add(3)`; "
        "(4) variables: `. as $x | $x + 5`; "
        "(5) recursive: `def is_even: . % 2 == 0;` works inside `def`.",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: array (`[\"x\"]`) is not a string",
        "Function expects scalar but got array. "
        "Fix: (1) extract first: `.[0]`; "
        "(2) join: `join(\",\")`; "
        "(3) sum-like reduce: `[1,2,3] | add`; "
        "(4) common: `.tags | join(\", \")` to make CSV.",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: try .x catch <message>",
        "Not an error — `try/catch` lets you handle errors. "
        "Pattern: `try .field.subfield catch \"missing\"`. "
        "Re-throw with `try ... catch error`. "
        "For optional path only: `.?field` (silent null) is shorter.",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: Unfinished string at line N",
        "String literal in JSON input has no closing quote (corrupted input). "
        "Fix: (1) check input: `cat -A input.json` shows special chars; "
        "(2) for multi-line strings: JSON strings can't span lines unescaped — `\\n` instead; "
        "(3) for shell-built JSON: quoting escapes get mangled — use heredoc: `jq . <<EOF\\n{\"k\":\"v\"}\\nEOF`; "
        "(4) for HTTP API: server may have returned an error page mid-stream.",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: --slurp + --null-input conflict",
        "`-s` collects ALL input into one array; `-n` ignores input — these don't mix sensibly. "
        "Fix: (1) for transform pipeline: just `-s`: `jq -s 'map(.x)' file1 file2`; "
        "(2) for ad-hoc generation (no input): just `-n`: `jq -n '{generated: \"value\"}'`; "
        "(3) for combining: `jq -n --slurpfile arr file.json '$arr | map(.x)'`.",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: select returned no output (filter dropped everything)",
        "Not an error — `select` is a filter; if nothing matches, output is empty. "
        "Fix: (1) for default value when empty: pipe through `// \"none\"`; "
        "(2) for count of matches: `[ ... | select(...) ] | length`; "
        "(3) to wrap in array: `[ .[] | select(.x > 0) ]`; "
        "(4) verify expectation: drop the select temporarily to see what's there.",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: cannot find module / import failed",
        "Used `import` or `include` but module path is wrong. "
        "Fix: (1) jq looks in `~/.jq` by default for `.jq` files; "
        "(2) explicit path: `jq -L /path/to/modules 'import \"mymod\" as m; m::myfunc'`; "
        "(3) file must be `name.jq`; import without `.jq` extension; "
        "(4) namespace: `import \"foo\" as f` then call as `f::funcname`.",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: Range with negative step or wrong types",
        "`range` requires numeric args, step must be non-zero. "
        "Fix: (1) `range(0; 10)` → 0..9; `range(0; 10; 2)` → 0,2,4,6,8; "
        "(2) reverse: `range(10; 0; -1)`; "
        "(3) for floats: works fine `range(0; 1; 0.1)`; "
        "(4) common: for slice indices use `.[$i:$j]` not `range`.",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: Output not valid JSON when using -r without final transform",
        "`-r` strips quotes from strings — if you mean to keep them, omit. "
        "Fix: (1) for shell-consumed string: `-r` is what you want: `jq -r '.name'` → `Alice`; "
        "(2) for JSON output: drop `-r`: `jq '.name'` → `\"Alice\"`; "
        "(3) for keeping JSON shape but extracting only strings: `--raw-output0` (NUL-separated) for safe shell consumption with newlines in values.",
        f"{BASE}/",
    ),
    (
        "jq-errors", "jq error: too many open files (with many input files)",
        "Hit `ulimit -n`. "
        "Fix: (1) check: `ulimit -n`; "
        "(2) raise: `ulimit -n 4096`; "
        "(3) better: use `--slurp` with cat: `cat *.json | jq -s 'add'`; "
        "(4) for parallel: limit concurrency: `find . -name '*.json' | xargs -n 100 jq -s 'add'`.",
        f"{BASE}/",
    ),

    # ============================================================
    # jq-recipes — 38 common workflows
    # ============================================================
    (
        "jq-recipes", "jq recipe: extract a single field",
        "`jq '.name'` for top-level. "
        "Nested: `jq '.user.email'`. "
        "Dynamic: `jq --arg k name '.[$k]'`. "
        "From array: `jq '.[].name'` (each name). "
        "Pretty: `jq -r '.name'` strips quotes for shell consumption.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: array element by index or slice",
        "First: `jq '.[0]'`. "
        "Last: `jq '.[-1]'`. "
        "Slice (Python-style): `jq '.[2:5]'` (indices 2,3,4). "
        "Reverse: `jq 'reverse'`. "
        "All: `jq '.[]'`.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: filter array elements with select",
        "`jq '.[] | select(.age > 18)'` keeps only matching. "
        "Multiple conditions: `select(.age > 18 and .country == \"US\")`. "
        "OR: `select(.role == \"admin\" or .role == \"owner\")`. "
        "Negation: `select(.deleted | not)`. "
        "Wrap in array: `[.[] | select(...)]`.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: transform with map",
        "`jq 'map(.field)'` extracts a field from each item, returns array. "
        "Transform: `map({id: .id, name: .name | ascii_upcase})`. "
        "Numeric: `map(.price * 1.1)`. "
        "Combined with select: `map(select(.active) | .name)`. "
        "Equivalent: `[.[] | ...]`.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: build a new object",
        "`jq '{name: .name, email: .email}'`. "
        "Shorthand (when key=field): `jq '{name, email}'`. "
        "With computed: `jq '{full: \"\\(.first) \\(.last)\", age: (now - .born_ts) / 31556952 | floor}'`. "
        "Conditional fields: `{name} + (if .admin then {role: \"admin\"} else {} end)`.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: update / modify a field (|=)",
        "Replace value: `.name = \"new\"`. "
        "Update with function: `.name |= ascii_upcase`. "
        "Nested update: `.user.email |= ascii_downcase`. "
        "All array elements: `.[].price |= (. * 1.1)`. "
        "Combined: `map(.email |= ascii_downcase)`.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: add or remove keys",
        "Add: `. + {new_key: \"value\"}` or `.new_key = \"value\"`. "
        "Delete: `del(.unwanted)` or `del(.a, .b)`. "
        "Nested delete: `del(.user.secret)`. "
        "Conditional add: `. + (if .enable then {feature: true} else {} end)`. "
        "Replace whole object: `{kept: .kept}` (other keys dropped).",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: get unique values",
        "`jq '[.[] | .category] | unique'` produces sorted unique list. "
        "Unique by computed key: `unique_by(.id)`. "
        "Count unique: `[.[] | .category] | unique | length`. "
        "Frequency map: `group_by(.category) | map({key: .[0].category, value: length}) | from_entries`.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: group by a field",
        "`jq 'group_by(.category)'` produces array of arrays. "
        "Summarize per group: `group_by(.category) | map({category: .[0].category, count: length, total: map(.amount) | add})`. "
        "Equivalent to SQL GROUP BY.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: sort by field",
        "`jq 'sort_by(.date)'` ascending. "
        "Descending: `sort_by(.date) | reverse`. "
        "Multi-key: `sort_by(.last, .first)`. "
        "Custom: `sort_by(.priority * -1, .name)` (negate for desc on that key). "
        "For simple arrays: just `sort`.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: sum / average a field",
        "Sum: `jq '[.[] | .amount] | add'`. "
        "Average: `[.[] | .amount] | add / length`. "
        "Max: `[.[] | .amount] | max`. Or `max_by(.amount)` for the whole object. "
        "Min: `min` / `min_by(.amount)`. "
        "Stats in one pass: `[.[] | .amount] | {sum: add, avg: add/length, max, min}`.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: object ↔ entries (key-value pairs)",
        "Object → array: `to_entries` produces `[{key:\"a\", value:1}, ...]`. "
        "Array → object: `from_entries`. "
        "Rename keys: `to_entries | map(.key |= ascii_upcase) | from_entries`. "
        "Filter keys: `to_entries | map(select(.key != \"secret\")) | from_entries`. "
        "Transform values: `to_entries | map(.value |= tostring) | from_entries`.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: string operations",
        "Split: `\"a,b,c\" | split(\",\")` → `[\"a\",\"b\",\"c\"]`. "
        "Join: `[\"a\",\"b\"] | join(\"-\")` → `\"a-b\"`. "
        "Case: `ascii_upcase`, `ascii_downcase`. "
        "Trim: `ltrimstr(\" \")` / `rtrimstr(\" \")`. "
        "Replace: `gsub(\"foo\"; \"bar\")` (regex). "
        "Length: `length` (works on strings too). "
        "Test pattern: `test(\"^foo\")`.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: regex match and capture",
        "Test: `test(\"^[0-9]+$\")` returns true/false. "
        "Match: `match(\"foo(.+)\"; \"i\")` returns object with offsets + captures (i = case-insensitive). "
        "Extract capture: `match(\"version: ([0-9]+)\") | .captures[0].string`. "
        "Find all: `scan(\"\\\\d+\")` yields all matches as a stream.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: format dates",
        "From ISO: `\"2024-12-01T10:00:00Z\" | fromdateiso8601` (returns Unix timestamp). "
        "To ISO: `now | todateiso8601`. "
        "Custom format: `strftime(\"%Y-%m-%d %H:%M\")`. "
        "Parse custom: `strptime(\"%Y-%m-%d\") | mktime`. "
        "Now: `now` (Unix epoch).",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: conditional logic (if-then-else)",
        "`if .x > 10 then \"big\" else \"small\" end`. "
        "With elif: `if .a then 1 elif .b then 2 else 3 end`. "
        "Truthiness: null and false are falsy, everything else truthy (including empty arrays). "
        "Default value: `.x // \"default\"` (the `//` alternative operator — uses RHS when LHS is null or false).",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: alternative operator // (default values)",
        "`.maybe_missing // \"default\"` returns RHS if LHS is null or false. "
        "Chained: `.a // .b // \"final fallback\"`. "
        "For empty filters too: `.maybe_empty[] // \"placeholder\"` — but this iterates; usually you want `(.maybe_empty // []) | .[]`. "
        "Use case: API responses with optional fields.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: flatten nested arrays",
        "`[[1,2],[3,4]] | flatten` → `[1,2,3,4]`. "
        "Single level: `flatten(1)`. "
        "Deep: `flatten` (default = all levels). "
        "For nested objects: jq has no built-in deep flatten — write recursive: `.. | objects`.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: recurse into nested structures (..)",
        "`..` yields every node in the tree (DFS). "
        "All objects: `.. | objects`. "
        "All strings: `.. | strings`. "
        "Find specific value: `.. | select(type == \"object\" and has(\"target_field\"))`. "
        "Useful when JSON shape is dynamic / unknown.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: pretty-print JSON (default) vs compact",
        "Pretty (default): `jq .` indents 2 spaces. "
        "Compact: `jq -c .` (one line per value — good for JSONL output). "
        "Tab indent: `jq --tab .`. "
        "Specific indent: `jq --indent 4 .`. "
        "Sort keys: `jq -S .` (lexicographic order).",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: raw string output (-r)",
        "`jq -r '.name'` strips quotes — output: `Alice` not `\"Alice\"`. "
        "Essential for shell consumption: `name=$(jq -r '.name' file.json)`. "
        "Combined with `[]`: `jq -r '.users[].email'` produces one email per line. "
        "Multi-value safe: `jq -r '.users[] | [.id, .name] | @tsv'` for tab-separated.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: slurp multiple JSON documents (-s)",
        "`jq -s . file1 file2` reads all into one array. "
        "Then process: `jq -s '[.[] | .users] | flatten' file1 file2`. "
        "For JSONL: `jq -s '.' lines.jsonl`. "
        "Compact JSONL out: `jq -c '.[]' < combined.json` (per-line emit).",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: inject shell variables (--arg, --argjson)",
        "String: `jq --arg name \"$NAME\" '.users[] | select(.name == $name)'`. "
        "JSON value: `jq --argjson count 5 '.[0:$count]'` (parsed as number). "
        "Multiple: `jq --arg user $USER --argjson admin true '...'`. "
        "Better than embedding: avoids shell-quoting issues with `\"$NAME\"`.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: read JSON from another file (--slurpfile, --rawfile)",
        "`--slurpfile NAME PATH` parses file as JSON, binds to `$NAME` as array. "
        "`--rawfile NAME PATH` loads file as raw string (any text). "
        "Example: `jq --slurpfile users users.json '.activity | map(. + (.user_id as $id | $users[] | select(.id == $id)))'`.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: process curl output (parse API responses)",
        "Direct pipe: `curl -s api.example.com/users | jq '.[] | .name'`. "
        "Failure-aware: `curl -fsSL ... | jq` (the `-f` fails on 4xx so jq doesn't see HTML); "
        "save + inspect: `curl -s ... | tee /tmp/api.json | jq '.results[0]'`; "
        "follow pagination: ```bash\\nnext=\"...\"; while [ -n \"$next\" ]; do\\n  resp=$(curl -s \"$next\")\\n  echo \"$resp\" | jq '.data[]'\\n  next=$(echo \"$resp\" | jq -r '.next_url')\\ndone\\n```.",
        f"{TUT}/",
    ),
    (
        "jq-recipes", "jq recipe: generate JSON from scratch (-n / null input)",
        "`jq -n '{name: \"Alice\", age: 30}'` produces an object — no input needed. "
        "From shell vars: `jq -n --arg name \"$NAME\" --argjson age \"$AGE\" '{name: $name, age: $age}'`. "
        "Generate sequences: `jq -n '[range(1;11)]'` → `[1,2,3,4,5,6,7,8,9,10]`. "
        "Build complex: `jq -n '{api: {version: \"v1\", endpoints: [\"users\",\"posts\"]}}'`.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: reduce / aggregate to single value",
        "Sum array: `reduce .[] as $x (0; . + $x)`. "
        "Build object: `reduce .users[] as $u ({}; . + {($u.id): $u.name})`. "
        "Max-by tracking value: `reduce .[] as $x (null; if . == null or $x.val > .val then $x else . end)`. "
        "Pattern: `reduce STREAM as $VAR (INIT; UPDATE)`.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: walk recursively transforming",
        "`walk(...)` applies a filter to every node in the tree. "
        "Lowercase all string values: `walk(if type == \"string\" then ascii_downcase else . end)`. "
        "Remove all nulls: `walk(if type == \"object\" then with_entries(select(.value != null)) else . end)`. "
        "Trim all strings: `walk(if type == \"string\" then ltrimstr(\" \") | rtrimstr(\" \") else . end)`.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: count occurrences (histogram)",
        "By field: `jq 'group_by(.status) | map({(.[0].status): length}) | add'`. "
        "Result: `{\"ok\": 47, \"error\": 3, \"pending\": 5}`. "
        "Just counts (no keys): `jq 'group_by(.status) | map(length)'`. "
        "Combined with sort: `... | to_entries | sort_by(.value) | reverse`.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: extract top N or bottom N",
        "Top 5 by score (desc): `sort_by(.score) | reverse | .[0:5]`. "
        "Bottom 5: `sort_by(.score) | .[0:5]`. "
        "Top 10% by score: `sort_by(-.score) | .[0: (length / 10 | floor)]`. "
        "Combined with grouping: `group_by(.dept) | map(sort_by(-.salary) | .[0])` (top earner per dept).",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: build CSV output (@csv / @tsv)",
        "From array: `jq -r '.users[] | [.id, .name, .email] | @csv'` (handles quoting). "
        "TSV: `... | @tsv` (tab-separated, faster to consume). "
        "With headers: `(.users[0] | keys_unsorted), (.users[] | [.[]]) | @csv` (first row = headers). "
        "Custom: build strings with `\"\\(.a),\\(.b)\"` if @csv doesn't fit.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: build URI query string (@uri)",
        "`jq -nr --arg q 'hello world' '\"https://api.com/search?q=\\($q | @uri)\"'`. "
        "@uri URL-encodes its input. "
        "Build whole query: `{q: $query, page: $page} | to_entries | map(\"\\(.key)=\\(.value | tostring | @uri)\") | join(\"&\")`.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: handle paths with dots in keys",
        "`jq '.[\"key.with.dots\"]'` (bracket-quote form). "
        "Dynamic path: `jq --arg k 'a.b' '.[$k]'`. "
        "For nested paths with dots: `getpath([\"a.b\", \"c\"])`. "
        "For YAML-converted JSON where dots are common: use `getpath` consistently.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: stream very large files (--stream)",
        "`jq --stream` emits path-value pairs without loading entire JSON in memory. "
        "Format: `[[\"path\",\"to\"], value]` per line. "
        "Reconstruct subtrees: `jq --stream 'select(length == 2 and .[0][0] == \"users\")'`. "
        "Use case: multi-GB JSON files that don't fit in RAM. "
        "Tools to compose: `gron` for grep-style, `dasel` for path-based.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: combine multiple JSON files (union/merge)",
        "Concatenate arrays: `jq -s 'add' file1.json file2.json` (treats top-level as arrays). "
        "Merge objects (later wins): `jq -s '.[0] * .[1]'`. "
        "Deep merge (recursive): `jq -s 'reduce .[] as $x ({}; . * $x)'` (jq's `*` is non-recursive — for true deep merge, custom def needed). "
        "Concatenate as JSONL: `cat file1.json file2.json | jq -c '.'`.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: process Kubernetes / docker output",
        "K8s pods by status: `kubectl get pods -o json | jq -r '.items[] | select(.status.phase != \"Running\") | .metadata.name'`. "
        "Docker container IPs: `docker inspect $(docker ps -q) | jq -r '.[] | \"\\(.Name) \\(.NetworkSettings.IPAddress)\"'`. "
        "Helm values: `helm get values -o json my-release | jq '.image'`.",
        f"{TUT}/",
    ),
    (
        "jq-recipes", "jq recipe: JSON Patch / partial update",
        "RFC 6902 not built-in, but jq can build patches: `[{op: \"replace\", path: \"/name\", value: \"new\"}]`. "
        "Apply patch operations: ```jq\\nreduce .[] as $op (.; \\n  if $op.op == \"replace\" then setpath($op.path | split(\"/\") | .[1:]; $op.value) \\n  elif $op.op == \"remove\" then delpaths([$op.path | split(\"/\") | .[1:]]) \\n  else . end)\\n```. "
        "For real JSON Patch: external tools like `json-patch`.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: validate JSON structure",
        "`jq -e .` exits non-zero on null/empty (good for shell scripts). "
        "Type check: `jq -e 'type == \"object\" and has(\"required_key\")' file.json`. "
        "Schema-like: `jq -e '. | (.name | type == \"string\") and (.age | type == \"number\")' file.json`. "
        "Just is-it-valid: `jq empty file.json` returns nothing on success, fails on parse error.",
        f"{BASE}/",
    ),
    (
        "jq-recipes", "jq recipe: define and use custom functions",
        "Inline: `jq 'def average: add / length; .scores | average' data.json`. "
        "With args: `jq 'def take($n): .[0:$n]; .items | take(5)'`. "
        "Recursive: `jq 'def repeat(s; $n): if $n <= 0 then \"\" else s + repeat(s; $n - 1) end; repeat(\"-\"; 10)'`. "
        "Library: save in `~/.jq/mylib.jq`, then `jq 'import \"mylib\" as m; m::myfunc' file.json`.",
        f"{BASE}/",
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
            evidence_id=f"ev-jq-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-jq-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="jq-catalog",
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
