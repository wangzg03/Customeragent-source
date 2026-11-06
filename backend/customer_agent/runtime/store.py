from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL, request TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL, summary TEXT, error TEXT, cancel INTEGER NOT NULL DEFAULT 0, idempotency_key TEXT UNIQUE)"
            )
            if "resume" not in {
                r["name"] for r in c.execute("PRAGMA table_info(jobs)")
            }:
                c.execute(
                    "ALTER TABLE jobs ADD COLUMN resume INTEGER NOT NULL DEFAULT 0"
                )

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=15000")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def decode(self, row):
        if row is None:
            return None
        out = dict(row)
        out["request"] = json.loads(out["request"])
        out["summary"] = json.loads(out["summary"]) if out["summary"] else None
        out.pop("idempotency_key", None)
        return out

    def create(self, request, count, limit=16, key=None):
        now = utcnow()
        body = json.dumps(request.model_dump(), ensure_ascii=False, sort_keys=True)
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            if key:
                existing = c.execute(
                    "SELECT * FROM jobs WHERE idempotency_key=?", (key,)
                ).fetchone()
                if existing:
                    if existing["request"] != body:
                        raise ValueError("idempotency_key_payload_conflict")
                    return self.decode(existing)
            if (
                c.execute(
                    "SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')"
                ).fetchone()[0]
                >= limit
            ):
                raise OverflowError("job_queue_full")
            uid = uuid.uuid4().hex
            c.execute(
                "INSERT INTO jobs(id,name,status,request,created_at,updated_at,total,idempotency_key) VALUES(?,?,'queued',?,?,?,?,?)",
                (uid, request.name, body, now, now, count, key),
            )
            return self.decode(
                c.execute("SELECT * FROM jobs WHERE id=?", (uid,)).fetchone()
            )

    def get(self, uid):
        with self.connection() as c:
            return self.decode(
                c.execute("SELECT * FROM jobs WHERE id=?", (uid,)).fetchone()
            )

    def list(self, limit=100, offset=0):
        with self.connection() as c:
            return [
                self.decode(r)
                for r in c.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            ]

    def claim(self):
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            r = c.execute(
                "SELECT * FROM jobs WHERE status='queued' AND cancel=0 ORDER BY created_at LIMIT 1"
            ).fetchone()
            if r is None:
                return None
            c.execute(
                "UPDATE jobs SET status='running',updated_at=? WHERE id=?",
                (utcnow(), r["id"]),
            )
            return self.decode(r)

    def progress(self, uid, done, total):
        with self.connection() as c:
            c.execute(
                "UPDATE jobs SET done=?,total=?,updated_at=? WHERE id=?",
                (done, total, utcnow(), uid),
            )

    def finish(self, uid, status, summary=None, error=None):
        with self.connection() as c:
            c.execute(
                "UPDATE jobs SET status=?,summary=?,error=?,updated_at=? WHERE id=?",
                (
                    status,
                    json.dumps(summary, ensure_ascii=False) if summary else None,
                    error,
                    utcnow(),
                    uid,
                ),
            )

    def cancel(self, uid):
        with self.connection() as c:
            c.execute(
                "UPDATE jobs SET cancel=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END,updated_at=? WHERE id=? AND status IN ('queued','running')",
                (utcnow(), uid),
            )

    def recover(self):
        with self.connection() as c:
            c.execute(
                "UPDATE jobs SET status='interrupted',error='service_restarted; partial episodes retained',updated_at=? WHERE status='running'",
                (utcnow(),),
            )

    def resume(self, uid, limit=16):
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT status FROM jobs WHERE id=?", (uid,)).fetchone()
            if not row or row["status"] not in {"failed", "interrupted"}:
                raise ValueError("only_failed_or_interrupted_jobs_can_resume")
            if (
                c.execute(
                    "SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')"
                ).fetchone()[0]
                >= limit
            ):
                raise ValueError("job_queue_full")
            c.execute(
                "UPDATE jobs SET status='queued',cancel=0,resume=1,error=NULL,updated_at=? WHERE id=?",
                (utcnow(), uid),
            )
        return self.get(uid)
