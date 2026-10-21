"""
Read-only probe: what environment extensions does Boomi expose for a process
in an environment, and which of the connections the process (and every process
it calls, to any depth) uses are extensible there?

Never prints or stores a field VALUE — only structure: connection name/id, field
ids, whether the field still uses the default, whether it is an encrypted
(password-type) field, and whether a value is set. --raw additionally writes the
full extensions JSON with every "value" redacted, for discovering the API shape.

Nothing is changed in Boomi.

Usage:
    python scripts/extension_probe.py --process-name "My Process" --environment qa
    python scripts/extension_probe.py --process-name "My Process" --environment qa --raw raw.json --out probe.md
"""

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import deque

from boomi_client import BoomiClient

_ENV_DIR = os.path.join(os.path.dirname(__file__), "..", "environments")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def environment_id(environment):
    with open(os.path.join(_ENV_DIR, f"{environment}.json"), encoding="utf-8") as f:
        return json.load(f)["environment_id"]


def _tag(el):
    return el.tag.split("}", 1)[-1].lower()


def collect_dependencies(client, root_component_id):
    """Walk a process and its sub-processes (any depth, cycle-safe).

    Returns (connections, processes): connections maps connectionId -> sorted list of process
    names that use it; processes maps componentId -> process name.
    """
    connections, processes = {}, {}
    queue, seen = deque([root_component_id]), set()
    while queue:
        cid = queue.popleft()
        if cid in seen:
            continue
        seen.add(cid)
        try:
            root = ET.fromstring(client.get_component_xml(cid))
        except Exception as exc:
            print(f"Could not read process {cid}: {exc}", file=sys.stderr)
            processes[cid] = cid
            continue
        name = root.attrib.get("name") or cid
        processes[cid] = name
        for el in root.iter():
            for key, value in el.attrib.items():
                k = key.lower()
                if not _UUID.match(value or ""):
                    continue
                if k == "connectionid":
                    connections.setdefault(value, set()).add(name)
                elif _tag(el) == "processcall" and k == "processid":
                    queue.append(value)
    return {c: sorted(users) for c, users in connections.items()}, processes


def _redact(node):
    if isinstance(node, dict):
        return {k: ("<redacted>" if k == "value" else _redact(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [_redact(v) for v in node]
    return node


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _sections(extensions):
    """Yield (section_name, item) for every extensible item in the extensions JSON."""
    for section, body in extensions.items():
        if not isinstance(body, dict):
            continue
        for key, items in body.items():
            if key.startswith("@"):
                continue
            for item in _as_list(items):
                if isinstance(item, dict):
                    yield section, item


def field_summary(field):
    return {
        "id": field.get("id") or field.get("label") or "?",
        "use_default": field.get("useDefault"),
        "encrypted": str(field.get("usesEncryption")).lower() == "true" or field.get("type") == "password",
        "value_set": bool(field.get("value")) or bool(field.get("encryptedValueSet")),
    }


def render(process_name, environment, connections, processes, extensions):
    ext_by_id = {}
    others = []
    for section, item in _sections(extensions):
        if section == "connections":
            ext_by_id[item.get("id")] = item
        else:
            others.append((section, item))

    lines = [
        f"## 🔌 Extension probe — `{process_name}` in **{environment}**",
        "",
        f"Process tree: {len(processes)} process(es) — " + ", ".join(f"`{n}`" for n in processes.values()),
        "",
        f"Connections used by the process tree: **{len(connections)}**. Values are never shown.",
        "",
    ]
    if not connections:
        lines.append("> ⚠️ No connection IDs found in the process XML.")
    for cid, users in connections.items():
        ext = ext_by_id.get(cid)
        title = (ext or {}).get("name") or cid
        lines += [f"### Connection: {title}", "", f"- ID: `{cid}`", f"- Used by: {', '.join(users)}"]
        if ext is None:
            lines += ["- ❌ **Not listed in this environment's extensions** (process not deployed here yet, or the connection has no extensible fields).", ""]
            continue
        fields = [field_summary(f) for f in _as_list(ext.get("field")) if isinstance(f, dict)]
        lines += ["- ✅ Listed in the environment's extensions.", "",
                  "| Field | Uses default | Secret (encrypted) | Value set |", "|---|:-:|:-:|:-:|"]
        for f in fields:
            lines.append(f"| `{f['id']}` | {f['use_default']} | {'🔒' if f['encrypted'] else ''} | {'yes' if f['value_set'] else 'no'} |")
        if not fields:
            lines.append("| _(no fields listed)_ | | | |")
        lines.append("")
    if others:
        lines += ["### Other extension sections present", ""]
        counts = {}
        for section, _ in others:
            counts[section] = counts.get(section, 0) + 1
        lines += [f"- `{s}`: {n} item(s)" for s, n in sorted(counts.items())]
        lines.append("")
    lines.append(f"Top-level keys returned by Boomi: {', '.join(f'`{k}`' for k in extensions.keys())}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--process-name", required=True)
    parser.add_argument("--component-id", default="")
    parser.add_argument("--environment", required=True, choices=["dev", "qa", "prod"])
    parser.add_argument("--out", default="extension_probe.md")
    parser.add_argument("--raw", default="", help="also write the extensions JSON with all values redacted")
    args = parser.parse_args()

    client = BoomiClient()
    component_id = args.component_id or client.find_component_by_name(args.process_name)
    connections, processes = collect_dependencies(client, component_id)
    extensions = client.get_environment_extensions(environment_id(args.environment))

    if args.raw:
        with open(args.raw, "w", encoding="utf-8") as f:
            json.dump(_redact(extensions), f, indent=2)
    report = render(args.process_name, args.environment, connections, processes, extensions)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"component-id={component_id}")
    print(f"connections-used={len(connections)}")
    print(f"connections-extensible={sum(1 for c in connections if any(i.get('id') == c for s, i in _sections(extensions) if s == 'connections'))}")


if __name__ == "__main__":
    main()
