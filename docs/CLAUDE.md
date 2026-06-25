# docs/

Human + Claude reference. The source of truth for **why**.

| File | Purpose |
|---|---|
| `architecture.md` | The big picture and data flow. Update when the structure changes. |
| `data-contracts.md` | What "trusted data" means: schemas, ranges, freshness, ownership. |
| `runbook.md` | How to run/schedule the pipeline and recover from failures. |
| `migration-from-legacy.md` | Legacy → v2 component map + the secret-rotation checklist. |
| `adr/` | Architecture Decision Records. One decision per file, numbered. Add with `/new-adr`. |

Rule: if a PR changes behaviour or structure, it updates the relevant doc in the
same PR. Stale docs are worse than no docs.
