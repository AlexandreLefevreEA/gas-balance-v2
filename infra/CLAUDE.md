# infra/ — local dev stack + DB migrations

Everything needed to run the system locally and to evolve the Postgres schema.

- `docker-compose.yml` — postgres + api + web for `make dev`.
- `Dockerfile.api`, `Dockerfile.etl` — images for the Python services.
- `db/` — schema migrations (Alembic) and/or SQL. The Postgres schema is the
  contract between etl/ml (writers) and api (reader); change it via a migration, never by hand.

## Rules

- Local-only secrets come from `.env` (git-ignored); compose reads them.
- Keep images minimal; install only what each service needs.
- A schema change ships with its migration in the same PR.

> Scaffold: compose/Dockerfiles are skeletons; flesh out as services are implemented.
