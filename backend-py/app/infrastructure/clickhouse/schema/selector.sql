-- ClickHouse schemas for the selector subsystem.
--
-- Not wired into the app yet: the engine emits events through
-- ``LoggingSink``, and a log shipper / batch job lands them here. When
-- direct insertion is needed, add a ``ClickHouseSink`` (clickhouse-connect
-- or async-clickhouse) that conforms to the ``EventSink`` Protocol.
--
-- Apply order matters: decisions / feedback first (used by recompute and
-- A/B analysis), order_events last (richer denormalized view).

CREATE TABLE IF NOT EXISTS selector_decisions (
    ts                  DateTime64(3),
    selector_name       LowCardinality(String),
    order_id            String,
    chosen_entity_id    String,
    reason              LowCardinality(String),
    candidates          Array(Tuple(
        entity_id       String,
        quality         Float32,
        bandit_sample   Float32,
        final_score     Float32,
        probability     Float32
    )),
    context             String CODEC(ZSTD),
    experiment_variant  LowCardinality(String) DEFAULT '',
    decision_latency_ms Float32
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (selector_name, ts, order_id)
TTL toDateTime(ts) + INTERVAL 180 DAY;


CREATE TABLE IF NOT EXISTS selector_feedback (
    ts                  DateTime64(3),
    selector_name       LowCardinality(String),
    order_id            String,
    entity_id           String,
    reward              Float32,
    signal              LowCardinality(String),
    duplicate           UInt8 DEFAULT 0,
    metadata            String CODEC(ZSTD)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (selector_name, ts, order_id)
TTL toDateTime(ts) + INTERVAL 180 DAY;


-- Denormalized order-level view used by the recompute job and by A/B
-- analysis SQL. Populated independently by the order processor (not by
-- the selector itself); the selector only joins against it via Materialized
-- View definitions, never inserts.
CREATE TABLE IF NOT EXISTS order_events (
    ts          DateTime64(3),
    order_id    String,
    event_type  LowCardinality(String),
    trader_id   String,
    provider_id String,
    amount      Decimal64(2),
    currency    LowCardinality(String),
    payload     String CODEC(ZSTD)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (ts, order_id)
TTL toDateTime(ts) + INTERVAL 730 DAY;
