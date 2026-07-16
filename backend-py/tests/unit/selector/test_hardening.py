"""Rate limiter, outlier detector, audit log, schema migration."""
from __future__ import annotations

import logging
import time

import pytest

from app.modules.selector.core.stats import EntityStats, SCHEMA_VERSION
from app.modules.selector.hooks.audit import AuditEntry, AuditLogger
from app.modules.selector.hooks.outlier import OutlierDetector
from app.modules.selector.hooks.rate_limit import TokenBucketRateLimiter


# --- rate limiter -----------------------------------------------------------


def test_rate_limiter_first_request_passes():
    rl = TokenBucketRateLimiter(rate_per_sec=1.0, burst=3.0)
    assert rl.allow("k") is True


def test_rate_limiter_burst_then_throttle():
    rl = TokenBucketRateLimiter(rate_per_sec=1.0, burst=3.0)
    now = 100.0
    # 3 burst requests succeed.
    assert rl.allow("k", now=now) is True
    assert rl.allow("k", now=now) is True
    assert rl.allow("k", now=now) is True
    # 4th in the same instant fails.
    assert rl.allow("k", now=now) is False
    # After 2s we've refilled 2 tokens.
    assert rl.allow("k", now=now + 2.0) is True
    assert rl.allow("k", now=now + 2.0) is True
    assert rl.allow("k", now=now + 2.0) is False


def test_rate_limiter_per_key_isolation():
    rl = TokenBucketRateLimiter(rate_per_sec=1.0, burst=1.0)
    assert rl.allow("a") is True
    assert rl.allow("a") is False
    assert rl.allow("b") is True  # different key has its own budget


def test_rate_limiter_validates_inputs():
    with pytest.raises(ValueError):
        TokenBucketRateLimiter(rate_per_sec=0.0, burst=1.0)
    with pytest.raises(ValueError):
        TokenBucketRateLimiter(rate_per_sec=1.0, burst=0.0)


def test_rate_limiter_reset_specific_key():
    rl = TokenBucketRateLimiter(rate_per_sec=1.0, burst=1.0)
    rl.allow("a")
    rl.allow("a")  # exhausts
    rl.reset("a")
    assert rl.allow("a") is True


def test_rate_limiter_reset_all():
    rl = TokenBucketRateLimiter(rate_per_sec=1.0, burst=1.0)
    rl.allow("a")
    rl.allow("b")
    rl.reset()
    assert rl.allow("a") is True
    assert rl.allow("b") is True


# --- outlier detector ------------------------------------------------------


def test_outlier_warmup_window_is_quiet():
    det = OutlierDetector(factor=2.0, warmup=5)
    # Even an extreme value during warmup returns False.
    for _ in range(4):
        assert det.observe("k", 0.5) is False
    assert det.observe("k", 100.0) is False


def test_outlier_flags_large_jumps():
    det = OutlierDetector(factor=2.0, warmup=3)
    # Build up a tight cluster around 0.5.
    for v in [0.50, 0.51, 0.49, 0.50, 0.51, 0.50]:
        det.observe("k", v)
    assert det.observe("k", 5.0) is True


def test_outlier_no_flag_when_stddev_is_zero():
    det = OutlierDetector(factor=2.0, warmup=3)
    for _ in range(10):
        det.observe("k", 0.5)  # zero variance
    # Even an extreme value won't flag because stddev=0.
    assert det.observe("k", 100.0) is False


def test_outlier_per_key_isolated():
    det = OutlierDetector(factor=2.0, warmup=2)
    for _ in range(5):
        det.observe("a", 0.5)
    # 'b' is still in warmup; its observations don't affect 'a'.
    assert det.observe("b", 100.0) is False


def test_outlier_rejects_bad_factor():
    with pytest.raises(ValueError):
        OutlierDetector(factor=0.0)


# --- audit logger ---------------------------------------------------------


def test_audit_logger_emits_via_python_logger(caplog):
    log = logging.getLogger("test_audit")
    audit = AuditLogger(log=log)
    with caplog.at_level(logging.INFO, logger="test_audit"):
        audit.emit(
            selector_name="traders",
            action="upsert_metrics",
            target="trader-42",
            actor="admin-1",
            old_value=0.5,
            new_value=0.7,
        )
    assert any(r.message == "selector_audit" for r in caplog.records)


def test_audit_record_accepts_entry_directly():
    log = logging.getLogger("test_audit2")
    audit = AuditLogger(log=log)
    audit.record(
        AuditEntry(
            ts=time.time(),
            selector_name="x",
            action="disable",
            actor=None,
            target="e",
        )
    )


# --- lazy schema migration -------------------------------------------------


def test_old_payload_gets_migrated_on_load():
    """A v1 payload (no ``tags`` field) must load cleanly and end up at the
    current SCHEMA_VERSION."""
    old = {
        "entity_id": "e",
        "metrics": {},
        "alpha": 1.0,
        "beta": 1.0,
        "last_updated": 0.0,
        "total_selections": 5,
        "enabled": True,
        "schema_version": 1,
    }
    s = EntityStats.from_dict(old)
    assert s.schema_version == SCHEMA_VERSION
    assert s.tags == {}


def test_missing_schema_version_assumed_v1():
    """Payloads from before schema versioning are treated as v1 and migrated."""
    old = {
        "entity_id": "e",
        "metrics": {},
        "alpha": 1.0,
        "beta": 1.0,
        "last_updated": 0.0,
        "total_selections": 0,
        "enabled": True,
    }
    s = EntityStats.from_dict(old)
    assert s.schema_version == SCHEMA_VERSION


def test_current_schema_passes_through_unchanged():
    s = EntityStats(entity_id="e", schema_version=SCHEMA_VERSION)
    rebuilt = EntityStats.from_dict(s.to_dict())
    assert rebuilt.schema_version == SCHEMA_VERSION
    assert rebuilt == s


def test_unknown_future_schema_bumps_through_gaps():
    """If we encounter a higher version we don't know, we should still load
    it without crashing — forward-compat by assumption."""
    future = {
        "entity_id": "e",
        "metrics": {},
        "alpha": 1.0,
        "beta": 1.0,
        "last_updated": 0.0,
        "total_selections": 0,
        "enabled": True,
        "tags": {},
        "schema_version": SCHEMA_VERSION + 5,
    }
    s = EntityStats.from_dict(future)
    # We don't downgrade — keep whatever version was there.
    assert s.schema_version >= SCHEMA_VERSION
