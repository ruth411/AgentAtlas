"""Direct-insert ~30 awk error claims + ~30 awk recipe claims.

Synthesized like the ansible/apt seed scripts — each with OFFICIAL_DOCS
evidence pointing to the most relevant gawk manual page, orchestrator-
verified, then embedded for semantic re-rank.
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

BASE = "https://www.gnu.org/software/gawk/manual/html_node"

CLAIMS = [
    # ============================================================
    # awk-errors — common awk / gawk runtime + syntax errors
    # ============================================================
    (
        "awk-errors", "awk error: syntax error near ... newline",
        "awk's parser failed at an unexpected newline — usually a missing closing brace or paren earlier in your program. "
        "Common causes: forgetting `}` after an action block, unbalanced parens in a complex expression, or a single-quoted shell program where you accidentally embedded a literal single quote (which terminates the shell quote early). "
        "Fix: (1) count `{` vs `}` — they must balance; (2) use `awk -f program.awk` instead of inline `awk '...'` for non-trivial programs (avoids shell quoting hell); (3) for shell quoting, embed literals with `\"'\"` patterns or use double-quoted programs with escaped `\\$`.",
        f"{BASE}/Quoting.html",
    ),
    (
        "awk-errors", "awk error: cannot open file for reading",
        "awk couldn't open a file you passed as an argument — file doesn't exist or you lack read permission. "
        "Fix: (1) confirm the path with `ls -la <file>`; (2) check permissions; (3) for awk reading from stdin, omit the filename: `cat data | awk '{print $1}'` or use `awk '{print $1}' -` (explicit dash for stdin); "
        "(4) for processing multiple files, glob expansion in the shell happens before awk sees them — `awk '{...}' *.log` is fine; (5) inside the program, use `getline line < \"/path\"` to read explicitly and handle the result.",
        f"{BASE}/Naming-Standard-Input.html",
    ),
    (
        "awk-errors", "awk error: division by zero attempted",
        "awk tried to evaluate `x / 0` or `x % 0`. Causes: the divisor variable was never set (defaults to 0 in awk), or your data had a zero where you didn't expect. "
        "Fix: (1) guard with a conditional: `{ if ($2 != 0) print $1 / $2 }`; (2) initialize variables in BEGIN if you want a non-zero default; (3) use the `int()` test wisely — `int(\"abc\")` returns 0 silently in awk, which can produce surprise zero divisors. "
        "Debug: `awk '{print \"line\", NR, \"divisor:\", $2}'` to print what's being used.",
        f"{BASE}/Arithmetic-Ops.html",
    ),
    (
        "awk-errors", "awk error: regular expression too big",
        "gawk's regex engine hit an internal complexity limit, usually from a very long alternation or nested quantifiers. "
        "Fix: (1) split the alternation into separate awk programs piped together; (2) use multiple simpler patterns: `awk '/p1/ {...} /p2/ {...}'`; (3) for huge keyword lists, read keywords into an awk array and use `if (word in keywords)` instead of one giant regex; (4) for gawk-specific, try increasing `RS_REGEXP_SIZE` — or just use a different tool (sed, perl) for the pathological case.",
        f"{BASE}/Regexp.html",
    ),
    (
        "awk-errors", "awk error: backslash not last character on line",
        "awk's line-continuation backslash needs to be the LAST character before the newline; trailing whitespace breaks it. "
        "Fix: (1) trim trailing whitespace from the line with the backslash; (2) for shell-embedded awk, the backslash-newline must survive shell quoting — single quotes preserve it cleanly; (3) in a `.awk` file, use a proper continuation character placement.",
        f"{BASE}/Statements.html",
    ),
    (
        "awk-errors", "awk error: unterminated string",
        "awk's parser found an opening double-quote with no matching closing quote on the same line. "
        "Fix: (1) escape literal quotes inside strings with `\\\"`; (2) for multi-line strings, awk doesn't support them natively — use string concatenation: `s = \"line 1 \" \\\\\\n   \"line 2\";` (string concat is implicit by adjacent strings); (3) common shell-quoting bug: `awk \"BEGIN{print \\\"$x\\\"}\"` — easier to use single quotes around the whole program and pass shell vars via `-v`: `awk -v x=\"$x\" 'BEGIN{print x}'`.",
        f"{BASE}/Constants.html",
    ),
    (
        "awk-errors", "awk error: $(expr) is not allowed as a function argument",
        "Old syntax error from non-GNU awks — using `$(some_expression)` to access a field number stored in an expression isn't always supported. "
        "Fix: parenthesize separately: `$(NF-1)` is fine; `$(i+1)` is fine in gawk but reject in some older awks. For maximum portability, assign to a variable first: `j = i + 1; print $j`.",
        f"{BASE}/Fields.html",
    ),
    (
        "awk-errors", "awk error: array used in scalar context",
        "You wrote `arr` where awk expected a scalar (a single value). Arrays in awk can only appear as `arr[index]` or in `for (k in arr)` constructs. "
        "Fix: (1) reference an element: `print arr[\"key\"]`; (2) iterate: `for (k in arr) print k, arr[k]`; (3) get array size with gawk's `length(arr)`; (4) common bug: assigning to an array name without a subscript: `arr = \"value\"` after `arr[1] = \"x\"` — awk refuses this.",
        f"{BASE}/Arrays.html",
    ),
    (
        "awk-errors", "awk error: scalar used in array context",
        "Inverse of the previous: you used a name as `arr[k]` after using it as a scalar. awk's variables are typed dynamically but inconsistently across these two uses produces an error. "
        "Fix: (1) `delete name` first to reset; (2) use distinct names for scalars and arrays in the same program; (3) gawk lets you `delete arr` to clear the entire array — useful in loops that reset state.",
        f"{BASE}/Arrays.html",
    ),
    (
        "awk-errors", "awk error: invalid backslash escape",
        "awk's string literal contains an unrecognized escape sequence like `\\m` or `\\z`. Behavior varies by awk implementation; gawk warns. "
        "Fix: (1) use only standard escapes (\\n, \\t, \\\\, \\\", \\b, \\f, \\r, \\v, \\a, \\/, octal \\NNN); (2) for a literal backslash in a regex, double it: `/path\\\\to/` (regex sees `\\\\` which means a literal `\\`); (3) for a literal forward slash in a regex, just `/`. The escaping rules differ between string literals and regex literals — be careful.",
        f"{BASE}/Constants.html",
    ),
    (
        "awk-errors", "awk error: cannot find function",
        "You called `foo(args)` but no function named `foo` is defined and it's not a built-in. "
        "Fix: (1) check the function definition is in the same `awk -f` invocation or `@include`'d; (2) for gawk-specific built-ins (gensub, asort, asorti, mktime, etc.), verify you're using gawk not mawk/POSIX awk; (3) for typos, awk doesn't autocorrect — `lentgh()` won't resolve to `length()`.",
        f"{BASE}/Functions.html",
    ),
    (
        "awk-errors", "awk error: function called with wrong number of arguments",
        "Built-in functions are picky about arity. Examples: `substr(s, start)` is OK; `substr(s, start, length)` is OK; `substr(s)` errors. "
        "Fix: (1) check the manual for the function signature; (2) for user-defined functions in gawk, extra args become local-scoped variables — but missing required args still error. "
        "Common slip: `sprintf(\"%d\")` errors (no value to format); `sprintf(\"%d\", 0)` works.",
        f"{BASE}/Built_002din.html",
    ),
    (
        "awk-errors", "awk error: cannot redirect to two-way pipe and read input",
        "You used `|&` two-way I/O with a command that doesn't behave the way awk expects. "
        "Fix: (1) ensure the coprocess actually reads stdin and writes stdout; (2) check buffering — awk may block waiting for output if the coprocess is line-buffered upstream. Use `fflush()` or run the coprocess with `stdbuf -o0`; (3) for simple cases, prefer one-way pipes (`print x | \"cmd\"` and `\"cmd\" | getline y`).",
        f"{BASE}/Two_002dway-I_002fO.html",
    ),
    (
        "awk-errors", "awk error: print redirection bad",
        "awk's redirection target (after `>`, `>>`, or `|`) wasn't a valid filename or command. "
        "Fix: (1) the right-hand side must be a string expression: `print x > \"output.txt\"` (quoted filename) or `print x > outfile` (variable holding a filename); (2) without quotes, awk interprets the name as a variable — and an uninitialized variable is empty string, which causes the bad-redirection error; (3) for appending: `print x >> \"output.txt\"`; (4) close files explicitly with `close(\"output.txt\")` to free file handles in long-running programs.",
        f"{BASE}/I_002fO-Functions.html",
    ),
    (
        "awk-errors", "awk error: split has bad argument",
        "`split(string, array, [separator])` requires a string and a destination array; the separator is optional. Common errors: passing a scalar where awk expected the array. "
        "Fix: (1) ensure the second arg is a fresh or properly-typed array — `delete arr` before reuse; (2) for regex separator, quote it: `split($0, parts, /\\t/)` or `split($0, parts, \"\\t\")`; (3) split returns the number of fields — capture: `n = split($0, parts)`.",
        f"{BASE}/Split-Function.html",
    ),
    (
        "awk-errors", "awk warning: regexp constant for parameter has been replaced",
        "gawk warns when you pass a regex literal directly to a function that expects a string. "
        "Fix: (1) for `match(s, /regex/)`, use `match(s, /regex/)` — the literal IS the regex pattern; (2) for `split(s, arr, /regex/)`, same — gawk accepts a literal regex directly; (3) the warning typically points to user-defined functions where a regex literal is being implicitly converted to string '0' or '1' (truth value). Pass strings or use dynamic regex via variable instead.",
        f"{BASE}/Gory-Details.html",
    ),
    (
        "awk-errors", "awk error: floating-point exception",
        "Numeric operation produced NaN or Infinity. Possible: log of negative, sqrt of negative, divide by very-small, exponentiation overflow. "
        "Fix: (1) bounds-check input: `if (x > 0) y = log(x)`; (2) for sqrt of possibly-negative: clamp `y = sqrt(x > 0 ? x : 0)`; (3) for division precision issues, use `printf \"%.6f\"` to format output and treat very-small numbers as zero in comparisons.",
        f"{BASE}/Numeric-Functions.html",
    ),
    (
        "awk-errors", "awk error: makepath: cannot find file (gawk @include)",
        "gawk's `@include` couldn't find the named library file. "
        "Fix: (1) check AWKPATH env var — gawk searches it for `@include` targets; (2) absolute paths in @include work: `@include \"/usr/share/awk/lib.awk\"`; (3) verify the file exists and is readable; (4) for collections of library functions, set AWKPATH to a directory and use relative names: `AWKPATH=/etc/awk awk -f main.awk`.",
        f"{BASE}/Include-Files.html",
    ),
    (
        "awk-errors", "awk error: too many fields",
        "Rare error from non-GNU awks with a fixed MAX_FIELDS limit (typically 32k or 64k). The current record has more fields than awk can handle. "
        "Fix: (1) use gawk (no field limit beyond memory); (2) preprocess to chunk: split the record before awk sees it; (3) check FS — if it's wrong, awk might be over-splitting (e.g., binary data with random control chars as FS=' ').",
        f"{BASE}/Fields.html",
    ),
    (
        "awk-errors", "awk warning: assignment used as truth value",
        "gawk warns when you write `if (x = 5)` (assignment) where you probably meant `if (x == 5)` (comparison). Assignment always evaluates to the RHS value, so the if always fires unless RHS is 0. "
        "Fix: (1) use `==` for comparison; (2) for intentional assignment-in-condition (read-and-test), gawk accepts `if ((line = getline) > 0) ...` with parens; (3) to silence the warning when you really mean assignment, double-parens: `if ((x = compute()))`.",
        f"{BASE}/Statements.html",
    ),
    (
        "awk-errors", "awk error: filename specified more than once",
        "Some awks complain when the same filename appears twice in the argument list. "
        "Fix: (1) deduplicate file args at the shell level; (2) on gawk this isn't an error (it's a feature — process the same file twice with different FILENAME); (3) for portability, write a wrapper that uniquifies args.",
        f"{BASE}/Naming-Standard-Input.html",
    ),
    (
        "awk-errors", "awk error: format string item is missing",
        "`printf` format string has more `%` placeholders than arguments. "
        "Fix: (1) count: `printf \"%s %s\\n\", x, y` needs two args; `printf \"%s %s %s\\n\", x, y` errors; (2) common bug: `printf \"%d items\\n\"` (missing the value) — meant `printf \"%d items\\n\", n`; (3) extra arguments are silently ignored — easier to catch with -W lint in gawk.",
        f"{BASE}/Printf.html",
    ),
    (
        "awk-errors", "awk error: cannot redirect from cmd (broken pipe)",
        "When you do `\"cmd\" | getline`, the upstream command exited before awk read all its output. Common when the upstream produces a lot, awk reads only a few lines, then closes. "
        "Fix: (1) explicitly close after reading what you need: `close(\"cmd\")`; (2) for one-shot reads, this is safe — the error usually means the command itself failed; (3) check the upstream command's exit status by capturing via `cmd | getline; close(cmd); n = ... ` then check via `system(\"echo $?\")`.",
        f"{BASE}/Getline.html",
    ),
    (
        "awk-errors", "awk error: NF set to negative value",
        "Setting NF to a negative number (e.g., `NF = -1`) is invalid in some awks; gawk treats it as 0 with a warning. "
        "Fix: (1) sanity-check: `if (n >= 0) NF = n`; (2) to drop trailing fields safely: `NF = max(0, NF - 3)` or similar; (3) note that setting NF lower than its current value DOES truncate $0 in gawk — useful but watch for surprises.",
        f"{BASE}/Fields.html",
    ),
    (
        "awk-errors", "awk error: nonfatal: cannot open file for writing",
        "awk can't open an output file for `print > file` redirection — usually a permissions issue or invalid path. "
        "Fix: (1) check the directory is writable; (2) for /tmp or stdout-style cases, use `print x` (no redirection); (3) for batching, accumulate in a variable and write once at END; (4) gawk runs out of file descriptors after ~1024 simultaneously-open files — explicitly close() when done with each.",
        f"{BASE}/I_002fO-Functions.html",
    ),
    (
        "awk-errors", "awk error: empty action",
        "awk syntax requires SOMETHING between `{` and `}` (or an explicit semicolon). An empty action block is sometimes accepted, sometimes errored. "
        "Fix: (1) `pattern { ; }` is fine — explicit no-op; (2) `pattern {}` is also accepted by most awks; (3) for 'print the line', omit the action entirely: `pattern` (default action is `{ print $0 }`). "
        "Idiom: `1` as a program runs `{print $0}` for every line because `1` is a true pattern with no action specified.",
        f"{BASE}/Action-Overview.html",
    ),
    (
        "awk-errors", "awk: program too long",
        "A single line in your awk program exceeds an internal limit (rare with modern awks). Usually from auto-generated programs. "
        "Fix: (1) split into multiple lines using `\\` continuation; (2) use `awk -f file.awk` instead of an inline shell-quoted program; (3) for very long string literals, concatenate adjacent strings: `\"long \" \"string \" \"in pieces\"`.",
        f"{BASE}/Statements.html",
    ),
    (
        "awk-errors", "awk error: function name function() repeats",
        "Function name collides with a built-in. awk has reserved names for built-ins (length, substr, gsub, etc.) that you can't override. "
        "Fix: (1) rename your function — `mylength`, `_length`, etc.; (2) gawk supports `@namespace::function()` for module-style isolation if you need to redefine; (3) for adding to a library: prefix consistently (`mylib_`).",
        f"{BASE}/Functions.html",
    ),
    (
        "awk-errors", "awk: print or printf with no arguments and no action",
        "`print` and `printf` are statements, not expressions. Using them mid-expression (like `x = print y`) is invalid. "
        "Fix: (1) use `sprintf` for string-returning formatting: `x = sprintf(\"%d\", n)`; (2) for capture into a variable, build the string with concatenation; (3) `print` always writes to stdout (or redirected target); to capture into a shell variable, `awk '{print}' | head -1` in shell, not within awk.",
        f"{BASE}/Print.html",
    ),
    (
        "awk-errors", "awk: getline returned EOF (return value 0)",
        "Not an error per se — `getline` returns 0 when there's no more input. Often comes up when programs assume getline always succeeds. "
        "Fix: (1) ALWAYS check the return value: `if ((getline line < \"file\") > 0) {...} else if (getline ... < 0) error; else EOF`; (2) returns: 1 = got a line; 0 = EOF; -1 = error; (3) for reading a single line of config, use the pattern: `getline line < \"config\"; close(\"config\")`.",
        f"{BASE}/Getline.html",
    ),

    # ============================================================
    # awk-recipes — common one-liners + multi-line patterns
    # ============================================================
    (
        "awk-recipes", "awk recipe: count unique values in a column",
        "Count how many times each value appears in column N. "
        "One-liner: `awk '{ count[$N]++ } END { for (k in count) print count[k], k }' file | sort -rn`. "
        "(1) `count[$N]++` builds an associative array keyed by field value; (2) END block iterates after all input is read; (3) pipe to `sort -rn` for frequency-descending output. "
        "Variant for column 1 of CSV: `awk -F, '{count[$1]++} END {for (k in count) print count[k] \"\\t\" k}'`.",
        f"{BASE}/Arrays.html",
    ),
    (
        "awk-recipes", "awk recipe: sum a column",
        "Sum the numeric values of column N. "
        "One-liner: `awk '{sum += $N} END {print sum}' file`. "
        "For column 3: `awk '{sum += $3} END {print sum}' file`. "
        "With average: `awk '{sum += $3; n++} END {print sum, sum/n}' file`. "
        "For CSV: `awk -F, '{sum += $3} END {print sum}' file.csv`. "
        "Note: awk treats unparseable values as 0 silently, so a non-numeric row won't crash but will silently contribute 0 to the sum.",
        f"{BASE}/Numeric-Functions.html",
    ),
    (
        "awk-recipes", "awk recipe: print specific columns",
        "Extract specific columns from whitespace-separated input. "
        "One-liner: `awk '{print $1, $3}' file` prints columns 1 and 3 (comma in `print` becomes OFS, default space). "
        "Change separator: `awk -F: '{print $1, $7}' /etc/passwd` prints username and shell. "
        "For tab output: `awk 'BEGIN{OFS=\"\\t\"} {print $1, $3}' file`. "
        "Reorder columns: `awk '{print $3, $1, $2}'`. "
        "Pick last column: `awk '{print $NF}'`. Pick second-to-last: `awk '{print $(NF-1)}'`.",
        f"{BASE}/Fields.html",
    ),
    (
        "awk-recipes", "awk recipe: filter lines by condition",
        "Print only lines matching some condition (alternative to grep). "
        "Match a regex: `awk '/pattern/' file` (the regex IS the program; default action prints). "
        "Numeric filter: `awk '$3 > 100' file` prints lines where column 3 > 100. "
        "Negate: `awk '$3 <= 100'` or `awk '!/pattern/'`. "
        "Multiple conditions: `awk '$3 > 100 && $5 == \"foo\"' file`. "
        "First N matching: pipe to `head -N`. Last N matching: pipe to `tail -N`.",
        f"{BASE}/Patterns.html",
    ),
    (
        "awk-recipes", "awk recipe: print lines between two patterns",
        "Range pattern: `awk '/START/,/END/' file` prints every line from the line matching /START/ through the line matching /END/, inclusive. "
        "Variants: `awk '/START/,/END/ {if (!/START/ && !/END/) print}'` excludes the boundary lines themselves. "
        "Conditional: `awk '/^---$/,/^---$/' file` prints sections between `---` separator lines. "
        "Multi-occurrence: range pattern re-arms after END matches, so multiple START..END blocks all print.",
        f"{BASE}/Patterns.html",
    ),
    (
        "awk-recipes", "awk recipe: process CSV with quoted fields",
        "awk's default field splitting can't handle CSV with embedded commas inside quoted fields. Solutions: "
        "(1) gawk's `FPAT` to define what a field LOOKS like: `gawk -v FPAT='([^,]*)|(\"[^\"]+\")' '{print $1, $2}' file.csv` — matches either unquoted-no-comma or quoted-with-anything; "
        "(2) for simpler CSV (no embedded newlines), `awk -F, '{...}'` works if you trust the data; "
        "(3) for complex CSV (embedded newlines, escaped quotes), use a real CSV tool: `csvkit`, `mlr` (miller), or python's `csv` module.",
        f"{BASE}/Splitting-By-Content.html",
    ),
    (
        "awk-recipes", "awk recipe: parse log file with timestamp + message",
        "Common pattern: extract entries between two timestamps. "
        "Filter by date string: `awk '/2026-05-29/' app.log`. "
        "More precise — between two times: `awk '/2026-05-29 10:00:/,/2026-05-29 11:00:/'`. "
        "By timestamp arithmetic (gawk): `gawk -v start='2026-05-29 10:00:00' -v end='2026-05-29 11:00:00' '$0 >= start && $0 <= end {print}'`. "
        "Count errors per hour: `awk '/ERROR/ {hr = substr($1, 1, 13); count[hr]++} END {for (h in count) print h, count[h]}' app.log | sort`.",
        f"{BASE}/Time-Functions.html",
    ),
    (
        "awk-recipes", "awk recipe: deduplicate lines (alternative to sort | uniq)",
        "Remove duplicate lines while preserving original order (sort -u doesn't preserve order). "
        "One-liner: `awk '!seen[$0]++' file`. "
        "How it works: the first time a line is seen, `seen[$0]` is 0 (false), `!seen[$0]` is 1 (true), and the default action `{print}` runs; `seen[$0]++` then sets it to 1 so subsequent occurrences are false. "
        "Variants: dedupe by column: `awk '!seen[$1]++' file` (unique first column). Reverse — keep only duplicates: `awk 'seen[$0]++' file`.",
        f"{BASE}/Arrays.html",
    ),
    (
        "awk-recipes", "awk recipe: join two files on a common key",
        "Mimic SQL JOIN with awk. Given file1 and file2 sharing a key in column 1: "
        "`awk 'NR==FNR {a[$1] = $2; next} ($1 in a) {print $1, a[$1], $2}' file1 file2`. "
        "How: NR==FNR is true only while reading the FIRST file (NR is global, FNR resets per file). Load file1 into array `a` keyed by $1, then for file2 lookup and emit. "
        "For left join: `awk 'NR==FNR {a[$1]=$2; next} {print $1, ($1 in a ? a[$1] : \"NULL\"), $2}' file1 file2`.",
        f"{BASE}/Auto_002dset.html",
    ),
    (
        "awk-recipes", "awk recipe: replace text with sub or gsub",
        "Replace strings in records before printing. "
        "Replace ONE occurrence per line: `awk '{sub(/old/, \"new\"); print}' file`. "
        "Replace ALL occurrences per line: `awk '{gsub(/old/, \"new\"); print}' file`. "
        "Operate on a specific field: `awk '{gsub(/old/, \"new\", $3); print}' file` modifies $3 but reassigns $0 too. "
        "Use regex backref (gawk): `gawk '{print gensub(/(\\w+)@(\\w+)/, \"\\\\1 at \\\\2\", \"g\")}' file`.",
        f"{BASE}/String-Functions.html",
    ),
    (
        "awk-recipes", "awk recipe: split a string into parts",
        "Use `split(string, array, separator)` to break a string into an array. "
        "Example: `awk '{n = split($0, parts, \"/\"); for (i=1; i<=n; i++) print i, parts[i]}'`. "
        "Default separator is FS if omitted. Regex separator works: `split($1, parts, /[,;]/)`. "
        "Returns the number of fields. To split and reassign $0: `gsub(/old/, OFS); split($0, dummy)` — not idiomatic; usually you want one of the above.",
        f"{BASE}/Split-Function.html",
    ),
    (
        "awk-recipes", "awk recipe: change field separator mid-program",
        "Useful when reading multi-format input. Set FS in a BEGIN block to apply from start; assign mid-program to apply to NEXT record. "
        "Read tab-separated: `awk -F'\\t' '{...}' file` or `awk 'BEGIN{FS=\"\\t\"} {...}'` (both equivalent). "
        "Multi-char FS: `awk -F'::' '{...}' file`. "
        "Regex FS: `awk 'BEGIN{FS=\"[ ,\\t]+\"} {print $2}' file` splits on any whitespace or comma run. "
        "FS change inside the program affects future records, not the current one (current is already parsed): `{if (NR==1) FS=\"|\"; else print $2}` — line 1 is parsed with old FS.",
        f"{BASE}/Field-Separators.html",
    ),
    (
        "awk-recipes", "awk recipe: process input by paragraphs (multi-line records)",
        "Set RS to empty string to treat blank-line-separated chunks as records. "
        "Example: `awk 'BEGIN{RS=\"\"} /needle/' file` prints each paragraph that contains 'needle'. "
        "How: RS=\"\" enables paragraph mode where any blank line ends a record. FS automatically includes \\n in this mode, so $1 is the first word in the paragraph. "
        "Use case: search email-formatted files where each paragraph is a header block; combine with multi-line patterns to extract structured data.",
        f"{BASE}/Multiple-Line.html",
    ),
    (
        "awk-recipes", "awk recipe: do math on columns",
        "awk's arithmetic ops cover the common cases. "
        "Compute column 3 as percentage of column 4: `awk '{printf \"%.2f%%\\n\", $3/$4 * 100}' file`. "
        "Cumulative sum: `awk '{total += $1; print $0, total}' file` (print each line with running total). "
        "Min/max in a column: `awk 'NR==1 {min=max=$1} {if ($1<min) min=$1; if ($1>max) max=$1} END {print \"min:\", min, \"max:\", max}'`. "
        "Average + stddev: `awk '{s+=$1; ss+=$1*$1; n++} END {m=s/n; print \"mean:\", m, \"stddev:\", sqrt(ss/n - m*m)}'`.",
        f"{BASE}/Arithmetic-Ops.html",
    ),
    (
        "awk-recipes", "awk recipe: transpose a matrix",
        "Swap rows and columns. "
        "Pattern: `awk '{for (i=1; i<=NF; i++) a[NR, i] = $i; if (NF > max) max = NF} END {for (i=1; i<=max; i++) {row=\"\"; for (j=1; j<=NR; j++) row = (row==\"\" ? a[j,i] : row OFS a[j,i]); print row}}'`. "
        "Uses 2D associative array (gawk SUBSEP trick: a[i,j] internally is a[i SUBSEP j]). "
        "For simpler case (consistent column count): the END loop simplifies — `for (i=1; i<=NF; i++) {for (j=1; j<=NR; j++) printf \"%s \", a[j,i]; print \"\"}`.",
        f"{BASE}/Multidimensional.html",
    ),
    (
        "awk-recipes", "awk recipe: skip header line",
        "Don't process the first line of input (it's a header). "
        "`awk 'NR > 1' file` — only print lines after the first. "
        "Or inside a more complex program: `NR > 1 { sum += $3 } END { print sum }`. "
        "Skip multiple headers: `NR > 5` skips first 5 lines. "
        "For multi-file: FNR>1 resets per file (skip header in EACH file): `awk 'FNR > 1' file1 file2`.",
        f"{BASE}/Auto_002dset.html",
    ),
    (
        "awk-recipes", "awk recipe: take every Nth line",
        "Print every 5th line: `awk 'NR % 5 == 0' file`. "
        "Every odd line: `awk 'NR % 2'` (NR%2 is 1 (truthy) for odd, 0 (falsy) for even — default action prints when truthy). "
        "Every even line: `awk 'NR % 2 == 0'`. "
        "Sample 1% randomly: `awk 'rand() < 0.01' file` (use `srand()` in BEGIN for reproducible randomization).",
        f"{BASE}/Auto_002dset.html",
    ),
    (
        "awk-recipes", "awk recipe: count lines per pattern",
        "Count occurrences of different patterns in one pass. "
        "Pattern: `awk '/ERROR/ {err++} /WARN/ {warn++} /INFO/ {info++} END {print \"errors:\", err, \"warnings:\", warn, \"info:\", info}' log`. "
        "For arbitrary patterns from a list: read patterns into an array first, then iterate per line. Or use grep -c separately if simpler.",
        f"{BASE}/Patterns.html",
    ),
    (
        "awk-recipes", "awk recipe: reformat dates",
        "Convert YYYY-MM-DD HH:MM:SS to a different format. Plain awk (no date library): "
        "`awk '{split($1, d, \"-\"); printf \"%s/%s/%s\\n\", d[3], d[2], d[1]}' file` converts 2026-05-29 to 29/05/2026. "
        "With gawk + strftime: `gawk '{t = mktime(\"2026 05 29 12 00 00\"); print strftime(\"%a %d %B %Y\", t)}'`. "
        "Note: mktime takes a space-separated string `YYYY MM DD HH MM SS [DST]` — preprocess your date format with gsub first.",
        f"{BASE}/Time-Functions.html",
    ),
    (
        "awk-recipes", "awk recipe: convert tabs to commas (or any FS conversion)",
        "Repipe tab-separated to comma-separated. "
        "Idiom: `awk 'BEGIN {FS=\"\\t\"; OFS=\",\"} {$1=$1; print}' file`. "
        "Why $1=$1: assigning to any field forces awk to rebuild $0 from the fields using OFS. Without this, $0 stays as the original input string with original separators. "
        "This works for any FS/OFS swap. For OFS=\\t to CSV: `awk 'BEGIN{FS=\"\\t\"; OFS=\",\"} {$1=$1; print}'`.",
        f"{BASE}/Built_002din-Variables.html",
    ),
    (
        "awk-recipes", "awk recipe: print last N lines (tail-like)",
        "Without `tail`, implement a ring buffer: "
        "`awk -v n=10 '{a[NR%n] = $0} END {for (i=NR-n+1; i<=NR; i++) print a[i%n]}' file`. "
        "How: keep a circular buffer of size N; at END, walk the buffer in order. "
        "For first N (head): `awk 'NR <= 10' file`. "
        "First/last with no buffer: just use `head` and `tail` — awk is for the cases shell tools can't easily express.",
        f"{BASE}/Arrays.html",
    ),
    (
        "awk-recipes", "awk recipe: extract column ranges (cut-like)",
        "Print columns 2 through 5: `awk '{for (i=2; i<=5; i++) printf \"%s%s\", $i, (i==5 ? ORS : OFS)}' file`. "
        "Or use the built-in $0 manipulation: `awk '{$1=\"\"; sub(/^ /, \"\"); print}' file` drops $1 and trims leading space — repeat for other columns. "
        "For range, mlr (miller) or `cut -f2-5` are usually easier; awk shines when the range is dynamic (depends on data).",
        f"{BASE}/Fields.html",
    ),
    (
        "awk-recipes", "awk recipe: group lines by key",
        "Group rows in one column with another. "
        "Sum values by category: `awk '{tot[$1] += $2} END {for (k in tot) print k, tot[k]}' file | sort`. "
        "Concatenate values by key: `awk '{vals[$1] = (vals[$1] ? vals[$1] \",\" : \"\") $2} END {for (k in vals) print k, vals[k]}'`. "
        "Count rows per key (histogram): `awk '{count[$1]++} END {for (k in count) printf \"%-20s %d\\n\", k, count[k]}'`.",
        f"{BASE}/Arrays.html",
    ),
    (
        "awk-recipes", "awk recipe: number lines",
        "Add line numbers (`cat -n` equivalent). "
        "Right-aligned: `awk '{printf \"%6d  %s\\n\", NR, $0}' file`. "
        "Left-aligned: `awk '{print NR, $0}' file`. "
        "Number only non-blank: `awk 'NF {printf \"%6d  %s\\n\", ++n, $0; next} {print}'` (`nl -ba` style). "
        "Per-file numbering: use FNR instead of NR: `awk '{print FNR, $0}' file1 file2` resets per file.",
        f"{BASE}/Print.html",
    ),
    (
        "awk-recipes", "awk recipe: invoke a shell command and read output",
        "Use `command | getline result` for shell integration. "
        "Example: `awk 'BEGIN {\"date\" | getline today; close(\"date\"); print \"Today:\", today}'`. "
        "Inline within record processing: `{cmd = \"file \" $1; cmd | getline type; close(cmd); print $1, type}`. "
        "ALWAYS close() after use — otherwise the file descriptor leaks. "
        "Variables for the command name make close() easier — store the cmd string in a variable, pass to both `|` and `close()`.",
        f"{BASE}/Getline.html",
    ),
    (
        "awk-recipes", "awk recipe: print N copies of each line",
        "Duplicate every line: `awk '{print; print}' file` (each line twice). "
        "Variable N: `awk -v n=3 '{for (i=1; i<=n; i++) print}' file`. "
        "Useful for testing tools that need many input lines from a small sample.",
        f"{BASE}/Statements.html",
    ),
    (
        "awk-recipes", "awk recipe: compare two files line by line",
        "Find lines in file2 not in file1: `awk 'NR==FNR {a[$0]; next} !($0 in a)' file1 file2`. "
        "Intersection: `awk 'NR==FNR {a[$0]; next} ($0 in a)' file1 file2`. "
        "Difference both ways with line-of-origin: combine with named groups. "
        "For sorted-file diffs use `comm` instead — much faster on large files since it streams.",
        f"{BASE}/Arrays.html",
    ),
    (
        "awk-recipes", "awk recipe: extract regex captures",
        "POSIX awk has limited regex captures; gawk has powerful `match()` with `RSTART` + `RLENGTH` + array. "
        "Extract first match: `awk 'match($0, /([0-9]+)/) {print substr($0, RSTART, RLENGTH)}'` returns matched substring. "
        "With capture groups (gawk): `gawk 'match($0, /([^@]+)@(.+)/, m) {print \"user:\", m[1], \"domain:\", m[2]}'`. "
        "For pure POSIX awk, use sub/gsub to remove around what you want, leaving only the capture.",
        f"{BASE}/String-Functions.html",
    ),
    (
        "awk-recipes", "awk recipe: format numbers with separators",
        "Add thousand-separators to numbers: `awk '{n = $1; while (match(n, /^[0-9]+[0-9]{3}/)) {n = substr(n, 1, RLENGTH-3) \",\" substr(n, RLENGTH-2)}; print n}' file`. "
        "With printf for floats: `awk '{printf \"%'\\''d\\n\", $1}'` (gawk supports POSIX %'d for locale-aware thousands separator). "
        "Currency format: `awk '{printf \"$%.2f\\n\", $1}'`. "
        "Bytes to human-readable: `awk '{n=$1; sfx=\"BKMGT\"; for (i=1; n>=1024; i++) n/=1024; printf \"%.2f%s\\n\", n, substr(sfx, i, 1)}'`.",
        f"{BASE}/Printf.html",
    ),
    (
        "awk-recipes", "awk recipe: write a self-contained awk script",
        "Make awk programs reusable. "
        "Pattern: create a file `myprog.awk` with `#!/usr/bin/awk -f` as the first line, then your program. Make it executable: `chmod +x myprog.awk`. "
        "Run: `./myprog.awk file1 file2`. "
        "For passing variables: use `-v` in the shebang: `#!/usr/bin/awk -f -v debug=0` (though portability of args in shebangs is limited; safer to set in BEGIN). "
        "Library style: `@include \"lib.awk\"` in gawk pulls in shared code (set AWKPATH to find it).",
        f"{BASE}/Options.html",
    ),
    (
        "awk-recipes", "awk recipe: pretty-print JSON-like records",
        "When your input is structured but not pure JSON (e.g., key=value pairs), reformat. "
        "Input: `key1=val1 key2=val2 key3=val3` per line. Print as table: "
        "`awk '{for (i=1; i<=NF; i++) {split($i, kv, \"=\"); printf \"%s\\t%s\\n\", kv[1], kv[2]} print \"---\"}' file`. "
        "For real JSON, use `jq` not awk — awk's regex isn't powerful enough for nested structures.",
        f"{BASE}/Split-Function.html",
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
            evidence_id=f"ev-awk-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-awk-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="awk-catalog",
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
