-- Provider request log: every outbound HTTP request to an external cascade
-- provider (issue / cancel / receipt / dispute / poll / balance), success or
-- failure. Written by ClickHouseBatchSink (batched inserts, never per-row).
--
-- Applied on app startup via app.infrastructure.clickhouse.ensure_schema when
-- CLICKHOUSE_ENABLED. MergeTree + monthly partitions + ZSTD payloads + TTL,
-- matching the conventions in app/infrastructure/clickhouse/schema/selector.sql.

CREATE TABLE IF NOT EXISTS provider_requests (
    ts                  DateTime64(3),
    request_id          String DEFAULT '',          -- correlates to the merchant request
    provider_id         LowCardinality(String),
    provider_code       LowCardinality(String),
    order_id            String DEFAULT '',
    method              LowCardinality(String),
    url                 String,
    request_headers     String CODEC(ZSTD(3)),       -- JSON, credential headers masked
    request_body        String CODEC(ZSTD(3)),
    response_status     UInt16,                       -- 0 on network error / timeout
    response_body       String CODEC(ZSTD(3)),
    success             UInt8,
    error               String DEFAULT '',
    provider_latency_ms UInt32,                       -- the provider call itself
    e2e_ms              UInt32 DEFAULT 0,             -- merchant request total (0 if background)
    request_type        LowCardinality(String) DEFAULT 'other'  -- payin/cancel/balance/check/upload/…
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (provider_code, ts)
TTL toDateTime(ts) + INTERVAL 90 DAY;

-- Backfill column for tables created before request_type existed (idempotent).
ALTER TABLE provider_requests ADD COLUMN IF NOT EXISTS request_type LowCardinality(String) DEFAULT 'other';


CREATE TABLE IF NOT EXISTS provider_callbacks (
    ts                DateTime64(3),
    request_id        String DEFAULT '',          -- correlates to our request log
    provider_id       LowCardinality(String),
    provider_code     LowCardinality(String),
    order_id          String DEFAULT '',          -- our internal order id (resolved)
    external_order_id String DEFAULT '',          -- provider's order id
    signature_valid   UInt8,
    parsed_status     LowCardinality(String) DEFAULT '',  -- provider status we parsed
    request_headers   String CODEC(ZSTD(3)),       -- JSON, credential headers masked
    request_body      String CODEC(ZSTD(3)),       -- raw provider payload
    response_status   UInt16,                       -- the HTTP status WE returned
    response_body     String CODEC(ZSTD(3)),       -- the body WE returned
    error             String DEFAULT '',
    processing_ms     UInt32 DEFAULT 0              -- our handling time (receive to respond)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (provider_code, ts)
TTL toDateTime(ts) + INTERVAL 90 DAY;
