"""Direct-insert sed error + recipe claims."""

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

BASE = "https://www.gnu.org/software/sed/manual"

CLAIMS = [
    # ============================================================
    # sed-errors — 30 common errors
    # ============================================================
    (
        "sed-errors", "sed error: -e expression #1, char N: unknown command",
        "sed didn't recognize the command character at column N. "
        "Fix: (1) check valid commands: s/p/d/q/i/a/c/r/w/n/N/D/P/G/H/g/h/b/t/x/y/=/l plus GNU extensions (T/F/Q/z/e); "
        "(2) common typo: `sed 's/a/b'` (missing closing delimiter) — sed reads next char as the next command; "
        "(3) for shell quoting: use single quotes to prevent shell interpretation: `sed 's/$foo/bar/'`; "
        "(4) check delimiter consistency: if you used `/` to start an `s` command, you must close with `/` (or pick another like `|`).",
        f"{BASE}/html_node/sed-commands-list.html",
    ),
    (
        "sed-errors", "sed error: unterminated `s' command",
        "Missing closing delimiter in substitution. "
        "Fix: (1) basic syntax: `s/pattern/replacement/flags` — three delimiters minimum; "
        "(2) if pattern or replacement contains `/`: switch delimiter — `s|/path/foo|/new/path|` (the char right after `s` is the delimiter); "
        "(3) for multi-line in shell: use double-quoted with continuation OR a script file; "
        "(4) common typo: `sed 's/a/b'` missing final `/`.",
        f"{BASE}/html_node/The-_0022s_0022-Command.html",
    ),
    (
        "sed-errors", "sed error: extra characters after command",
        "sed found unexpected text after a command that should have ended. "
        "Fix: (1) common: `s/a/b/g extra` — anything after the flags is parsed as next command; "
        "(2) for multiple commands: separate with `;` OR `-e`: `sed -e 's/a/b/' -e 's/c/d/'` or `sed 's/a/b/; s/c/d/'`; "
        "(3) for command groups: `{ ... }` braces required; "
        "(4) GNU sed quirks: some BSD sed implementations don't accept `;` separator — use `-e`.",
        f"{BASE}/html_node/Common-Commands.html",
    ),
    (
        "sed-errors", "sed error: can't read <file>: No such file or directory",
        "Input file doesn't exist or wrong path. "
        "Fix: (1) verify: `ls -la <file>`; "
        "(2) for stdin: omit filename, pipe in: `cat file.txt | sed '...'`; "
        "(3) for multiple files: list them: `sed 's/a/b/' file1.txt file2.txt` (sed processes each then concatenates output); "
        "(4) for relative paths inside `r` (read) or `w` (write) commands: relative to cwd, not script.",
        f"{BASE}/html_node/Invoking-sed.html",
    ),
    (
        "sed-errors", "sed error: macOS BSD sed requires '' after -i for in-place",
        "macOS / BSD sed has different `-i` syntax than GNU sed. "
        "Fix: (1) BSD/macOS: `sed -i '' 's/a/b/' file.txt` (empty string = no backup); "
        "(2) GNU: `sed -i 's/a/b/' file.txt` (no `''`); "
        "(3) with backup: `sed -i.bak 's/a/b/' file.txt` (works on both); "
        "(4) for portability: GNU sed on Mac via `brew install gnu-sed` then use `gsed`; "
        "(5) for scripts: detect OS and branch.",
        f"{BASE}/html_node/Invoking-sed.html",
    ),
    (
        "sed-errors", "sed error: 1: \"...\": invalid command code (macOS BSD sed)",
        "BSD sed rejects some constructs GNU sed accepts — often `;` between commands, `\\n` in replacement, or extended regex shortcuts. "
        "Fix: (1) use `-e` instead of `;`: `sed -e 's/a/b/' -e 's/c/d/' file.txt`; "
        "(2) for literal newline in replacement: use `\\` + actual newline: `sed 's/foo/bar\\\\\\nbaz/' file.txt`; "
        "(3) for extended regex: `-E` (both GNU and BSD support it); "
        "(4) for full GNU compatibility: install gnu-sed.",
        f"{BASE}/html_node/Common-Commands.html",
    ),
    (
        "sed-errors", "sed error: Unmatched `{' (no closing brace)",
        "Command group started with `{` but no matching `}`. "
        "Fix: (1) every `{ ... }` block needs a `}` on its own (in many implementations); "
        "(2) modern GNU sed accepts `{...}` on one line: `sed '/start/,/end/{s/old/new/;p}' file`; "
        "(3) for BSD/POSIX: separate with `\\n` or `-e`: `sed -e '/foo/,/bar/{' -e 's/a/b/' -e '}' file`; "
        "(4) check nested braces.",
        f"{BASE}/html_node/Common-Commands.html",
    ),
    (
        "sed-errors", "sed error: unknown option to `s' (bad flag)",
        "Flag at end of substitution command isn't recognized. "
        "Fix: (1) valid s flags: `g` (global), `p` (print), `<N>` (Nth match), `i`/`I` (case-insensitive, GNU), `e` (execute, GNU), `M` (multiline, GNU); "
        "(2) common typo: `s/a/b/gi` works in GNU but BSD may require `s/a/b/Ig` order; "
        "(3) for write to file: `s/a/b/w file.txt` — `w` and filename must be the last component; "
        "(4) BSD sed: `i` flag for case-insensitive may not be supported — use `[Aa][Bb]` patterns instead.",
        f"{BASE}/html_node/The-_0022s_0022-Command.html",
    ),
    (
        "sed-errors", "sed error: -E vs -r difference (GNU vs BSD ERE flag)",
        "Extended regex flag is `-E` (POSIX, both GNU and BSD) or `-r` (GNU only, deprecated). "
        "Fix: (1) use `-E` everywhere — works on both; "
        "(2) ERE differences from BRE: `+`, `?`, `|`, `()`, `{n,m}` are literal in BRE but special in ERE; "
        "(3) for portability: stick to BRE (no flag needed) when possible; "
        "(4) for richer regex (PCRE-like): use `perl -pe` instead of sed.",
        f"{BASE}/html_node/BRE-vs-ERE.html",
    ),
    (
        "sed-errors", "sed error: backreference \\1 doesn't capture expected text",
        "Subexpression groupings in BRE require `\\(...\\)`; in ERE use plain `(...)`. "
        "Fix: (1) BRE (default): `sed 's/\\([a-z]*\\)/[\\1]/' file`; "
        "(2) ERE (`-E`): `sed -E 's/([a-z]*)/[\\1]/' file`; "
        "(3) greedy by default: `*` and `+` match longest; for shortest match use a more restrictive class: `[^/]*`; "
        "(4) max 9 backreferences: \\1 through \\9; "
        "(5) `&` in replacement refers to the whole match.",
        f"{BASE}/html_node/Back_002dreferences-and-Subexpressions.html",
    ),
    (
        "sed-errors", "sed error: greedy regex matches too much",
        "`.*` consumes from first match to last possible match on the line. "
        "Fix: (1) use a negated class: `[^/]*` matches everything-not-slash (greedy within constraint); "
        "(2) anchor: `^pattern` or `pattern$` constrains start/end; "
        "(3) sed regex doesn't support non-greedy (`*?`) — that's a Perl/PCRE feature; "
        "(4) for non-greedy semantics: pipe to `grep -o` or `awk` or `perl -pe`.",
        f"{BASE}/html_node/Regular-Expressions.html",
    ),
    (
        "sed-errors", "sed error: anchors ^ and $ don't work as expected with N command",
        "After `N` joins lines into pattern space, the `\\n` becomes literal — `^` only matches the very start of pattern space, `$` only the very end. "
        "Fix: (1) use `M` flag for multiline mode: `sed -nE 's/^foo/bar/M' file` matches start of any line in pattern space; "
        "(2) for explicit per-line: match `\\nfoo` instead of `^foo`; "
        "(3) for end of any line: match `bar\\n` instead of `bar$`; "
        "(4) for cross-line operations: it's often easier to use awk or perl.",
        f"{BASE}/html_node/regexp-extensions.html",
    ),
    (
        "sed-errors", "sed error: branch to undefined label",
        "`b` or `t` command references a label not defined in the script. "
        "Fix: (1) define label with `:name` before the branch: `sed ':a;s/  /\\t/;t a' file` (loops until no more); "
        "(2) label scope: within the current script; "
        "(3) common typo: `:a` (define) vs `b a` (branch) — name must match exactly; "
        "(4) labels can be reused (multiple branches to same label).",
        f"{BASE}/html_node/Programming-Commands.html",
    ),
    (
        "sed-errors", "sed error: delimiter conflict in s/// (slash inside pattern)",
        "Slashes in pattern or replacement conflict with the default delimiter. "
        "Fix: (1) use a different delimiter: `s|/path/foo|/new/path|`; "
        "(2) any printable char works: `s,foo,bar,`, `s#foo#bar#`, `s_foo_bar_`; "
        "(3) escape the slash: `s/\\/path/\\/new\\/path/` (ugly); "
        "(4) for addresses too: `\\#/var/log#d` (escape delimiter when defining a regex address with custom delim).",
        f"{BASE}/html_node/The-_0022s_0022-Command.html",
    ),
    (
        "sed-errors", "sed error: & not capturing whole match as expected",
        "`&` in replacement refers to the entire matched text. If you want a subexpression, use `\\1`. "
        "Fix: (1) `s/[0-9]+/[&]/g` wraps every number in brackets; "
        "(2) literal `&` in replacement: `\\&`; "
        "(3) for partial: parenthesize + backref: `s/(foo)bar/\\1baz/`; "
        "(4) for case-changing: GNU extensions `\\u`/`\\l`/`\\U`/`\\L`/`\\E` — uppercase next char / lowercase next / uppercase all / lowercase all / end conversion.",
        f"{BASE}/html_node/Back_002dreferences-and-Subexpressions.html",
    ),
    (
        "sed-errors", "sed error: missing command after address",
        "An address (line number or pattern) was given but no command follows. "
        "Fix: (1) address+command: `sed '5d'` deletes line 5; `sed '/foo/p'` prints lines matching `foo`; "
        "(2) range: `sed '5,10d'` deletes lines 5–10; "
        "(3) regex range: `sed '/start/,/end/d'`; "
        "(4) command-less address is an error — there's no implicit print or anything.",
        f"{BASE}/html_node/Addresses.html",
    ),
    (
        "sed-errors", "sed error: q command exit code unexpected",
        "GNU sed `q [N]` (or `Q [N]`) exits with status N; some shell pipelines depend on this. "
        "Fix: (1) for default exit 0: `sed '5q'` (quits after line 5); "
        "(2) for explicit exit code: `sed '/error/q 1'` quits with status 1 on first match; "
        "(3) BSD sed may not support `q N`; "
        "(4) for grep-like: `sed -n '/pattern/{p;q}'` prints and exits on first match.",
        f"{BASE}/html_node/Common-Commands.html",
    ),
    (
        "sed-errors", "sed error: piping output through sed loses last line if no trailing newline",
        "POSIX sed assumes line-terminated input; final line without `\\n` may be ignored on some implementations. "
        "Fix: (1) GNU sed handles this correctly; BSD/macOS may not; "
        "(2) ensure trailing newline: `echo \"$var\" | sed '...'` (echo adds newline); "
        "(3) `printf '%s\\n'` instead of `printf '%s'`; "
        "(4) for binary or unusual data: consider awk or perl which have explicit record terminators.",
        f"{BASE}/html_node/Invoking-sed.html",
    ),
    (
        "sed-errors", "sed error: character class [[:space:]] not recognized",
        "POSIX classes (`[[:space:]]`, `[[:digit:]]`, etc) work in modern sed but ancient versions may not. "
        "Fix: (1) for `[[:space:]]`: ` ` (literal space) + `\\t` may be enough; "
        "(2) alternatives: `[ \\t]` for space-or-tab; "
        "(3) for digits: `[0-9]`; "
        "(4) check version: `sed --version` (GNU) — old enough seds may not support; "
        "(5) for non-printables: `[[:cntrl:]]`, `[[:print:]]`.",
        f"{BASE}/html_node/Character-Classes-and-Bracket-Expressions.html",
    ),
    (
        "sed-errors", "sed error: perl-style regex \\d, \\w, \\s not working",
        "sed uses BRE/ERE — not PCRE. `\\d`, `\\w`, `\\s` are Perl-isms. "
        "Fix: (1) `\\d` → `[0-9]` (or POSIX `[[:digit:]]`); "
        "(2) `\\w` → `[A-Za-z0-9_]` (or `[[:alnum:]_]`); "
        "(3) `\\s` → `[[:space:]]` or `[ \\t]`; "
        "(4) `\\b` (word boundary): GNU sed supports it; BSD sed doesn't — use `\\<` and `\\>` (GNU) or workaround; "
        "(5) for full PCRE: use `perl -pe` instead.",
        f"{BASE}/html_node/regexp-extensions.html",
    ),
    (
        "sed-errors", "sed error: cannot perform substitution that requires \\n newline in replacement",
        "Embedding a real newline in replacement text. "
        "Fix: (1) GNU: `\\n` in replacement = literal newline (with `-E` or default); "
        "(2) BSD: must use `\\` + actual newline in script: `sed 's/a/b\\\\\\nc/' file` (the `\\\\\\n` is shell-escaped); "
        "(3) workaround: pipe through `tr`: `sed 's/a/bMARKERc/' | tr 'MARKER' '\\n'`; "
        "(4) for adding lines: use `a` (append) or `i` (insert) commands.",
        f"{BASE}/html_node/Common-Commands.html",
    ),
    (
        "sed-errors", "sed error: -i.bak left .bak files everywhere (cleanup confusion)",
        "`-i.bak` saves a `<file>.bak` for every modified file. "
        "Fix: (1) intentional safety net — review then delete: `find . -name '*.bak' -delete`; "
        "(2) for no backup: GNU `sed -i ''` doesn't work — use `sed -i` (no suffix); BSD `sed -i ''` (empty string); "
        "(3) for explicit backup dir: not supported natively — script around with `cp` then `sed`.",
        f"{BASE}/html_node/Invoking-sed.html",
    ),
    (
        "sed-errors", "sed error: crashes on huge single-line input",
        "Pattern space holds the whole line; multi-GB lines exhaust memory. "
        "Fix: (1) split first: `sed 's/\\(.\\{1000\\}\\)/&\\n/g' bigfile` (chunks every 1000 chars); "
        "(2) use awk with `RS` (record separator) for non-newline records; "
        "(3) for streaming binary: tool other than sed — `dd`, `head -c`, custom Python; "
        "(4) for huge input: sed is line-oriented and assumes lines fit in memory.",
        f"{BASE}/html_node/Invoking-sed.html",
    ),
    (
        "sed-errors", "sed error: shell variable inside single quotes not expanded",
        "`sed '...$var...'` — single quotes prevent shell from expanding `$var`. "
        "Fix: (1) double quotes: `sed \"s/foo/$var/\"` (vars expand, but shell metachars need escape); "
        "(2) close-open quote trick: `sed 's/foo/'$var'/g'`; "
        "(3) safer: assign to a sed variable via env: `export VAR=...; sed \"s/foo/$VAR/\"`; "
        "(4) for vars with special regex chars: escape first (`printf '%q'` in bash isn't enough — escape `/`, `&`, `\\`).",
        f"{BASE}/html_node/Invoking-sed.html",
    ),
    (
        "sed-errors", "sed error: addressing first/last line wrong",
        "Common: `1d` deletes first line (works), `$d` deletes last line (works), but `0,/pattern/d` and `,N` aren't always supported. "
        "Fix: (1) `1d` first; `$d` last; "
        "(2) `5,$d` from line 5 to end; "
        "(3) `0,/pat/` (GNU): from start up to first match — note `0,` won't include the match itself; "
        "(4) `/start/,/end/` is regex range; "
        "(5) tail/head are simpler if you just need first/last N: `head -n -1` for all-but-last-line.",
        f"{BASE}/html_node/Addresses.html",
    ),
    (
        "sed-errors", "sed error: s/a/b/Ng (Nth match plus global) doesn't work as expected",
        "`s/a/b/3` replaces only the 3rd match. `s/a/b/3g` (GNU) replaces 3rd and all subsequent. BSD sed may not support combining. "
        "Fix: (1) `s/a/b/Ng` is GNU-only — for BSD use a different approach; "
        "(2) for 'replace only first match': default (no flag) does that; "
        "(3) for 'replace last match only': counter-intuitive in sed — easier in awk.",
        f"{BASE}/html_node/The-_0022s_0022-Command.html",
    ),
    (
        "sed-errors", "sed error: file modification time preserved by accident with -i",
        "GNU sed `-i` updates mtime on rewrite; some sed versions don't preserve original timestamps. "
        "Fix: (1) if you need atime/mtime preservation: store + restore: `mtime=$(stat -c %Y file); sed -i '...' file; touch -d \"@$mtime\" file`; "
        "(2) for in-place that DOESN'T touch mtime: use `--follow-symlinks` for symlink-safe edit; "
        "(3) for atomic update: write to temp then mv (mv preserves nothing).",
        f"{BASE}/html_node/Invoking-sed.html",
    ),
    (
        "sed-errors", "sed error: hold space and pattern space confusion",
        "Two buffers: pattern space (current line) and hold space (separate storage). "
        "Common ops: `h` (overwrite hold with pattern), `H` (append pattern to hold), `g` (overwrite pattern with hold), `G` (append hold to pattern), `x` (swap). "
        "Fix: (1) for reversing file: `sed -n '1!G;h;$p' file` (classic); "
        "(2) for cross-line replacement: build up multi-line pattern with `N` (join next line); "
        "(3) for debugging hold space: temporarily `x` to see it (visible in output).",
        f"{BASE}/html_node/Programming-Commands.html",
    ),
    (
        "sed-errors", "sed error: M (multiline) flag not supported on older sed",
        "GNU sed only — for `^` and `$` to anchor each embedded line in multi-line pattern space. "
        "Fix: (1) GNU: `sed -nE 's/^foo/bar/M; p' file`; "
        "(2) BSD: no M flag — work line-by-line or use awk; "
        "(3) for complex multi-line: pre-process with `tr` or pipe through perl with `-0` (null record separator).",
        f"{BASE}/html_node/regexp-extensions.html",
    ),
    (
        "sed-errors", "sed warning: 's' command appears without text after delimiter",
        "Empty replacement: `s/foo//g` (deletes matched text) — not an error if intentional. "
        "Fix: (1) intentional delete: `s/foo//g` is the idiom; "
        "(2) `d` command deletes whole line if it matches: `/foo/d`; "
        "(3) for selective: `s/foo bar baz//g` deletes the specific phrase.",
        f"{BASE}/html_node/The-_0022s_0022-Command.html",
    ),

    # ============================================================
    # sed-recipes — 38 common workflows
    # ============================================================
    (
        "sed-recipes", "sed recipe: basic substitution",
        "Single match per line: `sed 's/foo/bar/' file`. "
        "All matches: `sed 's/foo/bar/g' file`. "
        "Case-insensitive (GNU): `sed 's/foo/bar/gi' file`. "
        "Only Nth match per line: `sed 's/foo/bar/3' file`. "
        "Only on lines matching pattern: `sed '/condition/s/foo/bar/' file`. "
        "On line range: `sed '5,10s/foo/bar/' file`.",
        f"{BASE}/html_node/The-_0022s_0022-Command.html",
    ),
    (
        "sed-recipes", "sed recipe: in-place edit (GNU and BSD)",
        "GNU: `sed -i 's/foo/bar/g' file.txt`. "
        "BSD/macOS: `sed -i '' 's/foo/bar/g' file.txt` (the empty `''` is required). "
        "With backup: `sed -i.bak 's/foo/bar/g' file.txt` (creates file.txt.bak — works on both). "
        "Cross-platform script: `sed -i.bak 's/foo/bar/g' file && rm file.bak`. "
        "Modern alternative: `perl -i -pe 's/foo/bar/g' file` (consistent across platforms).",
        f"{BASE}/html_node/Invoking-sed.html",
    ),
    (
        "sed-recipes", "sed recipe: print only matching lines (sed as grep)",
        "`sed -n '/pattern/p' file` — `-n` suppresses default print; `p` explicitly prints. "
        "Equivalent to: `grep 'pattern' file` (grep is faster). "
        "With context: `sed -n '/pattern/{N;p}' file` (line + next). "
        "Inverse (lines NOT matching): `sed -n '/pattern/!p' file` — equivalent to `grep -v`.",
        f"{BASE}/html_node/Common-Commands.html",
    ),
    (
        "sed-recipes", "sed recipe: delete lines matching pattern",
        "`sed '/pattern/d' file`. "
        "Multiple patterns: `sed -e '/foo/d' -e '/bar/d' file`. "
        "Range delete: `sed '/start/,/end/d' file` (deletes start-line through end-line inclusive). "
        "Inverse (keep only matching): `sed -n '/pattern/p' file`. "
        "By line number: `sed '5d' file` (line 5); `sed '5,10d' file` (lines 5–10); `sed '$d' file` (last line).",
        f"{BASE}/html_node/Common-Commands.html",
    ),
    (
        "sed-recipes", "sed recipe: delete blank lines",
        "Empty (zero-length): `sed '/^$/d' file`. "
        "Whitespace-only: `sed '/^[[:space:]]*$/d' file`. "
        "GNU short: `sed -E '/^\\s*$/d' file`. "
        "Squeeze multiple blanks to one: `sed '/^$/N;/^\\n$/D' file` (uses N + D for state).",
        f"{BASE}/html_node/Common-Commands.html",
    ),
    (
        "sed-recipes", "sed recipe: print specific line range",
        "`sed -n '5,10p' file` — lines 5 through 10. "
        "From start: `sed -n '1,10p' file` (or just `head -n 10`). "
        "To end: `sed -n '10,$p' file`. "
        "Single line: `sed -n '5p' file` (or `sed '5q' file` for stop-at). "
        "Print line N then quit (fast on huge files): `sed -n '5{p;q}' file`.",
        f"{BASE}/html_node/Addresses.html",
    ),
    (
        "sed-recipes", "sed recipe: insert / append / change lines",
        "Insert before line: `sed '5i\\\\\\nNEW LINE' file` (GNU; backslash + newline required by POSIX). "
        "GNU 4+: `sed '5i NEW LINE' file` (one-liner). "
        "Append after line: `sed '5a NEW LINE' file`. "
        "Change (replace) line: `sed '5c NEW LINE' file`. "
        "Before pattern match: `sed '/pattern/i NEW LINE' file`. "
        "After pattern match: `sed '/pattern/a NEW LINE' file`.",
        f"{BASE}/html_node/Common-Commands.html",
    ),
    (
        "sed-recipes", "sed recipe: extract substring with backreference",
        "Capture + replace with subgroup: `sed -E 's/.*name=\"([^\"]+)\".*/\\1/' file`. "
        "BRE: `sed 's/.*name=\"\\([^\"]*\\)\".*/\\1/' file` (escape parens). "
        "Print only the match: combine with `-n` and `p`: `sed -nE 's/.*name=\"([^\"]+)\".*/\\1/p' file`. "
        "For extracting many fields: chain with multiple `s///`.",
        f"{BASE}/html_node/Back_002dreferences-and-Subexpressions.html",
    ),
    (
        "sed-recipes", "sed recipe: strip trailing whitespace from every line",
        "`sed 's/[[:space:]]*$//' file`. "
        "Just trailing spaces (no tabs): `sed 's/ *$//' file`. "
        "In-place: `sed -i 's/[[:space:]]*$//' file`. "
        "For leading whitespace: `sed 's/^[[:space:]]*//' file`. "
        "Both leading and trailing: `sed 's/^[[:space:]]*//;s/[[:space:]]*$//' file`.",
        f"{BASE}/html_node/The-_0022s_0022-Command.html",
    ),
    (
        "sed-recipes", "sed recipe: squeeze multiple blank lines to one",
        "`sed '/^$/N;/^\\n$/D' file` — joins consecutive blanks then deletes the duplicate. "
        "GNU alternative: `cat -s file` (much simpler). "
        "Strip all blank lines: `sed '/^$/d' file`.",
        f"{BASE}/html_node/Programming-Commands.html",
    ),
    (
        "sed-recipes", "sed recipe: number lines (like cat -n)",
        "`sed = file | sed 'N;s/\\n/\\t/'` — `=` prints line number, then second sed joins with tab. "
        "Modern simpler: `cat -n file` or `nl file`. "
        "Number only matching lines: `sed -n '/pattern/{=;p}' file | sed 'N;s/\\n/ /'`.",
        f"{BASE}/html_node/Other-Commands.html",
    ),
    (
        "sed-recipes", "sed recipe: convert CRLF to LF (and reverse)",
        "Windows → Unix: `sed -i 's/\\r$//' file.txt`. "
        "Or: `dos2unix file.txt` (dedicated tool). "
        "Unix → Windows: `sed -i 's/$/\\r/' file.txt`. "
        "Or: `unix2dos file.txt`. "
        "Verify: `file file.txt` or `cat -A file.txt` (shows `^M` for `\\r`).",
        f"{BASE}/html_node/The-_0022s_0022-Command.html",
    ),
    (
        "sed-recipes", "sed recipe: convert tabs to spaces (or reverse)",
        "Tabs to 4 spaces: `sed -i 's/\\t/    /g' file`. "
        "Better tool: `expand -t 4 file > file.new && mv file.new file`. "
        "Spaces to tabs: `unexpand -t 4 file > file.new && mv file.new file`. "
        "Inline with sed: `sed -i 's/    /\\t/g' file` (but `unexpand` is smarter — only converts leading whitespace).",
        f"{BASE}/html_node/The-_0022s_0022-Command.html",
    ),
    (
        "sed-recipes", "sed recipe: multiple commands in one invocation",
        "Semicolon (GNU): `sed 's/a/b/; s/c/d/' file`. "
        "Multiple `-e` (POSIX, portable): `sed -e 's/a/b/' -e 's/c/d/' file`. "
        "Script file: `sed -f script.sed file` (one command per line in script.sed). "
        "Combined commands on one line via braces: `sed '/start/,/end/{s/old/new/;s/foo/bar/}' file`.",
        f"{BASE}/html_node/Invoking-sed.html",
    ),
    (
        "sed-recipes", "sed recipe: replace text only on specific lines",
        "By line number: `sed '5s/foo/bar/' file` — only line 5. "
        "By range: `sed '5,10s/foo/bar/' file`. "
        "By pattern: `sed '/start/,/end/s/foo/bar/' file` — between start and end markers. "
        "By condition: `sed '/^#/!s/foo/bar/' file` — only on lines NOT starting with `#`.",
        f"{BASE}/html_node/Addresses.html",
    ),
    (
        "sed-recipes", "sed recipe: comment / uncomment a config block",
        "Comment lines in a block: `sed -i '/\\[section\\]/,/^\\[/s/^/#/' config.ini` (prepend `#` from `[section]` to next `[`). "
        "Uncomment lines in a block: `sed -i '/\\[section\\]/,/^\\[/s/^#//' config.ini`. "
        "Toggle single line by pattern: `sed -i 's/^# *\\(key=value\\)/\\1/' config` (uncomment) or `sed -i 's/^\\(key=value\\)/#\\1/' config` (comment).",
        f"{BASE}/html_node/Common-Commands.html",
    ),
    (
        "sed-recipes", "sed recipe: read from file at a position (r command)",
        "Insert file contents after line 5: `sed '5r insert.txt' main.txt`. "
        "Insert after every line matching pattern: `sed '/pattern/r insert.txt' main.txt`. "
        "Insert at start: `sed '1i\\\\' main.txt && cat insert.txt main.txt` (easier with cat). "
        "For templates: combine `r` with substitution to inject blocks.",
        f"{BASE}/html_node/Programming-Commands.html",
    ),
    (
        "sed-recipes", "sed recipe: write matched lines to file (w command)",
        "Save matching lines to a separate file: `sed -n '/pattern/w matches.txt' file`. "
        "Combined with substitution: `sed 's/foo/bar/w changes.txt' file`. "
        "Multiple files based on content: ```\\nsed -n '/^ERROR/w errors.txt\\n        /^WARN/w warnings.txt\\n        /^INFO/w info.txt' log.txt\\n```. "
        "Append-mode: GNU `W` writes only first line of pattern space.",
        f"{BASE}/html_node/Programming-Commands.html",
    ),
    (
        "sed-recipes", "sed recipe: use extended regex (-E)",
        "`sed -E 's/(foo|bar)/X/g' file` — `|`, `+`, `?`, `(...)` are unescaped meta chars. "
        "Without `-E` (BRE): `sed 's/\\(foo\\|bar\\)/X/g' file` (needs escapes). "
        "POSIX (works on GNU + BSD): `-E`. "
        "Old GNU: `-r` (same as `-E` but deprecated). "
        "Inside a pattern: `\\w+` etc are NOT POSIX — see error claim about Perl-style.",
        f"{BASE}/html_node/BRE-vs-ERE.html",
    ),
    (
        "sed-recipes", "sed recipe: reverse all lines (tac equivalent)",
        "`sed -n '1!G;h;$p' file` — classic one-liner using hold space. "
        "Better tool: `tac file` (GNU) or `tail -r file` (BSD). "
        "For reversed pipeline: `cat file | sed -n '1!G;h;$p'`. "
        "On macOS without tac: `tail -r file` works.",
        f"{BASE}/html_node/Programming-Commands.html",
    ),
    (
        "sed-recipes", "sed recipe: print only first N lines",
        "First 10: `sed 10q file` (faster than `head -n 10` on huge files because sed stops reading). "
        "First match then quit: `sed '/pattern/q' file`. "
        "Up to and including the first match: `sed '/pattern/{p;q}' file` (GNU). "
        "Use case: parse only header section of a log file.",
        f"{BASE}/html_node/Common-Commands.html",
    ),
    (
        "sed-recipes", "sed recipe: print only last N lines (use tail)",
        "sed alone is awkward for last-N. Use tail: `tail -n 10 file`. "
        "For 'last line only': `sed -n '$p' file`. "
        "For 'all but last line': `sed '$d' file`. "
        "For 'lines from N to end': `sed -n 'N,$p' file`.",
        f"{BASE}/html_node/Addresses.html",
    ),
    (
        "sed-recipes", "sed recipe: case conversion (GNU only)",
        "Uppercase all: `sed 's/.*/\\U&/' file` — `\\U` starts uppercase, `&` is the whole match. "
        "Lowercase all: `sed 's/.*/\\L&/' file`. "
        "Capitalize first letter: `sed 's/.*/\\u&/' file` — `\\u` uppercases just next char. "
        "Lowercase first letter: `sed 's/.*/\\l&/' file`. "
        "Within match: `sed 's/\\(foo\\)/\\U\\1\\E/' file` — `\\E` ends conversion.",
        f"{BASE}/html_node/regexp-extensions.html",
    ),
    (
        "sed-recipes", "sed recipe: increment number with sed (awkward but possible)",
        "sed isn't designed for arithmetic. For just '+1' on integers: ```sh\\nsed -E 's/([0-9]+)/{ echo $((\\1+1)) }/e' file\\n```. "
        "Easier with awk: `awk '{ for (i=1;i<=NF;i++) if ($i ~ /^[0-9]+$/) $i++; print }' file`. "
        "For version bumps: `bumpver` tool or custom script.",
        f"{BASE}/html_node/Programming-Commands.html",
    ),
    (
        "sed-recipes", "sed recipe: join lines that match a pattern",
        "Join continuation lines ending in `\\`: `sed -E ':a; /\\\\$/{N; s/\\\\\\n//; ba}' file`. "
        "Read top-down: label `a`, if line ends with `\\` then append next + remove escaped newline + branch back to `a`. "
        "For one-liner per record (paragraph mode): `sed 'N;s/\\n/ /;P;D'`.",
        f"{BASE}/html_node/Programming-Commands.html",
    ),
    (
        "sed-recipes", "sed recipe: use sed for simple templating",
        "Replace placeholders: `sed -e \"s/{{name}}/$NAME/g\" -e \"s/{{version}}/$VERSION/g\" template.txt > output.txt`. "
        "For multiple vars: chain with `-e` or use envsubst: `NAME=Alice envsubst < template.txt`. "
        "For complex templating: use jinja2 (Python) or Mustache implementations.",
        f"{BASE}/html_node/The-_0022s_0022-Command.html",
    ),
    (
        "sed-recipes", "sed recipe: use sed in a pipeline",
        "Filter pipe: `cat file | sed 's/error/ERROR/' | grep ERROR`. "
        "Modify command output: `ls -la | sed 's/ \\+/ /g'` (collapse multi-spaces). "
        "Inside subshell: `for f in $(find . -name '*.txt' | sed 's|^\\./||'); do ...; done`. "
        "Combine with cut/awk: `ps aux | sed -n '2,$p' | awk '{print $2}'`.",
        f"{BASE}/html_node/Invoking-sed.html",
    ),
    (
        "sed-recipes", "sed recipe: substitute with shell variable",
        "Double quotes (vars expand): `sed \"s/foo/$VAR/g\" file`. "
        "Caveat: `$VAR` containing `/` or `&` or `\\` breaks. Escape first: ```sh\\nESC=$(printf '%s' \"$VAR\" | sed 's/[\\\\/&]/\\\\\\\\&/g')\\nsed \"s/foo/$ESC/g\" file\\n```. "
        "Safer for paths: change delimiter: `sed \"s|foo|$VAR|g\" file` (still need to escape `|` if it appears).",
        f"{BASE}/html_node/The-_0022s_0022-Command.html",
    ),
    (
        "sed-recipes", "sed recipe: extract just the matched portion",
        "Print matches only: `sed -nE 's/.*foo([^bar]*)bar.*/\\1/p' file`. "
        "Cleaner with grep: `grep -oE 'pattern' file`. "
        "For URL extraction: `grep -oE 'https?://[^[:space:]]+' file` is much easier than sed equivalent.",
        f"{BASE}/html_node/Back_002dreferences-and-Subexpressions.html",
    ),
    (
        "sed-recipes", "sed recipe: replace nth occurrence on each line",
        "Replace only Nth match per line: `sed 's/foo/bar/3' file` — only 3rd `foo` on each line. "
        "Replace from Nth onward (GNU): `sed 's/foo/bar/3g' file` — 3rd, 4th, 5th... all. "
        "Skip first N: `sed -E 's/(foo){2}/\\1bar/' file` (replace after 2nd occurrence).",
        f"{BASE}/html_node/The-_0022s_0022-Command.html",
    ),
    (
        "sed-recipes", "sed recipe: swap two columns separated by delimiter",
        "Swap CSV columns: `sed -E 's/^([^,]+),([^,]+),(.*)/\\2,\\1,\\3/' file.csv` — swap first two. "
        "For tab-separated: `sed -E 's/^([^\\t]+)\\t([^\\t]+)/\\2\\t\\1/' file`. "
        "For arbitrary positions: easier with `awk`: `awk -F, '{print $2,$1,$3,$4}' OFS=, file.csv`.",
        f"{BASE}/html_node/Back_002dreferences-and-Subexpressions.html",
    ),
    (
        "sed-recipes", "sed recipe: delete every Nth line",
        "Every other line (1st, 3rd, ...): `sed -n '1~2p' file` (GNU step syntax). "
        "Every 3rd line: `sed -n '1~3p' file`. "
        "Skip first 5, then every other: `sed -n '5~2p' file`. "
        "Inverse: `sed '0~2d' file` (delete every 2nd).",
        f"{BASE}/html_node/Addresses.html",
    ),
    (
        "sed-recipes", "sed recipe: print line containing pattern AND surrounding context",
        "1 line before + after: `sed -nE '/pattern/{x;p;x;p;N;p}' file` (uses hold space — gnarly). "
        "Way easier with grep: `grep -A 1 -B 1 'pattern' file`. "
        "For N lines after: `grep -A 3 'pattern' file`.",
        f"{BASE}/html_node/Programming-Commands.html",
    ),
    (
        "sed-recipes", "sed recipe: split joined lines",
        "Split on `;`: `sed 's/;/\\n/g' file`. "
        "Split on `, `: `sed 's/, /\\n/g' file`. "
        "Caveat: `\\n` in replacement is GNU — for BSD use literal newline (`\\` then newline). "
        "Alternative: `tr ';' '\\n' < file` (simpler for single-char split).",
        f"{BASE}/html_node/The-_0022s_0022-Command.html",
    ),
    (
        "sed-recipes", "sed recipe: remove ANSI color escape codes",
        "`sed -E 's/\\x1b\\[[0-9;]*m//g' colored.txt`. "
        "(For non-printable: `\\x1b` is the ESC char. `[0-9;]*m` matches the ANSI sequence.) "
        "Alternative tools: `ansi2txt`, `perl -pe 's/\\e\\[[0-9;]*m//g'`. "
        "Use case: capture colored CLI output then strip codes for grep/diff.",
        f"{BASE}/html_node/regexp-extensions.html",
    ),
    (
        "sed-recipes", "sed recipe: keep only lines between two patterns (range filter)",
        "Print: `sed -n '/start/,/end/p' file` — lines from `/start/` to `/end/` inclusive. "
        "Delete the range: `sed '/start/,/end/d' file`. "
        "Replace only in range: `sed '/start/,/end/s/foo/bar/g' file`. "
        "First occurrence only: in awk: `awk '/start/,/end/' file` (same syntax).",
        f"{BASE}/html_node/Addresses.html",
    ),
    (
        "sed-recipes", "sed recipe: production-safe in-place edit (atomic)",
        "Default `sed -i` isn't truly atomic — for safety: ```sh\\nsed 's/foo/bar/g' file > file.tmp && mv file.tmp file\\n```. "
        "Or use `--follow-symlinks` for symlink-safe edits. "
        "For team workflows: `git diff` after edit, verify changes are what you want before commit. "
        "For batch operations across many files: `find . -name '*.conf' -exec sed -i 's/foo/bar/g' {} +`.",
        f"{BASE}/html_node/Invoking-sed.html",
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
            evidence_id=f"ev-sed-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-sed-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="sed-catalog",
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
