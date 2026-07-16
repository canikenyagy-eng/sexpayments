"""ClickHouse (sync) client singleton + schema bootstrap.

A **sync** client is used deliberately: the app runs in two process models —
the uvicorn web process (one long-lived event loop) and Celery workers (a fresh
``asyncio.run`` loop per task). A loop-bound async client can't span both. The
sync client is loop-agnostic; the web batch sink flushes it off the event loop
via ``run_in_executor``, and Celery inserts directly.

Lazy: ``clickhouse_connect`` is imported only on first use (i.e. only when
``CLICKHOUSE_ENABLED``), so dev/test runs that keep CH off never need the
package or a live CH instance. Not on any hot path.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_client: Optional[Any] = None  # clickhouse_connect sync Client

# The clickhouse_connect sync Client is NOT safe for concurrent use — its single
# connection/query context is shared. In the web process, reads (query) run on
# the default thread-pool executor and the batch sink inserts on its own executor
# thread, so multiple threads hit the one client at once → corrupted responses,
# which the fail-safe read path swallows and returns as empty. Serialise EVERY
# access to the shared client through this lock (held only for the fast CH
# round-trip, off the event loop). Use ``with ch.query_lock:`` around any
# ``get_client().query(...)`` / ``.insert(...)`` call.
query_lock = threading.Lock()


def get_client() -> Any:
    """Return (creating on first use) the shared sync ClickHouse client."""
    global _client
    if _client is not None:
        return _client

    import clickhouse_connect  # lazy: only when CH is actually used

    from app.core.config import get_settings

    s = get_settings()
    _client = clickhouse_connect.get_client(
        host=s.CLICKHOUSE_HOST,
        port=s.CLICKHOUSE_PORT,
        username=s.CLICKHOUSE_USER,
        password=s.CLICKHOUSE_PASSWORD,
        database=s.CLICKHOUSE_DB,
    )
    return _client


def ensure_schema(ddl_path: Path) -> None:
    """Run the DDL file (``;``-separated statements). Idempotent when the DDL
    uses ``CREATE TABLE IF NOT EXISTS``. Sync — call from an executor.

    ``--`` line comments are stripped before splitting on ``;`` — otherwise a
    semicolon *inside a comment* (e.g. ``(batched inserts; never per-row)``)
    would be treated as a statement separator, sending a comment-only fragment
    to the server and failing the whole bootstrap with a syntax error (code 62).
    (Assumes ``--`` never appears inside a string literal, which holds for our
    schema files — they only use ``DEFAULT ''``.)
    """
    client = get_client()
    raw = ddl_path.read_text()
    lines = []
    for line in raw.splitlines():
        idx = line.find("--")
        if idx != -1:
            line = line[:idx]
        lines.append(line)
    ddl = "\n".join(lines)
    for stmt in (s.strip() for s in ddl.split(";")):
        if stmt:
            client.command(stmt)


def close() -> None:
    global _client
    if _client is not None:
        try:
            _client.close()
        except Exception:  # noqa: BLE001 — shutdown best-effort
            pass
        _client = None
