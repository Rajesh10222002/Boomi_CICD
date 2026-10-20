"""
Where the per-environment extension values (connection user / password / URL ...)
live. A small provider interface so the backing store can change (local Oracle
now; CyberArk / a hosted DB later) without touching the deploy logic.

Row shape (one value per row, keyed by the Boomi COMPONENT, not the process, so a
connection shared by many processes is stored once):

    environment     'qa' | 'pd'
    extension_type  'CONNECTION' (others reserved)
    component_id    Boomi connection componentId (or a placeholder + component_name)
    component_name  human name of the connection
    field_name      Boomi extension field id: url, user, password, ...
    value_text      the value; 'enc:<token>' when is_secret
    is_secret       1 | 0

Providers (EXT_STORE env var):
    oracle   python-oracledb, thin mode. Reads  <owner>.<qa|pd>_extension_values
             (or ..._extension_meta, which has NO value column, for planning).
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
        "extension_type": row["extension_type"],
        "component_id": row["component_id"],
        "component_name": row.get("component_name") or "",
        "field_name": row["field_name"],
        "is_secret": bool(row.get("is_secret", 1)),
    }
    if with_values:
        value = row["value_text"]
        out["value"] = decrypt(value) if out["is_secret"] else value
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
        cols = ("environment, extension_type, component_id, component_name, field_name, is_secret"
                + (", value_text" if with_values else ""))
        try:
            with oracledb.connect(user=self.user, password=self.password, dsn=self.dsn) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT {cols} FROM {view}")
                names = [d[0].lower() for d in cur.description]
                raw = [dict(zip(names, r)) for r in cur.fetchall()]
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


def rows_for_connection(rows, connection_id, connection_name):
    """Rows for one connection: by componentId, or — for rows whose component_id isn't a real
    Boomi id yet (a placeholder) — by connection name."""
    out = []
    for r in rows:
        if r["extension_type"] != "CONNECTION":
            continue
        if r["component_id"] == connection_id:
            out.append(r)
        elif not _UUID.match(r["component_id"]) and r["component_name"] and r["component_name"] == connection_name:
            out.append(r)
    return out
