"""
Where the per-environment extension values live. A small provider interface so the
backing store can change (Oracle now; Databricks / CyberArk later) without touching
the deploy logic.

Row shape (one value per row). Rows are keyed by the Boomi COMPONENT, not the
process, so a connection shared by many processes is stored once:

    environment     'qa' | 'pd'
    extension_type  CONNECTION | DYNAMIC_PROPERTY | PROCESS_PROPERTY | CROSS_REFERENCE
    component_id    Boomi component id (connection / property component / table); may be a
                    placeholder while only the NAME is known — see rows_for_connection()
    component_name  human name (the key people type); for a dynamic property, its name
    field_name      the field (url, user, password ...), the process-property key, the
                    dynamic-property value slot ('value') or the cross-reference column (ref1..)
    sub_key         reserved for nested keys (e.g. a trading-partner category)
    row_no          cross-reference table row number (1..n)
    use_default     1 = revert this field to the component's own value (no value needed)
    process_name    optional: row applies only when this process is promoted (NULL = any)
    connector_type  optional: disambiguates two connections that share a name
    value_text      the value; 'enc:<token>' when is_secret
    is_secret       1 | 0

Providers (EXT_STORE env var):
    oracle   python-oracledb, thin mode. Reads  <owner>.<qa|pd>_extension_values
             (or ..._extension_meta, which has NO value column, for planning). Works with the
             original schema too: if the v2 columns are not in the views yet it falls back to
             the original column list.
    file     a JSON list of rows at EXT_STORE_FILE (tests / offline demos).

Secret values are Fernet-encrypted with a key derived from SECRETS_KEY.
Values are never printed by anything in this module.
"""

import base64
import hashlib
import json
import os
import re

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

BASE_COLUMNS = ["environment", "extension_type", "component_id", "component_name", "field_name", "is_secret"]
V2_COLUMNS = ["sub_key", "row_no", "use_default", "process_name", "connector_type"]


class StoreError(RuntimeError):
    pass


def store_env(environment):
    """Pipeline environment name (dev|qa|prod) -> store environment name (qa|pd)."""
    mapping = {"qa": "qa", "prod": "pd", "pd": "pd"}
    if environment not in mapping:
        raise StoreError(f"No extension values are kept for environment '{environment}'.")
    return mapping[environment]


def _fernet():
    from cryptography.fernet import Fernet

    secret = os.environ.get("SECRETS_KEY", "")
    if not secret:
        raise StoreError("SECRETS_KEY is not set — it is needed to encrypt/decrypt secret values.")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest()))


def encrypt(plain):
    return "enc:" + _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt(stored):
    if not stored.startswith("enc:"):
        raise StoreError("A secret value is not in the expected encrypted format (re-seed it with seed_extension_values).")
    return _fernet().decrypt(stored[4:].encode("ascii")).decode("utf-8")


def _normalise(row, with_values):
    out = {
        "environment": row["environment"],
        "extension_type": (row["extension_type"] or "").upper(),
        "component_id": row["component_id"] or "",
        "component_name": row.get("component_name") or "",
        "field_name": row["field_name"] or "",
        "is_secret": bool(row.get("is_secret", 1)),
        "sub_key": row.get("sub_key") or "",
        "row_no": int(row["row_no"]) if row.get("row_no") not in (None, "") else None,
        "use_default": bool(row.get("use_default") or 0),
        "process_name": row.get("process_name") or "",
        "connector_type": row.get("connector_type") or "",
    }
    if with_values:
        value = row.get("value_text")
        if value is None:
            value = ""
        out["value"] = decrypt(value) if (out["is_secret"] and value) else value
    return out


class OracleStore:
    def __init__(self, user, password, dsn, owner=None):
        self.user, self.password, self.dsn = user, password, dsn
        self.owner = owner or os.environ.get("EXT_DB_OWNER", "cicd_owner")
        if not re.fullmatch(r"[A-Za-z0-9_$#]+", self.owner):
            raise StoreError("EXT_DB_OWNER contains unexpected characters.")

    def rows(self, environment, with_values=True):
        import oracledb

        env = store_env(environment)
        view = f"{self.owner}.{env}_extension_values" if with_values else f"{self.owner}.{env}_extension_meta"
        base = BASE_COLUMNS + (["value_text"] if with_values else [])
        raw = None
        try:
            with oracledb.connect(user=self.user, password=self.password, dsn=self.dsn) as conn, conn.cursor() as cur:
                for cols in (base + V2_COLUMNS, base):  # v2 first, original schema as the fallback
                    try:
                        cur.execute(f"SELECT {', '.join(cols)} FROM {view}")
                    except oracledb.Error as exc:
                        if "ORA-00904" in str(exc) and cols is not base:
                            continue  # the view predates the v2 columns
                        raise
                    names = [d[0].lower() for d in cur.description]
                    raw = [dict(zip(names, r)) for r in cur.fetchall()]
                    break
        except oracledb.Error as exc:
            raise StoreError(f"Could not read {view} as {self.user}: {exc}") from None
        return [_normalise(r, with_values) for r in raw]


class FileStore:
    def __init__(self, path):
        self.path = path

    def rows(self, environment, with_values=True):
        env = store_env(environment)
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        return [_normalise(r, with_values) for r in data if r["environment"] == env]


def open_store(role):
    """role: 'plan' (metadata only) or 'apply' (values). Credentials come from env vars."""
    kind = os.environ.get("EXT_STORE", "oracle").lower()
    if kind == "file":
        return FileStore(os.environ.get("EXT_STORE_FILE", "extension_values.json"))
    if kind != "oracle":
        raise StoreError(f"Unknown EXT_STORE '{kind}' (use 'oracle' or 'file').")
    prefix = "EXT_PLAN_DB" if role == "plan" else "EXT_DB"
    try:
        return OracleStore(os.environ[f"{prefix}_USER"], os.environ[f"{prefix}_PASSWORD"], os.environ["EXT_DB_DSN"])
    except KeyError as exc:
        raise StoreError(f"Missing environment variable {exc.args[0]} for the Oracle store.") from None


def is_real_id(value):
    return bool(_UUID.match(value or ""))


def applies_to_process(row, process_name):
    """A row with a process_name only applies when that process is promoted."""
    return not row.get("process_name") or row["process_name"].lower() == (process_name or "").lower()


def match_connection(rows, connection_id, connection_name, connector_type=""):
    """(rows, how) for one connection.

    how: 'id' (matched by componentId), 'name' (matched by name — the row's id is a
    placeholder, or differs because the connection was renamed/replaced), 'ambiguous' (several
    store names collide), or '' (nothing). By-ID always wins; by-name only fills in when no
    row carries the real id.
    """
    conn = [r for r in rows if r["extension_type"] == "CONNECTION"]
    by_id = [r for r in conn if r["component_id"] == connection_id]
    by_name = [r for r in conn if r["component_name"] and r["component_name"] == connection_name
               and not is_real_id(r["component_id"])]
    if connector_type:
        by_name = [r for r in by_name if not r["connector_type"] or r["connector_type"].lower() == connector_type.lower()]
    if len({r["component_id"] for r in by_name}) > 1 and not by_id:
        return [], "ambiguous"  # several different un-pinned store connections share this name
    # rows pinned to the real id win; un-pinned rows (same name) fill in the fields not pinned yet
    pinned = {(r["field_name"].lower(), r["sub_key"], r["row_no"]) for r in by_id}
    merged = by_id + [r for r in by_name if (r["field_name"].lower(), r["sub_key"], r["row_no"]) not in pinned]
    return merged, ("id" if by_id else ("name" if merged else ""))


def rows_for_connection(rows, connection_id, connection_name):
    """Backwards-compatible wrapper around match_connection()."""
    return match_connection(rows, connection_id, connection_name)[0]
