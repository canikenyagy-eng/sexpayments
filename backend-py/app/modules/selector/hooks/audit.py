"""Structured audit log for admin-side selector mutations.

Every "manual" change — upsert_metrics from the HTTP API, enable/disable,
config reloads — runs through ``AuditLogger.record(...)``. By default it
writes to the python logger; replace with a sink that hits the audit DB if
the team wants persistent storage later.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional


logger = logging.getLogger("selector.audit")


@dataclass
class AuditEntry:
    ts: float
    selector_name: str
    action: str
    actor: Optional[str]
    target: str
    old_value: Any = None
    new_value: Any = None
    metadata: Optional[dict] = None


class AuditLogger:
    def __init__(self, *, log: Optional[logging.Logger] = None):
        self._log = log or logger

    def record(self, entry: AuditEntry) -> None:
        self._log.info(
            "selector_audit",
            extra={
                "event": "audit",
                "ts": entry.ts,
                "selector": entry.selector_name,
                "action": entry.action,
                "actor": entry.actor,
                "target": entry.target,
                "old_value": entry.old_value,
                "new_value": entry.new_value,
                "metadata": entry.metadata or {},
            },
        )

    def emit(
        self,
        *,
        selector_name: str,
        action: str,
        target: str,
        actor: Optional[str] = None,
        old_value: Any = None,
        new_value: Any = None,
        metadata: Optional[dict] = None,
    ) -> None:
        self.record(
            AuditEntry(
                ts=time.time(),
                selector_name=selector_name,
                action=action,
                actor=actor,
                target=target,
                old_value=old_value,
                new_value=new_value,
                metadata=metadata,
            )
        )
