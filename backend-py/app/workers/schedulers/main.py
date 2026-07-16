from celery.schedules import crontab

from app.workers.celery_app import celery_app

# Define all periodic tasks here
celery_app.conf.beat_schedule = {
    "sync-active-rates-frequently": {
        "task": "sync_all_active_rates_task",
        "schedule": 10.0,
    },
    "expire-orders-frequently": {
        "task": "expire_orders_task",
        "schedule": 10.0,
    },
    "auto-disable-traders-payin": {
        "task": "auto_disable_traders_payin_task",
        "schedule": 60.0,
    },
    "refresh-stats-snapshot": {
        "task": "refresh_stats_snapshot_task",
        "schedule": 600.0,
    },
    "refresh-merchant-stats-snapshots": {
        "task": "refresh_merchant_stats_snapshot_task",
        "schedule": 600.0,
    },
    # Once-a-minute snapshot of which requisites are «в работе» (ready to accept
    # payins) → ClickHouse, for the admin «Активность» tab. One lean column-only
    # PG read + one batched CH insert per tick; fail-safe when CH is off.
    "snapshot-requisite-activity": {
        "task": "snapshot_requisite_activity_task",
        "schedule": 60.0,
    },
    # Hourly check for requisite-limit auto-resets. Boundary precision is one
    # hour (good enough — pooling permissions evaluate counters, not the exact
    # rollover timestamp). Lighter than 1-minute polling and survives short
    # worker outages around midnight UTC.
    "reset-requisite-limits": {
        "task": "reset_requisite_limits_task",
        "schedule": 3600.0,
    },
    # Hourly rollup of CascadeOrderAttempt → CascadeProviderMetric. The admin
    # dashboard reads from the metric table so we never scan attempts directly
    # on the hot path.
    "aggregate-cascade-metrics": {
        "task": "aggregate_cascade_metrics_task",
        "schedule": 3600.0,
    },
    # Defensive polling for cascade attempts stuck in IN_FLIGHT — handles the
    # case where a provider's webhook never reached us. Cheap (no-op when the
    # adapter doesn't implement poll_status, which is the default).
    "poll-cascade-in-flight": {
        "task": "poll_cascade_in_flight_task",
        "schedule": 60.0,
    },
    # Payout maintenance: expire stale payouts (refund), return lapsed claims to
    # the pool, release elapsed trader holds.
    "expire-payouts": {
        "task": "expire_payouts_task",
        "schedule": 30.0,
    },
    "return-stale-payout-claims": {
        "task": "return_stale_payout_claims_task",
        "schedule": 30.0,
    },
    "release-payout-holds": {
        "task": "release_payout_holds_task",
        "schedule": 60.0,
    },
    # Re-ping the support chat about receipt checks left without a reaction
    # (reply to the original card). Interval is fine-grained; the actual
    # threshold is the ``premoderation_reminder_minutes`` platform setting, and
    # ``reminded_at`` makes each nudge fire at most once per interval.
    "remind-stale-premoderation-checks": {
        "task": "remind_stale_premoderation_checks_task",
        "schedule": 60.0,
    },
    # Recompute the clients rollup (counts/turnover/activity — a throttled full
    # GROUP-BY, self-gated to ~once per MATERIALIZE_INTERVAL_S) and reconcile the
    # Redis blocked-set from the DB. Fires every 60s for the ban reconcile; the
    # heavier rollup self-throttles to a longer window. Off the order hot path;
    # the Clients page tolerates up-to-interval lag, and admin block/unblock
    # updates Redis immediately (the reconcile only self-heals after a flush).
    "refresh-clients": {
        "task": "refresh_clients_task",
        "schedule": 60.0,
    },
    # Trader achievements/bonuses. Every 5 min: re-aggregate TODAY's per-trader
    # turnover and recompute the materialized bonus (daily-tier reacts to the live
    # running turnover). One bounded one-day GROUP-BY + small rollup reads — off
    # the order hot path (fee calc reads the materialized column for free).
    "refresh-trader-turnover": {
        "task": "refresh_trader_turnover_task",
        "schedule": 300.0,
    },
    # Nightly (00:05 UTC): finalize YESTERDAY's rollup (catch late-settled orders)
    # and recompute, so streaks advance cleanly at the day boundary.
    "finalize-trader-day": {
        "task": "finalize_trader_day_task",
        "schedule": crontab(hour=0, minute=5),
    },
}
