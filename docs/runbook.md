# Runbook

> Scaffold stage: commands below describe the intended operations; they go live as
> subsystems are implemented.

## Local development

```bash
cp .env.example .env          # fill in DB + source credentials
make setup                    # uv workspace + web deps
make dev                      # docker-compose: postgres + api + web
```

API at `http://localhost:8000`, web at the Vite dev URL. Stop with Ctrl-C.

## Parallel work (multiple agents)

A git **branch isolates commits, not files** — every branch shares the single working
tree, index, and `HEAD` of one checkout. So two Claude agents (or two terminals) editing
the same folder at once **collide**: edits clobber each other, `git add -A` cross-stages,
and a commit lands on whatever branch is checked out. Branching alone does **not** isolate
them.

**Rule: one checkout = one agent.** Each concurrent agent works in its own **git
worktree** — a separate working directory linked to the same repo, with its own files and
index:

```bash
git worktree add ../gas-balance-v2-<task> -b <branch>   # new dir + new branch
cd ../gas-balance-v2-<task>                             # edit + commit here
git worktree list                                       # see all worktrees
git worktree remove ../gas-balance-v2-<task>            # clean up when done (from main checkout)
```

For subagents spawned inside a session, pass `isolation: "worktree"` (the Agent tool /
`EnterWorktree`) so each gets its own checkout.

**Enforcement.** A `PreToolUse` hook (`.claude/hooks/worktree-guard.ps1`, wired in
`.claude/settings.json`) blocks `Edit`/`Write`/`NotebookEdit` when a **second live
session** is detected in the same checkout, printing the `git worktree add` command to
run. The earliest session "owns" a checkout via a short heartbeat lock under
`.claude/locks/` (~10 min freshness, released on clean exit); later concurrent sessions
are blocked until they move to their own worktree. Solo work and separate worktrees are
never blocked, and the guard fails **open** — a hook error never blocks an edit.

## Running the pipeline

```bash
make run-etl                  # all connectors → validate → load to Postgres
uv run etl run <source>       # a single source in isolation
# then forecasting:
uv run ml forecast            # fit/backtest/forecast → write forecasts to Postgres
```

ETL and forecasting are independent — run, retry, or backfill either alone.

## Scheduling (production)

Cron/CI triggers `etl run all` on the source's natural cadence, then `ml forecast`.
No orchestrator yet (ADR 0001); revisit if backfills or cross-job lineage get painful.

**Commodity Essentials** runs hourly, re-fetching full history (since 2014) and
upserting — re-runs are idempotent. Schedule with a plain cron line on the host:

```cron
0 * * * * cd /path/to/gas-balance-v2 && uv run etl run ce >> /var/log/etl-ce.log 2>&1
```

(`.env` must hold `CE_USERNAME` / `CE_PASSWORD` / `CE_BASE_URL` and `DATABASE_URL`.)

## Failure handling

- **A connector fails validation** → that source's load is blocked; others proceed.
  Inspect the logged offending rows, fix the mapping or schema, re-run that source.
- **A connector fails to fetch** → it's isolated; re-run just that source once the
  upstream recovers. Incremental fetch means a re-run only pulls the missing delta.
- **A model errors** → forecasting falls back per `ml/` policy; the registry's last
  good forecast remains served by the API.

## Tests & checks

```bash
make test                     # pytest + vitest
make lint                     # ruff + mypy + eslint + tsc
uv run pre-commit run -a      # format, secret scan, yaml checks
```
