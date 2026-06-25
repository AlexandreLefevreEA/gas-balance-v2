# Architecture Decision Records

One decision per file, numbered `NNNN-kebab-title.md`. They capture **why**, so we
don't relitigate settled choices — and so each "we're not doing X yet" has a written
trigger for when to revisit. Add one with `/new-adr`.

## Template

```markdown
# NNNN. <Title>

- Status: Accepted | Superseded by NNNN | Proposed
- Date: YYYY-MM-DD

## Context
What forces are at play? What problem/constraint?

## Decision
What we chose, stated plainly.

## Consequences
What this makes easy, what it makes hard, what we give up.

## Trigger to revisit
The concrete condition under which we'd change this.
```

## Index

| # | Decision |
|---|---|
| [0001](0001-monorepo-uv-workspace.md) | Monorepo with a uv workspace; lightweight ETL CLI (no orchestrator) |
| [0002](0002-reuse-postgresql.md) | Reuse PostgreSQL as the trusted store |
| [0003](0003-source-agnostic-etl.md) | Source-agnostic ETL: connector contract + template, sources TBD |
| [0004](0004-react-vite-web.md) | React + Vite for the web app |
| [0005](0005-lean-ds-tooling.md) | Lean DS tooling; defer DVC/dbt/Dagster/Nx |
| [0006](0006-rotate-legacy-secrets.md) | Rotate leaked secrets; exclude legacy from VCS |
