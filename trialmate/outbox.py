"""Outgoing messages: write first, then send.

Every outgoing text is stored with a unique idempotency key before any network
call (pattern from the earlier Hermes auto-reply engine: persist intent before
sending). Replies on medical turns wait in `needs_approval` until a coordinator
approves or edits them. Photon receives the same Idempotency-Key on retries, so
a retry after a crash or timeout cannot double-text a patient.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Optional

from .db import Database, now

MAX_ATTEMPTS = 4


class ChannelError(Exception):
    """Temporary failure: retry later with the same idempotency key."""


class PermanentChannelError(ChannelError):
    """Will not succeed on retry (e.g. patient never texted this line)."""


def enqueue(db: Database, patient_id: int, body: str, status: str, key: str, reason: str = "",
            kind: str = "reply", parent_external_id: Optional[str] = None) -> Optional[int]:
    assert status in {"queued", "needs_approval"}
    try:
        return db.insert(
            "INSERT INTO outbox(patient_id,body,status,reason,kind,idempotency_key,parent_external_id,created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (patient_id, body.strip()[:4000], status, reason, kind, key, parent_external_id, now()))
    except sqlite3.IntegrityError:
        return None  # same key already queued: never send twice


def approve(db: Database, outbox_id: int, body: Optional[str] = None) -> bool:
    edited = (body or "").strip() or None
    cur = db.run("UPDATE outbox SET status='queued', body=COALESCE(?,body), decided_at=?,"
                 " reason=CASE WHEN ? IS NULL THEN reason ELSE reason || ' (edited by coordinator)' END"
                 " WHERE id=? AND status='needs_approval'", (edited, now(), edited, outbox_id))
    if cur.rowcount:
        db.audit(None, "outbox:approve", f"{outbox_id}{' edited' if edited else ''}")
    return bool(cur.rowcount)


def reject(db: Database, outbox_id: int) -> bool:
    cur = db.run("UPDATE outbox SET status='rejected', decided_at=? WHERE id=? AND status='needs_approval'",
                 (now(), outbox_id))
    if cur.rowcount:
        db.audit(None, "outbox:reject", str(outbox_id))
    return bool(cur.rowcount)


def recover(db: Database) -> int:
    """After a restart, rows left in 'sending' are retried with the same idempotency key."""
    return db.run("UPDATE outbox SET status='queued' WHERE status='sending'").rowcount


def _claim(db: Database) -> Optional[dict]:
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM outbox WHERE status='queued' AND next_attempt_at<=? ORDER BY id LIMIT 1",
                           (now(),)).fetchone()
        if not row:
            return None
        conn.execute("UPDATE outbox SET status='sending', attempts=attempts+1 WHERE id=?", (row["id"],))
        return dict(row)


def deliver_one(app: Any) -> bool:
    """Send the oldest queued message. Returns False when nothing was due."""
    db = app.db
    row = _claim(db)
    if not row:
        return False
    patient = db.one("SELECT * FROM patients WHERE id=?", (row["patient_id"],))
    if not patient:
        db.run("UPDATE outbox SET status='failed', last_error='patient missing' WHERE id=?", (row["id"],))
        return True
    if patient["opted_out"] and row["kind"] != "system":
        db.run("UPDATE outbox SET status='suppressed', last_error='patient opted out' WHERE id=?", (row["id"],))
        return True
    channel = app.channels.get(patient["channel"])
    try:
        if channel is None:
            raise PermanentChannelError(f"channel '{patient['channel']}' is not configured")
        external_id = channel.send(patient, row["body"], row["idempotency_key"], row["parent_external_id"])
    except PermanentChannelError as e:
        db.run("UPDATE outbox SET status='failed', last_error=? WHERE id=?", (str(e)[:500], row["id"]))
    except Exception as e:
        attempts = row["attempts"] + 1
        if attempts >= MAX_ATTEMPTS:
            db.run("UPDATE outbox SET status='failed', last_error=? WHERE id=?", (str(e)[:500], row["id"]))
        else:
            db.run("UPDATE outbox SET status='queued', last_error=?, next_attempt_at=? WHERE id=?",
                   (str(e)[:500], now() + 2 ** attempts, row["id"]))
    else:
        t = now()
        with db.tx() as conn:
            conn.execute("UPDATE outbox SET status='sent', sent_at=?, last_error=NULL WHERE id=?", (t, row["id"]))
            conn.execute("INSERT INTO messages(patient_id,direction,body,external_id,processed,created_at)"
                         " VALUES(?,?,?,?,1,?)", (patient["id"], "out", row["body"], external_id, t))
    return True


def flush(app: Any, limit: int = 50) -> int:
    sent = 0
    while sent < limit and deliver_one(app):
        sent += 1
    return sent
