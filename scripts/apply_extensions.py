"""
Set a process's environment extensions in a Boomi environment from the extension value
store (see extension_store.py) — or just plan / dry-run that. Handles every extension type
in extension_types.py (connections, dynamic process properties, process-property
components, cross-reference tables).

For the process and every process it calls (any depth) the connections used are found; for
the other types the store rows apply when they belong to this process (or to no particular
process) AND the item is defined in Boomi's extensions for the environment. Only items with
a stored value are updated; everything else keeps what was deployed (the Dev values). Dev
is never touched.

Modes (--mode / EXT_MODE):
    plan      Before approval. Reads the store METADATA only (no values) and the field list
              from the Dev environment (where the process already lives). Changes nothing.
    dry-run   After deploy. Reads the target environment's extensions and the stored values
              (decrypting them, to prove they're usable), builds the update, sends nothing.
    apply     After deploy. Sends ONE partial update covering every type, re-reads Boomi and
              verifies each type.

Missing-secret policy (--missing-policy / EXT_MISSING_POLICY): a connection with an encrypted
(password-type) field that has no stored value is a "missing secret". 'stop' fails the run
(exit 3) — the default for prod/PD; 'warn' reports it and carries on — the default for QA.

Never prints a value. Output goes to stdout as key=value lines for $GITHUB_OUTPUT (also written
there directly when that env var is set), a markdown report to $GITHUB_STEP_SUMMARY / --out,
and optionally a comment on a GitHub issue.

Usage:
    python scripts/apply_extensions.py --process-name "My Process" --environment qa --mode plan
"""

import argparse
import os
import sys

from boomi_client import BoomiApiError, BoomiClient
from extension_probe import collect_dependencies, environment_id
from extension_store import StoreError, open_store
from extension_types import HANDLERS, base_payload, rows_for


def analyse_all(field_source, rows, process_name, connections):
    ctx = {"process_name": process_name, "connections": connections}
    return {h.type: h.analyse(field_source, rows_for(h, rows, process_name), ctx) for h in HANDLERS}


def build_update(ext, results_by_type):
    payload = base_payload(ext)
    for h in HANDLERS:
        h.build(ext, results_by_type[h.type], payload)
    return payload


def verify_all(client, env_id, results_by_type):
    after = client.get_environment_extensions(env_id)
    problems = []
    for h in HANDLERS:
        problems += h.verify(after, results_by_type[h.type])
    return problems


def _scrub(text, results_by_type):
    for results in results_by_type.values():
        for r in results:
            for row in (r["set"].values() if isinstance(r["set"], dict) else []):
                v = (row or {}).get("value") if isinstance(row, dict) else None
                if v:
                    text = text.replace(str(v), "***")
    return text[:400]


def _names(items):
    return ", ".join(f"`{i}`" for i in items) or "—"


def store_line(rows, process_name):
    """One line saying what the store returned, so an empty section can be explained."""
    by_type = {}
    for r in rows:
        by_type[r["extension_type"]] = by_type.get(r["extension_type"], 0) + 1
    mine = sum(1 for h in HANDLERS for r in rows_for(h, rows, process_name))
    kinds = ", ".join(f"{t}: {n}" for t, n in sorted(by_type.items())) or "none"
    return f"Store rows read: **{len(rows)}** ({kinds}); applying to this process: **{mine}**"


def render(process_name, environment, mode, results_by_type, policy, stopped, store_info=""):
    icon = {"plan": "🗺️", "dry-run": "🧪", "apply": "🔐"}[mode]
    lines = [f"## {icon} Extensions — `{process_name}` → **{environment}** ({mode})", ""]
    if mode == "plan":
        lines += ["_Plan only: field list read from Dev, store read as metadata. Nothing is changed and no value is read._", ""]
    lines += [f"Missing-secret policy for {environment}: **{policy}**", ""]
    if store_info:
        lines += [store_info, ""]

    conns = results_by_type["CONNECTION"]
    lines += ["### Connections", "", "| Connection | Matched by | Will set from store | Missing secrets | Store fields not in Boomi | Note |",
              "|---|---|---|---|---|---|"]
    for r in conns:
        lines.append(f"| {r['name']} | {r['match'] or '—'} | {_names(r['set'])} | {_names(r['missing_secret'])} | "
                     f"{_names(r['unknown'])} | {r['note']} |")
    if not conns:
        lines.append("| _(no connections found in the process tree)_ | | | | | |")
    lines.append("")

    for h in HANDLERS[1:]:
        res = results_by_type[h.type]
        if not res:
            continue
        lines += [f"### {h.title}", "", "| Item | Will set from store | Not found / unknown | Note |", "|---|---|---|---|"]
        for r in res:
            lines.append(f"| {r['name']} | {_names(r['set'])} | {_names(r['unknown'])} | {r['note']} |")
        lines.append("")

    if stopped:
        lines.append("> ❌ **Stopping:** missing secrets for this environment — add them to the store before promoting.")
    elif any(r["missing_secret"] for r in conns):
        lines.append("> ⚠️ Missing secrets are left on their deployed (Dev) values.")
    return "\n".join(lines) + "\n"


def _write_env_file(var, text):
    path = os.environ.get(var)
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)


def _post_issue_comment(body):
    import requests

    number, repo, token = os.environ.get("ISSUE_NUMBER"), os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not (number and repo and token):
        return
    requests.post(
        f"https://api.github.com/repos/{repo}/issues/{number}/comments",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json={"body": body}, timeout=30,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--process-name", default=os.environ.get("PROCESS_NAME", ""))
    parser.add_argument("--component-id", default="")
    parser.add_argument("--environment", default=os.environ.get("ENVIRONMENT", ""), choices=["qa", "prod", ""])
    parser.add_argument("--mode", default=os.environ.get("EXT_MODE", "plan"), choices=["plan", "dry-run", "apply"])
    parser.add_argument("--missing-policy", default=os.environ.get("EXT_MISSING_POLICY", ""), choices=["warn", "stop", ""])
    parser.add_argument("--out", default="extensions_report.md")
    args = parser.parse_args()
    if not args.process_name or not args.environment:
        parser.error("--process-name and --environment are required (or PROCESS_NAME / ENVIRONMENT)")
    policy = args.missing_policy or ("stop" if args.environment == "prod" else "warn")

    try:
        client = BoomiClient()
        component_id = args.component_id or client.find_component_by_name(args.process_name)
        connections, _processes = collect_dependencies(client, component_id)
        if args.mode == "plan":
            field_source = client.get_environment_extensions(environment_id("dev"))
            rows = open_store("plan").rows(args.environment, with_values=False)
        else:
            field_source = client.get_environment_extensions(environment_id(args.environment))
            rows = open_store("apply").rows(args.environment, with_values=True)
    except (StoreError, BoomiApiError, LookupError) as exc:
        print(f"::error::Extension step failed before any change: {exc}", file=sys.stderr)
        sys.exit(2)

    results = analyse_all(field_source, rows, args.process_name, connections)
    missing = sum(len(r["missing_secret"]) for r in results["CONNECTION"])
    to_set = sum(len(r["set"]) for rs in results.values() for r in rs)
    stopped = bool(missing) and policy == "stop"

    problems = []
    if args.mode == "apply" and to_set and not stopped:
        payload = build_update(field_source, results)
        try:
            client.update_environment_extensions(environment_id(args.environment), payload)
        except BoomiApiError as exc:
            print(f"::error::Boomi rejected the extension update: {_scrub(str(exc), results)}", file=sys.stderr)
            sys.exit(2)
        problems = verify_all(client, environment_id(args.environment), results)

    report = render(args.process_name, args.environment, args.mode, results, policy, stopped,
                    store_line(rows, args.process_name))
    if args.mode == "apply" and not stopped:
        report += (f"\n✅ Applied {to_set} item(s) and verified in Boomi.\n" if not problems and to_set
                   else (f"\n❌ Verification problems: {'; '.join(problems)}\n" if problems else "\nNothing to apply.\n"))
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    _write_env_file("GITHUB_STEP_SUMMARY", report + "\n")
    _post_issue_comment(report)
    _write_env_file("GITHUB_OUTPUT", f"extensions-to-set={to_set}\nextensions-missing-secrets={missing}\n")
    print(f"extensions-to-set={to_set}")
    print(f"extensions-missing-secrets={missing}")
    print(report, file=sys.stderr)

    if stopped:
        sys.exit(3)
    if problems:
        sys.exit(4)


if __name__ == "__main__":
    main()
