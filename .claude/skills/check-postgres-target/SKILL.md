---
name: check-postgres-target
description: Inspect a PostgreSQL instance (default a CERN DBOD one) for version, pgvector and pg_textsearch availability, text-search configs, and memory and connection limits, then state which lexical backend pg-retrieve should use. Use before writing migrations, when deploying to a new database, or when updating docs/cern-infrastructure.md.
---

# Check a PostgreSQL target

Turns "what does this database support" into a checklist for the config and the
migrations. Read-only: it only queries catalog views and settings.

## Inputs

- A connection string in `PG_DSN`, or the usual `PGHOST`, `PGPORT`, `PGUSER`,
  `PGDATABASE`, `PGPASSWORD` variables. Never echo the password.
- `psql` on the path.

## Steps

1. Run the query file:

   ```
   psql "$PG_DSN" -v ON_ERROR_STOP=1 -f .claude/skills/check-postgres-target/check.sql
   ```

2. Interpret the sections:
   - **server**: must be PostgreSQL 17 or newer.
   - **extensions of interest**: `vector` must be available. Note its version: below
     0.8.0 there is no iterative HNSW scan, so filtered vector queries need over-fetch.
     `pg_textsearch` available means the BM25 backend can be enabled; absent means the
     tsvector backend stays the default. `unaccent` and `btree_gin` are nice to have.
   - **installed in this database**: what `CREATE EXTENSION` has already been run.
     Migrations must create `vector` if missing and the role must be allowed to.
   - **text search configs**: confirm `english` (or the config the corpus needs) exists.
   - **settings**: `shared_preload_libraries` shows whether pg_textsearch is preloaded.
     `maintenance_work_mem` bounds HNSW build speed. `max_connections` bounds the
     async pool size. `shared_buffers` is the memory ceiling for indexes.
   - **privileges**: whether the role can create extensions and schemas.

3. Report the outcome in this form:
   - lexical backend: `pg_textsearch` or `tsvector`, and why
   - pgvector version and whether over-fetch is needed for filtered queries
   - pool size recommendation (at most a third of `max_connections` for one service)
   - anything that needs a DBOD ticket (missing extension, memory limits)

4. Record the findings in `docs/cern-infrastructure.md` with today's date when the
   target is the CERN instance.

## Notes

- A managed service usually lets the owner edit `postgresql.conf` only within limits.
  On DBOD `shared_buffers` is capped at 2 GB and `max_connections` at 150.
- Do not run `CREATE EXTENSION` from this skill. That belongs in a migration.
- On DBOD the `admin` role cannot read `shared_preload_libraries`, so that row is
  missing from **settings**. Treat it as "not preloaded" unless the DBOD team says
  otherwise. If psql reports a GSSAPI failure before the password step, add
  `gssencmode=disable` to the connection string.
