# Requisite & Trader Priority — Design

- **Date:** 2026-07-11
- **Status:** Approved (ready for implementation plan)
- **Scope:** `backend-py` (requisites, traders, pooling) + `frontend-vue` (trader & admin panels)

## Goal

Give traders control over how deals are distributed **across their own requisites**, and
give admins a lever to boost/penalize a trader's **total** share — without letting a trader
inflate their own total volume. Deal assignment becomes proportional to a per-requisite
`priority_score` instead of the current uniform random pick.

## Model (confirmed)

- Every requisite has a base priority of **100**.
- A trader assigns each requisite a **weight `trader_priority` ∈ {1,2,3}** (default 1) on the
  frontend.
- An admin sets a per-trader **`priority_bonus_percent`** (any number, incl. negative;
  default 0).
- The trader's priority budget is **conserved and redistributed by weight, per
  (currency, method) group**. A requisite belongs to exactly one group (it has one currency
  and one payment_method), so it has exactly one stored `priority_score`.

### Formula

For each group = (`trader_id`, `currency`, `payment_method`), over the trader's **active**
requisites in that group (`is_active AND NOT is_archived`):

```
base    = max(0, 100 × (1 + priority_bonus_percent / 100))
N       = count of active requisites in the group
Σ       = sum of their trader_priority weights
score_i = trader_priority_i × (N × base) / Σ
```

`Σ ≥ N ≥ 1` (each weight ≥ 1) → no division by zero. `admin% ≤ −100` → `base = 0` → all
scores in the trader's groups become 0.

### Worked examples

| Input | base | N | Σ | scores |
|---|---|---|---|---|
| weights [2,1,1], admin 0 | 100 | 3 | 4 | 150 / 75 / 75 (Σ=300) |
| weights [1,2,3], admin 0 | 100 | 3 | 6 | 50 / 100 / 150 (Σ=300) |
| weights [1,1,1], admin 50 | 150 | 3 | 3 | 150 / 150 / 150 |
| weights [1,1,1], admin 100 | 200 | 3 | 3 | 200 / 200 / 200 |
| weights [1,1,1], admin 0 | 100 | 3 | 3 | 100 / 100 / 100 (current behavior) |

**Total-share effect:** selection is global weighted-random across all eligible requisites
(all traders). A trader's total probability mass ∝ the sum of his eligible requisites'
scores = `N × base`. So more active requisites and a higher admin % both raise the trader's
total share; the trader's own weights only *reallocate* his budget internally.

## 1. Data model (Alembic migration)

- `requisites.trader_priority` — `Integer NOT NULL DEFAULT 1` (values 1–3, enforced in schema).
- `requisites.priority_score` — `Numeric(12,4) NOT NULL DEFAULT 100` (fractional possible).
- `traders.priority_bonus_percent` — `Numeric(10,4) NOT NULL DEFAULT 0` (any value).

No data backfill: existing rows get `trader_priority=1`, `%=0` → `score=100`, which is the
correct value (all weights equal → each score = base).

`priority_bonus_percent` lives on the **`traders`** table (business config). Requisites link
to `users.id` via `trader_id`; the recompute resolves the `Trader` row by `user_id`. If a
trader has no `Trader` row, treat `%` as 0 (base 100).

## 2. Priority computation service

New `PriorityService` (in `app/modules/requisites/`), pure/deterministic, **off the order
hot path**:

- `recompute_group(trader_id, currency, method)` — load active requisites in the group,
  compute `base`, `N`, `Σ`, set each `priority_score`. Stages updates on the session.
- `recompute_all_for_trader(trader_id)` — find the distinct (currency, method) groups among
  the trader's active requisites and recompute each (used when `base` changes, i.e. admin %).

All recomputes run **inside the same transaction** as the triggering change (atomic).

### Triggers

- **Create requisite** → recompute its group.
- **Update requisite** → recompute the affected group(s): if `trader_priority`, `is_active`,
  or currency/method changed, recompute the current group (and the old group too if
  currency/method changed).
- **Delete / archive requisite** → recompute its (former) group.
- **Admin changes `priority_bonus_percent`** → `recompute_all_for_trader`.

## 3. Selection integration (hot path)

- Add `PoolingStrategy.WEIGHTED` and make it the **default** in
  `PoolingService._resolve_pooling_strategy`. `RANDOM` / `BANDIT` / LRU stay in the code,
  unchanged, just no longer the default.
- `_serialize_requisite` includes `priority_score` (read from the already-fetched column —
  **no extra query**, same O(n)).
- Weighted pick in `select_requisite_with_diagnostics`:

  ```python
  weights = [c["priority_score"] for c in candidates]
  pick = (random.choices(candidates, weights=weights, k=1)[0]
          if sum(weights) > 0 else random.choice(candidates))
  ```

  All-equal scores → uniform (backward compatible). All-zero (e.g. admin ≤ −100 and only that
  trader's requisites are candidates) → uniform fallback so an order is still assigned.
- `select_requisite` (the simpler variant) gets the same weighted branch under `WEIGHTED`.

## 4. API / schemas

- **Trader** (`requisites/schemas/trader.py`): `TraderRequisiteCreate` / `TraderRequisiteUpdate`
  gain `trader_priority: int` (validated 1–3, default 1). Trader response schema exposes
  `trader_priority` only — **not** `priority_score` (traders see just their 1–3 choice).
- **Admin** (`traders` schemas): the trader-update schema gains `priority_bonus_percent`; its
  setter triggers `recompute_all_for_trader`. Admin responses may expose both
  `priority_bonus_percent` and requisite `priority_score` for visibility.
- Requisite create/update already flow through `RequisiteService`; the priority recompute is
  invoked there after the write.

## 5. Frontend

- **Trader** (`views/trader/RequisitesView.vue`): a 1–3 selector (segmented / dropdown) in the
  requisite create & edit forms; the card shows the chosen weight. **No** `priority_score`
  shown. On save → existing update flow → backend recomputes the group.
- **Admin** (trader detail/card view): a numeric `priority_bonus_percent` (%) input; on save →
  update API → `recompute_all_for_trader`.

## 6. Edge cases & backward compatibility

- Existing requisites: defaults (`trader_priority=1`, `score=100`) already correct → no backfill.
- `admin% ≤ −100` → `base=0` → the trader's scores 0: near-zero share against traders with
  positive scores; uniform fallback if only his requisites are candidates.
- `Σ` never 0 (weights ≥ 1); `N ≥ 1` within any group by construction.
- Fractional scores are fine for `random.choices`.

## 7. Testing

- **Unit — formula/recompute:** the worked examples above; single-requisite group;
  `admin% ≤ −100` → base 0; per-(currency,method) isolation.
- **Unit — weighted selection:** seed `random`; empirical ratio ≈ score ratio; all-zero → uniform.
- **Integration:** create/update requisite and admin-% change recompute the correct group(s);
  currency/method change recomputes old+new; backward-compat (pre-existing data stays 100).

## Out of scope

- **Cascade requisites** (`source=cascade`, virtual traders) — untouched; feature is LOCAL only.
- **Bandit / LRU strategies** — kept as-is, just not the default.
- No change to eligibility filters (limits/ACL/status); priority only weights the choice
  **among** already-eligible candidates.
