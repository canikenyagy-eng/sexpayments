# Selector — Multi-Armed Bandit routing engine

Drop-in replacement for `random.choice(candidates)` in order routing.
Mixes a **static quality score** (from business metrics like conversion,
dispute rate, rate, ...) with a **dynamic bandit score** (Thompson sampling
by default) and a **selection policy** (softmax by default).

```
FinalScore(i) = QualityScore(i)^qw * BanditScore(i)^bw
P(i)         = softmax(FinalScore / T)   [+ min-share floor + max-share cap]
```

## Quick start

```python
from app.modules.selector import (
    SelectorRegistry, SelectionContext, MetricValue, load_from_yaml,
)
from app.infrastructure.cache.redis import redis_client

# 1. Load config once at app startup
root = load_from_yaml("config/selectors.prod.yaml")
registry = SelectorRegistry(root, redis_client=redis_client)

# 2. In your order router:
sel = registry.get("traders")
result = await sel.select(
    candidate_ids=["t1", "t2", "t3"],
    context=SelectionContext(order_id="ord-42", amount=1000.0),
)
if result.entity_id is None:
    # No eligible candidates — fall back as you see fit
    ...

# 3. When the order finishes:
await sel.feedback(
    order_id="ord-42",
    entity_id=result.entity_id,
    reward=1.0 if order_completed else 0.0,
    signal="completed",
)

# 4. Feed business metrics in periodically (recompute job):
await sel.upsert_metrics(
    "t1",
    {
        "conversion": MetricValue(value=0.87, sample_size=240),
        "dispute_rate": MetricValue(value=0.02, sample_size=240),
    },
)
```

## Configuration

See [`config/selectors.example.yaml`](../../../../config/selectors.example.yaml)
for the full schema. Add a new selector by adding a new entry under
`selectors:` — no code changes needed.

Hot-reload after editing a YAML by calling `registry.reload(new_config)`.

## Architecture

```
config/         dataclasses + YAML loader, hot-reload-safe
core/           stats, scoring, bandit (Thompson/ε-greedy/UCB), policy
                (softmax/argmax/random), and the EntitySelector engine
storage/        SelectorStorage protocol; RedisStorage (Lua-atomic α/β
                updates) and MemoryStorage (tests, fallback)
hooks/          circuit breaker (sliding-window failures, Redis-backed),
                fairness (min/max share), segment filter (tags)
registry.py     SelectorRegistry — single facade for the app
factory.py      build_selector / build_memory_selector
```

## Operational notes

- **Cold start**: new entities get `bootstrap_alpha/beta` priors. While any
  candidate has `< cold_start.forced_exploration_orders` selections, the
  warm candidates are excluded from that pick so the new ones gather data.
- **Decay**: α and β decay toward the (1, 1) prior by `decay_factor^(Δt /
  decay_interval_sec)`. Old performance fades; recent performance dominates.
- **Idempotency**: `feedback(order_id=...)` claims a Redis token at
  `{ns}:fb:{order_id}` so retries of the same order can't double-count.
- **Graceful degradation**: if Redis is down the storage layer raises and
  the caller decides — the engine itself never silently fails closed.

## Testing

```sh
PYTHONPATH=. pytest tests/unit/selector/ -v
```
