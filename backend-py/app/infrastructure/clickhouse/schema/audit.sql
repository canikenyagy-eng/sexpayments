-- Merchant/bot API request log + order-creation snapshot.
--
-- Replaces the Postgres merchant_api_logs / order_creation_snapshots audit
-- tables (high volume, previously unbounded). The two are linked by request_id
-- (ClickHouse has no autoincrement id), and merchant_id / order_id /
-- response_time_ms are denormalized into the snapshot so stats never need a
-- cross-table join. Bootstrapped on startup via ensure_schema. Credential
-- headers are masked before storage.

CREATE TABLE IF NOT EXISTS merchant_api_logs (
    ts                DateTime64(3),
    request_id        String DEFAULT '',
    merchant_id       Int64 DEFAULT 0,             -- 0 = unknown
    order_id          String DEFAULT '',           -- internal order id; '' if none created
    url               String,
    method            LowCardinality(String),
    request_headers   String CODEC(ZSTD(3)),        -- JSON, credential headers masked
    request_body      String CODEC(ZSTD(3)),
    response_status   UInt16 DEFAULT 0,
    response_headers  String CODEC(ZSTD(3)),        -- JSON
    response_body     String CODEC(ZSTD(3)),
    response_time_ms  UInt32 DEFAULT 0
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (ts)
TTL toDateTime(ts) + INTERVAL 90 DAY;


CREATE TABLE IF NOT EXISTS order_creation_snapshots (
    ts                DateTime64(3),
    request_id        String DEFAULT '',           -- links to merchant_api_logs.request_id
    merchant_id       Int64 DEFAULT 0,
    order_id          String DEFAULT '',
    response_time_ms  UInt32 DEFAULT 0,
    request_data      String CODEC(ZSTD(3)),        -- JSON
    merchant_snapshot String CODEC(ZSTD(3)),        -- JSON
    rate_snapshot     String CODEC(ZSTD(3)),        -- JSON ('' if none)
    traders_snapshot  String CODEC(ZSTD(3)),        -- JSON array
    candidates        String CODEC(ZSTD(3)),        -- JSON array
    result            String CODEC(ZSTD(3))         -- JSON
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (ts)
TTL toDateTime(ts) + INTERVAL 90 DAY;
