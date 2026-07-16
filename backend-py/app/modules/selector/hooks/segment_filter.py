"""Tag-based segment filter: drop candidates whose tags don't match the context.

Context filtering rules are passed in via ``SelectionContext.extra["require_tags"]``,
a dict of {tag_name: required_value}. An entity is admitted iff every required
tag matches what's stored in EntityStats.tags. Entities without any tags pass
(no opinion = no rejection).
"""
from __future__ import annotations

from typing import Mapping

from app.modules.selector.core.stats import EntityStats


def filter_by_tags(
    candidates: list[EntityStats],
    required: Mapping[str, str] | None,
) -> list[EntityStats]:
    if not required:
        return candidates
    out = []
    for c in candidates:
        if not c.tags:
            out.append(c)
            continue
        if all(c.tags.get(k) == v for k, v in required.items()):
            out.append(c)
    return out
