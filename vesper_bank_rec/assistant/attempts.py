"""Durable request receipts: uncertain retries never silently spend twice."""

import json
import re
import sqlite3
from threading import RLock

from . import AssistantError
from ..models import digest, now


class AssistantAttempts:
    def __init__(self, data_dir):
        self.path = data_dir / "assistant-attempts.sqlite3"
        self.lock = RLock()
        with self.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS attempts ("
                "id TEXT PRIMARY KEY, workspace_hash TEXT NOT NULL, fingerprint TEXT NOT NULL, "
                "status TEXT NOT NULL, created_at TEXT NOT NULL, result TEXT)"
            )

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def claim(self, request_id, scope, message, rehearsal):
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{12,100}", request_id):
            raise AssistantError("Refresh the app before starting this assistant request.")
        fingerprint = digest([scope, message, rehearsal])
        with self.lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT * FROM attempts WHERE id=?", (request_id,)).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise AssistantError("This request belongs to different work. Start a new question in the current period.")
                if existing["status"] == "succeeded":
                    return json.loads(existing["result"])
                raise AssistantError(
                    "This request was already started and will not be charged again automatically. "
                    "Review any saved drafts before asking a new question. A new live request can incur API usage."
                )
            connection.execute("INSERT INTO attempts VALUES (?,?,?,?,?,NULL)", (
                request_id, digest(scope["id"]), fingerprint, "started", now(),
            ))
        return None

    def finish(self, request_id, status, result=None):
        if status not in {"succeeded", "failed", "cancelled"}:
            raise ValueError("Unsupported assistant receipt status.")
        encoded = json.dumps(result, allow_nan=False) if result is not None else None
        with self.lock, self.connect() as connection:
            connection.execute(
                "UPDATE attempts SET status=?, result=? WHERE id=? AND status='started'",
                (status, encoded, request_id),
            )

    def delete_workspace(self, workspace_id):
        with self.lock, self.connect() as connection:
            connection.execute("DELETE FROM attempts WHERE workspace_hash=?", (digest(workspace_id),))
