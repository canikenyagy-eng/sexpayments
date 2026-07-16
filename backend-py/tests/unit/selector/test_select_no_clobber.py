"""Regression: select() must not clobber atomic feedback α/β.

Before the fix, ``_finalize_choice`` did ``chosen.total_selections += 1;
save(chosen)`` — a full-blob write of the (client-side decayed) stats we read
earlier, which overwrote α/β that a concurrent ``feedback()`` updated through
its own atomic Lua. Now it bumps only the selection counter atomically.
"""
import time

import pytest

from app.modules.selector.core.stats import EntityStats


@pytest.mark.asyncio
async def test_select_preserves_stored_alpha_beta(make_selector):
    sel = make_selector(seed=1)
    # Large time gap → decay would noticeably shrink α/β if persisted.
    old = time.time() - 10_000
    await sel._storage.save(
        EntityStats(entity_id="t1", alpha=8.0, beta=2.0, total_selections=50, last_updated=old)
    )

    result = await sel.select(["t1"])
    assert result.entity_id == "t1"

    stored = await sel._storage.get("t1")
    assert stored.total_selections == 51   # counter bumped atomically
    assert stored.alpha == 8.0             # α untouched (not decayed/clobbered)
    assert stored.beta == 2.0              # β untouched


@pytest.mark.asyncio
async def test_select_still_persists_cold_start_entity(make_selector):
    """A never-persisted candidate must still get its initial blob written by
    the finalize save-fallback (the atomic increment no-ops on a missing key)."""
    sel = make_selector(seed=1)

    result = await sel.select(["brand_new"])
    assert result.entity_id == "brand_new"

    stored = await sel._storage.get("brand_new")
    assert stored is not None
    assert stored.total_selections == 1


@pytest.mark.asyncio
async def test_feedback_after_select_is_not_lost(make_selector):
    """End-to-end of the race intent: feedback updates α, a later select on the
    same entity must not revert it."""
    sel = make_selector(seed=3)
    await sel._storage.save(
        EntityStats(entity_id="t1", alpha=2.0, beta=2.0, total_selections=20, last_updated=time.time())
    )
    # Reward bumps α via the atomic feedback path.
    await sel.feedback(order_id="ord-1", entity_id="t1", reward=1.0)
    after_fb = await sel._storage.get("t1")
    assert after_fb.alpha > 2.0  # feedback took effect

    # A subsequent selection must keep that α (only the counter changes).
    await sel.select(["t1"])
    after_sel = await sel._storage.get("t1")
    assert after_sel.alpha == pytest.approx(after_fb.alpha)
    assert after_sel.total_selections == after_fb.total_selections + 1
