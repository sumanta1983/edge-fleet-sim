"""Local SQLite storage on the edge box: the event outbox plus a little
key/value state (active software and config version) that must survive a
container restart."""
import json
import sqlite3


class LocalStore:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.executescript(
            """
            -- Outbox pattern: an event is committed here first and only
            -- deleted after the broker has acknowledged it (QoS 1 PUBACK).
            -- An outage or crash therefore delays events but never loses them.
            --
            -- seq is the per-device sequence number. AUTOINCREMENT (not just
            -- INTEGER PRIMARY KEY) guarantees a seq is never reused, even
            -- after sent rows are deleted, so (device_id, seq) is a safe
            -- idempotency key in the cloud.
            CREATE TABLE IF NOT EXISTS outbox (
                seq     INTEGER PRIMARY KEY AUTOINCREMENT,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS local_state (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

    # --- outbox ---------------------------------------------------------

    def add_event(self, event: dict) -> int:
        with self.db:
            cur = self.db.execute("INSERT INTO outbox (payload) VALUES (?)", (json.dumps(event),))
        return cur.lastrowid

    def pending(self, limit: int = 100) -> list[tuple[int, dict]]:
        rows = self.db.execute("SELECT seq, payload FROM outbox ORDER BY seq LIMIT ?", (limit,))
        return [(seq, json.loads(payload)) for seq, payload in rows]

    def mark_sent(self, seq: int) -> None:
        with self.db:
            self.db.execute("DELETE FROM outbox WHERE seq = ?", (seq,))

    def backlog(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]

    # --- local state ----------------------------------------------------

    def get(self, key: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT value FROM local_state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key: str, value: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO local_state (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
