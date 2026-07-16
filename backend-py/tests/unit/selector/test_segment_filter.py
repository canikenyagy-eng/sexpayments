from app.modules.selector import new_stats
from app.modules.selector.hooks.segment_filter import filter_by_tags


def _e(eid, **tags):
    s = new_stats(eid)
    s.tags.update(tags)
    return s


def test_no_required_tags_returns_all():
    candidates = [_e("a", region="ru"), _e("b", region="us")]
    assert filter_by_tags(candidates, None) == candidates
    assert filter_by_tags(candidates, {}) == candidates


def test_filters_by_tag_match():
    candidates = [_e("a", region="ru"), _e("b", region="us"), _e("c", region="ru")]
    out = filter_by_tags(candidates, {"region": "ru"})
    assert {c.entity_id for c in out} == {"a", "c"}


def test_untagged_entity_passes_through():
    """No opinion = no rejection."""
    candidates = [_e("a"), _e("b", region="us")]
    out = filter_by_tags(candidates, {"region": "ru"})
    # 'a' has no tags so it's admitted; 'b' has region=us so it's filtered
    assert {c.entity_id for c in out} == {"a"}


def test_multi_tag_all_must_match():
    candidates = [
        _e("a", region="ru", tier="gold"),
        _e("b", region="ru", tier="silver"),
        _e("c", region="us", tier="gold"),
    ]
    out = filter_by_tags(candidates, {"region": "ru", "tier": "gold"})
    assert [c.entity_id for c in out] == ["a"]


def test_empty_candidate_list():
    assert filter_by_tags([], {"region": "ru"}) == []
