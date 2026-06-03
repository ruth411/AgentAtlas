"""Direct-insert postgresql-psql depth claims (meta-commands, formatting, variables, scripting, errors)."""

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

URL = "https://www.postgresql.org/docs/current/app-psql.html"

CLAIMS = [
    # ============================================================
    # Meta-commands — \d family (describe / list objects)
    # ============================================================
    (
        "psql meta-command: \\l (list databases)",
        "`\\l` lists all databases on the connected cluster: name, owner, encoding, collation, ctype, access privileges. "
        "Verbose: `\\l+` adds size, tablespace, description. "
        "Pattern: `\\l prod*` filters by name pattern. "
        "Equivalent SQL: `SELECT datname FROM pg_database WHERE NOT datistemplate;`.",
    ),
    (
        "psql meta-command: \\c (connect to database)",
        "`\\c <dbname>` reconnects to a different database on the same server. "
        "Full: `\\c <dbname> <user> <host> <port>` switches all four. "
        "Reset prompt only: `\\c -` (no args reuses current). "
        "Connection info: `\\conninfo` prints current connection details.",
    ),
    (
        "psql meta-command: \\dt (list tables)",
        "`\\dt` lists tables in the current search_path. "
        "All schemas: `\\dt *.*`. "
        "Specific schema: `\\dt myschema.*`. "
        "Pattern: `\\dt orders*` matches tables starting with 'orders'. "
        "Verbose (size, description, persistence): `\\dt+`. "
        "Foreign tables only: `\\dtS+` includes system tables; modifier `+` adds details.",
    ),
    (
        "psql meta-command: \\d (describe object)",
        "`\\d` alone lists everything in current schema (tables, views, sequences, foreign tables). "
        "`\\d <name>` describes the object: columns, types, indexes, constraints, triggers. "
        "Verbose: `\\d+ <name>` adds storage, stats target, description, persistence (permanent/temporary/unlogged). "
        "Quote-sensitive: `\\d \"MyTable\"` for case-sensitive names.",
    ),
    (
        "psql meta-command: \\dn (list schemas / namespaces)",
        "`\\dn` lists schemas. "
        "Verbose: `\\dn+` adds owner, access privileges, description. "
        "Pattern: `\\dn pg_*` filters by name. "
        "Includes system schemas only with `S` modifier: `\\dnS+`. "
        "Equivalent: `SELECT * FROM information_schema.schemata;`.",
    ),
    (
        "psql meta-command: \\df (list functions)",
        "`\\df` lists functions in current search_path. "
        "Pattern: `\\df *trim*` matches functions with 'trim' in name. "
        "Verbose: `\\df+` adds source code, language, security, owner. "
        "By type: `\\dfa` (aggregates), `\\dfn` (normal), `\\dfp` (procedure), `\\dft` (trigger), `\\dfw` (window).",
    ),
    (
        "psql meta-command: \\du and \\dg (list roles / groups)",
        "`\\du` lists database roles (login + group). "
        "`\\dg` is an alias. "
        "Output: name, attributes (Superuser, Createrole, Replication, etc.), member of. "
        "Verbose: `\\du+` adds description. "
        "Equivalent: `SELECT * FROM pg_roles;`. "
        "For role membership tree: `SELECT * FROM pg_auth_members;`.",
    ),
    (
        "psql meta-command: \\dv (list views)",
        "`\\dv` lists views. "
        "Verbose: `\\dv+` shows columns + definition + size. "
        "All schemas: `\\dv *.*`. "
        "Materialized views: `\\dm` (separate command). "
        "Verbose materialized: `\\dm+` shows definition + last refresh.",
    ),
    (
        "psql meta-command: \\di (list indexes)",
        "`\\di` lists indexes (regular + unique). "
        "Verbose: `\\di+` adds size + description. "
        "By table: `\\d+ <table>` shows indexes inline with the table description. "
        "All including bloated: cross-reference with `pg_stat_user_indexes` for usage stats.",
    ),
    (
        "psql meta-command: \\ds (list sequences)",
        "`\\ds` lists sequences. "
        "Verbose: `\\ds+` includes current value, increment, max, cache. "
        "Inspect specific: `SELECT * FROM <seq_name>;` shows current state. "
        "For sequences associated with serial/identity columns: `\\d+ <table>` shows the linkage.",
    ),
    (
        "psql meta-command: \\dx (list extensions)",
        "`\\dx` lists installed extensions in current database. "
        "Verbose: `\\dx+` shows owned objects per extension. "
        "Available (not installed): `SELECT * FROM pg_available_extensions;`. "
        "Install: `CREATE EXTENSION <name>;`. "
        "Common: uuid-ossp, pg_trgm, hstore, pgcrypto, vector, postgis.",
    ),
    (
        "psql meta-command: \\dp and \\z (list privileges)",
        "`\\dp <pattern>` or `\\z <pattern>` shows access privileges on tables/views/sequences. "
        "Format: `role=privileges/grantor`. "
        "Privilege letters: `r`=SELECT, `w`=UPDATE, `a`=INSERT, `d`=DELETE, `D`=TRUNCATE, `x`=REFERENCES, `t`=TRIGGER, `*`=grant option. "
        "Empty result: object has only default ACL (owner-only).",
    ),
    (
        "psql meta-command: \\dD (list domains)",
        "`\\dD` lists user-defined domains (CREATE DOMAIN). "
        "Verbose: `\\dD+` adds check constraints + default + description. "
        "Use case: type aliases with constraints (e.g. `email`, `positive_int`).",
    ),
    (
        "psql meta-command: \\det and \\des (foreign tables + servers)",
        "`\\det` lists foreign tables. "
        "`\\des` lists foreign servers (postgres_fdw, file_fdw, etc.). "
        "`\\dew` lists foreign-data wrappers. "
        "`\\deu` lists user mappings (foreign user credentials). "
        "Verbose `+` adds details.",
    ),
    (
        "psql meta-command: \\dT (list types)",
        "`\\dT` lists data types (user-defined). "
        "Verbose: `\\dT+` shows internal name, size, owner. "
        "Includes composite types, enums, ranges, base types. "
        "For enums: `\\dT+ <enum_name>` shows the values.",
    ),
    (
        "psql meta-command: \\dy and \\dL (event triggers + procedural languages)",
        "`\\dy` lists event triggers (DDL-level triggers). "
        "`\\dL` lists installed procedural languages (plpgsql, plpython3u, plperl, etc.). "
        "Verbose `+` adds details. "
        "Default plpgsql is always present.",
    ),
    # ============================================================
    # Meta-commands — output and formatting
    # ============================================================
    (
        "psql meta-command: \\pset (output formatting)",
        "`\\pset` sets one of many output options. "
        "Format: `\\pset format aligned|unaligned|csv|html|latex|wrapped|markdown`. "
        "Border: `\\pset border 0|1|2`. "
        "Field separator (unaligned): `\\pset fieldsep ','`. "
        "Null display: `\\pset null '<NULL>'`. "
        "Pager: `\\pset pager off|always|on`. "
        "No args: list current settings.",
    ),
    (
        "psql meta-command: \\x (expanded output)",
        "`\\x` toggles expanded display (one column per line — easier to read wide rows). "
        "On/off: `\\x on` / `\\x off`. "
        "Auto: `\\x auto` (uses expanded only when output is too wide). "
        "Useful for tables with many columns or wide TEXT fields.",
    ),
    (
        "psql meta-command: \\a (toggle aligned output)",
        "`\\a` toggles between aligned (default, table layout) and unaligned (raw, suitable for pipes). "
        "Combined with `\\f ','` and `\\t` (tuples-only) it gives basic CSV. "
        "But for proper CSV: `\\pset format csv` is better (handles quoting).",
    ),
    (
        "psql meta-command: \\t (tuples-only mode)",
        "`\\t` toggles header + footer off — output is just row data. "
        "Useful for piping result into shell scripts: `psql -t -c 'SELECT name FROM users'`. "
        "Combined with `-A` (unaligned) + `-q` (quiet): gold standard for scripts.",
    ),
    (
        "psql meta-command: \\f (field separator)",
        "`\\f '<sep>'` sets the field separator for unaligned output. "
        "Tab: `\\f '\\t'`. "
        "Comma: `\\f ','`. "
        "Pipe: `\\f '|'`. "
        "Equivalent: `\\pset fieldsep '<sep>'`.",
    ),
    (
        "psql meta-command: \\H (HTML output mode)",
        "`\\H` toggles HTML table output (for piping into email or HTML report). "
        "One-shot: `\\pset format html`. "
        "Combine with `\\pset tableattr '...'` to set HTML attributes on the <table> tag.",
    ),
    (
        "psql meta-command: \\C (table caption / title)",
        "`\\C '<title>'` sets a caption shown above the output. "
        "Empty: `\\C` (no args) clears the title. "
        "Persists across queries until cleared.",
    ),
    # ============================================================
    # Meta-commands — query execution + scripting
    # ============================================================
    (
        "psql meta-command: \\i (include / run script file)",
        "`\\i <path>` runs SQL commands from a file. "
        "Relative to current dir (not the script's). "
        "Stops on error by default — for batch processing: `\\set ON_ERROR_STOP on`. "
        "Recursive: `\\ir <path>` (relative to including file's dir).",
    ),
    (
        "psql meta-command: \\copy (client-side COPY)",
        "`\\copy <table> FROM '<file>' CSV HEADER` — copies from client file (no superuser needed). "
        "Server-side COPY needs filesystem access on server. "
        "`\\copy (SELECT ...) TO '<file>' CSV HEADER` exports query result. "
        "All COPY options work: `FORMAT csv`, `DELIMITER`, `NULL`, `HEADER`, `QUOTE`. "
        "For huge data: COPY is 10-100x faster than INSERTs.",
    ),
    (
        "psql meta-command: \\watch (repeat query)",
        "`\\watch [<seconds>]` repeats the previous query every N seconds (default 2). "
        "Ctrl-C to stop. "
        "Useful for monitoring: `SELECT count(*) FROM pg_stat_activity;` then `\\watch 5`. "
        "PG 16+: `\\watch <interval> <max_iterations>` stops after N runs.",
    ),
    (
        "psql meta-command: \\timing (query timing)",
        "`\\timing` toggles execution time display. "
        "After each query: `Time: 123.456 ms`. "
        "Useful when comparing query optimizations. "
        "Persists for the session.",
    ),
    (
        "psql meta-command: \\g (execute previous / send to file)",
        "`\\g` re-executes the previous query buffer (after editing in another tool). "
        "`\\g <file>` sends output to file instead of stdout. "
        "`\\g | <command>` pipes output to a shell command. "
        "`\\gx` executes with expanded output (toggles \\x for one query).",
    ),
    (
        "psql meta-command: \\gset (capture query result into variables)",
        "`\\gset` runs the previous query and stores each column into a psql variable. "
        "Example: `SELECT count(*) AS n FROM users \\gset` then `\\echo :n`. "
        "Prefixed: `\\gset row_` produces `:row_n`. "
        "Use case: parameterize subsequent queries with previous-result values.",
    ),
    (
        "psql meta-command: \\gexec (execute generated SQL)",
        "`\\gexec` treats query output as SQL statements and executes each. "
        "Example: drop all tables matching pattern: `SELECT 'DROP TABLE ' || tablename FROM pg_tables WHERE schemaname='temp' \\gexec`. "
        "Powerful + dangerous — preview the SELECT before running \\gexec.",
    ),
    (
        "psql meta-command: \\e (edit query in $EDITOR)",
        "`\\e` opens the previous query buffer in $EDITOR (vim/nano/etc). "
        "Save + exit → executes. "
        "`\\e <file>` edits + executes the file. "
        "`\\ef <function>` edits a function definition. "
        "`\\ev <view>` edits a view definition. "
        "Set editor: `EDITOR=code -w psql` (VS Code with wait).",
    ),
    (
        "psql meta-command: \\if / \\elif / \\else / \\endif",
        "PG 10+: conditional blocks in psql scripts. "
        "```\\nSELECT count(*) = 0 AS empty FROM users \\gset\\n\\if :empty\\n  INSERT INTO users VALUES (...);\\n\\else\\n  \\echo 'users table not empty, skipping'\\n\\endif\\n```. "
        "Useful for idempotent migration scripts.",
    ),
    (
        "psql meta-command: \\q (quit psql)",
        "`\\q` exits psql cleanly. "
        "Ctrl-D (EOF) also works. "
        "Inside a transaction: ROLLBACK + disconnect. "
        "Exit code: 0 success, 1 internal error, 2 connection failed, 3 script error.",
    ),
    (
        "psql meta-command: \\! (shell escape)",
        "`\\!` opens a subshell. "
        "`\\! <command>` runs a single shell command and returns. "
        "Useful: `\\! clear`, `\\! ls *.sql`. "
        "Result not captured — for capturing output into a variable, use shell-redirect or write to a temp file then `\\i`.",
    ),
    (
        "psql meta-command: \\cd (change directory)",
        "`\\cd <path>` changes the working directory. "
        "Affects `\\i`, `\\copy`, `\\g <file>` paths. "
        "No args: changes to $HOME. "
        "Persists for the session.",
    ),
    (
        "psql meta-command: \\password (change password)",
        "`\\password` prompts for new password for current user. "
        "Specific user: `\\password <username>`. "
        "Uses secure prompt (not logged in command history). "
        "Equivalent of `ALTER USER ... WITH PASSWORD '...'` but encrypted on the wire.",
    ),
    # ============================================================
    # Variables and prompts
    # ============================================================
    (
        "psql variable: AUTOCOMMIT",
        "`\\set AUTOCOMMIT off` makes psql start an implicit transaction before each statement (default `on` commits each statement immediately). "
        "With AUTOCOMMIT off: every statement runs inside a transaction; must `COMMIT` or `ROLLBACK` manually. "
        "Best for: manual data editing, exploratory work where you want a safety net.",
    ),
    (
        "psql variable: ON_ERROR_ROLLBACK",
        "`\\set ON_ERROR_ROLLBACK on` (or `interactive`) makes psql wrap each statement in an implicit savepoint. "
        "On error: roll back to savepoint instead of aborting the entire transaction. "
        "`interactive` mode only enables when in interactive shell (not when reading from file). "
        "Useful for long interactive transactions where one typo would otherwise lose all work.",
    ),
    (
        "psql variable: ON_ERROR_STOP",
        "`\\set ON_ERROR_STOP on` exits with non-zero on first SQL error. "
        "Critical for migration scripts and CI: fail fast on any error. "
        "Default `off`: psql continues to next statement after error. "
        "Combine with `--single-transaction` flag (or wrap in BEGIN/COMMIT) for atomic script execution.",
    ),
    (
        "psql variable: ECHO and ECHO_HIDDEN",
        "`\\set ECHO all` prints each command before running (useful for script audit). "
        "`\\set ECHO queries` prints only SQL queries. "
        "`\\set ECHO_HIDDEN on` reveals the SQL that meta-commands generate (e.g. shows what `\\d` actually queries internally — useful for learning).",
    ),
    (
        "psql variable: FETCH_COUNT",
        "`\\set FETCH_COUNT 1000` fetches rows in batches of 1000 instead of all-at-once. "
        "Critical for queries that return millions of rows — avoids OOM on the client. "
        "Default `0` (no batching). "
        "Set in ~/.psqlrc for permanent effect.",
    ),
    (
        "psql variable: PROMPT1 / PROMPT2 / PROMPT3",
        "Customize the prompts. "
        "PROMPT1: main prompt. PROMPT2: continuation (multi-line). PROMPT3: COPY FROM stdin. "
        "Example: `\\set PROMPT1 '%[%033[1;33m%]%n@%/%R%# %[%033[0m%]'` shows user@db with color. "
        "Format specifiers: `%n` user, `%/` database, `%R` state (=, *, !, ?), `%x` transaction state, `%M` host, `%>` port. "
        "Put in ~/.psqlrc to persist.",
    ),
    (
        "psql variable: HISTFILE / HISTSIZE",
        "`\\set HISTFILE ~/.psql_history-:DBNAME` per-database history (the `:DBNAME` is substituted). "
        "`\\set HISTSIZE 5000` keeps last 5000 commands. "
        "History persists across sessions. "
        "Disable history: `\\set HISTFILE /dev/null`.",
    ),
    (
        "psql variable: VERBOSITY",
        "`\\set VERBOSITY verbose` shows detailed errors (location, SQLSTATE, schema). "
        "`default`: standard. "
        "`terse`: just the error message. "
        "`sqlstate`: include error code. "
        "Verbose mode names the table/column when constraint violations occur — extremely useful debugging.",
    ),
    # ============================================================
    # Command-line flags
    # ============================================================
    (
        "psql flag: -c / --command (run one command)",
        "`psql -c 'SELECT version();' -d mydb` runs a single command and exits. "
        "Multiple: `psql -c 'BEGIN' -c 'UPDATE ...' -c 'COMMIT'` (each `-c` is a separate transaction unless wrapped). "
        "For multi-statement: `-c 'BEGIN; UPDATE ...; COMMIT;'` (semicolons inside one -c). "
        "Suppresses interactive prompt — designed for scripting.",
    ),
    (
        "psql flag: -f / --file (run script)",
        "`psql -f script.sql -d mydb` executes a SQL script. "
        "Combine with `--single-transaction` for atomic execution. "
        "`-1` is the short form of `--single-transaction`. "
        "For variables: `psql -f script.sql -v env=prod -v batch_size=1000`.",
    ),
    (
        "psql flag: -A / --no-align (unaligned output)",
        "`psql -A -t` produces pipe-friendly output (no aligned columns, no header). "
        "Combine with `-F '<sep>'` (or `--field-separator='<sep>'`) for custom delimiter. "
        "Typical scripted query: `psql -tAc 'SELECT count(*) FROM users' -d mydb`.",
    ),
    (
        "psql flag: -t / --tuples-only",
        "`-t` strips column names + footer. "
        "Output is just data rows. "
        "Equivalent to `\\t` meta-command. "
        "Always pair with `-A` for raw, unaligned data.",
    ),
    (
        "psql flag: -X / --no-psqlrc",
        "`psql -X` skips reading ~/.psqlrc. "
        "Critical for scripts: ensures consistent behavior regardless of user's psqlrc. "
        "Use case: CI scripts, automated tools, embedded SQL in apps.",
    ),
    (
        "psql flag: -v / --set / --variable (set psql variable)",
        "`psql -v name='alice' -c \"SELECT :'name'\"` sets a variable. "
        "Quote-aware: `:'name'` produces quoted string; `:name` produces raw (use for keywords/identifiers). "
        "Special vars accept short form: `-v ON_ERROR_STOP=1`. "
        "From env: `psql -v env=$ENV -f script.sql`.",
    ),
    (
        "psql flag: -L / --log-file",
        "`psql -L session.log` writes copy of session (queries + output) to a log file. "
        "For audit, troubleshooting, replay. "
        "Output still goes to stdout normally — `-L` is a duplicate, not a redirect.",
    ),
    (
        "psql flag: -1 / --single-transaction",
        "Wraps all input in a single BEGIN/COMMIT. "
        "On any error: ROLLBACK + exit. "
        "Critical for safe script execution: either everything succeeds or nothing changes. "
        "Combine with `ON_ERROR_STOP`: `psql -1 -v ON_ERROR_STOP=1 -f migration.sql`.",
    ),
    (
        "psql flag: -P / --pset (output options from CLI)",
        "Set output formatting via command-line: `psql -P format=csv -c 'SELECT * FROM users'`. "
        "Multiple: `-P format=csv -P pager=off -P tuples_only=on`. "
        "Equivalent to `\\pset` in interactive mode.",
    ),
    (
        "psql flag: -E / --echo-hidden",
        "Reveals the SQL generated by meta-commands. "
        "Run psql with `-E` then type `\\d users` — you'll see the SQL psql sends to introspect. "
        "Useful for learning, building app-side equivalents.",
    ),
    (
        "psql flag: --csv",
        "PG 12+: `psql --csv -c 'SELECT * FROM users'` outputs CSV with proper quoting. "
        "Equivalent to `-P format=csv`. "
        "Use with `-t` to skip header, or `--csv -t`. "
        "For COPY-style data export: `\\copy (...) TO ... CSV HEADER` is still faster.",
    ),
    (
        "psql flag: -h / -p / -U / -d (connection)",
        "Connection target: `-h <host> -p <port> -U <user> -d <database>`. "
        "Equivalent to URI: `psql 'postgres://user@host:port/dbname'`. "
        "Or service: `psql 'service=prod'` (reads ~/.pg_service.conf). "
        "Default port 5432. Default user: $PGUSER or OS username. Default database: same as user.",
    ),
    # ============================================================
    # Recipes — common psql workflows
    # ============================================================
    (
        "psql recipe: scripted query output for shell",
        "Pipe-friendly: `psql -tAc 'SELECT count(*) FROM users' -d mydb`. "
        "`-t`: tuples only. `-A`: no aligned columns. `-c`: one command. "
        "Result is a single line ready for shell capture: `n=$(psql -tAc '...' -d mydb)`. "
        "For multi-row: `psql -tA -c 'SELECT id, name FROM users' | while IFS='|' read id name; do ...; done`.",
    ),
    (
        "psql recipe: idempotent migration script",
        "```sql\\n\\set ON_ERROR_STOP on\\nBEGIN;\\n\\nCREATE TABLE IF NOT EXISTS users (\\n    id SERIAL PRIMARY KEY,\\n    email TEXT NOT NULL UNIQUE\\n);\\n\\nALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW();\\n\\nCOMMIT;\\n```. "
        "`ON_ERROR_STOP on` aborts on first error inside BEGIN. "
        "Run: `psql -X -v ON_ERROR_STOP=1 -f migration.sql`.",
    ),
    (
        "psql recipe: capture query result into psql variable",
        "Use `\\gset` to bind columns to psql variables. "
        "```sql\\nSELECT count(*) AS user_count FROM users \\gset\\n\\echo Found :user_count users\\n```. "
        "Multiple columns: each becomes its own variable. "
        "Prefix: `\\gset row_` produces `:row_user_count`. "
        "Then use in subsequent SQL: `INSERT INTO audit (count_at) VALUES (:user_count);`.",
    ),
    (
        "psql recipe: format query result as JSON",
        "`SELECT row_to_json(t) FROM users t;` produces one JSON row per result. "
        "Aggregated: `SELECT json_agg(t) FROM users t;` produces single JSON array. "
        "Pretty: `\\pset format unaligned` + the JSON functions. "
        "For piping to jq: `psql -tAc 'SELECT row_to_json(t) FROM users t' | jq .`.",
    ),
    (
        "psql recipe: export query to CSV file",
        "Client-side (no server file access): `\\copy (SELECT * FROM users) TO '/tmp/users.csv' CSV HEADER`. "
        "With custom delimiter: `\\copy (...) TO '...' WITH (FORMAT csv, DELIMITER '|', HEADER)`. "
        "Server-side: `COPY (...) TO '/tmp/users.csv' CSV HEADER;` (requires superuser + server-local file). "
        "From CLI: `psql -c \"\\copy (SELECT * FROM users) TO 'users.csv' CSV HEADER\" -d mydb`.",
    ),
    (
        "psql recipe: import CSV file",
        "Client-side: `\\copy users FROM 'users.csv' CSV HEADER`. "
        "Specific columns: `\\copy users (name, email) FROM 'users.csv' CSV HEADER`. "
        "Handle NULL: `WITH (NULL 'NULL', FORMAT csv, HEADER)` treats literal 'NULL' as NULL. "
        "Encoding: server uses connection encoding; ensure file matches (`\\encoding UTF8`).",
    ),
    (
        "psql recipe: edit query in $EDITOR",
        "`\\e` opens previous query in $EDITOR. "
        "Set editor: `export EDITOR='code -w'` (VS Code with wait flag). "
        "For long queries: edit, save, close → psql runs the new text. "
        "Doesn't execute if you exit without saving. "
        "`\\ef <function>` and `\\ev <view>` edit their definitions directly.",
    ),
    (
        "psql recipe: customize psql with ~/.psqlrc",
        "Common setup: ```\\n\\set ON_ERROR_ROLLBACK interactive\\n\\set HISTFILE ~/.psql_history-:DBNAME\\n\\set HISTSIZE 5000\\n\\set FETCH_COUNT 1000\\n\\timing\\n\\x auto\\n\\pset null '<NULL>'\\nSELECT 'Connected to ' || current_database() AS msg;\\n```. "
        "Per-database override: `~/.psqlrc-mydb`. "
        "Skip for one session: `psql -X`.",
    ),
    (
        "psql recipe: monitor with \\watch",
        "Repeating dashboard: ```sql\\nSELECT pid, state, query_start, substring(query, 1, 60) AS q\\nFROM pg_stat_activity\\nWHERE state = 'active' AND pid <> pg_backend_pid()\\nORDER BY query_start;\\n\\watch 2\\n```. "
        "Refreshes every 2 seconds. Ctrl-C stops. "
        "PG 16+: `\\watch 2 30` runs 30 iterations then stops automatically.",
    ),
    (
        "psql recipe: generate + execute dynamic SQL with \\gexec",
        "Drop all tables in a schema: ```sql\\nSELECT format('DROP TABLE %I.%I CASCADE;', schemaname, tablename)\\nFROM pg_tables\\nWHERE schemaname = 'temp' \\gexec\\n```. "
        "Always preview the SELECT before \\gexec — generated SQL runs immediately. "
        "Use `format('%I', name)` for safe identifier quoting (handles reserved words + special chars).",
    ),
    (
        "psql recipe: split output to multiple files",
        "After a query: `\\g out.txt` writes that one query's output to a file. "
        "Subsequent queries return to stdout. "
        "For continuous redirect: `\\o out.txt` (sticky) until `\\o` (clear). "
        "Pipe to command: `\\g | wc -l` (count rows). "
        "Pipe sticky: `\\o | tee output.txt`.",
    ),
    (
        "psql recipe: conditional execution with \\if",
        "PG 10+: ```sql\\nSELECT count(*) = 0 AS is_empty FROM migrations WHERE name = 'add_users_table' \\gset\\n\\if :is_empty\\n  CREATE TABLE users (...);\\n  INSERT INTO migrations VALUES ('add_users_table');\\n\\else\\n  \\echo 'Migration already applied'\\n\\endif\\n```. "
        "Nested \\if blocks supported. "
        "Variables must be boolean strings: `t`/`f`/`true`/`false`.",
    ),
    (
        "psql recipe: customize prompt to show transaction state",
        "Best practice: ```\\n\\set PROMPT1 '%[%033[1;36m%]%n%[%033[0m%]@%[%033[1;33m%]%/%[%033[0m%]%R%x%# '\\n\\set PROMPT2 '%[%033[1;36m%]%R%x%# '\\n```. "
        "`%x` shows `*` when in a transaction, `!` if transaction is in error state. "
        "Lets you immediately see whether the current statement is in a transaction.",
    ),
    (
        "psql recipe: batch report generation",
        "```bash\\npsql -X -h db.prod -d analytics -P format=html -c '\\n  SELECT date_trunc(\\'day\\', created_at) AS day, count(*)\\n  FROM signups\\n  GROUP BY 1 ORDER BY 1 DESC LIMIT 30\\n' > report.html\\n```. "
        "`-P format=html` produces HTML table. "
        "Combine multiple queries: `-c '...' -c '...'` (each rendered separately). "
        "For email: pipe to `mail -s 'Daily report'`.",
    ),
    (
        "psql recipe: use \\copy with PIPE for streaming",
        "From a command's output: `\\copy users FROM PROGRAM 'curl -s https://api.example.com/users.csv' CSV HEADER`. "
        "To a command's input: `\\copy (SELECT * FROM users) TO PROGRAM 'gzip > users.csv.gz' CSV HEADER`. "
        "Requires PG 14+ + appropriate grants. "
        "Use case: stream-load without intermediate disk file.",
    ),
    (
        "psql recipe: large objects (lo_*)",
        "PostgreSQL stores binary data either inline (bytea, up to ~1GB) or as large objects (separate storage, streaming I/O). "
        "Import file as LO: `\\lo_import '/path/to/file.bin'` (returns OID). "
        "Export: `\\lo_export <oid> '/path/out.bin'`. "
        "List: `\\lo_list`. "
        "Unlink: `\\lo_unlink <oid>`. "
        "Modern preference: bytea (simpler, transactional, no extra metadata table).",
    ),
    (
        "psql recipe: connection string with sslmode",
        "URI form: `psql 'postgres://user:pass@host:5432/dbname?sslmode=verify-full&sslrootcert=/path/ca.crt'`. "
        "Keyword form: `psql 'host=db user=u dbname=d sslmode=verify-full sslrootcert=/path/ca.crt'`. "
        "sslmode values: `disable`, `allow`, `prefer` (default), `require`, `verify-ca`, `verify-full`. "
        "Production: always `verify-full`.",
    ),
    (
        "psql recipe: pause between statements with prompt",
        "For walkthroughs / demos: `\\prompt 'Press enter to continue...' x` waits for input. "
        "Captures into variable `x` (often discarded). "
        "Use case: presentation, step-through debugging.",
    ),
    (
        "psql recipe: re-run query buffer",
        "After scrolling history (up arrow), `\\g` re-runs the last query. "
        "Or just press enter on a copied line. "
        "After `\\e` (edit), save+exit also re-runs. "
        "Buffer is preserved across commands until cleared with `\\r`.",
    ),
    # ============================================================
    # Errors — psql-specific (vs postgres server errors)
    # ============================================================
    (
        "psql error: invalid command \\<X>",
        "Meta-command typo or doesn't exist. "
        "Fix: (1) check available: `\\?` shows all meta-commands; "
        "(2) `\\? variables` shows special variables; "
        "(3) common typos: `\\dt` (not `\\dT` — different result); `\\d+` (verbose) vs `\\d`; "
        "(4) for SQL not psql: drop the backslash — `SELECT * FROM ...` not `\\select`.",
    ),
    (
        "psql error: unrecognized value \"X\" for \"Y\"",
        "Meta-command argument doesn't match expected enum. "
        "Fix: (1) read the error — it usually lists valid values; "
        "(2) common: `\\pset format X` — must be aligned/unaligned/csv/html/latex/markdown/wrapped; "
        "(3) `\\set AUTOCOMMIT X` — must be on/off; "
        "(4) for help: `\\h <command>` shows SQL syntax; `\\?` lists meta-commands.",
    ),
    (
        "psql error: extra command-line argument \"X\" ignored",
        "Passed too many args (e.g. `psql mydb extra`). "
        "Fix: (1) connection target must be ONE arg: `psql mydb` or `psql -d mydb`; "
        "(2) for query: use `-c 'SELECT ...'`; "
        "(3) for script: use `-f script.sql`; "
        "(4) for connection URI: `psql 'postgres://...'` (single quoted arg).",
    ),
    (
        "psql error: connection to server lost / server closed connection",
        "Mid-session connection drop. "
        "Fix: (1) network blip — usually safe to reconnect: `\\c`; "
        "(2) server restart — check `pg_isready -h <host>`; "
        "(3) idle timeout from middleware (pgbouncer, load balancer); "
        "(4) OOM kill on server side — check server logs; "
        "(5) for long-running scripts: wrap with retry logic in shell.",
    ),
    (
        "psql error: input/output error / cannot read SQL file",
        "Script file not found or unreadable. "
        "Fix: (1) absolute path is safest: `psql -f /full/path/to/script.sql`; "
        "(2) for `\\i`: path is relative to psql's CWD, not script's directory — use `\\ir` for include-relative; "
        "(3) check permissions: `ls -la script.sql`; "
        "(4) on Windows: forward slashes work, backslashes don't.",
    ),
    (
        "psql error: \\copy: parse error at \"X\"",
        "Meta-command syntax differs from server-side COPY. "
        "Fix: (1) `\\copy` accepts most COPY options but uses semicolons differently — `\\copy <table> FROM '<file>' CSV HEADER` (no semicolon, no parentheses around options); "
        "(2) modern: `WITH (FORMAT csv, HEADER)` works in \\copy too; "
        "(3) for SELECT source: parens required: `\\copy (SELECT ...) TO '...' CSV HEADER`.",
    ),
    (
        "psql error: variable name \"X\" not valid (\\set)",
        "Variable name violates rules. "
        "Fix: (1) start with letter or _; "
        "(2) subsequent chars: letters, digits, _; "
        "(3) case-sensitive: `name` and `NAME` are different; "
        "(4) reserved: lowercase names are app-style; uppercase are built-in (AUTOCOMMIT, ON_ERROR_STOP, ECHO).",
    ),
    (
        "psql error: pager exited unsuccessfully / broken pipe in pager",
        "User pressed `q` in less while data was still being sent. "
        "Fix: (1) harmless — ignore; "
        "(2) suppress: `\\pset pager off`; "
        "(3) auto-pager: `\\pset pager always` (always) or `on` (only when needed); "
        "(4) set custom pager: `export PAGER='less -SFX'` (`-S` no-wrap, `-F` quit-if-one-screen, `-X` no-clear).",
    ),
    (
        "psql error: ON_ERROR_ROLLBACK only works inside a transaction",
        "Misconception: `\\set ON_ERROR_ROLLBACK on` only kicks in inside an explicit `BEGIN`. "
        "Fix: (1) for per-statement safety net: AUTOCOMMIT off + ON_ERROR_ROLLBACK = each statement is a transaction with savepoint; "
        "(2) for scripts: wrap in `BEGIN; ... COMMIT;` + set ON_ERROR_STOP.",
    ),
    (
        "psql error: COPY from STDIN failed / waiting for input",
        "psql is waiting for stdin data after a `COPY <table> FROM STDIN;` statement. "
        "Fix: (1) provide data lines + terminate with `\\.` on its own line; "
        "(2) for file: use `\\copy <table> FROM '<file>'` instead (handles stdin/file abstraction); "
        "(3) if stuck: Ctrl-C (cancels the COPY but leaves transaction in aborted state — ROLLBACK).",
    ),
    (
        "psql error: \\password change failed",
        "Server rejected the new password. "
        "Fix: (1) password complexity requirements: check `passwordcheck` extension if installed; "
        "(2) for SCRAM auth: ensure `password_encryption = scram-sha-256` in postgresql.conf; "
        "(3) for clusters using LDAP/PAM: change password in the external system instead.",
    ),
    (
        "psql error: readline / libedit history disabled",
        "psql compiled without readline support — no command history, no line editing. "
        "Fix: (1) check: `psql --version` reports `readline` if compiled in; "
        "(2) install proper version: macOS `brew install postgresql` (links readline); Linux `apt install libreadline-dev` then rebuild psql; "
        "(3) workaround: pipe through `rlwrap`: `rlwrap psql -d mydb`.",
    ),
    (
        "psql error: encoding mismatch / cannot convert character",
        "Client and database encodings disagree. "
        "Fix: (1) check: `\\encoding` shows client encoding; `SHOW server_encoding;` shows server; "
        "(2) set client: `\\encoding UTF8` (or via env `PGCLIENTENCODING=UTF8`); "
        "(3) for COPY: file encoding must match client encoding (use `iconv` to convert files); "
        "(4) for output mangling: terminal encoding must match — `locale charmap`.",
    ),
    (
        "psql error: \\watch interval invalid",
        "`\\watch` interval too short or non-numeric. "
        "Fix: (1) interval is seconds (integer or fractional): `\\watch 2` or `\\watch 0.5`; "
        "(2) minimum: 0.001 (1ms — usually too fast); "
        "(3) PG 16+: second arg is max iterations: `\\watch 5 10` (5s interval, 10 runs max).",
    ),
]

TOOL_ID = "postgresql-psql"


def main() -> None:
    store = get_claim_store()
    orchestrator = CanonOrchestrator(store)
    svc = EmbeddingService.get_default()
    now = datetime.now(timezone.utc)
    ok = 0
    failed = 0
    for i, (subject, statement) in enumerate(CLAIMS):
        suffix = f"{i:03d}"
        body_for_hash = f"{subject}\n{statement}"
        evidence = Evidence(
            evidence_id=f"ev-pgpsql-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=URL,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-pgpsql-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=TOOL_ID,
            submitted_by="postgresql-psql-catalog",
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
        print(f"  ✓ #{suffix} [{TOOL_ID}] {subject[:70]}")
    print(f"\nDone: {ok} created / {failed} failed")


if __name__ == "__main__":
    main()
