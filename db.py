"""SQLite storage: users (with salted PBKDF2 password hashes), refund notes, audit trail."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime

DB_PATH = os.environ.get("REFUND_DB", "refund_app.db")

# ---- workflow statuses
DRAFT = "DRAFT"                    # with underwriter, editable
WITH_CLAIMS = "WITH_CLAIMS"        # sent to Claims
CLAIMS_DONE = "CLAIMS_DONE"        # back with underwriter, claims completed
WITH_HOD = "WITH_HOD"              # sent to Head of Department
FINAL = "FINAL"                    # HOD done, back with underwriter, ready to download

STATUS_LABEL = {
    DRAFT: "Draft (with Underwriter)",
    WITH_CLAIMS: "With Claims",
    CLAIMS_DONE: "Claims done - with Underwriter",
    WITH_HOD: "With Head of Department",
    FINAL: "Checked - ready for final submission",
}

UNDERWRITER, CLAIMS, HOD = "underwriter", "claims", "hod"
ROLE_LABEL = {UNDERWRITER: "Underwriter", CLAIMS: "Claims Officer", HOD: "Head of Department"}

# Demo accounts created on first run. CHANGE THESE PASSWORDS after first login.
SEED_USERS = [
    ("underwriter1", "Underwriter One", UNDERWRITER, "Underwriter@123"),
    ("claims1", "Claims Officer One", CLAIMS, "Claims@123"),
    ("hod1", "Head Of Department", HOD, "Hod@123"),
]


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000).hex()


def init_db() -> None:
    with conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
                username TEXT PRIMARY KEY, full_name TEXT NOT NULL, role TEXT NOT NULL,
                pw_hash TEXT NOT NULL, salt TEXT NOT NULL, signature BLOB);
            CREATE TABLE IF NOT EXISTS refunds(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ref_no TEXT, status TEXT NOT NULL,
                created_by TEXT NOT NULL, created_at TEXT, updated_at TEXT, data TEXT NOT NULL,
                uw_sig BLOB, claims_sig BLOB, hod_sig BLOB);
            CREATE TABLE IF NOT EXISTS history(
                id INTEGER PRIMARY KEY AUTOINCREMENT, refund_id INTEGER, at TEXT,
                actor TEXT, action TEXT, note TEXT);
            """
        )
        if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            for u, n, r, p in SEED_USERS:
                salt = secrets.token_bytes(16)
                c.execute("INSERT INTO users VALUES(?,?,?,?,?,NULL)", (u, n, r, _hash(p, salt), salt.hex()))


# ------------------------------------------------------------------ users
def verify_login(username: str, password: str) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM users WHERE username=?", (username.strip().lower(),)).fetchone()
    if not row:
        return None
    if hmac.compare_digest(_hash(password, bytes.fromhex(row["salt"])), row["pw_hash"]):
        return {"username": row["username"], "full_name": row["full_name"], "role": row["role"]}
    return None


def change_password(username: str, new_password: str) -> None:
    salt = secrets.token_bytes(16)
    with conn() as c:
        c.execute("UPDATE users SET pw_hash=?, salt=? WHERE username=?",
                  (_hash(new_password, salt), salt.hex(), username))


def get_signature(username: str) -> bytes | None:
    with conn() as c:
        row = c.execute("SELECT signature FROM users WHERE username=?", (username,)).fetchone()
    return row["signature"] if row else None


def set_signature(username: str, png: bytes | None) -> None:
    with conn() as c:
        c.execute("UPDATE users SET signature=? WHERE username=?", (png, username))


# ---------------------------------------------------------------- refunds
def _row_to_rec(row) -> dict:
    rec = dict(row)
    rec["data"] = json.loads(rec["data"])
    return rec


def create_refund(created_by: str, data: dict) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO refunds(status,created_by,created_at,updated_at,data) VALUES(?,?,?,?,?)",
            (DRAFT, created_by, _now(), _now(), json.dumps(data)))
        rid = cur.lastrowid
        c.execute("UPDATE refunds SET ref_no=? WHERE id=?", (f"RF-{datetime.now().year}-{rid:05d}", rid))
    add_history(rid, created_by, "Created draft")
    return rid


def update_refund(rid: int, data: dict | None = None, status: str | None = None, **sigs) -> None:
    """sigs may contain uw_sig / claims_sig / hod_sig (PNG bytes)."""
    sets, vals = ["updated_at=?"], [_now()]
    if data is not None:
        sets.append("data=?"); vals.append(json.dumps(data))
    if status is not None:
        sets.append("status=?"); vals.append(status)
    for k, v in sigs.items():
        if k not in ("uw_sig", "claims_sig", "hod_sig"):
            raise ValueError(k)
        sets.append(f"{k}=?"); vals.append(v)
    vals.append(rid)
    with conn() as c:
        c.execute(f"UPDATE refunds SET {', '.join(sets)} WHERE id=?", vals)


def get_refund(rid: int) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM refunds WHERE id=?", (rid,)).fetchone()
    return _row_to_rec(row) if row else None


def list_refunds() -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT id,ref_no,status,created_by,created_at,updated_at,data FROM refunds ORDER BY id DESC"
        ).fetchall()
    return [_row_to_rec(r) for r in rows]


def add_history(rid: int, actor: str, action: str, note: str = "") -> None:
    with conn() as c:
        c.execute("INSERT INTO history(refund_id,at,actor,action,note) VALUES(?,?,?,?,?)",
                  (rid, _now(), actor, action, note))


def get_history(rid: int) -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT at,actor,action,note FROM history WHERE refund_id=? ORDER BY id", (rid,)).fetchall()
    return [dict(r) for r in rows]
