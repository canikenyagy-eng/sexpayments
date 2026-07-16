-- Requisite-activity snapshot: which requisites are "в работе" (ready to accept
-- payins) at each minute. Written by the ``snapshot_requisite_activity_task``
-- Celery beat task (once per minute, one batched insert per tick) and read by
-- the admin «Активность» stats tab. Bootstrapped on startup via ensure_schema.
--
-- One row per ready requisite per tick. The reader groups by (bucket,
-- requisite_id) so per-minute duplicates collapse before Python merges the
-- limit ranges into bars. ORDER BY (currency, ts) keeps the range+currency scan
-- a cheap partition read. Low retention — this is a live activity view, not a
-- compliance trail.

CREATE TABLE IF NOT EXISTS requisite_activity_snapshots (
    ts            DateTime64(3, 'UTC'),     -- pinned UTC so toStartOf*/toUnixTimestamp
                                            -- bucket in UTC regardless of server TZ
                                            -- (must match the reader's aware-UTC walker)
    requisite_id  Int64,
    trader_id     Int64,                    -- users.id of the owning trader
    currency      LowCardinality(String),
    limit_min     Decimal(18, 2),           -- limit_min_transaction
    limit_max     Decimal(18, 2),           -- limit_max_transaction
    limit_total   Decimal(18, 2)            -- limit_daily («общий»)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (currency, ts, requisite_id)
TTL toDateTime(ts) + INTERVAL 180 DAY;
