"""Small atomic SQLite document store. Money remains canonical strings in JSON."""

from __future__ import annotations
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from .models import DomainError, uid, now


class Store:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "bank-rec.sqlite3"
        self.lock = threading.RLock()
        with self.connect() as conn:
            conn.executescript(
                "CREATE TABLE IF NOT EXISTS workspaces (id TEXT PRIMARY KEY, data TEXT NOT NULL); CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL); CREATE TABLE IF NOT EXISTS previews (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, session_id TEXT NOT NULL, data TEXT NOT NULL);"
            )

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def all(self):
        with self.connect() as conn:
            return [
                json.loads(row["data"]) for row in conn.execute("SELECT data FROM workspaces ORDER BY rowid")
            ]

    def active(self):
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM preferences WHERE key='active'").fetchone()
            result = (
                conn.execute("SELECT data FROM workspaces WHERE id=?", (row["value"],)).fetchone()
                if row
                else None
            )
            if not result:
                raise DomainError("Create or select a client workspace first.")
            return json.loads(result["data"])

    def save(self, workspace):
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO workspaces VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (workspace["id"], json.dumps(workspace, allow_nan=False)),
            )

    def select(self, workspace_id):
        with self.lock, self.connect() as conn:
            if not conn.execute("SELECT id FROM workspaces WHERE id=?", (workspace_id,)).fetchone():
                raise DomainError("Workspace was not found.")
            conn.execute(
                "INSERT INTO preferences VALUES ('active',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (workspace_id,),
            )
        return self.active()

    def create(self, name, currency="USD", synthetic=False):
        workspace = {
            "id": uid("workspace"),
            "name": name,
            "currency": currency,
            "synthetic": synthetic,
            "version": 1,
            "created_at": now(),
            "active_session_id": None,
            "sessions": [],
            "events": [],
        }
        with self.lock:
            self.save(workspace)
            return self.select(workspace["id"])

    @contextmanager
    def edit(
        self,
        expected_workspace_id=None,
        expected_session_id=None,
        expected_input_version=None,
        expected_workspace_version=None,
        bump_version=True,
    ):
        with self.lock:
            workspace = self.active()
            if expected_workspace_id is not None and workspace["id"] != expected_workspace_id:
                raise DomainError(
                    "The active workspace changed before this write. Start again in the selected workspace."
                )
            if expected_workspace_version is not None and workspace["version"] != expected_workspace_version:
                raise DomainError("The selected workspace revision changed before this write.")
            if expected_session_id is not None and workspace.get("active_session_id") != expected_session_id:
                raise DomainError("The active reconciliation changed before this write.")
            if expected_input_version is not None:
                session = next(
                    (s for s in workspace["sessions"] if s["id"] == workspace.get("active_session_id")), None
                )
                if not session or session["input_version"] != expected_input_version:
                    raise DomainError("The input version changed before this write.")
            yield workspace
            if bump_version:
                workspace["version"] += 1
            self.save(workspace)

    def preview(self, workspace_id, session_id, preview):
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO previews VALUES (?,?,?,?)",
                (preview["id"], workspace_id, session_id, json.dumps(preview)),
            )

    def get_preview(self, identifier, workspace_id, session_id):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT data FROM previews WHERE id=? AND workspace_id=? AND session_id=?",
                (identifier, workspace_id, session_id),
            ).fetchone()
        if not row:
            raise DomainError("Preview was not found in this selected session.")
        return json.loads(row["data"])

    def delete(self, identifier):
        with self.lock, self.connect() as conn:
            conn.execute("DELETE FROM workspaces WHERE id=?", (identifier,))
            conn.execute("DELETE FROM previews WHERE workspace_id=?", (identifier,))
            row = conn.execute("SELECT id FROM workspaces ORDER BY rowid LIMIT 1").fetchone()
            conn.execute("DELETE FROM preferences WHERE key='active'")
            if row:
                conn.execute("INSERT INTO preferences VALUES ('active',?)", (row["id"],))
