# 0006. Rotate leaked secrets; exclude legacy from VCS

- Status: Accepted
- Date: 2026-06-25

## Context

`legacy/Code/raw.py` hardcodes Commodity Essentials credentials; the covariate
price/availability fetchers hardcode a Kpler API key; `legacy/Code/.env` exists.
These secrets are valuable to keep `legacy/` as a local reference during migration —
but committing it (now or via history) would leak them, especially on a public-ish
GitHub repo.

## Decision

1. **Exclude `legacy/` from version control** (`.gitignore` keeps only
   `legacy/CLAUDE.md`), so no secret ever enters git history.
2. **Rotate** the CE credentials and Kpler key — they are already compromised.
3. To later bring legacy under VCS: **scrub** literals to `os.environ[...]` reads
   first, then un-ignore.
4. CI/pre-commit run **gitleaks** + `detect-private-key` as defence-in-depth.

## Consequences

- Easy: zero risk of the known secrets entering history; legacy stays usable locally.
- Give up: legacy code isn't browsable in the remote until scrubbed.

## Trigger to revisit

When someone wants legacy in the remote for reference — do the scrub+rotate first,
then revisit this ADR.
