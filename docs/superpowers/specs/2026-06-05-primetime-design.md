# Prime-Time — design spec

**Date:** 2026-06-05
**Status:** approved (pending spec review)

## 1. Goal

A temporary, platform-wide boost to every trader's payin rate. An admin sets a
**percentage-point** bump and a **duration in minutes**; while the window is
active, every order created earns the trader `+X` points on top of their normal
per-method fee. A `⚡ Primetime +X%` strip shows under the header (all pages) for
**traders and admins**, on desktop and mobile.

**Hard constraint:** the order-creation hot path must NOT get measurably slower.

## 2. Money semantics (confirmed)

- The boost is **additive in percentage points** to the trader's per-method fee.
  - `effective_fee_percent = trader.method_config.fee + primetime_points`
  - Example: trader fee `2%`, prime-time `+1` → `3%`.
- Applied in `OrderService._calculate_trader_fee` (the existing creation-time
  computation), so `trader_fee_usdt = amount_usdt × effective_fee_percent / 100`
  is **stored on the order at creation**. The deal keeps the boost even if it
  completes after the window ends ("locked at creation").
- **Verification point (must confirm during implementation):** completion
  (`FinanceService.complete_order` / `TeamleaderService`) must pay the *stored*
  `order.trader_fee_usdt`, not recompute it from the live `method_config`. If it
  recomputes, fix it so the locked value is used — otherwise the boost leaks away
  at payout.
- Boost is **global**: one value for the whole platform, identical for all
  traders. No per-trader / per-group / per-method targeting (explicit non-goal).

## 3. State & storage (the hot-path design)

### Source of truth — Redis
A single key holds the active window:

```
key:   primetime:active
value: {"points": <float>, "ends_at": "<ISO8601 UTC>"}
TTL:   minutes × 60   (Redis EX → free auto-expiry, no cron)
```

- **Activate:** `SET primetime:active '{"points":X,"ends_at":...}' EX minutes*60`
- **Stop early:** `DEL primetime:active`
- **Reactivate:** just `SET` again → replaces value + resets TTL.
- Redis is the authoritative transient state. A promo that doesn't survive a
  Redis restart is acceptable (it's a short, transient event); TTL gives free
  expiry. Activations/stops are additionally written to `audit_logs` for history.

### Hot-path read — in-process micro-cache over Redis
`_calculate_trader_fee` must not add a Redis round-trip per order. A tiny
module-level cache wraps the read:

```python
# app/modules/settings/primetime.py  (sketch)
_cache = {"value": None, "expires_at": 0.0}   # process-local
_TTL_S = 3.0

async def get_active_points() -> Decimal:
    """Return current prime-time points (0 if inactive). Hot-path safe:
    hits Redis at most once per _TTL_S per worker; never raises."""
    now = monotonic()
    if now >= _cache["expires_at"]:
        try:
            raw = await redis_client.get("primetime:active")
            _cache["value"] = _parse_points(raw)   # Decimal or 0
        except Exception:
            _cache["value"] = Decimal("0")          # fail-safe: no boost
        _cache["expires_at"] = now + _TTL_S
    return _cache["value"]
```

- 99.9% of order creations read the in-memory value → **zero added I/O**.
- Refresh hits Redis ≤ once / 3s / worker.
- **Fail-safe:** any Redis error → treat as inactive (`0` points). Prime-time can
  never block or slow order creation.
- **Trade-off:** at the very edges of a window the boost may turn on/off up to
  ~3s late. Irrelevant for a minutes-long promo. Documented, accepted.
- `monotonic()` is used for the cache clock (allowed; `time.time()`/`Date.now()`
  bans in this repo are about workflow scripts, not app code — but monotonic is
  the right tool regardless).

Note: `redis_client` is already imported in `orders/service.py`, so no new wiring.

## 4. Backend components

### `app/modules/settings/primetime.py` (new, small, focused)
- `async def activate(points: Decimal, minutes: int) -> PrimeTimeState`
  — validates, writes Redis key with TTL, returns state, audits.
- `async def stop() -> None` — `DEL` + audit.
- `async def get_state() -> PrimeTimeState | None` — reads Redis (off hot path),
  returns `{points, ends_at}` or `None`.
- `async def get_active_points() -> Decimal` — the cached hot-path accessor (§3).
- `PrimeTimeState` = small dataclass/pydantic `{points: Decimal, ends_at: datetime}`.

Validation: `points` in `(0, MAX_POINTS]` (e.g. ≤ 50), `minutes` in `[1, MAX_MIN]`
(e.g. ≤ 1440). Reject 0/negative.

### Order service integration
`_calculate_trader_fee`:
```python
fee_percent = Decimal(str(method_conf.fee)) + await get_active_points()
```
Single line; the cached accessor keeps it ~free.

### API — `app/api/v1/endpoints/settings.py` (or existing settings router)
- `POST   /api/v1/settings/primetime`  (admin) — body `{points, minutes}` → activate.
- `DELETE /api/v1/settings/primetime`  (admin) — stop.
- `GET    /api/v1/settings/primetime`  (any authenticated user) → `{active, points, ends_at}`.
  Single Redis GET, off the hot path. Drives the banner.

Schemas in `app/modules/settings/schemas.py`:
`PrimeTimeActivateRequest{points: Decimal, minutes: int}`,
`PrimeTimeResponse{active: bool, points: Decimal | None, ends_at: datetime | None}`.

## 5. Frontend components

### `PrimeTimeBanner.vue` (new) — mounted in `AppLayout.vue`
- Rendered **under the header**, full-width thin strip, on **all pages**.
- Visible only when (role ∈ {trader, admin}) AND prime-time active.
- Content: `⚡ Primetime +X%` + small live countdown to `ends_at`.
- Same component for desktop & mobile (responsive); hides itself when expired.

### `usePrimeTime` store/composable (new)
- Polls `GET /api/v1/settings/primetime` on an interval (e.g. every 30s) + on mount.
- Computes `active` locally from `ends_at` (so the strip disappears exactly at
  expiry without waiting for the next poll); a tiny 1s ticker drives the countdown.
- Exposes `{ active, points, endsAt, remainingLabel }`.

### `PlatformSettingsView.vue` — add a "Prime-Time" block
- Inputs: *Процент (пункты)*, *Длительность (мин)*; **Activate** button.
- When active: show `+X%`, countdown, **Stop** button.
- Calls the api-service methods below.

### `api/services/settings.service.ts` (or existing)
- `getPrimeTime()`, `activatePrimeTime({points, minutes})`, `stopPrimeTime()`.

### Types (`types/index.ts`)
- `PrimeTimeState { active: boolean; points: number | null; ends_at: string | null }`.

## 6. Lifecycle

- Admin Activate → starts immediately, TTL = minutes.
- Auto-expires via Redis TTL (no job).
- Admin Stop → DEL (immediate).
- Reactivate while active → replaces points + resets timer.
- One global window at a time.

## 7. Edge cases & rules

- `points <= 0` or `minutes <= 0` → 422.
- Upper bounds (`MAX_POINTS`, `MAX_MINUTES`) → 422 (guards fat-finger promos).
- Redis unavailable on read → treated as inactive (fail-safe), order creation
  unaffected.
- Boost locked at creation; expiry mid-deal does not change `order.trader_fee_usdt`.
- Cache staleness ≤ ~3s at window edges — accepted.
- Banner only for trader/admin; merchant never sees it.
- Admin manual order edits (`admin_update_order`) don't re-read prime-time — the
  order's stored fee already carries the lock.

## 8. Performance

- Order-creation hot path: **0** new I/O in the common case (in-process cache);
  ≤ 1 Redis GET per 3s per worker. No new DB query.
- Banner: separate `GET /primetime` = one Redis GET, polled ~every 30s, off the
  hot path.
- Re-measure with the existing `TRACE_PAYIN_TIMING=1` tracer to confirm
  `trader_fee` stage is unchanged.

## 9. Testing

**Unit**
- `_calculate_trader_fee`: no boost vs `+X` points → correct `trader_fee_usdt`.
- `get_active_points`: cache hit returns cached value without Redis call; refresh
  after TTL; Redis error → `0` (fail-safe).
- `primetime` service: activate writes key+TTL+audit; stop DELs+audit; get_state
  parses value; validation rejects 0/negative/over-max.

**E2E**
- Admin `POST /primetime` → `GET /primetime` shows active with points/ends_at.
- Order created during window → its `trader_fee_usdt` reflects `fee + points`
  (compare against a baseline order with no window). Skip-tolerant until deployed.
- Admin `DELETE /primetime` → `GET` shows inactive; new order uses base fee.

## 10. Non-goals (YAGNI)

- Scheduling a future window.
- Per-trader / per-group / per-method targeting.
- Stacking multiple concurrent windows.
- Persisting the window across Redis restarts.

## 11. File touch-list

Backend:
- `app/modules/settings/primetime.py` (new)
- `app/modules/settings/schemas.py` (+request/response)
- `app/api/v1/endpoints/settings.py` (+3 routes)  *(confirm exact router file)*
- `app/modules/orders/service.py` (`_calculate_trader_fee` one line)
- tests: `tests/unit/test_primetime*.py`, `tests/e2e/test_2x_primetime.py`

Frontend:
- `src/components/layout/PrimeTimeBanner.vue` (new) + mount in `AppLayout.vue`
- `src/stores|composables/usePrimeTime.ts` (new)
- `src/views/admin/PlatformSettingsView.vue` (+block)
- `src/api/services/settings.service.ts` (+3 methods)
- `src/types/index.ts` (+`PrimeTimeState`)
