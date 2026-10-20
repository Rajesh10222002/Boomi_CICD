# Environment extensions from a value store (POC)

After a package is deployed to **QA** or **PD**, `CD - Promote` can set that environment's
connection extensions (user, password, URL ...) from a store, so QA/PD don't run on the Dev
values. **Dev is never touched.** A connection with no stored values keeps what was deployed
(the Dev values) — except that a **missing secret for PD stops the run** (QA only warns).

Off by default. Nothing below changes `CD - Promote` until the repo variable `EXT_SYNC` is `true`.

## How it runs

```
prepare ──► extension-plan-qa ──► deploy (QA gate) ──► apply-extensions-qa
                                                              │ (success or off)
                                                              ▼
prepare-production ──► extension-plan-pd ──► deploy-production (PD gate) ──► apply-extensions-pd
```

* **plan** (before each approval): store *metadata only* (no values) + the field list from Dev.
  Shows which connections will take stored values and which secrets are missing. A missing PD
  secret fails the run **before** the PD approval.
* **apply** (after each deploy): reads the target environment's extensions and the stored values,
  sends a *partial* update with only the stored fields, re-reads Boomi and verifies.
  `extensions_mode` = `dry-run` (default: shows what it would set) or `apply`.

Values are never printed, logged or uploaded. Keys are Boomi **connection components** (not
processes), so a connection shared by many processes is stored once. The process and every process
it calls (any depth) are walked to find the connections.

## One-time setup

1. **Oracle store** — run `scripts/sql/oracle_extension_store.sql` (users, table, views, plan/readers).
2. **Seed values** — `python scripts/seed_extension_values.py --environment qa --connection-name "<name>" --field password --secret`
   (prompts hide the value; secrets are encrypted with `SECRETS_KEY`).
3. **Runner** — the jobs run on `vars.EXT_RUNNER_LABELS` (a self-hosted runner that can reach the store;
   make the repo **private** first). Python 3.11+ must be on that runner's PATH.
4. **GitHub repo variables:** `EXT_SYNC=true`, `EXT_RUNNER_LABELS=["self-hosted","ext-local"]`.
5. **GitHub repo secrets** (never in files): `EXT_DB_DSN` (e.g. `localhost:1521/XEPDB1`),
   `EXT_PLAN_DB_USER`/`EXT_PLAN_DB_PASSWORD` (metadata-only reader), `EXT_QA_DB_USER`/`EXT_QA_DB_PASSWORD`,
   `EXT_PD_DB_USER`/`EXT_PD_DB_PASSWORD`, `SECRETS_KEY`.

## Not yet confirmed against a real account

The Boomi `EnvironmentExtensions` shape (connections → connection → field[]) and the partial-update
call follow Boomi's documented object but are unverified here. The first runs are therefore
`dry-run`; `apply` re-reads Boomi and fails the job if a changed field isn't actually updated.
Use the **Extension Probe** workflow (read-only) to see the real field ids.
