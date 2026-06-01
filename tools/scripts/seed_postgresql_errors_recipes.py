"""Direct-insert postgresql error + recipe claims."""

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

BASE = "https://www.postgresql.org/docs/current"

CLAIMS = [
    # ============================================================
    # postgresql-errors — 30 common errors
    # ============================================================
    (
        "postgresql-errors", "postgresql error: FATAL: role \"<name>\" does not exist",
        "Connecting as a user that doesn't exist in PostgreSQL. "
        "Fix: (1) list roles: `psql -U postgres -c '\\du'`; "
        "(2) create the role: `createuser <name>` or `psql -c \"CREATE ROLE <name> LOGIN PASSWORD '...';\"`; "
        "(3) default OS-user fallback: on Linux/macOS, if no `-U` is given psql uses your OS username — create a matching role or pass `-U postgres`; "
        "(4) for a brand-new install: the `postgres` superuser is auto-created — switch to it: `sudo -u postgres psql`.",
        f"{BASE}/app-createuser.html",
    ),
    (
        "postgresql-errors", "postgresql error: FATAL: database \"<name>\" does not exist",
        "Trying to connect to a database that hasn't been created. "
        "Fix: (1) list databases: `psql -U postgres -l`; "
        "(2) create it: `createdb <name>` or `CREATE DATABASE <name>;` inside psql; "
        "(3) connect to a default DB first: `psql -U postgres -d postgres`; "
        "(4) connection URIs: `postgres://user@host/dbname` — `dbname` is required.",
        f"{BASE}/app-createdb.html",
    ),
    (
        "postgresql-errors", "postgresql error: FATAL: password authentication failed for user",
        "Wrong password (or wrong auth method configured for this user/host). "
        "Fix: (1) reset password as superuser: `ALTER USER <name> WITH PASSWORD '...';`; "
        "(2) check pg_hba.conf — the matching row might require `peer`, `ident`, `md5`, or `scram-sha-256`; "
        "(3) for scripting: use .pgpass (`~/.pgpass` with mode 0600, format `host:port:db:user:password`); "
        "(4) env var: `PGPASSWORD=...` (insecure on shared systems — prefer .pgpass); "
        "(5) for clients on the same machine: peer auth uses OS user (`sudo -u postgres psql`).",
        f"{BASE}/auth-password.html",
    ),
    (
        "postgresql-errors", "postgresql error: could not connect to server: Connection refused / socket failed",
        "Server isn't running, OR not listening on the address you're trying. "
        "Fix: (1) `pg_isready -h <host> -p <port>` — should print 'accepting connections'; "
        "(2) start server: macOS `brew services start postgresql`; Linux `sudo systemctl start postgresql`; Docker `docker start <container>`; "
        "(3) check what address it's bound to: `postgresql.conf` `listen_addresses` (default `localhost` — must include external IPs/`*` for remote); "
        "(4) restart after changing listen_addresses: `pg_ctl reload` (or restart); "
        "(5) for Docker: ensure port mapped: `-p 5432:5432`.",
        f"{BASE}/app-pg-isready.html",
    ),
    (
        "postgresql-errors", "postgresql error: FATAL: no pg_hba.conf entry for host",
        "pg_hba.conf doesn't have a matching rule for your connection's host/user/database. "
        "Fix: (1) find pg_hba.conf: `psql -U postgres -t -c \"SHOW hba_file\"`; "
        "(2) add a line: `host    mydb    myuser    192.168.1.0/24    scram-sha-256`; "
        "(3) reload: `pg_ctl reload` (or `SELECT pg_reload_conf();`); "
        "(4) rule order matters — first match wins; "
        "(5) for Docker images: pg_hba.conf is usually in /var/lib/postgresql/data/pg_hba.conf inside the container.",
        f"{BASE}/auth-pg-hba-conf.html",
    ),
    (
        "postgresql-errors", "postgresql error: server closed the connection unexpectedly",
        "Server crashed mid-query OR connection was severed (network blip, OOM kill, restart). "
        "Fix: (1) check server logs (`log_directory` in postgresql.conf — often `/var/log/postgresql/`); "
        "(2) common: OOM killer — check `dmesg | grep postgres` (raise `shared_buffers` cap appropriately or reduce other memory load); "
        "(3) network: middleware (load balancer, pgbouncer) may have idle-timeout — adjust client-side keepalive; "
        "(4) restart server: `sudo systemctl restart postgresql`.",
        f"{BASE}/runtime-config-resource.html",
    ),
    (
        "postgresql-errors", "postgresql error: ERROR: permission denied for table / relation",
        "Role lacks the required privilege on the target object. "
        "Fix: (1) grant the privilege: `GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE <table> TO <role>;`; "
        "(2) for all current tables: `GRANT ... ON ALL TABLES IN SCHEMA public TO <role>;`; "
        "(3) for future tables (default privileges): `ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ... TO <role>;`; "
        "(4) check existing: `\\dp <table>` in psql shows ACL; "
        "(5) for table-owner concerns: `ALTER TABLE <table> OWNER TO <role>;`.",
        f"{BASE}/app-psql.html",
    ),
    (
        "postgresql-errors", "postgresql error: ERROR: relation \"<X>\" does not exist",
        "Table/view/sequence not found in the current search_path. "
        "Fix: (1) check current search_path: `SHOW search_path;`; "
        "(2) qualify with schema: `SELECT * FROM myschema.mytable`; "
        "(3) add to search_path: `SET search_path TO myschema, public;` (session-only) or `ALTER DATABASE mydb SET search_path TO ...` (persistent); "
        "(4) check case: PostgreSQL lowercases unquoted identifiers — `\"MyTable\"` (with quotes) is different from `mytable`; "
        "(5) list tables: `\\dt *.<pattern>` in psql.",
        f"{BASE}/app-psql.html",
    ),
    (
        "postgresql-errors", "postgresql error: ERROR: duplicate key value violates unique constraint",
        "INSERT or UPDATE would create a duplicate where a UNIQUE constraint (often the primary key) forbids it. "
        "Fix: (1) UPSERT instead: `INSERT ... ON CONFLICT (col) DO UPDATE SET ...` (or `DO NOTHING`); "
        "(2) check existing: `SELECT * FROM tbl WHERE key = '...'`; "
        "(3) for sequences out of sync after data import: `SELECT setval('tbl_id_seq', (SELECT MAX(id) FROM tbl));`; "
        "(4) for soft-delete: include `deleted_at IS NULL` in the unique constraint via partial index.",
        f"{BASE}/app-psql.html",
    ),
    (
        "postgresql-errors", "postgresql error: ERROR: insert/update on table violates foreign key constraint",
        "Referenced row doesn't exist (insert) or is being deleted (delete cascade). "
        "Fix: (1) ensure parent row exists first: `INSERT INTO parent (id, ...) ... ;` THEN `INSERT INTO child ... ;`; "
        "(2) for circular: defer with `DEFERRABLE INITIALLY DEFERRED` on the FK + `SET CONSTRAINTS ... DEFERRED` in transaction; "
        "(3) for cascading deletes: `ON DELETE CASCADE` in FK definition; "
        "(4) inspect the constraint: `\\d+ <table>` in psql.",
        f"{BASE}/app-psql.html",
    ),
    (
        "postgresql-errors", "postgresql error: ERROR: deadlock detected",
        "Two transactions waiting on locks held by each other. PostgreSQL aborts one. "
        "Fix: (1) retry the aborted transaction (often the right move at app layer); "
        "(2) consistent locking order: always lock tables/rows in the same order across all transactions; "
        "(3) for batch updates: process in sorted ID order; "
        "(4) reduce transaction time: keep transactions short, avoid user input mid-transaction; "
        "(5) for debugging: `log_lock_waits = on` + `deadlock_timeout` (default 1s) in postgresql.conf.",
        f"{BASE}/runtime-config-logging.html",
    ),
    (
        "postgresql-errors", "postgresql error: ERROR: canceling statement due to statement timeout",
        "Query took longer than `statement_timeout`. "
        "Fix: (1) check current: `SHOW statement_timeout;`; "
        "(2) raise (session): `SET statement_timeout = '5min';`; "
        "(3) raise globally in postgresql.conf: `statement_timeout = 5min`; "
        "(4) but: if a query NEEDS 5 min, optimize it first — `EXPLAIN ANALYZE` shows hotspots; "
        "(5) for per-query exception: `SET LOCAL statement_timeout = 0;` inside a transaction (no limit).",
        f"{BASE}/runtime-config-query.html",
    ),
    (
        "postgresql-errors", "postgresql error: ERROR: column \"<X>\" does not exist (HINT: Perhaps you meant ...)",
        "Column name typo OR case-sensitivity issue. "
        "Fix: (1) PostgreSQL folds unquoted identifiers to lowercase: `\"FooBar\"` != `foobar`; "
        "(2) check actual columns: `\\d+ <table>` in psql; "
        "(3) the HINT often nails the typo — listen to it; "
        "(4) if column was created with double quotes: it's case-sensitive forever — quote it in every query.",
        f"{BASE}/app-psql.html",
    ),
    (
        "postgresql-errors", "postgresql error: ERROR: syntax error at or near \"<X>\"",
        "SQL parse error. PostgreSQL points at the location. "
        "Fix: (1) re-read the query — usually a missing comma, extra paren, or reserved word; "
        "(2) reserved words: `user`, `order`, `select` etc need double-quoting if used as identifiers; "
        "(3) for psql multi-line: ensure statement terminator `;` is present; "
        "(4) common: backslash commands inside `psql -c` don't work — those are psql-only, not SQL.",
        f"{BASE}/app-psql.html",
    ),
    (
        "postgresql-errors", "postgresql error: ERROR: invalid input syntax for type <X>",
        "Value doesn't parse for the declared column type. "
        "Fix: (1) for date/time: use ISO 8601: `'2026-01-15'` not `'15/01/2026'`; "
        "(2) for boolean: `true`/`false` or `'t'`/`'f'` (no quotes for bool literal); "
        "(3) for UUID: must be 36-char canonical form; "
        "(4) for jsonb: must be valid JSON: `'{\"key\":\"value\"}'`; "
        "(5) cast explicitly: `'2026-01-15'::date`.",
        f"{BASE}/app-psql.html",
    ),
    (
        "postgresql-errors", "postgresql error: ERROR: out of shared memory / max_locks_per_transaction",
        "Transaction acquired more locks than `max_locks_per_transaction × max_connections + max_prepared_transactions` allows. Usually from touching huge numbers of partitions / temp tables. "
        "Fix: (1) raise in postgresql.conf: `max_locks_per_transaction = 256` (default 64) — REQUIRES RESTART; "
        "(2) reduce locks: drop unused partitions, batch DDL operations; "
        "(3) for `DROP TABLE` cascading many partitions: split into smaller batches.",
        f"{BASE}/runtime-config-resource.html",
    ),
    (
        "postgresql-errors", "postgresql error: ERROR: too many connections for role / database / cluster",
        "Hit `max_connections`, `CONNECTION LIMIT` on the role, or `CONNECTION LIMIT` on the database. "
        "Fix: (1) check active: `SELECT count(*) FROM pg_stat_activity;`; "
        "(2) by user: `SELECT usename, count(*) FROM pg_stat_activity GROUP BY usename;`; "
        "(3) kill idle: `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state = 'idle' AND state_change < now() - interval '1 hour';`; "
        "(4) raise limit: `max_connections = 200` in postgresql.conf (REQUIRES RESTART); "
        "(5) better long-term: connection pooler (pgbouncer, pgpool).",
        f"{BASE}/runtime-config-connection.html",
    ),
    (
        "postgresql-errors", "postgresql error: ERROR: cannot drop the currently open database",
        "DROP DATABASE refuses to drop the database you're connected to. "
        "Fix: (1) connect to a different DB first: `\\c postgres` or `psql -d postgres`; "
        "(2) then: `DROP DATABASE mydb;`; "
        "(3) for stuck connections: terminate first: `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'mydb' AND pid <> pg_backend_pid();`; "
        "(4) force drop (PG 13+): `DROP DATABASE mydb WITH (FORCE);`.",
        f"{BASE}/app-dropdb.html",
    ),
    (
        "postgresql-errors", "postgresql error: ERROR: SSL connection required / SSL error / certificate verify",
        "Server requires SSL, OR client and server disagree on TLS verification. "
        "Fix: (1) for required SSL: ensure `sslmode=require` or higher in connection: `psql 'host=db sslmode=require user=u'`; "
        "(2) verify cert chain: `sslmode=verify-full` (most secure) + `sslrootcert=/path/to/ca.pem`; "
        "(3) for self-signed (dev): `sslmode=require` (no verification); "
        "(4) server config: `ssl = on` + `ssl_cert_file` + `ssl_key_file` in postgresql.conf; "
        "(5) test: `openssl s_client -connect host:5432 -starttls postgres`.",
        f"{BASE}/runtime-config-connection.html",
    ),
    (
        "postgresql-errors", "postgresql error: pg_dump: error: query failed / connection lost mid-dump",
        "Long-running dump got interrupted (network, server restart, OOM). "
        "Fix: (1) for big DBs: use `--format=directory --jobs=N` for parallel dumps that resume better; "
        "(2) for huge tables: `--exclude-table=<large_table>` + dump separately; "
        "(3) use a replica for dumps to avoid loading primary; "
        "(4) raise `statement_timeout` for the pg_dump session; "
        "(5) for very long: take a physical backup with pg_basebackup instead — much faster.",
        f"{BASE}/app-pgdump.html",
    ),
    (
        "postgresql-errors", "postgresql error: pg_restore: error: could not read from input file",
        "Restore can't read the dump file. "
        "Fix: (1) check file exists + readable: `ls -la dump.tar`; "
        "(2) for custom format (-Fc): use `pg_restore`; for plain SQL: use `psql -f`; "
        "(3) for directory format: `pg_restore -d mydb ./dump_dir/`; "
        "(4) compression: pg_restore handles `.dump` (custom) and `.tar`; for `.sql.gz`: `gunzip -c dump.sql.gz | psql -d mydb`; "
        "(5) for empty target DB: create first: `createdb mydb`.",
        f"{BASE}/app-pg-restore.html",
    ),
    (
        "postgresql-errors", "postgresql error: pg_upgrade: error: cluster is not compatible",
        "Major version upgrade pre-check failed. "
        "Fix: (1) read pre-check output — it lists specific issues (deprecated types, extensions, etc.); "
        "(2) common: extensions need updating in source cluster first: `ALTER EXTENSION ... UPDATE;`; "
        "(3) drop extensions you don't need; "
        "(4) for plpython2: removed in PG 13+ — must rewrite to plpython3u; "
        "(5) test on a clone first: `pg_basebackup` to a staging server + run pg_upgrade there.",
        f"{BASE}/app-pg-upgrade.html",
    ),
    (
        "postgresql-errors", "postgresql error: ERROR: tuple concurrently updated",
        "Two transactions tried to modify the same row in incompatible ways (often during DDL + DML races). "
        "Fix: (1) retry: usually transient; "
        "(2) for serialization issues: use `SELECT ... FOR UPDATE` to lock rows explicitly; "
        "(3) for advisory locks across transactions: `SELECT pg_advisory_lock(N);`; "
        "(4) for CONCURRENT index builds + writes: the CONCURRENT index build pauses for active transactions — short retry helps.",
        f"{BASE}/app-psql.html",
    ),
    (
        "postgresql-errors", "postgresql error: WARNING: there is no transaction in progress",
        "COMMIT or ROLLBACK issued without a matching BEGIN. Harmless but noisy. "
        "Fix: (1) drop stray COMMIT/ROLLBACK from script; "
        "(2) check autocommit settings: psql `\\set AUTOCOMMIT off` makes every statement implicit-transactional; "
        "(3) for migrations: ensure BEGIN/COMMIT bracket each step properly.",
        f"{BASE}/app-psql.html",
    ),
    (
        "postgresql-errors", "postgresql error: ERROR: prepared transaction with identifier already exists",
        "Stale prepared transaction blocks new one with same name (often from crashed app + 2PC). "
        "Fix: (1) list: `SELECT * FROM pg_prepared_xacts;`; "
        "(2) commit if safe: `COMMIT PREPARED 'xact-name';`; "
        "(3) rollback otherwise: `ROLLBACK PREPARED 'xact-name';`; "
        "(4) if app shouldn't use 2PC: disable in postgresql.conf: `max_prepared_transactions = 0` (REQUIRES RESTART).",
        f"{BASE}/runtime-config-resource.html",
    ),
    (
        "postgresql-errors", "postgresql error: ERROR: canceling statement due to user request",
        "Someone pressed Ctrl-C (psql) or called `pg_cancel_backend(pid)`. Not really an error — confirms the cancel worked. "
        "Fix: (1) ignore if intentional; "
        "(2) for unwanted cancels: check if a monitoring tool is sending cancels (pg_terminate_backend on slow queries); "
        "(3) `statement_timeout` produces a different message: 'canceling statement due to statement timeout'.",
        f"{BASE}/app-psql.html",
    ),
    (
        "postgresql-errors", "postgresql error: WAL contains references to invalid pages / catalog tables are corrupted",
        "Cluster corruption — usually from unclean shutdown, hardware failure, or interrupted upgrade. "
        "Fix: (1) STOP USING THE CLUSTER for writes; "
        "(2) take an immediate filesystem-level backup; "
        "(3) try recovery: `pg_ctl start` with `--allow-recovery-conf` may help; "
        "(4) pg_resetwal is LAST RESORT — destroys data: only after a backup; "
        "(5) restore from last good backup if available.",
        f"{BASE}/app-pg-resetwal.html",
    ),
    (
        "postgresql-errors", "postgresql error: ERROR: type \"<X>\" does not exist",
        "Custom type or extension not present in this database. "
        "Fix: (1) list installed extensions: `\\dx` in psql; "
        "(2) create extension: `CREATE EXTENSION IF NOT EXISTS uuid-ossp;` (or `pg_trgm`, `vector`, etc.); "
        "(3) for custom types: must be created with `CREATE TYPE ...`; "
        "(4) for extension types not loaded in current schema: try `CREATE EXTENSION ... WITH SCHEMA public;` then `SET search_path TO public, my_schema;`.",
        f"{BASE}/app-psql.html",
    ),
    (
        "postgresql-errors", "postgresql error: pg_basebackup: error: requested wal_level too low",
        "pg_basebackup needs `wal_level = replica` (or higher) and `max_wal_senders > 0`. "
        "Fix: (1) postgresql.conf: `wal_level = replica` and `max_wal_senders = 10` (REQUIRES RESTART); "
        "(2) pg_hba.conf: add `host replication <user> <ip> scram-sha-256`; "
        "(3) reload + restart; "
        "(4) verify: `SHOW wal_level;` returns replica or logical.",
        f"{BASE}/runtime-config-replication.html",
    ),
    (
        "postgresql-errors", "postgresql error: error: failed to fetch table data / pg_dump on huge table",
        "Network timeout or memory exhaustion on huge table. "
        "Fix: (1) tweak network keepalive on the connection: `--tcp-keepalives-idle=60`; "
        "(2) use parallel dump: `pg_dump --format=directory --jobs=4`; "
        "(3) exclude the huge table + dump it separately with COPY for streaming; "
        "(4) for tables with many large LOBs: increase `work_mem` for the dump session.",
        f"{BASE}/app-pgdump.html",
    ),

    # ============================================================
    # postgresql-recipes — 35 common workflows
    # ============================================================
    (
        "postgresql-recipes", "postgresql recipe: connect to a database (psql)",
        "Local: `psql -d mydb` (uses OS user as default role). "
        "Specific user: `psql -U myuser -d mydb`. "
        "Remote: `psql -h db.example.com -p 5432 -U myuser -d mydb`. "
        "Connection URI: `psql 'postgres://user:pass@host:5432/dbname?sslmode=require'`. "
        "From service file: `psql 'service=prod'` (reads ~/.pg_service.conf). "
        "Once in psql: `\\?` for help, `\\q` to quit.",
        f"{BASE}/app-psql.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: use .pgpass for credentials (no PGPASSWORD in env)",
        "Create `~/.pgpass` with mode 0600: ```\\nhost:port:database:user:password\\n*:*:*:myuser:mypassword\\n```. "
        "Permissions critical: `chmod 600 ~/.pgpass` (psql refuses if too open). "
        "Wildcards: `*` matches anything. "
        "On Windows: `%APPDATA%\\postgresql\\pgpass.conf`. "
        "Now `psql -U myuser -h db.example.com` doesn't prompt.",
        f"{BASE}/libpq-pgservice.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: use libpq environment variables",
        "Set per-shell so every client picks them up. "
        "`export PGHOST=db.example.com` (or `PGHOSTADDR` for IP). "
        "`export PGPORT=5432`. "
        "`export PGUSER=myuser`. "
        "`export PGDATABASE=mydb`. "
        "`export PGSSLMODE=require`. "
        "After: `psql` (no args) connects to the env-configured target. "
        "Avoid `PGPASSWORD` (visible in `ps`) — use .pgpass.",
        f"{BASE}/libpq-envars.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: create a database + user with appropriate grants",
        "```bash\\nsudo -u postgres psql -c \"CREATE USER appuser WITH PASSWORD 'secret';\"\\nsudo -u postgres psql -c \"CREATE DATABASE appdb OWNER appuser;\"\\nsudo -u postgres psql -d appdb -c \"GRANT ALL PRIVILEGES ON SCHEMA public TO appuser;\"\\n```. "
        "For least-privilege apps (separate read-only role): create the role, then `GRANT SELECT ON ALL TABLES IN SCHEMA public TO ...;` + `ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO ...;` for future tables.",
        f"{BASE}/app-createuser.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: list databases / tables / schemas in psql",
        "Databases: `\\l` (or `\\list`). "
        "Tables in current DB: `\\dt` (just tables). "
        "All in schema: `\\dt schema.*` or `\\dt *.*` (across schemas). "
        "Schemas: `\\dn`. "
        "Indexes: `\\di`. "
        "Sequences: `\\ds`. "
        "Views: `\\dv`. "
        "Functions: `\\df`. "
        "Roles/users: `\\du`. "
        "Extensions: `\\dx`.",
        f"{BASE}/app-psql.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: describe a table (columns + indexes + constraints)",
        "Basic: `\\d <table>` (columns + types). "
        "Full: `\\d+ <table>` (also: storage, stats target, description, partitions). "
        "Just indexes on table: `\\di+ <table>*`. "
        "Just functions: `\\df mypkg.*`. "
        "For SQL-only (cross-app): query `information_schema.columns` or `pg_attribute`.",
        f"{BASE}/app-psql.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: run a query from a file / single command",
        "From file: `psql -d mydb -f script.sql`. "
        "Single command: `psql -d mydb -c \"SELECT count(*) FROM users;\"`. "
        "Multiple commands: `psql -d mydb -c \"BEGIN; UPDATE ...; COMMIT;\"`. "
        "From stdin: `cat script.sql | psql -d mydb`. "
        "With variables: `psql -d mydb -v name='alice' -c \"SELECT * FROM users WHERE name = :'name'\"`. "
        "Quiet mode (for scripts): `-q` (suppress notices).",
        f"{BASE}/app-psql.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: export query results to CSV",
        "Inside psql: `\\copy (SELECT * FROM users WHERE active) TO 'users.csv' CSV HEADER`. "
        "Server-side (postgres user owns file): `COPY (SELECT * FROM users) TO '/tmp/users.csv' CSV HEADER;`. "
        "From bash: `psql -d mydb -c \"\\copy (SELECT * FROM users) TO 'users.csv' CSV HEADER\"`. "
        "Tab-separated: `WITH (FORMAT csv, DELIMITER E'\\t')`. "
        "No header: omit `HEADER`.",
        f"{BASE}/app-psql.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: import data from CSV",
        "Inside psql: `\\copy users FROM 'users.csv' CSV HEADER`. "
        "Specific columns: `\\copy users (name, email) FROM 'users.csv' CSV HEADER`. "
        "Server-side (only if you have file-system access on the server): `COPY users FROM '/tmp/users.csv' CSV HEADER;`. "
        "Handle NULLs: `WITH (NULL 'NULL')` treats literal 'NULL' as NULL. "
        "For huge files: use COPY (orders of magnitude faster than INSERTs).",
        f"{BASE}/app-psql.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: dump a database (pg_dump)",
        "Custom format (compressed, parallel-restorable, recommended): `pg_dump -d mydb -Fc -f mydb.dump`. "
        "Plain SQL (huge, but human-readable + can restore with psql): `pg_dump -d mydb > mydb.sql`. "
        "Directory format (parallel-dumpable): `pg_dump -d mydb -Fd -j 4 -f mydb_dir/`. "
        "Schema only: `--schema-only`. "
        "Data only: `--data-only`. "
        "Specific tables: `-t users -t orders`. "
        "Exclude: `-T audit_log`.",
        f"{BASE}/app-pgdump.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: restore from a pg_dump file",
        "Custom format (.dump): `pg_restore -d mydb mydb.dump` (target DB must exist). "
        "Parallel: `pg_restore -d mydb -j 4 mydb.dump`. "
        "Plain SQL (.sql): `psql -d mydb -f mydb.sql`. "
        "List contents without restoring: `pg_restore -l mydb.dump > toc.list`. "
        "Selective restore: edit toc.list (comment lines with `;`), then `pg_restore -d mydb -L toc.list mydb.dump`. "
        "Drop + create target first: include `-c -C`.",
        f"{BASE}/app-pg-restore.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: dump entire cluster (pg_dumpall)",
        "`pg_dumpall > cluster.sql` — all databases + global objects (roles, tablespaces). "
        "Just globals: `pg_dumpall --globals-only > globals.sql`. "
        "Just roles (no DBs): `pg_dumpall --roles-only > roles.sql`. "
        "Restore: `psql -f cluster.sql -d postgres` (use a default DB as entry point). "
        "For per-DB dump + globals separately: typical pattern is `pg_dumpall --globals-only` + `pg_dump -Fc` per database.",
        f"{BASE}/app-pg-dumpall.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: take a physical backup (pg_basebackup)",
        "`pg_basebackup -h db.example.com -U replica -D ./backup -Fp -Xs -P`. "
        "Args: `-D` destination dir, `-Fp` plain format, `-Xs` stream WAL during backup (consistent), `-P` show progress. "
        "Tar format (single file): `-Ft -z` (gzipped). "
        "Pre-req: postgresql.conf `wal_level = replica` + `max_wal_senders >= 1`; pg_hba.conf allow replication. "
        "Verify: `pg_verifybackup ./backup`.",
        f"{BASE}/app-pg-basebackup.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: major version upgrade (pg_upgrade)",
        "(1) Stop old + new clusters; "
        "(2) Pre-check: `pg_upgrade -b /usr/lib/postgresql/14/bin -B /usr/lib/postgresql/16/bin -d /var/lib/postgresql/14/main -D /var/lib/postgresql/16/main --check`; "
        "(3) Actual upgrade (no `--check` flag); "
        "(4) Use `--link` for fast in-place (uses hardlinks — old cluster becomes unusable); "
        "(5) Post-upgrade: run `vacuumdb --all --analyze-in-stages`; "
        "(6) For zero-downtime: use logical replication (pglogical, native logical) instead.",
        f"{BASE}/app-pg-upgrade.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: check server status / readiness",
        "`pg_isready -h <host> -p <port>` exit codes: 0 accepting, 1 rejecting, 2 not responding, 3 attempt failed. "
        "Suitable for health checks: `pg_isready -q && echo OK`. "
        "From inside server: `pg_ctl status -D /var/lib/postgresql/data`. "
        "Detailed: `SELECT * FROM pg_stat_activity;` (live connections, current queries).",
        f"{BASE}/app-pg-isready.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: vacuum + analyze a database",
        "Standard maintenance: `vacuumdb --analyze --all`. "
        "Per-database: `vacuumdb -d mydb --analyze`. "
        "Specific table: `psql -d mydb -c 'VACUUM (ANALYZE, VERBOSE) my_table;'`. "
        "Full (rebuilds — locks the table): `VACUUM FULL` — only for emergency space reclaim. "
        "After mass deletes: regular VACUUM marks space reusable; ANALYZE updates stats. "
        "Modern PG: autovacuum runs automatically — tune via `autovacuum_vacuum_scale_factor` etc.",
        f"{BASE}/app-vacuumdb.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: reindex a database (rebuild indexes)",
        "All indexes in DB: `reindexdb -d mydb`. "
        "Specific table's indexes: `psql -d mydb -c 'REINDEX TABLE my_table;'`. "
        "Concurrently (no exclusive lock — recommended for prod, PG 12+): `REINDEX TABLE CONCURRENTLY my_table;`. "
        "All databases: `reindexdb --all`. "
        "Use case: bloat after heavy updates, corruption after crash, switching collation.",
        f"{BASE}/app-reindexdb.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: use transactions safely",
        "Explicit: `BEGIN; UPDATE ...; SELECT ...; COMMIT;` or `ROLLBACK;`. "
        "Inside psql: `\\set ON_ERROR_ROLLBACK on` continues transaction on error (vs the default of aborting on first error). "
        "Savepoints: `SAVEPOINT s1; ... ROLLBACK TO s1; ... COMMIT;`. "
        "Isolation: `BEGIN ISOLATION LEVEL SERIALIZABLE;` (strictest) or `REPEATABLE READ`. "
        "Default isolation: READ COMMITTED.",
        f"{BASE}/app-psql.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: show + kill running queries",
        "List: `SELECT pid, usename, datname, state, query_start, query FROM pg_stat_activity WHERE state = 'active';`. "
        "Long-running: `... AND now() - query_start > interval '1 minute';`. "
        "Cancel (graceful, keeps connection): `SELECT pg_cancel_backend(<pid>);`. "
        "Terminate (kills connection): `SELECT pg_terminate_backend(<pid>);`. "
        "Use pg_cancel first; only pg_terminate if cancel doesn't work.",
        f"{BASE}/app-psql.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: query timing + EXPLAIN ANALYZE",
        "Time queries in psql: `\\timing` toggles on, then each query prints duration. "
        "Plan-only (no execution): `EXPLAIN SELECT ...;`. "
        "Plan + actual run: `EXPLAIN (ANALYZE, BUFFERS, VERBOSE) SELECT ...;`. "
        "Format: `EXPLAIN (ANALYZE, FORMAT JSON) ...` for tooling consumption. "
        "Read plan top-down: outer nodes consume inner. "
        "Better tools: https://explain.dalibo.com paste-and-explore.",
        f"{BASE}/runtime-config-query.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: use \\watch for repeated execution",
        "In psql: ```sql\\nSELECT count(*) FROM pg_stat_activity WHERE state = 'active';\\n\\watch 5\\n```. "
        "Refreshes every 5 seconds. Ctrl-C to stop. "
        "Use case: monitor active connections during a load test, watch a job complete.",
        f"{BASE}/app-psql.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: use \\set for psql variables",
        "Set: `\\set username 'alice'`. "
        "Use in SQL: `SELECT * FROM users WHERE name = :'username';` (`:'name'` quotes as string; `:name` injects raw). "
        "Show all: `\\set` (no args). "
        "Unset: `\\unset username`. "
        "Built-ins: `\\set ECHO_HIDDEN on` (show SQL behind `\\d`), `\\set AUTOCOMMIT off`.",
        f"{BASE}/app-psql.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: use pg_service.conf for connection profiles",
        "`~/.pg_service.conf`: ```ini\\n[prod]\\nhost=db.example.com\\nport=5432\\nuser=myuser\\ndbname=mydb\\nsslmode=require\\n\\n[dev]\\nhost=localhost\\nuser=myuser\\ndbname=mydb_dev\\n```. "
        "Then: `psql 'service=prod'` connects with all settings. "
        "Set as default: `PGSERVICE=prod` env var, then `psql` alone connects to prod. "
        "Combine with .pgpass for password.",
        f"{BASE}/libpq-pgservice.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: configure SSL connection",
        "Server side: `ssl = on` + `ssl_cert_file = '/path/server.crt'` + `ssl_key_file = '/path/server.key'` in postgresql.conf. "
        "Client side: connection URI `?sslmode=verify-full&sslrootcert=/path/ca.crt`. "
        "sslmode values: `disable`, `allow`, `prefer` (default), `require`, `verify-ca`, `verify-full`. "
        "`verify-full` is the only mode that verifies hostname — use this in production. "
        "Test: `openssl s_client -connect host:5432 -starttls postgres`.",
        f"{BASE}/runtime-config-connection.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: create + use a schema",
        "Create: `CREATE SCHEMA analytics;`. "
        "Set as default: `SET search_path TO analytics, public;` (session) or `ALTER DATABASE mydb SET search_path TO analytics, public;` (persistent). "
        "Create table in schema: `CREATE TABLE analytics.events (...);`. "
        "Cross-schema query: `SELECT * FROM analytics.events JOIN public.users USING (user_id);`. "
        "Drop with everything inside: `DROP SCHEMA analytics CASCADE;`.",
        f"{BASE}/app-psql.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: tune memory settings (shared_buffers, work_mem)",
        "postgresql.conf rules of thumb: "
        "`shared_buffers`: 25% of RAM (cap ~16GB on single instance). "
        "`work_mem`: 32MB-128MB per connection per sort/hash — too high causes OOM under load. "
        "`maintenance_work_mem`: 1GB for VACUUM/CREATE INDEX (one-off). "
        "`effective_cache_size`: 75% of RAM (planner hint, not allocation). "
        "After changing: restart for `shared_buffers`, reload for others. "
        "Tool: https://pgtune.leopard.in.ua for sensible defaults by workload.",
        f"{BASE}/runtime-config-resource.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: set up logging (log_statement, slow queries)",
        "postgresql.conf: ```\\nlog_destination = 'stderr'\\nlogging_collector = on\\nlog_directory = 'log'\\nlog_filename = 'postgresql-%Y-%m-%d.log'\\nlog_statement = 'mod'                # log all data-modifying statements\\nlog_min_duration_statement = 1000      # log queries > 1 sec\\nlog_lock_waits = on\\nlog_temp_files = 0                     # log all temp file creation\\n```. "
        "Reload: `pg_ctl reload`. "
        "For audit trail: `pgaudit` extension.",
        f"{BASE}/runtime-config-logging.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: create an index concurrently (no lock)",
        "Standard: `CREATE INDEX ON users (email);` — locks table briefly. "
        "Production-safe: `CREATE INDEX CONCURRENTLY ON users (email);` — no exclusive lock, takes longer. "
        "Caveats: CONCURRENTLY can't be inside a transaction; on failure leaves an INVALID index — drop + retry. "
        "Drop concurrently too: `DROP INDEX CONCURRENTLY users_email_idx;`. "
        "Always CONCURRENTLY on production tables.",
        f"{BASE}/app-psql.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: monitor with pg_stat views",
        "Active queries: `SELECT * FROM pg_stat_activity;`. "
        "Table I/O: `SELECT * FROM pg_stat_user_tables;`. "
        "Index usage: `SELECT * FROM pg_stat_user_indexes WHERE idx_scan = 0;` (unused indexes). "
        "Locks: `SELECT * FROM pg_locks JOIN pg_stat_activity USING (pid);`. "
        "Cache hit ratio: `SELECT sum(blks_hit)::float / sum(blks_hit + blks_read) FROM pg_stat_database;`. "
        "Replication lag: `SELECT * FROM pg_stat_replication;`.",
        f"{BASE}/app-psql.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: drop everything in a database (start fresh)",
        "Nuclear: `DROP DATABASE mydb; CREATE DATABASE mydb;` (need to disconnect first). "
        "Keep DB, drop content: `DROP SCHEMA public CASCADE; CREATE SCHEMA public;`. "
        "Then: `GRANT ALL ON SCHEMA public TO public;` (restore default ACL). "
        "From shell: `dropdb mydb && createdb mydb`. "
        "Caveat: extensions in public must be recreated.",
        f"{BASE}/app-dropdb.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: write upserts (INSERT ... ON CONFLICT)",
        "Skip duplicate: `INSERT INTO users (id, email) VALUES (1, 'a@x') ON CONFLICT (id) DO NOTHING;`. "
        "Update on conflict: `INSERT INTO users (id, email, updated_at) VALUES (1, 'a@x', NOW()) ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email, updated_at = EXCLUDED.updated_at;`. "
        "`EXCLUDED` references the row that would have been inserted. "
        "Multiple targets: `ON CONFLICT (col1, col2)`. "
        "Conditional update: `... DO UPDATE SET email = EXCLUDED.email WHERE users.updated_at < EXCLUDED.updated_at;`.",
        f"{BASE}/app-psql.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: install + use an extension",
        "List available: `SELECT * FROM pg_available_extensions;`. "
        "Install: `CREATE EXTENSION IF NOT EXISTS pg_trgm;`. "
        "Common: `uuid-ossp` (UUID generation), `pg_trgm` (fuzzy text search), `hstore` (key-value), `postgis` (geospatial), `pgvector` (vectors), `pgcrypto` (hashing). "
        "Update: `ALTER EXTENSION pg_trgm UPDATE;`. "
        "Drop: `DROP EXTENSION pg_trgm;` (refuses if other things depend on it).",
        f"{BASE}/app-psql.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: configure connection pooling with PgBouncer",
        "Not PostgreSQL itself — but the standard solution. Install pgbouncer + config /etc/pgbouncer/pgbouncer.ini: ```\\n[databases]\\nmydb = host=localhost port=5432 dbname=mydb\\n\\n[pgbouncer]\\nlisten_port = 6432\\npool_mode = transaction\\nmax_client_conn = 1000\\ndefault_pool_size = 25\\n```. "
        "Connect via pgbouncer: `psql -h pgbouncer-host -p 6432 -d mydb`. "
        "Modes: `session` (per-client), `transaction` (per-transaction, most common), `statement` (most aggressive).",
        f"{BASE}/runtime-config-connection.html",
    ),
    (
        "postgresql-recipes", "postgresql recipe: set up streaming replication (primary + replica)",
        "Primary postgresql.conf: `wal_level = replica`, `max_wal_senders = 10`, `archive_mode = on`. "
        "Primary pg_hba.conf: `host replication replicator <ip> scram-sha-256`. "
        "On replica: `pg_basebackup -h primary -U replicator -D /var/lib/postgresql/data -Fp -Xs -P -R` (the `-R` writes standby.signal). "
        "Start replica server. "
        "Verify: on primary `SELECT * FROM pg_stat_replication;`. "
        "Modern PG (16+): logical replication is often easier — see `CREATE PUBLICATION` / `CREATE SUBSCRIPTION`.",
        f"{BASE}/runtime-config-replication.html",
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
            evidence_id=f"ev-pg-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-pg-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="postgresql-catalog",
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
