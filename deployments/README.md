# Deployment ledger

Metadata this repo maintains about itself — not Boomi API state, and not a
replacement for the GitHub Issues/Deployments audit trail, which is still
the record of *individual runs*. These two CSVs are the record of *what's
actually deployed where*, kept in git so it's diffable and reviewable like
any other change.

- **`current.csv`** — one row per (process, environment): whichever
  package is live there right now. This is what `cd.yml` reads to resolve
  "promote whatever's currently in Dev" (for a `qa` promotion) or
  "whatever's currently in QA" (for a `prod` promotion) when `package_id`
  is left blank — not "the most recently built package overall", which
  could be a version that was built but never actually promoted.
- **`ledger.csv`** — append-only full history of every successful deploy
  (one row per `ci.yml` Dev deploy or `cd.yml` promotion/rollback). This is
  what `rollback.yml` reads to find "the package that was in this
  environment before the current one", with real dates, instead of
  guessing from global package-creation order.

Both are written by `scripts/update_deployment_ledger.py`, called by
`ci.yml`/`cd.yml` right after a successful deploy. The workflow step then
commits and pushes the changed files using the run's own `GITHUB_TOKEN` —
GitHub does not re-trigger `push`-triggered workflows for commits made
with that token, so this can't create an infinite loop on `ci.yml`.

Don't hand-edit these — a wrong row here doesn't just look wrong, it changes what the *next* promotion or rollback picks.
