"""A/B routing for selectors.

Given an order_id and a set of weighted variants, we deterministically pick
one (so the same order always lands in the same arm). The assignment is
also cached in Redis (``{ns}:exp:{order_id}``) with a TTL — that's the
"sticky" bucket from §10.1 of the TZ — so re-evaluations of the same order
under a slightly mutated config still produce the same variant for the
24h experiment window.

When the inner ``EntitySelector`` consults the router it gets back a
``VariantAssignment`` carrying the variant name and an optional policy
override (e.g. control → random, treatment → use the engine default).
The engine logs the variant on every decision so SQL analysis can split
metrics by arm cleanly.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Optional, Protocol

from app.modules.selector.config.models import ExperimentConfig, ExperimentVariant


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VariantAssignment:
    experiment_name: str
    variant: str
    policy_override: Optional[str]


class StickyStore(Protocol):
    """Anything that can persist & retrieve a {order_id -> variant} mapping."""

    async def get(self, namespace: str, order_id: str) -> Optional[str]: ...

    async def set(
        self, namespace: str, order_id: str, variant: str, ttl_sec: int
    ) -> None: ...


class RedisStickyStore:
    """Redis-backed sticky variant store. Optional — None disables stickiness."""

    def __init__(self, client):  # redis.asyncio.Redis
        self._client = client

    def _key(self, namespace: str, order_id: str) -> str:
        return f"{namespace}:exp:{order_id}"

    async def get(self, namespace: str, order_id: str) -> Optional[str]:
        try:
            v = await self._client.get(self._key(namespace, order_id))
            return v if v else None
        except Exception as exc:
            logger.warning("ab_sticky_get_failed: %s", exc)
            return None

    async def set(
        self, namespace: str, order_id: str, variant: str, ttl_sec: int
    ) -> None:
        try:
            await self._client.set(
                self._key(namespace, order_id), variant, ex=ttl_sec
            )
        except Exception as exc:
            logger.warning("ab_sticky_set_failed: %s", exc)


def _hash_to_unit(order_id: str, salt: str) -> float:
    """Map ``order_id`` to [0, 1) deterministically. Salting with experiment
    name lets two experiments coexist without their arms being correlated."""
    h = hashlib.sha256(f"{salt}:{order_id}".encode("utf-8")).hexdigest()
    return int(h[:12], 16) / float(1 << 48)


def assign_variant(
    experiment: ExperimentConfig, order_id: str
) -> tuple[str, ExperimentVariant]:
    """Pick a variant by weighted hash. Caller pre-checks ``enabled``."""
    if not experiment.variants:
        raise ValueError(f"experiment {experiment.name!r} has no variants")
    total = sum(v.weight for v in experiment.variants.values())
    if total <= 0:
        # Fallback: first variant alphabetically.
        first_name = sorted(experiment.variants.keys())[0]
        return first_name, experiment.variants[first_name]
    r = _hash_to_unit(order_id, experiment.name) * total
    cumulative = 0.0
    last_name = ""
    last_variant: Optional[ExperimentVariant] = None
    for name in sorted(experiment.variants.keys()):
        variant = experiment.variants[name]
        cumulative += variant.weight
        last_name, last_variant = name, variant
        if r < cumulative:
            return name, variant
    # Floating-point slack.
    return last_name, last_variant  # type: ignore[return-value]


class ABRouter:
    """Resolves the variant for an order, honoring stickiness when configured."""

    def __init__(
        self,
        experiments: tuple[ExperimentConfig, ...],
        *,
        sticky: Optional[StickyStore] = None,
        sticky_namespace: str = "sel",
        sticky_ttl_sec: int = 86_400,
    ):
        self._experiments = [e for e in experiments if e.enabled]
        self._sticky = sticky
        self._ns = sticky_namespace
        self._ttl = sticky_ttl_sec

    @property
    def enabled(self) -> bool:
        return bool(self._experiments)

    async def assign(self, order_id: str) -> Optional[VariantAssignment]:
        if not self._experiments or not order_id:
            return None
        # Single-experiment shorthand: pick the first enabled one. Multi-
        # experiment routing is straightforward to extend later; we keep the
        # primary one for now to avoid combinatorial logging surprises.
        exp = self._experiments[0]
        cached: Optional[str] = None
        if self._sticky is not None:
            cached = await self._sticky.get(self._ns, order_id)
        if cached and cached in exp.variants:
            variant = exp.variants[cached]
            return VariantAssignment(
                experiment_name=exp.name,
                variant=cached,
                policy_override=variant.policy,
            )
        variant_name, variant = assign_variant(exp, order_id)
        if self._sticky is not None:
            await self._sticky.set(self._ns, order_id, variant_name, self._ttl)
        return VariantAssignment(
            experiment_name=exp.name,
            variant=variant_name,
            policy_override=variant.policy,
        )
