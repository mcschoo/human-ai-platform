import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from .models import (
    AppRegistration,
    CallbackAction,
    GroupPolicy,
    PersonalityTemplate,
    RuntimeEvent,
    SessionRegistration,
    StoredApp,
)

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS apps (
  app_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, callback_url TEXT NOT NULL,
  token_hash TEXT NOT NULL, webhook_secret TEXT NOT NULL,
  capabilities TEXT NOT NULL, defaults TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  app_id TEXT NOT NULL, session_id TEXT NOT NULL, group_id TEXT,
  registration TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'registered',
  created_at TEXT NOT NULL, PRIMARY KEY (app_id, session_id),
  FOREIGN KEY (app_id) REFERENCES apps(app_id)
);
CREATE TABLE IF NOT EXISTS events (
  app_id TEXT NOT NULL, event_id TEXT NOT NULL, session_id TEXT NOT NULL,
  payload TEXT NOT NULL, received_at TEXT NOT NULL,
  PRIMARY KEY (app_id, event_id)
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, app_id TEXT NOT NULL,
  session_id TEXT NOT NULL, channel TEXT NOT NULL, sender_id TEXT,
  role TEXT NOT NULL, text TEXT NOT NULL, event_id TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS personalities (
  app_id TEXT NOT NULL, personality_id TEXT NOT NULL, name TEXT NOT NULL,
  prompt TEXT NOT NULL, PRIMARY KEY (app_id, personality_id)
);
CREATE TABLE IF NOT EXISTS group_policies (
  app_id TEXT NOT NULL, group_id TEXT NOT NULL, policy TEXT NOT NULL,
  PRIMARY KEY (app_id, group_id)
);
CREATE TABLE IF NOT EXISTS pending_deliveries (
  action_id TEXT PRIMARY KEY, app_id TEXT NOT NULL, session_id TEXT NOT NULL,
  action TEXT NOT NULL, due_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT
);
CREATE TABLE IF NOT EXISTS webhook_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, action_id TEXT NOT NULL,
  attempted_at TEXT NOT NULL, status_code INTEGER, error TEXT,
  success INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, app_id TEXT, session_id TEXT,
  kind TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS session_group_idx ON sessions(app_id, group_id);
CREATE INDEX IF NOT EXISTS message_session_idx
  ON messages(app_id, session_id, id);
CREATE INDEX IF NOT EXISTS pending_due_idx
  ON pending_deliveries(status, due_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Repository:
    def __init__(self, path: str):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(SCHEMA)

    def save_app(self, app: AppRegistration) -> None:
        values = (
            app.app_id,
            app.display_name,
            str(app.callback_url),
            _hash_token(app.bearer_token),
            app.webhook_secret,
            app.capabilities.model_dump_json(by_alias=True),
            app.defaults.model_dump_json(by_alias=True),
            _now(),
        )
        with self.connect() as db:
            db.execute(
                """INSERT INTO apps VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(app_id) DO UPDATE SET display_name=excluded.display_name,
                callback_url=excluded.callback_url, token_hash=excluded.token_hash,
                webhook_secret=excluded.webhook_secret,
                capabilities=excluded.capabilities, defaults=excluded.defaults""",
                values,
            )
        self.audit(app.app_id, None, "app.registered", {"name": app.display_name})

    def get_app(self, app_id: str) -> StoredApp | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM apps WHERE app_id=?", (app_id,)).fetchone()
        if not row:
            return None
        return StoredApp.model_validate(
            {
                "appId": row["app_id"],
                "displayName": row["display_name"],
                "callbackUrl": row["callback_url"],
                "capabilities": json.loads(row["capabilities"]),
                "defaults": json.loads(row["defaults"]),
            }
        )

    def list_apps(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT app_id, display_name, callback_url FROM apps ORDER BY app_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def app_token_valid(self, app_id: str, token: str) -> bool:
        with self.connect() as db:
            row = db.execute(
                "SELECT token_hash FROM apps WHERE app_id=?", (app_id,)
            ).fetchone()
        return bool(row and row["token_hash"] == _hash_token(token))

    def webhook_target(self, app_id: str) -> tuple[str, str] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT callback_url, webhook_secret FROM apps WHERE app_id=?",
                (app_id,),
            ).fetchone()
        return (row["callback_url"], row["webhook_secret"]) if row else None

    def save_session(self, app_id: str, session: SessionRegistration) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO sessions
                (app_id, session_id, group_id, registration, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(app_id, session_id) DO UPDATE SET
                group_id=excluded.group_id, registration=excluded.registration""",
                (
                    app_id,
                    session.session_id,
                    session.group_id,
                    session.model_dump_json(by_alias=True),
                    _now(),
                ),
            )
        self.audit(
            app_id,
            session.session_id,
            "session.registered",
            {"group": session.group_id},
        )

    def get_session(self, app_id: str, session_id: str) -> SessionRegistration | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT registration FROM sessions WHERE app_id=? AND session_id=?",
                (app_id, session_id),
            ).fetchone()
        return SessionRegistration.model_validate_json(row[0]) if row else None

    def list_groups(self, app_id: str) -> list[str]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT DISTINCT COALESCE(group_id, '(ungrouped)') AS group_id
                FROM sessions WHERE app_id=? ORDER BY group_id""",
                (app_id,),
            ).fetchall()
        return [row["group_id"] for row in rows]

    def list_sessions(
        self, app_id: str, group_id: str | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT session_id, group_id, state, created_at FROM sessions WHERE app_id=?"
        params: list[Any] = [app_id]
        if group_id:
            query += " AND COALESCE(group_id, '(ungrouped)')=?"
            params.append(group_id)
        query += " ORDER BY created_at DESC"
        with self.connect() as db:
            rows = db.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def set_session_state(self, app_id: str, session_id: str, state: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE sessions SET state=? WHERE app_id=? AND session_id=?",
                (state, app_id, session_id),
            )
            if state == "ended":
                db.execute(
                    """UPDATE pending_deliveries SET status='cancelled'
                    WHERE app_id=? AND session_id=? AND status='pending'""",
                    (app_id, session_id),
                )

    # Input: One app event and its authenticated app ID.
    # Output: True when inserted, or False when the event was already seen.
    def record_event(self, app_id: str, event: RuntimeEvent) -> bool:
        try:
            with self.connect() as db:
                db.execute(
                    "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                    (
                        app_id,
                        event.event_id,
                        event.session_id,
                        event.model_dump_json(by_alias=True),
                        _now(),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def add_message(
        self,
        app_id: str,
        session_id: str,
        channel: str,
        sender_id: str | None,
        role: str,
        text: str,
        event_id: str | None = None,
    ) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO messages
                (app_id, session_id, channel, sender_id, role, text, event_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    app_id,
                    session_id,
                    channel,
                    sender_id,
                    role,
                    text,
                    event_id,
                    _now(),
                ),
            )

    def transcript(
        self, app_id: str, session_id: str, channel: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        query = """SELECT channel, sender_id, role, text, event_id, created_at
            FROM messages WHERE app_id=? AND session_id=?"""
        params: list[Any] = [app_id, session_id]
        if channel:
            query += " AND channel=?"
            params.append(channel)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.connect() as db:
            rows = db.execute(query, params).fetchall()
        return [dict(row) for row in reversed(rows)]

    def save_personality(self, app_id: str, item: PersonalityTemplate) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO personalities VALUES (?, ?, ?, ?)
                ON CONFLICT(app_id, personality_id) DO UPDATE SET
                name=excluded.name, prompt=excluded.prompt""",
                (app_id, item.personality_id, item.name, item.prompt),
            )

    def personalities(self, app_id: str) -> list[dict[str, str]]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT personality_id, name, prompt FROM personalities
                WHERE app_id=? ORDER BY name""",
                (app_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_group_policy(self, app_id: str, policy: GroupPolicy) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO group_policies VALUES (?, ?, ?)
                ON CONFLICT(app_id, group_id) DO UPDATE SET policy=excluded.policy""",
                (app_id, policy.group_id, policy.model_dump_json(by_alias=True)),
            )

    def group_policy(self, app_id: str, group_id: str | None) -> GroupPolicy | None:
        if not group_id:
            return None
        with self.connect() as db:
            row = db.execute(
                "SELECT policy FROM group_policies WHERE app_id=? AND group_id=?",
                (app_id, group_id),
            ).fetchone()
        return GroupPolicy.model_validate_json(row["policy"]) if row else None

    def personality_prompt(self, app_id: str, personality_id: str | None) -> str:
        if not personality_id:
            return ""
        with self.connect() as db:
            row = db.execute(
                "SELECT prompt FROM personalities WHERE app_id=? AND personality_id=?",
                (app_id, personality_id),
            ).fetchone()
        return row["prompt"] if row else ""

    def schedule(self, action: CallbackAction, due_at: datetime) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT OR IGNORE INTO pending_deliveries
                (action_id, app_id, session_id, action, due_at)
                VALUES (?, ?, ?, ?, ?)""",
                (
                    action.action_id,
                    action.app_id,
                    action.session_id,
                    action.model_dump_json(by_alias=True),
                    due_at.isoformat(),
                ),
            )

    def due_deliveries(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT * FROM pending_deliveries
                WHERE status='pending' AND due_at<=? ORDER BY due_at LIMIT ?""",
                (_now(), limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def finish_delivery(
        self, action_id: str, success: bool, error: str | None = None
    ) -> None:
        status = "sent" if success else "failed"
        with self.connect() as db:
            db.execute(
                """UPDATE pending_deliveries SET status=?, attempts=attempts+1,
                last_error=? WHERE action_id=?""",
                (status, error, action_id),
            )

    def retry_delivery(self, action_id: str, due_at: datetime, error: str) -> None:
        with self.connect() as db:
            db.execute(
                """UPDATE pending_deliveries SET attempts=attempts+1,
                due_at=?, last_error=? WHERE action_id=?""",
                (due_at.isoformat(), error, action_id),
            )

    def record_webhook_attempt(
        self,
        action_id: str,
        success: bool,
        status_code: int | None,
        error: str | None,
    ) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO webhook_attempts VALUES (NULL, ?, ?, ?, ?, ?)",
                (action_id, _now(), status_code, error, int(success)),
            )

    def webhook_health(self, app_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT p.action_id, p.session_id, p.status, p.attempts,
                p.last_error, MAX(w.attempted_at) AS last_attempt
                FROM pending_deliveries p LEFT JOIN webhook_attempts w
                ON p.action_id=w.action_id WHERE p.app_id=?
                GROUP BY p.action_id ORDER BY p.due_at DESC LIMIT 100""",
                (app_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def audit(
        self,
        app_id: str | None,
        session_id: str | None,
        kind: str,
        detail: dict[str, Any],
    ) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO audit_events VALUES (NULL, ?, ?, ?, ?, ?)",
                (app_id, session_id, kind, json.dumps(detail), _now()),
            )

    def audit_log(
        self, app_id: str, session_id: str | None = None
    ) -> list[dict[str, Any]]:
        query = """SELECT session_id, kind, detail, created_at FROM audit_events
            WHERE app_id=?"""
        params: list[Any] = [app_id]
        if session_id:
            query += " AND session_id=?"
            params.append(session_id)
        query += " ORDER BY id DESC LIMIT 200"
        with self.connect() as db:
            rows = db.execute(query, params).fetchall()
        return [dict(row) for row in rows]
