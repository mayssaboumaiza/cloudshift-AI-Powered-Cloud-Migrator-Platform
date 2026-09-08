"""
executor/log_stream.py — Writes runner log lines to the pipeline_events table
so the existing SSE infrastructure picks them up without any changes.

Each terraform output line becomes a 'progress' event with phase='agent_03'
and payload containing the log line. The SSE router (already reading
pipeline_events by thread_id) streams these to the browser automatically.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

logger = logging.getLogger("RunnerLogStream")


def _dsn() -> str:
    return (
        f"host={os.getenv('DB_HOST', 'localhost')} "
        f"port={os.getenv('DB_PORT', '5432')} "
        f"dbname={os.getenv('DB_NAME', 'cloud_migrator')} "
        f"user={os.getenv('DB_USER', 'postgres')} "
        f"password={os.getenv('DB_PASSWORD', 'postgres')}"
    )


class RunnerLogStream:
    """
    Dual-write logger: persists log lines to deployment_logs (for /logs endpoint)
    AND emits them as pipeline_events (for SSE streaming to the browser).

    Usage:
        stream = RunnerLogStream(job_id="...", thread_id="...", stage="apply")
        stream.write("Terraform initialized successfully.")
        stream.write_event("apply_complete", payload={"outputs": {...}})
    """

    def __init__(self, job_id: str, thread_id: str, stage: str) -> None:
        self.job_id = job_id
        self.thread_id = thread_id
        self.stage = stage
        self._conn: psycopg2.extensions.connection | None = None

    def _get_conn(self) -> psycopg2.extensions.connection:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(_dsn(), cursor_factory=psycopg2.extras.RealDictCursor)
            self._conn.autocommit = False
        return self._conn

    def write(self, line: str, stream: str = "stdout") -> None:
        """Write one log line to deployment_logs AND pipeline_events."""
        now = datetime.now(timezone.utc)
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                # deployment_logs (for /logs polling)
                cur.execute(
                    "INSERT INTO deployment_logs (job_id, stage, stream, line, created_at) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (self.job_id, self.stage, stream, line, now),
                )
                # pipeline_events (for SSE streaming)
                cur.execute(
                    "INSERT INTO pipeline_events (thread_id, event_type, phase, payload, created_at) "
                    "VALUES (%s, 'progress', %s, %s, %s)",
                    (
                        self.thread_id,
                        f"agent_03",
                        psycopg2.extras.Json({
                            "stage": self.stage,
                            "stream": stream,
                            "line": line,
                            "job_id": self.job_id,
                            "ts": now.isoformat(),
                        }),
                        now,
                    ),
                )
            conn.commit()
        except psycopg2.OperationalError as exc:
            # H-1: TCP-level disconnect — psycopg2 .closed is 0 even on broken connections.
            # Reset so _get_conn() opens a fresh socket on the next write call.
            try:
                conn.rollback()
            except Exception:
                pass
            self._conn = None
            logger.warning("RunnerLogStream.write: DB connection lost, will reconnect — %s", type(exc).__name__)
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            logger.warning("RunnerLogStream.write failed: %s", type(exc).__name__)

    def write_event(self, event_type: str, payload: dict | None = None) -> None:
        """Write a structured lifecycle event (e.g. plan_ready, apply_complete)."""
        now = datetime.now(timezone.utc)
        conn = self._get_conn()
        full_payload = {"job_id": self.job_id, "stage": self.stage, "ts": now.isoformat()}
        if payload:
            full_payload.update(payload)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO pipeline_events (thread_id, event_type, phase, payload, created_at) "
                    "VALUES (%s, %s, 'agent_03', %s, %s)",
                    (
                        self.thread_id,
                        event_type,
                        psycopg2.extras.Json(full_payload),
                        now,
                    ),
                )
            conn.commit()
        except psycopg2.OperationalError as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            self._conn = None
            logger.warning("RunnerLogStream.write_event: DB connection lost, will reconnect — %s", type(exc).__name__)
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            logger.warning("RunnerLogStream.write_event failed event=%s: %s", event_type, type(exc).__name__)

    def close(self) -> None:
        if self._conn and not self._conn.closed:
            self._conn.close()

    def __enter__(self) -> "RunnerLogStream":
        return self

    def __exit__(self, *_) -> None:
        self.close()
