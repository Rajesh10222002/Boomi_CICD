# Boomi CI/CD Pilot

Automates packaging, testing, and promoting Boomi integration processes through
**Dev → QA → PD**, driven by GitHub Actions and a Streamlit admin console.

Full design doc (architecture, decisions, open questions): see the linked plan
shared with this repo, or `docs/PLAN.md` once exported here.

## Layout

```
components/    Boomi process/component references to package (components.json)
environments/  Per-environment config: environment IDs, atom IDs, extension values
tests/         Test-case definitions (input + expected outcome) per process
scripts/       Python helpers that call the Boomi AtomSphere Platform API
.github/workflows/
  ci.yml       Package + deploy-to-Dev + run tests, on every push to main
  cd.yml       Promote a tested package to QA or PD, manually or via the app
app/           Streamlit admin console (process list, single-click promote)
```

## One-time setup (you do this, not Claude Code)

1. **Boomi API token** — Boomi trial account → Settings → My User Settings →
   Platform API Tokens → Add New Token. Copy the value immediately.
2. **GitHub repo secrets** — Settings → Secrets and variables → Actions → New
   repository secret. Add:
   - `BOOMI_ACCOUNT_ID`
   - `BOOMI_USERNAME` — format `BOOMI_TOKEN.<your-boomi-login-email>`
   - `BOOMI_API_TOKEN` — the token value from step 1
   - `BOOMI_BASE_URL` — only if the trial account is NOT on the US platform
     (defaults to `https://api.boomi.com`; use `https://api.platform.gb.boomi.com`
     for EU/GB accounts)

   Once these are set, the Streamlit app lists live processes and looks up
   packages by calling the Boomi API directly — nobody has to type a
   componentId or packageId anywhere in the UI. If you just want to look
   without opening the app, run the **"List Boomi Processes"** workflow from
   the Actions tab (workflow_dispatch) to see the same data in a job summary.
3. **GitHub Environments** — Settings → Environments → create `qa` and
   `production`. On `production`, add required reviewers if you want a human
   approval gate on top of the app's single click (see the plan doc).
4. **GitHub OAuth App** (for the Streamlit login) — GitHub → Settings →
   Developer settings → OAuth Apps → New OAuth App. Callback URL is whatever
   Streamlit Community Cloud gives your deployed app. Put all of the keys
   listed in `app/.streamlit/secrets.toml.example` into Streamlit's app
   secrets (`.streamlit/secrets.toml` locally, or the Community Cloud
   "Secrets" panel in production) — **never commit them**.

## What's scaffolded vs. what's still TODO

The Streamlit app and the `component_id`/`package_id` it passes through are
live — no placeholder IDs there. Still to do:
- Fill in the real atom IDs in `environments/*.json` once an atom/runtime is
  attached to each Boomi environment (environment IDs are already filled in).
- `components/components.json` still has a placeholder `component_id` — it
  only matters for the push-triggered `ci.yml` pipeline (packages/tests
  whatever's listed there on every push to `main`); the app-driven flow
  doesn't touch this file at all.
- Write the real expected-output assertions in `tests/*.json` once the pilot
  process is chosen.

See `CLAUDE.md` for what to hand to Claude Code next.
