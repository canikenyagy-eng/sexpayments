# Requisite Activity chart ("Активность" tab)

**Date:** 2026-07-03
**Status:** Approved

## Goal

Every minute, snapshot which requisites are *ready to accept payins*, store the
snapshot in ClickHouse (cheap, TTL'd), and add an "Активность" tab to the admin
Statistics page that renders, per time-bucket, the merged limit ranges of the
ready requisites as floating bars — colored by the number of unique traders
active in the bucket.

Hard constraint: **light on the DB and CPU**. One lean column-only PG query per
minute; ClickHouse does the read-time reduction; the read endpoint is Redis-cached.

## Decisions (locked)

- **Ready predicate** = the static pooling pre-filter, no order/balance:
  `is_active ∧ ¬is_archived ∧ status=ENABLED ∧ source=LOCAL ∧ trader.status=ENABLED ∧ trader.is_payin_active`.
- **Stored per requisite per tick**: `ts, requisite_id, trader_id (user_id), currency, limit_min (=limit_min_transaction), limit_max (=limit_max_transaction), limit_total (=limit_daily)`.
- **Currency**: stored per row; UI has a currency selector, default `RUB` (one currency per chart — amounts across currencies aren't comparable on one axis).
- **Bar color** = unique trader count in the bucket → red→yellow→green scale
  (0–15 red, 15–30 yellow, 30–45 green, 45+ solid green).
- **Hover** = at the cursor's amount level, the number of requisites covering it (step function `segments`).
- **Time bucket** = union of all snapshot ranges within the bucket (merge overlapping `[min,max]` across requisites).

## Efficiency design

- **Write** (`snapshot_requisite_activity_task`, every 60s): ONE `select()` of 6
  columns filtered to ready requisites (no ORM hydration), then ONE batched
  `ch.get_client().insert(...)` for the whole tick via a new `sink.emit_records()`
  (never per-row). Fail-safe: CH errors never break the task.
- **Read** (`GET /stats/admin/activity`): ClickHouse `GROUP BY bucket, requisite_id`
  with `min/max` + `any(trader_id)` collapses 60×/hour of per-minute rows to
  per-requisite-per-bucket before Python sees them; a second cheap
  `SELECT DISTINCT currency` feeds the selector. Python only merges a few
  intervals per bucket + computes the segment step-function, then backfills empty
  buckets. Redis-cached 60s (key includes date_from/date_to/granularity/currency).
- **Table** `requisite_activity_snapshots`: `MergeTree`,
  `PARTITION BY toYYYYMM(ts)`, `ORDER BY (currency, ts, requisite_id)` (so the
  range+currency scan is a cheap partition read), `TTL 180 DAY`.

## Components

### Backend
- `app/infrastructure/clickhouse/schema/activity.sql` — new DDL; add to `main.py` `ddl_files`.
- `app/infrastructure/clickhouse/sink.py` — add `emit_records(records)` (batched, grouped-by-table, fail-safe, sync — for bulk producers).
- `app/modules/stats/activity_ch.py` — `RequisiteActivitySnapshotRecord` + `insert_snapshots(records)` (write) + `query_activity(df, dt, currency, granularity)` + `available_currencies(df, dt)` (reads, executor, fail-safe). Mirrors `audit/repository.py`.
- `app/modules/stats/repository.py` — `list_ready_requisites_for_snapshot()` (lean PG query).
- `app/modules/stats/service.py` — `snapshot_requisite_activity()` (collect → build records → insert) + `get_activity_timeseries(df, dt, granularity, currency)` (cache → CH read → bucket/merge/segments/backfill). Pure helpers `_merge_intervals`, `_segments`.
- `app/modules/stats/schemas.py` — `AdminActivitySegment / AdminActivityBar / AdminActivityBucket / AdminActivityResponse`.
- `app/api/v1/endpoints/stats.py` — `GET /admin/activity` (`require_admin`, `date_from/date_to` UnixTimestamp, `granularity` regex, `currency`).
- `app/workers/tasks/stats.py` — `snapshot_requisite_activity_task`; beat entry `snapshot-requisite-activity` @ 60s in `schedulers/main.py`.

### Frontend (`frontend-vue`)
- `views/admin/StatsView.vue` — add tab `{ key:'activity', label:'Активность' }` + section with от/до `BaseDatePicker`, granularity + currency `BaseSelect`.
- `components/ui/ActivityRangeChart.vue` — custom SVG (mirrors `BaseLineChart.vue`): floating bars per bucket, `colorForTraders()` fill, hover tooltip with per-level requisite count.
- `api/services/stats.service.ts` — `getActivityTimeseries(...)` + types.

### Tests
- `tests/unit/test_stats_service.py` (or a new `test_activity_service.py`): `_merge_intervals`, `_segments`, bucketing + empty-bucket backfill, ready-predicate shape.

## YAGNI
- No role-folder refactor of stats schemas/endpoints (follow current `stats.py`).
- No per-level color (color = traders; per-level only on hover).
- No simultaneous multi-currency axis (selector).
