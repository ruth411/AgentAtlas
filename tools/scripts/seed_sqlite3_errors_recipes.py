"""Direct-insert sqlite3 error + recipe claims."""

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

BASE = "https://sqlite.org"

CLAIMS = [
    # ============================================================
    # sqlite3-errors — 30 common errors
    # ============================================================
    (
        "sqlite3-errors", "sqlite3 error: database is locked (SQLITE_BUSY)",
        "Another connection holds a lock incompatible with yours. "
        "Fix: (1) enable WAL mode for concurrent reads + one writer: `PRAGMA journal_mode=WAL;`; "
        "(2) bump busy timeout: `PRAGMA busy_timeout = 5000;` (5s — connection waits instead of erroring); "
        "(3) at app level: retry with exponential backoff on SQLITE_BUSY; "
        "(4) check for long-running readers — `BEGIN; SELECT...;` without COMMIT blocks writers; "
        "(5) for high-concurrency: SQLite isn't ideal — consider Postgres.",
        f"{BASE}/wal.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: database disk image is malformed (SQLITE_CORRUPT)",
        "File-level corruption — pages reference each other inconsistently. "
        "Fix: (1) check: `PRAGMA integrity_check;` (lists problems) or `PRAGMA quick_check;` (faster); "
        "(2) dump + reload: `sqlite3 corrupt.db .dump | sqlite3 fresh.db` recovers what's salvageable; "
        "(3) for recent corruption: restore from backup; "
        "(4) common causes: filesystem crash mid-write, network filesystems (NFS) — never put SQLite on NFS for writes; "
        "(5) verify hardware: bad disk sectors, RAM errors. Run `smartctl`, `memtest`.",
        f"{BASE}/backup.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: no such table: <X>",
        "Table doesn't exist in the connected database. "
        "Fix: (1) list tables: `.tables` (CLI) or `SELECT name FROM sqlite_schema WHERE type='table';`; "
        "(2) check current DB: `.databases` in CLI (you may be in main vs attached); "
        "(3) for ATTACH'd database: qualify name: `SELECT * FROM other_db.tablename`; "
        "(4) case-sensitive lookup: SQLite matches case-insensitively by default but quoted identifiers preserve case; "
        "(5) typo check — `sqlite_schema` (modern) replaces older `sqlite_master`.",
        f"{BASE}/lang_createtable.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: no such column: <X>",
        "Column missing or scope wrong. "
        "Fix: (1) describe table: `.schema <table>` or `PRAGMA table_info(<table>);`; "
        "(2) for joins: qualify with table: `users.name` not `name`; "
        "(3) for aliases inside same query: column aliases (`AS`) defined in SELECT aren't available in WHERE; use HAVING or subquery; "
        "(4) for typos with quoted names: SQLite quietly accepts `\"missing_column\"` as a string literal in SELECT — only fails in WHERE. Use `[col]` or single backticks for safety.",
        f"{BASE}/lang_naming.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: UNIQUE constraint failed: <table>.<col>",
        "INSERT or UPDATE would create a duplicate where a UNIQUE constraint forbids it. "
        "Fix: (1) UPSERT: `INSERT INTO t(id, name) VALUES (1, 'a') ON CONFLICT(id) DO UPDATE SET name = excluded.name;`; "
        "(2) INSERT OR IGNORE: silently skip duplicates: `INSERT OR IGNORE INTO t VALUES (...);`; "
        "(3) INSERT OR REPLACE: deletes old row + inserts new (CAREFUL — triggers + foreign keys cascade); "
        "(4) for auto-id seqs out of sync after import: `UPDATE sqlite_sequence SET seq = (SELECT max(id) FROM t) WHERE name = 't';`; "
        "(5) for soft delete: include `deleted_at IS NULL` in a partial unique index.",
        f"{BASE}/lang_upsert.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: NOT NULL constraint failed: <table>.<col>",
        "Provided NULL for a NOT NULL column. "
        "Fix: (1) ensure your INSERT names all NOT NULL columns: `INSERT INTO t (a, b) VALUES (1, 2);`; "
        "(2) provide a DEFAULT: `CREATE TABLE t (created_at TEXT NOT NULL DEFAULT (datetime('now')));`; "
        "(3) for migrations adding NOT NULL to existing column: must provide DEFAULT or pre-fill: `ALTER TABLE t ADD COLUMN c TEXT NOT NULL DEFAULT '';`; "
        "(4) for ON CONFLICT recovery: `INSERT ... ON CONFLICT DO UPDATE SET c = ifnull(excluded.c, 'default');`.",
        f"{BASE}/lang_createtable.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: FOREIGN KEY constraint failed",
        "INSERT references nonexistent parent, OR DELETE of parent with dependent rows (without CASCADE). "
        "Fix: (1) IMPORTANT: foreign keys are OFF by default in SQLite — enable per-connection: `PRAGMA foreign_keys = ON;`; "
        "(2) insert parent first, then child; "
        "(3) for cascading: `FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE`; "
        "(4) for circular refs: defer with `DEFERRABLE INITIALLY DEFERRED` (limited support — not all FK options work like Postgres); "
        "(5) for transactional bulk delete: `BEGIN; PRAGMA defer_foreign_keys = 1; ...; COMMIT;` (deferred-FK in transaction).",
        f"{BASE}/pragma.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: CHECK constraint failed: <name>",
        "Value violates a CHECK constraint. "
        "Fix: (1) view the constraint: `.schema <table>` shows the CHECK clause; "
        "(2) common: `CHECK (age >= 0)` rejects negative ages; "
        "(3) to allow: relax or remove the constraint via `ALTER TABLE` (modern SQLite allows ALTER for renames; for CHECK changes use 12-step process: create new table with desired schema, copy data, drop old, rename); "
        "(4) for nullable values: CHECK with NULL: `value IS NULL OR value > 0`.",
        f"{BASE}/lang_createtable.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: attempt to write a readonly database",
        "Database opened read-only or file/dir not writable. "
        "Fix: (1) check file perms: `ls -la file.db` — must be writable; "
        "(2) check directory perms: SQLite needs to create journal files in same dir; "
        "(3) for WAL mode: needs to create `-wal` and `-shm` files alongside; "
        "(4) verify open mode: in Python `sqlite3.connect('file.db', uri=True)` defaults read-write but URI like `file:db?mode=ro` is read-only; "
        "(5) for containers: ensure user inside container can write the volume.",
        f"{BASE}/cli.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: unable to open database file",
        "File path wrong, parent dir doesn't exist, or perms forbid. "
        "Fix: (1) absolute path is safest: `sqlite3 /full/path/to/file.db`; "
        "(2) for new database: parent dir must exist (sqlite3 creates the file but not directories); "
        "(3) check perms on the parent dir; "
        "(4) for in-memory: `:memory:` (single connection) or `file::memory:?cache=shared` (multi-connection in same process).",
        f"{BASE}/cli.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: file is not a database / file is encrypted",
        "Opening a file that isn't a SQLite database (or is encrypted via SQLCipher and you don't have key). "
        "Fix: (1) check header: `xxd file.db | head -1` — should start with `SQLite format 3\\x00`; "
        "(2) for SQLCipher: provide the key via `PRAGMA key = 'your-key';` immediately after open; "
        "(3) verify the file isn't truncated: `wc -c file.db` — should be a multiple of page size (default 4096); "
        "(4) you might be opening a Postgres dump / CSV / etc. by mistake.",
        f"{BASE}/cli.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: database or disk is full",
        "Filesystem out of space, OR file size limit hit. "
        "Fix: (1) check disk: `df -h $(dirname file.db)`; "
        "(2) WAL files can grow huge — checkpoint: `PRAGMA wal_checkpoint(TRUNCATE);`; "
        "(3) VACUUM to reclaim deleted space: `VACUUM;` (rewrites entire DB — large I/O); "
        "(4) page-count limit: 281TB max (2^41 bytes) — almost never hit; "
        "(5) for embedded systems: SQLITE_DEFAULT_PAGE_SIZE compile option.",
        f"{BASE}/wal.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: cannot start a transaction within a transaction",
        "Nested BEGIN without SAVEPOINT. "
        "Fix: (1) use SAVEPOINT for nesting: `SAVEPOINT sp1; ...; RELEASE sp1;` (or `ROLLBACK TO sp1;`); "
        "(2) check that previous transaction is closed: `COMMIT;` or `ROLLBACK;` first; "
        "(3) Python: `conn.commit()` or use `with conn:` context manager which auto-commits; "
        "(4) for automatic transaction wrapping: SQLite is in autocommit mode by default until you issue BEGIN.",
        f"{BASE}/lang_savepoint.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: cannot ROLLBACK - no transaction is active",
        "ROLLBACK without prior BEGIN. "
        "Fix: (1) only ROLLBACK after BEGIN; "
        "(2) check transaction state: in CLI, status command or just attempt a `SELECT` and observe; "
        "(3) Python: catch the OperationalError; "
        "(4) for safe retry pattern: `try: cur.execute(\"BEGIN\"); ...; conn.commit(); except: conn.rollback();`.",
        f"{BASE}/lang_transaction.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: SQL logic error: syntax error",
        "Parser couldn't make sense of the SQL. "
        "Fix: (1) error usually points at the offending token; "
        "(2) common: missing comma in column list, wrong quoting (use `'` for strings, `\"` for identifiers, `[col]` or backticks also work for identifiers); "
        "(3) reserved words: `order`, `user`, `transaction` etc need quoting if used as names; "
        "(4) check parens: `(...)` matching; "
        "(5) for multiline scripts: stray `;` early ends a statement.",
        f"{BASE}/lang.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: too many SQL variables (SQLITE_LIMIT_VARIABLE_NUMBER)",
        "More than 999 placeholders in one statement (SQLite default limit — 32766 in 3.32+). "
        "Fix: (1) batch in chunks of 500: split your IN clause into multiple queries; "
        "(2) use temporary table: `CREATE TEMP TABLE ids(id); INSERT INTO ids VALUES ...; SELECT * FROM t WHERE id IN (SELECT id FROM ids);`; "
        "(3) recompile SQLite with `SQLITE_MAX_VARIABLE_NUMBER=32766` (most modern distros already do this); "
        "(4) check current limit: programmatically `sqlite3_limit(db, SQLITE_LIMIT_VARIABLE_NUMBER, -1);`.",
        f"{BASE}/limits.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: interrupted (SQLITE_INTERRUPT)",
        "User pressed Ctrl-C or app called `sqlite3_interrupt()`. "
        "Fix: (1) for CLI: just retry; "
        "(2) for app: catch the interrupted error; transaction is automatically rolled back; "
        "(3) for long queries: consider `PRAGMA query_only = ON;` for read-only sessions to prevent accidental long writes; "
        "(4) avoid interrupting during ANALYZE / VACUUM — these don't resume well.",
        f"{BASE}/cli.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: .read / .import file not found",
        "Dot-command's file argument doesn't exist. "
        "Fix: (1) check cwd: dot-commands use cwd, not script dir; `pwd` first; "
        "(2) tilde doesn't expand in dot-commands: use full path; "
        "(3) for `.import`: `.import --csv --skip 1 data.csv my_table` (modern, with header skip); "
        "(4) `.read script.sql` runs SQL from file; relative to cwd.",
        f"{BASE}/cli.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: .import CSV column count mismatch",
        "CSV row has wrong number of columns. "
        "Fix: (1) check delimiter: `.separator ','` or `.mode csv` (handles quoting properly); "
        "(2) for header-aware import: `.import --csv --skip 1 file.csv table_name` (3.32+); "
        "(3) for mismatched columns: pre-process with sed/awk, OR create table matching CSV's column count and migrate; "
        "(4) for embedded newlines: use `--ascii` mode if you control the export.",
        f"{BASE}/cli.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: column ambiguous after join",
        "Same column name in multiple joined tables — must qualify. "
        "Fix: (1) qualify: `users.id, posts.user_id`; "
        "(2) for shared key: `USING (id)` collapses both sides into one; "
        "(3) for clarity: use AS to disambiguate: `users.id AS user_id, posts.id AS post_id`; "
        "(4) for `SELECT *` from join: explicitly list columns instead.",
        f"{BASE}/lang_select.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: integer overflow / numeric overflow",
        "Value too big for INTEGER type. "
        "Fix: (1) SQLite INTEGER is up to 8 bytes (i64, -2^63 to 2^63-1); "
        "(2) for bigger numbers: store as TEXT and do arithmetic in app; "
        "(3) for monetary values: store as integer cents (avoid REAL float imprecision); "
        "(4) explicit cast: `CAST(x AS REAL)` for large products that would overflow int.",
        f"{BASE}/datatype3.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: cannot drop sqlite_sequence / sqlite_schema",
        "System tables can't be dropped or directly modified. "
        "Fix: (1) for `sqlite_sequence` (used by AUTOINCREMENT): can DELETE rows: `DELETE FROM sqlite_sequence WHERE name = 't';` (resets the seq for that table); "
        "(2) `sqlite_schema` (formerly `sqlite_master`): read-only schema info; "
        "(3) to drop a table cleanly: `DROP TABLE t;` (handles all metadata).",
        f"{BASE}/lang_droptable.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: ATTACH 'X' AS Y: cannot attach with name 'main'",
        "`main` is reserved for the connection's primary DB. "
        "Fix: (1) pick another name: `ATTACH 'other.db' AS other;`; "
        "(2) max attached: 10 by default (SQLITE_LIMIT_ATTACHED), 125 hard limit; "
        "(3) for queries across: qualify: `SELECT * FROM other.tablename;`; "
        "(4) detach: `DETACH other;`.",
        f"{BASE}/lang_attach.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: WAL file growing forever",
        "WAL accumulates transactions until checkpoint. If readers are very slow, checkpoints can't truncate. "
        "Fix: (1) periodic forced checkpoint: `PRAGMA wal_checkpoint(TRUNCATE);` (blocks until done); "
        "(2) configure auto: `PRAGMA wal_autocheckpoint = 1000;` (checkpoint after 1000 pages — default); "
        "(3) for embedded apps: connection close triggers checkpoint; "
        "(4) for long-running readers: hold their transactions briefly; "
        "(5) if WAL file becomes huge: connection close (or app restart) + manual `wal_checkpoint(RESTART)` resets it.",
        f"{BASE}/wal.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: ON CONFLICT clause does not match any constraint",
        "`ON CONFLICT(col)` references a column without UNIQUE/PRIMARY KEY constraint. "
        "Fix: (1) only columns with UNIQUE constraint can be conflict targets; "
        "(2) create the constraint first: `CREATE UNIQUE INDEX idx_email ON users(email);`; "
        "(3) for composite: `ON CONFLICT(col1, col2)` needs UNIQUE on the same combination; "
        "(4) alternative without ON CONFLICT: `INSERT OR REPLACE` (uses any UNIQUE — be careful with triggers).",
        f"{BASE}/lang_upsert.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: foreign keys disabled (silently)",
        "Foreign keys are off by default — common surprise. INSERT/DELETE that should fail silently succeed. "
        "Fix: (1) ALWAYS enable per-connection: `PRAGMA foreign_keys = ON;` — must be issued on each new connection; "
        "(2) for libraries: many ORMs do this automatically; for raw connections, do it manually; "
        "(3) for migrations: temporarily disable: `PRAGMA foreign_keys = OFF;` then re-enable + `PRAGMA foreign_key_check;` to verify; "
        "(4) check status: `PRAGMA foreign_keys;` returns 0 (off) or 1 (on).",
        f"{BASE}/pragma.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: text encoding mismatch / invalid byte sequence",
        "Mixed UTF-8 / UTF-16 / non-UTF data inserted into text column. "
        "Fix: (1) default encoding is UTF-8 — set explicitly at DB creation: `PRAGMA encoding = 'UTF-8';`; "
        "(2) for binary data: use BLOB column type, not TEXT; "
        "(3) for legacy CSV with weird encoding: convert with `iconv -f LATIN1 -t UTF-8 file.csv > clean.csv` before import; "
        "(4) check: `PRAGMA encoding;` reports current.",
        f"{BASE}/datatype3.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: recursive trigger / max recursion exceeded",
        "Trigger that calls SQL that fires another trigger that fires the original → infinite recursion. "
        "Fix: (1) by default triggers don't recurse into themselves but cross-trigger recursion is possible; "
        "(2) disable recursion: `PRAGMA recursive_triggers = OFF;` (default OFF in some versions, ON in others); "
        "(3) use WHEN clauses to break recursion: `CREATE TRIGGER ... WHEN NEW.flag = 0 ...`; "
        "(4) check recursion depth: `PRAGMA max_trigger_depth;` (default 1000).",
        f"{BASE}/pragma.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: cannot use generated columns in INSERT",
        "Generated columns are computed — you can't INSERT into them directly. "
        "Fix: (1) omit generated columns from INSERT column list: `INSERT INTO t (a, b) VALUES (1, 2);` where `c GENERATED ALWAYS AS (a + b)` exists; "
        "(2) for ALTER TABLE generated col: `ALTER TABLE t ADD COLUMN c GENERATED ALWAYS AS (a + b);`; "
        "(3) STORED vs VIRTUAL: STORED takes disk space, VIRTUAL computes on read.",
        f"{BASE}/lang_createtable.html",
    ),
    (
        "sqlite3-errors", "sqlite3 error: JSON path doesn't exist (returns NULL)",
        "JSON1 functions return NULL when the path is missing — not an error, but often unexpected. "
        "Fix: (1) check existence: `json_extract(j, '$.path') IS NOT NULL`; "
        "(2) provide default: `coalesce(json_extract(j, '$.path'), 'default')`; "
        "(3) for arrays: `json_extract(j, '$.arr[0]')` — `[0]` is 0-indexed; "
        "(4) for validation: `json_valid(j)` returns 1 / 0; "
        "(5) modern syntax: `j -> '$.path'` returns JSON, `j ->> '$.path'` returns text/scalar.",
        f"{BASE}/json1.html",
    ),

    # ============================================================
    # sqlite3-recipes — 36 common workflows
    # ============================================================
    (
        "sqlite3-recipes", "sqlite3 recipe: open / create a database",
        "Create + open: `sqlite3 mydb.db` (creates if missing). "
        "Memory-only: `sqlite3 :memory:`. "
        "Read-only: `sqlite3 -readonly mydb.db` or URI `file:mydb.db?mode=ro`. "
        "From script: `sqlite3 mydb.db < init.sql` (run schema then exit). "
        "Run one command: `sqlite3 mydb.db 'SELECT * FROM users;'`. "
        "Persistent CLI history: `~/.sqlite_history` is auto-saved.",
        f"{BASE}/cli.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: useful CLI dot-commands",
        "`.tables` — list tables. "
        "`.schema [<table>]` — show CREATE statements. "
        "`.indexes [<table>]` — show indexes. "
        "`.databases` — list attached. "
        "`.headers on` — show column names. "
        "`.mode <table|csv|column|line|json|html>` — output format. "
        "`.timer on` — show query time. "
        "`.read file.sql` — run script. "
        "`.import data.csv table` — bulk load CSV. "
        "`.dump` — full SQL dump. "
        "`.quit` (or Ctrl-D).",
        f"{BASE}/cli.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: configure output format for queries",
        "Default: pipe-separated. Better: `.headers on; .mode column;` — aligned columns with names. "
        "CSV (with proper quoting): `.mode csv`. "
        "JSON (each row as JSON object): `.mode json`. "
        "HTML table: `.mode html`. "
        "Quoted (re-usable as INSERT VALUES): `.mode quote`. "
        "Wide tables: `.mode list` is often more readable than columns. "
        "Set width per col: `.width 20 30 10` (for column mode).",
        f"{BASE}/cli.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: import CSV file",
        "Modern (header-aware): `.mode csv; .headers on; .import --csv --skip 1 data.csv users`. "
        "Without `--skip 1` the header row would become a row of data. "
        "For existing table: column order in CSV must match table order. "
        "For NULL values: by default treats empty as empty string; `.nullvalue NULL` to recognize literal `NULL`. "
        "For tab-separated: `.mode tabs; .import data.tsv users`.",
        f"{BASE}/cli.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: export query results to CSV",
        "From CLI: `.headers on; .mode csv; .output users.csv; SELECT * FROM users; .output stdout;`. "
        "One-liner from shell: `sqlite3 -header -csv mydb.db 'SELECT * FROM users' > users.csv`. "
        "For tab-separated: `sqlite3 -separator $'\\t' mydb.db 'SELECT * FROM users' > users.tsv`. "
        "JSON output: `sqlite3 -json mydb.db 'SELECT * FROM users' > users.json`.",
        f"{BASE}/cli.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: backup a database (online, no downtime)",
        "Modern: `VACUUM INTO 'backup.db';` (atomic, no lock contention with writers). "
        "CLI: `sqlite3 source.db \".backup 'backup.db'\"` (uses internal backup API). "
        "Dump as SQL: `sqlite3 source.db .dump > backup.sql` (huge but version-portable). "
        "Restore from dump: `sqlite3 fresh.db < backup.sql`. "
        "For active databases: backup API (`.backup`) is best — copies pages incrementally without blocking long.",
        f"{BASE}/backup.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: enable WAL mode for concurrent reads",
        "`PRAGMA journal_mode = WAL;` (one-time per database — persists). "
        "Benefits: multiple readers + one writer concurrent; commit is faster (writes to WAL not main DB); reads don't block writes. "
        "Trade-offs: creates `-wal` and `-shm` files alongside; doesn't work over network filesystems. "
        "Verify: `PRAGMA journal_mode;` returns `wal`. "
        "Checkpoint to merge WAL into main: `PRAGMA wal_checkpoint(TRUNCATE);`.",
        f"{BASE}/wal.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: enable foreign keys",
        "MUST run on every connection (default is OFF for legacy compat): `PRAGMA foreign_keys = ON;`. "
        "Verify: `PRAGMA foreign_keys;` returns 1. "
        "In CREATE TABLE: `FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE ON UPDATE CASCADE`. "
        "Verify all existing data conforms: `PRAGMA foreign_key_check;` returns rows for violations. "
        "Disable for bulk import: `PRAGMA foreign_keys = OFF;` then re-enable + check.",
        f"{BASE}/pragma.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: CREATE TABLE with proper types + constraints",
        "```sql\\nCREATE TABLE users (\\n    id INTEGER PRIMARY KEY,           -- auto-increments by default\\n    email TEXT NOT NULL UNIQUE,\\n    name TEXT NOT NULL,\\n    age INTEGER CHECK (age >= 0),\\n    created_at TEXT NOT NULL DEFAULT (datetime('now')),\\n    metadata TEXT CHECK (json_valid(metadata) OR metadata IS NULL)\\n);\\n```. "
        "Storage classes: NULL, INTEGER, REAL, TEXT, BLOB. Type affinity is loose — `TEXT` accepts numbers. "
        "STRICT mode (3.37+): `CREATE TABLE users (...) STRICT;` enforces declared types.",
        f"{BASE}/lang_createtable.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: CREATE INDEX (regular, unique, partial)",
        "Regular: `CREATE INDEX idx_email ON users(email);`. "
        "Unique: `CREATE UNIQUE INDEX idx_email ON users(email);`. "
        "Composite: `CREATE INDEX idx_user_created ON posts(user_id, created_at);`. "
        "Partial (filter rows that get indexed): `CREATE INDEX idx_active ON users(email) WHERE deleted_at IS NULL;`. "
        "Expression index: `CREATE INDEX idx_lower_email ON users(lower(email));` — then query `WHERE lower(email) = 'alice@x'` uses it. "
        "Drop: `DROP INDEX idx_email;`.",
        f"{BASE}/lang_createindex.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: UPSERT (INSERT ... ON CONFLICT)",
        "Skip duplicate: `INSERT INTO users (id, name) VALUES (1, 'a') ON CONFLICT(id) DO NOTHING;`. "
        "Update on conflict: `INSERT INTO users (id, name, updated_at) VALUES (1, 'a', datetime('now')) ON CONFLICT(id) DO UPDATE SET name = excluded.name, updated_at = excluded.updated_at;`. "
        "`excluded` refers to row that would have been inserted. "
        "Conditional update: `... ON CONFLICT(id) DO UPDATE SET name = excluded.name WHERE users.updated_at < excluded.updated_at;`. "
        "Multi-column conflict: `ON CONFLICT(col1, col2)` needs matching UNIQUE constraint.",
        f"{BASE}/lang_upsert.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: UPDATE with values from another table (UPDATE FROM, 3.33+)",
        "```sql\\nUPDATE products\\nSET price = pricing.new_price\\nFROM pricing\\nWHERE pricing.product_id = products.id\\n  AND pricing.effective_date <= date('now');\\n```. "
        "Pre-3.33: subquery — `UPDATE products SET price = (SELECT new_price FROM pricing WHERE pricing.product_id = products.id);`. "
        "RETURNING clause (3.35+): `UPDATE ... RETURNING id, price;` gives back updated rows.",
        f"{BASE}/lang_update.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: DELETE with subquery / DELETE returning",
        "Subquery: `DELETE FROM users WHERE id IN (SELECT id FROM users_to_delete);`. "
        "Range: `DELETE FROM logs WHERE created_at < date('now', '-30 days');`. "
        "Soft delete: `UPDATE users SET deleted_at = datetime('now') WHERE id = ?;` is usually better than hard DELETE. "
        "RETURNING (3.35+): `DELETE FROM users WHERE id = 5 RETURNING *;`. "
        "TRUNCATE-like: SQLite has no TRUNCATE — use `DELETE FROM users;` then `VACUUM;` to reclaim space.",
        f"{BASE}/lang_delete.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: common table expressions (CTE)",
        "```sql\\nWITH active_users AS (\\n    SELECT * FROM users WHERE deleted_at IS NULL\\n),\\nrecent_orders AS (\\n    SELECT * FROM orders WHERE created_at > date('now', '-7 days')\\n)\\nSELECT u.name, COUNT(o.id) AS order_count\\nFROM active_users u\\nLEFT JOIN recent_orders o ON o.user_id = u.id\\nGROUP BY u.id;\\n```. "
        "Improves readability for complex queries. "
        "Each CTE is computed once, then referenced.",
        f"{BASE}/lang_with.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: recursive CTE (graph / tree traversal)",
        "Generate series: `WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n WHERE i < 10) SELECT * FROM n;`. "
        "Tree traversal: ```sql\\nWITH RECURSIVE descendants AS (\\n    SELECT id, parent_id, name FROM categories WHERE id = 1\\n    UNION ALL\\n    SELECT c.id, c.parent_id, c.name FROM categories c\\n    JOIN descendants d ON c.parent_id = d.id\\n)\\nSELECT * FROM descendants;\\n```. "
        "Always include a terminating condition.",
        f"{BASE}/lang_with.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: window functions",
        "Row number: `SELECT name, ROW_NUMBER() OVER (ORDER BY score DESC) AS rank FROM players;`. "
        "Running total: `SELECT day, amount, SUM(amount) OVER (ORDER BY day) AS cumulative FROM sales;`. "
        "Partition: `SELECT category, name, RANK() OVER (PARTITION BY category ORDER BY price DESC) AS rk FROM products;`. "
        "Lag/lead: `SELECT day, amount, LAG(amount, 1) OVER (ORDER BY day) AS prev FROM sales;`. "
        "Available 3.25+ (2018).",
        f"{BASE}/windowfunctions.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: JSON queries with JSON1",
        "Extract: `SELECT json_extract(data, '$.name') FROM events;`. "
        "Modern operator (3.38+): `SELECT data -> '$.name' FROM events;` (returns JSON), `SELECT data ->> '$.name' FROM events;` (returns scalar/text). "
        "Filter: `SELECT * FROM events WHERE data ->> '$.type' = 'login';`. "
        "Index for JSON path: `CREATE INDEX idx_event_type ON events(data ->> '$.type');`. "
        "Validate: `json_valid(data)` returns 1/0. "
        "Build: `json_object('name', 'alice', 'age', 30)`. "
        "Array: `json_array(1, 2, 3)`. "
        "Iterate: `json_each(data, '$.tags')` returns rows.",
        f"{BASE}/json1.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: full-text search with FTS5",
        "Create virtual table: `CREATE VIRTUAL TABLE docs_fts USING fts5(title, body);`. "
        "Insert: `INSERT INTO docs_fts (title, body) VALUES ('Title', 'Lorem ipsum');`. "
        "Search: `SELECT * FROM docs_fts WHERE docs_fts MATCH 'lorem';`. "
        "Phrase: `MATCH '\"lorem ipsum\"'`. "
        "Column-specific: `MATCH 'title:lorem'`. "
        "Ranking: `SELECT *, bm25(docs_fts) AS score FROM docs_fts WHERE docs_fts MATCH 'lorem' ORDER BY score;`. "
        "Highlight: `highlight(docs_fts, 1, '<b>', '</b>')`.",
        f"{BASE}/fts5.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: ATTACH multiple databases",
        "`ATTACH DATABASE 'other.db' AS other;`. "
        "Cross-DB query: `SELECT * FROM main.users JOIN other.events USING (user_id);`. "
        "Copy a table: `INSERT INTO main.users SELECT * FROM other.users;`. "
        "Detach: `DETACH other;`. "
        "For testing: `ATTACH ':memory:' AS scratch;` gives a temp DB. "
        "Limit: 10 attached by default; bump with SQLITE_LIMIT_ATTACHED.",
        f"{BASE}/lang_attach.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: EXPLAIN QUERY PLAN",
        "`EXPLAIN QUERY PLAN SELECT * FROM users WHERE email = 'x';`. "
        "Output shows whether SQLite uses an index or a table scan. "
        "Look for `USING INDEX` (good) vs `SCAN TABLE` (often bad on large tables). "
        "Detailed bytecode: `EXPLAIN SELECT ...;` (low-level VDBE instructions). "
        "For tooling: tools like DBeaver and DataGrip show query plans graphically. "
        "Improve plans: add indexes for WHERE/JOIN columns, run `ANALYZE` to update stats.",
        f"{BASE}/lang_explain.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: VACUUM to reclaim space + defragment",
        "`VACUUM;` rewrites entire DB — reclaims space from deleted rows, defragments, repacks. "
        "Cost: doubles disk during operation, can be slow on huge DBs. "
        "Auto-VACUUM (incremental): `PRAGMA auto_vacuum = INCREMENTAL;` (must be set at DB creation), then `PRAGMA incremental_vacuum(N);` periodically. "
        "FULL auto: `PRAGMA auto_vacuum = FULL;` — slower writes but always compact. "
        "Quick win: `VACUUM INTO 'compact.db';` creates compacted copy without locking original long.",
        f"{BASE}/lang_vacuum.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: transactions (BEGIN / COMMIT / ROLLBACK)",
        "Explicit: `BEGIN; INSERT ...; UPDATE ...; COMMIT;`. "
        "Or rollback: `BEGIN; ...; ROLLBACK;`. "
        "Modes: `BEGIN IMMEDIATE;` (acquires reserved lock immediately — prevents SQLITE_BUSY at COMMIT in WAL mode); `BEGIN EXCLUSIVE;` (excludes other readers too — rarely needed in WAL). "
        "Implicit: every statement outside BEGIN is its own transaction. "
        "For bulk inserts: wrap in single transaction — 100-1000x faster than per-row commits.",
        f"{BASE}/lang_transaction.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: SAVEPOINT for nested transactions",
        "```sql\\nBEGIN;\\nINSERT INTO outer ...;\\nSAVEPOINT inner;\\nINSERT INTO middle ...;\\n  SAVEPOINT inner2;\\n  INSERT INTO leaf ...;\\n  ROLLBACK TO inner2; -- undo leaf insert\\nRELEASE inner; -- commit middle\\nCOMMIT; -- commit outer\\n```. "
        "ROLLBACK TO doesn't end transaction — continues. "
        "RELEASE merges savepoint into parent. "
        "Use case: partial undo in long transactions.",
        f"{BASE}/lang_savepoint.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: date/time functions",
        "Current: `SELECT datetime('now');` (UTC by default). "
        "Local: `SELECT datetime('now', 'localtime');`. "
        "Format: `SELECT strftime('%Y-%m-%d %H:%M', 'now');`. "
        "Parse: `SELECT datetime('2026-06-01T12:00:00Z');`. "
        "Arithmetic: `SELECT date('now', '-7 days');` or `SELECT datetime('now', '+1 month', '-2 hours');`. "
        "Julian day: `SELECT julianday('now') - julianday('2026-01-01');` (days between).",
        f"{BASE}/lang_datefunc.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: generated columns (computed fields)",
        "```sql\\nCREATE TABLE products (\\n    id INTEGER PRIMARY KEY,\\n    price REAL,\\n    tax_rate REAL,\\n    total_price REAL GENERATED ALWAYS AS (price * (1 + tax_rate)) VIRTUAL\\n);\\n```. "
        "VIRTUAL: computed on read (no disk cost, no index possible without expression-index). "
        "STORED: computed on write, stored on disk (can be indexed). "
        "Use case: denormalize derived fields without app-level logic. "
        "Cannot be INSERT/UPDATE'd directly.",
        f"{BASE}/lang_createtable.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: triggers (audit, derived, constraint)",
        "```sql\\nCREATE TRIGGER audit_user_update\\nAFTER UPDATE ON users\\nFOR EACH ROW\\nBEGIN\\n    INSERT INTO audit_log (user_id, changed_at, old_email)\\n    VALUES (NEW.id, datetime('now'), OLD.email);\\nEND;\\n```. "
        "Events: BEFORE/AFTER + INSERT/UPDATE/DELETE. "
        "`NEW.col` / `OLD.col` references row state. "
        "Drop: `DROP TRIGGER audit_user_update;`. "
        "Disable temporarily: no native disable — drop + recreate.",
        f"{BASE}/lang_createtable.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: schema migrations with user_version",
        "Pattern: ```sql\\nPRAGMA user_version;  -- returns current version (default 0)\\nBEGIN;\\nALTER TABLE users ADD COLUMN bio TEXT;\\nPRAGMA user_version = 2;\\nCOMMIT;\\n```. "
        "App reads version on startup + applies migrations in order. "
        "For complex changes (drop column pre-3.35, change CHECK): 12-step recreate-table process. "
        "Tools: golang-migrate, alembic (SQLAlchemy), Atlas, sqlite-utils.",
        f"{BASE}/pragma.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: Python sqlite3 module basics",
        "```python\\nimport sqlite3\\nconn = sqlite3.connect('mydb.db')\\nconn.execute('PRAGMA foreign_keys = ON')\\nconn.row_factory = sqlite3.Row  # dict-like rows\\ncur = conn.cursor()\\ncur.execute('SELECT * FROM users WHERE email = ?', ('alice@x',))\\nfor row in cur:\\n    print(row['name'])\\nwith conn:  # context manager auto-commits or rolls back\\n    conn.execute('INSERT INTO users (name, email) VALUES (?, ?)', ('Bob', 'b@x'))\\nconn.close()\\n```",
        f"{BASE}/cli.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: optimize for read-heavy workloads",
        "Combined PRAGMAs for read-tuning: ```sql\\nPRAGMA journal_mode = WAL;\\nPRAGMA synchronous = NORMAL;  -- vs FULL (default) — minor durability trade-off\\nPRAGMA cache_size = -64000;   -- 64MB cache (negative = KiB)\\nPRAGMA temp_store = MEMORY;   -- temp tables in RAM\\nPRAGMA mmap_size = 268435456; -- 256MB memory-mapped I/O\\n```. "
        "Run `ANALYZE;` after bulk loads to refresh query planner stats.",
        f"{BASE}/pragma.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: STRICT tables for type safety (3.37+)",
        "`CREATE TABLE users (id INTEGER, name TEXT NOT NULL) STRICT;`. "
        "In STRICT mode: declared types are enforced. INSERT 'abc' into INTEGER column → error. "
        "Allowed types: INT, INTEGER, REAL, TEXT, BLOB, ANY. "
        "Use case: when you want type safety like Postgres. "
        "Trade-off: less flexible — many SQLite-isms (storing arbitrary data in any column) don't work.",
        f"{BASE}/lang_createtable.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: RETURNING clause (3.35+) for INSERT/UPDATE/DELETE",
        "Get back what was affected: `INSERT INTO users (name) VALUES ('Alice') RETURNING id, created_at;`. "
        "On UPDATE: `UPDATE products SET price = price * 1.1 WHERE category = 'a' RETURNING id, price AS new_price;`. "
        "On DELETE: `DELETE FROM events WHERE created_at < date('now', '-30 days') RETURNING id;`. "
        "Use case: get the auto-incremented ID without separate query (`last_insert_rowid()` works too but RETURNING is per-statement).",
        f"{BASE}/lang_insert.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: bulk insert performance",
        "```sql\\nBEGIN;\\nINSERT INTO t (a, b) VALUES (...), (...), (...);  -- bulk values\\n-- ... up to 500 rows per INSERT\\nCOMMIT;\\n```. "
        "1000-10000x faster than per-row commits. "
        "For huge: read file in Python + use `executemany`: `cur.executemany('INSERT ...', rows)`. "
        "Pre-create indexes AFTER bulk load (insert without index, then `CREATE INDEX`). "
        "Use `PRAGMA synchronous = OFF;` + WAL for fastest (risky — data loss on power failure).",
        f"{BASE}/lang_insert.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: check + verify database integrity",
        "Quick: `PRAGMA quick_check;` — fast, catches most issues. "
        "Thorough: `PRAGMA integrity_check;` — full check (slow on huge DBs). "
        "Foreign keys: `PRAGMA foreign_key_check;` — lists rows violating FK. "
        "Stats: `ANALYZE;` updates the planner's stats. "
        "Run periodically (daily/weekly) on production DBs.",
        f"{BASE}/pragma.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: identifying slow queries",
        "Enable timer: `.timer on` in CLI shows duration per query. "
        "For deeper: `EXPLAIN QUERY PLAN <query>` shows execution strategy. "
        "Find missing indexes: any `SCAN TABLE` on a large table is suspect. "
        "Update stats: `ANALYZE;` (re-run after bulk inserts). "
        "Page cache hit ratio: `PRAGMA cache_size` affects this; bigger cache = fewer disk reads.",
        f"{BASE}/lang_explain.html",
    ),
    (
        "sqlite3-recipes", "sqlite3 recipe: production-ready sqlite.db config",
        "On every connection: ```sql\\nPRAGMA journal_mode = WAL;\\nPRAGMA synchronous = NORMAL;\\nPRAGMA foreign_keys = ON;\\nPRAGMA busy_timeout = 5000;\\nPRAGMA cache_size = -64000;\\nPRAGMA temp_store = MEMORY;\\n```. "
        "Daily: `PRAGMA optimize;` (3.18+ — incremental stats update). "
        "Weekly: `PRAGMA wal_checkpoint(TRUNCATE);` (keeps WAL bounded). "
        "Monthly: `VACUUM;` (if heavy DELETEs) or `VACUUM INTO 'compact.db';` for online compaction.",
        f"{BASE}/wal.html",
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
            evidence_id=f"ev-sqlite-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-sqlite-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="sqlite3-catalog",
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
