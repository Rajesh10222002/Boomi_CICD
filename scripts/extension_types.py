"""
One handler per Boomi environment-extension type. Each knows how to
  analyse(ext, rows, ctx) -> what would be set / what is missing,
  build(ext, results, payload) -> add the changed items to a PARTIAL update payload,
  verify(ext_after, results) -> problems found when Boomi is read back.

Handlers read Boomi's GET response and reuse its own key names (e.g. whether an item is
identified by "id" or "name"), so they follow the real response shape rather than a guess.
Nothing here prints a value.

Types (Boomi EnvironmentExtensions sections):
    CONNECTION         connections > connection > field[]           partial per field
    DYNAMIC_PROPERTY   properties > property (name, value)          partial per property
    PROCESS_PROPERTY   processProperties > ProcessProperty >
                       ProcessPropertyValue[key, value]             whole component resent
    CROSS_REFERENCE    crossReferences > crossReference >
                       CrossReferenceRows > row[ref1..refN]         whole table resent
Not handled yet: operations, shared communication, trading partners, PGP certificates, map
extensions (see EXTENSIONS.md).
"""

import copy

from extension_store import applies_to_process, is_real_id, match_connection


# ----------------------------------------------------------------------------- helpers
def as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def ci_key(d, *names):
    """The actual key of dict d that matches any of `names`, ignoring case."""
    wanted = {n.lower() for n in names}
    for k in d:
        if k.lower() in wanted:
            return k
    return None


def find_section(ext, section_names, item_names):
    """(section_key, wrapper, item_key, items) for one extension section, or (None, {}, None, [])."""
    sk = ci_key(ext, *section_names)
    if sk is None or not isinstance(ext.get(sk), dict):
        return None, {}, None, []
    wrapper = ext[sk]
    ik = ci_key(wrapper, *item_names)
    if ik is None:  # fall back to the first container-looking key
        ik = next((k for k, v in wrapper.items() if not k.startswith("@") and isinstance(v, (list, dict))), None)
    items = [x for x in as_list(wrapper.get(ik)) if isinstance(x, dict)] if ik else []
    return sk, wrapper, ik, items


def base_payload(ext):
    payload = {k: v for k, v in ext.items() if k.startswith("@") or k in ("environmentId", "extensionGroupId")}
    payload["partial"] = True
    return payload


def put_items(payload, section_key, wrapper, item_key, items):
    new = {k: v for k, v in wrapper.items() if k != item_key}
    new[item_key] = items
    payload[section_key] = new


def item_id(entry):
    return entry.get("id") or entry.get("name") or ""


def item_name(entry):
    return entry.get("name") or entry.get("id") or ""


def truthy(value):
    return value is True or str(value).lower() == "true"


def is_encrypted(field):
    """Only fields Boomi itself marks as encrypted (password-type)."""
    return truthy(field.get("usesEncryption")) or field.get("type") == "password"


def field_id(field):
    return field.get("id") or field.get("label") or ""


def result(kind, key, name, **extra):
    r = {"type": kind, "key": key, "name": name, "listed": True, "set": {}, "unknown": [],
         "missing_secret": [], "note": "", "match": ""}
    r.update(extra)
    return r


def _find_entry(items, row_keys):
    """First Boomi item whose id or name equals one of the store's keys."""
    wanted = {k.lower() for k in row_keys if k}
    for e in items:
        if item_id(e).lower() in wanted or item_name(e).lower() in wanted:
            return e
    return None


def _group(rows, key_fn):
    out = {}
    for r in rows:
        out.setdefault(key_fn(r), []).append(r)
    return out


# ----------------------------------------------------------------------------- connections
class ConnectionHandler:
    type = "CONNECTION"
    title = "Connections"

    def analyse(self, ext, rows, ctx):
        _, _, _, items = find_section(ext, ("connections",), ("connection",))
        by_id = {item_id(e): e for e in items}
        out = []
        for conn_id, users in ctx["connections"].items():
            entry = by_id.get(conn_id)
            name = item_name(entry) if entry else conn_id
            mine, how = match_connection(rows, conn_id, name, (entry or {}).get("type", ""))
            res = result(self.type, conn_id, name, match=how, used_by=users, listed=entry is not None)
            if how == "ambiguous":
                res["note"] = "several store connections share this name — pin the connection id in the store"
            elif entry is None:
                res["note"] = ("stored values exist but the connection isn't listed in the extensions yet" if mine
                               else "not listed as extensible")
                res["unknown"] = [r["field_name"] for r in mine]
            else:
                fields = {field_id(f).lower(): f for f in as_list(entry.get("field")) if isinstance(f, dict) and field_id(f)}
                for r in mine:
                    f = fields.get(r["field_name"].lower())
                    if f is None:
                        res["unknown"].append(r["field_name"])
                    else:
                        res["set"][field_id(f)] = r
                res["missing_secret"] = [field_id(f) for f in fields.values()
                                         if is_encrypted(f) and field_id(f) not in res["set"]]
                if how == "id" and mine and mine[0]["component_name"] and mine[0]["component_name"] != name:
                    res["note"] = f"renamed in Boomi (store says '{mine[0]['component_name']}')"
                elif how == "name":
                    res["note"] = "matched by name — pin the connection id in the store"
                elif not mine:
                    res["note"] = "no stored values — keeps the deployed (Dev) values"
            out.append(res)
        return out

    def build(self, ext, results, payload):
        changes = {r["key"]: r["set"] for r in results if r["set"]}
        if not changes:
            return
        sk, wrapper, ik, items = find_section(ext, ("connections",), ("connection",))
        out = []
        for entry in items:
            if item_id(entry) not in changes:
                continue
            new_entry = {k: v for k, v in entry.items() if k != "field"}
            new_entry["field"] = []
            for f in as_list(entry.get("field")):
                fid = field_id(f)
                if fid in changes[item_id(entry)]:
                    row = changes[item_id(entry)][fid]
                    nf = copy.deepcopy(f)
                    if row["use_default"]:
                        nf["useDefault"] = True
                        nf.pop("value", None)
                    else:
                        nf["value"] = row["value"]
                        nf["useDefault"] = False
                        if is_encrypted(nf):
                            nf["encryptedValueSet"] = True
                        elif "encryptedValueSet" in nf:
                            nf["encryptedValueSet"] = False  # plain fields must stay plain
                    new_entry["field"].append(nf)
            out.append(new_entry)
        put_items(payload, sk, wrapper, ik, out)

    def verify(self, ext_after, results):
        _, _, _, items = find_section(ext_after, ("connections",), ("connection",))
        by_id = {item_id(e): e for e in items}
        problems = []
        for r in results:
            if not r["set"]:
                continue
            entry = by_id.get(r["key"])
            if entry is None:
                problems.append(f"connection {r['name']}: missing after update")
                continue
            fields = {field_id(f): f for f in as_list(entry.get("field")) if isinstance(f, dict)}
            for fid, row in r["set"].items():
                f = fields.get(fid)
                if f is None:
                    problems.append(f"{r['name']}.{fid}: not found after update")
                elif row["use_default"]:
                    if f.get("useDefault") is not True:
                        problems.append(f"{r['name']}.{fid}: still overridden")
                elif f.get("useDefault") is not False:
                    problems.append(f"{r['name']}.{fid}: still using the default")
                elif not is_encrypted(f) and f.get("value") != row["value"]:
                    problems.append(f"{r['name']}.{fid}: value differs after update")
        return problems


# ----------------------------------------------------------------------------- dynamic process properties
class DynamicPropertyHandler:
    type = "DYNAMIC_PROPERTY"
    title = "Dynamic process properties"
    SECTION, ITEM = ("properties",), ("property",)

    def analyse(self, ext, rows, ctx):
        _, _, _, items = find_section(ext, self.SECTION, self.ITEM)
        out = []
        for key, mine in _group(rows, lambda r: r["component_name"] or r["component_id"]).items():
            row = mine[-1]
            entry = _find_entry(items, [key, row["component_id"]])
            res = result(self.type, key, key, match="name")
            if entry is None:
                res.update(listed=False, note="not defined in this environment's extensions (deploy the process first)")
            else:
                res["set"][key] = row
                res["entry_key"] = item_id(entry)
                if row["use_default"]:
                    res["note"] = "dynamic properties have no default switch; the stored value is applied"
            out.append(res)
        return out

    def build(self, ext, results, payload):
        todo = {r["entry_key"]: r["set"][r["key"]] for r in results if r["set"]}
        if not todo:
            return
        sk, wrapper, ik, items = find_section(ext, self.SECTION, self.ITEM)
        out = []
        for entry in items:
            if item_id(entry) in todo:
                e = copy.deepcopy(entry)
                e["value"] = todo[item_id(entry)]["value"]
                out.append(e)
        put_items(payload, sk, wrapper, ik, out)

    def verify(self, ext_after, results):
        _, _, _, items = find_section(ext_after, self.SECTION, self.ITEM)
        by_id = {item_id(e): e for e in items}
        problems = []
        for r in results:
            if r["set"]:
                e = by_id.get(r["entry_key"])
                if e is None or e.get("value") != r["set"][r["key"]]["value"]:
                    problems.append(f"dynamic property {r['name']}: value differs after update")
        return problems


# ----------------------------------------------------------------------------- defined process properties
class ProcessPropertyHandler:
    type = "PROCESS_PROPERTY"
    title = "Process properties (components)"
    SECTION, ITEM, VALUES = ("processProperties",), ("ProcessProperty",), "ProcessPropertyValue"

    def _values(self, entry):
        k = ci_key(entry, self.VALUES) or self.VALUES
        return k, [v for v in as_list(entry.get(k)) if isinstance(v, dict)]

    def analyse(self, ext, rows, ctx):
        _, _, _, items = find_section(ext, self.SECTION, self.ITEM)
        out = []
        for key, mine in _group(rows, lambda r: r["component_id"] if is_real_id(r["component_id"]) else r["component_name"]).items():
            names = {r["component_name"] for r in mine}
            entry = _find_entry(items, [key] + list(names))
            res = result(self.type, key, next(iter(names)) or key)
            if entry is None:
                res.update(listed=False, note="not defined in this environment's extensions (deploy the process first)")
            else:
                res["entry_key"] = item_id(entry)
                _, vals = self._values(entry)
                have = {str(v.get("key") or v.get("id") or "").lower(): v for v in vals}
                for r in mine:
                    if r["field_name"].lower() in have:
                        res["set"][have[r["field_name"].lower()].get("key") or have[r["field_name"].lower()].get("id")] = r
                    else:
                        res["unknown"].append(r["field_name"])
            out.append(res)
        return out

    def build(self, ext, results, payload):
        todo = {r["entry_key"]: r["set"] for r in results if r["set"]}
        if not todo:
            return
        sk, wrapper, ik, items = find_section(ext, self.SECTION, self.ITEM)
        out = []
        for entry in items:
            if item_id(entry) not in todo:
                continue
            e = copy.deepcopy(entry)  # Boomi needs EVERY value of the component, not just the changed ones
            vk, vals = self._values(e)
            for v in vals:
                k = v.get("key") or v.get("id")
                if k in todo[item_id(entry)]:
                    row = todo[item_id(entry)][k]
                    if row["use_default"]:
                        v["useDefault"] = True
                        v.pop("value", None)
                    else:
                        v["value"] = row["value"]
                        v["useDefault"] = False
            e[vk] = vals
            out.append(e)
        put_items(payload, sk, wrapper, ik, out)

    def verify(self, ext_after, results):
        _, _, _, items = find_section(ext_after, self.SECTION, self.ITEM)
        by_id = {item_id(e): e for e in items}
        problems = []
        for r in results:
            if not r["set"]:
                continue
            e = by_id.get(r["entry_key"])
            if e is None:
                problems.append(f"process property {r['name']}: missing after update")
                continue
            have = {str(v.get("key") or v.get("id")): v for v in self._values(e)[1]}
            for k, row in r["set"].items():
                v = have.get(str(k))
                if v is None:
                    problems.append(f"{r['name']}.{k}: not found after update")
                elif row["use_default"]:
                    if v.get("useDefault") is not True:
                        problems.append(f"{r['name']}.{k}: still overridden")
                elif v.get("useDefault") is not False or (not truthy(v.get("encryptedValueSet")) and v.get("value") != row["value"]):
                    problems.append(f"{r['name']}.{k}: not applied")
        return problems


# ----------------------------------------------------------------------------- cross-reference tables
class CrossReferenceHandler:
    type = "CROSS_REFERENCE"
    title = "Cross-reference tables"
    SECTION, ITEM, ROWS, ROW = ("crossReferences",), ("crossReference",), "CrossReferenceRows", "row"

    def _rows_of(self, entry):
        rk = ci_key(entry, self.ROWS)
        wrapper = entry.get(rk) if rk else None
        if isinstance(wrapper, dict):
            ik = ci_key(wrapper, self.ROW) or self.ROW
            return rk, wrapper, ik, [r for r in as_list(wrapper.get(ik)) if isinstance(r, dict)]
        return rk or self.ROWS, {}, self.ROW, []

    @staticmethod
    def _table(rows):
        """store rows -> [{ref1:.., ref2:..}, ...] ordered by row_no."""
        by_row = {}
        for r in rows:
            by_row.setdefault(r["row_no"] or 1, {})[r["field_name"]] = r["value"] if "value" in r else ""
        return [by_row[k] for k in sorted(by_row)]

    def analyse(self, ext, rows, ctx):
        _, _, _, items = find_section(ext, self.SECTION, self.ITEM)
        out = []
        for key, mine in _group(rows, lambda r: r["component_id"] if is_real_id(r["component_id"]) else r["component_name"]).items():
            names = {r["component_name"] for r in mine}
            entry = _find_entry(items, [key] + list(names))
            res = result(self.type, key, next(iter(names)) or key)
            if entry is None:
                res.update(listed=False, note="not defined in this environment's extensions (deploy the process first)")
            else:
                res["entry_key"] = item_id(entry)
                res["set"] = {f"row {n}": None for n in sorted({r["row_no"] or 1 for r in mine})}
                res["_rows"] = mine
            out.append(res)
        return out

    def build(self, ext, results, payload):
        todo = {r["entry_key"]: r["_rows"] for r in results if r["set"]}
        if not todo:
            return
        sk, wrapper, ik, items = find_section(ext, self.SECTION, self.ITEM)
        out = []
        for entry in items:
            if item_id(entry) not in todo:
                continue
            e = copy.deepcopy(entry)
            rk, rwrap, rik, _ = self._rows_of(e)
            new_wrap = dict(rwrap)
            new_wrap[rik] = self._table(todo[item_id(entry)])  # the whole table, every row
            e[rk] = new_wrap
            e["overrideValues"] = True
            out.append(e)
        put_items(payload, sk, wrapper, ik, out)

    def verify(self, ext_after, results):
        _, _, _, items = find_section(ext_after, self.SECTION, self.ITEM)
        by_id = {item_id(e): e for e in items}
        problems = []
        for r in results:
            if not r["set"]:
                continue
            e = by_id.get(r["entry_key"])
            if e is None:
                problems.append(f"cross-reference {r['name']}: missing after update")
                continue
            got = [{k: str(v) for k, v in row.items() if not k.startswith("@")} for row in self._rows_of(e)[3]]
            want = [{k: str(v) for k, v in row.items()} for row in self._table(r["_rows"])]
            if got != want or not truthy(e.get("overrideValues")):
                problems.append(f"cross-reference {r['name']}: rows differ after update")
        return problems


HANDLERS = [ConnectionHandler(), DynamicPropertyHandler(), ProcessPropertyHandler(), CrossReferenceHandler()]


def rows_for(handler, rows, process_name):
    return [r for r in rows if r["extension_type"] == handler.type and applies_to_process(r, process_name)]
