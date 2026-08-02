# Database Migrations (Alembic)

This project's schema was managed directly through
`Database.create_all_tables()` (`Base.metadata.create_all()`) through
v1.0.0's development. Alembic is now wired in (`alembic/env.py` reads
the same `DB_*` environment variables `config.py` does, so a migration
always targets whatever database the running application would connect
to) with a single **baseline** revision —
`versions/f945cdc05edd_baseline_schema.py` — capturing the complete
v1.0.0 schema as of this release.

## Applying migrations

```bash
alembic upgrade head
```

- **Fresh installation** (no existing database file/tables): just run
  the command above — it creates the full schema from scratch, exactly
  as `Database.initialize()` already does at app startup. You do not
  need to run this manually before first launch; the app still calls
  `create_all_tables()` itself. Alembic is for *future* schema changes,
  from here on.
- **Existing v1.0.0 (or earlier) deployment**: your database already
  has this exact schema (created via `create_all_tables()`, not
  Alembic), so running `alembic upgrade head` would try to re-create
  tables that already exist and fail. Stamp the database as already
  being at the baseline instead, without touching any table:

  ```bash
  alembic stamp f945cdc05edd
  ```

  From that point on, `alembic upgrade head` correctly applies only
  the migrations that come *after* the baseline.

## Creating a new migration

Once a model in `models/` changes:

```bash
alembic revision --autogenerate -m "short description"
```

Then **always read the generated file before committing it**:

- Autogenerate does not import custom `TypeDecorator` columns it
  detects (`models.base.UTCDateTime`,
  `models.encrypted_types.EncryptedString`/`EncryptedDecimal`) — add
  the missing `import models.base` / `import models.encrypted_types`
  by hand if the new migration touches a column using one of them (see
  the baseline migration for the pattern).
- Autogenerate cannot detect every change (e.g. a column rename shows
  up as a drop + add, silently discarding that column's data) — review
  the diff against your actual intent, not just whether it runs.
- Test both directions (`alembic upgrade head` then
  `alembic downgrade -1`) against a throwaway database before shipping
  a migration.
