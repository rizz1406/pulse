"""
Database layer — one place that decides where data lives.

If TURSO_DATABASE_URL + TURSO_AUTH_TOKEN are set (in production on Render),
data is stored permanently in Turso's cloud SQLite. Otherwise it falls back to
a local SQLite file (for local testing). Everything else in the app uses the
same tiny cursor-like interface, so storage/goals/portions barely change.
"""

import sqlite3
import os
import config

TURSO_URL = os.getenv("TURSO_DATABASE_URL", "")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")
USE_TURSO = bool(TURSO_URL)

# NOTE: we do NOT import libsql at module load. Under gunicorn this module is
# imported in the master process before forking; libsql's native (Rust/tokio)
# threads don't survive a fork and deadlock. We import it lazily inside the
# connection, which runs in the worker process after the fork.


# ─────────────────────────────────────────────────────────────
# A small result wrapper so callers can do row["col"] like sqlite3.Row
# ─────────────────────────────────────────────────────────────
class _Row(dict):
    def __getitem__(self, k):
        if isinstance(k, int):
            return list(self.values())[k]
        return super().__getitem__(k)


class _Cursor:
    """Wraps a libsql or sqlite3 cursor to a common interface."""
    def __init__(self, backend, cur):
        self._backend = backend
        self._cur = cur

    def execute(self, sql, params=()):
        self._cur.execute(sql, params)
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in self._cur.description]
        return _Row(zip(cols, row))

    def fetchall(self):
        rows = self._cur.fetchall()
        if not rows:
            return []
        cols = [d[0] for d in self._cur.description]
        return [_Row(zip(cols, r)) for r in rows]


class _Conn:
    """Context-managed connection that commits on exit, for both backends."""
    def __init__(self):
        if USE_TURSO:
            import libsql  # lazy import — only in worker, after fork
            self._conn = libsql.connect(database=TURSO_URL, auth_token=TURSO_TOKEN)
        else:
            self._conn = sqlite3.connect(config.DB_PATH)

    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        return _Cursor(self, cur).execute(sql, params)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._conn.commit()
        try:
            self._conn.close()
        except Exception:
            pass


def connect():
    """Get a connection. Use as: `with db.connect() as c: c.execute(...)`."""
    return _Conn()