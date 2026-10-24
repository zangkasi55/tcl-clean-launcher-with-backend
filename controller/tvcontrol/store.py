import json
import math
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import Rule, Theme


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        with self.lock, self.db:
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version > 2:
                raise RuntimeError("The database is newer than this controller")
            if version == 1:
                backup_path = path.with_name("controller-before-passwordless.sqlite3")
                if not backup_path.exists():
                    backup = sqlite3.connect(backup_path)
                    try:
                        self.db.backup(backup)
                    finally:
                        backup.close()
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS usage (
                    package TEXT NOT NULL, zone TEXT NOT NULL, day TEXT NOT NULL,
                    seconds REAL NOT NULL CHECK(seconds >= 0), PRIMARY KEY(package, zone, day)
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL,
                    authenticated INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL,
                    message TEXT NOT NULL
                );
            """)
            columns = {row["name"] for row in self.db.execute("PRAGMA table_info(sessions)")}
            if "authenticated" not in columns:
                self.db.execute("ALTER TABLE sessions ADD COLUMN authenticated "
                                "INTEGER NOT NULL DEFAULT 1")
            self.db.execute("PRAGMA user_version=2")
        if self.get("rule") is None:
            self.put("rule", Rule().model_dump())
        if self.get("theme") is None:
            self.put("theme", Theme().model_dump())

    def get(self, key: str):
        with self.lock:
            row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def put(self, key: str, value) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) "
                            "DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def rule(self) -> Rule:
        return Rule.model_validate(self.get("rule"))

    def usage(self, package: str, zone: str, at: datetime) -> float:
        day = at.astimezone(ZoneInfo(zone)).date().isoformat()
        with self.lock:
            row = self.db.execute(
                "SELECT seconds FROM usage WHERE package=? AND zone=? AND day=?",
                (package, zone, day),
            ).fetchone()
            return float(row[0]) if row else 0.0

    def add_interval(self, package: str, zone: str, start: datetime, end: datetime,
                     elapsed_seconds: float | None = None) -> None:
        start = start.astimezone(timezone.utc)
        end = end.astimezone(timezone.utc)
        wall_seconds = (end - start).total_seconds()
        measured = wall_seconds if elapsed_seconds is None else elapsed_seconds
        if wall_seconds < 0 or measured < 0 or not math.isfinite(measured):
            raise ValueError("Usage intervals must have finite, nonnegative elapsed time")
        if wall_seconds == 0:
            if measured != 0:
                raise ValueError("A zero wall interval cannot locate positive usage in a day")
            return
        scale = measured / wall_seconds
        tz = ZoneInfo(zone)
        cursor = start
        with self.lock, self.db:
            while cursor < end:
                local_day = cursor.astimezone(tz).date()
                midnight = datetime.combine(local_day + timedelta(days=1), datetime.min.time(), tz)
                boundary = min(end, midnight.astimezone(timezone.utc))
                seconds = (boundary - cursor).total_seconds() * scale
                self.db.execute(
                    "INSERT INTO usage VALUES (?,?,?,?) ON CONFLICT(package,zone,day) "
                    "DO UPDATE SET seconds=seconds+excluded.seconds",
                    (package, zone, local_day.isoformat(), seconds),
                )
                cursor = boundary

    def event(self, kind: str, message: str) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT INTO events(at,kind,message) VALUES (?,?,?)",
                            (datetime.now(timezone.utc).isoformat(), kind, message))
            self.db.execute("DELETE FROM events WHERE id NOT IN "
                            "(SELECT id FROM events ORDER BY id DESC LIMIT 300)")

    def events(self) -> list[dict]:
        with self.lock:
            return [dict(row) for row in self.db.execute(
                "SELECT at,kind,message FROM events ORDER BY id DESC LIMIT 30")]

    def create_session(self, token_hash: str, csrf: str, expires: float,
                       authenticated: bool = True) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT INTO sessions(token_hash,csrf,expires,authenticated) "
                            "VALUES (?,?,?,?)", (token_hash, csrf, expires, int(authenticated)))
            self.db.execute("DELETE FROM sessions WHERE expires<?",
                            (datetime.now(timezone.utc).timestamp(),))

    def session(self, token_hash: str, now: float) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM sessions WHERE token_hash=? AND expires>?",
                                  (token_hash, now)).fetchone()
            return dict(row) if row else None

    def logout(self, token_hash: str) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))

    def clear_sessions(self) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM sessions")

    def close(self) -> None:
        with self.lock:
            self.db.close()
