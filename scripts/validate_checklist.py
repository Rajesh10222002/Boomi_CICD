"""
Pre-promotion checklist validation (see checklist.md in the repo root).

Pulls the package's component XML from Boomi (same versioned fetch
diff_component_versions.py uses), runs each checklist item against it, and
writes a PASS / FAIL / REVIEW report:

  PASS    checked automatically and satisfied
  FAIL    checked automatically and not satisfied
  REVIEW  can't be decided from the XML alone (needs a human eye) — the
          reviewer at the environment gate makes that call

This script never exits non-zero because of a FAIL: the point is to put the
results in front of the reviewer at the approval gate, who decides whether
the promotion goes ahead. It exits non-zero only if it can't run at all.

Most checks are heuristics over Boomi's process XML (shape types, attributes
such as catchAll/retryCount/allowSimultaneous, shape dragpoints), so each
result names the evidence it saw. Tune the thresholds/patterns in CONFIG.

Prints `checklist-passed=`, `checklist-failed=`, `checklist-review=` and
`checklist-report=<path>` for $GITHUB_OUTPUT. The report is a markdown file the
workflow appends to the job summary.

Sub-processes: every process-call shape in the process is followed (recursively,
cycle-guarded, CONFIG["max_subprocess_depth"] deep). Each sub-process is fetched at
the version deployed in the source environment, run through the structural checks in
SUBPROCESS_CHECKS (reported as "Sub-process: <name>" rows), and verified to be
deployed in the target environment — a parent promoted without its sub-process fails
at runtime, so a missing/older sub-process there is a FAIL.

Usage:
    python scripts/validate_checklist.py --process-name "PR_LOAD_..." --component-id X --package-id P
        [--source-environment dev --target-environment qa]
    python scripts/validate_checklist.py --process-name "..." --xml-file process.xml   # offline
"""

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import deque

CONFIG = {
    # checklist.md: PR_LOAD_[Domain]_[Object]_[Direction]_[Target]
    "name_pattern": r"^PR_LOAD_[A-Za-z0-9]+_[A-Za-z0-9]+_[A-Za-z0-9]+_[A-Za-z0-9]+$",
    # Shapes that don't need a label of their own.
    "unlabelled_ok": {"start", "stop", "returndocuments"},
    # A process bigger than this with no sub-process call should probably be split.
    "max_shapes_without_subprocess": 25,
    # Words that suggest test-only shapes were left in.
    "test_markers": re.compile(r"\b(test|debug|temp|tmp|todo|fixme|dummy)\b", re.I),
    # Attribute/element names that should never carry a literal credential.
    "secret_names": re.compile(r"(password|passwd|secret|token|api[_-]?key|apikey|authorization)", re.I),
    "max_script_lines": 30,
    "max_dynamic_properties": 20,
    # Hosts' connector types that count as "send a notification".
    "notify_connectors": {"mail", "slack", "teams", "http", "smtp"},
    "execution_sample": 50,
    "max_subprocess_depth": 5,
}

PASS, FAIL, REVIEW = "PASS", "FAIL", "REVIEW"
ICON = {PASS: "✅", FAIL: "❌", REVIEW: "🔎"}


def _tag(el):
    return el.tag.split("}", 1)[-1].lower()


def _all(root, tag):
    return [e for e in root.iter() if _tag(e) == tag]


def _shapes(root):
    return _all(root, "shape")


def _shape_type(shape):
    return (shape.attrib.get("shapetype") or "").lower()


def _label(shape):
    return (shape.attrib.get("userlabel") or "").strip()


def _shape_desc(shape):
    return f"{_shape_type(shape) or 'shape'} '{shape.attrib.get('name', '?')}'"


def _process_el(root):
    procs = _all(root, "process")
    return procs[0] if procs else None


def _reachable_shapes(shapes_by_name, start_shape, identifier=None):
    """Shape names reachable by following dragpoints from start_shape.

    If `identifier` is set, only the start shape's dragpoints with that
    identifier are used for the first hop (e.g. the 'error' branch of a
    try/catch); later hops follow every dragpoint.
    """
    seen, queue = set(), deque()
    for dp in _all(start_shape, "dragpoint"):
        if identifier is None or (dp.attrib.get("identifier") or "").lower() == identifier:
            if dp.attrib.get("toShape"):
                queue.append(dp.attrib["toShape"])
    while queue:
        name = queue.popleft()
        if name in seen or name not in shapes_by_name:
            continue
        seen.add(name)
        for dp in _all(shapes_by_name[name], "dragpoint"):
            if dp.attrib.get("toShape"):
                queue.append(dp.attrib["toShape"])
    return seen


# --- individual checks: each returns (status, evidence) ---------------------

def check_naming(ctx):
    name = ctx["process_name"]
    ok = re.match(CONFIG["name_pattern"], name) is not None
    return (PASS if ok else FAIL), f"`{name}` " + ("matches" if ok else "does not match") + " the convention `PR_LOAD_<Domain>_<Object>_<Direction>_<Target>`"


def check_labels(ctx):
    missing = [
        _shape_desc(s)
        for s in _shapes(ctx["root"])
        if _shape_type(s) not in CONFIG["unlabelled_ok"] and not _label(s)
    ]
    if missing:
        return FAIL, f"{len(missing)} unlabelled shape(s): " + ", ".join(missing[:8]) + (" …" if len(missing) > 8 else "")
    return PASS, "every shape has a label"


def check_subprocess(ctx):
    shapes = _shapes(ctx["root"])
    calls = [s for s in shapes if _shape_type(s) in ("processcall", "subprocess", "process")]
    if calls:
        return PASS, f"{len(calls)} sub-process call(s) in {len(shapes)} shapes"
    if len(shapes) > CONFIG["max_shapes_without_subprocess"]:
        return FAIL, f"{len(shapes)} shapes and no sub-process call — consider extracting reusable logic"
    return PASS, f"{len(shapes)} shapes — small enough not to need sub-processes"


def _subprocess_calls(root):
    """componentIds of every sub-process called from this process's process-call shapes."""
    ids = []
    for el in root.iter():
        if _tag(el) == "processcall" and el.attrib.get("processId"):
            ids.append(el.attrib["processId"])
    return list(dict.fromkeys(ids))


def check_subprocess_dependencies(ctx):
    subs = ctx["subprocesses"]
    if not subs:
        if ctx.get("subprocess_note"):
            return REVIEW, ctx["subprocess_note"]
        return PASS, "no sub-process calls"
    target, source = ctx["target_env"], ctx["source_env"]
    bad, notes = [], []
    for cid, sub in subs.items():
        label = f"'{sub['name']}'"
        if sub.get("error"):
            bad.append(f"{label} ({cid}) could not be fetched: {sub['error']}")
            continue
        deps = ctx["sub_deployments"].get(cid)
        if deps is None:
            notes.append(f"{label}: deployment state not checked")
            continue
        src, tgt = deps.get(source), deps.get(target)
        if tgt is None:
            where = f"deployed in {source}" if src else f"not deployed in {source} either"
            bad.append(f"{label} is NOT deployed in {target} ({where})")
        elif src and src.get("packageVersion") != tgt.get("packageVersion"):
            bad.append(
                f"{label} version differs: {source}={src.get('packageVersion')} vs {target}={tgt.get('packageVersion')}"
            )
    for env in ctx.get("extra_envs", ()):
        missing = [f"'{s['name']}'" for cid, s in subs.items()
                   if cid in ctx["sub_deployments"] and ctx["sub_deployments"][cid].get(env) is None]
        if missing:
            notes.append(f"not yet deployed in {env} (needed before promotion there): " + ", ".join(missing))
    if bad:
        return FAIL, "; ".join(bad)
    if notes:
        return REVIEW, "; ".join(notes)
    return PASS, f"{len(subs)} sub-process(es), all deployed in {target} at the {source} version: " + ", ".join(
        f"'{s['name']}'" for s in subs.values()
    )


def check_description(ctx):
    for el in _all(ctx["root"], "description"):
        if (el.text or "").strip():
            return PASS, "description is populated"
    desc = ctx["root"].attrib.get("description", "").strip()
    if desc:
        return PASS, "description is populated"
    return FAIL, "component description is empty"


def check_no_hardcoded_credentials(ctx):
    hits = []
    for el in ctx["root"].iter():
        for k, v in el.attrib.items():
            if CONFIG["secret_names"].search(k) and v.strip() and not v.strip().startswith(("{", "$")):
                hits.append(f"<{_tag(el)} {k}=…>")
        if CONFIG["secret_names"].search(_tag(el)) and (el.text or "").strip():
            hits.append(f"<{_tag(el)}> has a literal value")
    for el in ctx["root"].iter():
        text = (el.text or "")
        if re.search(r"(bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}", text, re.I):
            hits.append(f"literal auth header in <{_tag(el)}>")
    if hits:
        return FAIL, "possible hardcoded credential: " + "; ".join(sorted(set(hits))[:6])
    return PASS, "no credential-looking literals in the process definition"


def _scripts(root):
    out = []
    for el in root.iter():
        t = _tag(el)
        if (t == "script" or t.endswith("script") or t == "scripttoexecute") and len((el.text or "").strip()) > 5:
            out.append(el.text.strip())
    return out


def check_scripting(ctx):
    scripts = _scripts(ctx["root"])
    for map_root in ctx["map_roots"].values():
        scripts.extend(_scripts(map_root))
    if not ctx["map_roots"] and ctx["map_ids"]:
        return REVIEW, f"{len(ctx['map_ids'])} map(s) referenced but their definitions could not be fetched"
    if not scripts:
        return PASS, "no scripting found in the process or its maps"
    problems = []
    for i, s in enumerate(scripts, 1):
        lines = [l for l in s.splitlines() if l.strip()]
        if len(lines) > CONFIG["max_script_lines"]:
            problems.append(f"script #{i} is {len(lines)} lines (> {CONFIG['max_script_lines']})")
        if not re.search(r"(//|/\*|^\s*#)", s, re.M):
            problems.append(f"script #{i} has no comments")
    if problems:
        return FAIL, "; ".join(problems)
    return PASS, f"{len(scripts)} script(s), all short and commented"


def check_dynamic_properties(ctx):
    n = len(_all(ctx["root"], "documentproperty")) + len(_all(ctx["root"], "dynamicprocessproperty"))
    if n > CONFIG["max_dynamic_properties"]:
        return FAIL, f"{n} document/dynamic property settings (> {CONFIG['max_dynamic_properties']}) — likely overloaded"
    return PASS, f"{n} document/dynamic property setting(s)"


def _catch_shapes(root):
    return [s for s in _shapes(root) if _shape_type(s) in ("catcherrors", "trycatch")]


def _catch_cfg(shape):
    for el in shape.iter():
        if _tag(el) in ("catcherrors", "trycatch"):
            return el.attrib
    return {}


def check_try_catch(ctx):
    catches = _catch_shapes(ctx["root"])
    if not catches:
        return FAIL, "no Try/Catch shape"
    cfgs = [_catch_cfg(c) for c in catches]
    if any((c.get("catchAll") or "").lower() == "true" for c in cfgs):
        return PASS, "Try/Catch present with 'catch all errors' enabled"
    return FAIL, "Try/Catch present but 'catch all errors' is not enabled (document-level errors only)"


def check_notifications(ctx):
    notifies = [s for s in _shapes(ctx["root"]) if _shape_type(s) == "notify"]
    conns = [
        s for s in _shapes(ctx["root"])
        if _shape_type(s) == "connectoraction"
        and any((e.attrib.get("connectorType") or "").lower() in CONFIG["notify_connectors"] for e in s.iter())
    ]
    if notifies:
        return PASS, f"{len(notifies)} Notify shape(s)"
    if conns:
        return REVIEW, "no Notify shape; email/HTTP connector call(s) present — confirm one is the error notification"
    return FAIL, "no Notify shape or email/webhook connector call"


def check_dead_letter(ctx):
    shapes_by_name = {s.attrib.get("name"): s for s in _shapes(ctx["root"])}
    catches = _catch_shapes(ctx["root"])
    if not catches:
        return FAIL, "no Try/Catch shape, so no error path to write failed documents from"
    for c in catches:
        reach = _reachable_shapes(shapes_by_name, c, identifier="error")
        if any(_shape_type(shapes_by_name[n]) == "connectoraction" for n in reach):
            return REVIEW, "error path reaches a connector call — confirm it persists failed documents for reprocessing"
    return FAIL, "the Catch (error) path does not reach any connector, so failed documents aren't stored anywhere"


def check_retry(ctx):
    counts = []
    for c in _catch_shapes(ctx["root"]):
        try:
            counts.append(int(_catch_cfg(c).get("retryCount", "0") or 0))
        except ValueError:
            pass
    if any(n > 0 for n in counts):
        return PASS, f"Try/Catch retryCount = {max(counts)}"
    return FAIL, "no Try/Catch with retryCount > 0"


def check_overlap(ctx):
    proc = _process_el(ctx["root"])
    if proc is None:
        return REVIEW, "process element not found"
    if (proc.attrib.get("allowSimultaneous") or "").lower() == "true":
        return FAIL, "allowSimultaneous=true — runs may overlap (also confirm the schedule window)"
    return REVIEW, "allowSimultaneous=false; schedule window itself is set on the atom — confirm it's realistic"


def check_test_artifacts(ctx):
    hits = [
        _shape_desc(s) + f" ('{_label(s)}')"
        for s in _shapes(ctx["root"])
        if CONFIG["test_markers"].search(_label(s))
    ]
    if hits:
        return FAIL, "test-looking shape(s): " + ", ".join(hits[:6])
    return PASS, "no test/debug/temp-labelled shapes"


def _exec_statuses(ctx):
    return [(r.get("status") or "").upper() for r in ctx["executions"]]


def check_dev_tested(ctx):
    if ctx["executions"] is None:
        return REVIEW, ctx["executions_note"]
    n = sum(1 for s in _exec_statuses(ctx) if s == "COMPLETE")
    if n:
        return PASS, f"{n} COMPLETE execution(s) in Dev"
    return FAIL, "no COMPLETE execution of this process found in Dev"


def check_paths_tested(ctx):
    if ctx["executions"] is None:
        return REVIEW, ctx["executions_note"]
    statuses = _exec_statuses(ctx)
    ok, err = "COMPLETE" in statuses, any(s in ("ERROR", "ABORTED") for s in statuses)
    if ok and err:
        return PASS, "both a successful and a failed execution exist in Dev"
    return FAIL, "Dev executions: " + ("happy path only" if ok else "error path only" if err else "none")


# Items that can't be judged from the XML. (Section, item) -> why.
MANUAL_ITEMS = {
    "Shared / reused connections — no duplicates": "connection duplication is across components, not visible in this process's XML",
    "Connector operations not reused across unrelated contexts": "needs a cross-process usage search",
    "Maps are clean — no orphaned fields": "needs profile-vs-map field analysis",
    "No sensitive data in process logs or document cache": "depends on data content, not structure",
    "Error messages include sufficient context": "message wording is free text",
    "Batch processing — no unnecessary single-document loops": "depends on data volumes/intent",
    "Document caching used appropriately": "depends on runtime behaviour",
    "Test logs / screenshots attached or referenced": "evidence lives outside Boomi",
}

# (section, item text from checklist.md, check fn or None for manual)
CHECKLIST = [
    ("Structure & Naming", "Process name follows naming convention", check_naming),
    ("Structure & Naming", "All shapes are labelled — no unnamed connectors or maps", check_labels),
    ("Structure & Naming", "Sub-processes used appropriately for reusable logic", check_subprocess),
    ("Structure & Naming", "Sub-processes deployed in the target environment (and themselves checked)", check_subprocess_dependencies),
    ("Structure & Naming", "Process notes / description populated", check_description),
    ("Connectors & Connections", "Shared / reused connections — no duplicates", None),
    ("Connectors & Connections", "Credentials in connection extensions, not hardcoded", check_no_hardcoded_credentials),
    ("Connectors & Connections", "Connector operations not reused across unrelated contexts", None),
    ("Data Handling & Mapping", "Maps are clean — no orphaned fields", None),
    ("Data Handling & Mapping", "Scripting in maps minimal and commented", check_scripting),
    ("Data Handling & Mapping", "Document / dynamic properties not overloaded", check_dynamic_properties),
    ("Data Handling & Mapping", "No sensitive data in process logs or document cache", None),
    ("Error Handling", "Try/Catch captures all error types, not just document-level", check_try_catch),
    ("Error Handling", "Error notifications configured (email / Slack webhook)", check_notifications),
    ("Error Handling", "Failed documents go to a dead-letter path / DB", check_dead_letter),
    ("Error Handling", "Retry logic for transient / timeout failures", check_retry),
    ("Error Handling", "Error messages include sufficient context", None),
    ("Performance & Scalability", "Batch processing — no unnecessary single-document loops", None),
    ("Performance & Scalability", "Document caching used appropriately", None),
    ("Performance & Scalability", "Scheduled processes do not overlap", check_overlap),
    ("Testing Evidence", "Process tested in Dev with representative data", check_dev_tested),
    ("Testing Evidence", "Happy path and error path both tested", check_paths_tested),
    ("Testing Evidence", "Test logs / screenshots attached or referenced", None),
    ("Testing Evidence", "No test data or test-only shapes left in the process", check_test_artifacts),
]


def run_subprocess_checks(ctx):
    """The full checklist, run again on every sub-process (same rules as the parent)."""
    results = []
    for sub in ctx["subprocesses"].values():
        if sub.get("error"):
            results.append({"section": f"Sub-process: {sub['name']}", "item": "Definition fetched",
                            "status": FAIL, "evidence": sub["error"]})
            continue
        sub_ctx = dict(ctx, root=sub["root"], map_roots=sub["map_roots"], map_ids=sub["map_ids"],
                       process_name=sub["name"], executions=sub["executions"],
                       executions_note=sub["executions_note"])
        for r in _run_items(sub_ctx):
            results.append(dict(r, section=f"Sub-process {sub['name']} / {r['section']}"))
    return results


def _run_items(ctx):
    results = []
    for section, item, fn in CHECKLIST:
        if fn is check_subprocess_dependencies and ctx.get("is_subprocess"):
            continue  # dependency state is evaluated once, on the parent
        if fn is None:
            status, evidence = REVIEW, MANUAL_ITEMS.get(item, "needs human review")
        else:
            try:
                status, evidence = fn(ctx)
            except Exception as exc:  # a broken heuristic must not hide the other results
                status, evidence = REVIEW, f"check errored ({type(exc).__name__}: {exc})"
        results.append({"section": section, "item": item, "status": status, "evidence": evidence})
    return results


def run_checklist(ctx):
    return _run_items(ctx) + run_subprocess_checks(dict(ctx, is_subprocess=True))


def _table(rows, head=("Section", "Checklist item", "Finding")):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows:
        ev = r["evidence"].replace("|", "\|")
        out.append(f"| {r['section']} | {r['item']} | {ev} |")
    return out


def render_markdown(results, title):
    by = {s: [r for r in results if r["status"] == s] for s in (PASS, FAIL, REVIEW)}
    total = len(results)
    if by[FAIL]:
        verdict = f"> ## ❌ {len(by[FAIL])} checklist item(s) failed — reviewer decision required"
    elif by[REVIEW]:
        verdict = "> ## 🔎 No automated failures — manual review items remain"
    else:
        verdict = "> ## ✅ All checklist items passed"
    filled = round(10 * len(by[PASS]) / total) if total else 0
    bar = "🟩" * filled + "⬜" * (10 - filled)

    lines = [
        "## 🧾 Pre-QA Checklist Validation",
        "",
        f"**Subject:** {title}",
        "",
        verdict,
        "",
        f"{bar} **{len(by[PASS])} of {total}** items passed",
        "",
        "| ✅ Passed | ❌ Failed | 🔎 Needs review | Total |",
        "|:-:|:-:|:-:|:-:|",
        f"| {len(by[PASS])} | {len(by[FAIL])} | {len(by[REVIEW])} | {total} |",
        "",
        "### Results by section",
        "",
        "| Section | ✅ | ❌ | 🔎 | Status |",
        "|---|:-:|:-:|:-:|:-:|",
    ]
    for sec in dict.fromkeys(r["section"] for r in results):
        rs = [r for r in results if r["section"] == sec]
        p_ = sum(r["status"] == PASS for r in rs)
        f_ = sum(r["status"] == FAIL for r in rs)
        v_ = sum(r["status"] == REVIEW for r in rs)
        icon = ICON[FAIL] if f_ else ICON[REVIEW] if v_ else ICON[PASS]
        lines.append(f"| {sec} | {p_} | {f_} | {v_} | {icon} |")

    if by[FAIL]:
        lines += ["", "### ❌ Failed — needs attention", ""] + _table(by[FAIL])
    if by[REVIEW]:
        lines += ["", "### 🔎 Needs reviewer judgement", ""] + _table(by[REVIEW])
    if by[PASS]:
        lines += ["", f"<details><summary><b>✅ Passed ({len(by[PASS])})</b></summary>", ""]
        lines += _table(by[PASS]) + ["", "</details>"]
    lines += [
        "",
        "---",
        "_Failed and review items do not block the run. The reviewer decides at the "
        "**Review pending deployments** prompt whether to approve the QA deployment._",
    ]
    return "\n".join(lines) + "\n"


def _parse(xml_text):
    try:
        return ET.fromstring(xml_text)
    except ET.ParseError as exc:
        print(f"Could not parse component XML: {exc}", file=sys.stderr)
        return None


def _load_dev_env_id():
    path = os.path.join(os.path.dirname(__file__), "..", "environments", "dev.json")
    with open(path) as f:
        return json.load(f)["environment_id"]


def _load_env_id(environment):
    path = os.path.join(os.path.dirname(__file__), "..", "environments", f"{environment}.json")
    with open(path) as f:
        return json.load(f)["environment_id"]


def _collect_maps(root, client, sink_roots, sink_ids):
    for el in root.iter():
        map_id = el.attrib.get("mapId") if _tag(el) == "map" else None
        if map_id:
            sink_ids.add(map_id)
    if client is None:
        return
    for map_id in sorted(sink_ids):
        if map_id in sink_roots:
            continue
        try:
            parsed = _parse(client.get_component_xml(map_id))
            if parsed is not None:
                sink_roots[map_id] = parsed
        except Exception as exc:
            print(f"Could not fetch map {map_id}: {exc}", file=sys.stderr)


def _fetch_subprocess_xml(client, cid, source_dep):
    """Component XML at the version deployed in the source env, else the latest."""
    if source_dep:
        version = next(
            (p.get("componentVersion") for p in client.list_packages(cid, limit=200)
             if p.get("packageId") == source_dep.get("packageId")),
            None,
        )
        if version is not None:
            return client.get_component_xml(cid, version=version)
    return client.get_component_xml(cid)


def _query_dev_executions(client, component_id):
    """(records|None, note) — Dev execution history for one component."""
    try:
        return client.query_execution_records(
            component_id, _load_dev_env_id(), limit=CONFIG["execution_sample"]
        ), ""
    except Exception as exc:
        if "do not have access to any containers" in str(exc):
            return None, "no runtime (atom) is attached to the Dev environment, so there is no execution history to check"
        return None, f"could not query Dev execution history ({type(exc).__name__}: {exc})"


def load_subprocesses(root, client, source_env, target_env, extra_envs=()):
    """Follow process calls recursively. Returns ({cid: info}, {cid: {env: deployment|None}})."""
    subs, deployments, env_cache = {}, {}, {}

    def find(env, cid):
        if env not in env_cache:
            env_cache[env] = client.list_deployed_packages(_load_env_id(env))
        recs = [r for r in env_cache[env]
                if r.get("componentId") == cid and str(r.get("active", True)).lower() != "false"]
        recs.sort(key=lambda r: r.get("deployedDate", ""), reverse=True)
        return recs[0] if recs else None

    queue = deque((cid, 1) for cid in _subprocess_calls(root))
    while queue:
        cid, depth = queue.popleft()
        if cid in subs:  # also guards against call cycles
            continue
        info = {"name": cid, "depth": depth, "map_roots": {}, "map_ids": set()}
        info["executions"], info["executions_note"] = _query_dev_executions(client, cid)
        subs[cid] = info
        try:
            deployments[cid] = {env: find(env, cid) for env in (source_env, target_env, *extra_envs)}
        except Exception as exc:
            print(f"Could not read deployments for sub-process {cid}: {exc}", file=sys.stderr)
        try:
            sub_root = _parse(_fetch_subprocess_xml(client, cid, (deployments.get(cid) or {}).get(source_env)))
            if sub_root is None:
                raise ValueError("component XML did not parse")
        except Exception as exc:
            info["error"] = f"{type(exc).__name__}: {exc}"
            continue
        info["root"] = sub_root
        info["name"] = sub_root.attrib.get("name") or cid
        _collect_maps(sub_root, client, info["map_roots"], info["map_ids"])
        nested = _subprocess_calls(sub_root)
        if depth < CONFIG["max_subprocess_depth"]:
            queue.extend((c, depth + 1) for c in nested)
        elif nested:
            print(f"Sub-process '{info['name']}' calls further sub-processes beyond depth "
                  f"{CONFIG['max_subprocess_depth']}; not followed.", file=sys.stderr)
    return subs, deployments


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--process-name", required=True)
    parser.add_argument("--component-id", default="")
    parser.add_argument("--package-id", default="")
    parser.add_argument("--xml-file", default="", help="offline: validate this XML file instead of calling Boomi")
    parser.add_argument("--source-environment", default="dev", help="environment the package is promoted from")
    parser.add_argument("--target-environment", default="qa", help="environment the package is promoted to")
    parser.add_argument("--also-check-environment", action="append", default=[],
                        help="further environment(s) the package will reach next (e.g. prod); sub-processes missing there are flagged for review")
    parser.add_argument("--out", default="checklist_report.md")
    args = parser.parse_args()

    ctx = {"process_name": args.process_name, "map_roots": {}, "map_ids": set(),
           "executions": None, "executions_note": "execution history not queried (offline run)",
           "subprocesses": {}, "sub_deployments": {}, "source_env": args.source_environment,
           "target_env": args.target_environment, "extra_envs": args.also_check_environment}
    client = None

    if args.xml_file:
        with open(args.xml_file, encoding="utf-8") as f:
            xml_text = f.read()
        title = os.path.basename(args.xml_file)
    else:
        if not args.component_id or not args.package_id:
            parser.error("--component-id and --package-id are required unless --xml-file is given")
        from boomi_client import BoomiClient
        client = BoomiClient()
        version = next(
            (p.get("componentVersion") for p in client.list_packages(args.component_id, limit=200)
             if p.get("packageId") == args.package_id),
            None,
        )
        if version is None:
            print(f"Could not resolve a componentVersion for package {args.package_id}", file=sys.stderr)
            sys.exit(1)
        xml_text = client.get_component_xml(args.component_id, version=version)
        title = f"{args.process_name} (package {args.package_id}, component v{version})"

        ctx["executions"], note = _query_dev_executions(client, args.component_id)
        if note:
            ctx["executions_note"] = note

    root = _parse(xml_text)
    if root is None:
        sys.exit(1)
    ctx["root"] = root

    _collect_maps(root, client, ctx["map_roots"], ctx["map_ids"])
    if client is not None:
        ctx["subprocesses"], ctx["sub_deployments"] = load_subprocesses(
            root, client, args.source_environment, args.target_environment, args.also_check_environment
        )
    elif _subprocess_calls(root):
        ctx["subprocess_note"] = f"{len(_subprocess_calls(root))} sub-process call(s) found but not fetched (offline run)"

    results = run_checklist(ctx)
    report = render_markdown(results, title)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)

    for status, key in ((PASS, "passed"), (FAIL, "failed"), (REVIEW, "review")):
        print(f"checklist-{key}={sum(1 for r in results if r['status'] == status)}")
    print(f"checklist-report={args.out}")
    sys.stderr.reconfigure(encoding="utf-8")
    print(report, file=sys.stderr)


if __name__ == "__main__":
    main()
