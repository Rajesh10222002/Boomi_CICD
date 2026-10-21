"""
Offline tests for the extension handlers and apply_extensions.py, against a simulated Boomi
that applies PARTIAL updates the way the real API documents them. No network, no secrets.

Run:  python tests/test_extension_types.py
"""

import contextlib
import copy
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ["SECRETS_KEY"] = "test-key-for-offline-tests"
os.environ["EXT_STORE"] = "file"

import apply_extensions as a  # noqa: E402
import extension_store as st  # noqa: E402

C1 = "11111111-2222-3333-4444-555555555555"
C2 = "aaaaaaaa-2222-3333-4444-555555555555"
PROC = "PR_LOAD_CICD_Test_Inbound_Demo"


def fresh_ext():
    return {
        "@type": "EnvironmentExtensions", "environmentId": "E", "extensionGroupId": "",
        "connections": {"@type": "C", "connection": [
            {"id": C1, "name": "Demo Conn", "type": "http", "field": [
                {"id": "url", "useDefault": True}, {"id": "user", "useDefault": True},
                {"id": "password", "usesEncryption": True, "useDefault": True}]},
            {"id": C2, "name": "Second Conn", "type": "http", "field": [
                {"id": "url", "useDefault": True},
                {"id": "password", "usesEncryption": True, "useDefault": True}]}]},
        "properties": {"property": [{"name": "DPP_GREETING", "value": "hello-dev"}, {"name": "DPP_HI", "value": "Hi Dev"},
                                    {"name": "OTHER_PROCESS_PROP", "value": "x"}]},
        "processProperties": {"ProcessProperty": [{"id": "PP1", "name": "Settings", "ProcessPropertyValue": [
            {"key": "region", "label": "Region", "value": "dev", "useDefault": True},
            {"key": "mode", "label": "Mode", "value": "d", "useDefault": True}]}]},
        "crossReferences": {"crossReference": [{"id": "XR1", "name": "Codes", "overrideValues": False,
                                                 "CrossReferenceRows": {"row": [{"ref1": "a", "ref2": "1"}]}}]},
    }


class FakeBoomi:
    """Applies partial updates like Boomi documents: only what is sent changes."""
    state = None
    last_payload = None

    def find_component_by_name(self, n):
        return "PROC"

    def get_environment_extensions(self, env_id):
        out = copy.deepcopy(FakeBoomi.state)
        for c in out["connections"]["connection"]:  # Boomi never returns secret values
            for f in c["field"]:
                if f.get("usesEncryption"):
                    f.pop("value", None)
        return out

    def update_environment_extensions(self, env_id, payload):
        FakeBoomi.last_payload = copy.deepcopy(payload)
        assert payload.get("partial") is True
        s = FakeBoomi.state
        for c in payload.get("connections", {}).get("connection", []):
            tgt = next(x for x in s["connections"]["connection"] if x["id"] == c["id"])
            for f in c["field"]:
                tf = next(x for x in tgt["field"] if x["id"] == f["id"])
                tf.update({k: v for k, v in f.items() if k not in ("encryptedValueSet",)})
                if f.get("usesEncryption") and "value" in f:
                    tf["encryptedValueSet"] = True
                    tf.pop("value", None)
                if f.get("useDefault") is True:
                    tf.pop("value", None)
        for p in payload.get("properties", {}).get("property", []):
            next(x for x in s["properties"]["property"] if x["name"] == p["name"]).update(p)
        for p in payload.get("processProperties", {}).get("ProcessProperty", []):
            tgt = next(x for x in s["processProperties"]["ProcessProperty"] if x["id"] == p["id"])
            tgt["ProcessPropertyValue"] = copy.deepcopy(p["ProcessPropertyValue"])
        for x in payload.get("crossReferences", {}).get("crossReference", []):
            tgt = next(t for t in s["crossReferences"]["crossReference"] if t["id"] == x["id"])
            tgt["overrideValues"] = x["overrideValues"]
            tgt["CrossReferenceRows"] = copy.deepcopy(x["CrossReferenceRows"])
        return {}


def row(env, typ, cid, cname, field, value, secret=0, **kw):
    r = {"environment": env, "extension_type": typ, "component_id": cid, "component_name": cname, "field_name": field,
         "value_text": st.encrypt(value) if secret else value, "is_secret": secret}
    r.update(kw)
    return r


def qa_rows():
    return [
        row("qa", "CONNECTION", C1, "Demo Conn", "url", "https://qa.example"),
        row("qa", "CONNECTION", "REPLACE-WITH-CONNECTION-ID", "Demo Conn", "user", "qauser"),  # by name: id not pinned
        row("qa", "CONNECTION", C1, "Demo Conn", "password", "qapass-secret", 1),
        row("qa", "CONNECTION", C2, "Second Conn", "url", "https://qa2.example"),  # no password -> missing secret
        row("qa", "DYNAMIC_PROPERTY", "DPP_GREETING", "DPP_GREETING", "value", "hello-qa"),
        row("qa", "DYNAMIC_PROPERTY", "DPP_HI", "DPP_HI", "value", "Hi QA"),
        row("qa", "DYNAMIC_PROPERTY", "OTHER_PROCESS_PROP", "OTHER_PROCESS_PROP", "value", "nope", process_name="Some Other Process"),
        row("qa", "PROCESS_PROPERTY", "PP1", "Settings", "region", "eu-qa"),
        row("qa", "CROSS_REFERENCE", "XR1", "Codes", "ref1", "a", row_no=1),
        row("qa", "CROSS_REFERENCE", "XR1", "Codes", "ref2", "QA-1", row_no=1),
        row("qa", "CROSS_REFERENCE", "XR1", "Codes", "ref1", "b", row_no=2),
        row("qa", "CROSS_REFERENCE", "XR1", "Codes", "ref2", "QA-2", row_no=2),
    ]


def run(env, mode, rows):
    f = tempfile.mktemp(suffix=".json")
    json.dump(rows, open(f, "w"))
    os.environ.update(EXT_STORE_FILE=f, PROCESS_NAME=PROC, ENVIRONMENT=env, EXT_MODE=mode)
    a.BoomiClient = FakeBoomi
    a.environment_id = lambda e: e
    a.collect_dependencies = lambda client, cid: ({C1: ["P"], C2: ["P"]}, {})
    sys.argv = ["x", "--out", tempfile.mktemp()]
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            a.main()
        except SystemExit as exc:
            code = exc.code or 0
    return code, out.getvalue() + err.getvalue()


SECRETS = ("qapass-secret", "qauser", "hello-qa", "Hi QA", "eu-qa", "QA-1", "QA-2", "https://qa")


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        raise SystemExit(1)


def main():
    # --- plan (metadata only): nothing read, no value in output
    FakeBoomi.state = fresh_ext()
    meta = [{k: v for k, v in r.items() if k != "value_text"} for r in qa_rows()]
    code, text = run("qa", "plan", qa_rows())
    check(code == 0, "qa plan exits 0 (missing secret only warns for QA)")
    check(not any(s in text for s in SECRETS), "plan output shows no stored value")
    check("OTHER_PROCESS_PROP" not in text, "row scoped to another process is ignored")

    # --- PD policy stops on the missing connection secret
    pd = [dict(r, environment="pd") for r in qa_rows()]
    code, text = run("prod", "plan", pd)
    check(code == 3, "prod plan stops (exit 3) when a password has no stored value")

    # --- dry-run changes nothing in Boomi
    FakeBoomi.state = fresh_ext()
    before = copy.deepcopy(FakeBoomi.state)
    code, text = run("qa", "dry-run", qa_rows())
    check(code == 0 and FakeBoomi.state == before and FakeBoomi.last_payload is None, "dry-run sends nothing")

    # --- apply: one partial update, only changed items, verified
    FakeBoomi.state = fresh_ext()
    FakeBoomi.last_payload = None
    code, text = run("qa", "apply", qa_rows())
    p = FakeBoomi.last_payload
    check(code == 0, f"apply exits 0 (got {code})")
    check(p["partial"] is True and p["environmentId"] == "E", "payload is partial and keeps the environment id")
    conns = {c["id"]: c for c in p["connections"]["connection"]}
    check(set(conns) == {C1, C2}, "both connections with stored values are in the payload")
    c1_fields = {f["id"]: f for f in conns[C1]["field"]}
    check(set(c1_fields) == {"url", "user", "password"}, "only stored fields are sent for connection 1")
    check(c1_fields["url"]["encryptedValueSet"] is False if "encryptedValueSet" in c1_fields["url"] else True,
          "plain field is not flagged encrypted")
    check(c1_fields["password"]["encryptedValueSet"] is True and c1_fields["password"]["value"] == "qapass-secret",
          "password sent as an encrypted override")
    check({f["id"] for f in conns[C2]["field"]} == {"url"}, "connection 2: only the stored url is sent")
    props = {x["name"]: x for x in p["properties"]["property"]}
    check(set(props) == {"DPP_GREETING", "DPP_HI"} and props["DPP_HI"]["value"] == "Hi QA", "dynamic properties sent, other process's skipped")
    pp = p["processProperties"]["ProcessProperty"][0]
    check({v["key"] for v in pp["ProcessPropertyValue"]} == {"region", "mode"}, "process property sends EVERY value of the component")
    vals = {v["key"]: v for v in pp["ProcessPropertyValue"]}
    check(vals["region"]["value"] == "eu-qa" and vals["region"]["useDefault"] is False and vals["mode"]["useDefault"] is True,
          "only the stored key is overridden; the other stays on default")
    xr = p["crossReferences"]["crossReference"][0]
    check(xr["overrideValues"] is True and xr["CrossReferenceRows"]["row"] == [{"ref1": "a", "ref2": "QA-1"}, {"ref1": "b", "ref2": "QA-2"}],
          "cross-reference sends the whole table, rows in order")
    check("OTHER_PROCESS_PROP" not in json.dumps(p), "unrelated dynamic property untouched")
    check(not any(s in text for s in SECRETS), "apply output shows no stored value")
    check("Applied" in text and "verified" in text, "report says applied and verified")

    # --- verification catches a Boomi that silently ignores a value
    class Ignoring(FakeBoomi):
        def update_environment_extensions(self, env_id, payload):
            FakeBoomi.last_payload = payload
            return {}
    FakeBoomi.state = fresh_ext()
    a_BoomiClient = Ignoring
    f = tempfile.mktemp(suffix=".json")
    json.dump(qa_rows(), open(f, "w"))
    os.environ.update(EXT_STORE_FILE=f, PROCESS_NAME=PROC, ENVIRONMENT="qa", EXT_MODE="apply")
    a.BoomiClient = a_BoomiClient
    sys.argv = ["x", "--out", tempfile.mktemp()]
    out = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            a.main()
        except SystemExit as exc:
            code = exc.code or 0
    check(code == 4, "verification failure exits 4 when Boomi did not apply the change")

    # --- use_default row reverts a field
    FakeBoomi.state = fresh_ext()
    FakeBoomi.state["connections"]["connection"][0]["field"][0].update(useDefault=False, value="old")
    rows = [row("qa", "CONNECTION", C1, "Demo Conn", "url", "", use_default=1), row("qa", "CONNECTION", C1, "Demo Conn", "password", "pw", 1)]
    code, text = run("qa", "apply", rows)
    f1 = {f["id"]: f for f in FakeBoomi.last_payload["connections"]["connection"][0]["field"]}
    check(code in (0, 4) and f1["url"]["useDefault"] is True and "value" not in f1["url"], "use_default row reverts the field to the component value")

    # --- ambiguous names are refused
    rows = [row("qa", "CONNECTION", "REPLACE-A", "Demo Conn", "user", "u1"), row("qa", "CONNECTION", "REPLACE-B", "Demo Conn", "user", "u2")]
    ids, how = st.match_connection(st._normalise(rows[0], True) and [st._normalise(r, True) for r in rows], C1, "Demo Conn")
    check(how == "ambiguous" and ids == [], "two store connections with the same name are reported ambiguous, not guessed")

    print("\nAll extension tests passed.")


if __name__ == "__main__":
    main()
